/* 通常・SP・Gridで同じ端末ページを利用する。 */
(function (root) {
  "use strict";
  class ConsoleView {
    constructor(host, token, callbacks = {}) {
      this.host = host;
      this.token = token;
      this.callbacks = callbacks;
      this.key = "";
      this.frame = null;
      this.isEnabled = false;
      this.sequence = 0;
      this.configuration = null;
      this.listener = event => this.receive(event);
      root.addEventListener("message", this.listener);
    }

    async setSlot(slot) {
      const key = `${slot.provider}\u0000${slot.name}`;
      if (key === this.key) return;
      this.key = key;
      const sequence = ++this.sequence;
      this.detach();
      this.callbacks.onChange?.();
      let mode = slot.execution_mode;
      try {
        if (!mode) mode = await this.fetchExecutionMode(slot);
      } catch (error) {
        if (sequence === this.sequence) {
          this.key = "";
          this.host.textContent = error.message;
        }
        return;
      }
      if (sequence !== this.sequence || mode !== "tmux") return;
      this.isEnabled = true;
      const frame = document.createElement("iframe");
      frame.className = "argos-console-frame";
      frame.title = `${slot.name}のターミナル`;
      frame.style.cssText = "width:100%;height:100%;border:0;display:block;min-height:0";
      frame.src = "/static/tmux_terminal.html";
      this.configuration = {type:"argos-console-config", name:slot.name, provider:slot.provider, token:this.token};
      frame.addEventListener("load", () => {
        if (this.frame === frame) this.sendConfiguration();
      });
      this.frame = frame;
      this.host.style.padding = "0";
      this.host.style.overflow = "hidden";
      this.host.replaceChildren(frame);
      this.callbacks.onChange?.();
    }

    /** 能力照会を表示処理から分離し、失敗時の再試行を可能にする。 */
    async fetchExecutionMode(slot) {
      const response = await fetch("/api/terminal/slots", {headers:{Authorization:`Bearer ${this.token}`}});
      if (!response.ok) throw new Error("スロット情報を取得できません");
      const payload = await response.json();
      return payload.slots.find(item => item.name === slot.name && item.provider === slot.provider)?.execution_mode;
    }

    /** 設定や音声操作を別のiframeへ漏らさない。 */
    isOwnMessage(event) {
      return event.origin === location.origin && event.source === this.frame?.contentWindow && event.data?.source === "argos-console";
    }

    receive(event) {
      if (!this.isOwnMessage(event)) return;
      if (event.data.type === "ready") this.sendConfiguration();
      if (event.data.type === "background" && Array.isArray(event.data.ids)) {
        this.callbacks.onBackground?.({name:this.configuration.name, provider:this.configuration.provider}, event.data.ids);
      }
      const callback = {focus:"onFocus", voice:"onVoice", submitted:"onSubmitted", cancel:"onCancel"}[event.data.type];
      this.callbacks[callback]?.();
      if (event.data.type === "ptt") this.forwardPtt(event.data);
    }

    /** 任意のキーイベントを親へ注入させず、PTTだけを転送する。 */
    forwardPtt(data) {
      if (["keydown","keyup"].includes(data.eventType) && /^F(1[3-9]|2[0-4])$/.test(data.code)) {
        this.callbacks.onFocus?.();
        this.host.dispatchEvent(new KeyboardEvent(data.eventType, {key:data.code, code:data.code, bubbles:true}));
      }
    }

    /** iframeの読み込み順序に依存せず、検証済みの相手だけへ設定を渡す。 */
    sendConfiguration() {
      if (this.frame && this.configuration) this.frame.contentWindow.postMessage(this.configuration, location.origin);
    }

    detach() {
      if (this.frame) {
        this.frame.contentWindow.postMessage({type:"argos-console-dispose"}, location.origin);
        this.frame.remove();
      }
      this.frame = null;
      this.configuration = null;
      this.isEnabled = false;
      this.host.style.padding = "";
      this.host.style.overflow = "";
    }

    dispose() {
      ++this.sequence;
      this.detach();
      root.removeEventListener("message", this.listener);
    }
  }
  root.ArgosTmuxEmbed = {ConsoleView};
})(globalThis);
