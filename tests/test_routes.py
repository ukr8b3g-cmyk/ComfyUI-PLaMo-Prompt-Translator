"""Route validation, token isolation, bounded requests, and mutual exclusion."""

import asyncio
import importlib
import os
from pathlib import Path
import sys
import types
import unittest
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
        self.assertEqual(len(self.server_stub.routes), 3)

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
