/* 会話の時刻表示と、間が空いたときの区切りを検証する。 */
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const html = fs.readFileSync(path.join(__dirname, "../../src/argos/services/dashboard/static/dashboard.html"), "utf8");

/** 配布HTMLの withTimeLabels を取り出して、評価できる環境を作る。 */
function load() {
  const context = {};
  vm.createContext(context);
  const begin = html.indexOf("function withTimeLabels(");
  const end = html.indexOf("const escapeHtml = value =>", begin);
  vm.runInContext(html.slice(begin, end) + "\nthis.withTimeLabels = withTimeLabels;", context);
  return context.withTimeLabels;
}

/** 今日の指定時刻のISO文字列を作る。 */
function todayAt(hours, minutes) {
  const at = new Date();
  at.setHours(hours, minutes, 0, 0);
  return at.toISOString();
}

test("今日の会話は時刻だけ、30分以上空いたら区切りを付ける", () => {
  const labels = load()([
    {id: "a", created_at: todayAt(9, 5)},
    {id: "b", created_at: todayAt(9, 20)},
    {id: "c", created_at: todayAt(9, 55)},
    {id: "d", created_at: todayAt(12, 10)},
  ]);
  assert.deepEqual(labels.map(item => item.time_label), ["9:05", "9:20", "9:55", "12:10"]);
  assert.deepEqual(labels.map(item => item.gap_label), ["", "", "35分ぶり", "2時間ぶり"]);
});

test("今日より前の会話は日付も出し、時刻のないものは空にする", () => {
  const old = new Date(2026, 10, 21, 22, 10).toISOString();
  const older = new Date(2026, 10, 18, 7, 0).toISOString();
  const labels = load()([{id: "x", created_at: older}, {id: "a", created_at: old}, {id: "b"}]);
  assert.equal(labels[0].time_label, "11/18 7:00");
  assert.equal(labels[1].time_label, "11/21 22:10");
  assert.equal(labels[1].gap_label, "3日ぶり");
  assert.equal(labels[2].time_label, "");
});
