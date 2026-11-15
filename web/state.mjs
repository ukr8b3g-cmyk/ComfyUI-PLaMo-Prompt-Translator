import languages from "./languages.json" with { type: "json" };

export const LANGUAGES = Object.freeze(languages);
export const FORMAT = "pfml3-completions-v1";
export const DEFAULT_CONFIG = Object.freeze({
  url: "http://127.0.0.1:1234",
  model_key: "",
  timeout_seconds: 60,
  max_tokens: 512,
});

const encoder = new TextEncoder();
const sourceLimit = [16_000, 64 * 1024];
const outputLimit = [64_000, 256 * 1024];
const stateByteLimit = 128 * 1024;
const fields = ["source_text", "target_lang", "output_text", "translation_state", "source_lang"];
const sourceLanguage = (values) => values.source_lang === undefined ? "auto" : values.source_lang;
const validSourceLanguage = (code) => code === "auto" || LANGUAGES.includes(code);

function checkText(value, chars, bytes, allowEmpty = false) {
  if (typeof value !== "string" || (value.isWellFormed && !value.isWellFormed())) {
    throw Object.assign(new Error("Invalid text."), { code: "INVALID_INPUT" });
  }
  if ([...value].length > chars || encoder.encode(value).length > bytes) {
    throw Object.assign(new Error("The text is too long."), { code: "INPUT_TOO_LONG" });
  }
  if (!allowEmpty && !value.trim()) {
    throw Object.assign(new Error("Enter text first."), { code: "INPUT_EMPTY" });
  }
}

export function inspectState(values) {
  try {
    checkText(values.source_text, ...sourceLimit, true);
    checkText(values.output_text, ...outputLimit);
    if (!LANGUAGES.includes(values.target_lang) || !validSourceLanguage(sourceLanguage(values))) return null;
    if (typeof values.translation_state !== "string" ||
        encoder.encode(values.translation_state).length > stateByteLimit) return null;
    const state = JSON.parse(values.translation_state);
    if (!state || Array.isArray(state) || state.v !== 1 ||
        !["translated", "manual"].includes(state.status) ||
        state.source_snapshot !== values.source_text ||
        sourceLanguage(state) !== sourceLanguage(values) ||
        state.target_lang !== values.target_lang ||
        (state.format !== undefined && state.format !== FORMAT) ||
        (state.model_key !== undefined &&
         (typeof state.model_key !== "string" || [...state.model_key].length > 200)) ||
        (state.status === "translated" && !values.source_text.trim())) return null;
    return state;
  } catch {
    return null;
  }
}

export function makeManualState(values, modelKey = "") {
  checkText(values.source_text, ...sourceLimit, true);
  checkText(values.output_text, ...outputLimit);
  const sourceLang = sourceLanguage(values);
  if (!LANGUAGES.includes(values.target_lang) || !validSourceLanguage(sourceLang)) {
    throw Object.assign(new Error("Choose a supported source and target language."), { code: "INVALID_INPUT" });
  }
  return {
    v: 1,
    status: "manual",
    source_snapshot: values.source_text,
    ...(sourceLang !== "auto" ? { source_lang: sourceLang } : {}),
    target_lang: values.target_lang,
    model_key: typeof modelKey === "string" && [...modelKey].length <= 200 ? modelKey : "",
    format: FORMAT,
  };
}

/** Keeps request acceptance separate from the lifetime of the HTTP operation. */
export class TranslationSession {
  constructor({ read, writeText, writeState, request, currentContext, notify = () => {} }) {
    Object.assign(this, { read, writeText, writeState, request, currentContext, notify });
    this.revision = 0;
    this.pending = null;
    this.disposed = false;
    this.observed = { ...read() };
    this.restore();
  }

  get busy() { return this.pending !== null; }
  get accepting() { return Boolean(this.pending?.accept); }

  emit() {
    if (!this.disposed) this.notify(this.status, this);
  }

  setStatus(kind, error = null, warnings = []) {
    this.status = { kind, error, warnings };
    this.emit();
  }

  saveState(value) {
    this.writeState(value);
    this.observed = { ...this.read() };
  }

