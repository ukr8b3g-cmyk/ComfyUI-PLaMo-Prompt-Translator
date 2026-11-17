import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { DEFAULT_CONFIG, FORMAT, TranslationSession, inspectState, makeManualState } from "../web/state.mjs";

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function harness(initial = {}) {
  const values = { source_text: "赤い服の女性", target_lang: "en", output_text: "", translation_state: "", ...initial };
  const context = { scope: {}, attached: true, configRevision: 0 };
  const calls = [];
  const session = new TranslationSession({
    read: () => ({ ...values }),
    writeText: (text) => { values.output_text = text; },
    writeState: (state) => { values.translation_state = state; },
    currentContext: () => ({ ...context }),
    request: (body) => {
      const pending = deferred(); calls.push({ body, ...pending }); return pending.promise;
    },
  });
  const start = () => session.translate({ config: { ...DEFAULT_CONFIG, model_key: "plamo" }, token: "session-only", requestId: `request-${calls.length}` });
  const result = (index = 0, text = "A woman wearing red.") => ({
    request_id: calls[index].body.request_id, status: "translated", text,
    translation_state: {
      v: 1, status: "translated", source_snapshot: calls[index].body.source_text,
      target_lang: calls[index].body.target_lang, model_key: "plamo", format: FORMAT,
    },
  });
  function edit(name, value) { values[name] = value; session.observe(); }
  return { values, context, calls, session, start, result, edit };
}

test("complete translation becomes a saved editable output without a second confirmation", async () => {
  const h = harness();
  const job = h.start();
  assert.equal(h.session.busy, true);
  assert.equal(h.values.translation_state, "");
  h.calls[0].resolve(h.result());
  await job;
  assert.equal(h.values.output_text, "A woman wearing red.");
  assert.equal(h.session.status.kind, "ready");
  assert.equal(inspectState(h.values).status, "translated");
  assert.equal(h.session.busy, false);
  assert.ok(!h.values.translation_state.includes("session-only"));
});

test("output-only edits preserve acceptance and exact whitespace", async () => {
  const h = harness(); const job = h.start(); h.calls[0].resolve(h.result()); await job;
  const state = h.values.translation_state;
  h.edit("output_text", "  red coat\n(red dress:1.2), {A|B}  ");
  assert.equal(h.values.output_text, "  red coat\n(red dress:1.2), {A|B}  ");
  assert.equal(h.values.translation_state, state);
  assert.equal(h.session.status.kind, "ready");
});

test("changing source or target invalidates acceptance without deleting the old text", () => {
  for (const [field, value] of [["source_text", "青い服"], ["target_lang", "ja"]]) {
    const h = harness({ output_text: "old translation" });
    h.session.acceptCurrent("plamo"); h.edit(field, value);
    assert.equal(h.values.output_text, "old translation");
    assert.equal(h.values.translation_state, "");
    assert.equal(inspectState(h.values), null);
  }
});

test("editing then reverting the source still rejects the in-flight response", async () => {
  const h = harness({ output_text: "keep me" }); const original = h.values.source_text;
  const job = h.start();
  h.edit("source_text", "changed"); h.edit("source_text", original);
  h.calls[0].resolve(h.result());
  assert.deepEqual(await job, { ignored: true });
  assert.equal(h.values.output_text, "keep me");
  assert.equal(h.values.translation_state, "");
});

test("editing the output while waiting prevents a late response from overwriting it", async () => {
  const h = harness(); const job = h.start(); h.edit("output_text", "my edit");
  h.calls[0].resolve(h.result()); await job;
  assert.equal(h.values.output_text, "my edit");
  assert.equal(inspectState(h.values), null);
});

test("manual acceptance during translation invalidates old success without changing the text first", async () => {
  const h = harness({ output_text: "use this existing text" }); const job = h.start();
  h.session.acceptCurrent("plamo");
  assert.equal(h.session.busy, true);
  assert.equal(h.session.accepting, false);
  h.calls[0].resolve(h.result()); await job;
  assert.equal(h.values.output_text, "use this existing text");
  assert.equal(inspectState(h.values).status, "manual");
  assert.equal(h.session.status.kind, "ready");
});

