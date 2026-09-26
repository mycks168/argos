const test = require("node:test");
const assert = require("node:assert/strict");

const {shouldPopup, NoticePopup} = require("../../src/argos/services/dashboard/static/sp_notice_popup.js");

/** 開閉と時計を記録する、ブラウザなしの通知欄を作る。 */
function createPanel(seconds = 8) {
  const panel = {opened: false, log: [], timers: new Map(), nextTimer: 1};
  panel.popup = new NoticePopup({
    seconds,
    open: () => { panel.opened = true; panel.log.push("open"); },
    close: () => { panel.opened = false; panel.log.push("close"); },
    isOpen: () => panel.opened,
    setTimer: (callback, ms) => { const id = panel.nextTimer++; panel.timers.set(id, {callback, ms}); return id; },
    clearTimer: id => panel.timers.delete(id),
  });
  panel.fire = () => { const [id, timer] = panel.timers.entries().next().value; panel.timers.delete(id); timer.callback(); };
  return panel;
}

const notice = (id, source = "Slack", priority = "normal") => ({id, source, priority, title: `通知${id}`});

test("ARGOSの通常の通知は対象外、外部と優先度が高いものは対象", () => {
  assert.equal(shouldPopup(notice("1", "ARGOS", "normal")), false);
  assert.equal(shouldPopup(notice("1", " argos ", "normal")), false);
  assert.equal(shouldPopup(notice("1", "ARGOS", "high")), true);
  assert.equal(shouldPopup(notice("1", "Kokoro", "high")), true);
  assert.equal(shouldPopup(notice("1", "Slack", "normal")), true);
  assert.equal(shouldPopup(notice("1", "", "low")), true);
  assert.equal(shouldPopup({}), true);
  assert.equal(shouldPopup(null), true);
});

test("最初の一覧では開かず、新しい通知が来たら開く", () => {
  const panel = createPanel();
  assert.equal(panel.popup.update([notice("1"), notice("2")]), 0);
  assert.deepEqual(panel.log, []);
  assert.equal(panel.popup.update([notice("1"), notice("2"), notice("3")]), 1);
  assert.deepEqual(panel.log, ["open"]);
  assert.equal(panel.popup.autoOpened, true);
});

test("時間がたつと自動で閉じる", () => {
  const panel = createPanel(8);
  panel.popup.update([]);
  panel.popup.update([notice("1")]);
  assert.equal([...panel.timers.values()][0].ms, 8000);
  panel.fire();
  assert.deepEqual(panel.log, ["open", "close"]);
  assert.equal(panel.popup.autoOpened, false);
});

test("続けて通知が来たら、表示時間を延ばす", () => {
  const panel = createPanel();
  panel.popup.update([]);
  panel.popup.update([notice("1")]);
  panel.popup.update([notice("1"), notice("2")]);
  assert.equal(panel.timers.size, 1);
  assert.deepEqual(panel.log, ["open"]);
});

test("利用者が操作したら、自動では閉じない", () => {
  const panel = createPanel();
  panel.popup.update([]);
  panel.popup.update([notice("1")]);
  panel.popup.takeOver();
  assert.equal(panel.timers.size, 0);
  assert.equal(panel.popup.autoOpened, false);
  assert.equal(panel.opened, true);
  panel.popup.dismiss();
  assert.deepEqual(panel.log, ["open"]);
});

test("利用者が自分で開いている間は、閉じもしないし、時計も動かさない", () => {
  const panel = createPanel();
  panel.opened = true;
  panel.popup.update([]);
  assert.equal(panel.popup.update([notice("1")]), 1);
  assert.deepEqual(panel.log, []);
  assert.equal(panel.timers.size, 0);
});

test("ARGOS自身の通常の通知だけでは開かない", () => {
  const panel = createPanel();
  panel.popup.update([]);
  assert.equal(panel.popup.update([notice("1", "ARGOS")]), 0);
  assert.deepEqual(panel.log, []);
});

test("表示時間が0以下や数値でなければ、自動表示しない", () => {
  for (const seconds of [0, -1, "abc", NaN]) {
    const panel = createPanel(seconds);
    panel.popup.update([]);
    assert.equal(panel.popup.update([notice("1")]), 0);
    assert.deepEqual(panel.log, []);
  }
});

test("同じ通知では二度開かず、閉じたあとの新しい通知では、また開く", () => {
  const panel = createPanel();
  panel.popup.update([]);
  panel.popup.update([notice("1")]);
  panel.fire();
  panel.popup.update([notice("1")]);
  assert.deepEqual(panel.log, ["open", "close"]);
  panel.popup.update([notice("1"), notice("2")]);
  assert.deepEqual(panel.log, ["open", "close", "open"]);
});

test("通知の一覧が空になっても、次に来た通知は新しいものとして扱う", () => {
  const panel = createPanel();
  panel.popup.update([notice("1")]);
  panel.popup.update([]);
  assert.equal(panel.popup.update([notice("1")]), 1);
  assert.equal(panel.popup.update(undefined), 0);
});

test("dismissは自動で開いたときだけ閉じる", () => {
  const panel = createPanel();
  panel.opened = true;
  panel.popup.dismiss();
  assert.deepEqual(panel.log, []);
});

test("既定の時計(setTimeout)でも、エラーなく開いて、時間がたてば閉じる", async () => {
  // ブラウザでは、setTimeoutをメソッドとして呼ぶと失敗する。模擬の時計では見つけられないため、実際の時計で確かめる。
  let opened = false;
  const popup = new NoticePopup({seconds: 0.03, open: () => { opened = true; }, close: () => { opened = false; }, isOpen: () => opened});
  popup.update([]);
  assert.equal(popup.update([notice("1")]), 1);
  assert.equal(opened, true);
  await new Promise(resolve => setTimeout(resolve, 120));
  assert.equal(opened, false);
  popup.update([notice("1"), notice("2")]);
  assert.equal(opened, true);
  popup.takeOver();
  await new Promise(resolve => setTimeout(resolve, 120));
  assert.equal(opened, true);
});
