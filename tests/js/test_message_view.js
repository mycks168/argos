const test = require("node:test");
const assert = require("node:assert/strict");

/** 並び替え・差し替え・削除だけを再現する、ブラウザなしの要素。 */
class FakeElement {
  constructor(html = "") {
    this.html = html;
    this.dataset = {};
    this.children = [];
    this.parent = null;
    this.figures = [];
    this.listeners = {};
    this.classList = {added: [], add: name => this.classList.added.push(name), remove: () => {}};
  }
  get innerHTML() { return this.html; }
  set innerHTML(value) {
    this.html = value;
    for (const child of this.children) child.parent = null;
    this.children = [];
  }
  get isConnected() { return true; }
  get lastElementChild() { return this.children[this.children.length - 1] || null; }
  detach() {
    if (!this.parent) return;
    this.parent.children.splice(this.parent.children.indexOf(this), 1);
    this.parent = null;
  }
  insertBefore(element, reference) {
    element.detach();
    const index = reference ? this.children.indexOf(reference) : this.children.length;
    this.children.splice(index, 0, element);
    element.parent = this;
  }
  appendChild(element) { this.insertBefore(element, null); }
  replaceWith(element) {
    element.detach();
    this.parent.children[this.parent.children.indexOf(this)] = element;
    element.parent = this.parent;
    this.parent = null;
  }
  remove() { this.detach(); }
  querySelector(selector) {
    if (selector === "code") return this.code || null;
    if (selector === "figcaption") return this.children.find(child => child.tag === "figcaption") || null;
    return this.figures.find(figure => figure.dataset.state === "pending") || null;
  }
  querySelectorAll() { return this.figures.filter(figure => figure.dataset.state === "pending"); }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  cloneNode() { return new FakeElement(this.html); }
  setAttribute() {}
  removeAttribute() {}
}

/** mermaidの図の枠を作る。 */
function figure(source) {
  const element = new FakeElement();
  element.dataset.state = "pending";
  element.code = {textContent: source};
  return element;
}

const documentListeners = {};
const body = new FakeElement();
globalThis.document = {
  body,
  head: new FakeElement(),
  getElementById: () => null,
  addEventListener: (name, handler) => { documentListeners[name] = handler; },
  createElement: tag => {
    if (tag !== "template") {
      const element = new FakeElement();
      element.tag = tag;
      return element;
    }
    return {
      content: {firstElementChild: null},
      set innerHTML(html) {
        const element = new FakeElement(html);
        // 図を含むメッセージは、描画待ちの枠を1つ持つものとして扱う。
        if (html.includes('data-kind="mermaid"')) element.figures.push(figure("graph TD"));
        this.content.firstElementChild = element;
      },
    };
  },
};

const mermaidCalls = [];
globalThis.mermaid = {
  initialize: options => { globalThis.mermaid.options = options; },
  render: async (id, source) => {
    mermaidCalls.push(source);
    if (source.includes("壊れた")) throw new Error("構文エラー");
    return {svg: `<svg id="${id}">${source}</svg>`};
  },
};

require("../../src/argos/services/dashboard/static/message_text.js");
const view = require("../../src/argos/services/dashboard/static/message_view.js");

const shell = {
  renderShell: (message, bodyHtml) => `<article data-role="${message.role}">${bodyHtml}</article>`,
  emptyHtml: '<div class="empty">会話待機中</div>',
};

test("メッセージが無いときは空表示を出し、同じ状態なら書き換えない", () => {
  const container = new FakeElement();

  assert.equal(view.sync(container, [], shell), true);
  assert.equal(container.innerHTML, shell.emptyHtml);
  assert.equal(view.sync(container, [], shell), false);
});

test("変わったメッセージだけを作り直し、変わらない要素は使い回す", () => {
  const container = new FakeElement();
  view.sync(container, [], shell);
  const first = [
    {id: "a", role: "user", text: "質問", streaming: false},
    {id: "b", role: "assistant", text: "答え", streaming: true},
  ];

  assert.equal(view.sync(container, first, shell), true);
  assert.equal(container.children.length, 2);
  assert.equal(container.dataset.msgEmpty, "false");
  const [userElement, answerElement] = container.children;
  assert.match(userElement.html, /<article data-role="user"><p>質問<\/p>/);

  // 何も変わらなければ触らない。
  assert.equal(view.sync(container, first, shell), false);
  assert.equal(container.children[0], userElement);
  assert.equal(container.children[1], answerElement);

  // ストリーミングで伸びた回答だけが新しい要素になる。
  const second = [first[0], {...first[1], text: "答えの続き"}];
  assert.equal(view.sync(container, second, shell), true);
  assert.equal(container.children[0], userElement);
  assert.notEqual(container.children[1], answerElement);
  assert.match(container.children[1].html, /答えの続き/);
});

