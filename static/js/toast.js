// 提示内容只保存在当前页面中，刷新不会再次显示旧错误。
function formatToastMessage(message) {
    // 只处理提示展示，保留接口地址与用户名等数据本身的有效字符。
    return String(message ?? '')
        .replace(/(\d)[～~](\d)/g, '$1 至 $2')
        .replace(/[，。！？；：、【】（）“”‘’「」『』]/g, '')
        .replace(/[,.!?;:]+$/g, '')
        .replace(/\s+/g, ' ')
        .trim();
}

// 服务端直接渲染的提示也采用相同格式。
document.querySelectorAll('.error_toast').forEach(toast => {
    toast.textContent = formatToastMessage(toast.textContent);
});

function showError(message, success = false) {
	document.querySelectorAll('.error_toast').forEach(item => item.remove());
	const toast = document.createElement('div');
	toast.className = success ? 'error_toast success_toast' : 'error_toast';
	toast.setAttribute('role', success ? 'status' : 'alert');
	toast.textContent = formatToastMessage(message);
	toast.addEventListener('animationend', () => toast.remove(), {
		once: true
	});
	document.body.appendChild(toast);
}

// 成功消息跨页面传递一次，读取后立即移除，避免刷新重复提示。
try {
	const saved = sessionStorage.getItem('auth_success');
	const notice = saved ? JSON.parse(saved) : null;
	if (notice && window.location.pathname === notice.path) {
		sessionStorage.removeItem('auth_success');
		showError(notice.message, true);
	}
} catch {
	// 浏览器禁用存储时不影响表单提交。
}

