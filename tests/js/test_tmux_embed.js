/* 端末メッセージの認証境界とスロット切替競合を検証する。 */
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

function setup() {
  const sent = [];
  const frame = {style:{}, contentWindow:{postMessage:message => sent.push(message)},
    addEventListener(type, callback) {this[type] = callback;}, remove() {this.removed = true;}};
  const host = {style:{}, replaceChildren(child) {this.child = child;}, dispatchEvent(event) {this.event = event;}};
  const context = {location:{origin:"http://argos"}, document:{createElement:() => frame},
    addEventListener() {}, removeEventListener() {}, KeyboardEvent:class {constructor(type, fields) {this.type = type; Object.assign(this, fields);}}};
  vm.createContext(context);
  const filename = path.join(__dirname, "../../src/argos/services/dashboard/static/tmux_embed.js");
  vm.runInContext(fs.readFileSync(filename, "utf8"), context, {filename});
  return {context, frame, host, sent, View:context.ArgosTmuxEmbed.ConsoleView};
}

test("tmuxだけを埋め込み、同一origin・同一frameの操作だけを受け取る", async () => {
  const {View, frame, host, sent} = setup();
  const calls = [];
  const view = new View(host, "secret", {onVoice:() => calls.push("voice"), onSubmitted:() => calls.push("submitted")});
  await view.setSlot({name:"作業", provider:"claude", execution_mode:"cli"});
  assert.equal(view.isEnabled, false);
  await view.setSlot({name:"常駐", provider:"claude", execution_mode:"tmux"});
  frame.load();
  assert.equal(view.isEnabled, true);
  assert.equal(sent[0].token, "secret");
  assert.equal(frame.src.includes("secret"), false);
  const event = {origin:"http://evil", source:frame.contentWindow, data:{source:"argos-console", type:"voice"}};
  view.receive(event);
  event.origin = "http://argos";
  view.receive(event);
  event.data.type = "submitted";
  view.receive(event);
  event.data = {source:"argos-console", type:"ptt", eventType:"keydown", code:"F13"};
  view.receive(event);
  assert.equal(host.event.code, "F13");
  assert.deepEqual(calls, ["voice", "submitted"]);
  await view.setSlot({name:"通常", provider:"codex", execution_mode:"cli"});
  assert.equal(view.isEnabled, false);
  assert.equal(frame.removed, true);
  view.dispose();
});

test("遅延した能力照会が後から別スロットを上書きしない", async () => {
  const {View, context, host} = setup();
  let resolve;
  context.fetch = () => new Promise(complete => {resolve = complete;});
  const view = new View(host, "secret");
  const pending = view.setSlot({name:"遅延", provider:"remote"});
  await view.setSlot({name:"通常", provider:"codex", execution_mode:"cli"});
  resolve({ok:true, json:async () => ({slots:[{name:"遅延", provider:"remote", execution_mode:"tmux"}]})});
  await pending;
  assert.equal(view.isEnabled, false);
  view.dispose();
});

test("準備完了通知で設定を再送し、別フレームには渡さない", async () => {
  const {View, frame, host, sent} = setup();
  const view = new View(host, "secret");
  await view.setSlot({name:"常駐", provider:"claude", execution_mode:"tmux"});
  const data = {source:"argos-console", type:"ready"};
  view.receive({origin:"http://argos", source:{}, data});
  assert.equal(sent.length, 0);
  view.receive({origin:"http://argos", source:frame.contentWindow, data});
  assert.equal(sent[0].name, "常駐");
  view.dispose();
  const count = sent.length;
  frame.load();
  assert.equal(sent.length, count);
});

test("能力照会失敗後も同じスロットで再試行できる", async () => {
  const {View, context, host} = setup();
  context.fetch = async () => ({ok:false});
  const view = new View(host, "secret");
  const slot = {name:"常駐", provider:"claude"};
  await view.setSlot(slot);
  assert.match(host.textContent, /取得できません/);
  context.fetch = async () => ({ok:true, json:async () => ({slots:[{...slot, execution_mode:"tmux"}]})});
  await view.setSlot(slot);
  assert.equal(view.isEnabled, true);
  view.dispose();
});
