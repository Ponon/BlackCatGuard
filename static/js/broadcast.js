// 内容库与循环任务独立保存，只有任务拥有启用开关。
const initialBroadcast = JSON.parse(document.querySelector('#broadcast-data').textContent);
let contents = initialBroadcast.contents;
let tasks = initialBroadcast.tasks;
const contentForm = document.querySelector('#broadcast-content-form');
const taskForm = document.querySelector('#broadcast-task-form');
const buttonEditor = document.querySelector('#broadcast-button-editor');
const timeEditor = document.querySelector('#broadcast-time-editor');
const messageInput = contentForm.elements.message;
let buttonNumber = 0;
let timeNumber = 0;
let savingContent = false;
let savingTask = false;
let deletingBroadcast = false;
const togglingTasks = new Set();

function readButtons() {
    return Array.from(buttonEditor.querySelectorAll('.broadcast-button-row')).map(row => ({
        text: row.querySelector('[data-button-text]').value,
        url: row.querySelector('[data-button-url]').value,
        row: Number(row.querySelector('[data-button-row]').value),
    }));
}

function addButton(value = { text: '', url: '', row: 1 }) {
    const container = document.createElement('div');
    container.className = 'broadcast-button-row';
    const number = ++buttonNumber;
    for (const [key, title, type, placeholder] of [
        ['text', '按钮文字', 'text', '查看详情'],
        ['url', '链接地址', 'url', 'https://'],
        ['row', '所在行', 'number', '1'],
    ]) {
        const field = document.createElement('div');
        const label = document.createElement('label');
        const input = document.createElement('input');
        input.className = 'broadcast-input';
        input.id = `broadcast-button-${number}-${key}`;
        input.name = `button_${number}_${key}`;
        input.dataset[`button${key[0].toUpperCase() + key.slice(1)}`] = '';
        input.type = type;
        input.value = value[key];
        input.placeholder = placeholder;
        if (key === 'row') {
            input.min = '1';
            input.max = '8';
            input.step = '1';
        } else {
            input.maxLength = key === 'text' ? 64 : 2048;
        }
        label.htmlFor = input.id;
        label.textContent = title;
        field.append(label, input);
        container.appendChild(field);
    }
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'button';
    remove.textContent = '删除';
    remove.setAttribute('aria-label', '删除此链接按钮');
    remove.addEventListener('click', () => {
        container.remove();
        contentForm.dispatchEvent(new Event('input', { bubbles: true }));
    });
    container.appendChild(remove);
    buttonEditor.appendChild(container);
}

function updateContentPreview() {
    const buttons = readButtons();
    contentForm.elements.buttons.value = JSON.stringify(buttons);
    document.querySelector('#broadcast-buttons-empty').hidden = buttons.length > 0;
    const now = new Intl.DateTimeFormat('sv-SE', {
        timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
    }).format(new Date());
    document.querySelector('#broadcast-preview-message').textContent = messageInput.value.replace(/\{datetime\}/g, now) || '输入广播内容后在这里预览。';
    const preview = document.querySelector('#broadcast-preview-buttons');
    preview.replaceChildren();
    const rows = new Map();
    for (const button of buttons) {
        if (!rows.has(button.row)) rows.set(button.row, []);
        rows.get(button.row).push(button);
    }
    for (const [, values] of Array.from(rows).sort(([a], [b]) => a - b)) {
        const row = document.createElement('div');
        row.className = 'broadcast-preview-row';
        for (const button of values) {
            const link = document.createElement('a');
            link.className = 'broadcast-preview-link';
            link.textContent = button.text || '按钮文字';
            try {
                const url = new URL(button.url);
                if (['http:', 'https:'].includes(url.protocol) && !url.username && !url.password) {
                    link.href = url.href;
                    link.target = '_blank';
                    link.rel = 'noopener noreferrer';
                }
            } catch { /* 未填完整的链接仍可预览文字。 */ }
            row.appendChild(link);
        }
        preview.appendChild(row);
    }
}

function addTime(value = '') {
    const row = document.createElement('div');
    row.className = 'broadcast-time-row';
    const input = document.createElement('input');
    input.type = 'time';
    input.className = 'broadcast-input';
    input.name = `send_time_${++timeNumber}`;
    input.value = value;
    input.setAttribute('aria-label', '发送时间（北京时间）');
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'button';
    remove.textContent = '删除';
    remove.setAttribute('aria-label', '删除此发送时间');
    remove.addEventListener('click', () => {
        row.remove();
        taskForm.dispatchEvent(new Event('input', { bubbles: true }));
    });
    row.append(input, remove);
    timeEditor.appendChild(row);
}

