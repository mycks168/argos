/* 複数タイマーの一覧ページ（timers.html）の残り時間の書き方・並び順・色を検証する。 */
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const html = fs.readFileSync(path.join(__dirname, "../../src/argos/services/dashboard/static/timers.html"), "utf8");

/** ページのスクリプトから関数だけを取り出す（画面を作る部分は動かさない）。 */
function load() {
  const script = html.slice(html.indexOf("<script>") + 8, html.indexOf("</script>"));
  const context = {document: {getElementById: () => null}, location: {search: ""}, URLSearchParams};
  vm.createContext(context);
  vm.runInContext(script + "\nthis.api = {parseTimers, formatRemain, accentFor, ringSize};", context);
  return context.api;
}

/** 別の実行環境で作られた配列を、比べられる普通の値にする。 */
const plain = value => JSON.parse(JSON.stringify(value));

test("終わる順に並べ、壊れた値は捨てる", () => {
  const {parseTimers} = load();
  const t = JSON.stringify([{l: "後", e: 2000, s: 60}, {l: "先", e: 1000}, {l: 1, e: 5}, {e: 3}]);
  const timers = parseTimers("?t=" + encodeURIComponent(t));
  assert.deepEqual(plain(timers.map(item => item.label)), ["先", "後"]);
  assert.equal(timers[1].totalMs, 60000);
  assert.equal(timers[0].totalMs, 60000);  // 長さがなければ60秒として描く
  assert.deepEqual(plain(parseTimers("?t=bad")), []);
  assert.deepEqual(plain(parseTimers("")), []);
});

test("残り時間の書き方・色・リングの大きさ", () => {
  const {formatRemain, accentFor, ringSize} = load();
  assert.equal(formatRemain(165000), "02:45");
  assert.equal(formatRemain(3723000), "1:02:03");
  assert.equal(formatRemain(-5), "00:00");
  assert.equal(formatRemain(1), "00:01");
  assert.equal(accentFor(60000), "#22d3ee");
  assert.equal(accentFor(20000), "#f59e0b");
  assert.equal(accentFor(5000), "#ef4444");
  assert.deepEqual([1, 2, 3, 5].map(ringSize), [56, 40, 30, 22]);
});
