# Provenance and API references

The functionality is ported at the repository owner's request from:

- [Forge Neo PLaMo Prompt Translator](https://github.com/ukr8b3g-cmyk/Forge-Neo-PLaMo-Prompt-Translator)
- Reviewed source baseline: `bbf674e9889d1cc38e37149012f473f13341663b`
- [Original model/transport implementation](https://github.com/ukr8b3g-cmyk/Forge-Neo-PLaMo-Prompt-Translator/blob/bbf674e9889d1cc38e37149012f473f13341663b/plamo_backend.py)

This package implements a ComfyUI-specific interface and aiohttp transport. It does not import Forge Neo or Gradio, and does not bundle code from the other ComfyUI translation projects examined for UI ideas.

## Primary references

- [PFN PLaMo 3 Translate GGUF and PFML3](https://huggingface.co/pfnet/plamo-3-translate-0.8b-GGUF)
- [LM Studio Completions](https://lmstudio.ai/docs/developer/openai-compat/completions)
- [LM Studio model list](https://lmstudio.ai/docs/developer/rest/list)
- [LM Studio model load](https://lmstudio.ai/docs/developer/rest/load)
- [LM Studio model unload](https://lmstudio.ai/docs/developer/rest/unload)
- [ComfyUI custom routes](https://docs.comfy.org/development/comfyui-server/comms_routes)
- [ComfyUI V1 node schema](https://docs.comfy.org/custom-nodes/backend/server_overview)
- [Frontend widget migration](https://github.com/Comfy-Org/ComfyUI_frontend/blob/3633e2613c24a74d58f8183ceef3b470a7801878/docs/extensions/widgets-migration.md)

The LM Studio model search and Model Server screenshots in the README come from the Forge Neo project linked above. The four ComfyUI screenshots were supplied by the project owner.

Download PLaMo model weights separately under the [PLaMo Community License](https://huggingface.co/pfnet/plamo-3-translate-0.8b-GGUF#license). LM Studio is distributed separately by its publisher.
