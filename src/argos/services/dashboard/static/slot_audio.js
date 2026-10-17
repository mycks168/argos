/* 選択変更と読み上げ取消を分離し、別スロットの回答を失わない。 */
(function (root) {
  "use strict";
  class SlotAudio {
    /** 再生装置は画面側から受け取り、音声の所有スロットだけ管理する。 */
    constructor(player) {
      this.player = player;
      this.key = "";
      this.generation = 0;
      this.queues = new Map();
      this.revision = 0;
      this.isDraining = false;
    }
    /** 非選択スロットの音声は戻るまで保持する。 */
    select(key) {
      if (key === this.key) return;
      this.revision += 1;
      this.key = key;
      this.player.cancel();
      this.drain();
    }
    /** 古いターンの取消済み音声を再び登録させない。 */
    enqueue(key, item, generation) {
      if (generation !== this.generation) return;
      const queue = this.queues.get(key) || [];
      queue.push(item);
      this.queues.set(key, queue);
      this.drain();
    }
    /** PTTによる停止は以降届く同一世代のチャンクも無効にする。 */
    cancel() {
      this.generation += 1;
      this.revision += 1;
      this.queues.clear();
      this.player.cancel();
      return this.generation;
    }
    /** 切替中に止めた文はキューに残し、戻った時に文頭から再生する。 */
    async drain() {
      if (this.isDraining) return;
      this.isDraining = true;
      try {
        let queue = this.queues.get(this.key);
        while (queue?.length) {
          const revision = this.revision;
          const item = queue[0];
          if (item.url) await this.player.enqueueUrl(item.url, item.options, this.player.generation);
          else await this.player.enqueueBase64(item.data, this.player.generation);
          if (revision === this.revision) queue.shift();
          queue = this.queues.get(this.key);
        }
      } finally {
        this.isDraining = false;
      }
    }
  }
  root.ArgosSlotAudio = {SlotAudio};
})(globalThis);
