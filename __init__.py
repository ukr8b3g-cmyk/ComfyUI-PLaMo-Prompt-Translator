"""PLaMo Prompt Translator for ComfyUI."""

from .nodes import PLaMoPromptTranslator, PLaMoPromptTranslatorLocal

__version__ = "1.2.0"
NODE_CLASS_MAPPINGS = {"PLaMoPromptTranslator": PLaMoPromptTranslator,
                       "PLaMoPromptTranslatorLocal": PLaMoPromptTranslatorLocal}
NODE_DISPLAY_NAME_MAPPINGS = {"PLaMoPromptTranslator": "PLaMo Prompt Translator",
                              "PLaMoPromptTranslatorLocal": "PLaMo Prompt Translator (Local)"}
WEB_DIRECTORY = "./web"

# Allow documentation, unit tests and offline prompt inspection without ComfyUI.
# In ComfyUI, PromptServer exists before custom nodes are imported.
try:
    from server import PromptServer
except ModuleNotFoundError as exc:
    if exc.name != "server":
        raise
else:
    if getattr(PromptServer, "instance", None) is not None:
        from .routes import register_routes
        register_routes(PromptServer.instance)

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
