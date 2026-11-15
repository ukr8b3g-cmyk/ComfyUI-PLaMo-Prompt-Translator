"""Serializable text acceptance state, independent of ComfyUI and networking."""

import json
from pathlib import Path

LANGUAGES = tuple(json.loads(Path(__file__).with_name("web").joinpath("languages.json").read_text(encoding="utf-8")))
STATE_VERSION = 1
PROMPT_FORMAT = "pfml3-completions-v1"
MAX_SOURCE_CHARS = 16_000
MAX_SOURCE_BYTES = 64 * 1024
MAX_OUTPUT_CHARS = 64_000
MAX_OUTPUT_BYTES = 256 * 1024
MAX_STATE_BYTES = 128 * 1024


def _fail(code, message):
    raise ValueError(f"PLaMo {code}: {message}")


def _check_text(value, label, max_chars, max_bytes, allow_empty=False):
    if not isinstance(value, str):
        _fail("INVALID_INPUT", f"{label} must be text.")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        _fail("INVALID_INPUT", f"{label} contains invalid Unicode.")
    if len(value) > max_chars or size > max_bytes:
        _fail("INPUT_TOO_LONG", f"{label} exceeds the text limit.")
    if not allow_empty and not value.strip():
        _fail("INPUT_EMPTY", f"{label} is empty. 翻訳または訳文の入力をしてください。")


def make_state(source_text, target_lang, status="translated", model_key="", source_lang="auto"):
    """Capture source/direction when a translation or explicit manual text is accepted."""
    if status not in ("translated", "manual"):
        _fail("INVALID_STATE", "Unsupported acceptance state.")
    if target_lang not in LANGUAGES or (source_lang != "auto" and source_lang not in LANGUAGES):
        _fail("INVALID_INPUT", "Choose a supported source and target language.")
    _check_text(source_text, "Source", MAX_SOURCE_CHARS, MAX_SOURCE_BYTES,
                allow_empty=status == "manual")
    if not isinstance(model_key, str) or len(model_key) > 200 or any(ord(c) < 32 for c in model_key):
        _fail("INVALID_INPUT", "Invalid model identifier.")
    return {
        "v": STATE_VERSION,
        "status": status,
        "source_snapshot": source_text,
        **({"source_lang": source_lang} if source_lang != "auto" else {}),
        "target_lang": target_lang,
        "model_key": model_key,
        "format": PROMPT_FORMAT,
    }


def validate_output(source_text, target_lang, output_text, translation_state, source_lang="auto"):
    """Return verified state; never change the text being sent to downstream nodes."""
    _check_text(output_text, "Output", MAX_OUTPUT_CHARS, MAX_OUTPUT_BYTES)
    _check_text(source_text, "Source", MAX_SOURCE_CHARS, MAX_SOURCE_BYTES, allow_empty=True)
    if target_lang not in LANGUAGES or (source_lang != "auto" and source_lang not in LANGUAGES):
        _fail("INVALID_INPUT", "Choose a supported source and target language.")
    if not isinstance(translation_state, str) or not translation_state:
        _fail("INVALID_STATE", "Translate first, or choose 'Use current text'. 再翻訳か「現在の訳文を使う」を選択してください。")
    try:
        if len(translation_state.encode("utf-8")) > MAX_STATE_BYTES:
            _fail("INVALID_STATE", "Saved translation state is too large.")
        state = json.loads(translation_state)
    except (ValueError, RecursionError, UnicodeError):
        _fail("INVALID_STATE", "Saved translation state is invalid. 訳文を再確認してください。")
    if not isinstance(state, dict) or type(state.get("v")) is not int or state["v"] != STATE_VERSION:
        _fail("INVALID_STATE", "Unknown saved state version. 訳文を再確認してください。")
    if state.get("status") not in ("translated", "manual"):
        _fail("INVALID_STATE", "This text has not been accepted. 訳文を再確認してください。")
    saved_source_lang = state.get("source_lang", "auto")
    if (not isinstance(state.get("source_snapshot"), str) or state.get("target_lang") not in LANGUAGES
            or (saved_source_lang != "auto" and saved_source_lang not in LANGUAGES)):
        _fail("INVALID_STATE", "Saved source or direction is invalid.")
    if (state["source_snapshot"] != source_text or state["target_lang"] != target_lang
            or saved_source_lang != source_lang):
        _fail("STALE_RESULT", "Source or direction changed. 再翻訳か「現在の訳文を使う」を選択してください。")
    if state["status"] == "translated" and not source_text.strip():
        _fail("INVALID_STATE", "A translated result requires a nonempty source.")
    if state.get("format", PROMPT_FORMAT) != PROMPT_FORMAT:
        _fail("INVALID_STATE", "Unknown translation format. 訳文を再確認してください。")
    if not isinstance(state.get("model_key", ""), str) or len(state.get("model_key", "")) > 200:
        _fail("INVALID_STATE", "Invalid saved model identifier.")
    return state