test("a late failure cannot revoke text manually accepted while the request was running", async () => {
  const h = harness({ output_text: "manual" }); const job = h.start();
  h.session.acceptCurrent();
  h.calls[0].reject(Object.assign(new Error("late error"), { code: "TIMEOUT" })); await job;
  assert.equal(inspectState(h.values).status, "manual");
  assert.equal(h.session.status.kind, "ready");
});

test("cancelling adoption keeps the operation busy until it actually settles", async () => {
  const h = harness({ output_text: "keep" }); const job = h.start();
  h.session.cancelAcceptance();
  assert.equal(h.session.busy, true);
  assert.equal(h.session.status.kind, "cancelled");
  await h.start(); assert.equal(h.calls.length, 1);
  h.calls[0].resolve(h.result()); await job;
  assert.equal(h.values.output_text, "keep");
  assert.equal(h.session.busy, false);
  assert.equal(inspectState(h.values), null);
});

test("changing connection settings discards old results while accepted offline text remains valid", async () => {
  const h = harness({ output_text: "keep" });
  h.session.acceptCurrent();
  h.context.configRevision += 1; h.session.settingsChanged();
  assert.equal(inspectState(h.values).status, "manual");
  const job = h.start(); h.context.configRevision += 1; h.session.settingsChanged();
  h.calls[0].resolve(h.result()); await job;
  assert.equal(h.values.output_text, "keep");
  assert.equal(h.session.status.kind, "review");
});

test("even a context change without an event does not leave a stale busy label", async () => {
  const h = harness(); const job = h.start(); h.context.configRevision += 1;
  h.calls[0].resolve(h.result()); await job;
  assert.equal(h.values.output_text, "");
  assert.equal(h.session.busy, false);
  assert.equal(h.session.status.kind, "empty");
});

test("partial output is visible but requires explicit adoption", async () => {
  const h = harness(); const job = h.start();
  h.calls[0].resolve({ request_id: "request-0", status: "partial", text: "A woman wearing", translation_state: null });
  await job;
  assert.equal(h.values.output_text, "A woman wearing");
  assert.equal(h.session.status.kind, "partial");
  assert.equal(inspectState(h.values), null);
  h.session.acceptCurrent(); assert.equal(inspectState(h.values).status, "manual");
});

test("failed translation preserves the existing text and clears old readiness", async () => {
  const h = harness({ output_text: "last good text" }); h.session.acceptCurrent();
  const job = h.start(); h.calls[0].reject(new Error("offline")); await job;
  assert.equal(h.values.output_text, "last good text");
  assert.equal(h.values.translation_state, "");
  assert.equal(h.session.status.kind, "error");
});

test("request id and returned snapshot must match before either field is replaced", async () => {
  for (const mismatch of ["request", "snapshot", "status"]) {
    const h = harness({ output_text: "keep" }); const job = h.start(); const response = h.result();
    if (mismatch === "request") response.request_id = "another request";
    if (mismatch === "snapshot") response.translation_state.source_snapshot = "another source";
    if (mismatch === "status") response.translation_state.status = "manual";
    h.calls[0].resolve(response); await job;
    assert.equal(h.values.output_text, "keep"); assert.equal(h.values.translation_state, "");
    assert.equal(h.session.status.kind, "error");
  }
});

test("workflow scope change and node removal both discard pending results", async () => {
  for (const action of ["switch", "remove"]) {
    const h = harness({ output_text: "keep" }); const job = h.start();
    if (action === "switch") h.context.scope = {};
    else h.session.dispose();
    h.calls[0].resolve(h.result()); await job;
    assert.equal(h.values.output_text, "keep"); assert.equal(h.values.translation_state, "");
  }
});

