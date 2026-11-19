"""One manual-review node. Graph execution never contacts LM Studio."""

from .state import LANGUAGES, validate_output

_MISSING = object()


class PLaMoPromptTranslator:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "source_text": ("STRING", {
                    "default": "", "multiline": True, "dynamicPrompts": False,
                    "socketless": True, "display_name": "Source",
                    "tooltip": "Plain text to translate. Use the Translate button before Queue.",
                }),
                "target_lang": (list(LANGUAGES), {
                    "default": "en", "socketless": True,
                    "display_name": "Translate to",
                    "tooltip": "Language of the translation.",
                }),
                "output_text": ("STRING", {
                    "default": "", "multiline": True, "dynamicPrompts": False,
                    "socketless": True, "display_name": "Translation",
                    "tooltip": "Editable text sent unchanged to the output. No translation runs at Queue time.",
                }),
                "translation_state": ("STRING", {
                    "default": "", "hidden": True, "socketless": True,
                    "dynamicPrompts": False,
                }),
            },
            "optional": {
                "source_lang": (["auto", *LANGUAGES], {
                    "default": "auto", "socketless": True,
                    "display_name": "Source language",
                    "tooltip": "Auto lets PLaMo detect the source language.",
                }),
            },
            "hidden": {"prompt": "PROMPT", "unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    FUNCTION = "emit_text"
    CATEGORY = "PLaMo/Text"
    DESCRIPTION = (
        "Translate with local LM Studio before Queue, review/edit the result, "
        "then connect text to a text encoder. Queue uses the saved text without network access."
    )

    @classmethod
    def VALIDATE_INPUTS(cls, source_text=_MISSING, target_lang=_MISSING,
                        output_text=_MISSING, translation_state=_MISSING, source_lang="auto"):
        # ComfyUI passes constant inputs here; linked inputs may not be available yet.
        values = (source_text, target_lang, output_text, translation_state)
        if any(value is _MISSING for value in values):
            return True
        try:
            validate_output(*values, source_lang)
        except ValueError as exc:
            return str(exc)
        return True

    def emit_text(self, source_text, target_lang, output_text, translation_state,
                  prompt=None, unique_id=None, source_lang="auto"):
        if prompt is not None:
            if not isinstance(prompt, dict) or unique_id is None:
                raise ValueError("PLaMo INVALID_INPUT: Cannot inspect the node's input links.")
            entry = prompt.get(str(unique_id), prompt.get(unique_id))
            if not isinstance(entry, dict) or not isinstance(entry.get("inputs"), dict):
                raise ValueError("PLaMo INVALID_INPUT: Cannot inspect the node's input links.")
            for name in ("source_text", "target_lang", "output_text", "translation_state", "source_lang"):
                if isinstance(entry["inputs"].get(name), (list, tuple)):
                    raise ValueError(f"PLaMo UNSUPPORTED_LINK: {name} must be entered on this node. Disconnect its input link.")
        validate_output(source_text, target_lang, output_text, translation_state, source_lang)
        return (output_text,)


class PLaMoPromptTranslatorLocal(PLaMoPromptTranslator):
    DESCRIPTION = (
        "Translate directly with the local PLaMo model. Translate and Live run in ComfyUI. "
        "Run sends the reviewed text to connected nodes without loading the translator."
    )
