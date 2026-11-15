// 会話欄のDOMを受け持つ。メッセージ単位の差分更新、mermaidの図の描画、タップでの拡大表示を行う。
// 本文のHTML化は message_text.js（ArgosMessageText）に任せる。
(function initializeMessageView(globalScope) {
  "use strict";

  const MERMAID_URL = "/static/vendor/mermaid.min.js";
  const DIAGRAM_CACHE_LIMIT = 100;
  const ZOOMABLE_SELECTOR = ".msg-zoomable";

  // 図のソース → 描画結果（SVG文字列。描画できなかったものはnull）。同じ図を描き直さないために覚えておく。
  const diagramCache = new Map();
  const signatures = new WeakMap();
  const watchedContainers = new WeakSet();
  let mermaidLoading = null;
  let renderChain = Promise.resolve();
  let diagramSequence = 0;
  let lightbox = null;

  /** 描画結果を覚える。古いものから捨てて、際限なく増えないようにする。 */
  function rememberDiagram(source, svg) {
    if (diagramCache.size >= DIAGRAM_CACHE_LIMIT) {
      diagramCache.delete(diagramCache.keys().next().value);
    }
    diagramCache.set(source, svg);
  }

  /** mermaidは大きいので、図が初めて出てきたときにだけ読み込む。 */
  function loadMermaid() {
    if (mermaidLoading) return mermaidLoading;
    mermaidLoading = new Promise((resolve, reject) => {
      if (globalScope.mermaid) {
        resolve(globalScope.mermaid);
        return;
      }
      const script = document.createElement("script");
      script.src = MERMAID_URL;
      script.onload = () => resolve(globalScope.mermaid);
      script.onerror = () => reject(new Error("mermaidを読み込めません"));
      document.head.appendChild(script);
    }).then(mermaid => {
      // securityLevel: strict で、図の中のHTMLやクリック動作を無効にする。
      mermaid.initialize({startOnLoad: false, securityLevel: "strict", theme: "dark"});
      return mermaid;
    });
    mermaidLoading.catch(() => { mermaidLoading = null; });
    return mermaidLoading;
  }

  /** mermaidのソースをSVG文字列にする。描画できなければnullを返す。同時に1つずつ順番に描く。 */
  function renderMermaid(source) {
    if (diagramCache.has(source)) return Promise.resolve(diagramCache.get(source));
    const task = renderChain.then(async () => {
      if (diagramCache.has(source)) return diagramCache.get(source);
      const id = `argos-mermaid-${++diagramSequence}`;
      let svg = null;
      try {
        const mermaid = await loadMermaid();
        svg = (await mermaid.render(id, source)).svg;
      } catch (error) {
        console.warn("mermaidの図を描画できません", error);
      } finally {
        // 失敗時にmermaidが画面へ残す作業用の要素を片付ける。
        for (const leftover of [id, `d${id}`]) {
          const element = document.getElementById(leftover);
          if (element) element.remove();
        }
      }
      rememberDiagram(source, svg);
      return svg;
    });
    renderChain = task.catch(() => null);
    return task;
  }

  /** 図の枠へ描画結果を入れる。描画できなかった場合は、コードを残して一言添える。 */
  function applyDiagram(figure, svg) {
    if (svg) {
      figure.innerHTML = svg;
      figure.dataset.state = "done";
      figure.classList.add("msg-zoomable");
      return;
    }
    figure.dataset.state = "error";
    if (!figure.querySelector("figcaption")) {
      const caption = document.createElement("figcaption");
      caption.textContent = "図を描画できなかったよ";
      figure.appendChild(caption);
    }
  }

  /** 要素の中にある、まだ描いていない図を描画する。描き終えるたびに onLayoutChange を呼ぶ。 */
  function enhance(root, onLayoutChange) {
    const figures = root.querySelectorAll('figure[data-kind="mermaid"][data-state="pending"]');
    for (const figure of figures) {
      const code = figure.querySelector("code");
      const source = code ? code.textContent : "";
      if (diagramCache.has(source)) {
        // 描いたことがある図は、その場で入れる（ストリーミング中の再描画でちらつかせない）。
        applyDiagram(figure, diagramCache.get(source));
        continue;
      }
      figure.dataset.state = "rendering";
      renderMermaid(source).then(svg => {
        if (!figure.isConnected) return;
        applyDiagram(figure, svg);
        if (onLayoutChange) onLayoutChange();
      });
    }
  }

  /** 画像の読み込みで高さが変わったことを、会話欄ごとに1度だけ見張る。 */
  function watchContainer(container, onLayoutChange) {
    if (!onLayoutChange || watchedContainers.has(container)) return;
    watchedContainers.add(container);
    // loadイベントは親へ伝わらないので、キャプチャで受ける。
    container.addEventListener("load", event => {
      if (event.target && event.target.tagName === "IMG") onLayoutChange();
    }, true);
  }

  /** メッセージの見た目が変わったかを見分けるための署名を作る。 */
  function messageSignature(message) {
    return JSON.stringify([message.role, message.text, Boolean(message.streaming)]);
  }

  /**
   * 会話欄を、渡されたメッセージ一覧に合わせる。変わったメッセージの要素だけを作り直す。
   *
   * options.renderShell(message, bodyHtml): メッセージ1件分のHTML（要素1つ）を返す。
   * options.emptyHtml: メッセージが無いときに出すHTML。
   * options.onLayoutChange: 図や画像が後から入って高さが変わったときに呼ばれる。
   * 戻り値: 画面を書き換えたらtrue。
   */
  function sync(container, messages, options) {
    const {renderShell, emptyHtml = "", onLayoutChange} = options;
    watchContainer(container, onLayoutChange);
    if (!messages.length) {
      if (container.dataset.msgEmpty === "true") return false;
      container.innerHTML = emptyHtml;
      container.dataset.msgEmpty = "true";
      return true;
    }
    if (container.dataset.msgEmpty !== "false") {
      container.innerHTML = "";
      container.dataset.msgEmpty = "false";
    }

    const existing = new Map();
    for (const child of Array.from(container.children)) {
      if (child.dataset.msgKey) existing.set(child.dataset.msgKey, child);
    }
    let changed = false;
    messages.forEach((message, index) => {
      const key = String(message.id || `index-${index}`);
      const signature = messageSignature(message);
      let element = existing.get(key);
      existing.delete(key);
      if (!element || signatures.get(element) !== signature) {
        const bodyHtml = globalScope.ArgosMessageText.renderMessageHtml(message.text, {streaming: message.streaming});
        const template = document.createElement("template");
        template.innerHTML = renderShell(message, bodyHtml).trim();
        const fresh = template.content.firstElementChild;
        fresh.dataset.msgKey = key;
        signatures.set(fresh, signature);
        if (element) element.replaceWith(fresh);
        element = fresh;
        changed = true;
      }
      const current = container.children[index];
      if (current !== element) {
        container.insertBefore(element, current || null);
        changed = true;
      }
      if (element.querySelector('figure[data-state="pending"]')) enhance(element, onLayoutChange);
    });
    // 一覧から消えたメッセージと、管理外の要素を取り除く。
    while (container.children.length > messages.length) {
      container.lastElementChild.remove();
      changed = true;
    }
    return changed;
  }

  /** 会話欄を空の状態から作り直させる（表示の切り替え後などに使う）。 */
  function reset(container) {
    container.innerHTML = "";
    delete container.dataset.msgEmpty;
  }

  /** 拡大表示を閉じる。 */
  function closeLightbox() {
    if (!lightbox) return;
    lightbox.remove();
    lightbox = null;
  }

  /** 図・表・画像を、画面いっぱいに拡大して表示する。 */
  function openLightbox(source) {
    closeLightbox();
    lightbox = document.createElement("div");
    lightbox.className = "msg-lightbox msg-body";
    lightbox.setAttribute("role", "dialog");
    lightbox.setAttribute("aria-label", "拡大表示（タップで閉じる）");
    const copy = source.cloneNode(true);
    copy.classList.remove("msg-zoomable");
    copy.removeAttribute("loading");
    lightbox.appendChild(copy);
    lightbox.addEventListener("click", closeLightbox);
    document.body.appendChild(lightbox);
  }

  /** 拡大表示の操作（タップで開く、Escで閉じる）をページへ1度だけ登録する。 */
  function installLightbox() {
    document.addEventListener("click", event => {
      const target = event.target;
      if (!target || typeof target.closest !== "function") return;
      if (target.closest(".msg-lightbox") || target.closest("a")) return;
      const zoomable = target.closest(ZOOMABLE_SELECTOR);
      if (zoomable) openLightbox(zoomable);
    });
    document.addEventListener("keydown", event => {
      if (event.key === "Escape") closeLightbox();
    });
  }

  if (typeof document !== "undefined" && document.addEventListener) installLightbox();

  const api = {sync, reset, enhance, renderMermaid, openLightbox, closeLightbox, messageSignature};
  globalScope.ArgosMessageView = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