  restore() {
    this.observed = { ...this.read() };
    const ready = inspectState(this.observed);
    this.setStatus(ready ? "ready" : this.observed.output_text ? "review" : "empty");
  }

  observe() {
    if (this.disposed) return;
    const values = this.read();
    if (fields.every((field) => values[field] === this.observed[field])) return;
    const changedSource = values.source_text !== this.observed.source_text ||
      values.target_lang !== this.observed.target_lang || sourceLanguage(values) !== sourceLanguage(this.observed);
    this.observed = { ...values };
    this.revision += 1;
    if (this.pending) this.pending.accept = false;
    if (changedSource) this.saveState("");
    this.restore();
  }

  settingsChanged() {
    if (!this.pending?.accept) return;
    this.pending.accept = false;
    this.revision += 1;
    this.restore();
  }

  cancelAcceptance() {
    if (!this.pending?.accept) return;
    this.pending.accept = false;
    this.revision += 1;
    this.saveState("");
    this.setStatus("cancelled");
  }

  acceptCurrent(modelKey = "") {
    this.observe();
    const state = makeManualState(this.read(), modelKey);
    if (this.pending) this.pending.accept = false;
    this.revision += 1;
    this.saveState(JSON.stringify(state));
    this.setStatus("ready");
    return state;
  }

  mayApply(pending) {
    this.observe();
    const context = this.currentContext();
    return !this.disposed && this.pending === pending && pending.accept &&
      this.revision === pending.revision && context.attached &&
      context.scope === pending.scope && context.configRevision === pending.configRevision;
  }

  async translate({ config, token = "", requestId }) {
    if (this.disposed || this.busy) return { ignored: true };
    this.observe();
    const values = this.read();
    const context = this.currentContext();
    try {
      checkText(values.source_text, ...sourceLimit);
      if (!LANGUAGES.includes(values.target_lang) || !validSourceLanguage(sourceLanguage(values))) {
        throw Object.assign(new Error("Choose a supported source and target language."), { code: "INVALID_INPUT" });
      }
      if (!config.model_key) {
        throw Object.assign(new Error("Choose a model in Connection settings."), { code: "MODEL_NOT_FOUND" });
      }
      if (!context.attached) return { ignored: true };
    } catch (error) {
      this.saveState("");
      this.setStatus("error", error);
      return { error };
    }
    this.saveState("");
    const pending = {
      id: requestId, accept: true, revision: this.revision,
      scope: context.scope, configRevision: context.configRevision,
    };
    this.pending = pending;
    this.setStatus("busy");
    try {
      const result = await this.request({
        request_id: requestId,
        source_text: values.source_text,
        ...(sourceLanguage(values) !== "auto" ? { source_lang: sourceLanguage(values) } : {}),
        target_lang: values.target_lang,
        config: { ...config }, token,
      });
      if (!this.mayApply(pending)) return { ignored: true };
      checkText(result?.text, ...outputLimit);
      if (result.request_id !== pending.id || !["translated", "partial"].includes(result.status)) {
        throw Object.assign(new Error("Invalid translation response."), { code: "INVALID_RESPONSE" });
      }
      const state = result.status === "translated" ? JSON.stringify(result.translation_state) : "";
      if (result.status === "translated" && (result.translation_state?.status !== "translated" || !inspectState({
        ...values, output_text: result.text, translation_state: state,
      }))) {
        throw Object.assign(new Error("Translation state did not match the request."), { code: "INVALID_RESPONSE" });
      }
      this.writeText(result.text);
      this.saveState(state);
      const warnings = Array.isArray(result.warnings)
        ? result.warnings.filter((warning) => typeof warning === "string").slice(0,8) : [];
      this.setStatus(result.status === "translated" ? "ready" : "partial", null, warnings);
      return { applied: true };
    } catch (error) {
      if (this.mayApply(pending)) this.setStatus("error", error);
      return { error };
    } finally {
      if (this.pending === pending) this.pending = null;
      if (this.status.kind === "busy" && !this.disposed) this.restore();
      else this.emit();
    }
  }

  dispose() {
    this.disposed = true;
    if (this.pending) this.pending.accept = false;
  }
}
