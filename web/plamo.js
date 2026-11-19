import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
// ComfyUI serves this entry point without caching. Version the dependent module as well.
import { DEFAULT_CONFIG, LANGUAGES, TranslationSession, inspectState } from "./state.mjs?v=long-text";

import { LiveTranslation } from "./live.mjs?v=1";

const NODE_CLASS = "PLaMoPromptTranslator";
const LOCAL_CLASS = "PLaMoPromptTranslatorLocal";
const LOCAL_MODEL = "plamo-3-translate-0.8b";
const localSettingIds = {
  model_key: "PLaMo.Local.Model", max_tokens: "PLaMo.Local.MaxTokens", timeout_seconds: "PLaMo.Local.Timeout",
};
const INPUT_NAMES = ["source_text", "target_lang", "output_text", "translation_state", "source_lang"];
const settingIds = {
  url: "PLaMo.Translator.URL",
  model_key: "PLaMo.Translator.Model",
  timeout_seconds: "PLaMo.Translator.Timeout",
  max_tokens: "PLaMo.Translator.MaxTokens",
};
const controllers = new Set();
const nodeControllers = new WeakMap();
const revisions = { local: 0, studio: 0 };
let apiToken = "";
let activeDialog = null;

const strings = {
  localTitle: ["Local model settings", "ローカルモデル設定"],
  copyInstall: ["Copy install command", "インストールコマンドをコピー"],
  installCommand: ["Install command", "インストールコマンド"],
  commandCopied: ["✓ Command copied", "✓ コマンドをコピーしました"],
  manualCopy: ["Copy failed. Select and copy the command above.", "コピーできませんでした。上のコマンドを選択してコピーしてください。"],
  runPowerShell: ["Paste into PowerShell on the computer running ComfyUI and press Enter.", "ComfyUIが動いているPCのPowerShellに貼り付け、Enterを押してください。"],
  runWindows: ["Paste into Command Prompt or PowerShell on the computer running ComfyUI and press Enter.", "ComfyUIが動いているPCのコマンドプロンプトまたはPowerShellに貼り付け、Enterを押してください。"],
  runTerminal: ["Paste into a terminal on the computer running ComfyUI and press Enter.", "ComfyUIが動いているPCのターミナルに貼り付け、Enterを押してください。"],
  terminalUv: ["This command requires uv. In ComfyUI Desktop, use the built-in terminal.", "このコマンドにはuvが必要です。ComfyUI Desktopでは内蔵ターミナルを使ってください。"],
  afterInstall: ["When installation finishes, restart ComfyUI and click Translate.", "インストールが終わったらComfyUIを再起動し、「翻訳」を押してください。"],
  missingPackages: ["Waiting for installation", "インストール待ち"],
  updateTransformers: ["Update Transformers", "Transformersを更新"],
  packagesReady: ["Required packages installed.", "必要なパッケージはインストール済みです。"],
  recheckSetup: ["Check setup again", "状態を再確認"],
  download: ["Download model (1.78 GB)", "モデルをダウンロード（1.78 GB）"],
  downloading: ["Downloading the PFN model…", "PFN公式モデルをダウンロード中…"],
  preparing: ["Checking local model…", "ローカルモデルを確認中…"],
  loading: ["Loading model into memory…", "モデルをメモリに読み込み中…"],
  LOCAL_DOWNLOAD_FAILED: ["Download failed. Check the internet connection and free disk space, then press Translate again.", "ダウンロードに失敗しました。インターネット接続と空き容量を確認し、もう一度「翻訳」を押してください。"],
  localSelect: ["Choose a model folder", "モデルフォルダーを選択"],
  localUnload: ["Unload model", "モデルを解放"],
  localCancelled: ["Result discarded. Local inference may still be running.", "結果の受け取りを中止しました。ローカル推論は続く場合があります。"],
  localAvailable: ["Model downloaded · loads on Translate.", "モデルはダウンロード済み・未ロードです。翻訳時に読み込みます。"],
  localNew: ["Model not downloaded. Translate will download and load it.", "モデルは未ダウンロードです。翻訳時にダウンロードして読み込みます。"],
  localLoaded: ["Model loaded", "モデルはロード済みです"],
  localNotFound: ["Model folder not found. Choose an available model.", "モデルフォルダーが見つかりません。利用可能なモデルを選んでください。"],
  LOCAL_DEPS: ["Open ⚙ for the required package install or update.", "歯車で必要なパッケージと導入・更新コマンドを確認してください。"],
  LOCAL_MISSING: ["Open ⚙ to choose or download a model.", "歯車でモデルを選択またはダウンロードしてください。"],
  LOCAL_CODE: ["Model code does not match the supported PFN release.", "モデル定義が対応するPFN公式版と一致しません。"],
  LOCAL_INCOMPLETE: ["The existing model folder is incomplete. Check its files.", "既存のモデルフォルダーに不足ファイルがあります。"],
  LOCAL_BUSY: ["Another local model operation is running.", "ローカルモデルを処理中です。"],
  LOCAL_TIMEOUT: ["Local translation timed out. Increase the timeout in ⚙.", "時間切れ。歯車でタイムアウトを延ばしてください。"],
  LOCAL_ERROR: ["Local model operation failed. See the ComfyUI console.", "ローカルモデルの処理に失敗。ComfyUIのコンソールを確認してください。"],
  live: ["Live", "自動翻訳"],
  liveHint: ["Automatically translates after you stop typing. Click again to turn off.", "入力が止まると自動翻訳します。もう一度押すとオフになります。"],
  liveWaiting: ["Waiting to translate…", "翻訳待ち…"],
  title: ["PLaMo connection settings", "PLaMo 接続設定"],
  source: ["Source", "原文"], target: ["Translate to", "翻訳先"],
  sourceLanguage: ["Source language", "原文の言語"], auto: ["Auto", "自動"],
  simplifiedChinese: ["Chinese (Simplified)", "中国語（簡体字）"],
  traditionalChinese: ["Chinese (Traditional)", "中国語（繁体字）"],
  output: ["Translation", "訳文"], translate: ["Translate", "翻訳"],
  copy: ["Copy translation", "訳文をコピー"], copied: ["Copied.", "コピーしました。"],
  accept: ["Use current text", "現在の訳文を使う"],
  settings: ["Connection settings", "接続設定"],
  cancel: ["Stop receiving this result", "結果の受け取りを中止"],
  stop: ["Stop", "中止"],
  empty: ["Enter source text, then Translate.", "原文を入力して「翻訳」を押してください。"],
  ready: ["Ready. Run sends this text to connected nodes.", "出力準備完了。Runで接続先へ渡します。"],
  readyShort: ["Ready.", "出力準備完了"],
  review: ["Review the current text. Translate again or explicitly use it.", "要確認。再翻訳するか「現在の訳文を使う」を選択してください。"],
  busy: ["Translating…", "翻訳中…"],
  pending: ["The previous request is still finishing on the server.", "前のリクエストはサーバー側で処理終了を待っています。"],
  cancelled: ["Result reception stopped. LM Studio may still be running.", "結果の受け取りを中止しました。LM Studioの処理は続く場合があります。"],
  partial: [
    "Partial translation. In Connection settings (gear), increase Maximum output tokens, save, and translate again. You can also edit the translation and choose Use current text.",
    "訳文が途中で終了しました。歯車の「接続設定」で「最大出力トークン数」を増やし、保存して再翻訳してください。訳文を編集して「現在の訳文を使う」こともできます。",
  ],
  partialShort: ["Cut off. ⚙ →\nMax output tokens", "訳文が途中。歯車 →\n最大出力トークン数"],
  error: ["Translation failed. Your existing text was kept.", "翻訳に失敗しました。既存の本文は保持しています。"],
  url: ["LM Studio URL", "LM Studio 接続先"],
  model: ["Model", "モデル"], timeout: ["Total timeout (seconds)", "全体タイムアウト（秒）"],
  tokens: ["Maximum output tokens", "最大出力トークン数"],
  token: ["API token (optional; this tab only)", "API Token（任意・このタブ内のみ）"],
  refresh: ["Refresh models / Check connection", "モデル一覧更新・接続確認"],
  unload: ["Unload selected model", "選択モデルを解放"],
  save: ["Save settings", "設定を保存"], close: ["Close", "閉じる"],
  select: ["Refresh the list and select a PLaMo model", "一覧を更新してPLaMoモデルを選択"],
  savedModel: ["saved selection", "保存済みの選択"],
  not_selected: ["Connected. Select a model.", "接続済み。モデルを選択してください。"],
  missing: ["Connected. The saved model was not found; the selection was kept.", "接続済み。保存したモデルは見つかりません。選択値は保持しています。"],
  unloaded: ["Connected. The model will load when you translate.", "接続済み・未ロード。翻訳時にロードします。"],
  loaded: ["Connected. The selected model is loaded.", "接続済み・ロード済み。"],
  ambiguous: ["Multiple instances of this model are loaded. Keep one instance in LM Studio.", "同じモデルのinstanceが複数あります。LM Studioで一つに整理してください。"],
  freed: ["Selected model unloaded. The next translation will load it again.", "選択モデルを解放しました。次の翻訳時に再ロードします。"],
  already_unloaded: ["The selected model is already unloaded.", "選択モデルは既に解放されています。"],
  checking: ["Checking…", "確認中…"], saved: ["Settings saved.", "設定を保存しました。"],
  staleSettings: ["Settings changed while waiting. Refresh again if needed.", "待機中に設定が変更されました。必要なら再度更新してください。"],
  settingsNote: [
    "Connection settings apply to all PLaMo nodes. localhost means the computer running ComfyUI. The API token is never saved in a workflow or persistent settings.",
    "接続設定は全PLaMoノード共通です。localhostはComfyUIが動くPCです。API Tokenはworkflowや永続設定に保存しません。",
  ],
  modelsHint: ["Model list updates do not load a model or change your saved selection.", "一覧更新ではモデルのロードや保存済み選択の変更は行いません。"],
  INPUT_EMPTY: ["Enter nonempty text first.", "空でない本文を入力してください。"],
  INPUT_TOO_LONG: ["The text exceeds the limit. Shorten it and try again.", "文字数の上限を超えています。本文を短くしてください。"],
  OUTPUT_TOO_LONG: ["The combined translation is too long. Shorten the source and retry.", "結合した訳文が長すぎます。原文を短くして再翻訳してください。"],
  MODEL_NOT_FOUND: ["Select the intended model in Connection settings.", "接続設定でモデル一覧を更新し、対象モデルを選択してください。"],
  UNSUPPORTED_LINK: ["This version accepts text entered on this node. Disconnect input links first.", "初期版はノード内への直接入力に対応しています。入力の配線を外してください。"],
  BUSY: ["LM Studio is busy with another PLaMo operation. Wait for it to finish.", "別のPLaMo処理が進行中です。終了後に操作してください。"],
  LMSTUDIO_UNAVAILABLE: ["Check that the LM Studio server is running and the URL is correct.", "LM Studioのサーバー起動と接続先を確認してください。"],
  AUTH_FAILED: ["Check the LM Studio API token.", "LM StudioのAPI Tokenを確認してください。"],
  TIMEOUT: ["The request timed out. Check LM Studio or increase the timeout.", "タイムアウトしました。LM Studioの状態または期限設定を確認してください。"],
  UNSUPPORTED_SYNTAX: ["Translate only natural-language text; add prompt control syntax after translation.", "自然文部分だけを翻訳し、重みなどの制御構文は訳文に追加してください。"],
  copy_error: ["Copy failed. Select and copy the translation manually.", "コピーできませんでした。訳文を選択して手動でコピーしてください。"],
};

