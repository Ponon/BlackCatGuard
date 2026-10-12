// 只比较表单值，不用输入事件次数判断是否修改；恢复原值后恢复原状态。
function trackFormState(form, state) {
    const toggle = form.elements.enabled;
    const status = form.querySelector('[data-toggle-status]');
    let saved = form.dataset.saved === 'true';
    let savedToggle = toggle ? toggle.checked : false;
    function snapshot() {
        return JSON.stringify(Array.from(form.elements)
            .filter(field => field.name && field.name !== 'csrf_token' && !['submit', 'button'].includes(field.type))
            .map(field => [field.name, ['checkbox', 'radio'].includes(field.type) ? field.checked : field.value]));
    }
    let original = snapshot();
    function refresh() {
        const dirty = snapshot() !== original;
        if (state) state.textContent = dirty ? '有未保存修改' : (saved ? '已保存' : '尚未保存');
        if (status && toggle) {
            status.textContent = toggle.checked !== savedToggle
                ? (toggle.checked ? '待保存启用' : '待保存关闭')
                : (toggle.checked ? '已开启设置' : '已关闭');
        }
        form.dispatchEvent(new Event('form-state-change'));
    }
    form.addEventListener('input', refresh);
    form.addEventListener('change', refresh);
    form.addEventListener('group-unavailable', refresh);
    refresh();
    return {
        refresh,
        isDirty: () => snapshot() !== original,
        reset(isSaved = false) {
            saved = isSaved;
            if (toggle) savedToggle = toggle.checked;
            original = snapshot();
            refresh();
        },
        markSaved() {
            saved = true;
            if (toggle) savedToggle = toggle.checked;
            original = snapshot();
            refresh();
        },
    };
}

// 其他标签页清空群配置后，服务端拒绝启用时同步关闭本页所有关联开关。
function resetGroupDependentForms() {
    document.querySelectorAll('form[data-group-configured]').forEach(form => {
        form.dataset.groupConfigured = 'false';
        if (form.elements.enabled) form.elements.enabled.checked = false;
        form.dispatchEvent(new Event('group-unavailable'));
    });
}