function syncTaskFields() {
    const mode = taskForm.elements.schedule_mode.value;
    document.querySelector('#broadcast-interval').hidden = mode !== 'interval';
    document.querySelector('#broadcast-time').hidden = mode === 'interval';
    taskForm.elements.times.value = Array.from(timeEditor.querySelectorAll('input')).map(input => input.value).join(',');
}

function renderContentOptions() {
    const boundContent = taskForm.elements.content_id.value;
    const target = taskForm.elements.content_id;
    target.replaceChildren(new Option(contents.length ? '请选择广播内容' : '请先保存广播内容', '0'));
    for (const content of contents) {
        target.add(new Option(content.name, String(content.id)));
    }
    target.value = boundContent;
    renderContentList();
}

// 两个列表共用表头、空状态结构与样式。
function createBroadcastTable(container, columns, hasRows) {
    container.replaceChildren();
    const table = document.createElement('table');
    table.className = 'broadcast-list-table';
    const head = table.createTHead().insertRow();
    for (const title of columns) {
        const cell = document.createElement('th');
        cell.scope = 'col';
        cell.textContent = title;
        head.appendChild(cell);
    }
    const body = table.createTBody();
    if (!hasRows) {
        const cell = body.insertRow().insertCell();
        cell.colSpan = columns.length;
        cell.className = 'broadcast-list-empty';
        cell.textContent = '暂无数据';
    }
    container.appendChild(table);
    return body;
}

// 右侧列表负责编辑、删除入口，左侧表单负责内容维护。
function renderContentList() {
    const list = document.querySelector('#broadcast-content-list');
    const body = createBroadcastTable(list, ['广播内容', '操作'], contents.length > 0);
    for (const content of contents) {
        const row = body.insertRow();
        row.classList.toggle('is-selected', Number(contentForm.elements.content_id.value) === content.id);
        const name = row.insertCell();
        name.textContent = content.name;
        const actions = row.insertCell();
        actions.className = 'broadcast-task-actions';
        for (const [label, action] of [
            ['编辑', () => {
                loadContent(content);
                contentForm.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }],
            ['删除', () => confirmDeleteContent(content)],
        ]) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'text-button';
            button.textContent = label;
            button.setAttribute('aria-label', `${label}广播内容“${content.name}”`);
            button.addEventListener('click', () => {
                if (!savingContent && !deletingBroadcast) action();
            });
            actions.appendChild(button);
        }
    }
}

function loadContent(content) {
    contentForm.elements.content_id.value = content ? content.id : 0;
    contentForm.elements.name.value = content ? content.name : '';
    messageInput.value = content ? content.message : '';
    buttonEditor.replaceChildren();
    for (const button of content ? content.buttons : []) addButton(button);
    renderContentOptions();
    updateContentPreview();
    contentState.reset(Boolean(content));
}

function loadTask(task) {
    taskForm.elements.task_id.value = task ? task.id : 0;
    taskForm.elements.content_id.value = task ? task.content_id : 0;
    taskForm.elements.schedule_mode.value = task ? task.schedule_mode : 'daily';
    taskForm.elements.interval_minutes.value = task ? task.interval_minutes : 60;
    timeEditor.replaceChildren();
    for (const time of task && task.times.length ? task.times : ['']) addTime(time);
    syncTaskFields();
    taskState.reset(Boolean(task));
}

function renderTasks() {
    const container = document.querySelector('#broadcast-task-list');
    const body = createBroadcastTable(container, ['广播内容', '循环规则', '状态', '操作'], tasks.length > 0);
    for (const task of tasks) {
        const row = body.insertRow();
        const content = contents.find(item => item.id === task.content_id);
        row.insertCell().textContent = content ? content.name : '内容不存在';
        const rules = document.createElement('div');
        rules.className = 'broadcast-rule-list';
        const daily = task.schedule_mode === 'daily';
        const values = daily ? task.times : [String(task.interval_minutes)];
        for (const value of values) {
            const rule = document.createElement('div');
            rule.className = 'broadcast-rule';
            rule.setAttribute('aria-label', daily ? `每天 ${value} 发送` : `每隔 ${value} 分钟发送`);
            const type = document.createElement('span');
            type.className = 'broadcast-rule-type';
            type.textContent = daily ? 'DAY' : 'MIN';
            const time = document.createElement('span');
            time.className = 'broadcast-rule-time';
            time.textContent = value;
            rule.append(type, time);
            rules.appendChild(rule);
        }
        row.insertCell().appendChild(rules);
        const stateCell = row.insertCell();
        const state = document.createElement('div');
        state.className = 'broadcast-task-toggle';
        const label = document.createElement('label');
        label.className = 'broadcast-switch';
        const toggle = document.createElement('input');
        toggle.type = 'checkbox';
        toggle.checked = task.enabled;
        toggle.disabled = togglingTasks.has(task.id);
        toggle.setAttribute('role', 'switch');
        toggle.setAttribute('aria-label', `启用${content ? content.name : '广播'}的循环任务`);
        toggle.addEventListener('change', () => toggleBroadcastTask(task, toggle));
        const track = document.createElement('span');
        track.className = 'broadcast-switch-track';
        track.setAttribute('aria-hidden', 'true');
        label.append(toggle, track);
        state.appendChild(label);
        stateCell.appendChild(state);
        const actions = row.insertCell();
        actions.className = 'broadcast-task-actions';
        for (const [label, action] of [
            ['编辑', () => { if (!savingTask && !deletingBroadcast) loadTask(task); }],
            ['删除', () => confirmDeleteTask(task)],
        ]) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'text-button';
            button.textContent = label;
            button.addEventListener('click', action);
            actions.appendChild(button);
        }
    }
}

