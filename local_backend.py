"""On-demand PLaMo inference in ComfyUI's Python process."""

import asyncio
from contextlib import nullcontext
import gc
import hashlib
import importlib.util
import logging
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
from tempfile import TemporaryDirectory
import time

from . import plamo_backend as backend
from .state import make_state

MODEL = "plamo-3-translate-0.8b"
REPOSITORY = "pfnet/" + MODEL
REVISION = "c5a33c561bc6a4fbac38c3460bbb384e15c0b231"
# These two Python modules are executed by Transformers. Accept the reviewed PFN code.
CODE_HASHES = {
    "modeling_plamo.py": "498ff5f52b565315325ffff29b6c5e628a55156a4df5cd2bacb48a9c8697f32b",
    "tokenization_plamo.py": "68b769d516541603857ce9d2813e48193efe41abb64c1091311b973d491724d9",
}
FILES = (*CODE_HASHES, "model.safetensors", "config.json", "generation_config.json",
         "chat_template.jinja", "tokenizer_config.json", "tokenizer.jsonl", "special_tokens_map.json")
_lock = asyncio.Lock()
_loaded = None


def roots():
    import folder_paths
    paths = folder_paths.folder_names_and_paths.get("LLM", ([], set()))[0]
    return list(dict.fromkeys([Path(folder_paths.models_dir) / "LLM", *map(Path, paths)]))


def model_folders():
    models = {}
    for root in roots():
        if root.is_dir():
            for path in sorted(root.iterdir()):
                if not path.name.startswith(".") and all((path / name).is_file() for name in FILES):
                    models.setdefault(path.name, path)
    return models


def missing_packages():
    return [name for name in ("torch", "transformers", "accelerate", "numba")
            if importlib.util.find_spec(name) is None]


def shell_command(args):
    if os.name != "nt":
        return shlex.join(args)
    bare_word = re.compile(r"[\w.:/\\-]+").fullmatch
    if all(bare_word(arg) for arg in args):
        return " ".join(args)

    # Both interactive shells accept this launcher. Keep spaced paths readable
    # and prevent either outer shell from expanding characters inside a path.
    def literal(arg):
        if bare_word(arg):
            return arg
        escaped = arg.replace("'", "''")
        protected = re.sub(r"[%!$`‘’‚‛“”„‟]", lambda m: f"' + [char]{ord(m[0])} + '", escaped)
        quoted = "'" + protected + "'"
        return "(" + quoted + ")" if protected != escaped else quoted

    return 'powershell.exe -NoProfile -Command "& ' + " ".join(map(literal, args)) + '"'


def install_command(missing):
    if not missing:
        return "", False
    # Keep the venv interpreter path, even when it is a symlink to a shared Python.
    python = [sys.executable]
    if sys.flags.no_user_site:
        python.append("-s")
    pip = shell_command([*python, "-m", "pip", "install", *missing])
    if importlib.util.find_spec("pip") is not None:
        return pip, False
    uv = shutil.which("uv")
    if uv:
        return shell_command([uv, "pip", "install", "--python", sys.executable, *missing]), False
    if importlib.util.find_spec("ensurepip") is not None:
        return shell_command([*python, "-m", "ensurepip"]) + "\n" + pip, False
    # Desktop's built-in terminal can expose uv even when the server PATH does not.
    return shell_command(["uv", "pip", "install", "--python", sys.executable, *missing]), True


def setup():
    missing = missing_packages()
    command, needs_terminal_uv = install_command(missing)
    return {"models": list(model_folders()), "loaded": _loaded[0] if _loaded else None,
            "missing": missing, "install_command": command, "needs_terminal_uv": needs_terminal_uv,
            "install_shell": "windows" if os.name == "nt" else "sh",
            "directory": str(roots()[0])}


def download():
    from huggingface_hub import snapshot_download
    root = roots()[0]
    root.mkdir(parents=True, exist_ok=True)
    dest = root / MODEL
    if MODEL in model_folders():
        return
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise backend.TranslationError("LOCAL_INCOMPLETE", "The existing model folder is incomplete.")
    with TemporaryDirectory(dir=root, prefix=".plamo-") as temp:
        stage = Path(temp) / MODEL
        try:
            snapshot_download(REPOSITORY, revision=REVISION, local_dir=stage,
                              allow_patterns=[*FILES, "LICENSE/*", "README.md"])
        except Exception as exc:
            logging.exception("Failed to download the official PLaMo model")
            raise backend.TranslationError("LOCAL_DOWNLOAD_FAILED", "Could not download the PFN model.", 502) from exc
        if not all((stage / name).is_file() for name in FILES):
            raise backend.TranslationError("LOCAL_INCOMPLETE", "Incomplete model download.")
        try:
            if dest.is_dir() and not any(dest.iterdir()):
                dest.rmdir()
            stage.rename(dest)
        except OSError:
            # Two ComfyUI instances can finish downloading into the same shared root.
            if MODEL not in model_folders():
                raise


