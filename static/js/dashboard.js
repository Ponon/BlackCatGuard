// 尚未接入的功能明确提示当前状态，避免按钮点击无反馈。
document.querySelectorAll('[data-notice]').forEach(button => {
    button.addEventListener('click', () => showError(button.dataset.notice));
});
