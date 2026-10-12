// 打开确认框不执行删除；仅确认提交时调用清空接口。
const clearDialog = document.querySelector('#clear-events-dialog');
const clearForm = document.querySelector('#clear-events-form');
let clearing = false;
document.querySelector('#clear-events').addEventListener('click', () => clearDialog.showModal());
document.querySelector('#cancel-clear-events').addEventListener('click', () => clearDialog.close());
clearDialog.addEventListener('cancel', event => {
    if (clearing) event.preventDefault();
});
clearForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (clearing) return;
    clearing = true;
    const data = new FormData(clearForm);
    const button = clearForm.querySelector('[type="submit"]');
    button.textContent = '清空中…';
    clearForm.querySelectorAll('button').forEach(item => { item.disabled = true; });
    try {
        const response = await fetch(clearForm.action, {
            method: 'POST', body: data, credentials: 'same-origin',
            headers: { Accept: 'application/json' },
        });
        if (response.status === 401) {
            window.location.replace('/login');
            return;
        }
        const result = await response.json();
        if (!response.ok) {
            clearDialog.close();
            showError(typeof result.detail === 'string' ? result.detail : '清空失败，请重试。');
            return;
        }
        const url = new URL(window.location.href);
        url.searchParams.set('page', '1');
        window.location.replace(url.toString());
    } catch {
        clearDialog.close();
        showError('网络请求失败请稍后重试');
    } finally {
        clearing = false;
        button.textContent = '确认清空';
        clearForm.querySelectorAll('button').forEach(item => { item.disabled = false; });
    }
});
