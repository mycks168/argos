// 画面のどこでホイールを回しても、中央の会話欄をスクロールする。
// USBメディアノブの下のダイアルはマウスホイールとして届き、ポインタの位置に関係なく会話を送りたいため。
(function (globalScope) {
  "use strict";

  // 自分でスクロールさせたい場所。ここでは通常のホイール動作を残す。
  const OWN_WHEEL_SELECTOR = [
    "#messages",
    "textarea",
    "select",
    '#slot-menu[data-open="true"]',
    ".settings-modal",
    ".slot-overlay",
  ].join(",");
  const LINE_PIXELS = 40;

  /** ホイールの移動量をピクセルへそろえる。 */
  function wheelPixels(event, pageHeight) {
    const delta = Number(event.deltaY) || 0;
    if (event.deltaMode === 1) return delta * LINE_PIXELS;
    if (event.deltaMode === 2) return delta * (pageHeight || 0);
    return delta;
  }

  /** 会話欄へ回すべきホイールか判定する。 */
  function shouldRedirect(event, messages) {
    if (!messages || event.defaultPrevented || event.ctrlKey) return false;
    if (!(Number(event.deltaY) || 0)) return false;
    // 会話欄が画面に出ていない（オーバーレイ表示中など）ときは触らない。
    if (!messages.getClientRects || messages.getClientRects().length === 0) return false;
    const target = event.target;
    if (target && typeof target.closest === "function" && target.closest(OWN_WHEEL_SELECTOR)) return false;
    return true;
  }

  /** windowへホイールの付け替えを登録する。 */
  function install(scope, messages) {
    scope.addEventListener("wheel", event => {
      if (!shouldRedirect(event, messages)) return;
      event.preventDefault();
      messages.scrollTop += wheelPixels(event, messages.clientHeight);
    }, {passive: false});
  }

  const api = {shouldRedirect, wheelPixels, install, OWN_WHEEL_SELECTOR};
  globalScope.ArgosWheelRedirect = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