test("two nodes keep their results separate when responses finish in reverse order", async () => {
  const a = harness({ source_text: "赤" }), b = harness({ source_text: "青" });
  const ja = a.start(), jb = b.start();
  b.calls[0].resolve(b.result(0, "blue")); await jb;
  a.calls[0].resolve(a.result(0, "red")); await ja;
  assert.equal(a.values.output_text, "red"); assert.equal(b.values.output_text, "blue");
  assert.equal(inspectState(a.values).source_snapshot, "赤");
  assert.equal(inspectState(b.values).source_snapshot, "青");
});

test("manual output works with an empty source and restores without contacting LM Studio", () => {
  const values = { source_text: "", target_lang: "en", output_text: "  manual text  " };
  const translation_state = JSON.stringify(makeManualState(values));
  const h = harness({ ...values, translation_state });
  assert.equal(h.session.status.kind, "ready");
  assert.equal(h.calls.length, 0);
  assert.equal(h.values.output_text, "  manual text  ");
  assert.throws(() => makeManualState({ ...values, output_text: "  \n" }), { code: "INPUT_EMPTY" });
});

test("invalid restored state never becomes ready", () => {
  const v = { source_text: "source", target_lang: "en", output_text: "output" };
  const valid = makeManualState(v);
  for (const state of ["", "not-json", JSON.stringify({ ...valid, v: 2 }),
    JSON.stringify({ ...valid, source_snapshot: "old" }),
    JSON.stringify({ ...valid, target_lang: "ja" }), JSON.stringify({ ...valid, format: "unknown" })]) {
    assert.equal(inspectState({ ...v, translation_state: state }), null);
  }
});

test("long source and joined translation are saved as one editable result", async () => {
  const h = harness({ source_text: "あ".repeat(7000) });
  const job = h.start();
  const output = "Long translation.\n".repeat(1000);
  assert.equal(h.calls.length, 1);
  h.calls[0].resolve(h.result(0, output));
  await job;
  assert.equal(h.values.output_text, output);
  assert.equal(inspectState(h.values).source_snapshot, h.values.source_text);
  h.session.restore();
  assert.equal(h.session.status.kind, "ready");
});

test("empty or oversized source fails before a network request and preserves existing text", async () => {
  for (const source_text of [" \n", "あ".repeat(16_001)]) {
    const h = harness({ source_text, output_text: "keep" }); await h.start();
    assert.equal(h.calls.length, 0); assert.equal(h.values.output_text, "keep");
    assert.equal(h.session.status.kind, "error");
  }
});