test("古いメッセージが押し出されても、残った要素は使い回す", () => {
  const container = new FakeElement();
  const messages = ["a", "b", "c"].map(id => ({id, role: "assistant", text: id, streaming: false}));
  view.sync(container, messages, shell);
  const [, second, third] = container.children;

  const shifted = [...messages.slice(1), {id: "d", role: "user", text: "d", streaming: false}];
  assert.equal(view.sync(container, shifted, shell), true);

  assert.equal(container.children.length, 3);
  assert.equal(container.children[0], second);
  assert.equal(container.children[1], third);
  assert.equal(container.children[2].dataset.msgKey, "d");
});

test("別スロットの会話へ切り替えると全て入れ替え、resetで作り直せる", () => {
  const container = new FakeElement();
  view.sync(container, [{id: "a", role: "user", text: "a"}, {id: "b", role: "user", text: "b"}], shell);

  view.sync(container, [{role: "assistant", text: "idなし"}], shell);
  assert.equal(container.children.length, 1);
  assert.equal(container.children[0].dataset.msgKey, "index-0");

  view.reset(container);
  assert.equal(container.children.length, 0);
  assert.equal(container.dataset.msgEmpty, undefined);
  assert.equal(view.sync(container, [{role: "assistant", text: "idなし"}], shell), true);
});

test("図は1度だけ描き、2度目からは覚えた結果をその場で入れる", async () => {
  const first = figure("graph LR\nA-->B");
  const root = new FakeElement();
  root.figures.push(first);
  let layoutChanges = 0;

  view.enhance(root, () => { layoutChanges += 1; });
  assert.equal(first.dataset.state, "rendering");
  await view.renderMermaid("graph LR\nA-->B");
  await new Promise(resolve => setImmediate(resolve));

  assert.equal(first.dataset.state, "done");
  assert.match(first.innerHTML, /<svg id="argos-mermaid-\d+">graph LR/);
  assert.deepEqual(first.classList.added, ["msg-zoomable"]);
  assert.equal(layoutChanges, 1);
  assert.equal(globalThis.mermaid.options.securityLevel, "strict");

  // 同じ図は描き直さず、待たずに入る（ストリーミング中の再描画でちらつかせない）。
  const again = figure("graph LR\nA-->B");
  const nextRoot = new FakeElement();
  nextRoot.figures.push(again);
  view.enhance(nextRoot);
  assert.equal(again.dataset.state, "done");
  assert.equal(mermaidCalls.filter(source => source === "graph LR\nA-->B").length, 1);
});

test("描画できない図はコードを残して一言添え、後続の図は描ける", async () => {
  const broken = figure("壊れた図");
  const fine = figure("graph TD\nX-->Y");
  const root = new FakeElement();
  root.figures.push(broken, fine);

  view.enhance(root);
  await view.renderMermaid("graph TD\nX-->Y");
  await new Promise(resolve => setImmediate(resolve));

  assert.equal(broken.dataset.state, "error");
  assert.equal(broken.children[0].textContent, "図を描画できなかったよ");
  assert.equal(fine.dataset.state, "done");
});

test("図を含むメッセージは、同期のときに図の描画も始める", async () => {
  const container = new FakeElement();
  view.sync(container, [{id: "m", role: "assistant", text: "```mermaid\ngraph TD\n```"}], shell);
  await view.renderMermaid("graph TD");
  await new Promise(resolve => setImmediate(resolve));

  assert.equal(container.children[0].figures[0].dataset.state, "done");
});

test("画像の読み込みで高さが変わったら知らせる", () => {
  const container = new FakeElement();
  let layoutChanges = 0;
  view.sync(container, [{id: "a", role: "user", text: "a"}], {...shell, onLayoutChange: () => { layoutChanges += 1; }});

  container.listeners.load({target: {tagName: "IMG"}});
  container.listeners.load({target: {tagName: "SCRIPT"}});

  assert.equal(layoutChanges, 1);
});

test("図・表・画像のタップで拡大し、もう一度のタップやEscで閉じる", () => {
  const zoomable = new FakeElement("<table></table>");
  const target = {closest: selector => (selector === ".msg-zoomable" ? zoomable : null)};

  documentListeners.click({target});
  assert.equal(body.children.length, 1);
  const lightbox = body.children[0];
  assert.equal(lightbox.className, "msg-lightbox msg-body");
  assert.equal(lightbox.children[0].html, "<table></table>");

  lightbox.listeners.click();
  assert.equal(body.children.length, 0);

  documentListeners.click({target});
  documentListeners.keydown({key: "Escape"});
  assert.equal(body.children.length, 0);

  // リンクのタップや、拡大できない場所のタップでは開かない。
  documentListeners.click({target: {closest: selector => (selector === "a" ? {} : zoomable)}});
  documentListeners.click({target: {closest: () => null}});
  documentListeners.click({target: null});
  assert.equal(body.children.length, 0);
});
