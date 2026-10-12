// 两个模块分别预览、校验和保存，互不提交对方的设置。
const verificationForm = document.querySelector('#verification-form');
const welcomeForm = document.querySelector('#welcome-form');

function updateVerificationPreview() {
    document.querySelector('#verification-status').textContent = verificationForm.elements.enabled.checked ? '已选择启用' : '已关闭';
    const arithmetic = verificationForm.elements.method.value === 'arithmetic';
    document.querySelector('#preview-question').hidden = !arithmetic;
    const answers = document.querySelector('#preview-answers');
    answers.replaceChildren();
    for (const text of arithmetic ? ['11', '13', '15'] : ['点击验证']) {
        const answer = document.createElement('span');
        answer.className = 'verification-answer';
        answer.textContent = text;
        answers.appendChild(answer);
    }
}

function updateWelcomePreview() {
    document.querySelector('#welcome-status').textContent = welcomeForm.elements.enabled.checked ? '已选择启用' : '已关闭';
    document.querySelector('#preview-welcome').textContent = welcomeForm.elements.welcome_message.value
        .replace(/\{nickname\}/g, '小黑').replace(/\{username\}/g, '@blackcat');
}

// 变量只插入欢迎语，昵称和用户名以普通文本展示。
welcomeForm.querySelectorAll('[data-variable]').forEach(button => {
    button.addEventListener('click', () => {
        const textarea = welcomeForm.elements.welcome_message;
        const text = button.dataset.variable;
        if (textarea.value.length - (textarea.selectionEnd - textarea.selectionStart) + text.length > 2000) {
            showError('欢迎语最多 2000 个字符。');
            return;
        }
        textarea.setRangeText(text, textarea.selectionStart, textarea.selectionEnd, 'end');
        textarea.focus();
        document.querySelector('#welcome-save-state').textContent = '有未保存修改';
        updateWelcomePreview();
    });
});

function bindSettingsForm(form, buttonId, stateId, updatePreview, validate) {
    const button = document.querySelector(buttonId);
    const state = document.querySelector(stateId);
    let saving = false;
    // 开关可点击以显示引导，但无群配置时立即恢复关闭状态。
    form.elements.enabled.addEventListener('change', () => {
        if (form.elements.enabled.checked && form.dataset.groupConfigured !== 'true') {
            form.elements.enabled.checked = false;
            showError('请先在群管理中配置机器人 Token 和群 ID。');
            updatePreview();
        }
    });
    for (const event of ['input', 'change']) {
        form.addEventListener(event, () => {
            state.textContent = '有未保存修改';
            updatePreview();
        });
    }
    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (saving || !validate()) return;
        if (form.elements.enabled.checked && form.dataset.groupConfigured !== 'true') {
            showError('请先在群管理中配置机器人 Token 和群 ID。');
            return;
        }
        const data = new FormData(form);
        data.set('enabled', String(form.elements.enabled.checked));
        saving = true;
        const label = button.textContent;
        button.textContent = '保存中…';
        form.setAttribute('aria-busy', 'true');
        form.querySelectorAll('input, textarea, button').forEach(input => { input.disabled = true; });
        try {
            const response = await fetch(form.action, {
                method: 'POST', body: data, credentials: 'same-origin',
                headers: { Accept: 'application/json' },
            });
            if (response.status === 401) {
                window.location.replace('/login');
                return;
            }
            const result = await response.json();
            if (!response.ok) {
                if (response.status === 409) {
                    form.dataset.groupConfigured = 'false';
                    form.elements.enabled.checked = false;
                    updatePreview();
                }
                showError(typeof result.detail === 'string' ? result.detail : '保存失败，请检查设置。');
                return;
            }
            state.textContent = '已保存';
            showError(result.message, true);
        } catch {
            showError('请求失败，请检查网络后重试。');
        } finally {
            saving = false;
            button.textContent = label;
            form.setAttribute('aria-busy', 'false');
            form.querySelectorAll('input, textarea, button').forEach(input => { input.disabled = false; });
        }
    });
    updatePreview();
}

function validateSeconds(field, min, max, message) {
    if (!/^[0-9]+$/.test(field.value) || Number(field.value) < min || Number(field.value) > max) {
        field.focus();
        showError(message);
        return false;
    }
    return true;
}

bindSettingsForm(verificationForm, '#save-verification', '#verification-save-state', updateVerificationPreview, () =>
    validateSeconds(verificationForm.elements.timeout_seconds, 10, 3600, '验证超时需为 10～3600 秒的整数。'));
bindSettingsForm(welcomeForm, '#save-welcome', '#welcome-save-state', updateWelcomePreview, () => {
    const message = welcomeForm.elements.welcome_message;
    if (!message.value.trim() || message.value.length > 2000) {
        message.focus();
        showError('欢迎语不能为空，最多 2000 个字符。');
        return false;
    }
    return validateSeconds(welcomeForm.elements.delete_after_seconds, 0, 86400, '欢迎语删除延时需为 0～86400 秒的整数。');
});
