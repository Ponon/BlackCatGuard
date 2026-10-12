// 检查统一提示格式，不依赖浏览器或真实网络。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const existing = { textContent: '管理员创建成功，请登录。' };
const context = {
    document: { querySelectorAll: () => [existing] },
    sessionStorage: { getItem: () => null },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync('static/js/toast.js', 'utf8'), context);
assert.equal(existing.textContent, '管理员创建成功请登录');
assert.equal(context.formatToastMessage('验证超时需为 10～3600 秒。'), '验证超时需为 10 至 3600 秒');
assert.equal(context.formatToastMessage('地址 http://127.0.0.1:7897。'), '地址 http://127.0.0.1:7897');
assert.equal(context.formatToastMessage('用户 @black_cat。'), '用户 @black_cat');
assert.equal(context.formatToastMessage('  保存失败，\n请重试。 '), '保存失败 请重试');
console.log('Toast 文案格式检查通过');
