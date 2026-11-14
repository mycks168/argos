/* 端末のバイト列と会話の読み上げ経路を混同しない。 */
(() => {
  "use strict";
  const endpoint = "/api/terminal/console";
  const status = document.getElementById("status");
  const holder = document.getElementById("terminal");
  const retryDelayMs = 1500;
  let terminal = null;
  let fit = null;
  let configuration = null;
  let connectionId = "";
  let offset = 0;
  let isDisposed = false;
  let isSuspended = false;
  let isConnecting = false;
  let shouldReconnect = false;
  let readController = null;
  let writeQueue = Promise.resolve();
  let previousSize = "";
  let resizeTimer = null;

  const notify = (type, extra = {}) => parent.postMessage({source:"argos-console", type, ...extra}, location.origin);

  /** 初期化に失敗しても操作ボタンと再試行の経路を残す。 */
  function initializeTerminal() {
    if (terminal) return;
    try {
      terminal = new Terminal({fontSize:14, cursorBlink:true, scrollback:5000,
        theme:{background:"#101820", foreground:"#edf3f7"}});
      fit = new FitAddon.FitAddon();
      terminal.loadAddon(fit);
      terminal.open(holder);
      terminal.onData(sendInput);
      terminal.attachCustomKeyEventHandler(forwardPtt);
    } catch (error) {
      terminal?.dispose();
      terminal = null;
      fit = null;
      throw new Error(`端末初期化エラー: ${error.message}。再接続で再試行できます`);
    }
  }

  /** PTTだけを親画面へ渡し、端末の通常入力を維持する。 */
  function forwardPtt(event) {
    if (event.code === "Escape" && event.type === "keydown") notify("cancel");
    if (!/^F(1[3-9]|2[0-4])$/.test(event.code)) return true;
    if (!event.repeat) notify("ptt", {eventType:event.type, code:event.code});
    event.preventDefault();
    return false;
  }
  const encode = value => {
    let binary = "";
    for (const byte of new TextEncoder().encode(value)) binary += String.fromCharCode(byte);
    return btoa(binary);
  };

  async function request(payload, isRead = false) {
    const controller = new AbortController();
    if (isRead) readController = controller;
    const timer = setTimeout(() => controller.abort(), payload.action === "input" ? 70000 : (isRead ? 25000 : 20000));
    const headers = {Authorization:`Bearer ${configuration.token}`};
    const options = {headers, signal:controller.signal};
    let url = endpoint;
    if (isRead) {
      url += `?${new URLSearchParams({connection_id:payload.connection_id, offset:payload.offset})}`;
    } else {
      options.method = "POST";
      headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(payload);
    }
    try {
      const response = await fetch(url, options);
      const result = await response.json();
      if (!response.ok) {
        const error = new Error(result.error || `端末接続エラー ${response.status}`);
        error.status = response.status;
        throw error;
      }
      return result;
    } finally {
      clearTimeout(timer);
    }
  }

  function measure() {
    if (!terminal || holder.clientWidth < 40 || holder.clientHeight < 40) return null;
    fit.fit();
    return {columns:terminal.cols, rows:terminal.rows};
  }

  function resize() {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      const size = measure();
      const signature = JSON.stringify(size);
      if (!size || !connectionId || signature === previousSize) return;
      previousSize = signature;
      writeQueue = writeQueue.then(() => request({action:"resize", connection_id:connectionId, ...size}))
        .catch(error => { status.textContent = error.message; });
    }, 80);
  }

  function closeConnection() {
    if (!connectionId || !configuration) return;
    const closingId = connectionId;
    connectionId = "";
    fetch(endpoint, {
      method:"POST", keepalive:true,
      headers:{Authorization:`Bearer ${configuration.token}`, "Content-Type":"application/json"},
      body:JSON.stringify({action:"close", connection_id:closingId}),
    }).catch(() => { /* 切断時の未達はサーバー側のリース期限で回収する。 */ });
  }

  /** 遅着したopenを回収し、離脱後にattachを残さない。 */
  async function openConnection() {
    if (connectionId) return;
    const size = measure() || {columns:80, rows:24};
    const opened = await request({action:"open", name:configuration.name, provider:configuration.provider, ...size});
    connectionId = opened.connection_id;
    if (isDisposed || isSuspended || shouldReconnect) {
      closeConnection();
      return;
    }
    offset = 0;
    previousSize = JSON.stringify(size);
    terminal.reset();
    resize();
  }

  /** 再接続前の遅い応答を画面に混ぜない。 */
  async function readOutput() {
    const output = await request({connection_id:connectionId, offset}, true);
    if (isDisposed || isSuspended || shouldReconnect) return;
    const bytes = Uint8Array.from(atob(output.data), character => character.charCodeAt(0));
    if (bytes.length) await new Promise(resolve => terminal.write(bytes, resolve));
    offset = output.offset;
    status.textContent = "接続済み · 端末内のSpaceは文字入力";
    if (output.has_pending_turn) notify("submitted");
    if (output.background_ids?.length) notify("background", {ids:output.background_ids});
    if (output.is_closed) {
      closeConnection();
      status.textContent = "端末へ再接続しています";
    }
  }

  /** 通信失敗の自動再試行と明示的な再接続を区別する。 */
  async function connect() {
    while (!isDisposed && !isSuspended) {
      shouldReconnect = false;
      try {
        await openConnection();
        if (connectionId) await readOutput();
      } catch (error) {
        if (isDisposed || isSuspended) return;
        if (shouldReconnect) continue;
        status.textContent = error.message;
        if ([400,404,409].includes(error.status)) closeConnection();
        await new Promise(resolve => setTimeout(resolve, retryDelayMs));
      }
    }
  }

  /** 復帰と手動操作が重なっても接続ループを二重起動しない。 */
  async function startConnection() {
    if (!configuration || isDisposed || isSuspended || isConnecting) return;
    isConnecting = true;
    shouldReconnect = false;
    try {
      initializeTerminal();
      await connect();
    } catch (error) {
      status.textContent = error.message;
    } finally {
      isConnecting = false;
      if (shouldReconnect && !isDisposed && !isSuspended) startConnection();
    }
  }

  /** 設定未着時にも親へ再要求し、端末プロセス自体は停止しない。 */
  function reconnect() {
    if (isDisposed) return;
    isSuspended = false;
    shouldReconnect = true;
    status.textContent = "端末へ再接続しています";
    readController?.abort();
    closeConnection();
    notify("ready");
    startConnection();
  }

  function sendInput(data) {
    if (!connectionId || isDisposed) return;
    const targetId = connectionId;
    writeQueue = writeQueue.then(() => request({action:"input", connection_id:targetId, data:encode(data)}))
      .then(() => { if (data.endsWith("\r")) notify("submitted"); })
      .catch(error => { status.textContent = `${error.message}（入力は再送していません）`; });
  }
  document.addEventListener("pointerdown", () => notify("focus"));
  window.addEventListener("focus", () => notify("focus"));
  window.addEventListener("message", event => {
    if (event.origin !== location.origin || event.source !== parent) return;
    if (event.data?.type === "argos-console-dispose") {
      isDisposed = true;
      readController?.abort();
      closeConnection();
    }
    if (event.data?.type !== "argos-console-config" || configuration) return;
    configuration = event.data;
    startConnection();
  });
  document.getElementById("voice").addEventListener("click", () => notify("voice"));
  document.getElementById("cancel-audio").addEventListener("click", () => notify("cancel"));
  document.getElementById("discard").addEventListener("click", () => sendInput("\x15"));
  document.getElementById("reconnect").addEventListener("click", reconnect);
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(resize).observe(holder);
  document.fonts?.ready.then(resize);
  window.addEventListener("pagehide", () => {
    isSuspended = true;
    readController?.abort();
    closeConnection();
  });
  window.addEventListener("pageshow", () => {
    if (isSuspended) reconnect();
  });
  notify("ready");
})();
