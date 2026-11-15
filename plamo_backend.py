"""PLaMo translation through a local LM Studio server.

Only aiohttp (already supplied by ComfyUI) and the standard library are used.
Importing this module opens no sockets and does not load any model.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from .state import LANGUAGES, MAX_OUTPUT_BYTES, MAX_OUTPUT_CHARS, MAX_SOURCE_BYTES, MAX_SOURCE_CHARS, make_state


DEFAULT_URL = "http://127.0.0.1:1234"
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_CHUNK_CHARS = 2_000
MIN_RETRY_CHARS = 64
STOP_TOKENS = ["<|plamo:tag|>", "<|plamo:bos|>"]
_SPLIT_BOUNDARIES = (
    re.compile(r"\r?\n(?:[^\S\r\n]*\r?\n)+"),
    re.compile(r"\r\n?|\n"),
    re.compile(r"(?<=[。！？!?])[^\S\r\n]*|(?<=\.)[^\S\r\n]+"),
    re.compile(r"[^\S\r\n]+"),
)


class TranslationError(Exception):
    """A fixed, user-facing error; never contains upstream response text."""

    def __init__(self, code: str, message: str, status: int = 400,
                 *, retryable_load: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable_load = retryable_load


@dataclass(frozen=True)
class Config:
    url: str = DEFAULT_URL
    model_key: str = ""
    timeout_seconds: float = 60
    max_tokens: int = 512


def _bad_config(message: str) -> TranslationError:
    return TranslationError("INVALID_CONFIG", message, 400)


def _has_control(text: str) -> bool:
    return any(unicodedata.category(char) in {"Cc", "Cs"} for char in text)


def normalize_url(value: Any) -> str:
    """Normalize a literal loopback HTTP origin, optionally followed by /v1."""
    if not isinstance(value, str) or len(value) > 512 or _has_control(value):
        raise _bad_config("接続先には同じPCのLM StudioのHTTP URLを入力してください。")
    value = value.strip()
    try:
        parts = urlsplit(value)
        if (parts.scheme.lower() != "http" or not parts.hostname
                or parts.username is not None or parts.password is not None
                or "?" in value or "#" in value
                or parts.path not in {"", "/", "/v1", "/v1/"}):
            raise ValueError
        host = parts.hostname.lower()
        if host == "localhost":
            host = "127.0.0.1"
        if "%" in host:
            raise ValueError
        address = ipaddress.ip_address(host)
        if not address.is_loopback:
            raise ValueError
        port = parts.port if parts.port is not None else 80
        if not 1 <= port <= 65535:
            raise ValueError
    except (ValueError, UnicodeError):
        raise _bad_config("初期版の接続先はloopbackのHTTP URLのみです。例: http://127.0.0.1:1234") from None
    formatted_host = f"[{address.compressed}]" if address.version == 6 else str(address)
    return f"http://{formatted_host}:{port}"


def parse_config(value: Any) -> Config:
    if isinstance(value, Config):
        value = {"url": value.url, "model_key": value.model_key,
                 "timeout_seconds": value.timeout_seconds, "max_tokens": value.max_tokens}
    if not isinstance(value, dict):
        raise _bad_config("configはJSONオブジェクトで指定してください。")
    if set(value) - {"url", "model_key", "timeout_seconds", "max_tokens"}:
        raise _bad_config("configに未対応の設定項目があります。")
    url = normalize_url(value.get("url", DEFAULT_URL))
    key = value.get("model_key", "")
    if not isinstance(key, str) or len(key) > 200 or _has_control(key):
        raise _bad_config("モデルkeyは制御文字を含まない200文字以下の文字列で指定してください。")
    if key != key.strip():
        raise _bad_config("モデルkeyの前後の空白を除いてください。")
    timeout = value.get("timeout_seconds", 60)
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or not 1 <= timeout <= 600):
        raise _bad_config("timeoutは1〜600秒で指定してください。")
    tokens = value.get("max_tokens", 512)
    if type(tokens) is not int or tokens not in {256, 512, 1024, 2048}:
        raise _bad_config("max_tokensは256、512、1024、2048から選択してください。")
    return Config(url, key, float(timeout), tokens)


_UNSUPPORTED_SYNTAX = re.compile(
    r"\([^()]*:\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*\)"
    r"|\[[^\[\]]*:\s*(?:[^\[\]]*:)?\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*\]"
    r"|__[^\r\n]+?__"
    r"|\{[^{}]*\|[^{}]*\}"
    r"|(?<!\w)(?:AND|BREAK)(?!\w)",
)
_LORA_SYNTAX = re.compile(r"<\s*(?:lora|lyco)\s*:", re.IGNORECASE)


def validate_source(source_text: Any, target_lang: Any, source_lang: Any = "auto") -> None:
    if not isinstance(source_text, str):
        raise TranslationError("INVALID_INPUT", "原文は文字列で指定してください。")
    if not source_text.strip():
        raise TranslationError("INPUT_EMPTY", "翻訳する原文を入力してください。")
    try:
        byte_count = len(source_text.encode("utf-8"))
    except UnicodeError:
        raise TranslationError("INVALID_INPUT", "原文に不正なUnicode文字が含まれています。") from None
    if len(source_text) > MAX_SOURCE_CHARS or byte_count > MAX_SOURCE_BYTES:
        raise TranslationError("INPUT_TOO_LONG", "原文は16,000文字かつUTF-8で64KiB以下にしてください。")
    if target_lang not in LANGUAGES or (source_lang != "auto" and source_lang not in LANGUAGES):
        raise TranslationError("INVALID_INPUT", "対応する原文の言語と翻訳先を選択してください。")
    if "<|plamo:" in source_text.lower():
        raise TranslationError("UNSUPPORTED_SYNTAX", "原文にPLaMo制御トークンを含めることはできません。")
    if _UNSUPPORTED_SYNTAX.search(source_text) or _LORA_SYNTAX.search(source_text):
        raise TranslationError("UNSUPPORTED_SYNTAX", "LoRA、重み、wildcard等を除き、翻訳したい自然文だけを入力してください。")


def build_prompt(source_text: str, target_lang: str, source_lang: str = "auto") -> str:
    validate_source(source_text, target_lang, source_lang)
    return _format_prompt(source_text, target_lang, source_lang)


def _format_prompt(source_text: str, target_lang: str, source_lang: str) -> str:
    input_language = "" if source_lang == "auto" else "<|plamo:key|>lang<|plamo:val|>" + source_lang
    return ("<|plamo:tag|>dataset<|plamo:msg|>translation"
            "<|plamo:tag|>input" + input_language + "<|plamo:msg|>" + source_text
            + "<|plamo:tag|>output<|plamo:key|>lang<|plamo:val|>"
            + target_lang + "<|plamo:msg|>")


def validate_token(token: Any) -> str:
    if not isinstance(token, str) or len(token) > 4096:
        raise _bad_config("API Tokenの形式が不正です。")
    token = token.strip()
    if any(ord(char) < 33 or ord(char) > 126 for char in token):
        raise _bad_config("API Tokenに空白や制御文字を含めないでください。")
    return token


def _response_error() -> TranslationError:
    return TranslationError("INVALID_RESPONSE", "LM Studioの応答形式を確認できませんでした。", 502)


def _timeout_error() -> TranslationError:
    return TranslationError("TIMEOUT", "LM Studioの処理が期限を超えました。原文、期限、モデル状態を確認してください。", 504)


def _error_fields(payload: Any) -> tuple[str, str]:
    """Read upstream diagnostics for classification only; never return them."""
    if not isinstance(payload, dict):
        return "", ""
    error = payload.get("error", {})
    if isinstance(error, str):
        return "", error.lower()
    if not isinstance(error, dict):
        return "", ""
    code = error.get("code", error.get("type", ""))
    message = error.get("message", "")
    return (code.lower() if isinstance(code, str) else "",
            message.lower() if isinstance(message, str) else "")


def _http_error(status: int, payload: Any, path: str) -> TranslationError:
    if status in {401, 403}:
        return TranslationError("AUTH_FAILED", "LM StudioのAPI Tokenと権限を確認してください。", 401)
    if 300 <= status < 400:
        return TranslationError("REDIRECT_REJECTED", "LM Studioの接続先がredirectを返しました。直接のloopback URLを指定してください。", 502)
    code, message = _error_fields(payload)
    if status < 500 and ("context" in code or "context" in message) and any(
            word in code + " " + message for word in ("length", "window", "exceed", "overflow", "limit", "token")):
        return TranslationError("CONTEXT_LIMIT", "モデルのcontext上限を超えました。原文や出力上限を小さくしてください。", 400)
    if path == "/api/v1/models" and status == 404:
        return TranslationError("LMSTUDIO_API_UNAVAILABLE", "LM Studioのnative v1 APIを利用できません。LM Studioのバージョンとサーバー設定を確認してください。", 502)
    if path == "/api/v1/models/load":
        # Only a definite conflict with a known unloading state permits a new
        # load POST. Transport failures and timeouts never enable that retry.
        retryable = status == 409 and code in {"model_unloading", "unload_in_progress"}
        return TranslationError("LOAD_FAILED", "モデルをロードできませんでした。LM Studioのモデル状態を確認してください。", 502,
                                retryable_load=retryable)
    return TranslationError("LMSTUDIO_REQUEST_FAILED", "LM Studioが要求を受け付けませんでした。モデルとサーバー設定を確認してください。", 502)


class _Client:
    def __init__(self, config: Config, token: str) -> None:
        self.config = config
        self.token = validate_token(token)
        self.deadline = asyncio.get_running_loop().time() + config.timeout_seconds
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "_Client":
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        self.session = aiohttp.ClientSession(headers=headers, trust_env=False)
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        if self.session is not None:
            await self.session.close()

    def remaining(self) -> float:
        remaining = self.deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise _timeout_error()
        return remaining

    async def request(self, method: str, path: str, payload: Any = None) -> Any:
        remaining = self.remaining()
        timeout = aiohttp.ClientTimeout(total=remaining, connect=min(3, remaining),
                                        sock_connect=min(3, remaining))
        assert self.session is not None
        try:
            async with self.session.request(method, self.config.url + path,
                                            json=payload, timeout=timeout,
                                            allow_redirects=False) as response:
                if response.content_length is not None and response.content_length > MAX_RESPONSE_BYTES:
                    raise TranslationError("RESPONSE_TOO_LARGE", "LM Studioの応答が1MiBを超えました。", 502)
                data = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    data.extend(chunk)
                    if len(data) > MAX_RESPONSE_BYTES:
                        raise TranslationError("RESPONSE_TOO_LARGE", "LM Studioの応答が1MiBを超えました。", 502)
                try:
                    parsed = json.loads(data.decode("utf-8"), parse_constant=_reject_json_constant)
                except (ValueError, UnicodeError, RecursionError):
                    parsed = None
                if not 200 <= response.status < 300:
                    raise _http_error(response.status, parsed, path)
                if not isinstance(parsed, dict):
                    raise _response_error()
                return parsed
        except (asyncio.TimeoutError, TimeoutError):
            raise _timeout_error() from None
        except aiohttp.ClientError:
            raise TranslationError("LMSTUDIO_UNAVAILABLE", "LM Studioへ接続できません。サーバー起動と接続先を確認してください。", 502) from None


def _reject_json_constant(value: str) -> None:
    raise ValueError("Non-finite JSON constant")


@dataclass(frozen=True)
class _Instance:
    id: str
    context_length: int | None = None


@dataclass(frozen=True)
class _Model:
    key: str
    display_name: str
    instances: tuple[_Instance, ...]


def _valid_identifier(value: Any, limit: int = 512) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit and not _has_control(value)


def _context_length(config: Any) -> int | None:
    if config is None:
        return None
    if not isinstance(config, dict):
        raise _response_error()
    length = config.get("context_length")
    if length is not None and (type(length) is not int or length <= 0):
        raise _response_error()
    return length


async def _list_models(client: _Client) -> list[_Model]:
    payload = await client.request("GET", "/api/v1/models")
    items = payload.get("models")
    if not isinstance(items, list):
        raise _response_error()
    models: list[_Model] = []
    keys: set[str] = set()
    instance_ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise _response_error()
        if item.get("type") != "llm":
            continue
        key, name, loaded = item.get("key"), item.get("display_name"), item.get("loaded_instances")
        if (not _valid_identifier(key, 200) or key in keys
                or not isinstance(name, str) or not isinstance(loaded, list)):
            raise _response_error()
        keys.add(key)
        instances: list[_Instance] = []
        for instance in loaded:
            if not isinstance(instance, dict):
                raise _response_error()
            instance_id = instance.get("id")
            if not _valid_identifier(instance_id) or instance_id in instance_ids:
                raise _response_error()
            instance_ids.add(instance_id)
            instances.append(_Instance(instance_id, _context_length(instance.get("config"))))
        models.append(_Model(key, name, tuple(instances)))
    return models


def _selected(models: list[_Model], key: str) -> _Model:
    if not key:
        raise TranslationError("MODEL_NOT_SELECTED", "モデル一覧を更新してPLaMoモデルを選択してください。", 400)
    for model in models:
        if model.key == key:
            if len(model.instances) > 1:
                raise TranslationError("AMBIGUOUS_MODEL", "選択モデルに複数のinstanceがあります。LM Studioで一つに整理してください。", 409)
            return model
    raise TranslationError("MODEL_NOT_FOUND", "選択モデルが見つかりません。モデル一覧を更新してください。", 400)


def _parse_loaded(payload: Any) -> _Instance:
    if (not isinstance(payload, dict) or payload.get("status") != "loaded"
            or payload.get("type") != "llm" or not _valid_identifier(payload.get("instance_id"))):
        raise _response_error()
    return _Instance(payload["instance_id"], _context_length(payload.get("load_config")))


async def _resolve_instance(client: _Client, models: list[_Model]) -> _Instance:
    model = _selected(models, client.config.model_key)
    if model.instances:
        return model.instances[0]
    body = {"model": model.key, "echo_load_config": True}
    try:
        return _parse_loaded(await client.request("POST", "/api/v1/models/load", body))
    except TranslationError as error:
        # Authentication/configuration failures are conclusive. An uncertain
        # load may be reconciled by one read, never by repeating its POST.
        if error.code in {"AUTH_FAILED", "INVALID_CONFIG", "CONTEXT_LIMIT", "TIMEOUT"}:
            raise
        client.remaining()
        refreshed = _selected(await _list_models(client), model.key)
        if refreshed.instances:
            return refreshed.instances[0]
        if error.retryable_load:
            return _parse_loaded(await client.request("POST", "/api/v1/models/load", body))
        raise error


async def inspect_models(config: Config | dict[str, Any], token: str = "") -> dict[str, Any]:
    config = parse_config(config)
    async with _Client(config, token) as client:
        models = await _list_models(client)
    selected = next((model for model in models if model.key == config.model_key), None)
    if not config.model_key:
        status = "not_selected"
    elif selected is None:
        status = "missing"
    else:
        status = "unloaded" if not selected.instances else "loaded" if len(selected.instances) == 1 else "ambiguous"
    return {"models": [{"key": model.key, "display_name": model.display_name,
                         "loaded_instance_count": len(model.instances)} for model in models],
            "selected_model_status": status}


def _split_source(source: str, start: int, limit: int) -> tuple[str, str, str]:
    for pattern in _SPLIT_BOUNDARIES:
        boundary = None
        for match in pattern.finditer(source, start):
            if match.start() > limit:
                break
            if match.end() < len(source):
                boundary = match
        if boundary is not None:
            return source[:boundary.start()], boundary.group(), source[boundary.end():]
    return source[:limit], "", source[limit:]


def _read_completion(payload: dict[str, Any]) -> tuple[str, str]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise _response_error()
    text, reason = choices[0].get("text"), choices[0].get("finish_reason")
    if (not isinstance(text, str) or not text.strip() or not isinstance(reason, str) or reason not in {"stop", "length"}
            or "<|plamo:" in text.lower() or len(text) > MAX_OUTPUT_CHARS):
        raise _response_error()
    try:
        if len(text.encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise _response_error()
    except UnicodeError:
        raise _response_error() from None
    return text, reason


async def _translate_source(client: _Client, instance: _Instance, source: str,
                            target_lang: str, source_lang: str, warnings: set[str]) -> tuple[str, bool]:
    if len(source) > MAX_CHUNK_CHARS:
        source = source.strip()
    if len(source) <= MAX_CHUNK_CHARS:
        prompt = _format_prompt(source, target_lang, source_lang)
        max_tokens = client.config.max_tokens
        try:
            payload = await client.request("POST", "/v1/completions", {
                "model": instance.id, "prompt": prompt, "temperature": 0,
                "max_tokens": max_tokens, "stop": STOP_TOKENS, "stream": False,
            })
        except TranslationError as error:
            if error.code != "CONTEXT_LIMIT" or len(source.strip()) < MIN_RETRY_CHARS:
                raise
        else:
            text, reason = _read_completion(payload)
            if reason == "stop" or len(source.strip()) < MIN_RETRY_CHARS:
                if (instance.context_length is not None
                        and len(prompt.encode("utf-8")) + max_tokens > instance.context_length):
                    warnings.add("原文と出力上限がcontextを超える可能性があります。これはbyte数による保守的な目安です。")
                return text, reason == "stop"

    source = source.strip()
    if len(source) > MAX_CHUNK_CHARS:
        start, limit = 1, MAX_CHUNK_CHARS
    else:
        # After a cutoff, look for a boundary near the middle so both pieces get smaller.
        start, limit = len(source) // 4, len(source) // 2
    head, separator, tail = _split_source(source, start, limit)
    left, complete = await _translate_source(client, instance, head, target_lang, source_lang, warnings)
    if not complete:
        return left, False
    right, complete = await _translate_source(client, instance, tail, target_lang, source_lang, warnings)
    joined = left.rstrip() + (separator or " ") + right.lstrip()
    if len(joined) > MAX_OUTPUT_CHARS or len(joined.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise TranslationError("OUTPUT_TOO_LONG", "結合した訳文の上限を超えました。原文を短くしてください。")
    return joined, complete


async def translate(config: Config | dict[str, Any], token: str, source_text: str,
                    target_lang: str, source_lang: str = "auto") -> dict[str, Any]:
    config = parse_config(config)
    validate_source(source_text, target_lang, source_lang)
    if not config.model_key:
        raise TranslationError("MODEL_NOT_SELECTED", "モデル一覧を更新してPLaMoモデルを選択してください。", 400)
    warnings: set[str] = set()
    async with _Client(config, token) as client:
        instance = await _resolve_instance(client, await _list_models(client))
        text, complete = await _translate_source(client, instance, source_text, target_lang, source_lang, warnings)
    return {"status": "translated" if complete else "partial", "text": text,
            "translation_state": make_state(source_text, target_lang, status="translated",
                                            model_key=config.model_key, source_lang=source_lang) if complete else None,
            "warnings": sorted(warnings)}


async def unload(config: Config | dict[str, Any], token: str = "") -> dict[str, Any]:
    config = parse_config(config)
    if not config.model_key:
        raise TranslationError("MODEL_NOT_SELECTED", "解放するモデルを選択してください。", 400)
    async with _Client(config, token) as client:
        model = _selected(await _list_models(client), config.model_key)
        if not model.instances:
            return {"status": "already_unloaded"}
        instance_id = model.instances[0].id
        payload = await client.request("POST", "/api/v1/models/unload", {"instance_id": instance_id})
        if payload.get("instance_id") != instance_id:
            raise _response_error()
        return {"status": "unloaded", "instance_id": instance_id}
