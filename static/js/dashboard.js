// 读取服务端状态，提示和外部返回内容均按纯文本展示。
document.querySelectorAll('[data-notice]').forEach(button => {
    button.addEventListener('click', () => showError(button.dataset.notice));
});

// 从浏览器往返缓存恢复页面时重新读取配置，避免展示其他页面修改前的开关状态。
window.addEventListener('pageshow', event => {
    if (event.persisted) window.location.reload();
});

// 串行轮询，超时后显示未知状态，不把旧的在线状态继续展示。
async function refreshRuntimeStatus() {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    try {
        const response = await fetch('/runtime/status', { credentials: 'same-origin', cache: 'no-store', signal: controller.signal });
        if (response.status === 401) {
            window.location.replace('/login');
            return;
        }
        if (!response.ok) throw new Error('status unavailable');
        const { health, dashboard } = await response.json();
        const status = document.querySelector('#runtime-status');
        status.textContent = health.label;
        status.title = health.checked_at ? `检测时间 ${new Date(health.checked_at * 1000).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })} ${health.reason}` : health.reason;
        updateConnectionDisplay(health);
        document.querySelector('#runtime-protection-note').textContent = dashboard.protection_note;
        for (const [id, value] of Object.entries({
            'runtime-bot-note': dashboard.bot_note, 'runtime-group-name': dashboard.group_name,
            'runtime-group-note': dashboard.group_note, 'runtime-members-today': dashboard.members_today,
            'runtime-blocked-today': dashboard.blocked_today,
        })) document.getElementById(id).textContent = value;
        document.querySelectorAll('[data-stats-note]').forEach(element => { element.textContent = dashboard.stats_note; });
        document.querySelectorAll('.steps li').forEach((element, index) => {
            element.querySelector('.step-tag').textContent = dashboard.steps[index].state;
            element.querySelector('.step-number').classList.toggle('step-number--current', dashboard.steps[index].current);
        });
        const next = document.querySelector('.onboarding > a');
        next.href = dashboard.next_href;
        next.firstChild.textContent = dashboard.next_label;
        updateRecentEvents(dashboard.events);

        dashboard.features.forEach((feature, index) => {
            const element = document.querySelector(`[data-feature-status="${index}"]`);
            element.textContent = feature.status;
            element.classList.toggle('feature-status--enabled', feature.enabled);
        });
    } catch {
        document.querySelector('#runtime-status').textContent = '状态获取失败';
        updateConnectionDisplay({ tone: 'error', connection_label: '连接状态未知', latency_label: '—' });
    } finally {
        clearTimeout(timeout);
        setTimeout(refreshRuntimeStatus, 10000);
    }
}
if (document.querySelector('#runtime-status')) refreshRuntimeStatus();


let recentEventsSnapshot = '';
function updateRecentEvents(events) {
    const snapshot = JSON.stringify(events);
    if (snapshot === recentEventsSnapshot) return;
    recentEventsSnapshot = snapshot;
    const body = document.querySelector('#runtime-events-body');
    const fragment = document.createDocumentFragment();
    for (const event of events.slice(0, 5)) {
        const row = document.createElement('tr');
        for (const key of ['time', 'type', 'nickname', 'username', 'content', 'result']) {
            const cell = document.createElement('td');
            cell.textContent = event[key] || '—';
            if (key === 'time') cell.className = 'recent-events-time';
            if (key === 'content' || key === 'result') cell.className = `recent-events-${key}`;
            row.appendChild(cell);
        }
        fragment.appendChild(row);
    }
    body.replaceChildren(fragment);
    document.querySelector('#runtime-events-table').hidden = events.length === 0;
    document.querySelector('#runtime-events-empty').hidden = events.length !== 0;
}


function updateConnectionDisplay(health) {
    const tone = ['good', 'slow', 'warning', 'error', 'checking', 'idle'].includes(health.tone) ? health.tone : 'idle';
    document.querySelector('#runtime-connection').dataset.tone = tone;
    document.querySelector('#runtime-bot-state').textContent = health.connection_label;
    document.querySelector('#runtime-latency').textContent = health.latency_label;
}
