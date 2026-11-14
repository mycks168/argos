/* 子の回答が一つずつ再生され、次の入力を塞がないことを検証する。 */
const {test} = require("node:test");
const assert = require("node:assert/strict");
require("../../src/argos/services/dashboard/static/background_receiver.js");
const slot = {name:"作業", provider:"claude"};
const eventId = "a".repeat(64);

/** チャンクの境界を任意に分けたSSEを生成する。 */
function responseFor(events) {
  const body = events.map(event => `data: ${JSON.stringify(event)}\n\n`).join("");
  return {ok:true, body:new ReadableStream({start(controller) {
    for (const chunk of [body.slice(0, 7), body.slice(7)]) controller.enqueue(new TextEncoder().encode(chunk));
    controller.close();
  }})};
}

test("子の音声を回答単位でまとめ、同じ完了を二重取得しない", async () => {
  const originalFetch = globalThis.fetch;
  const played = [];
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({url, options});
    return responseFor([{event:"audio", data:"part1"}, {event:"audio", data:"part2"}, {event:"done"}]);
  };
  try {
    const receiver = new ArgosBackgroundReceiver.BackgroundReceiver({token:"secret", onAudio:(_slot, data) => played.push(data), onError:assert.fail});
    receiver.receive(slot, ["invalid", eventId, eventId]);
    await new Promise(resolve => setImmediate(resolve));
    receiver.receive(slot, [eventId]);
    assert.equal(requests.length, 1);
    assert.match(requests[0].url, /input_mode=background/);
    assert.equal(requests[0].options.body, eventId);
    assert.equal(requests[0].options.headers.Authorization, "Bearer secret");
    assert.deepEqual(played, ["part1", "part2"]);
  } finally {globalThis.fetch = originalFetch;}
});

test("完了前の切断やサーバーエラーでは部分音声を読まない", async () => {
  const receiver = new ArgosBackgroundReceiver.BackgroundReceiver({onAudio:assert.fail});
  await assert.rejects(receiver.consume(responseFor([{event:"audio", data:"partial"}]), slot), /完了前/);
  await assert.rejects(receiver.consume(responseFor([{event:"error", message:"取得失敗"}]), slot), /取得失敗/);
});

test("HTTP失敗は通知し、次の完了照会で再試行できる", async () => {
  const originalFetch = globalThis.fetch;
  const errors = [];
  globalThis.fetch = async () => ({ok:false, status:503});
  try {
    const receiver = new ArgosBackgroundReceiver.BackgroundReceiver({token:"secret", onError:error => errors.push(error.message)});
    receiver.receive(slot, [eventId]);
    await new Promise(resolve => setImmediate(resolve));
    assert.match(errors[0], /503/);
    receiver.receive(slot, [eventId]);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(errors.length, 2);
  } finally {globalThis.fetch = originalFetch;}
});
