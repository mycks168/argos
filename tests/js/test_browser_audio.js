const test = require("node:test");
const assert = require("node:assert/strict");

globalThis.atob = value => Buffer.from(value, "base64").toString("binary");
const {AudioPlayer, parsePcm16Wav} = require("../../src/argos/services/dashboard/static/browser_audio.js");

/** 最小の16bit PCM WAVを作成する。 */
function createPcmWav(samples, sampleRate = 48000) {
  const bytes = Buffer.alloc(44 + (samples.length * 2));
  bytes.write("RIFF", 0);
  bytes.writeUInt32LE(bytes.length - 8, 4);
  bytes.write("WAVEfmt ", 8);
  bytes.writeUInt32LE(16, 16);
  bytes.writeUInt16LE(1, 20);
  bytes.writeUInt16LE(1, 22);
  bytes.writeUInt32LE(sampleRate, 24);
  bytes.writeUInt32LE(sampleRate * 2, 28);
  bytes.writeUInt16LE(2, 32);
  bytes.writeUInt16LE(16, 34);
  bytes.write("data", 36);
  bytes.writeUInt32LE(samples.length * 2, 40);
  samples.forEach((sample, index) => bytes.writeInt16LE(sample, 44 + (index * 2)));
  return bytes;
}

class FakeSource {
  /** テスト用の音声ソースを初期化する。 */
  constructor() {
    this.listeners = new Map();
    this.didStart = false;
    this.didStop = false;
  }

  /** 出力先への接続を受け付ける。 */
  connect() {}

  /** イベントハンドラーを保存する。 */
  addEventListener(name, listener) {
    this.listeners.set(name, listener);
  }

  /** 再生開始を記録する。 */
  start() {
    this.didStart = true;
  }

  /** 停止とended通知を再現する。 */
  stop() {
    this.didStop = true;
    this.listeners.get("ended")?.();
  }
}

class FakeAudioContext {
  /** テスト用AudioContextを初期化する。 */
  constructor() {
    this.state = "running";
    this.destination = {};
    this.sources = [];
  }

  /** AudioBuffer相当の格納先を作る。 */
  createBuffer(channelCount, frameCount, sampleRate) {
    const channels = Array.from({length: channelCount}, () => new Float32Array(frameCount));
    return {
      channelCount,
      frameCount,
      sampleRate,
      copyToChannel: (samples, index) => channels[index].set(samples),
    };
  }

  /** 停止状態からの復帰を再現する。 */
  async resume() {
    this.state = "running";
  }

  /** 再生ノードを生成して検証用に保持する。 */
  createBufferSource() {
    const source = new FakeSource();
    this.sources.push(source);
    return source;
  }
}

test("PCM WAVの形式とサンプルを解析できる", () => {
  const wav = createPcmWav([-32768, 0, 32767]);
  const parsed = parsePcm16Wav(new Uint8Array(wav));

  assert.equal(parsed.sampleRate, 48000);
  assert.equal(parsed.channelCount, 1);
  assert.equal(parsed.frameCount, 3);
  assert.equal(parsed.channels[0][0], -1);
  assert.ok(parsed.channels[0][2] > 0.999);
});

test("キャンセル後に古い世代の待機音声を再生しない", async () => {
  const states = [];
  const player = new AudioPlayer({
    AudioContextClass: FakeAudioContext,
    onStateChange: state => states.push(state),
  });
  const base64 = createPcmWav([0, 100]).toString("base64");
  const generation = player.generation;
  const first = player.enqueueBase64(base64, generation);
  const second = player.enqueueBase64(base64, generation);
  await new Promise(resolve => setImmediate(resolve));

  player.cancel();
  await Promise.all([first, second]);

  assert.equal(player._context.sources.length, 1);
  assert.equal(player._context.sources[0].didStop, true);
  assert.deepEqual(states, ["playing", "idle"]);
});

test("異なる世代の音声チャンクを受け付けない", async () => {
  const player = new AudioPlayer({AudioContextClass: FakeAudioContext});
  const previousGeneration = player.generation;
  player.cancel();

  const didPlay = await player.enqueueBase64(createPcmWav([0]).toString("base64"), previousGeneration);

  assert.equal(didPlay, false);
  assert.equal(player._context, null);
});

test("URL音声を指定したHTTPキャッシュ設定で取得して再生する", async () => {
  const wav = createPcmWav([0, 100]);
  const requests = [];
  const player = new AudioPlayer({
    AudioContextClass: FakeAudioContext,
    fetch: async (url, options) => {
      requests.push({url, options});
      return {
        ok: true,
        status: 200,
        arrayBuffer: async () => wav.buffer.slice(wav.byteOffset, wav.byteOffset + wav.byteLength),
      };
    },
  });
  const playback = player.enqueueUrl(
    "/api/terminal/progress-audio/hash.wav",
    {cache: "force-cache", headers: {Authorization: "Bearer test"}},
  );
  await new Promise(resolve => setImmediate(resolve));

  assert.equal(requests.length, 1);
  assert.equal(requests[0].options.cache, "force-cache");
  assert.equal(requests[0].options.headers.Authorization, "Bearer test");
  assert.equal(player._context.sources[0].didStart, true);

  player.cancel();
  await playback;
});

test("Safariのinterrupted状態から音声出力を復帰する", async () => {
  class InterruptedAudioContext extends FakeAudioContext {
    /** Safariで割り込みを受けた状態を再現する。 */
    constructor() {
      super();
      this.state = "interrupted";
      this.didResume = false;
    }

    /** resume呼び出しを記録してrunningへ戻す。 */
    async resume() {
      this.didResume = true;
      this.state = "running";
    }
  }
  const player = new AudioPlayer({AudioContextClass: InterruptedAudioContext});

  await player.activate();

  assert.equal(player._context.didResume, true);
  assert.equal(player._context.state, "running");
});
