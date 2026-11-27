const {test} = require("node:test");
const assert = require("node:assert/strict");
const {Screen, mountSettings, normalizeUrl, isActiveStatus} = require("../../src/argos/services/dashboard/static/idle_screen.js");

/** フォーカス・DOMイベント・タイマーを外部ブラウザに依存せず制御する。 */
function environment(stored = null) {
  const timers = new Map();
  const intervals = new Map();
  const listeners = {};
  let nextId = 0;
  const element = () => ({hidden:false, attrs:{}, children:[], handlers:{}, value:"",
    classList:{values:new Set(), add(name) {this.values.add(name);}, toggle(name, on) {if (on) this.values.add(name); else this.values.delete(name);}},
    setAttribute(key, value) {this.attrs[key] = value;},
    addEventListener(key, callback) {this.handlers[key] = callback;},
    appendChild(child) {this.children.push(child);}, remove() {this.removed = true;},
    focus() {document.activeElement = this;},
    querySelector(selector) {return this.parts[selector];},
    parts:{},
  });
  global.document = {createElement:element, body:element(), activeElement:element()};
  const createElement = document.createElement;
  document.createElement = () => {const el = createElement(); el.parts.time = element(); return el;};
  global.location = {href:"http://argos.test/", protocol:"http:"};
  global.localStorage = {
    getItem:() => stored, setItem:(_key, value) => {stored = value;}, removeItem:() => {stored = null;},
  };
  global.addEventListener = (key, callback) => {listeners[key] = callback;};
  global.setTimeout = (callback, delay) => {timers.set(++nextId, {callback, delay}); return nextId;};
  global.clearTimeout = id => timers.delete(id);
  global.setInterval = callback => {intervals.set(++nextId, callback); return nextId;};
  global.clearInterval = id => intervals.delete(id);
  const tick = id => {const timer = timers.get(id); timers.delete(id); timer.callback();};
  return {timers, intervals, listeners, tick, element, get stored() {return stored;}};
}

test("HTTP(S)だけを許可し、HTTPSでHTTPページを拒否する", () => {
  environment();
  assert.equal(normalizeUrl(" /page "), "http://argos.test/page");
  assert.equal(normalizeUrl(""), "");
  assert.equal(normalizeUrl("  "), "");
  assert.equal(normalizeUrl(null), "");
  assert.equal(normalizeUrl("https://display.test"), "https://display.test/");
  for (const url of ["javascript:alert(1)", "file:///tmp/a", "http://u:p@display.test", "http://["]) {
    assert.throws(() => normalizeUrl(url));
  }
  assert.throws(() => normalizeUrl("http://display.test", {href:"https://argos.test", protocol:"https:"}), /HTTPS/);
  assert.equal(isActiveStatus("listening"), true);
  assert.equal(isActiveStatus("locked"), false);
});

test("未設定は黒画面、操作で復帰しフォーカスとタイマーを復元する", () => {
  const env = environment();
  const focused = document.activeElement;
  const screen = new Screen({url:"", timeoutMs:120});
  assert.equal(env.timers.get(screen.timer).delay, 120);
  env.tick(screen.timer);
  assert.equal(screen.active, true);
  assert.equal(screen.frame, null);
  assert.equal(screen.root.attrs["aria-hidden"], "false");
  assert.equal(document.activeElement, screen.root);
  screen.show();
  for (const event of ["pointerdown", "touchstart", "keydown", "wheel"]) {
    env.listeners[event]();
    assert.equal(screen.root.hidden, true);
    assert.equal(document.activeElement, focused);
    assert.equal(env.timers.size, 1);
  }
  screen.show();
  screen.root.handlers.click();
  assert.equal(screen.active, false);
});

test("無効設定は表示せず、処理中はSSE更新がなくても待機へ移らない", () => {
  environment();
  const disabled = new Screen({timeoutMs:0});
  disabled.show();
  assert.equal(disabled.active, false);
  assert.equal(disabled.timer, null);
  let busy = true;
  const screen = new Screen({timeoutMs:100, url:"", isBusy:() => busy});
  screen.show();
  assert.equal(screen.active, false);
  busy = false;
  screen.show();
  assert.equal(screen.active, true);
});

test("Webページは待機時だけ読み込み、操作後は通信と時計を停止する", () => {
  const env = environment();
  const screen = new Screen({timeoutMs:100, url:"http://display.test/"});
  screen.show();
  const frame = screen.frame;
  assert.equal(frame.src, "http://display.test/");
  assert.equal(frame.attrs.referrerpolicy, "no-referrer");
  assert.equal(frame.attrs.sandbox, "allow-scripts allow-same-origin");
  assert.equal(frame.attrs.inert, "");
  assert.equal(frame.tabIndex, -1);
  assert.equal(frame.hidden, true);
  frame.handlers.load();
  assert.equal(frame.hidden, false);
  assert.match(screen.clock.textContent, /\d/);
  for (const callback of env.intervals.values()) callback();
  screen.reset();
  assert.equal(frame.removed, true);
  assert.equal(screen.frame, null);
  assert.equal(env.intervals.size, 0);
  assert.equal(env.timers.size, 1);
  frame.handlers.load();
  screen.show();
  frame.handlers.error();
  assert.notEqual(screen.frame, null);
});

