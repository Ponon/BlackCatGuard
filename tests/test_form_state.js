// 使用 Node 内置模块验证表单状态，不依赖浏览器或额外测试框架。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Form extends EventTarget {
    constructor(saved = true) {
        super();
        this.dataset = { saved: String(saved), groupConfigured: 'true' };
        this.status = { textContent: '' };
        this.elements = [
            { name: 'enabled', type: 'checkbox', checked: false },
            { name: 'timeout', type: 'number', value: '60' },
            { name: 'csrf_token', type: 'hidden', value: 'token' },
        ];
        this.elements.enabled = this.elements[0];
    }
    querySelector() { return this.status; }
}
const forms = [new Form(), new Form()];
const context = vm.createContext({
    Event, JSON, Array,
    document: { querySelectorAll: () => forms },
});
vm.runInContext(fs.readFileSync('static/js/form-state.js', 'utf8'), context);
const state = { textContent: '' };
const tracker = context.trackFormState(forms[0], state);
assert.equal(state.textContent, '已保存');
assert.equal(forms[0].status.textContent, '已关闭');
forms[0].elements[1].value = '120';
forms[0].dispatchEvent(new Event('input'));
assert.equal(state.textContent, '有未保存修改');
forms[0].elements[1].value = '60';
forms[0].dispatchEvent(new Event('input'));
assert.equal(state.textContent, '已保存');
forms[0].elements.enabled.checked = true;
forms[0].dispatchEvent(new Event('change'));
assert.equal(forms[0].status.textContent, '待保存启用');
// 保存期间控件被冻结，不应影响快照判断。
forms[0].elements.forEach(field => { field.disabled = true; });
tracker.markSaved();
forms[0].elements.forEach(field => { field.disabled = false; });
assert.equal(tracker.isDirty(), false);
assert.equal(forms[0].status.textContent, '已开启设置');
context.resetGroupDependentForms();
assert.equal(forms[0].elements.enabled.checked, false);
assert.equal(forms[1].dataset.groupConfigured, 'false');
const unsaved = new Form(false);
context.trackFormState(unsaved, state);
assert.equal(state.textContent, '尚未保存');
console.log('表单状态检查通过');