// Adapter checks execute the unmodified entry module and state module. The DOM,
// Comfy widgets, serializer and API are deliberately small test doubles; these
// checks do not establish rendering or compatibility with a live ComfyUI build.
// A child process enables Node's built-in VM module loader without requiring
// callers to add --experimental-vm-modules to the normal node --test command.
async function adapterHarness(hasRandomUUID = true) {
  class Element {
    constructor(tag) {
      this.tagName = tag.toUpperCase(); this.style = {}; this.children = [];
      this.listeners = new Map(); this.attributes = {}; this.value = "";
      this.dataset = {};
      this.textContent = "";
    }
    setAttribute(name, value) { this.attributes[name] = value; }
    append(...children) { this.children.push(...children); }
    addEventListener(name, callback) {
      const callbacks = this.listeners.get(name) ?? [];
      callbacks.push(callback); this.listeners.set(name, callbacks);
    }
    removeEventListener(name, callback) {
      this.listeners.set(name, (this.listeners.get(name) ?? []).filter((item) => item !== callback));
    }
    dispatch(name) { for (const callback of this.listeners.get(name) ?? []) callback({ type: name }); }
  }
  const document = { createElement: (tag) => new Element(tag), body: new Element("body") };
  const settings = new Map([["PLaMo.Translator.Model", "plamo"], ["Comfy.Locale", "en"]]);
  const graph = { nodes: new Map(), versions: 0,
    getNodeById(id) { return this.nodes.get(id); }, incrementVersion() { this.versions += 1; } };
  let extension;
  const app = {
    rootGraph: graph, graph, canvas: {}, configuringGraph: false,
    extensionManager: { setting: {
      get(id) { return settings.get(id); },
      async set(id, value) {
        const oldValue = settings.get(id); settings.set(id, value);
        extension.settings.find((item) => item.id === id)?.onChange(value, oldValue);
      },
    } },
    registerExtension(value) {
      assert.equal(extension, undefined); extension = value;
      for (const item of value.settings) if (!settings.has(item.id)) settings.set(item.id, item.defaultValue);
    },
  };
  const calls = [];
  const api = { fetchApi(path, options) {
    assert.equal(options.method, "POST");
    assert.equal(options.headers["Content-Type"], "application/json");
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    calls.push({ path, options, body: JSON.parse(options.body),
      reply(body, status = 200) { resolve({ ok: status >= 200 && status < 300, status, json: async () => body }); },
      reject });
    return promise;
  } };
  const context = createContext({ document, navigator: { language: "en-US" },
    TextEncoder, AbortController, setTimeout, clearTimeout,
    crypto: hasRandomUUID ? { randomUUID: () => "adapter-request" } : {} });
  const state = new SourceTextModule(await readFile("web/state.mjs", "utf8"), { context, identifier: "state.mjs" });
  const languages = JSON.parse(await readFile("web/languages.json", "utf8"));
  const languageModule = new SyntheticModule(["default"], function () { this.setExport("default", languages); }, { context });
  const appModule = new SyntheticModule(["app"], function () { this.setExport("app", app); }, { context });
  const apiModule = new SyntheticModule(["api"], function () { this.setExport("api", api); }, { context });
  const live = new SourceTextModule(await readFile("web/live.mjs", "utf8"), { context });
  const entry = new SourceTextModule(await readFile("web/plamo.js", "utf8"), { context, identifier: "plamo.js" });
  await entry.link((specifier) => {
    if (specifier === "../../scripts/app.js") return appModule;
    if (specifier === "../../scripts/api.js") return apiModule;
    if (specifier.split("?")[0] === "./state.mjs") return state;
    if (specifier.split("?")[0] === "./live.mjs") return live;
    if (specifier === "./languages.json") return languageModule;
    throw new Error(`Unexpected import: ${specifier}`);
  });
  await entry.evaluate();

  let nextId = 1;
  function makeNode(initial = {}, comfyClass = "PLaMoPromptTranslator") {
    const values = { source_text: "赤い服の女性", target_lang: "en", output_text: "", translation_state: "", source_lang: "auto", ...initial };
    const node = {
      id: nextId++, comfyClass, graph, size: [320, 200], widgets: [],
      inputs: Object.keys(values).map((name) => ({ name, link: null })),
      changes: [], callbacks: [], writes: [], dirty: 0,
      setDirtyCanvas() { this.dirty += 1; },
      computeSize() { return [440, 480]; }, setSize(size) { this.size = size; },
      isInputConnected(index) { return this.inputs[index].link !== null; },
      onWidgetChanged(name, value, previous) { this.changes.push({ name, value, previous }); },
      onConnectInput() { return true; },
      onRemoved() { this.removed = true; },
      addWidget(type, name, value, callback, options) {
        const widget = { type, name, value, callback, options: { ...options } };
        this.widgets.push(widget); return widget;
      },
      addDOMWidget(name, type, element, options) {
        const widget = this.addWidget(type, name, options.getValue(), undefined, options);
        widget.element = element; return widget;
      },
      configure(saved) {
        this.widgets.filter((widget) => widget.serialize !== false).forEach((widget, index) => {
          if (index < saved.widgets_values.length) widget.setValue(saved.widgets_values[index], { node: this, canvas: app.canvas });
        });
      },
      serialize() { return { widgets_values: this.widgets.filter((widget) => widget.serialize !== false).map((widget) => widget.value) }; },
    };
    for (const [name, value] of Object.entries(values)) {
      const widget = node.addWidget(name === "target_lang" ? "combo" : "string", name, value,
        (next) => node.callbacks.push({ name, value: next }), {});
      widget.element = new Element(name === "target_lang" ? "select" : "textarea");
      widget.setValue = function (next, options) {
        assert.equal(options.node, node); assert.equal(options.canvas, app.canvas);
        const previous = this.value;
        this.value = next; this.element.value = next; node.writes.push({ name, value: next });
        this.callback?.(next); node.onWidgetChanged?.(name, next, previous, this);
        node.graph.incrementVersion();
      };
    }
    graph.nodes.set(node.id, node);
    return node;
  }
  const widget = (node, name) => node.widgets.find((item) => item.name === name);
  const values = (node) => Object.fromEntries(node.widgets.filter((item) => item.options.serialize !== false)
    .map((item) => [item.name, item.value]));
  function find(element, match) {
    if (match(element)) return element;
    for (const child of element.children) {
      const found = find(child, match);
      if (found) return found;
    }
  }
  const panel = (node, match) => find(widget(node, "plamo_panel").element, match);
  const action = (node, name) => panel(node, (el) => el.dataset.plamoAction === name);
  const field = (node, name) => panel(node, (el) => el.dataset.plamoField === name);
  const status = (node) => panel(node, (el) => el.attributes.role === "status");
  function response(index = 0, text = "A woman wearing red.") {
    const body = calls[index].body;
    return { request_id: body.request_id, status: "translated", text,
      translation_state: { v: 1, status: "translated", source_snapshot: body.source_text,
        ...(body.source_lang ? { source_lang: body.source_lang } : {}),
        target_lang: body.target_lang, model_key: body.config.model_key, format: state.namespace.FORMAT } };
  }
  return { extension, app, calls, graph, makeNode, widget, values, response, action, field, status, state: state.namespace,
    flush: () => new Promise(setImmediate) };
}

