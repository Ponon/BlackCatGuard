// 登录和创建管理员的表单处理，提示功能由先加载的 toast.js 提供。
document.querySelectorAll('form.setup_input').forEach(form => {
	// 禁用浏览器气泡，统一使用顶部提示；脚本未加载时仍保留原生校验。
	form.noValidate = true;
	let submitting = false;
	form.addEventListener('submit', async event => {
		// 先校验输入，失败时不发送请求。
		event.preventDefault();
		if (submitting) return;
		const labels = { username: '账号', password: '密码', confirm_password: '确认密码' };
		for (const input of form.querySelectorAll('input:not([type="hidden"])')) {
			const label = labels[input.name] || '此项';
			const value = input.name === 'username' ? input.value.trim() : input.value;
			let error = '';
			if (input.required && !value) {
				error = `请输入${label}`;
			} else if (input.name === 'username' && !/^[A-Za-z0-9_]{3,32}$/.test(value)) {
				error = '账号需为 3～32 位字母、数字或下划线。';
			} else if (input.minLength > 0 && value.length < input.minLength) {
				error = `${label}至少需要 ${input.minLength} 位。`;
			} else if (input.maxLength > 0 && value.length > input.maxLength) {
				error = `${label}最多允许 ${input.maxLength} 位。`;
			} else if (input.name === 'confirm_password' && value !== form.elements.password.value) {
				error = '两次输入的密码不一致。';
			}
			if (error) {
				input.focus();
				showError(error);
				return;
			}
		}
		submitting = true;
		const button = form.querySelector('button[type="submit"]');
		button.disabled = true;
		button.setAttribute('aria-busy', 'true');
		try {
			const response = await fetch(form.action, {
				method: 'POST',
				body: new FormData(form),
				headers: {
					Accept: 'application/json'
				},
				credentials: 'same-origin',
			});
			// 登录或初始化成功后，按服务器返回的跳转地址打开页面。
			if (response.redirected) {
				window.location.replace(response.url);
				return;
			}
			const data = await response.json();
			if (response.ok && data.redirect) {
				try {
					sessionStorage.setItem('auth_success', JSON.stringify({ message: data.message, path: data.redirect }));
				} catch {
					// 无法跨页保存时，先展示成功提示再跳转。
					showError(data.message, true);
					await new Promise(resolve => setTimeout(resolve, 2000));
				}
				window.location.replace(data.redirect);
				return;
			}
			showError(data.error || (typeof data.detail === 'string' ?
				data.detail : '提交失败，请检查输入后重试。'));
		} catch {
			showError('请求失败，请检查网络后重试。');
		} finally {
			submitting = false;
			button.disabled = false;
			button.removeAttribute('aria-busy');
		}
	});
});