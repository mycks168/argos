(function initializeSpNoticePopup(globalScope) {
  "use strict";

  /**
   * 通知を自動で見せる対象か判定する。
   * ARGOS自身の通常の通知（音声入力の開始やミュートなど）は頻繁に出るので対象外にする。
   * 外部から来た通知と、優先度が高いもの（内部エラーを含む）は対象にする。
   */
  function shouldPopup(notice) {
    const source = String(notice?.source ?? "").trim().toUpperCase();
    const priority = String(notice?.priority ?? "normal").trim().toLowerCase();
    return !(source === "ARGOS" && priority !== "high");
  }

  /**
   * SP表示で、新しい通知が来たときに通知欄を一時的に開き、時間がたったら閉じる。
   *
   * 開閉の操作(open/close/isOpen)と時計(setTimer/clearTimer)は外から渡すので、
   * ブラウザなしでも動作を検証できる。
   */
  class NoticePopup {
    constructor({seconds, open, close, isOpen, setTimer = setTimeout, clearTimer = clearTimeout}) {
      this.seconds = Number(seconds);
      this._open = open;
      this._close = close;
      this._isOpen = isOpen;
      // setTimeoutなどをメソッドとして呼ぶと、ブラウザが「Illegal invocation」で失敗する。
      // 素の関数として呼べるように包む。
      this._setTimer = (callback, ms) => setTimer(callback, ms);
      this._clearTimer = id => clearTimer(id);
      this._known = null;
      this._timer = null;
      this._autoOpened = false;
    }

    /** 自動表示が有効か。0以下や数値でない設定は無効として扱う。 */
    get enabled() {
      return Number.isFinite(this.seconds) && this.seconds > 0;
    }

    /** 自動で開いている最中ならtrueを返す。 */
    get autoOpened() {
      return this._autoOpened;
    }

    /**
     * 最新の通知一覧を受け取り、新しく来た対象の通知があれば通知欄を見せる。
     * 最初の一覧は「すでにある通知」として覚えるだけで、見せない。
     * 対象の新しい通知の件数を返す。
     */
    update(notifications) {
      const list = Array.isArray(notifications) ? notifications : [];
      const ids = new Set(list.map(item => item.id));
      if (this._known === null) {
        this._known = ids;
        return 0;
      }
      const fresh = list.filter(item => !this._known.has(item.id) && shouldPopup(item));
      // 消えた通知のidは忘れて、覚える数が増え続けないようにする。
      this._known = ids;
      if (!fresh.length || !this.enabled) return 0;
      this._show();
      return fresh.length;
    }

    /** 利用者が通知欄を操作したら、自動で閉じるのをやめて、開いたままにする。 */
    takeOver() {
      this._cancelTimer();
      this._autoOpened = false;
    }

    /** 時間切れの前に、自動表示を取り消して閉じる。 */
    dismiss() {
      this._cancelTimer();
      if (this._autoOpened) {
        this._autoOpened = false;
        this._close();
      }
    }

    _show() {
      if (this._isOpen() && !this._autoOpened) {
        // 利用者が自分で開いている間は、そのまま見せ続ける。
        return;
      }
      if (!this._isOpen()) {
        this._open();
        this._autoOpened = true;
      }
      // 続けて通知が来たら、表示時間を延ばす。
      this._cancelTimer();
      this._timer = this._setTimer(() => this.dismiss(), this.seconds * 1000);
    }

    _cancelTimer() {
      if (this._timer !== null) {
        this._clearTimer(this._timer);
        this._timer = null;
      }
    }
  }

  const api = {shouldPopup, NoticePopup};
  globalScope.ArgosSpNoticePopup = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
