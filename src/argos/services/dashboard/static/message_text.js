// 会話本文（Markdown）を安全なHTML文字列へ変換する。
// DOMに触れない純粋な変換だけを担当し、図の描画や拡大表示は message_view.js が受け持つ。
(function initializeMessageText(globalScope) {
  "use strict";

  const SVG_NAMESPACE = "http://www.w3.org/2000/svg";
  const FENCE_CLOSE_PATTERN = /^[\s>]*(`{3,}|~{3,})\s*$/;

  /** HTMLへ埋め込む文字列を安全にエスケープする。 */
  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, character => (
      {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[character]
    ));
  }

  /** ブラウザとNode.jsの両方から、同梱したmarkdown-itを取り出す。 */
  function loadMarkdownFactory() {
    if (typeof globalScope.markdownit === "function") return globalScope.markdownit;
    if (typeof module !== "undefined" && module.exports && typeof require === "function") {
      const loaded = require("./vendor/markdown-it.min.js");
      return typeof loaded === "function" ? loaded : loaded.default;
    }
    return null;
  }

  /** SVGのソースを、スクリプトが動かない<img>用のdata URLへ変換する。SVGでなければnullを返す。 */
  function svgDataUrl(source) {
    const text = String(source ?? "");
    const start = text.search(/<svg[\s>]/i);
    if (start < 0 || !/<\/svg>/i.test(text)) return null;
    let svg = text.slice(start).trim();
    const openTagEnd = svg.indexOf(">");
    const openTag = svg.slice(0, openTagEnd);
    // <img>で表示するSVGは名前空間の宣言が必須なので、無ければ補う。
    if (!/\sxmlns\s*=/.test(openTag)) {
      svg = `<svg xmlns="${SVG_NAMESPACE}"${svg.slice(4)}`;
    }
    return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
  }

  /** SVGが大きさを指定していない（viewBoxだけ）なら、幅に合わせて広げるべきと判定する。 */
  function isFluidSvg(source) {
    const match = /<svg[^>]*>/i.exec(String(source ?? ""));
    return Boolean(match) && !/\swidth\s*=/.test(match[0]);
  }

  /** 普通のコードブロックをHTMLにする。 */
  function renderCodeBlock(source, language, extraClass) {
    const languageAttribute = language ? ` data-lang="${escapeHtml(language)}"` : "";
    const classAttribute = extraClass ? ` class="${extraClass}"` : "";
    return `<pre${classAttribute}${languageAttribute}><code>${escapeHtml(source)}</code></pre>\n`;
  }

  // コードブロックの言語名ごとの描画器。新しい表現（グラフや数式など）はここへ登録して増やす。
  const fenceRenderers = new Map();

  /** コードブロックの言語名に、専用の描画器を登録する。 */
  function registerFenceRenderer(language, renderer) {
    fenceRenderers.set(String(language).toLowerCase(), renderer);
  }

  // mermaid：ソースを持つ枠だけを出し、図への変換は message_view.js がブラウザ上で行う。
  registerFenceRenderer("mermaid", source => (
    `<figure class="msg-figure" data-kind="mermaid" data-state="pending">${renderCodeBlock(source, "mermaid").trim()}</figure>\n`
  ));

  // svg：<img>経由で表示する。SVG内のスクリプトや外部読み込みは動かない。
  registerFenceRenderer("svg", source => {
    const url = svgDataUrl(source);
    if (!url) return renderCodeBlock(source, "svg");
    const fluid = isFluidSvg(source) ? " msg-svg-fluid" : "";
    return `<figure class="msg-figure msg-zoomable" data-kind="svg"><img class="msg-svg${fluid}" src="${url}" alt="SVG画像"></figure>\n`;
  });

  /** ストリーミング中で、まだ閉じていないコードブロックか判定する。 */
  function isUnclosedFence(token, env) {
    if (token.meta && token.meta.pending) return true;
    if (!env || !env.streaming || !token.map) return false;
    const [startLine, endLine] = token.map;
    if (endLine < env.lines.length) {
      // 後ろに空行以外の行が続いていれば、閉じている。
      if (env.lines.slice(endLine).some(line => line.trim())) return false;
    }
    let lastLine = Math.min(endLine, env.lines.length) - 1;
    while (lastLine > startLine && !env.lines[lastLine].trim()) lastLine -= 1;
    return !(lastLine > startLine && FENCE_CLOSE_PATTERN.test(env.lines[lastLine]));
  }

  /** 本文に直接書かれた <svg>...</svg> を、svgのコードブロックと同じ扱いにするブロック規則。 */
  function svgBlockRule(state, startLine, endLine, silent) {
    if (state.sCount[startLine] - state.blkIndent >= 4) return false;
    const lineStart = state.bMarks[startLine] + state.tShift[startLine];
    if (!/^<svg[\s>]/i.test(state.src.slice(lineStart, state.eMarks[startLine]))) return false;
    let closeLine = -1;
    for (let line = startLine; line < endLine; line += 1) {
      if (/<\/svg>/i.test(state.src.slice(state.bMarks[line], state.eMarks[line]))) {
        closeLine = line;
        break;
      }
    }
    const streaming = Boolean(state.env && state.env.streaming);
    if (closeLine < 0 && !streaming) return false;
    if (silent) return true;
    const lastLine = closeLine < 0 ? endLine - 1 : closeLine;
    const token = state.push("fence", "code", 0);
    token.info = "svg";
    token.markup = "```";
    token.content = state.src.slice(lineStart, state.eMarks[lastLine]);
    token.map = [startLine, lastLine + 1];
    if (closeLine < 0) token.meta = {pending: true};
    state.line = lastLine + 1;
    return true;
  }

  /** markdown-itを、ARGOSの会話表示用に設定して返す。 */
  function createMarkdown() {
    const factory = loadMarkdownFactory();
    if (!factory) return null;
    // html:false で生HTMLは文字として表示する（危険なタグを実行させない）。
    const md = factory({html: false, linkify: true, breaks: true});
    // 「main.py」のような文字列までリンクにしないよう、http(s):// で始まるものだけを自動リンクにする。
    md.linkify.set({fuzzyLink: false, fuzzyEmail: false, fuzzyIP: false});
    md.block.ruler.before("fence", "svg_block", svgBlockRule, {
      alt: ["paragraph", "reference", "blockquote", "list"],
    });

    md.renderer.rules.fence = (tokens, index, options, env) => {
      const token = tokens[index];
      const language = String(token.info || "").trim().split(/\s+/, 1)[0].toLowerCase();
      const renderer = fenceRenderers.get(language);
      if (!renderer) return renderCodeBlock(token.content, language);
      // 書きかけの図は、完成するまでコードのまま見せる。
      if (isUnclosedFence(token, env)) return renderCodeBlock(token.content, language, "msg-code-pending");
      return renderer(token.content, env);
    };

    md.renderer.rules.link_open = (tokens, index, options, env, self) => {
      tokens[index].attrSet("target", "_blank");
      tokens[index].attrSet("rel", "noreferrer");
      return self.renderToken(tokens, index, options);
    };

    const defaultImage = md.renderer.rules.image;
    md.renderer.rules.image = (tokens, index, options, env, self) => {
      tokens[index].attrJoin("class", "msg-image msg-zoomable");
      tokens[index].attrSet("loading", "lazy");
      return defaultImage(tokens, index, options, env, self);
    };

    // 表は横にはみ出してもスクロールでき、タップで拡大できるよう枠で包む。
    md.renderer.rules.table_open = () => '<div class="msg-table-wrap msg-zoomable"><table>\n';
    md.renderer.rules.table_close = () => "</table></div>\n";
    return md;
  }

  const markdown = createMarkdown();

  /**
   * 会話本文をHTMLへ変換する。
   * options.streaming が真なら、書きかけの図をコードのまま表示する。
   */
  function renderMessageHtml(value, options) {
    const text = String(value ?? "");
    if (!text) return "";
    if (!markdown) return escapeHtml(text).replace(/\n/g, "<br>");
    const env = {streaming: Boolean(options && options.streaming), lines: text.split("\n")};
    return markdown.render(text, env);
  }

  const api = {escapeHtml, renderMessageHtml, registerFenceRenderer, svgDataUrl};
  globalScope.ArgosMessageText = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