function languageIndex() {
  try {
    return /^ja(?:[-_]|$)/i.test(getSetting("Comfy.Locale") ?? "") ? 1 : 0;
  } catch {
    return 0; // The ComfyUI settings service may still be starting.
  }
}

function t(key) { return strings[key]?.[languageIndex()] ?? key; }

function getSetting(id) {
  const modern = app.extensionManager?.setting;
  return modern ? modern.get(id) : app.ui.settings.getSettingValue(id);
}

async function setSetting(id, value) {
  const modern = app.extensionManager?.setting;
  if (modern) return await modern.set(id, value);
  if (app.ui.settings.setSettingValueAsync) return await app.ui.settings.setSettingValueAsync(id, value);
  return app.ui.settings.setSettingValue(id, value);
}

function readConfig() {
  return Object.fromEntries(Object.entries(settingIds).map(([key, id]) => {
    const value = getSetting(id) ?? DEFAULT_CONFIG[key];
    return [key, ["timeout_seconds", "max_tokens"].includes(key) ? Number(value) : value];
  }));
}

function settingsChanged(local = false) {
  revisions[local ? "local" : "studio"]++;
  for (const controller of controllers) {
    if (controller.local === local) { controller.cancelLive(); controller.session.settingsChanged(); }
  }
}

function errorMessage(error) {
  const code = typeof error?.code === "string" ? error.code : "";
  const message = typeof error?.message === "string" ? error.message.slice(0, 900) : "";
  return strings[code] ? t(code) : message || t("error");
}

