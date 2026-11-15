"""Offline acceptance checks for the saved-text node and its state contract.

These tests deliberately do not contact LM Studio or claim translation quality.
Run with: python -m unittest discover -s tests -v
"""

import importlib
import json
from pathlib import Path
import sys
import types
import unittest
from unittest import mock
import uuid


ROOT = Path(__file__).resolve().parents[1]


class NodeAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Import the two pure modules without executing server registration.
        cls.package_name = "plamo_node_acceptance_" + uuid.uuid4().hex
        package = types.ModuleType(cls.package_name)
        package.__path__ = [str(ROOT)]
        sys.modules[cls.package_name] = package
        cls.state = importlib.import_module(cls.package_name + ".state")
        cls.nodes = importlib.import_module(cls.package_name + ".nodes")

    @classmethod
    def tearDownClass(cls):
        for name in list(sys.modules):
            if name == cls.package_name or name.startswith(cls.package_name + "."):
                sys.modules.pop(name, None)

    def setUp(self):
        self.source = "赤いコートを着た女性。\n夕方の駅。"
        self.target = "en"
        self.output = "A woman in a red coat.\nAn evening station.  \n"
        self.node = self.nodes.PLaMoPromptTranslator()

    def state_string(self, source=None, target=None, status="translated", **changes):
        state = self.state.make_state(
            self.source if source is None else source,
            self.target if target is None else target,
            status=status,
            model_key="pfnet/plamo-test",
        )
        state.update(changes)
        return json.dumps(state, ensure_ascii=False)

    def emit(self, output=None, state_string=None, **kwargs):
        return self.node.emit_text(
            self.source,
            self.target,
            self.output if output is None else output,
            self.state_string() if state_string is None else state_string,
            **kwargs,
        )

    def test_registered_schema_has_one_text_output_and_four_saved_inputs(self):
        cls = self.nodes.PLaMoPromptTranslator
        schema = cls.INPUT_TYPES()
        self.assertEqual(
            set(schema["required"]),
            {"source_text", "target_lang", "output_text", "translation_state"},
        )
        self.assertEqual(cls.RETURN_TYPES, ("STRING",))
        self.assertEqual(cls.RETURN_NAMES, ("text",))
        self.assertEqual(cls.FUNCTION, "emit_text")
        self.assertFalse(getattr(cls, "OUTPUT_NODE", False))
        self.assertEqual(schema["hidden"]["prompt"], "PROMPT")
        self.assertEqual(schema["hidden"]["unique_id"], "UNIQUE_ID")

    def test_text_inputs_disable_automatic_prompt_expansion(self):
        required = self.nodes.PLaMoPromptTranslator.INPUT_TYPES()["required"]
        for key in ("source_text", "output_text", "translation_state"):
            with self.subTest(key=key):
                self.assertEqual(required[key][0], "STRING")
                self.assertIs(required[key][1].get("dynamicPrompts"), False)
                self.assertIs(required[key][1].get("socketless"), True)
        self.assertIs(required["translation_state"][1].get("hidden"), True)
        self.assertNotEqual(required["translation_state"][1].get("serialize"), False)

    def test_translation_languages_have_priority_and_source_has_auto(self):
        schema = self.nodes.PLaMoPromptTranslator.INPUT_TYPES()
        targets = schema["required"]["target_lang"][0]
        sources, options = schema["optional"]["source_lang"]
        self.assertEqual(len(set(targets)), 53)
        self.assertEqual(targets[:4], ["en", "zh-hans", "zh-hant", "ja"])
        self.assertEqual(sources, ["auto", *targets])
        self.assertEqual(options["default"], "auto")

    def test_explicit_source_language_is_part_of_the_accepted_result(self):
        state = self.state_string(source_lang="ja")
        self.assertEqual(self.emit(state_string=state, source_lang="ja"), (self.output,))
        with self.assertRaisesRegex(ValueError, "STALE_RESULT"):
            self.emit(state_string=state, source_lang="auto")

    def test_emit_preserves_all_output_whitespace(self):
        output = " \tA woman.\r\nTwo lines.  \n"
        self.assertEqual(self.emit(output=output), (output,))

    def test_manual_weights_and_dynamic_prompt_text_are_not_rewritten(self):
        output = "(red dress:1.2), {day|night}, <lora:example:0.8>\n"
        self.assertEqual(self.emit(output=output), (output,))

    def test_editing_only_the_result_needs_no_retranslation(self):
        self.assertEqual(self.emit(output="My corrected translation."), ("My corrected translation.",))

    def test_saved_workflow_values_work_after_json_roundtrip(self):
        inputs = {
            "source_text": self.source,
            "target_lang": self.target,
            "output_text": self.output,
            "translation_state": self.state_string(),
        }
        restored = json.loads(json.dumps(inputs, ensure_ascii=False))
        self.assertEqual(self.node.emit_text(**restored), (self.output,))

    def test_saved_output_does_not_open_a_network_connection(self):
        with mock.patch("socket.socket.connect", side_effect=AssertionError("Queue must be offline")):
            self.assertEqual(self.emit(), (self.output,))

    def test_model_provenance_does_not_require_a_loaded_model(self):
        self.assertEqual(self.emit(state_string=self.state_string(model_key="deleted-or-unloaded-model")), (self.output,))

    def test_original_text_change_makes_result_stale(self):
        with self.assertRaises(ValueError):
            self.node.emit_text(self.source + " 変更", self.target, self.output, self.state_string())

    def test_whitespace_change_in_source_is_also_a_change(self):
        with self.assertRaises(ValueError):
            self.node.emit_text(self.source + " ", self.target, self.output, self.state_string())

    def test_direction_change_makes_result_stale(self):
        with self.assertRaises(ValueError):
            self.node.emit_text(self.source, "ja", self.output, self.state_string())

    def test_english_to_japanese_saved_result(self):
        source, output = "A red coat.", "赤いコート。\n"
        state_string = self.state_string(source=source, target="ja")
        self.assertEqual(self.node.emit_text(source, "ja", output, state_string), (output,))

    def test_empty_or_whitespace_output_is_rejected(self):
        for output in ("", " \t\r\n", "\u3000"):
            with self.subTest(output=repr(output)), self.assertRaises(ValueError):
                self.emit(output=output)

    def test_exact_output_character_limit_is_accepted(self):
        output = "x" * self.state.MAX_OUTPUT_CHARS
        self.assertEqual(self.emit(output=output), (output,))

    def test_output_above_character_limit_is_rejected(self):
        with self.assertRaises(ValueError):
            self.emit(output="x" * (self.state.MAX_OUTPUT_CHARS + 1))

    def test_unicode_count_uses_codepoints_not_utf16_units(self):
        output = "\U0001f600" * self.state.MAX_OUTPUT_CHARS
        self.assertEqual(self.emit(output=output), (output,))

    def test_long_translation_state_can_be_restored_and_emitted(self):
        source = "静かな待合室。\n\n" * 1_000
        output = "A quiet waiting room.\n\n" * 1_000
        state = json.dumps(self.state.make_state(source, "en"), ensure_ascii=False)
        self.assertGreater(len(state.encode("utf-8")), 16 * 1024)
        self.assertEqual(self.node.emit_text(source, "en", output, state), (output,))

    def test_no_state_cannot_silently_reuse_old_text(self):
        with self.assertRaises(ValueError):
            self.emit(state_string="")

    def test_malformed_and_nonobject_state_is_rejected(self):
        for value in ("{", "null", "[]", "true", '"state"'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.emit(state_string=value)

    def test_unknown_or_boolean_state_version_is_rejected(self):
        for version in (0, 2, True, "1"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                self.emit(state_string=self.state_string(v=version))

    def test_incomplete_failed_or_pending_state_is_rejected(self):
        for status in ("partial", "error", "pending", "", None):
            state = json.loads(self.state_string())
            state["status"] = status
            with self.subTest(status=status), self.assertRaises(ValueError):
                self.emit(state_string=json.dumps(state))

    def test_missing_snapshot_is_rejected(self):
        state = json.loads(self.state_string())
        del state["source_snapshot"]
        with self.assertRaises(ValueError):
            self.emit(state_string=json.dumps(state))

    def test_state_snapshot_is_not_coerced_to_string(self):
        with self.assertRaises(ValueError):
            self.emit(state_string=self.state_string(source_snapshot=42))

    def test_oversized_state_is_rejected_before_use(self):
        state = self.state_string(extra="x" * self.state.MAX_STATE_BYTES)
        with self.assertRaises(ValueError):
            self.emit(state_string=state)

    def test_translated_empty_source_is_invalid(self):
        state = json.loads(self.state_string())
        state["source_snapshot"] = ""
        with self.assertRaises(ValueError):
            self.node.emit_text("", "en", "A manually typed sentence.", json.dumps(state))

    def test_manual_adoption_allows_empty_source(self):
        state = self.state_string(source="", status="manual")
        output = "A manually typed sentence.  "
        self.assertEqual(self.node.emit_text("", "en", output, state), (output,))

    def test_manual_adoption_still_requires_matching_direction(self):
        state = self.state_string(status="manual")
        with self.assertRaises(ValueError):
            self.node.emit_text(self.source, "ja", self.output, state)

    def test_unsupported_language_is_rejected(self):
        with self.assertRaises(ValueError):
            self.node.emit_text(self.source, "auto", self.output, self.state_string())

    def test_nonstring_inputs_are_not_silently_coerced(self):
        good = [self.source, self.target, self.output, self.state_string()]
        for index in range(4):
            args = list(good)
            args[index] = None
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.node.emit_text(*args)

    def test_validate_inputs_accepts_current_saved_result(self):
        self.assertIs(
            self.node.VALIDATE_INPUTS(self.source, self.target, self.output, self.state_string()),
            True,
        )

    def test_validate_inputs_returns_actionable_error_for_stale_result(self):
        result = self.node.VALIDATE_INPUTS(self.source + "変更", self.target, self.output, self.state_string())
        self.assertIsInstance(result, str)
        self.assertTrue(result.strip())

    def test_validate_inputs_returns_error_for_unconfirmed_result(self):
        result = self.node.VALIDATE_INPUTS(self.source, self.target, self.output, "")
        self.assertIsInstance(result, str)
        self.assertTrue(result.strip())

    def test_raw_prompt_links_are_rejected_even_if_resolved_text_matches(self):
        for key in ("source_text", "target_lang", "output_text", "translation_state"):
            raw_inputs = {
                "source_text": self.source,
                "target_lang": self.target,
                "output_text": self.output,
                "translation_state": self.state_string(),
            }
            raw_inputs[key] = ["upstream", 0]
            prompt = {"9": {"class_type": "PLaMoPromptTranslator", "inputs": raw_inputs}}
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.emit(prompt=prompt, unique_id="9")

    def test_other_nodes_links_do_not_block_this_constant_node(self):
        prompt = {
            "9": {"class_type": "PLaMoPromptTranslator", "inputs": {
                "source_text": self.source,
                "target_lang": self.target,
                "output_text": self.output,
                "translation_state": self.state_string(),
            }},
            "10": {"class_type": "CLIPTextEncode", "inputs": {"text": ["9", 0]}},
        }
        self.assertEqual(self.emit(prompt=prompt, unique_id="9"), (self.output,))

    def test_state_return_value_is_validated_object(self):
        state = self.state.validate_output(self.source, self.target, self.output, self.state_string())
        self.assertEqual(state["status"], "translated")
        self.assertEqual(state["source_snapshot"], self.source)
        self.assertEqual(state["target_lang"], self.target)


if __name__ == "__main__":
    unittest.main()
