/* 実際に配布するHTMLの録音開始と状態選択を回帰検証する。 */
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const html = fs.readFileSync(path.join(__dirname, "../../src/argos/services/dashboard/static/dashboard.html"), "utf8");

/** 配布HTMLの状態選択式を、同じ評価環境で検証する。 */
function displayCode(inFlightTextSlots) {
  const context = {browserStatus:null, currentAgentSlotBusy:false, state:{status:{code:"thinking", label:"考え中"}},
    inFlightTextSlots, currentAgentSlotKey:"current", isSlotTurnInFlight:() => false};
  vm.createContext(context);
  const begin = html.indexOf("const displayStatus = browserStatus");
  const end = html.indexOf("status.dataset.code", begin);
  vm.runInContext(html.slice(begin, end), context);
  return vm.runInContext("displayStatus.code", context);
}

test("録音非対応ならエラーだけを出し、再生音声を取り消さない", async () => {
  let cancelled = 0;
  let errorMessage = "";
  const context = {currentAgentSlotKey:"claude\u0000試験", navigator:{}, window:{},
    slotAudio:{cancel() {cancelled += 1;}}, setTextComposerError(message) {errorMessage = message;}};
  vm.createContext(context);
  const begin = html.indexOf("async function startVoiceRecording(");
  const end = html.indexOf("function stopVoiceStream(", begin);
  vm.runInContext(html.slice(begin, end), context);
  await vm.runInContext("startVoiceRecording()", context);
  assert.equal(cancelled, 0);
  assert.match(errorMessage, /音声録音に対応していない/);
});

test("ブラウザ外で開始した処理のサーバーthinkingを隠さない", () => {
  assert.equal(displayCode(new Map()), "thinking");
});

test("別ブラウザスロットの実行中を現在スロットのthinkingへ流用しない", () => {
  assert.equal(displayCode(new Map([["other", 1]])), "ready");
});

/** 配布HTMLの停止・送信・状態同期を録音済みデータで実行する。 */
function recordingContext(hasAudio = true) {
  let stopped;
  let finishResponse;
  const inFlight = new Set();
  const context = {Blob, URLSearchParams, console, dashboardToken:"", pttReleasePending:false,
    voiceState:"recording", voicePlaybackState:"idle", voiceOperationSequence:1,
    voiceRecorderOperation:1, voiceRecorderPlaybackGeneration:0,
    voiceSlotKey:"claude\u0000試験", currentAgentSlotKey:"claude\u0000試験",
    voiceChunks:hasAudio ? [new Blob(["audio"])] : [],
    voiceRecorder:{state:"recording", mimeType:"audio/webm",
      addEventListener(_name, callback) {stopped = callback;}, stop() {this.state = "inactive"; stopped();}},
    stopVoiceStream() {}, setTextComposerError() {},
    setVoiceState(state) {context.voiceState = state;},
    beginSlotTurn:key => inFlight.add(key), finishSlotTurn:key => inFlight.delete(key),
    isSlotTurnInFlight:key => inFlight.has(key),
    fetch:async () => ({ok:true, body:{}}),
    consumeVoiceTurnEvents:() => new Promise(resolve => {finishResponse = resolve;}),
  };
  vm.createContext(context);
  const stopBegin = html.indexOf("async function stopVoiceRecording(");
  const stopEnd = html.indexOf("async function consumeVoiceTurnEvents(", stopBegin);
  const syncBegin = html.indexOf("function syncVoiceState(");
  const syncEnd = html.indexOf("function cancelBrowserAudio(", syncBegin);
  vm.runInContext(html.slice(stopBegin, stopEnd) + html.slice(syncBegin, syncEnd), context);
  return {context, finish:() => finishResponse()};
}

test("録音停止で処理中へ移り、回答再生と完了後も録音中に戻らない", async () => {
  const {context, finish} = recordingContext();
  const pending = vm.runInContext("stopVoiceRecording()", context);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(context.voiceRecorder, null);
  assert.equal(context.voiceState, "processing");
  context.voicePlaybackState = "playing";
  vm.runInContext("syncVoiceState()", context);
  assert.equal(context.voiceState, "playing");
  finish(); await pending;
  context.voicePlaybackState = "idle";
  vm.runInContext("syncVoiceState()", context);
  assert.equal(context.voiceState, "idle");
});

test("録音データが空でも録音中を解除する", async () => {
  const {context} = recordingContext(false);
  await vm.runInContext("stopVoiceRecording()", context);
  assert.equal(context.voiceState, "idle");
});
