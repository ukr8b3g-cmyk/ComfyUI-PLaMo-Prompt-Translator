"""Route validation, token isolation, bounded requests, and mutual exclusion."""

import asyncio
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import types
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


PACKAGE = "_plamo_contract_tests"
if PACKAGE not in sys.modules:
    module = types.ModuleType(PACKAGE)
    module.__path__ = [str(Path(__file__).resolve().parents[1])]
    sys.modules[PACKAGE] = module
routes = importlib.import_module(PACKAGE + ".routes")
backend = importlib.import_module(PACKAGE + ".plamo_backend")
BASE = "/plamo_translator/v1"


class RouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server_stub = types.SimpleNamespace(routes=web.RouteTableDef())
        self.assertTrue(routes.register_routes(self.server_stub))
        app = web.Application()
        app.add_routes(self.server_stub.routes)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_registration_is_idempotent_without_comfyui(self):
        self.assertFalse(routes.register_routes(self.server_stub))
        self.assertEqual(len(self.server_stub.routes), 4)

    async def test_local_setup_offers_only_missing_dependencies(self):
        local = importlib.import_module(PACKAGE + ".local_backend")
        with patch.object(local, "roots", return_value=[Path("models/LLM")]), \
             patch.object(local, "dependency_status", return_value=(["numba"], None)), \
             patch.object(local.importlib.util, "find_spec", return_value=object()):
            response = await self.client.post(BASE + "/local/setup", json={})
            data = await response.json()
        self.assertEqual(data["missing"], ["numba"])
        flags = ["-s"] if sys.flags.no_user_site else []
        self.assertEqual(data["install_command"], local.shell_command([sys.executable, *flags, "-m", "pip", "install", "numba"]))

    async def test_transformers_version_in_setup_and_translation(self):
        local = importlib.import_module(PACKAGE + ".local_backend")
        for installed, compatible in [("4.50.3", False), ("4.56.0rc1", False),
                                      ("4.56.0", True), ("5.23.0", True)]:
            with self.subTest(installed=installed), ExitStack() as stack:
                stack.enter_context(patch.dict(sys.modules, {"transformers": types.SimpleNamespace(__version__=installed)}))
                stack.enter_context(patch.object(local.importlib.util, "find_spec", return_value=object()))
                stack.enter_context(patch.object(local, "roots", return_value=[Path("models/LLM")]))
                stack.enter_context(patch.object(local, "_loaded", None))
                download = stack.enter_context(patch.object(local, "download"))
                load = stack.enter_context(patch.object(local, "load"))
                response = await self.client.post(BASE + "/local/setup", json={})
                data = await response.json()
                self.assertEqual(data["missing"], [])
                if compatible:
                    self.assertIsNone(data["transformers_update"])
                    self.assertEqual(data["install_command"], "")
                    continue
                self.assertEqual(data["transformers_update"], {"installed": installed, "minimum": "4.56.0"})
                self.assertIn("transformers>=4.56.0", data["install_command"])
                response = await self.client.post(BASE + "/local/translate", json={
                    "config": {"model_key": local.MODEL}, "source_text": "A cat.", "target_lang": "ja"})
                error = await response.json()
                self.assertEqual(error["code"], "LOCAL_DEPS")
                self.assertIn(installed, error["message"])
                download.assert_not_called()
                load.assert_not_called()

    def test_local_transformers_loading_api(self):
        local = importlib.import_module(PACKAGE + ".local_backend")
        if any(importlib.util.find_spec(name) is None for name in ("torch", "transformers")):
            self.skipTest("Transformers and PyTorch are optional for the LM Studio node")
        if local.dependency_status()[1]:
            self.skipTest("Installed Transformers requires an update for the Local node")
        import torch
        from transformers import DynamicCache, LlamaConfig, LlamaForCausalLM
        config = LlamaConfig(vocab_size=32, hidden_size=16, intermediate_size=32, num_hidden_layers=1,
                             num_attention_heads=2, num_key_value_heads=1)
        with TemporaryDirectory() as path:
            LlamaForCausalLM(config).save_pretrained(path)
            model = LlamaForCausalLM.from_pretrained(path, config=config, dtype=torch.float32, local_files_only=True)
            cache = DynamicCache(config=config)
            with torch.inference_mode():
                model(input_ids=torch.tensor([[1, 2]]), past_key_values=cache, use_cache=True)
            self.assertEqual(cache.get_seq_length(), 2)

    def test_install_command_targets_the_running_interpreter_without_pip(self):
        local = importlib.import_module(PACKAGE + ".local_backend")
        python = "/opt/Comfy UI/user's venv/bin/python"
        uv = "/opt/Comfy UI/uv"
        with patch.object(local.sys, "executable", python), patch.object(local.os, "name", "posix"), \
             patch.object(local.shutil, "which", return_value=uv), \
             patch.object(local.importlib.util, "find_spec", return_value=None):
            command, needs_uv = local.install_command(["numba"])
            self.assertEqual(local.shlex.split(command), [uv, "pip", "install", "--python", python, "numba"])
            self.assertFalse(needs_uv)
        with patch.object(local.sys, "executable", "C:/User's Env/python.exe"), \
             patch.object(local.os, "name", "nt"), patch.object(local.shutil, "which", return_value=None), \
             patch.object(local.importlib.util, "find_spec", side_effect=lambda name: name == "ensurepip" or None):
            command, needs_uv = local.install_command(["numba"])
            self.assertEqual(len(command.splitlines()), 2)
            self.assertTrue(all("'C:/User''s Env/python.exe'" in line for line in command.splitlines()))
            self.assertIn("-m ensurepip", command)
            self.assertFalse(needs_uv)
            with patch.object(local.importlib.util, "find_spec", return_value=None):
                self.assertEqual(local.install_command(["numba"]), (local.shell_command(["uv", "pip", "install", "--python", local.sys.executable, "numba"]), True))
                self.assertEqual(local.install_command([]), ("", False))

    @unittest.skipUnless(os.name == "nt", "uses both Windows command shells")
    def test_windows_pasted_commands_preserve_arguments_in_both_shells(self):
        local = importlib.import_module(PACKAGE + ".local_backend")
        shells = (["cmd.exe", "/d", "/q", "/v:on"], ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "-"])
        with TemporaryDirectory() as tmp:
            script = Path(tmp, "echo_args.py")
            script.write_text("import json, sys\nprint('ARGS=' + json.dumps(sys.argv[1:]))", encoding="utf-8")
            for value in ["numba", "transformers>=4.56.0", "C:/User's Env/%PATH%/!TEMP!/$env/&/model"]:
                command = local.shell_command([sys.executable, "-B", script.name, value])
                for shell in shells:
                    with self.subTest(shell=shell[0], value=value):
                        run = subprocess.run(shell, input=command + "\n", text=True, capture_output=True, cwd=tmp, timeout=15)
                        self.assertEqual(run.returncode, 0, run.stderr)
                        self.assertIn("ARGS=" + json.dumps([value]), run.stdout)

    async def test_local_rejects_connection_credentials(self):
        response = await self.client.post(BASE + "/local/translate", json={"token": "test"})
        self.assertEqual(response.status, 400)

    async def test_local_first_translate_streams_progress_and_reuses_loaded_model(self):
        local = importlib.import_module(PACKAGE + ".local_backend")
        body = {"config": {"model_key": local.MODEL}, "source_text": "窓辺の猫", "target_lang": "en"}
        async def phases():
            response = await self.client.post(BASE + "/local/translate", json=body,
                                              headers={"Accept": "application/x-ndjson"})
            records = [json.loads(line) for line in (await response.text()).splitlines()]
            self.assertEqual(records[-1]["result"]["text"], "A cat by the window")
            return [row["phase"] for row in records[:-1]]
        def load(key):
            local._loaded = (key, None, None)
            return local._loaded[1:]
        with ExitStack() as s:
            for name, value in {"_loaded": None, "dependency_status": lambda: ([], None), "model_folders": lambda: {},
                                "load": load, "generate": lambda *args: ("A cat by the window", True)}.items():
                s.enter_context(patch.object(local, name, value))
            download = s.enter_context(patch.object(local, "download"))
            self.assertEqual(await phases(), ["downloading", "loading", "translating"])
            self.assertEqual(await phases(), ["translating"])
            download.assert_called_once_with()

    async def test_local_missing_dependencies_or_custom_model_do_not_download(self):
        local = importlib.import_module(PACKAGE + ".local_backend")
        for missing, key, expected in [(["numba"], local.MODEL, "LOCAL_DEPS"), ([], "custom", "LOCAL_MISSING")]:
            with patch.object(local, "dependency_status", return_value=(missing, None)), \
                 patch.object(local, "model_folders", return_value={}), patch.object(local, "download") as download:
                response = await self.client.post(BASE + "/local/translate", json={
                    "config": {"model_key": key}, "source_text": "窓辺の猫", "target_lang": "en"})
                self.assertEqual((await response.json())["code"], expected)
                download.assert_not_called()

    async def test_models_token_precedence_and_no_secret_output(self):
        result = {"models": [], "selected_model_status": "not_selected"}
        with patch.object(backend, "inspect_models", AsyncMock(return_value=result)) as inspect:
            with patch.dict(os.environ, {"COMFYUI_PLAMO_API_TOKEN": "env-secret"}):
                first = await self.client.post(BASE + "/models", json={"config": {}, "request_id": "req1"})
                self.assertEqual(inspect.await_args.args[1], "env-secret")
                second = await self.client.post(BASE + "/models", json={"config": {}, "token": "typed-secret", "request_id": "req2"})
                self.assertEqual(inspect.await_args.args[1], "typed-secret")
        self.assertEqual(first.status, 200)
        self.assertEqual((await first.json())["request_id"], "req1")
        second_body = await second.text()
        self.assertNotIn("secret", second_body)
        self.assertIn("req2", second_body)

    async def test_translation_passes_constant_inputs_and_partial(self):
        partial = {"status": "partial", "text": "A wom", "translation_state": None, "warnings": []}
        with patch.object(backend, "translate", AsyncMock(return_value=partial)) as translate:
            response = await self.client.post(BASE + "/translate", json={"config": {}, "source_text": "女性", "target_lang": "en", "request_id": "abc-123"})
        self.assertEqual(response.status, 200)
        self.assertEqual(translate.await_args.args[2:], ("女性", "en", "auto"))
        data = await response.json()
        self.assertEqual(data["status"], "partial")
        self.assertIsNone(data["translation_state"])

    async def test_busy_rejects_second_operation_and_releases_lock(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def pending(*args):
            started.set()
            await release.wait()
            return {"status": "translated", "text": "A woman", "translation_state": {}, "warnings": []}
        with patch.object(backend, "translate", pending), patch.object(backend, "inspect_models", AsyncMock(return_value={"models": []})) as inspect:
            first = asyncio.create_task(self.client.post(BASE + "/translate", json={"config": {}, "source_text": "女性", "target_lang": "en"}))
            await asyncio.wait_for(started.wait(), 2)
            busy = await self.client.post(BASE + "/models", json={"config": {}, "request_id": "busy-id"})
            self.assertEqual(busy.status, 409)
            self.assertEqual((await busy.json())["code"], "BUSY")
            inspect.assert_not_awaited()
            release.set()
            self.assertEqual((await first).status, 200)
            normal = await self.client.post(BASE + "/models", json={"config": {}})
            self.assertEqual(normal.status, 200)
        self.assertFalse(routes._OPERATION_LOCK.locked())

    async def test_error_mapping_and_lock_release(self):
        with patch.object(backend, "unload", AsyncMock(side_effect=backend.TranslationError("TIMEOUT", "固定メッセージ", 504))):
            response = await self.client.post(BASE + "/unload", json={"config": {}, "request_id": "r1"})
        self.assertEqual(response.status, 504)
        self.assertEqual(await response.json(), {"code": "TIMEOUT", "message": "固定メッセージ", "request_id": "r1"})
        self.assertFalse(routes._OPERATION_LOCK.locked())

    async def test_unexpected_exception_is_redacted(self):
        with patch.object(backend, "unload", AsyncMock(side_effect=RuntimeError("typed-secret PRIVATE-SOURCE"))):
            response = await self.client.post(BASE + "/unload", json={"config": {}, "token": "typed-secret"})
        self.assertEqual(response.status, 500)
        text = await response.text()
        self.assertNotIn("typed-secret", text)
        self.assertNotIn("PRIVATE-SOURCE", text)
        self.assertFalse(routes._OPERATION_LOCK.locked())

    async def test_invalid_json_inputs_do_not_call_backend(self):
        with patch.object(backend, "inspect_models", AsyncMock()) as inspect:
            for payload in [[], None, {"config": []}, {"token": 1}, {"token": "bad token"},
                            {"request_id": "bad\nid"}, {"instance_id": "arbitrary"},
                            {"config": {"url": "http://public.example"}}]:
                with self.subTest(payload=payload):
                    response = await self.client.post(BASE + "/models", json=payload)
                    self.assertEqual(response.status, 400)
            invalid = await self.client.post(BASE + "/models", data="{", headers={"Content-Type": "application/json"})
            self.assertEqual(invalid.status, 400)
            invalid = await self.client.post(BASE + "/models", data='{"config":{"timeout_seconds":NaN}}', headers={"Content-Type": "application/json"})
            self.assertEqual(invalid.status, 400)
        inspect.assert_not_awaited()

    async def test_body_limit_before_backend_call(self):
        with patch.object(backend, "inspect_models", AsyncMock()) as inspect:
            response = await self.client.post(BASE + "/models", data=b"x" * (routes.MAX_BODY_BYTES + 1), headers={"Content-Type": "application/json"})
        self.assertEqual(response.status, 400)
        self.assertEqual((await response.json())["code"], "REQUEST_TOO_LARGE")
        inspect.assert_not_awaited()

    async def test_non_json_and_non_post_rejected(self):
        response = await self.client.post(BASE + "/models", data="{}")
        self.assertEqual(response.status, 400)
        response = await self.client.get(BASE + "/models")
        self.assertEqual(response.status, 405)


if __name__ == "__main__":
    unittest.main()
