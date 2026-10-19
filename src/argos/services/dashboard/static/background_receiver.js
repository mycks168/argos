/* 子の完了受信は通常ターンの送信状態と独立させる。 */
(function (root) {
  "use strict";
  class BackgroundReceiver {
    /** 再生と表示は既存のスロット別経路へ委譲する。 */
    constructor(options) {
      this.options = options;
      this.inFlight = new Set();
      this.completed = new Set();
    }
    /** 同じ通知を繰り返し受けても回収・再生を重ねない。 */
    receive(slot, ids) {
      for (const eventId of ids) {
        const key = `${slot.provider}\u0000${slot.name}\u0000${eventId}`;
        if (!/^[a-f0-9]{64}$/.test(eventId) || this.inFlight.has(key) || this.completed.has(key)) continue;
        this.inFlight.add(key);
        this.fetchAnswer(slot, eventId).then(() => this.completed.add(key))
          .catch(error => this.options.onError(error)).finally(() => this.inFlight.delete(key));
      }
    }
    /** 通常入力にフォールバックせず、子の回答取得だけを依頼する。 */
    async fetchAnswer(slot, eventId) {
      const controller = new AbortController();
      const timeoutMs = 120000;
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      try {
        const query = new URLSearchParams({name:slot.name, provider:slot.provider, input_mode:"background"});
        const response = await fetch(`/api/terminal/turn?${query}`, {
          method:"POST", signal:controller.signal, body:eventId,
          headers:{Authorization:`Bearer ${this.options.token}`, "Content-Type":"text/plain", Accept:"text/event-stream, audio/wav"},
        });
        if (!response.ok) throw new Error(`子の回答取得に失敗しました: ${response.status}`);
        await this.consume(response, slot);
      } finally {
        clearTimeout(timer);
      }
    }
    /** SSEの分割位置や複数音声チャンクに依存しない。 */
    async consume(response, slot) {
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let pending = "";
      let didComplete = false;
      const audio = [];
      while (true) {
        const result = await reader.read();
        pending += decoder.decode(result.value || new Uint8Array(), {stream:!result.done}).replace(/\r\n/g, "\n");
        const blocks = pending.split("\n\n");
        pending = result.done ? "" : blocks.pop();
        for (const block of blocks) {
          const line = block.split("\n").find(item => item.startsWith("data: "));
          if (line) didComplete = this.receiveEvent(JSON.parse(line.slice(6)), audio) || didComplete;
        }
        if (result.done) break;
      }
      if (!didComplete) throw new Error("子の回答取得が完了前に切断されました");
      for (const data of audio) this.options.onAudio(slot, data);
    }
    /** 子の回答は録音中なら再生装置の保留キューへ入れる。 */
    receiveEvent(event, audio) {
      if (event.event === "error") throw new Error(event.message);
      if (event.event === "audio" && event.data) audio.push(event.data);
      return event.event === "done";
    }
  }
  root.ArgosBackgroundReceiver = {BackgroundReceiver};
})(globalThis);
