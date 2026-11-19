"""ComfyUI routes for explicit, manual translation operations."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

from aiohttp import web

from . import plamo_backend as backend


# JSON escaping can expand the original text before it is decoded and validated.
MAX_BODY_BYTES = 128 * 1024
_OPERATION_LOCK = asyncio.Lock()
_REQUEST_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")


async def _read_body(request: web.Request) -> dict[str, Any]:
    if request.content_type != "application/json":
        raise backend.TranslationError("INVALID_REQUEST", "Send a JSON request body.", 400)
    if request.content_length is not None and request.content_length > MAX_BODY_BYTES:
        raise backend.TranslationError("REQUEST_TOO_LARGE", "The request body exceeds 128 KiB.", 400)
    data = bytearray()
    async for chunk in request.content.iter_chunked(8192):
        data.extend(chunk)
        if len(data) > MAX_BODY_BYTES:
            raise backend.TranslationError("REQUEST_TOO_LARGE", "The request body exceeds 128 KiB.", 400)
    try:
        body = json.loads(data.decode("utf-8"), parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise backend.TranslationError("INVALID_REQUEST", "The request body is not valid JSON.", 400) from None
    if not isinstance(body, dict):
        raise backend.TranslationError("INVALID_REQUEST", "The request body must be a JSON object.", 400)
    return body


def _reject_constant(value: str) -> None:
    raise ValueError("Non-finite JSON constant")


def _request_id(value: Any) -> str:
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or _REQUEST_ID.fullmatch(value) is None:
        raise backend.TranslationError("INVALID_REQUEST", "Invalid request_id format.", 400)
    return value


def _token(body: dict[str, Any]) -> str:
    supplied = backend.validate_token(body.get("token", ""))
    return supplied or backend.validate_token(os.environ.get("COMFYUI_PLAMO_API_TOKEN", ""))


async def _handle(request: web.Request, operation: str) -> web.Response:
    request_id = ""
    try:
        body = await _read_body(request)
        request_id = _request_id(body.get("request_id"))
        allowed = {"config", "token", "request_id"}
        if operation == "translate":
            allowed.update({"source_text", "target_lang", "source_lang"})
        if set(body) - allowed:
            raise backend.TranslationError("INVALID_REQUEST", "The request contains an unsupported field.", 400)
        config = backend.parse_config(body.get("config", {}))
        token = _token(body)
        if _OPERATION_LOCK.locked():
            raise backend.TranslationError("BUSY", "Another model operation is in progress. Wait for it to finish.", 409)
        # There is no suspension between the locked check and an uncontended
        # acquire, so another handler cannot enter the operation concurrently.
        await _OPERATION_LOCK.acquire()
        try:
            if operation == "models":
                result = await backend.inspect_models(config, token)
            elif operation == "translate":
                result = await backend.translate(config, token, body.get("source_text"), body.get("target_lang"),
                                                 body.get("source_lang", "auto"))
            else:
                result = await backend.unload(config, token)
        finally:
            _OPERATION_LOCK.release()
        return web.json_response({**result, "request_id": request_id})
    except backend.TranslationError as error:
        return web.json_response({"code": error.code, "message": error.message,
                                  "request_id": request_id}, status=error.status)
    except asyncio.CancelledError:
        raise
    except Exception:
        # Never serialize an exception, request body, or token into the response.
        return web.json_response({"code": "INTERNAL_ERROR", "message": "An internal error occurred while processing the translation.",
                                  "request_id": request_id}, status=500)


async def models_handler(request: web.Request) -> web.Response:
    return await _handle(request, "models")


async def translate_handler(request: web.Request) -> web.Response:
    return await _handle(request, "translate")


async def unload_handler(request: web.Request) -> web.Response:
    return await _handle(request, "unload")


async def local_handler(request: web.Request) -> web.StreamResponse:
    from . import local_backend
    request_id = ""
    stream = None
    status = 200

    async def report(phase):
        nonlocal stream
        if stream is None:
            stream = web.StreamResponse(headers={"Content-Type": "application/x-ndjson",
                                                 "Cache-Control": "no-store", "X-Accel-Buffering": "no"})
            await stream.prepare(request)
        await stream.write((json.dumps({"phase": phase}) + "\n").encode())

    try:
        body = await _read_body(request)
        request_id = _request_id(body.get("request_id"))
        action = request.match_info["action"]
        allowed = {"request_id"}
        if action == "translate":
            allowed.update({"config", "source_text", "target_lang", "source_lang"})
        if set(body) - allowed:
            raise backend.TranslationError("INVALID_REQUEST", "Unsupported local request fields.")
        # A browser disconnect must not unlock the model while its worker is still running.
        progress = action == "translate" and request.headers.get("Accept") == "application/x-ndjson"
        result = await asyncio.shield(local_backend.operate(action, body, report if progress else None))
    except backend.TranslationError as error:
        result = {"code": error.code, "message": error.message}
        status = error.status
    except ConnectionResetError:
        raise
    except Exception:
        logging.exception("Local PLaMo operation failed")
        result = {"code": "LOCAL_ERROR"}
        status = 500
    result["request_id"] = request_id
    if stream is None:
        return web.json_response(result, status=status)
    await stream.write_eof((json.dumps({"result": result}) + "\n").encode())
    return stream


def register_routes(server: Any = None) -> bool:
    """Register once on a PromptServer instance; importing server is deferred."""
    if server is None:
        from server import PromptServer
        server = PromptServer.instance
    if server is None:
        raise RuntimeError("PromptServer is not initialized")
    route_table = server.routes
    if getattr(route_table, "_plamo_translator_registered", False):
        return False
    route_table.post("/plamo_translator/v1/models")(models_handler)
    route_table.post("/plamo_translator/v1/translate")(translate_handler)
    route_table.post("/plamo_translator/v1/unload")(unload_handler)
    route_table.post("/plamo_translator/v1/local/{action:setup|translate|download|unload}")(local_handler)
    route_table._plamo_translator_registered = True
    return True