function runAdapterCase(check, { hasRandomUUID = true } = {}) {
  const script = `import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createContext, SourceTextModule, SyntheticModule } from "node:vm";
${adapterHarness.toString()}
const harness = await adapterHarness(${JSON.stringify(hasRandomUUID)});
await (${check.toString()})(harness);
`;
  const result = spawnSync(process.execPath, ["--experimental-vm-modules", "--input-type=module"], {
    input: script, encoding: "utf8", timeout: 15000,
    cwd: fileURLToPath(new URL("..", import.meta.url)),
  });
  assert.equal(result.status, 0, [result.error?.message, result.stdout, result.stderr].filter(Boolean).join("\n"));
}

test("manual and Live translation work when LAN HTTP does not expose randomUUID", () => {
  runAdapterCase(async (h) => {
    const node = h.makeNode(); h.extension.nodeCreated(node);
    h.action(node, "translate").dispatch("click"); await h.flush();
    assert.equal(h.calls.length, 1);
    assert.match(h.calls[0].body.request_id, /^plamo-[0-9]+-[a-z0-9]+$/);
    h.calls[0].reply(h.response(0, "manual translation")); await h.flush();
    assert.equal(h.values(node).output_text, "manual translation");

    h.action(node, "live").dispatch("click");
    const source = h.field(node, "source_text");
    source.value = "a new source"; source.dispatch("input");
    await new Promise(resolve => setTimeout(resolve, 850));
    assert.equal(h.calls.length, 2);
    assert.equal(h.calls[1].body.source_text, "a new source");
    assert.match(h.calls[1].body.request_id, /^plamo-[0-9]+-[a-z0-9]+$/);
    assert.notEqual(h.calls[1].body.request_id, h.calls[0].body.request_id);
    h.calls[1].reply(h.response(1, "live translation")); await h.flush();
    assert.equal(h.values(node).output_text, "live translation");
  }, { hasRandomUUID: false });
});

