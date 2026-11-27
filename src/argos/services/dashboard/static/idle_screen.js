/* 待機画面の内容とARGOSの会話処理を独立させる。 */
(function (global) {
  "use strict";
  const storageKey = "argos-dashboard-idle-url";
  const activeStatuses = new Set(["booting", "listening", "transcribing", "thinking", "speaking",
    "authenticating", "auth_listening", "followup"]);

  /** ブラウザが直接表示できるURLだけを受け付ける。 */
  function normalizeUrl(value, location = global.location) {
    if (!value || !value.trim()) return "";
    const url = new URL(value.trim(), location.href);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) {
      throw new Error("HTTPまたはHTTPSのURLを指定してください。");
    }
    if (location.protocol === "https:" && url.protocol !== "https:") {
      throw new Error("HTTPSのARGOSにはHTTPSの表示先を指定してください。");
    }
    return url.href;
  }

  /** 保存が禁止されたブラウザでも、共通設定で表示を続ける。 */
  function readOverride() {
    try { return global.localStorage.getItem(storageKey); }
    catch { return null; }
  }

  /** 音声入力・応答中に待機画面へ移らないための共通判定。 */
  function isActiveStatus(code) { return activeStatuses.has(code); }

  class Screen {
    /** 会話画面を保持したまま、表示用フレームを重ねる。 */
    constructor(options) {
      this.options = options;
      this.root = options.root || document.createElement("div");
      this.root.classList.add("argos-idle-screen");
      this.root.hidden = true;
      this.root.tabIndex = -1;
      this.root.setAttribute("aria-label", "待機画面。操作するとARGOSへ戻ります");
      this.root.innerHTML = '<div class="argos-idle-fallback"><time></time><p>操作するとARGOSへ戻ります</p></div>';
      this.clock = this.root.querySelector("time");
      if (!options.root) document.body.appendChild(this.root);
      this.active = false;
      this.sequence = null;
      this.noticeId = null;
      this.timer = null;
      this.loadTimer = null;
      this.clockTimer = null;
      this.frame = null;
      this.state = null;
      ["pointerdown", "touchstart", "keydown", "wheel"].forEach(name => {
        global.addEventListener(name, () => this.reset(), {passive: true});
      });
      global.addEventListener("storage", event => {
        if (event.key === storageKey || event.key === null) this.reset();
      });
      this.root.addEventListener("click", () => this.reset());
      this.reset();
    }

    /** 定期更新そのものではなく、活動の発生で会話画面へ戻す。 */
    update(state) {
      this.state = state;
      const activity = this.readActivity(state);
      if (this.hasNewActivity(activity) || isActiveStatus(state.status?.code) || this.isBusy()) this.reset();
      this.sequence = activity.sequence;
      this.noticeId = activity.noticeId;
    }

    /** 再描画のたびに変わる時刻と、利用者に知らせる活動を区別する。 */
    readActivity(state) {
      return {
        sequence: state.display_activity?.sequence ?? 0,
        noticeId: state.notifications?.at(-1)?.id || "",
      };
    }

    /** 初回の履歴読み込みを新着通知として扱わない。 */
    hasNewActivity(activity) {
      return (this.sequence !== null && activity.sequence !== this.sequence)
        || (this.noticeId !== null && activity.noticeId && activity.noticeId !== this.noticeId);
    }

    /** 実行中の会話や通知を待機画面で隠さない。 */
    isBusy() {
      return Boolean(this.options.isBusy?.() || this.state?.center_alert?.active);
    }

    /** 表示専用にし、クリックやキー入力はARGOSへの復帰に使う。 */
    show() {
      if (this.active || !(this.options.timeoutMs > 0)) return;
      if (this.isBusy()) { this.reset(); return; }
      this.active = true;
      this.root.hidden = false;
      this.root.setAttribute("aria-hidden", "false");
      this.previousFocus = document.activeElement;
      this.root.focus({preventScroll: true});
      const raw = readOverride() ?? this.options.url;
      this.root.classList.toggle("has-page", Boolean(raw));
      if (!raw) return;
      this.updateClock();
      this.clockTimer = global.setInterval(() => this.updateClock(), 1000);
      try { this.loadFrame(normalizeUrl(raw)); }
      catch { /* 無効な共通設定でも復帰操作と時計を利用できるようにする。 */ }
    }

    /** 読み込み待ちの間は時計を表示し、親の認証情報を送らない。 */
    loadFrame(url) {
      const frame = document.createElement("iframe");
      frame.title = "待機中のダッシュボード";
      frame.setAttribute("sandbox", "allow-scripts allow-same-origin");
      frame.setAttribute("referrerpolicy", "no-referrer");
      frame.setAttribute("inert", "");
      frame.tabIndex = -1;
      frame.hidden = true;
      frame.addEventListener("load", () => {
        if (this.frame !== frame) return;
        global.clearTimeout(this.loadTimer);
        frame.hidden = false;
      });
      frame.addEventListener("error", () => { if (this.frame === frame) this.removeFrame(); });
      this.frame = frame;
      this.loadTimer = global.setTimeout(() => this.removeFrame(), 15000);
      frame.src = url;
      this.root.appendChild(frame);
    }

    /** 外部アプリが表示されていない間の最小表示を用意する。 */
    updateClock() {
      this.clock.textContent = new Date().toLocaleString("ja-JP", {
        month: "long", day: "numeric", weekday: "short", hour: "2-digit", minute: "2-digit", second: "2-digit",
      });
    }

    /** 非表示中の更新・通信を止めるためフレームを破棄する。 */
    removeFrame() {
      global.clearTimeout(this.loadTimer);
      this.frame?.remove();
      this.frame = null;
    }

    /** 操作直後の入力先を復元し、無操作時間を数え直す。 */
    reset() {
      global.clearTimeout(this.timer);
      global.clearInterval(this.clockTimer);
      this.removeFrame();
      this.root.hidden = true;
      this.root.setAttribute("aria-hidden", "true");
      if (this.active && document.activeElement === this.root) this.previousFocus?.focus({preventScroll: true});
      this.active = false;
      if (this.options.timeoutMs > 0) this.timer = global.setTimeout(() => this.show(), this.options.timeoutMs);
    }
  }

  /** 端末ごとの設定は本体設定へ書き込まず、同じブラウザに保存する。 */
  function mountSettings(root, options) {
    root.classList.add("argos-idle-settings");
    root.innerHTML = '<label>このブラウザの待機画面URL<input type="url" aria-label="このブラウザの待機画面URL" placeholder="https://dashboard.example/" autocomplete="off"></label>'
      + '<p>空欄で保存すると黒画面になります。共通設定を使う場合は「共通設定に戻す」を押してください。</p>'
      + '<div><button type="button" data-action="save">このブラウザに保存</button> '
      + '<button type="button" data-action="reset">共通設定に戻す</button></div><p role="status"></p>';
    const input = root.querySelector("input");
    const status = root.querySelector('[role="status"]');
    input.value = readOverride() ?? options.url;
    const save = useDefault => {
      try {
        if (useDefault) global.localStorage.removeItem(storageKey);
        else global.localStorage.setItem(storageKey, normalizeUrl(input.value));
        input.value = readOverride() ?? options.url;
        status.textContent = useDefault ? "共通設定を使います。" : "このブラウザに保存しました。";
      } catch (error) { status.textContent = `保存できません: ${error.message}`; }
    };
    root.querySelector('[data-action="save"]').addEventListener("click", () => save(false));
    root.querySelector('[data-action="reset"]').addEventListener("click", () => save(true));
  }

  const api = {Screen, mountSettings, normalizeUrl, isActiveStatus};
  global.ArgosIdleScreen = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
