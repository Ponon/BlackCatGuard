// 导航反馈不干扰外链 新窗口 页内链接或已由业务脚本处理的请求
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const handlers = {};
const attributes = new Map();
const stored = new Map();
let bar;
function element() {
    return { hidden: false, style: { setProperty() {} }, classList: { remove() {}, add() {} }, setAttribute() {} };
}
const location = new URL('https://example.test/group');
const context = {
    URL, Date, Math, Number, JSON, location,
    sessionStorage: { getItem: key => stored.get(key) || null, setItem: (key, value) => stored.set(key, value), removeItem: key => stored.delete(key) },
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 2, clearTimeout() {}, requestAnimationFrame: callback => callback(),
    document: {
        readyState: 'complete',
        documentElement: { setAttribute: (key, value) => attributes.set(key, value), removeAttribute: key => attributes.delete(key) },
        body: { prepend: value => { bar = value; } },
        createElement: element,
        addEventListener: (name, callback) => { handlers[name] = callback; },
    },
    window: { location, matchMedia: () => ({ matches: false }), addEventListener: (name, callback) => { handlers[name] = callback; } },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync('static/js/navigation.js', 'utf8'), context);
function click(href, changes = {}) {
    const link = { href, target: '', hasAttribute: () => false };
    const event = { defaultPrevented: false, button: 0, target: { closest: () => link }, ...changes };
    handlers.click(event);
}
assert.equal(bar.hidden, true);
for (const href of ['https://other.test/', 'https://example.test/group#help', 'mailto:test@example.test']) {
    click(href);
    assert.equal(bar.hidden, true);
}
for (const flags of [{ ctrlKey: true }, { metaKey: true }, { button: 1 }, { defaultPrevented: true }]) {
    click('https://example.test/events', flags);
    assert.equal(bar.hidden, true);
}
click('https://example.test/events');
assert.equal(bar.hidden, false);
assert.equal(stored.size, 1);
handlers.keydown({ key: 'Escape' });
assert.equal(bar.hidden, true);
assert.equal(stored.size, 0);
click('https://example.test/broadcast');
handlers.pageshow({ persisted: true });
assert.equal(bar.hidden, true);
assert.equal(stored.size, 0);
handlers.submit({ defaultPrevented: true });
assert.equal(bar.hidden, true);
console.log('导航等待反馈 外链与特殊点击 取消及返回恢复检查通过');