async function readTranslation(response, onPhase) {
  let buffer = "";
  let result;
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      const lines = (buffer + chunk.value).split("\n");
      buffer = lines.pop();
      for (const line of lines) {
        if (!line) continue;
        const message = JSON.parse(line);
        if (message.phase) onPhase(message.phase);
        else if (message.result) result = message.result;
      }
    }
  } finally { reader.releaseLock(); }
  if (!result || buffer.trim()) throw new Error("Incomplete translation response.");
  return result;
}

async function requestJson(path, body, onPhase) {
  const abort = new AbortController();
  const seconds = Number(body.config?.timeout_seconds) || 60;
  const timeout = (Math.min(600, Math.max(1, seconds)) + 8) * 1000;
  const downloadTimeout = 1800000;
  let timer = setTimeout(() => abort.abort(), path === "local/download" ? downloadTimeout : timeout);
  try {
    const response = await api.fetchApi(`/plamo_translator/v1/${path}`, {
      method: "POST", headers: { "Content-Type": "application/json", ...(onPhase ? {Accept: "application/x-ndjson"} : {}) },
      body: JSON.stringify(body), signal: abort.signal,
    });
    let result;
    try {
      if (onPhase && response.headers.get("Content-Type")?.startsWith("application/x-ndjson")) {
        result = await readTranslation(response, phase => {
          clearTimeout(timer);
          timer = setTimeout(() => abort.abort(), phase === "downloading" ? downloadTimeout : timeout);
          onPhase(phase);
        });
      } else result = await response.json();
    } catch (error) {
      if (abort.signal.aborted) throw error;
      throw Object.assign(new Error(`Invalid server response (HTTP ${response.status}).`), { code: "INVALID_RESPONSE" });
    }
    if (!response.ok || result?.code) {
      throw Object.assign(new Error(typeof result?.message === "string" ? result.message : `HTTP ${response.status}`), {
        code: typeof result?.code === "string" ? result.code : "HTTP_ERROR",
      });
    }
    return result;
  } catch (error) {
    if (abort.signal.aborted) {
      const code = path.startsWith("local/") ? "LOCAL_TIMEOUT" : "TIMEOUT";
      throw Object.assign(new Error(t(code)), {code});
    }
    throw error;
  } finally { clearTimeout(timer); }
}

function element(tag, text, parent) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (parent) parent.append(node);
  return node;
}

async function copyText(text, parent = document.body) {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text);
  const temporary = element("textarea", undefined, parent);
  temporary.value = text;
  Object.assign(temporary.style, { position: "fixed", left: "-9999px" });
  try {
    temporary.select();
    if (!document.execCommand("copy")) throw new Error("copy_error");
  } finally { temporary.remove(); }
}

