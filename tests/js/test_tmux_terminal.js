/* 初期化失敗やページ復元でも操作経路を失わないことを検証する。 */
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

/** ネットワークと端末描画を隔離してページの寿命を再現する。 */
function setup() {
  const events = {};
  const sent = [];
  const requests = [];
  const timers = [];
  const elements = Object.fromEntries(["status", "terminal", "voice", "cancel-audio", "discard", "reconnect"].map(name =>
    [name, {clientWidth:800, clientHeight:600, addEventListener(type, callback) {this[type] = callback;}}]));
  const controls = {shouldFail:false, isClosed:false, errorStatus:0, output:"", shouldDelayOpen:false};
  class Terminal {
    constructor() {this.cols = 80; this.rows = 24;}
    loadAddon() {}
    open() {if (controls.shouldFail) throw new Error("描画失敗");}
    onData(callback) {controls.input = callback;}
    attachCustomKeyEventHandler(callback) {controls.key = callback;}
    dispose() {}
    reset() {}
    write(bytes, complete) {controls.bytes = bytes; complete();}
  }
  const context = {
    Terminal, FitAddon:{FitAddon:class {fit() {}}},
    document:{getElementById:name => elements[name], addEventListener(type, callback) {events[type] = callback;}},
    location:{origin:"http://argos"}, parent:{postMessage:message => sent.push(message)},
    addEventListener(type, callback) {events[type] = callback;},
    setTimeout(callback) {timers.push(callback); return timers.length;}, clearTimeout() {},
    AbortController, URLSearchParams, TextEncoder, Uint8Array, atob, btoa,
    fetch:async (url, options) => {
      const payload = options.body ? JSON.parse(options.body) : {action:"read"};
      requests.push(payload);
      if (payload.action === "open") {
        if (controls.shouldDelayOpen) await new Promise(resolve => {controls.finishOpen = resolve;});
        return {ok:true, json:async () => ({connection_id:`connection-${requests.length}`})};
      }
      if (payload.action !== "read") return {ok:true, json:async () => ({})};
      if (controls.errorStatus) return {ok:false, status:controls.errorStatus, json:async () => ({error:"接続期限切れ"})};
      if (controls.output || controls.isClosed) {
        const output = {data:controls.output, offset:1, is_closed:controls.isClosed, has_pending_turn:controls.hasPendingTurn};
        controls.output = ""; controls.isClosed = false;
        return {ok:true, json:async () => output};
      }
      return await new Promise((resolve, reject) => {
        controls.finishRead = resolve;
        options.signal.addEventListener("abort", () => reject(new Error("中断")), {once:true});
      });
    },
  };
  context.window = context;
  vm.createContext(context);
  const filename = path.join(__dirname, "../../src/argos/services/dashboard/static/tmux_terminal.js");
  vm.runInContext(fs.readFileSync(filename, "utf8"), context, {filename});
  const message = data => events.message({origin:"http://argos", source:context.parent, data});
  const configure = () => message({type:"argos-console-config", name:"常駐", provider:"claude", token:"secret"});
  return {context, controls, events, elements, sent, requests, timers, message, configure};
}

/** 複数の非同期継続を確実に実行する。 */
async function flush() {await new Promise(resolve => setImmediate(resolve));}

test("初期化失敗を表示し、音声ボタンと再接続で復旧できる", async () => {
  const state = setup();
  state.controls.shouldFail = true;
  state.configure();
  await flush();
  assert.match(state.elements.status.textContent, /端末初期化エラー.*描画失敗/);
  state.elements.voice.click();
  assert.equal(state.sent.at(-1).type, "voice");
  state.elements["cancel-audio"].click();
  assert.equal(state.sent.at(-1).type, "cancel");
  state.elements.reconnect.click();
  await flush();
  assert.match(state.elements.status.textContent, /端末初期化エラー/);
  state.controls.shouldFail = false;
  state.elements.reconnect.click();
  await flush();
  assert.equal(state.requests.filter(item => item.action === "open").length, 1);
  state.message({type:"argos-console-dispose"});
});

test("設定未着時は再要求し、不正な送信元の設定を拒否する", async () => {
  const state = setup();
  assert.equal(state.sent[0].type, "ready");
  state.elements.reconnect.click();
  assert.equal(state.sent.at(-1).type, "ready");
  state.events.message({origin:"http://evil", source:state.context.parent, data:{type:"argos-console-config"}});
  assert.equal(state.requests.length, 0);
  state.configure(); state.configure();
  await flush();
  assert.equal(state.requests.filter(item => item.action === "open").length, 1);
  state.message({type:"argos-console-dispose"});
});

test("ページ復元と再接続の競合でも一つの接続だけ再開する", async () => {
  const state = setup();
  state.configure(); await flush();
  state.events.pagehide();
  state.events.pageshow();
  state.elements.reconnect.click();
  await flush();
  assert.equal(state.requests.filter(item => item.action === "open").length, 2);
  state.message({type:"argos-console-dispose"});
  state.events.pageshow(); state.elements.reconnect.click();
  await flush();
  assert.equal(state.requests.filter(item => item.action === "open").length, 2);
});

test("接続開始途中のページ離脱は遅着した接続も閉じる", async () => {
  const state = setup();
  state.controls.shouldDelayOpen = true;
  state.configure();
  state.events.pagehide();
  state.controls.finishOpen(); await flush();
  assert.equal(state.requests.at(-1).action, "close");
  state.message({type:"argos-console-dispose"});
});

test("端末出力、入力取消、PTTを既存経路へ送る", async () => {
  const state = setup();
  state.controls.output = btoa("ready");
  state.controls.hasPendingTurn = true;
  state.configure(); await flush();
  assert.match(state.elements.status.textContent, /接続済み/);
  assert.equal(state.sent.at(-1).type, "submitted");
  state.elements.discard.click(); await flush();
  assert.equal(atob(state.requests.at(-1).data), "\x15");
  state.controls.input("hello\r"); await flush();
  assert.equal(state.sent.at(-1).type, "submitted");
  assert.equal(state.controls.key({code:"Space"}), true);
  assert.equal(state.controls.key({code:"F13", type:"keydown", preventDefault() {}}), false);
  state.message({type:"argos-console-dispose"});
});

test("端末の寸法変更をまとめて送信する", async () => {
  const state = setup();
  state.configure(); await flush();
  state.timers[1](); await flush();
  assert.equal(state.requests.some(item => item.action === "resize"), false);
  state.context.FitAddon.FitAddon.prototype.fit = () => {state.context.Terminal.prototype.cols = 100;};
  // インスタンスが保持する寸法を変え、実際のfitによる更新を再現する。
  state.context.Terminal.prototype.reset = function () {this.cols = 100;};
  state.elements.reconnect.click(); await flush();
  const resizeCallback = state.timers.at(-2);
  resizeCallback(); await flush();
  assert.equal(state.requests.some(item => item.action === "resize"), true);
  state.message({type:"argos-console-dispose"});
});

test("期限切れと閉じた接続を新規attachへ切り替える", async () => {
  const state = setup();
  state.controls.errorStatus = 409;
  state.configure(); await flush();
  assert.equal(state.elements.status.textContent, "接続期限切れ");
  assert.equal(state.requests.at(-1).action, "close");
  state.controls.errorStatus = 0;
  state.controls.isClosed = true;
  state.timers.at(-1)(); await flush();
  assert.equal(state.requests.filter(item => item.action === "open").length, 3);
  state.message({type:"argos-console-dispose"});
});
