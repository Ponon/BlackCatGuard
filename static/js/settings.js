// 保存代理设置不刷新页面，所有提示使用顶部 toast。
const settingsForm = document.querySelector('#settings-form');
const settingsButton = document.querySelector('#save-settings');
const proxyInput = document.querySelector('#proxy-url');
let savingSettings = false;
const networkState = trackFormState(settingsForm, document.querySelector('#settings-save-state'));

settingsForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (savingSettings) return;
    savingSettings = true;
    settingsButton.disabled = true;
    settingsButton.textContent = '保存中…';
    proxyInput.readOnly = true;
    settingsForm.setAttribute('aria-busy', 'true');
    try {
        const response = await fetch(settingsForm.action, {
            method: 'POST', body: new FormData(settingsForm),
            credentials: 'same-origin', headers: { Accept: 'application/json' },
        });
        if (response.status === 401) {
            window.location.replace('/login');
            return;
        }
        const result = await response.json();
        if (!response.ok) {
            showError(typeof result.detail === 'string' ? result.detail : '保存失败，请检查代理地址。');
            proxyInput.focus();
            return;
        }
        proxyInput.value = result.proxy_url;
        networkState.markSaved();
        document.querySelector('#network-mode').textContent = result.proxy_url ? '指定代理' : '默认网络';
        showError(result.message, true);
    } catch {
        showError('网络请求失败请稍后重试');
    } finally {
        savingSettings = false;
        settingsButton.disabled = false;
        settingsButton.textContent = '保存设置';
        proxyInput.readOnly = false;
        settingsForm.setAttribute('aria-busy', 'false');
    }
});

// 修改用户名与密码使用独立表单，不影响已保存的网络设置。
const accountForm = document.querySelector('#account-form');
const accountButton = document.querySelector('#save-account');
let savingAccount = false;

accountForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (savingAccount) return;
    const fields = accountForm.elements;
    const username = fields.username.value.trim();
    const password = fields.new_password.value;
    let error = '';
    let invalidInput;
    if (!/^[A-Za-z0-9_]{3,32}$/.test(username)) {
        error = '用户名需为 3～32 位字母、数字或下划线。';
        invalidInput = fields.username;
    } else if (!fields.current_password.value) {
        error = '请输入当前密码。';
        invalidInput = fields.current_password;
    } else if ((password || fields.confirm_password.value) && (password.length < 8 || password.length > 128)) {
        error = '新密码需为 8～128 位。';
        invalidInput = fields.new_password;
    } else if (password !== fields.confirm_password.value) {
        error = '两次输入的新密码不一致。';
        invalidInput = fields.confirm_password;
    }
    if (error) {
        invalidInput.focus();
        showError(error);
        return;
    }
    savingAccount = true;
    accountButton.disabled = true;
    accountButton.textContent = '保存中…';
    accountForm.setAttribute('aria-busy', 'true');
    accountForm.querySelectorAll('.settings-input').forEach(input => { input.readOnly = true; });
    try {
        const response = await fetch(accountForm.action, {
            method: 'POST', body: new FormData(accountForm),
            credentials: 'same-origin', headers: { Accept: 'application/json' },
        });
        if (response.status === 401) {
            window.location.replace('/login');
            return;
        }
        const result = await response.json();
        if (!response.ok) {
            showError(typeof result.detail === 'string' ? result.detail : '账号信息保存失败。');
            return;
        }
        accountForm.querySelectorAll('input[type="password"]').forEach(input => { input.value = ''; });
        // 沿用跨页面一次性 toast，重新登录页显示保存成功提示。
        try {
            sessionStorage.setItem('auth_success', JSON.stringify({ message: result.message, path: result.redirect }));
        } catch {
            showError(result.message, true);
            await new Promise(resolve => setTimeout(resolve, 2000));
        }
        window.location.replace(result.redirect);
    } catch {
        showError('网络请求失败请稍后重试');
    } finally {
        savingAccount = false;
        accountButton.disabled = false;
        accountButton.textContent = '保存账号信息';
        accountForm.setAttribute('aria-busy', 'false');
        accountForm.querySelectorAll('.settings-input').forEach(input => { input.readOnly = false; });
    }
});

// 推送配置独立保存 测试不要求启用也不改变事件选择
const pushForm = document.querySelector('#push-form');
const pushState = trackFormState(pushForm, document.querySelector('#push-save-state'));
let pushBusy = false;

async function submitPush(test = false) {
    if (pushBusy) return;
    const data = new FormData(pushForm);
    if (test) {
        data.delete('provider');
        data.delete('bark_url');
        data.delete('events');
    }
    pushBusy = true;
    pushForm.setAttribute('aria-busy', 'true');
    const controls = Array.from(pushForm.elements);
    controls.forEach(control => { control.disabled = true; });
    const button = test ? document.querySelector('#test-push') : pushForm.querySelector('[type="submit"]');
    const text = button.textContent;
    button.textContent = test ? '测试中…' : '保存中…';
    try {
        const response = await fetch(test ? '/settings/push/test' : pushForm.action, {
            method: 'POST', body: data, credentials: 'same-origin', headers: { Accept: 'application/json' },
        });
        if (response.status === 401) {
            window.location.replace('/login');
            return;
        }
        const result = await response.json();
        if (!response.ok) {
            showError(typeof result.detail === 'string' ? result.detail : '推送操作失败');
            return;
        }
        if (!test) {
            pushForm.elements.provider.value = result.provider;
            pushForm.elements.bark_url.value = result.bark_url;
            pushForm.querySelectorAll('[name="events"]').forEach(input => { input.checked = result.events.includes(input.value); });
            pushState.markSaved();
        }
        showError(result.message, true);
    } catch {
        showError('网络请求失败请稍后重试');
    } finally {
        pushBusy = false;
        controls.forEach(control => { control.disabled = false; });
        button.textContent = text;
        pushForm.setAttribute('aria-busy', 'false');
    }
}

pushForm.addEventListener('submit', event => {
    event.preventDefault();
    submitPush();
});
document.querySelector('#test-push').addEventListener('click', () => submitPush(true));
document.querySelector('#toggle-bark-url').addEventListener('click', event => {
    const input = pushForm.elements.bark_url;
    const visible = input.type === 'password';
    input.type = visible ? 'text' : 'password';
    const button = event.currentTarget;
    button.setAttribute('aria-pressed', String(visible));
    button.setAttribute('aria-label', visible ? '隐藏推送地址' : '显示推送地址');
    const icon = document.querySelector('#bark-eye');
    icon.src = visible ? icon.dataset.hide : icon.dataset.show;
});


const logSettingsForm = document.querySelector('#log-settings-form');
logSettingsForm.addEventListener('submit', async event => {
    event.preventDefault();
    const button = logSettingsForm.querySelector('[type="submit"]');
    if (button.disabled) return;
    button.disabled = true;
    try {
        const response = await fetch(logSettingsForm.action, {
            method: 'POST', body: new FormData(logSettingsForm), credentials: 'same-origin',
            headers: { Accept: 'application/json' },
        });
        if (response.status === 401) { window.location.replace('/login'); return; }
        const result = await response.json();
        if (!response.ok) { showError(typeof result.detail === 'string' ? result.detail : '日志设置保存失败'); return; }
        document.querySelector('#log-retention-days').value = result.log_retention_days;
        showError(result.message, true);
    } catch { showError('网络请求失败请稍后重试'); }
    finally { button.disabled = false; }
});
