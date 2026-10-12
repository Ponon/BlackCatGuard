// 三个表单独立提交，配置和测试均不刷新页面。
function bindSpamForm(form, onSuccess) {
    const button = form.querySelector('button[type="submit"]');
    let busy = false;
    const toggle = form.elements.enabled;
    const state = form.querySelector('[data-save-state]');
    if (toggle) {
        toggle.addEventListener('change', () => {
            if (toggle.checked && form.dataset.groupConfigured !== 'true') {
                toggle.checked = false;
                showError('请先在群管理中配置机器人 Token 和群 ID。');
            }
        });
    }
    form.addEventListener('input', () => {
        if (state) state.textContent = '有未保存修改';
    });
    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (busy) return;
        const testResult = form.id === 'spam-test-form' ? document.querySelector('#spam-test-result') : null;
        if (testResult) {
            // 保留结果区域高度，避免重新测试时卡片先收起再展开。
            testResult.style.minHeight = `${testResult.getBoundingClientRect().height}px`;
            testResult.hidden = false;
            testResult.textContent = '正在判断，请稍候…';
            testResult.setAttribute('aria-busy', 'true');
        }
        const data = new FormData(form);
        if (toggle) data.set('enabled', String(toggle.checked));
        if (form.elements.clear_api_key) data.set('clear_api_key', String(form.elements.clear_api_key.checked));
        const label = button.textContent;
        busy = true;
        button.textContent = form.id === 'spam-test-form' ? '判断中…' : '保存中…';
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
                if (testResult) testResult.textContent = '本次测试未完成，请检查提示后重试。';
                if (response.status === 409 && toggle) {
                    form.dataset.groupConfigured = 'false';
                    toggle.checked = false;
                }
                showError(typeof result.detail === 'string' ? result.detail : '操作失败，请检查配置。');
                return;
            }
            onSuccess(result);
            if (state) state.textContent = '已保存';
        } catch {
            if (testResult) testResult.textContent = '本次测试失败，请重试。';
            showError('请求失败，请检查网络后重试。');
        } finally {
            if (testResult) testResult.setAttribute('aria-busy', 'false');
            busy = false;
            button.textContent = label;
            form.setAttribute('aria-busy', 'false');
            form.querySelectorAll('input, textarea, button').forEach(input => { input.disabled = false; });
        }
    });
}

const keywordForm = document.querySelector('#keyword-form');
bindSpamForm(keywordForm, result => {
    keywordForm.elements.keywords.value = result.keywords;
    showError(result.message, true);
});

const aiForm = document.querySelector('#ai-form');
bindSpamForm(aiForm, result => {
    aiForm.elements.api_key.value = '';
    aiForm.elements.api_key.placeholder = result.has_api_key ? '已保存，留空保留原密钥' : '本地无鉴权服务可留空';
    aiForm.elements.clear_api_key.checked = false;
    aiForm.elements.base_url.value = result.base_url;
    showError(result.message, true);
});

bindSpamForm(document.querySelector('#spam-test-form'), result => {
    const container = document.querySelector('#spam-test-result');
    container.hidden = false;
    container.replaceChildren();
    const title = document.createElement('h3');
    title.textContent = result.message;
    container.appendChild(title);
    // 外部 AI 返回内容和命中词使用 textContent，不能作为 HTML 注入。
    for (const check of result.checks) {
        const row = document.createElement('p');
        row.textContent = `${check.source} · ${check.status}：${check.detail}`;
        container.appendChild(row);
    }
});

// 白名单独立保存，不依赖群配置或拦截开关。
const whitelistForm = document.querySelector('#whitelist-form');
bindSpamForm(whitelistForm, result => {
    whitelistForm.elements.entries.value = result.entries;
    showError(result.message, true);
});
