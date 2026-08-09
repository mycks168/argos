(function registerArgosBrowserAudio(globalScope) {
  "use strict";

  const RIFF_HEADER_SIZE_BYTES = 12;
  const CHUNK_HEADER_SIZE_BYTES = 8;
  const PCM_FORMAT_CODE = 1;
  const PCM_BITS_PER_SAMPLE = 16;

  /** Base64文字列を音声バイト列へ戻す。 */
  function decodeBase64(base64Data) {
    const binary = globalScope.atob(base64Data);
    return Uint8Array.from(binary, character => character.charCodeAt(0));
  }

  /** DataView内のASCII識別子を読む。 */
  function readAscii(view, offset, length) {
    let value = "";
    for (let index = 0; index < length; index += 1) {
      value += String.fromCharCode(view.getUint8(offset + index));
    }
    return value;
  }

  /** PCM WAVのfmtチャンクを検証して形式情報を返す。 */
  function parseFormatChunk(view, offset, sizeBytes) {
    if (sizeBytes < 16) throw new Error("WAVのfmtチャンクが短すぎます");
    const formatCode = view.getUint16(offset, true);
    const channelCount = view.getUint16(offset + 2, true);
    const sampleRate = view.getUint32(offset + 4, true);
    const bitsPerSample = view.getUint16(offset + 14, true);
    if (formatCode !== PCM_FORMAT_CODE || bitsPerSample !== PCM_BITS_PER_SAMPLE) {
      throw new Error(`未対応のWAV形式です: format=${formatCode}, bits=${bitsPerSample}`);
    }
    if (channelCount < 1 || sampleRate < 1) throw new Error("WAVのチャンネル数またはサンプルレートが不正です");
    return {channelCount, sampleRate};
  }

  /** 16bit PCM WAVをブラウザで再生可能なチャンネル配列へ変換する。 */
  function parsePcm16Wav(bytes) {
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    if (bytes.byteLength < RIFF_HEADER_SIZE_BYTES
      || readAscii(view, 0, 4) !== "RIFF"
      || readAscii(view, 8, 4) !== "WAVE") {
      throw new Error("RIFF/WAVEヘッダーが見つかりません");
    }
    let format = null;
    let dataOffset = -1;
    let dataSizeBytes = 0;
    let offset = RIFF_HEADER_SIZE_BYTES;
    while (offset + CHUNK_HEADER_SIZE_BYTES <= bytes.byteLength) {
      const chunkName = readAscii(view, offset, 4);
      const chunkSizeBytes = view.getUint32(offset + 4, true);
      const chunkDataOffset = offset + CHUNK_HEADER_SIZE_BYTES;
      if (chunkDataOffset + chunkSizeBytes > bytes.byteLength) throw new Error(`WAVの${chunkName}チャンクが壊れています`);
      if (chunkName === "fmt ") format = parseFormatChunk(view, chunkDataOffset, chunkSizeBytes);
      if (chunkName === "data") {
        dataOffset = chunkDataOffset;
        dataSizeBytes = chunkSizeBytes;
      }
      offset = chunkDataOffset + chunkSizeBytes + (chunkSizeBytes % 2);
    }
    if (!format || dataOffset < 0) throw new Error("WAVにfmtまたはdataチャンクがありません");
    const frameSizeBytes = format.channelCount * 2;
    if (dataSizeBytes % frameSizeBytes !== 0) throw new Error("WAVのPCMデータ長が不正です");
    const frameCount = dataSizeBytes / frameSizeBytes;
    const channels = Array.from({length: format.channelCount}, () => new Float32Array(frameCount));
    for (let frameIndex = 0; frameIndex < frameCount; frameIndex += 1) {
      for (let channelIndex = 0; channelIndex < format.channelCount; channelIndex += 1) {
        const sampleOffset = dataOffset + (frameIndex * frameSizeBytes) + (channelIndex * 2);
        channels[channelIndex][frameIndex] = view.getInt16(sampleOffset, true) / 32768;
      }
    }
    return {...format, frameCount, channels};
  }

  /** PCM解析結果からAudioBufferを生成する。 */
  function createAudioBuffer(audioContext, wave) {
    const audioBuffer = audioContext.createBuffer(wave.channelCount, wave.frameCount, wave.sampleRate);
    wave.channels.forEach((samples, channelIndex) => audioBuffer.copyToChannel(samples, channelIndex));
    return audioBuffer;
  }

  class AudioPlayer {
    /** キャンセル可能なブラウザ音声キューを初期化する。 */
    constructor(options = {}) {
      this._AudioContext = options.AudioContextClass
        || globalScope.AudioContext
        || globalScope.webkitAudioContext;
      this._onStateChange = options.onStateChange || (() => {});
      this._onError = options.onError || (() => {});
      this._context = null;
      this._generation = 0;
      this._pendingCount = 0;
      this._chain = Promise.resolve();
      this._activePlayback = null;
      this._state = "idle";
    }

    /** 現在の再生世代を返す。 */
    get generation() {
      return this._generation;
    }

    /** ユーザー操作中にAudioContextを起動し、自動再生制限を解除する。 */
    async activate() {
      if (!this._AudioContext) throw new Error("このブラウザはWeb Audio APIに対応していません");
      this._context ||= new this._AudioContext();
      if (this._context.state !== "running" && this._context.state !== "closed") await this._context.resume();
      if (this._context.state !== "running") throw new Error(`音声出力を開始できません: ${this._context.state}`);
    }

    /** 指定世代のBase64音声を順番に再生する。 */
    enqueueBase64(base64Data, generation = this._generation) {
      if (generation !== this._generation) return Promise.resolve(false);
      this._pendingCount += 1;
      this._setState("playing");
      const queued = this._chain
        .catch(() => false)
        .then(() => this._playBase64(base64Data, generation));
      const settled = queued.catch(error => {
        if (generation === this._generation) this._onError(error);
        return false;
      }).finally(() => {
        if (generation !== this._generation) return;
        this._pendingCount = Math.max(0, this._pendingCount - 1);
        if (this._pendingCount === 0) this._setState("idle");
      });
      this._chain = settled;
      return settled;
    }

    /** 再生中と待機中の音声を破棄し、以後の古いチャンクを無効化する。 */
    cancel() {
      this._generation += 1;
      this._pendingCount = 0;
      const activePlayback = this._activePlayback;
      this._activePlayback = null;
      if (activePlayback) {
        try {
          activePlayback.source.stop();
        } catch (error) {
          this._onError(error);
        }
        activePlayback.finish();
      }
      this._chain = Promise.resolve();
      this._setState("idle");
      return this._generation;
    }

    /** キューへ登録済みの音声が終わるまで待つ。 */
    async waitForIdle() {
      await this._chain;
    }

    /** Base64を検証・デコードして一つの音声チャンクを再生する。 */
    async _playBase64(base64Data, generation) {
      if (generation !== this._generation) return false;
      await this.activate();
      const bytes = decodeBase64(base64Data);
      let audioBuffer;
      try {
        audioBuffer = createAudioBuffer(this._context, parsePcm16Wav(bytes));
      } catch (pcmError) {
        try {
          audioBuffer = await this._context.decodeAudioData(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
        } catch (decodeError) {
          throw new Error(`${pcmError.message} / ブラウザの音声デコードにも失敗しました: ${decodeError.message}`);
        }
      }
      if (generation !== this._generation) return false;
      return this._playBuffer(audioBuffer, generation);
    }

    /** AudioBufferSourceNodeを最後まで再生する。 */
    _playBuffer(audioBuffer, generation) {
      return new Promise(resolve => {
        if (generation !== this._generation) {
          resolve(false);
          return;
        }
        const source = this._context.createBufferSource();
        let isFinished = false;
        const finish = () => {
          if (isFinished) return;
          isFinished = true;
          if (this._activePlayback?.source === source) this._activePlayback = null;
          resolve(generation === this._generation);
        };
        source.buffer = audioBuffer;
        source.connect(this._context.destination);
        source.addEventListener("ended", finish, {once: true});
        this._activePlayback = {source, finish};
        source.start();
      });
    }

    /** 状態が変わった場合だけ利用画面へ通知する。 */
    _setState(state) {
      if (state === this._state) return;
      this._state = state;
      this._onStateChange(state);
    }
  }

  const api = {AudioPlayer, parsePcm16Wav};
  globalScope.ArgosBrowserAudio = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
}(typeof globalThis !== "undefined" ? globalThis : window));