async function postBroadcast(url, data) {
    const response = await fetch(url, { method: 'POST', body: data, credentials: 'same-origin', headers: { Accept: 'application/json' } });
    if (response.status === 401) {
        window.location.replace('/login');
        throw new Error('登录已过期，请重新登录。');
    }
    const result = await response.json();
    if (!response.ok) {
        const error = new Error(typeof result.detail === 'string' ? result.detail : '操作失败，请检查设置。');
        error.status = response.status;
        throw error;
    }
    return result;
}

function setFormBusy(form, busy) {
    form.setAttribute('aria-busy', String(busy));
    form.querySelectorAll('input, textarea, select, button').forEach(field => { field.disabled = busy; });
}

function replaceItem(items, item) {
    const index = items.findIndex(value => value.id === item.id);
    if (index >= 0) items[index] = item;
    else items.push(item);
}

contentForm.addEventListener('input', updateContentPreview);
contentForm.addEventListener('change', updateContentPreview);
taskForm.addEventListener('input', syncTaskFields);
taskForm.addEventListener('change', syncTaskFields);
const contentState = trackFormState(contentForm, document.querySelector('#broadcast-content-state'));
const taskState = trackFormState(taskForm, document.querySelector('#broadcast-task-state'));
loadContent(null);
loadTask(null);
renderTasks();

document.querySelector('#new-broadcast-content').addEventListener('click', () => {
    if (!savingContent) loadContent(null);
});
document.querySelector('#new-broadcast-task').addEventListener('click', () => {
    if (!savingTask) loadTask(null);
});
document.querySelector('#add-broadcast-time').addEventListener('click', () => {
    if (timeEditor.querySelectorAll('input').length >= 48) {
        showError('每天最多设置 48 个时间点。');
        return;
    }
    addTime();
    taskForm.dispatchEvent(new Event('input', { bubbles: true }));
});
document.querySelector('#add-broadcast-button').addEventListener('click', () => {
    if (readButtons().length >= 24) {
        showError('最多添加 24 个链接按钮。');
        return;
    }
    addButton();
    contentForm.dispatchEvent(new Event('input', { bubbles: true }));
});
document.querySelector('#insert-datetime').addEventListener('click', () => {
    const variable = '{datetime}';
    if (messageInput.value.length - (messageInput.selectionEnd - messageInput.selectionStart) + variable.length > 4096) {
        showError('广播内容最多 4096 个字符。');
        return;
    }
    messageInput.setRangeText(variable, messageInput.selectionStart, messageInput.selectionEnd, 'end');
    messageInput.focus();
    messageInput.dispatchEvent(new Event('input', { bubbles: true }));
});
taskForm.addEventListener('group-unavailable', () => {
    tasks.forEach(task => { task.enabled = false; });
    renderTasks();
});

contentForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (savingContent || deletingBroadcast) return;
    updateContentPreview();
    const data = new FormData(contentForm);
    savingContent = true;
    setFormBusy(contentForm, true);
    const button = contentForm.querySelector('[type="submit"]');
    button.textContent = '保存中…';
    try {
        const result = await postBroadcast(contentForm.action, data);
        replaceItem(contents, result.content);
        // 保存后更新右侧列表，编辑区回到新建状态。
        loadContent(null);
        renderTasks();
        showError(result.message, true);
    } catch (error) {
        showError(error instanceof TypeError ? '网络请求失败请稍后重试' : error.message);
    } finally {
        savingContent = false;
        setFormBusy(contentForm, false);
        button.textContent = '保存内容';
    }
});

taskForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (savingTask || deletingBroadcast || togglingTasks.has(Number(taskForm.elements.task_id.value))) return;
    syncTaskFields();
    if (taskForm.elements.schedule_mode.value === 'daily') {
        const times = Array.from(timeEditor.querySelectorAll('input')).map(input => input.value);
        if (!times.length || times.some(time => !/^(?:[01][0-9]|2[0-3]):[0-5][0-9]$/.test(time))) {
            showError('请至少设置一个有效发送时间');
            return;
        }
    }
    const data = new FormData(taskForm);
    savingTask = true;
    setFormBusy(taskForm, true);
    const button = taskForm.querySelector('[type="submit"]');
    button.textContent = '保存中…';
    try {
        const result = await postBroadcast(taskForm.action, data);
        replaceItem(tasks, result.task);
        loadTask(result.task);
        renderTasks();
        showError(result.message, true);
    } catch (error) {
        if (error.status === 409) resetGroupDependentForms();
        showError(error instanceof TypeError ? '网络请求失败请稍后重试' : error.message);
    } finally {
        savingTask = false;
        setFormBusy(taskForm, false);
        button.textContent = '保存循环任务';
    }
});

// 开关只更新启用状态，不提交左侧尚未保存的循环规则。
async function toggleBroadcastTask(task, toggle) {
    if (savingTask || deletingBroadcast || togglingTasks.has(task.id)) {
        toggle.checked = task.enabled;
        return;
    }
    const enabled = toggle.checked;
    if (enabled && taskForm.dataset.groupConfigured !== 'true') {
        toggle.checked = false;
        showError('请先完成群管理配置');
        return;
    }
    const data = new FormData();
    data.set('task_id', task.id);
    data.set('enabled', String(enabled));
    data.set('csrf_token', taskForm.elements.csrf_token.value);
    togglingTasks.add(task.id);
    renderTasks();
    try {
        const result = await postBroadcast('/broadcast/task/toggle', data);
        replaceItem(tasks, result.task);
        showError(result.message, true);
    } catch (error) {
        if (error.status === 409) resetGroupDependentForms();
        if (error.status === 404) tasks = tasks.filter(item => item.id !== task.id);
        showError(error instanceof TypeError ? '网络请求失败请稍后重试' : error.message);
    } finally {
        togglingTasks.delete(task.id);
        renderTasks();
    }
}

const deleteDialog = document.querySelector('#broadcast-delete-dialog');
let pendingDelete;
function showDeleteDialog(description, action) {
    if (savingContent || savingTask || deletingBroadcast || togglingTasks.size) return;
    pendingDelete = action;
    document.querySelector('#broadcast-delete-description').textContent = description;
    deleteDialog.showModal();
}
function confirmDeleteTask(task) {
    const content = contents.find(item => item.id === task.content_id);
    showDeleteDialog(`删除“${content ? content.name : '该内容'}”的循环规则？广播内容会保留。`, async () => {
        const data = new FormData();
        data.set('csrf_token', taskForm.elements.csrf_token.value);
        data.set('task_id', task.id);
        const result = await postBroadcast('/broadcast/task/delete', data);
        tasks = tasks.filter(item => item.id !== task.id);
        if (Number(taskForm.elements.task_id.value) === task.id) loadTask(null);
        renderTasks();
        showError(result.message, true);
    });
}
function confirmDeleteContent(content) {
    showDeleteDialog(`删除广播内容“${content.name}”？已绑定任务的内容需要先解除绑定。`, async () => {
        const data = new FormData();
        data.set('csrf_token', contentForm.elements.csrf_token.value);
        data.set('content_id', content.id);
        const result = await postBroadcast('/broadcast/content/delete', data);
        contents = contents.filter(item => item.id !== content.id);
        if (Number(contentForm.elements.content_id.value) === content.id) loadContent(null);
        else renderContentOptions();
        renderTasks();
        showError(result.message, true);
    });
}
document.querySelector('#broadcast-cancel-delete').addEventListener('click', () => deleteDialog.close());
deleteDialog.addEventListener('cancel', event => { if (deletingBroadcast) event.preventDefault(); });
document.querySelector('#broadcast-confirm-delete').addEventListener('click', async () => {
    if (!pendingDelete || deletingBroadcast) return;
    deletingBroadcast = true;
    deleteDialog.querySelectorAll('button').forEach(button => { button.disabled = true; });
    try {
        await pendingDelete();
    } catch (error) {
        showError(error instanceof TypeError ? '网络请求失败请稍后重试' : error.message);
    } finally {
        deletingBroadcast = false;
        deleteDialog.close();
        deleteDialog.querySelectorAll('button').forEach(button => { button.disabled = false; });
        pendingDelete = null;
    }
});
