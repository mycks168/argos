/* 別スロットの回答を鳴らさず、取消済み音声も復活させない。 */
const {test} = require("node:test");
const assert = require("node:assert/strict");
require("../../src/argos/services/dashboard/static/slot_audio.js");

/** 終了を明示制御できる再生装置で切替競合を再現する。 */
function setup() {
  const played = [];
  let finish;
  const player = {
    generation:0,
    cancel() {this.generation += 1; finish?.();},
    enqueueBase64(data) {played.push(data); return new Promise(resolve => {finish = resolve;});},
    enqueueUrl(url) {return this.enqueueBase64(url);},
  };
  return {audio:new ArgosSlotAudio.SlotAudio(player), played, end:() => finish()};
}

/** キューの非同期継続を進める。 */
async function flush() {await new Promise(resolve => setImmediate(resolve));}

test("非選択スロットの音声は戻った時だけ鳴る", async () => {
  const {audio, played, end} = setup();
  audio.select("A");
  audio.enqueue("B", {data:"Bの回答"}, 0);
  assert.deepEqual(played, []);
  audio.select("B");
  assert.deepEqual(played, ["Bの回答"]);
  end(); await flush();
  audio.select("A"); audio.select("B");
  assert.equal(played.length, 1);
});

test("途中切替で止めた文は戻った時に再開する", async () => {
  const {audio, played, end} = setup();
  audio.select("A");
  audio.enqueue("A", {data:"Aの回答"}, 0);
  audio.enqueue("B", {url:"Bの待機音声", options:{}}, 0);
  audio.select("B"); await flush();
  assert.deepEqual(played, ["Aの回答", "Bの待機音声"]);
  end(); await flush();
  audio.select("A");
  assert.equal(played.at(-1), "Aの回答");
  end(); await flush();
});

test("停止すると後着した古い回答も再生せず、新しい回答は受け付ける", async () => {
  const {audio, played, end} = setup();
  audio.select("A"); audio.enqueue("A", {data:"途中"}, 0);
  const generation = audio.cancel();
  audio.enqueue("A", {data:"古い続き"}, 0);
  await flush();
  audio.select("A"); audio.enqueue("A", {data:"新しい回答"}, generation);
  assert.deepEqual(played, ["途中", "新しい回答"]);
  end(); await flush();
});

test("録音中に到着した子の回答は保留し、録音終了後に一つずつ鳴らす", async () => {
  const {audio, played, end} = setup();
  audio.select("A"); audio.pause();
  audio.enqueue("A", {data:"子1"}, audio.generation);
  audio.enqueue("A", {data:"子2"}, audio.generation);
  assert.deepEqual(played, []);
  audio.resume();
  assert.deepEqual(played, ["子1"]);
  end(); await flush();
  assert.deepEqual(played, ["子1", "子2"]);
  end(); await flush();
});
