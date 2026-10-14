// 保留浏览器原生跳转 顶部进度条仅提供等待反馈 不拦截请求
(() => {
    const storageKey = 'blackcatguard-navigation';
    const root = document.documentElement;
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const bar = document.createElement('div');
    bar.className = 'page-progress';
    bar.setAttribute('role', 'progressbar');
    bar.setAttribute('aria-label', '页面加载中');
    bar.hidden = true;
    document.body.prepend(bar);
    let interval;
    let watchdog;
    let finishTimer;
    let progress = 0;

    function remember(value) {
        try {
            if (value) sessionStorage.setItem(storageKey, JSON.stringify(value));
            else sessionStorage.removeItem(storageKey);
        } catch { /* 禁用存储时仍支持当前页面的进度反馈 */ }
    }
    function readPending() {
        try {
            const value = JSON.parse(sessionStorage.getItem(storageKey));
            return value && Date.now() - value.startedAt >= 0 && Date.now() - value.startedAt < 60000 ? value : null;
        } catch { return null; }
    }
    function clearTimers() {
        clearInterval(interval);
        clearTimeout(watchdog);
        clearTimeout(finishTimer);
    }
    function reset() {
        clearTimers();
        remember(null);
        bar.hidden = true;
        bar.classList.remove('is-complete', 'is-finishing');
        root.removeAttribute('data-page-arrival');
        progress = 0;
    }
    function start(pending = null) {
        clearTimers();
        bar.hidden = false;
        bar.classList.remove('is-complete', 'is-finishing');
        progress = pending ? Math.min(0.92, Math.max(0, Number(pending.progress) || 0)) : 0;
        bar.style.setProperty('--page-progress', progress);
        const startedAt = pending ? pending.startedAt : Date.now();
        remember({ startedAt, progress });
        // 从零连续推进 越接近完成越慢 避免半秒一次的阶梯跳动
        const initialProgress = progress;
        const resumedAt = Date.now();
        interval = setInterval(() => {
            progress = initialProgress + (0.92 - initialProgress) * (1 - Math.exp(-(Date.now() - resumedAt) / 2500));
            bar.style.setProperty('--page-progress', progress);
            remember({ startedAt, progress });
        }, 100);
        // 请求取消或失败时不让旧页面一直保留进度条
        watchdog = setTimeout(reset, 30000);
    }
    function finish() {
        clearTimers();
        remember(null);
        bar.classList.add('is-finishing');
        bar.style.setProperty('--page-progress', 1);
        finishTimer = setTimeout(() => {
            bar.classList.add('is-complete');
            finishTimer = setTimeout(reset, reducedMotion ? 0 : 220);
        }, reducedMotion ? 0 : 420);
    }
    function destination(value, target) {
        if (target && target.toLowerCase() !== '_self') return null;
        try {
            const url = new URL(value, window.location.href);
            if (!['http:', 'https:'].includes(url.protocol) || url.origin !== window.location.origin) return null;
            // 页内锚点和当前页面链接不产生新的页面等待
            if (url.pathname === location.pathname && url.search === location.search) return null;
            return url;
        } catch { return null; }
    }

    document.addEventListener('click', event => {
        if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        const link = event.target.closest('a[href]');
        if (!link || link.hasAttribute('download') || !destination(link.href, link.target)) return;
        start();
    });
    document.addEventListener('submit', event => {
        const form = event.target;
        if (event.defaultPrevented || form.method.toLowerCase() === 'dialog') return;
        const submitter = event.submitter;
        const target = submitter?.getAttribute('formtarget') || form.target;
        const action = submitter?.getAttribute('formaction') || form.action;
        // GET 表单分页会提交到当前路径 仍然需要加载反馈
        if (destination(action, target) || (!target && new URL(action, location.href).origin === location.origin)) start();
    });
    window.addEventListener('pagehide', clearTimers);
    window.addEventListener('pageshow', event => { if (event.persisted) reset(); });
    document.addEventListener('keydown', event => { if (event.key === 'Escape') reset(); });

    const pending = readPending();
    if (pending) {
        start(pending);
        root.setAttribute('data-page-arrival', 'true');
        // 先绘制上一页进度 再平滑补满 防止新页面直接显示满条
        const finishAfterPaint = () => requestAnimationFrame(() => requestAnimationFrame(() => {
            if (!bar.hidden) finish();
        }));
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', finishAfterPaint, { once: true });
        else finishAfterPaint();
    } else remember(null);
})();
