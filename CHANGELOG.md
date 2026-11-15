# Changelog

## 1.0.0

- First GitHub release for ComfyUI.
- Source detection and explicit selection across 53 languages, including separate Simplified and Traditional Chinese options.
- Side-by-side, resizable source and translation editors with compact feedback and connection settings.
- One-click long-text translation with paragraph-aware splitting, automatic retries for cutoffs, and ordered reassembly.
- English and Japanese installation guides with LM Studio setup screenshots.

## 0.1.0

- One manual-review ComfyUI node with editable source and translation fields.
- Japanese ↔ English translation through local LM Studio using PFML3 and Completions.
- Model discovery, on-demand loading and selected-instance unloading.
- Workflow-persisted acceptance state and exact STRING output without network calls at Queue time.
- Stale-response and manual-edit protection, partial-output handling and explicit text acceptance.
- Japanese/English interface, setup instructions, example workflow, headless API helper and automated tests.

See `docs/VALIDATION.md` for the distinction between automated checks and real-host verification.