def unload():
    global _loaded
    if _loaded is not None:
        from comfy import model_management
        _loaded = None
        gc.collect()
        model_management.soft_empty_cache()


def load(key):
    global _loaded
    if _loaded is not None and _loaded[0] == key:
        return _loaded[1:]
    if missing_packages():
        raise backend.TranslationError("LOCAL_DEPS", "Open Local settings for the installation command.")
    path = model_folders().get(key)
    if path is None:
        raise backend.TranslationError("LOCAL_MISSING", "Choose or download a model in Local settings.")
    for name, digest in CODE_HASHES.items():
        if hashlib.sha256((path / name).read_bytes()).hexdigest() != digest:
            raise backend.TranslationError("LOCAL_CODE", "The model code differs from the supported PFN version.")
    import torch
    from comfy import model_management
    from transformers.dynamic_module_utils import get_class_from_dynamic_module
    unload()
    device = model_management.get_torch_device()
    dtype = torch.float32
    if model_management.should_use_bf16(device=device):
        dtype = torch.bfloat16
    elif model_management.should_use_fp16(device=device):
        dtype = torch.float16
    logging.info("Loading local PLaMo model %s on %s (%s)", key, device, dtype)
    tokenizer_class = get_class_from_dynamic_module("tokenization_plamo.Plamo3Tokenizer", path, local_files_only=True)
    model_class = get_class_from_dynamic_module("modeling_plamo.Plamo3ForCausalLM", path, local_files_only=True)
    config_class = get_class_from_dynamic_module("modeling_plamo.Plamo3Config", path, local_files_only=True)
    tokenizer = tokenizer_class.from_pretrained(path, local_files_only=True)
    model = model_class.from_pretrained(path, config=config_class.from_pretrained(path, local_files_only=True),
                                        dtype=dtype, device_map={"": device}, local_files_only=True,
                                        use_safetensors=True).eval()
    _loaded = (key, tokenizer, model)
    return tokenizer, model


def generate(tokenizer, model, text, target, source, max_tokens, deadline):
    import torch
    prompt = backend.build_prompt(text, target, source)
    with torch.inference_mode():
        encoded = tokenizer(prompt, return_tensors="pt").to(model.device)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise backend.TranslationError("LOCAL_TIMEOUT", "Local translation timed out.")
        decoding = nullcontext()
        if model.device.type == "cuda" and torch.version.cuda:
            from .local_cuda import accelerate_decode
            decoding = accelerate_decode(model, encoded, max_tokens)
        with decoding:
            result = model.generate(**encoded, do_sample=False, max_new_tokens=max_tokens, max_time=remaining)
        if time.monotonic() >= deadline:
            raise backend.TranslationError("LOCAL_TIMEOUT", "Local translation timed out.")
        ids = result[0, encoded.input_ids.shape[1]:].tolist()
        eos = model.generation_config.eos_token_id
        complete = bool(ids) and ids[-1] in (eos if isinstance(eos, list) else [eos])
        decoded = tokenizer.decode(ids, skip_special_tokens=True)
        return backend.checked_translation(decoded), complete


async def operate(action, body, report=None):
    if action == "setup":
        return setup()
    if _lock.locked():
        raise backend.TranslationError("LOCAL_BUSY", "Another local model operation is running.", 409)
    async with _lock:
        if action == "unload":
            await asyncio.to_thread(unload)
            return setup()
        if action == "download":
            await asyncio.to_thread(download)
            return setup()
        config = backend.parse_config(body.get("config", {}))
        text, target, source = body.get("source_text"), body.get("target_lang"), body.get("source_lang", "auto")
        backend.validate_source(text, target, source)
        if _loaded is None or _loaded[0] != config.model_key:
            if missing_packages():
                raise backend.TranslationError("LOCAL_DEPS", "Open Local settings for the installation command.")
            if config.model_key not in model_folders():
                if config.model_key != MODEL:
                    raise backend.TranslationError("LOCAL_MISSING", "Choose an installed model in Local settings.")
                if report:
                    await report("downloading")
                await asyncio.to_thread(download)
            if report:
                await report("loading")
        # The first download has its own budget; inference uses the configured timeout.
        deadline = time.monotonic() + config.timeout_seconds
        tokenizer, model = await asyncio.to_thread(load, config.model_key)
        if report:
            await report("translating")

        async def complete_segment(segment):
            return await asyncio.to_thread(generate, tokenizer, model, segment, target, source, config.max_tokens, deadline)

        result, complete = await backend.translate_segments(text, complete_segment)
        return {"text": result, "status": "translated" if complete else "partial", "warnings": [],
                "translation_state": make_state(text, target, status="translated", model_key=config.model_key,
                                                source_lang=source) if complete else None}
