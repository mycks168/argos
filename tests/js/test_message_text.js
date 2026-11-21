const test = require("node:test");
const assert = require("node:assert/strict");

const {
  escapeHtml,
  renderMessageHtml,
  registerFenceRenderer,
  svgDataUrl,
} = require("../../src/argos/services/dashboard/static/message_text.js");

test("生のHTMLは実行させず文字として表示し、http(s)のURLだけを別タブのリンクにする", () => {
  const rendered = renderMessageHtml('出典 <script>alert(1)</script>\nhttps://example.com/a?x=1&y=2 と main.py');

  assert.match(rendered, /出典 &lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.match(rendered, /<a href="https:\/\/example\.com\/a\?x=1&amp;y=2" target="_blank" rel="noreferrer">/);
  // ファイル名のような文字列までリンクにしない。
  assert.doesNotMatch(rendered, /href="[^"]*main\.py/);
});

test("危険なリンク先はリンクにしない", () => {
  const rendered = renderMessageHtml("[押して](javascript:alert(1))");

  assert.doesNotMatch(rendered, /<a /);
});

test("空の本文は空文字、改行は改行として表示する", () => {
  assert.equal(renderMessageHtml(""), "");
  assert.equal(renderMessageHtml(null), "");
  assert.match(renderMessageHtml("一行目\n二行目"), /一行目<br>\n二行目/);
});

test("見出し・強調・箇条書き・コードをMarkdownとして表示する", () => {
  const rendered = renderMessageHtml("## 見出し\n\n**太字** と `code`\n\n- 一つ目\n- 二つ目\n\n```python\nprint('<b>')\n```");

  assert.match(rendered, /<h2>見出し<\/h2>/);
  assert.match(rendered, /<strong>太字<\/strong>/);
  assert.match(rendered, /<code>code<\/code>/);
  assert.match(rendered, /<li>一つ目<\/li>/);
  assert.match(rendered, /<pre data-lang="python"><code>print\(&#39;&lt;b&gt;&#39;\)\n<\/code><\/pre>/);
});

test("表は横スクロールと拡大ができる枠で包む", () => {
  const rendered = renderMessageHtml("| 項目 | 値 |\n|---|---|\n| a | 1 |");

  assert.match(rendered, /<div class="msg-table-wrap msg-zoomable"><table>/);
  assert.match(rendered, /<th>項目<\/th>/);
  assert.match(rendered, /<\/table><\/div>/);
});

test("画像は拡大できる画像として表示する", () => {
  const rendered = renderMessageHtml("![猫](/uploads/cat.png)");

  assert.match(rendered, /<img src="\/uploads\/cat\.png" alt="猫" class="msg-image msg-zoomable" loading="lazy">/);
});

test("mermaidのコードブロックは、ソースを持つ図の枠にする", () => {
  const rendered = renderMessageHtml("図だよ\n```mermaid\ngraph TD\nA-->B\n```\n続き");

  assert.match(rendered, /<figure class="msg-figure" data-kind="mermaid" data-state="pending">/);
  assert.match(rendered, /<code>graph TD\nA--&gt;B\n<\/code>/);
  assert.match(rendered, /<p>続き<\/p>/);
});

test("ストリーミング中の書きかけの図はコードのまま見せ、閉じたら図の枠にする", () => {
  const writing = renderMessageHtml("```mermaid\ngraph TD\nA-->B", {streaming: true});
  assert.match(writing, /<pre class="msg-code-pending" data-lang="mermaid">/);
  assert.doesNotMatch(writing, /<figure/);

  const closed = renderMessageHtml("```mermaid\ngraph TD\nA-->B\n```\n", {streaming: true});
  assert.match(closed, /<figure class="msg-figure" data-kind="mermaid"/);

  const followed = renderMessageHtml("```mermaid\ngraph TD\n```\n続きを書いて", {streaming: true});
  assert.match(followed, /<figure class="msg-figure" data-kind="mermaid"/);

  // ストリーミングが終わっていれば、閉じ忘れていても図として扱う。
  const finished = renderMessageHtml("```mermaid\ngraph TD\nA-->B");
  assert.match(finished, /<figure class="msg-figure" data-kind="mermaid"/);
});

test("svgのコードブロックは、スクリプトが動かない画像として表示する", () => {
  const rendered = renderMessageHtml('```svg\n<svg viewBox="0 0 10 10"><script>alert(1)</script><circle r="5"/></svg>\n```');

  assert.match(rendered, /<figure class="msg-figure msg-zoomable" data-kind="svg"><img class="msg-svg msg-svg-fluid" src="data:image\/svg\+xml;charset=utf-8,/);
  // SVGの中身はURLとして符号化され、ページのHTMLとしては現れない。
  assert.doesNotMatch(rendered, /<script/);
  assert.doesNotMatch(rendered, /<circle/);
});

test("本文に直接書かれたSVGも画像として表示する", () => {
  const rendered = renderMessageHtml('絵だよ\n<svg width="10" height="10">\n<circle r="5"/>\n</svg>\n後ろの文');

  assert.match(rendered, /<p>絵だよ<\/p>/);
  assert.match(rendered, /<img class="msg-svg" src="data:image\/svg\+xml/);
  assert.match(rendered, /<p>後ろの文<\/p>/);
});

test("書きかけや壊れたSVGは画像にしない", () => {
  const writing = renderMessageHtml('<svg width="10">\n<circle', {streaming: true});
  assert.match(writing, /<pre class="msg-code-pending" data-lang="svg">/);

  const unclosed = renderMessageHtml('<svg width="10">\n<circle');
  assert.doesNotMatch(unclosed, /<img/);
  assert.match(unclosed, /&lt;svg/);

  const notSvg = renderMessageHtml("```svg\nこれはSVGではない\n```");
  assert.match(notSvg, /<pre data-lang="svg"><code>これはSVGではない/);
});

test("SVGのdata URLは名前空間を補い、SVGでなければnullを返す", () => {
  const url = svgDataUrl('<?xml version="1.0"?>\n<svg width="1"></svg>');
  assert.equal(decodeURIComponent(url.split(",")[1]), '<svg xmlns="http://www.w3.org/2000/svg" width="1"></svg>');

  const declared = svgDataUrl('<svg xmlns="http://www.w3.org/2000/svg"></svg>');
  assert.equal(decodeURIComponent(declared.split(",")[1]), '<svg xmlns="http://www.w3.org/2000/svg"></svg>');

  assert.equal(svgDataUrl("ただの文字"), null);
  assert.equal(svgDataUrl("<svg>閉じていない"), null);
});

test("コードブロックの描画器を言語名で追加できる", () => {
  registerFenceRenderer("Shout", source => `<p class="shout">${escapeHtml(source.trim().toUpperCase())}</p>\n`);

  assert.match(renderMessageHtml("```shout\nhello <b>\n```"), /<p class="shout">HELLO &lt;B&gt;<\/p>/);
});