test("live adapter: debounce, IME, newest input, off and manual output protection", () => {
  runAdapterCase(async (h) => {
    const node = h.makeNode(); h.extension.nodeCreated(node);
    const source = h.field(node, "source_text");
    const output = h.field(node, "output_text");
    const wait = () => new Promise(resolve => setTimeout(resolve, 850));
    assert.equal(node.properties.plamo_live, undefined);
    h.action(node, "live").dispatch("click"); await h.flush();
    assert.equal(h.calls.length, 1);
    source.value = "first"; source.dispatch("input");
    source.value = "latest"; source.dispatch("input");
    await wait(); assert.equal(h.calls.length, 1);
    h.calls[0].reply(h.response(0, "obsolete")); await h.flush();
    assert.equal(h.values(node).output_text, "");
    assert.equal(h.calls.length, 2);
    assert.equal(h.calls[1].body.source_text, "latest");
    output.value = "manual"; output.dispatch("input");
    h.calls[1].reply(h.response(1, "do not overwrite")); await h.flush();
    assert.equal(h.values(node).output_text, "manual");
    source.dispatch("compositionstart"); source.value = "変換中"; source.dispatch("input");
    await wait(); assert.equal(h.calls.length, 2);
    source.dispatch("compositionend"); await wait(); assert.equal(h.calls.length, 3);
    h.action(node, "live").dispatch("click");
    h.calls[2].reply(h.response(2, "off result")); await h.flush();
    assert.equal(h.values(node).output_text, "manual");
    assert.equal(node.properties.plamo_live, false);
    source.value = "off edit"; source.dispatch("input"); await wait();
    assert.equal(h.calls.length, 3);
  });
});

test("live adapter: restored ON is idle, completed toggle avoids repeat, removal clears waiting", () => {
  runAdapterCase(async (h) => {
    const node = h.makeNode(); node.properties = { plamo_live: true };
    h.extension.nodeCreated(node); h.extension.loadedGraphNode(node); await h.flush();
    assert.equal(h.calls.length, 0);
    h.action(node, "translate").dispatch("click"); await h.flush();
    h.calls[0].reply(h.response()); await h.flush();
    h.action(node, "live").dispatch("click"); h.action(node, "live").dispatch("click");
    await h.flush(); assert.equal(h.calls.length, 1);
    const source = h.field(node, "source_text"); source.value = "changed"; source.dispatch("input");
    node.onRemoved(); await new Promise(resolve => setTimeout(resolve, 850));
    assert.equal(h.calls.length, 1);
  });
});

test("entry adapter: registration keeps standard inputs and excludes transient controls from serialization", () => {
  runAdapterCase(async (h) => {
    assert.equal(h.extension.name, "PLaMo.PromptTranslator");
    assert.equal(h.extension.settings.length, 4);
    assert.deepEqual(Array.from(h.extension.settings, (setting) => setting.id),
      ["PLaMo.Translator.URL", "PLaMo.Translator.Model", "PLaMo.Translator.Timeout", "PLaMo.Translator.MaxTokens"]);
    const other = h.makeNode({}, "AnotherNode"); h.extension.nodeCreated(other);
    assert.equal(other.widgets.length, 5);
    const node = h.makeNode(); const originals = [...node.widgets];
    h.extension.nodeCreated(node); h.extension.loadedGraphNode(node); h.extension.nodeCreated(node);
    assert.equal(node.widgets.length, 6);
    originals.forEach((widget, index) => assert.equal(node.widgets[index], widget));
    assert.equal(h.widget(node, "translation_state").hidden, true);
    assert.equal(h.widget(node, "source_text").dynamicPrompts, false);
    assert.equal(h.widget(node, "output_text").dynamicPrompts, false);
    assert.ok(originals.every((widget) => widget.options.socketless === true));
    assert.ok(node.widgets.slice(5).every((widget) => widget.serialize === false && widget.options.serialize === false));
    assert.deepEqual(Object.keys(h.values(node)), ["source_text", "target_lang", "output_text", "translation_state", "source_lang"]);
    assert.equal(node.serialize().widgets_values.length, 5);
    assert.equal(h.action(node, "cancel").hidden, true);
    assert.equal(h.action(node, "accept").hidden, true);
    assert.deepEqual(Array.from(h.field(node, "target_lang").children.slice(0, 4), option => option.value),
      ["en", "zh-hans", "zh-hant", "ja"]);
    assert.equal(h.field(node, "source_lang").children[0].value, "auto");
    for (let index = 0; index < 5; index += 1) assert.equal(node.onConnectInput(index), false);
    assert.equal(h.calls.length, 0);
    node.onRemoved(); assert.equal(node.removed, true);
  });
});

