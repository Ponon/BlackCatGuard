// 首页刷新使用纯文本，并保持表格最多五条和空状态互斥。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function element() {
    return {
        children: [], hidden: false, textContent: '', className: '',
        appendChild(child) { this.children.push(child); },
        replaceChildren(fragment) { this.children = fragment.children; },
    };
}
const body = element();
const table = element();
const empty = element();
const selectors = { '#runtime-events-body': body, '#runtime-events-table': table, '#runtime-events-empty': empty };
const context = {
    document: {
        querySelector: selector => selectors[selector] || null,
        querySelectorAll: () => [],
        createElement: element,
        createDocumentFragment: element,
    },
    window: { addEventListener() {} },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync('static/js/dashboard.js', 'utf8'), context);
vm.runInContext('updateRecentEvents([])', context);
assert.equal(body.children.length, 0);
assert.equal(table.hidden, true);
assert.equal(empty.hidden, false);
context.events = Array.from({ length: 8 }, (_, index) => ({ time: `时间${index}`, type: '消息测试', content: '<script>bad()</script>', result: '正常' }));
context.events[1].content = '';
vm.runInContext('updateRecentEvents(events)', context);
assert.equal(body.children.length, 5);
assert.equal(body.children[0].children[2].textContent, '<script>bad()</script>');
assert.equal(body.children[1].children[2].textContent, '—');
assert.equal(table.hidden, false);
assert.equal(empty.hidden, true);
const unchanged = body.children;
vm.runInContext('updateRecentEvents(events)', context);
assert.equal(body.children, unchanged);
console.log('首页实时事件检查通过');

const connection = { dataset: {} };
const botState = element();
const latency = element();
selectors['#runtime-connection'] = connection;
selectors['#runtime-bot-state'] = botState;
selectors['#runtime-latency'] = latency;
vm.runInContext("updateConnectionDisplay({ tone: 'slow', connection_label: '延迟较高', latency_label: '1500 ms' })", context);
assert.equal(connection.dataset.tone, 'slow');
assert.equal(latency.textContent, '1500 ms');
vm.runInContext("updateConnectionDisplay({ tone: 'error', connection_label: '连接状态未知', latency_label: '—' })", context);
assert.equal(connection.dataset.tone, 'error');
assert.equal(botState.textContent, '连接状态未知');
assert.equal(latency.textContent, '—');