test("失敗・タイムアウト・不正URLでも時計と復帰手段が残る", () => {
  const env = environment();
  const screen = new Screen({timeoutMs:100, url:"http://display.test/"});
  screen.show();
  screen.frame.handlers.error();
  assert.equal(screen.frame, null);
  assert.equal(screen.active, true);
  screen.reset(); screen.show();
  env.tick(screen.loadTimer);
  assert.equal(screen.frame, null);
  screen.reset();
  screen.options.url = "javascript:alert(1)";
  screen.show();
  assert.equal(screen.frame, null);
  assert.match(screen.clock.textContent, /\d/);
});

test("初回状態と定期更新は待機を保ち、PTT・通知・状態変化で復帰する", () => {
  environment();
  const screen = new Screen({timeoutMs:100, url:""});
  const state = {status:{code:"ready"}, display_activity:{sequence:1}, notifications:[{id:"old"}]};
  screen.show(); screen.update(state);
  assert.equal(screen.active, true);
  screen.update(state);
  assert.equal(screen.active, true);
  screen.update({...state, display_activity:{sequence:2}});
  assert.equal(screen.active, false);
  screen.show(); screen.update({...state, notifications:[{id:"new"}]});
  assert.equal(screen.active, false);
  for (const code of ["listening", "thinking", "speaking"]) {
    screen.show(); screen.update({...state, status:{code}});
    assert.equal(screen.active, false);
  }
  screen.show(); screen.update({...state, center_alert:{active:true}});
  assert.equal(screen.active, false);
  screen.show();
  assert.equal(screen.active, false);
  screen.update({}); screen.show();
  assert.equal(screen.active, true);
});

test("ブラウザ専用設定が優先され、空欄と共通設定を区別する", () => {
  const env = environment("https://local.test/");
  const screen = new Screen({timeoutMs:100, url:"http://shared.test/"});
  screen.show();
  assert.equal(screen.frame.src, "https://local.test/");
  env.listeners.storage({key:"other"});
  assert.equal(screen.active, true);
  env.listeners.storage({key:"argos-dashboard-idle-url"});
  assert.equal(screen.active, false);
  screen.show(); env.listeners.storage({key:null});
  assert.equal(screen.active, false);
  const root = env.element();
  const input = env.element();
  const status = env.element();
  const save = env.element();
  const reset = env.element();
  root.parts = {input, '[role="status"]':status, '[data-action="save"]':save, '[data-action="reset"]':reset};
  mountSettings(root, screen.options);
  assert.equal(input.value, "https://local.test/");
  input.value = ""; save.handlers.click();
  assert.equal(env.stored, "");
  screen.show();
  assert.equal(screen.frame, null);
  reset.handlers.click();
  assert.equal(env.stored, null);
  assert.equal(input.value, "http://shared.test/");
  input.value = "file:///tmp/a"; save.handlers.click();
  assert.match(status.textContent, /保存できません/);
  global.localStorage.setItem = () => {throw new Error("storage denied");};
  input.value = "https://valid.test/"; save.handlers.click();
  assert.match(status.textContent, /storage denied/);
  global.localStorage.getItem = () => {throw new Error("storage denied");};
  screen.reset(); screen.show();
  assert.equal(screen.frame.src, "http://shared.test/");
  mountSettings(root, screen.options);
  assert.equal(input.value, "http://shared.test/");
});

test("既存のルートを利用し、別の入力欄に移ったフォーカスを奪わない", () => {
  const env = environment();
  const root = document.createElement("div");
  const screen = new Screen({timeoutMs:100, url:"", root});
  screen.show();
  const other = env.element(); other.focus();
  screen.reset();
  assert.equal(document.activeElement, other);
  assert.equal(document.body.children.length, 0);
});


test("処理中の更新でも復帰し、ブラウザ向けのAPIも公開する", () => {
  environment();
  let busy = false;
  const screen = new Screen({timeoutMs:100, url:"", isBusy:() => busy});
  screen.show(); busy = true; screen.update({});
  assert.equal(screen.active, false);
  const fs = require("node:fs");
  const vm = require("node:vm");
  const filename = require.resolve("../../src/argos/services/dashboard/static/idle_screen.js");
  const context = {window:{}};
  vm.runInNewContext(fs.readFileSync(filename, "utf8"), context, {filename});
  assert.equal(typeof context.window.ArgosIdleScreen.Screen, "function");
});
