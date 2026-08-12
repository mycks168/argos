const test = require("node:test");
const assert = require("node:assert/strict");

const {renderMessageText} = require("../../src/argos/services/dashboard/static/message_text.js");

test("本文をエスケープしながらHTTP URLだけをリンク化する", () => {
  const rendered = renderMessageText('出典 <script>\nhttps://example.com/a?x=1&y=2');

  assert.match(rendered, /出典 &lt;script&gt;/);
  assert.match(rendered, /href="https:\/\/example\.com\/a\?x=1&amp;y=2"/);
  assert.match(rendered, /target="_blank" rel="noreferrer"/);
});

test("URLでない本文にはHTMLを生成しない", () => {
  assert.equal(renderMessageText("通常の回答です。"), "通常の回答です。");
});
