(function initializeMessageText(globalScope) {
  "use strict";

  /** HTMLへ埋め込む文字列を安全にエスケープする。 */
  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, character => (
      {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[character]
    ));
  }

  /** 応答本文のHTTP URLだけを安全な別タブリンクとして描画する。 */
  function renderMessageText(value) {
    const text = String(value ?? "");
    const urlPattern = /https?:\/\/[^\s<>"']+/g;
    let rendered = "";
    let cursor = 0;
    for (const match of text.matchAll(urlPattern)) {
      const url = match[0];
      const start = match.index ?? cursor;
      rendered += escapeHtml(text.slice(cursor, start));
      rendered += `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${escapeHtml(url)}</a>`;
      cursor = start + url.length;
    }
    return rendered + escapeHtml(text.slice(cursor));
  }

  const api = {escapeHtml, renderMessageText};
  globalScope.ArgosMessageText = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