function localConfig() {
  return { model_key: getSetting(localSettingIds.model_key) || LOCAL_MODEL,
    max_tokens: Number(getSetting(localSettingIds.max_tokens)) || 512,
    timeout_seconds: Number(getSetting(localSettingIds.timeout_seconds)) || 180 };
}

function showSettings(local = false) {
  if (activeDialog) { activeDialog.focus(); return; }
  const dialog = element("dialog");
  activeDialog = dialog;
  dialog.setAttribute("aria-label", t(local ? "localTitle" : "title"));
  Object.assign(dialog.style, {
    width: "min(540px, calc(100vw - 48px))", maxHeight: "85vh", overflowY: "auto",
    padding: "22px", border: "1px solid var(--border-color, #666)", borderRadius: "10px",
    color: "var(--input-text, #eee)", background: "var(--comfy-menu-bg, #252525)",
    font: "14px system-ui, sans-serif", boxSizing: "border-box",
  });
  const heading = element("h2", t(local ? "localTitle" : "title"), dialog);
  heading.style.margin = "0 0 12px";
  const note = element("p", t("settingsNote"), dialog);
  note.style.lineHeight = "1.55";
  const form = element("form", undefined, dialog);
  const controls = {};
  let draftRevision = 0;
  let open = true;
  let operation = false;
  let modelRows = [];
  const config = local ? localConfig() : readConfig();

  function field(key, label, type = "text") {
    const wrapper = element("label", label, form);
    Object.assign(wrapper.style, { display: "grid", gap: "6px", marginBottom: "12px" });
    const tag = ["select", "textarea"].includes(type) ? type : "input";
    const control = element(tag, undefined, wrapper);
    if (tag === "input") control.type = type;
    Object.assign(control.style, {
      width: "100%", boxSizing: "border-box", padding: "8px", borderRadius: "4px",
      color: "var(--input-text, #eee)", background: "var(--comfy-input-bg, #171717)",
      border: "1px solid var(--border-color, #666)", font: "inherit",
    });
    controls[key] = control;
    control.addEventListener("input", () => { draftRevision += 1; });
    return control;
  }
  if (!local) field("url", t("url")).value = config.url;
  const modelSelect = field("model_key", t("model"), "select");
  const modelHint = element("p", t("modelsHint"), form);
  modelHint.style.fontSize = "12px";
  const timeout = field("timeout_seconds", t("timeout"), "number");
  timeout.min = "1"; timeout.max = "600"; timeout.step = "1"; timeout.required = true;
  timeout.value = String(config.timeout_seconds);
  const tokenSelect = field("max_tokens", t("tokens"), "select");
  for (const count of [256, 512, 1024, 2048]) {
    const option = element("option", String(count), tokenSelect); option.value = String(count);
  }
  tokenSelect.value = String(config.max_tokens);
  const tokenInput = local ? null : field("token", t("token"), "password");
  if (tokenInput) { tokenInput.autocomplete = "off"; tokenInput.value = apiToken; }
  const status = element("p", "", form);
  status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
  Object.assign(status.style, { whiteSpace: "pre-wrap", overflowWrap: "anywhere", lineHeight: "1.5" });
  const buttons = element("div", undefined, form);
  Object.assign(buttons.style, { display: "flex", flexWrap: "wrap", gap: "8px" });

  function button(label, action, type = "button") {
    const btn = element("button", label, buttons); btn.type = type;
    Object.assign(btn.style, { padding: "8px 12px", font: "inherit", cursor: "pointer" });
    if (action) btn.addEventListener("click", action);
    return btn;
  }
  function draftConfig() {
    return {
      ...(local ? {} : {url: controls.url.value}), model_key: modelSelect.value,
      timeout_seconds: Number(timeout.value), max_tokens: Number(tokenSelect.value),
    };
  }
  function fillModels(selection) {
    modelSelect.replaceChildren();
    const empty = element("option", t(local ? "localSelect" : "select"), modelSelect); empty.value = "";
    if (selection && !modelRows.some((row) => row.key === selection)) {
      const option = element("option", `${selection} (${t("savedModel")})`, modelSelect);
      option.value = selection;
    }
    for (const row of modelRows) {
      const loaded = row.loaded_instance_count > 0 ? ` · ${t(local ? "localLoaded" : "loaded")}` : "";
      const option = element("option", `${row.display_name || row.key}${loaded}`, modelSelect);
      option.value = row.key;
    }
    modelSelect.value = selection;
  }
  fillModels(config.model_key);

  async function operate(kind) {
    if (operation || !form.reportValidity()) return;
    const draft = draftConfig();
    if (kind === "unload" && !draft.model_key) { status.textContent = t("MODEL_NOT_FOUND"); return; }
    operation = true;
    refreshButton.disabled = true; unloadButton.disabled = true;
    status.textContent = t("checking");
    const revision = draftRevision;
    try {
      const result = await requestJson(kind, { config: draft, token: tokenInput.value });
      if (!open) return;
      if (draftRevision !== revision) { status.textContent = t("staleSettings"); return; }
      if (kind === "models") {
        if (!Array.isArray(result.models)) throw new Error("Invalid model list.");
        modelRows = result.models.filter((row) => row && typeof row.key === "string");
        fillModels(draft.model_key);
        status.textContent = t(result.selected_model_status || "not_selected");
      } else {
        status.textContent = t(result.status === "already_unloaded" ? "already_unloaded" : "freed");
      }
    } catch (error) {
      if (open && draftRevision === revision) status.textContent = errorMessage(error);
    } finally {
      operation = false;
      refreshButton.disabled = false; unloadButton.disabled = false;
    }
  }
  const refreshButton = button(t(local ? "recheckSetup" : "refresh"), () => void (local ? localAction("setup") : operate("models")));
  const unloadButton = button(t("unload"), () => void (local ? localAction("unload") : operate("unload")));
  const downloadButton = local ? button(t("download"), () => void localAction("download")) : null;
  let commandField, copyCommand, installHint;
  let installSteps = "";
  function showLocalModel() {
    const key = modelSelect.value;
    const row = modelRows.find(item => item.key === key);
    if (row?.loaded_instance_count) modelHint.textContent = t("localLoaded");
    else if (row) modelHint.textContent = t("localAvailable");
    else if (key === LOCAL_MODEL) modelHint.textContent = t("localNew");
    else modelHint.textContent = t(key ? "localNotFound" : "localSelect");
  }
  if (local) {
    note.textContent = "";
    note.style.overflowWrap = "anywhere";
    modelHint.textContent = "";
    modelSelect.addEventListener("change", showLocalModel);
    commandField = field("command", t("installCommand"), "textarea");
    commandField.readOnly = true;
    commandField.rows = 2;
    form.insertBefore(commandField.parentElement, buttons);
    commandField.parentElement.hidden = true;
    commandField.parentElement.style.display = "none";
    copyCommand = button(t("copyInstall"), async () => {
      try {
        await copyText(commandField.value, dialog);
        copyCommand.textContent = t("commandCopied");
        installHint.textContent = installSteps;
      } catch {
        copyCommand.textContent = t("copyInstall");
        commandField.focus(); commandField.select();
        installHint.textContent = t("manualCopy") + " " + installSteps;
      }
    });
    form.insertBefore(copyCommand, buttons);
    copyCommand.setAttribute("aria-live", "polite");
    installHint = element("p", "", form);
    installHint.setAttribute("aria-live", "polite");
    form.insertBefore(installHint, buttons);
    installHint.hidden = true;
    copyCommand.hidden = true;
    downloadButton.hidden = true;
    unloadButton.disabled = true;
  }
  async function localAction(action) {
    if (operation) return;
    operation = true;
    refreshButton.disabled = true;
    downloadButton.disabled = true;
    unloadButton.disabled = true;
    status.textContent = t(action === "download" ? "downloading" : "checking");
    try {
      const data = await requestJson("local/" + action, {});
      if (!open) return;
      note.textContent = data.directory;
      modelRows = data.models.map(key => ({key, loaded_instance_count: Number(data.loaded === key)}));
      fillModels(modelSelect.value || config.model_key);
      const update = data.transformers_update;
      const needsInstall = Boolean(data.install_command);
      commandField.parentElement.hidden = !needsInstall;
      commandField.parentElement.style.display = needsInstall ? "grid" : "none";
      let command = data.install_command;
      let shell = data.install_shell || (command.startsWith("& ") ? "powershell" : "sh");
      // Already-running servers still send the earlier PowerShell-only pip command.
      if (shell === "powershell" && /^& '[\w.:/\\-]+' (?:-s )?-m pip install [\w -]+$/.test(command)) {
        command = command.replace(/^& '([^']+)' /, "$1 ");
        shell = "windows";
      }
      commandField.value = command;
      commandField.rows = Math.max(2, command.split("\n").length);
      const terminalHint = { windows: "runWindows", powershell: "runPowerShell" }[shell] || "runTerminal";
      installSteps = [data.needs_terminal_uv && t("terminalUv"), t(terminalHint), t("afterInstall")].filter(Boolean).join(" ");
      copyCommand.textContent = t("copyInstall");
      copyCommand.hidden = !needsInstall;
      installHint.hidden = !needsInstall;
      installHint.textContent = needsInstall ? installSteps : "";
      downloadButton.hidden = data.models.includes(LOCAL_MODEL);
      unloadButton.disabled = !data.loaded;
      unloadButton.textContent = data.loaded ? t("localUnload") + " · " + data.loaded : t("localUnload");
      showLocalModel();
      const issues = [];
      if (update) issues.push(t("updateTransformers") + " · " + update.installed + " → ≥" + update.minimum);
      if (data.missing.length) issues.push(t("missingPackages") + " · " + data.missing.join(", "));
      status.textContent = issues.join("\n") || t("packagesReady");
    } catch (error) { status.textContent = errorMessage(error); }
    finally { refreshButton.disabled = false; downloadButton.disabled = false; operation = false; }
  }
  const saveButton = button(t("save"), null, "submit");
  button(t("close"), () => dialog.close());
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    saveButton.disabled = true;
    try {
      const draft = draftConfig();
      for (const [key, id] of Object.entries(local ? localSettingIds : settingIds)) await setSetting(id, draft[key]);
      if (tokenInput && apiToken !== tokenInput.value) { apiToken = tokenInput.value; settingsChanged(); }
      status.textContent = t("saved");
    } catch (error) { status.textContent = errorMessage(error); }
    finally { saveButton.disabled = false; }
  });
  dialog.addEventListener("close", () => {
    open = false; if (tokenInput) tokenInput.value = "";
    if (activeDialog === dialog) activeDialog = null;
    dialog.remove();
  }, { once: true });
  document.body.append(dialog);
  dialog.showModal();
  if (local) void localAction("setup");
}

