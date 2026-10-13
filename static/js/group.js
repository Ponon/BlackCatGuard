// 群管理页只负责表单和结果展示，Telegram 检测由后端执行。
const groupForm = document.querySelector('#group-form');
const saveButton = document.querySelector('#save-config');
const checkButton = document.querySelector('#check-config');
let configured = !checkButton.disabled;
let dirty = false;
let busy = false;
let saving = false;
const configSnapshot = () => JSON.stringify([groupForm.elements.bot_token.value, groupForm.elements.chat_id.value]);
let savedConfig = configSnapshot();

function setBusy(value) {
    busy = value;
    saveButton.disabled = saving;
    saveButton.setAttribute('aria-busy', String(saving));
    checkButton.setAttribute('aria-busy', String(value && !saving));
    checkButton.disabled = value || !configured || dirty;
    groupForm.setAttribute('aria-busy', String(value));
    // 请求期间冻结输入，避免保存结果覆盖用户刚输入的新内容。
    groupForm.querySelectorAll('.config-input').forEach(input => {
        input.readOnly = value;
    });
}

function clearResult(message) {
    document.querySelector('#check-status').textContent = '尚未检测';
    document.querySelector('#check-message').textContent = message;
    document.querySelector('#check-list').replaceChildren();
    document.querySelector('#bot-name').textContent = '—';
    document.querySelector('#group-name').textContent = '—';
}

function displayResult(result) {
    document.querySelector('#check-status').textContent = result.ok ? '检测通过' : '检测未通过';
    document.querySelector('#check-message').textContent = result.message;
    document.querySelector('#bot-name').textContent = result.bot_name || '—';
    document.querySelector('#group-name').textContent = result.group_name || '—';
    const list = document.querySelector('#check-list');
    list.replaceChildren();
    // 通过 textContent 写入外部群名等数据，避免 HTML 注入。
    for (const item of result.checks || []) {
        const row = document.createElement('li');
        row.className = item.ok ? 'check-item' : 'check-item check-item--error';
        const heading = document.createElement('div');
        heading.className = 'check-item-heading';
        const label = document.createElement('span');
        label.textContent = item.label;
        const tag = document.createElement('span');
        tag.className = `check-tag ${item.ok ? 'check-tag--success' : 'check-tag--error'}`;
        tag.textContent = item.ok ? '通过' : '未通过';
        const detail = document.createElement('p');
        detail.textContent = item.detail;
        heading.append(label, tag);
        row.appendChild(heading);
        if (!item.ok) row.appendChild(detail);
        list.appendChild(row);
    }
}

async function postForm(url, data) {
    const response = await fetch(url, {
        method: 'POST', body: data, credentials: 'same-origin',
        headers: { Accept: 'application/json' },
    });
    if (response.status === 401) {
        window.location.replace('/login');
        throw new Error('登录已过期，请重新登录。');
    }
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '操作失败，请稍后重试。');
    return result;
}

// 修改后的内容须先保存，检测始终对应已保存配置。
groupForm.addEventListener('input', () => {
    dirty = configSnapshot() !== savedConfig;
    document.querySelector('#config-status').textContent = dirty ? '有未保存修改' : (configured ? '已保存' : '未配置');
    clearResult(dirty ? '配置已修改，请保存后重新检测。' : '点击下方按钮检测当前配置。');
    setBusy(busy);
});

groupForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy) {
        showError('正在检测配置，请稍后再保存。');
        return;
    }
    const chatId = groupForm.elements.chat_id.value.trim();
    const token = groupForm.elements.bot_token.value.trim();
    const clearing = !token && !chatId;
    if (!clearing && !token) {
        groupForm.elements.bot_token.focus();
        showError('请输入 Bot Token。');
        return;
    }
    if (!clearing && !/^-[1-9][0-9]{0,19}$/.test(chatId)) {
        groupForm.elements.chat_id.focus();
        showError('请输入有效的群数字 ID');
        return;
    }
    saving = true;
    setBusy(true);
    saveButton.textContent = clearing ? '清除中…' : '保存并检测中…';
    try {
        const result = await postForm(groupForm.action, new FormData(groupForm));
        configured = result.configured;
        dirty = false;
        groupForm.elements.bot_token.value = result.bot_token;
        setTokenVisible(false);
        groupForm.elements.bot_token.placeholder = '请输入 BotFather 提供的 Token';
        groupForm.elements.chat_id.value = result.chat_id;
        savedConfig = configSnapshot();
        document.querySelector('#config-status').textContent = configured ? '已保存' : '未配置';
        if (result.check_result) displayResult(result.check_result);
        else clearResult('保存配置后，点击下方按钮开始检测。');
        showError(result.message, true);
    } catch (error) {
        showError(error instanceof TypeError ? '网络请求失败请稍后重试' : error.message);
    } finally {
        saveButton.textContent = '保存配置';
        saving = false;
        setBusy(false);
    }
});

checkButton.addEventListener('click', async () => {
    if (busy || dirty || !configured) return;
    setBusy(true);
    checkButton.textContent = '检测中…';
    document.querySelector('#check-status').textContent = '检测中…';
    document.querySelector('#check-message').textContent = '正在检测已保存的配置，请稍候。';
    try {
        const data = new FormData();
        data.set('csrf_token', groupForm.elements.csrf_token.value);
        const result = await postForm('/group/check', data);
        // 请求期间如果用户修改了输入，不把旧配置结果显示为当前结果。
        if (!dirty) displayResult(result);
        showError(result.message, result.ok);
    } catch (error) {
        clearResult('本次检测未完成，请重试。');
        document.querySelector('#check-status').textContent = '检测未完成';
        showError(error instanceof TypeError ? '网络请求失败请稍后重试' : error.message);
    } finally {
        checkButton.textContent = '检测配置';
        setBusy(false);
    }
});


// 显隐按钮只改变呈现方式，不修改 Token，也不触发表单提交。
const tokenToggle = document.querySelector('#toggle-token');
const tokenEye = document.querySelector('#token-eye');
function setTokenVisible(visible) {
    groupForm.elements.bot_token.type = visible ? 'text' : 'password';
    tokenToggle.setAttribute('aria-pressed', String(visible));
    tokenToggle.setAttribute('aria-label', visible ? '隐藏 Token' : '显示 Token');
    tokenEye.src = visible ? tokenEye.dataset.hide : tokenEye.dataset.show;
}
tokenToggle.addEventListener('click', () => {
    setTokenVisible(groupForm.elements.bot_token.type === 'password');
});
