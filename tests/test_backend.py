"""Backend contract tests against a real loopback HTTP mock, without an LLM."""

import asyncio
import importlib
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestServer


PACKAGE = "_plamo_contract_tests"
if PACKAGE not in sys.modules:
    module = types.ModuleType(PACKAGE)
    module.__path__ = [str(Path(__file__).resolve().parents[1])]
    sys.modules[PACKAGE] = module
backend = importlib.import_module(PACKAGE + ".plamo_backend")


def model(instances=None, key="pfnet/plamo", model_type="llm"):
    if instances is None:
        instances = [{"id": "plamo-active", "config": {"context_length": 4096}}]
    return {"type": model_type, "key": key, "display_name": "PLaMo test model",
            "loaded_instances": instances}


class ValidationTests(unittest.TestCase):
    def test_url_normalization_and_loopback_only(self):
        self.assertEqual(backend.normalize_url("http://localhost:1234/v1/"), "http://127.0.0.1:1234")
        self.assertEqual(backend.normalize_url("http://[::1]:1234/"), "http://[::1]:1234")
        denied = ["https://localhost:1234", "http://example.com", "http://127.0.0.1.evil.test",
                  "http://127.0.0.1:1234/other", "http://user:pass@127.0.0.1:1234",
                  "http://127.0.0.1:1234?", "http://127.0.0.1:1234#",
                  "http://[::1%25lo]:1234", "http://127.0.0.1:0", "http://localhost:1234\n"]
        for url in denied:
            with self.subTest(url=url), self.assertRaises(backend.TranslationError):
                backend.normalize_url(url)

    def test_config_type_and_bounds(self):
        self.assertEqual(backend.parse_config({}).max_tokens, 512)
        for config in [None, [], {"timeout_seconds": True}, {"timeout_seconds": 0},
                       {"timeout_seconds": 601}, {"timeout_seconds": float("nan")},
                       {"max_tokens": 513}, {"max_tokens": "512"},
                       {"model_key": "x\n"}, {"model_key": "x" * 201}, {"unknown": 1}]:
            with self.subTest(config=config), self.assertRaises(backend.TranslationError):
                backend.parse_config(config)

    def test_prompt_exact_whitespace_and_parentheses(self):
        source = "  女性（成人）。\nwoman (adult)  "
        prompt = backend.build_prompt(source, "en")
        self.assertEqual(prompt,
            "<|plamo:tag|>dataset<|plamo:msg|>translation"
            "<|plamo:tag|>input<|plamo:msg|>" + source +
            "<|plamo:tag|>output<|plamo:key|>lang<|plamo:val|>en<|plamo:msg|>")
        self.assertNotIn("<|plamo:bos|>", prompt)
        self.assertTrue(backend.build_prompt("A woman.", "ja").endswith("ja<|plamo:msg|>"))

    def test_source_rejects_controls_without_rejecting_natural_parentheses(self):
        for source in ["<lora:name:1>", "<LoRA:name:1>", "<lyco:name:0.5>",
                       "(red dress:1.2)", "[red:blue:0.5]", "__outfit__",
                       "{red|blue}", "one AND two", "one BREAK two", "<|plamo:tag|>"]:
            with self.subTest(source=source), self.assertRaises(backend.TranslationError) as caught:
                backend.validate_source(source, "en")
            self.assertEqual(caught.exception.code, "UNSUPPORTED_SYNTAX")
        for source in ["Woman (adult)", "女性（成人）", "sand and clouds", "A BREAKFAST scene"]:
            backend.validate_source(source, "en")

    def test_source_limits_surrogates_and_target(self):
        for source, target, code in [(" ", "en", "INPUT_EMPTY"), ("x" * (backend.MAX_SOURCE_CHARS + 1), "en", "INPUT_TOO_LONG"),
                                     ("\ud800", "en", "INVALID_INPUT"), ("text", [], "INVALID_INPUT")]:
            with self.subTest(code=code), self.assertRaises(backend.TranslationError) as caught:
                backend.validate_source(source, target)
            self.assertEqual(caught.exception.code, code)


class BackendHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.models = {"models": [model()]}
        self.completion = {"choices": [{"text": "  A woman.\n", "finish_reason": "stop"}]}
        self.load_reply = {"type": "llm", "status": "loaded", "instance_id": "fresh-instance",
                           "load_config": {"context_length": 4096}}
        self.overrides = {}

        async def dispatch(request):
            body = await request.json() if request.method == "POST" else None
            self.calls.append((request.method, request.path, body, request.headers.get("Authorization")))
            override = self.overrides.get(request.path)
            if override is not None:
                return await override(request, body)
            if request.path == "/api/v1/models":
                return web.json_response(self.models)
            if request.path == "/api/v1/models/load":
                return web.json_response(self.load_reply)
            if request.path == "/api/v1/models/unload":
                return web.json_response({"instance_id": body["instance_id"]})
            if request.path == "/v1/completions":
                return web.json_response(self.completion)
            return web.Response(status=404)

        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", dispatch)
        self.server = TestServer(app)
        await self.server.start_server()
        self.config = {"url": str(self.server.make_url("")), "model_key": "pfnet/plamo",
                       "timeout_seconds": 5, "max_tokens": 512}

    async def asyncTearDown(self):
        await self.server.close()

    async def test_models_does_not_load_or_select_another_model(self):
        self.models["models"].append(model([], "embedding", "embedding"))
        result = await backend.inspect_models({**self.config, "model_key": "missing"})
        self.assertEqual(result["selected_model_status"], "missing")
        self.assertEqual(result["models"], [{"key": "pfnet/plamo", "display_name": "PLaMo test model", "loaded_instance_count": 1}])
        self.assertEqual([call[0] for call in self.calls], ["GET"])

    async def test_loaded_translation_uses_instance_and_preserves_result(self):
        source = " 女性（成人）。\n"
        result = await backend.translate(self.config, "fake-token", source, "en")
        self.assertEqual(result["status"], "translated")
        self.assertEqual(result["text"], "  A woman.\n")
        self.assertEqual(result["translation_state"]["source_snapshot"], source)
        self.assertEqual(result["translation_state"]["model_key"], "pfnet/plamo")
        payload = self.calls[-1][2]
        self.assertEqual(payload["model"], "plamo-active")
        self.assertEqual(payload["prompt"], backend.build_prompt(source, "en"))
        self.assertEqual(payload["stop"], ["<|plamo:tag|>", "<|plamo:bos|>"])
        self.assertFalse(payload["stream"])
        self.assertEqual(self.calls[0][3], "Bearer fake-token")
        self.assertEqual([call[1] for call in self.calls], ["/api/v1/models", "/v1/completions"])

    async def test_unloaded_model_loads_only_selected_key_and_uses_returned_instance(self):
        self.models = {"models": [model([])]}
        self.load_reply["load_config"]["context_length"] = 128
        result = await backend.translate(self.config, "", "女性", "en")
        self.assertEqual(self.calls[1][2], {"model": "pfnet/plamo", "echo_load_config": True})
        self.assertEqual(self.calls[2][2]["model"], "fresh-instance")
        self.assertTrue(result["warnings"])
        self.assertTrue(all(call[3] is None for call in self.calls))

    async def test_ambiguous_and_missing_model_never_load_or_translate(self):
        self.models = {"models": [model([{"id": "one"}, {"id": "two"}])]}
        with self.assertRaises(backend.TranslationError) as caught:
            await backend.translate(self.config, "", "女性", "en")
        self.assertEqual((caught.exception.code, caught.exception.status), ("AMBIGUOUS_MODEL", 409))
        self.models = {"models": [model([], "another")]}
        with self.assertRaises(backend.TranslationError) as caught:
            await backend.translate(self.config, "", "女性", "en")
        self.assertEqual(caught.exception.code, "MODEL_NOT_FOUND")
        self.assertTrue(all(call[0] == "GET" for call in self.calls))

    async def test_partial_has_no_accepted_state(self):
        self.completion["choices"][0]["finish_reason"] = "length"
        result = await backend.translate(self.config, "", "女性", "en")
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["translation_state"])

    async def test_long_translation_preserves_paragraph_order_and_complete_snapshot(self):
        paragraphs = [letter * 1_100 for letter in ("あ", "い", "う")]
        separator = "\r\n \r\n"
        source = separator.join(paragraphs)
        outputs = dict(zip(paragraphs, [f"{word} " * 900 for word in ("one", "two", "three")]))

        async def translate_chunk(request, body):
            prompt = body["prompt"]
            paragraph = next(piece for piece in paragraphs if piece in prompt)
            self.assertEqual(prompt, backend.build_prompt(paragraph, "en", "ja"))
            self.assertEqual(body["max_tokens"], 2_048)
            return web.json_response({"choices": [{"text": outputs[paragraph], "finish_reason": "stop"}]})

        self.overrides["/v1/completions"] = translate_chunk
        result = await backend.translate({**self.config, "max_tokens": 2_048}, "", source, "en", "ja")
        self.assertEqual(result["status"], "translated")
        self.assertEqual(result["text"], separator.join(outputs[part].rstrip() for part in paragraphs[:-1]) + separator + outputs[paragraphs[-1]])
        self.assertEqual(result["translation_state"]["source_snapshot"], source)
        self.assertEqual(result["translation_state"]["source_lang"], "ja")
        self.assertEqual([call[1] for call in self.calls], ["/api/v1/models"] + ["/v1/completions"] * 3)

    async def test_cutoff_is_retried_at_paragraph_boundary_without_reusing_incomplete_text(self):
        first, second = "前" * 350, "後" * 350
        source = first + "\n\n" + second
        replies = {source: ("Discard this", "length"), first: ("First.", "stop"), second: ("Last.", "stop")}
        prompts = {backend.build_prompt(part, "en"): reply for part, reply in replies.items()}

        async def translate_chunk(request, body):
            text, reason = prompts[body["prompt"]]
            return web.json_response({"choices": [{"text": text, "finish_reason": reason}]})

        self.overrides["/v1/completions"] = translate_chunk
        result = await backend.translate(self.config, "", source, "en")
        self.assertEqual(result["text"], "First.\n\nLast.")
        self.assertEqual(result["status"], "translated")
        self.assertEqual([call[2]["prompt"] for call in self.calls[1:]], list(prompts))

    async def test_long_single_paragraph_can_split_on_sentence_after_context_limit(self):
        first, second = "静かな待合室。" * 26, "改札は右にある。" * 26
        source = first + second
        first_inference = True
        translated_sources = []

        async def translate_chunk(request, body):
            nonlocal first_inference
            prompt = body["prompt"]
            if first_inference:
                first_inference = False
                return web.json_response({"error": {"message": "context length exceeded"}}, status=400)
            part = prompt.split("<|plamo:msg|>")[2].split("<|plamo:tag|>output")[0]
            self.assertTrue(part.endswith("。"))
            translated_sources.append(part)
            return web.json_response({"choices": [{"text": "Complete.", "finish_reason": "stop"}]})

        self.overrides["/v1/completions"] = translate_chunk
        result = await backend.translate(self.config, "", source, "en")
        self.assertEqual("".join(translated_sources), source)
        self.assertEqual(result["status"], "translated")
        self.assertEqual(result["text"], "Complete. Complete.")

    async def test_malformed_completion_is_classified(self):
        invalid = [{"choices": []}, {"choices": [{"text": " ", "finish_reason": "stop"}]},
                   {"choices": [{"text": "answer", "finish_reason": []}]},
                   {"choices": [{"text": "answer", "finish_reason": "tool_calls"}]},
                   {"choices": [{"text": "<|plamo:tag|>", "finish_reason": "stop"}]},
                   {"choices": [{"text": "\ud800", "finish_reason": "stop"}]}]
        for completion in invalid:
            self.completion = completion
            with self.subTest(completion=completion), self.assertRaises(backend.TranslationError) as caught:
                await backend.translate(self.config, "", "女性", "en")
            self.assertEqual(caught.exception.code, "INVALID_RESPONSE")

    async def test_auth_failure_does_not_echo_secret_or_retry(self):
        async def unauthorized(request, body):
            return web.json_response({"error": {"message": "fake-secret PRIVATE-SOURCE"}}, status=401)
        self.overrides["/api/v1/models"] = unauthorized
        with self.assertRaises(backend.TranslationError) as caught:
            await backend.translate(self.config, "fake-secret", "PRIVATE-SOURCE", "en")
        self.assertEqual(caught.exception.code, "AUTH_FAILED")
        self.assertNotIn("fake-secret", str(caught.exception))
        self.assertNotIn("PRIVATE-SOURCE", str(caught.exception))
        self.assertEqual(len(self.calls), 1)

    async def test_context_failure_is_a_fixed_message(self):
        async def context_error(request, body):
            return web.json_response({"error": {"message": "PRIVATE-SOURCE exceeds context length"}}, status=400)
        self.overrides["/v1/completions"] = context_error
        with self.assertRaises(backend.TranslationError) as caught:
            await backend.translate(self.config, "", "女性", "en")
        self.assertEqual(caught.exception.code, "CONTEXT_LIMIT")
        self.assertNotIn("PRIVATE-SOURCE", str(caught.exception))

    async def test_redirect_is_not_followed(self):
        async def redirect(request, body):
            return web.Response(status=302, headers={"Location": str(self.server.make_url("/elsewhere"))})
        self.overrides["/api/v1/models"] = redirect
        with self.assertRaises(backend.TranslationError) as caught:
            await backend.inspect_models(self.config)
        self.assertEqual(caught.exception.code, "REDIRECT_REJECTED")
        self.assertEqual(len(self.calls), 1)

    async def test_response_size_is_bounded(self):
        async def oversized(request, body):
            return web.Response(body=b"x" * (backend.MAX_RESPONSE_BYTES + 1))
        self.overrides["/api/v1/models"] = oversized
        with self.assertRaises(backend.TranslationError) as caught:
            await backend.inspect_models(self.config)
        self.assertEqual(caught.exception.code, "RESPONSE_TOO_LARGE")

    async def test_one_deadline_covers_all_requests(self):
        async def slow_models(request, body):
            await asyncio.sleep(0.6)
            return web.json_response(self.models)
        async def slow_completion(request, body):
            await asyncio.sleep(0.6)
            return web.json_response(self.completion)
        self.overrides["/api/v1/models"] = slow_models
        self.overrides["/v1/completions"] = slow_completion
        with self.assertRaises(backend.TranslationError) as caught:
            await backend.translate({**self.config, "timeout_seconds": 1}, "", "女性", "en")
        self.assertEqual(caught.exception.code, "TIMEOUT")
        self.assertEqual(len(self.calls), 2)

    async def test_uncertain_load_is_reconciled_without_duplicate_post(self):
        self.models = {"models": [model([])]}
        async def disconnect_after_load(request, body):
            self.models = {"models": [model([{"id": "actually-loaded"}])]}
            request.transport.close()
            return web.Response()
        self.overrides["/api/v1/models/load"] = disconnect_after_load
        result = await backend.translate(self.config, "", "女性", "en")
        self.assertEqual(result["status"], "translated")
        self.assertEqual(sum(call[1] == "/api/v1/models/load" for call in self.calls), 1)
        self.assertEqual(sum(call[1] == "/api/v1/models" for call in self.calls), 2)
        self.assertEqual(self.calls[-1][2]["model"], "actually-loaded")

    async def test_uncertain_failed_load_is_not_reposted(self):
        self.models = {"models": [model([])]}
        async def failed_load(request, body):
            return web.json_response({"error": {"message": "unknown server failure"}}, status=500)
        self.overrides["/api/v1/models/load"] = failed_load
        with self.assertRaises(backend.TranslationError) as caught:
            await backend.translate(self.config, "", "女性", "en")
        self.assertEqual(caught.exception.code, "LOAD_FAILED")
        self.assertEqual([call[1] for call in self.calls],
                         ["/api/v1/models", "/api/v1/models/load", "/api/v1/models"])

    async def test_definite_unloading_conflict_retries_at_most_once(self):
        self.models = {"models": [model([])]}
        async def unloading(request, body):
            return web.json_response({"error": {"code": "model_unloading"}}, status=409)
        self.overrides["/api/v1/models/load"] = unloading
        with self.assertRaises(backend.TranslationError) as caught:
            await backend.translate(self.config, "", "女性", "en")
        self.assertEqual(caught.exception.code, "LOAD_FAILED")
        self.assertEqual(sum(call[1] == "/api/v1/models/load" for call in self.calls), 2)
        self.assertEqual(sum(call[1] == "/api/v1/models" for call in self.calls), 2)

    async def test_unload_is_targeted_and_unloaded_is_noop(self):
        result = await backend.unload(self.config)
        self.assertEqual(result, {"status": "unloaded", "instance_id": "plamo-active"})
        self.assertEqual(self.calls[-1][2], {"instance_id": "plamo-active"})
        self.models = {"models": [model([])]}
        self.calls.clear()
        self.assertEqual(await backend.unload(self.config), {"status": "already_unloaded"})
        self.assertEqual(len(self.calls), 1)

    async def test_unload_ambiguous_model_does_not_guess(self):
        self.models = {"models": [model([{"id": "one"}, {"id": "two"}])]}
        with self.assertRaises(backend.TranslationError) as caught:
            await backend.unload(self.config)
        self.assertEqual(caught.exception.code, "AMBIGUOUS_MODEL")
        self.assertEqual(len(self.calls), 1)

    async def test_proxy_environment_is_ignored(self):
        with patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1", "http_proxy": "http://127.0.0.1:1", "NO_PROXY": "", "no_proxy": ""}):
            result = await backend.inspect_models(self.config)
        self.assertEqual(result["selected_model_status"], "loaded")


if __name__ == "__main__":
    unittest.main()