test("entry adapter: button response uses widget setters and edit callbacks keep the queued values current", () => {
  runAdapterCase(async (h) => {
    const node = h.makeNode(); h.extension.nodeCreated(node);
    h.action(node, "translate").dispatch("click"); await h.flush();
    assert.equal(h.calls.length, 1);
    assert.equal(h.calls[0].path, "/plamo_translator/v1/translate");
    assert.equal(h.calls[0].body.source_text, "赤い服の女性");
    assert.equal(h.calls[0].body.config.model_key, "plamo");
    assert.equal(h.action(node, "translate").disabled, true);
    h.calls[0].reply(h.response()); await h.flush();
    assert.equal(h.widget(node, "output_text").value, "A woman wearing red.");
    assert.equal(h.field(node, "output_text").value, "A woman wearing red.");
    assert.equal(h.state.inspectState(h.values(node)).status, "translated");
    assert.ok(node.writes.some((write) => write.name === "output_text"));
    assert.ok(node.writes.some((write) => write.name === "translation_state" && write.value));
    assert.equal(h.action(node, "translate").disabled, false);
    assert.match(h.status(node).textContent, /^Ready\./);
    const acceptedState = h.widget(node, "translation_state").value;
    const edited = "  (red dress:1.2), {A|B}\n  ";
    h.field(node, "output_text").value = edited;
    h.field(node, "output_text").dispatch("input");
    assert.equal(h.values(node).output_text, edited);
    assert.equal(h.widget(node, "translation_state").value, acceptedState);
    assert.equal(h.state.inspectState(h.values(node)).status, "translated");
    // Older widgets can report user edits directly through their callbacks.
    const source = h.widget(node, "source_text"); source.value = "青い服"; source.callback(source.value);
    assert.equal(h.values(node).translation_state, "");
    assert.equal(h.values(node).output_text, edited);
    assert.equal(h.action(node, "accept").hidden, false);
    assert.equal(h.calls.length, 1);
  });
});

test("entry adapter: configure restores saved readiness and discards an earlier translation response", () => {
  runAdapterCase(async (h) => {
    const node = h.makeNode({ output_text: "  my manual prompt  " }); h.extension.nodeCreated(node);
    h.action(node, "accept").dispatch("click");
    const saved = JSON.parse(JSON.stringify(node.serialize()));
    assert.equal(saved.widgets_values.length, 5);
    assert.equal(JSON.parse(saved.widgets_values[3]).status, "manual");
    const restored = h.makeNode(); h.extension.nodeCreated(restored);
    const legacy = { widgets_values: saved.widgets_values.slice(0, 4) };
    restored.configure(legacy); h.extension.loadedGraphNode(restored);
    assert.equal(restored.widgets.length, 6);
    assert.equal(h.field(restored, "source_lang").value, "auto");
    assert.equal(h.state.inspectState(h.values(restored)).status, "manual");
    assert.equal(h.values(restored).output_text, "  my manual prompt  ");
    assert.match(h.status(restored).textContent, /^Ready\./);
    assert.equal(h.calls.length, 0);
    h.action(restored, "translate").dispatch("click"); await h.flush();
    assert.equal(h.calls.length, 1);
    h.extension.beforeConfigureGraph(); restored.configure(saved); h.extension.loadedGraphNode(restored);
    h.calls[0].reply(h.response(0, "late replacement")); await h.flush();
    assert.equal(h.values(restored).output_text, "  my manual prompt  ");
    assert.equal(h.state.inspectState(h.values(restored)).status, "manual");
    assert.equal(h.action(restored, "translate").disabled, false);
    assert.match(h.status(restored).textContent, /^Ready\./);
  });
});
