const test = require("node:test");
const assert = require("node:assert/strict");

const {shouldRedirect, wheelPixels, install} = require("../../src/argos/services/dashboard/static/wheel_redirect.js");

/** closestで指定セレクターに一致するかを決められる、ブラウザなしの要素を作る。 */
function element(ownWheel = false) {
  return {closest: () => (ownWheel ? {} : null)};
}

/** 表示中かどうかを切り替えられる会話欄を作る。 */
function messagesBox(visible = true) {
  return {scrollTop: 100, clientHeight: 500, getClientRects: () => (visible ? [{}] : [])};
}

/** ホイールイベントを作る。 */
function wheel(overrides = {}) {
  return {deltaY: 120, deltaMode: 0, ctrlKey: false, defaultPrevented: false, target: element(), ...overrides};
}

test("会話欄の外で回したホイールは会話欄へ回す", () => {
  assert.equal(shouldRedirect(wheel(), messagesBox()), true);
});

test("会話欄・入力欄・開いた一覧の上や、拡大操作、横スクロールは付け替えない", () => {
  assert.equal(shouldRedirect(wheel({target: element(true)}), messagesBox()), false);
  assert.equal(shouldRedirect(wheel({ctrlKey: true}), messagesBox()), false);
  assert.equal(shouldRedirect(wheel({deltaY: 0}), messagesBox()), false);
  assert.equal(shouldRedirect(wheel({defaultPrevented: true}), messagesBox()), false);
  assert.equal(shouldRedirect(wheel({target: null}), messagesBox()), true);
});

test("会話欄が表示されていなければ付け替えない", () => {
  assert.equal(shouldRedirect(wheel(), messagesBox(false)), false);
  assert.equal(shouldRedirect(wheel(), null), false);
  assert.equal(shouldRedirect(wheel(), {}), false);
});

test("行単位とページ単位の移動量をピクセルへそろえる", () => {
  assert.equal(wheelPixels(wheel({deltaY: 3, deltaMode: 1}), 500), 120);
  assert.equal(wheelPixels(wheel({deltaY: 1, deltaMode: 2}), 500), 500);
  assert.equal(wheelPixels(wheel({deltaY: -50}), 500), -50);
  assert.equal(wheelPixels(wheel({deltaY: "x"}), 500), 0);
});

test("登録したホイール処理は、既定動作を止めて会話欄をスクロールする", () => {
  let handler, options;
  const scope = {addEventListener: (name, callback, opts) => { assert.equal(name, "wheel"); handler = callback; options = opts; }};
  const box = messagesBox();
  install(scope, box);
  assert.equal(options.passive, false);

  let prevented = false;
  handler(wheel({preventDefault: () => { prevented = true; }}));
  assert.equal(prevented, true);
  assert.equal(box.scrollTop, 220);

  prevented = false;
  handler(wheel({target: element(true), preventDefault: () => { prevented = true; }}));
  assert.equal(prevented, false);
  assert.equal(box.scrollTop, 220);
});
