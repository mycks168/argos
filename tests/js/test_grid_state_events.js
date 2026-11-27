const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

test("GridはSSEの更新番号で状態APIを再取得し、再接続時も状態を更新する", async () => {
  const html = fs.readFileSync(path.join(__dirname, "../../src/argos/services/dashboard/static/grid.html"), "utf8");
  const applied = [];
  let source;
  let revision = 0;
  const context = {
    applyMuteState() {}, renderSlots() {}, authHeaders:() => ({}), connection:{},
    fetch:async url => ({ok:true, json:async () => url.endsWith("slots") ? {slots:[]} : {revision:++revision}}),
    applyState:state => applied.push(state), console,
    EventSource:class {
      constructor() {source = this; this.listeners = {};}
      addEventListener(name, callback) {this.listeners[name] = callback;}
    },
  };
  vm.createContext(context);
  const begin = html.indexOf("    async function refreshState()");
  const end = html.indexOf("    initialize().catch", begin);
  vm.runInContext(html.slice(begin, end), context);
  await vm.runInContext("initialize()", context);
  assert.equal(applied.length, 1);
  await source.listeners.update({data:'{"revision":100}'});
  assert.equal(applied.at(-1).revision, 2);
  source.onopen();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(applied.at(-1).revision, 3);
  assert.equal(context.connection.textContent, "接続済み");
});