function installNode(node) {
  const nodeClass = node.comfyClass ?? node.constructor?.comfyClass;
  if (![NODE_CLASS, LOCAL_CLASS].includes(nodeClass) || nodeControllers.has(node)) return;
  const local = nodeClass === LOCAL_CLASS;
  let localPhase = "preparing";
  const configForNode = local ? localConfig : readConfig;
  const widgets = Object.fromEntries(INPUT_NAMES.map((name) => [name, node.widgets?.find((widget) => widget.name === name)]));
  if (Object.values(widgets).some((widget) => !widget)) return;
  let internalWrite = 0;
  let restoring = false;
  let copyTimer;
  let live;
  let composing = false;
  let translationConfig = JSON.stringify(configForNode());
  node.properties ??= {};
  const liveEnabled = () => node.properties.plamo_live === true;
  const cleanups = [];

  function write(name, value) {
    internalWrite += 1;
    try {
      const widget = widgets[name];
      if (typeof widget.setValue === "function") widget.setValue(value, { node, canvas: app.canvas });
      else {
        const previous = widget.value;
        widget.value = value;
        node.onWidgetChanged?.(name, value, previous, widget);
        node.graph?.incrementVersion?.();
      }
      node.setDirtyCanvas?.(true, true);
    } finally { internalWrite -= 1; }
  }
  function read() { return Object.fromEntries(INPUT_NAMES.map((name) => [name, widgets[name].value])); }
  function hasLinks() {
    return node.inputs?.some((input, index) => INPUT_NAMES.includes(input.name) &&
      (typeof node.isInputConnected === "function" ? node.isInputConnected(index) : input.link != null));
  }
  function guardLinks() {
    if (!hasLinks()) return false;
    session.setStatus("error", { code: "UNSUPPORTED_LINK" });
    return true;
  }
  const panel = element("div");
  panel.className = "plamo-translator-panel";
  Object.assign(panel.style, {
    display: "grid", gridTemplateRows: "auto minmax(0, 1fr) auto", rowGap: "5px", height: "100%",
    boxSizing: "border-box", padding: "0 2px 2px", color: "var(--input-text, #ddd)", font: "12px/14px system-ui, sans-serif",
  });
  const columns = { display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: "6px" };
  const toolbar = element("div", undefined, panel);
  Object.assign(toolbar.style, columns, { alignItems: "end" });
  const targetControls = element("div");
  Object.assign(targetControls.style, {
    display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto auto", alignItems: "end", gap: columns.gap,
  });
  const editors = element("div", undefined, panel);
  Object.assign(editors.style, columns, { minHeight: "0" });
  const actions = element("div", undefined, panel);
  Object.assign(actions.style, { display: "flex", alignItems: "center", gap: "4px" });
  const statusElement = element("div", undefined, actions);
  statusElement.setAttribute("role", "status");
  statusElement.setAttribute("aria-live", "polite");
  Object.assign(statusElement.style, {
    flex: "1", minWidth: "0", maxHeight: "28px",
    whiteSpace: "pre-wrap", overflowWrap: "anywhere", overflow: "auto", scrollbarWidth: "none",
  });

  function showStatus(message, summary = message) {
    statusElement.textContent = summary;
    statusElement.title = message;
  }

  const controls = {};
  function addControl(name, caption, tag, parent) {
    const label = element("label", undefined, parent);
    Object.assign(label.style, {
      display: "flex", flexDirection: "column", gap: "2px", minWidth: "0", minHeight: "0",
    });
    element("span", t(caption), label);
    const control = element(tag, undefined, label);
    control.setAttribute("aria-label", t(caption));
    control.dataset.plamoField = name;
    Object.assign(control.style, {
      boxSizing: "border-box", width: "100%", minHeight: "0", flex: "1", padding: "4px",
      border: "1px solid var(--border-color, #666)", borderRadius: "4px",
      background: "var(--comfy-input-bg, #171717)", color: "var(--input-text, #eee)", font: "inherit",
    });
    if (tag === "select") {
      control.style.flex = "none";
      control.style.height = "24px";
    }
    control.addEventListener(tag === "select" ? "change" : "input", () => {
      write(name, control.value);
      observe();
      if (name === "output_text") live?.cancel();
      else if (liveEnabled() && !composing) live?.schedule();
    });
    if (tag === "textarea" && name === "source_text") {
      control.addEventListener("compositionstart", () => { composing = true; live?.cancel(); });
      control.addEventListener("compositionend", () => { composing = false; if (liveEnabled()) live?.schedule(); });
    }
    controls[name] = control;
    return control;
  }
  const languageNames = new Intl.DisplayNames([languageIndex() ? "ja" : "en"], { type: "language" });
  const specialLabels = { auto: "auto", "zh-hans": "simplifiedChinese", "zh-hant": "traditionalChinese" };
  const languageLabel = (code) => specialLabels[code] ? t(specialLabels[code]) : languageNames.of(code);
  const [priority, remaining] = [LANGUAGES.slice(0, 4), LANGUAGES.slice(4)];
  remaining.sort((left, right) => languageLabel(left).localeCompare(languageLabel(right), languageIndex() ? "ja" : "en"));
  for (const [name, caption, codes, parent] of [
    ["source_lang", "sourceLanguage", ["auto", ...priority, ...remaining], toolbar],
    ["target_lang", "target", [...priority, ...remaining], targetControls],
  ]) {
    const select = addControl(name, caption, "select", parent);
    for (const code of codes) {
      const option = element("option", languageLabel(code), select);
      option.value = code;
    }
  }
  toolbar.append(targetControls);
  for (const [name, caption] of [["source_text", "source"], ["output_text", "output"]]) {
    const editor = addControl(name, caption, "textarea", editors);
    editor.spellcheck = false;
    editor.style.resize = "none";
  }

  function addButton(name, callback, parent = actions) {
    const button = element("button", name === "settings" ? "⚙" : t(name), parent);
    button.type = "button";
    button.dataset.plamoAction = name;
    button.setAttribute("aria-label", t(name));
    button.title = t(name);
    Object.assign(button.style, {
      boxSizing: "border-box", flex: "none", height: "24px", padding: "0 6px",
      border: "1px solid var(--border-color, #666)", whiteSpace: "nowrap",
      borderRadius: "4px", color: "var(--input-text, #eee)", background: "var(--comfy-input-bg, #171717)",
      font: "inherit", cursor: "pointer",
    });
    button.addEventListener("click", callback);
    return button;
  }
  const translateButton = addButton("translate", () => {
    if (guardLinks()) return;
    if (!configForNode().model_key) { showSettings(local); return; }
    live.schedule(true);
  }, targetControls);
  const liveButton = addButton("live", () => {
    node.properties.plamo_live = !liveEnabled();
    live.cancel();
    session.cancelAcceptance();
    session.restore();
    updateLiveButton();
    node.graph?.incrementVersion?.();
    if (liveEnabled() && String(read().source_text).trim() &&
        !(inspectState(read())?.status === "translated" && translationConfig === JSON.stringify(configForNode()))) live.schedule(true);
  }, targetControls);
  function updateLiveButton() {
    liveButton.textContent = `${t("live")} ${liveEnabled() ? "ON" : "OFF"}`;
    liveButton.setAttribute("aria-pressed", String(liveEnabled()));
    liveButton.title = t("liveHint");
    liveButton.style.background = liveEnabled() ? "#b83232" : "var(--comfy-input-bg, #171717)";
    liveButton.style.color = liveEnabled() ? "#fff" : "var(--input-text, #eee)";
  }
  updateLiveButton();
  translateButton.style.background = "#345781";
  translateButton.style.color = "#fff";
  const cancelButton = addButton("cancel", () => { live.cancel(); session.cancelAcceptance(); });
  cancelButton.textContent = t("stop");
  addButton("copy", async () => {
    try {
      await copyText(widgets.output_text.value);
      showStatus(t("copied"));
      clearTimeout(copyTimer); copyTimer = setTimeout(() => render(session.status, session), 1600);
    } catch { showStatus(t("copy_error")); }
  });
  const acceptButton = addButton("accept", () => {
    if (guardLinks()) return;
    live.cancel();
    try { session.acceptCurrent(configForNode().model_key); }
    catch (error) { session.setStatus("error", error); }
  });
  const gear = addButton("settings", () => showSettings(local));
  if (local) { gear.title = t("localTitle"); gear.setAttribute("aria-label", gear.title); }
  const panelWidget = node.addDOMWidget("plamo_panel", "plamo_panel", panel, {
    serialize: false, getValue: () => "", setValue: () => {},
    margin: 4, getMinHeight: () => 146,
  });
  const computePanelSize = panelWidget.computeLayoutSize?.bind(panelWidget);
  if (computePanelSize) panelWidget.computeLayoutSize = () => ({ ...computePanelSize(), minWidth: 244 });
  panelWidget.serialize = false; panelWidget.options.serialize = false;

  function render(status, currentSession) {
    clearTimeout(copyTimer);
    translateButton.disabled = currentSession.busy;
    cancelButton.hidden = !currentSession.accepting;
    const values = read();
    for (const [name, control] of Object.entries(controls)) {
      const value = values[name] ?? (name === "source_lang" ? "auto" : "");
      if (control.value !== value) control.value = value;
    }
    acceptButton.hidden = !String(values.output_text ?? "").trim() || Boolean(inspectState(values));
    const localLabels = { busy: localPhase, cancelled: "localCancelled", partial: "partialShort" };
    const message = status.kind === "error" ? errorMessage(status.error) :
      t((local && localLabels[status.kind]) || status.kind);
    const waiting = currentSession.busy && !currentSession.accepting ? `\n${t("pending")}` : "";
    const details = [message + waiting, ...status.warnings].filter(Boolean).join("\n");
    let summary = details;
    if (status.kind === "partial") summary = t("partialShort");
    if (status.kind === "ready" && !waiting && !status.warnings.length) summary = t("readyShort");
    showStatus(details, summary);
    node.setDirtyCanvas?.(true, true);
  }
  const session = new TranslationSession({
    read, writeText: (value) => write("output_text", value), writeState: (value) => write("translation_state", value),
    request: ({token, ...body}) => {
      if (!local) return requestJson("translate", {...body, token});
      localPhase = "preparing";
      session.emit();
      return requestJson("local/translate", body, phase => {
        if (!session.accepting || session.disposed) return;
        const label = {downloading: "downloading", loading: "loading", translating: "busy"}[phase];
        if (label) { localPhase = label; session.emit(); }
      });
    }, notify: render,
    currentContext: () => {
      const graph = node.graph;
      const root = app.rootGraph ?? app.graph;
      return { scope: graph, configRevision: revisions[local ? "local" : "studio"],
        attached: Boolean(graph && (graph.rootGraph ?? graph) === root && graph.getNodeById(node.id) === node) };
    },
  });
  live = new LiveTranslation({
    valid: () => !composing && session.currentContext().attached && Boolean(String(read().source_text).trim()),
    notify: () => { if (String(read().source_text).trim()) showStatus(t("liveWaiting")); else session.restore(); },
    run: async () => {
      if (guardLinks()) return;
      const config = configForNode();
      const requestId = globalThis.crypto?.randomUUID?.() ?? `plamo-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      const result = await session.translate({ config, token: apiToken, requestId });
      if (result.applied) translationConfig = JSON.stringify(config);
      if (local && session.status.error === result.error && ["LOCAL_DEPS", "LOCAL_MISSING"].includes(result.error?.code)) showSettings(true);
    },
  });
  const observe = () => {
    if (!internalWrite && !restoring && !app.configuringGraph) session.observe();
  };
  for (const [name, widget] of Object.entries(widgets)) {
    widget.options ??= {};
    widget.options.socketless = true;
    widget.hidden = true;
    if (name !== "target_lang" && name !== "source_lang") widget.dynamicPrompts = false;
    const original = widget.callback;
    const callback = function (...args) { const result = original?.apply(this, args); observe(); return result; };
    widget.callback = callback;
    cleanups.push(() => { if (widget.callback === callback) widget.callback = original; });
    const input = widget.element;
    if (input?.addEventListener) {
      input.addEventListener("input", observe);
      input.addEventListener("compositionend", observe);
      cleanups.push(() => {
        input.removeEventListener("input", observe);
        input.removeEventListener("compositionend", observe);
      });
    }
  }
  function wrap(method, makeWrapper) {
    const original = node[method];
    const wrapper = makeWrapper(original);
    node[method] = wrapper;
    cleanups.push(() => { if (node[method] === wrapper) node[method] = original; });
  }
  wrap("onWidgetChanged", (original) => function (...args) {
    const result = original?.apply(this, args); observe(); return result;
  });
  wrap("onConnectInput", (original) => function (slot, ...args) {
    if (INPUT_NAMES.includes(this.inputs?.[slot]?.name)) return false;
    return original?.call(this, slot, ...args);
  });
  wrap("configure", (original) => function (...args) {
    live.cancel();
    session.settingsChanged();
    restoring = true;
    try { return original?.apply(this, args); }
    finally {
      restoring = false;
      const [width, height] = this.size;
      const [minWidth, minHeight] = this.computeSize();
      if (width < minWidth || height < minHeight) {
        this.setSize([Math.max(width, minWidth), Math.max(height, minHeight)]);
      }
      session.restore();
      updateLiveButton();
    }
  });
  const controller = {
    session, local,
    restore: () => { live.cancel(); session.restore(); updateLiveButton(); },
    cancelLive: () => live.cancel(),
    dispose() {
      live.dispose(); session.dispose(); clearTimeout(copyTimer);
      for (const cleanup of cleanups.splice(0)) cleanup();
      controllers.delete(controller); nodeControllers.delete(node);
    },
  };
  wrap("onRemoved", (original) => function (...args) {
    controller.dispose();
    return original?.apply(this, args);
  });
  nodeControllers.set(node, controller); controllers.add(controller);
  const [minWidth, minHeight] = node.computeSize();
  node.setSize?.([Math.max(420, minWidth), Math.max(200, minHeight)]);
}

app.registerExtension({
  name: "PLaMo.PromptTranslator",
  settings: [
    ...Object.entries(localSettingIds).map(([key, id]) => ({id, name: id, type: "hidden",
      defaultValue: {model_key: LOCAL_MODEL, max_tokens: 512, timeout_seconds: 180}[key]})),
    { id: settingIds.url, name: t("url"), type: "text", defaultValue: DEFAULT_CONFIG.url },
    { id: settingIds.model_key, name: t("model"), type: "text", defaultValue: "" },
    { id: settingIds.timeout_seconds, name: t("timeout"), type: "number", defaultValue: 60, attrs: { min: 1, max: 600, step: 1 } },
    { id: settingIds.max_tokens, name: t("tokens"), type: "combo", defaultValue: 512,
      options: [256, 512, 1024, 2048].map((value) => ({ text: String(value), value })) },
  ].map((setting) => ({ ...setting, category: ["PLaMo", "Translator", setting.id],
    telemetry: { trackChanges: false }, onChange: (value, oldValue) => {
      if (value !== oldValue) settingsChanged(Object.values(localSettingIds).includes(setting.id));
    } })),
  nodeCreated(node) { installNode(node); },
  loadedGraphNode(node) { installNode(node); nodeControllers.get(node)?.restore(); },
  beforeConfigureGraph() {
    activeDialog?.close();
    for (const controller of controllers) { controller.cancelLive(); controller.session.settingsChanged(); }
  },
});
