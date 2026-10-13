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
			if (input.type === 'checkbox') {
                if (input.required && !input.checked) {
                    input.focus();
                    showError('请阅读并同意使用须知');
                    return;
                }
                continue;
            }
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
					if (data.message) sessionStorage.setItem('auth_success', JSON.stringify({ message: data.message, path: data.redirect }));
				} catch {
					// 无法跨页保存时，先展示成功提示再跳转。
					if (data.message) showError(data.message, true);
					await new Promise(resolve => setTimeout(resolve, 2000));
				}
				window.location.replace(data.redirect);
				return;
			}
			showError(data.error || (typeof data.detail === 'string' ?
				data.detail : '提交失败，请检查输入后重试。'));
		} catch {
			showError('网络请求失败请稍后重试');
		} finally {
			submitting = false;
			button.disabled = false;
			button.removeAttribute('aria-busy');
		}
	});
});
// 密码可见性只影响当前输入框，不改变提交值。
document.querySelectorAll('[data-password]').forEach(button => {
    button.addEventListener('click', () => {
        const input = document.getElementById(button.dataset.password);
        const visible = input.type === 'password';
        input.type = visible ? 'text' : 'password';
        button.setAttribute('aria-pressed', String(visible));
        button.setAttribute('aria-label', visible ? '隐藏密码' : '显示密码');
        button.querySelector('img').src = visible ? '/static/images/eye-off.svg' : '/static/images/eye.svg';
        updateCharacters();
    });
});
function updateCharacters() {
    document.body.classList.toggle('is-private', Boolean(document.activeElement?.closest('.password-field') || document.querySelector('.password-field input[type="text"]')));
    document.body.classList.toggle('is-typing', Boolean(document.activeElement?.matches('.field input')));
}
document.querySelectorAll('.field input').forEach(input => {
    input.addEventListener('focus', updateCharacters);
    input.addEventListener('blur', updateCharacters);
});

// 每帧最多更新一次；只处理指针坐标，不读取或保存密码内容。
(() => {
    const cats = [...document.querySelectorAll('.cat')];
    if (!cats.length) return;
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let frame = 0;
    let pointer = null;
    let blinkTimer = 0;
    let blinkEnd = 0;
    const reset = () => {
        pointer = null;
        cancelAnimationFrame(frame);
        frame = 0;
        cats.forEach(cat => {
            ['--lean', '--look-x', '--look-y'].forEach(name => cat.style.removeProperty(name));
        });
    };
    const render = () => {
        frame = 0;
        if (!pointer || reducedMotion.matches || document.hidden) return;
        cats.forEach(cat => {
            const box = cat.getBoundingClientRect();
            const x = Math.max(-1, Math.min(1, (pointer.x - box.left - box.width / 2) / (innerWidth / 2)));
            const y = Math.max(-1, Math.min(1, (pointer.y - box.top - 60) / (innerHeight / 2)));
            cat.style.setProperty('--lean', `${-x * 5}deg`);
            cat.style.setProperty('--look-x', `${x * 2.5}px`);
            cat.style.setProperty('--look-y', `${y * 3}px`);
        });
    };
    document.addEventListener('pointermove', event => {
        if (event.pointerType === 'touch' || reducedMotion.matches) return;
        pointer = {x: event.clientX, y: event.clientY};
        if (!frame) frame = requestAnimationFrame(render);
    }, {passive: true});
    document.documentElement.addEventListener('pointerleave', reset);
    window.addEventListener('blur', reset);
    const stopBlink = () => {
        clearTimeout(blinkTimer);
        clearTimeout(blinkEnd);
        document.body.classList.remove('is-blinking');
    };
    const scheduleBlink = () => {
        stopBlink();
        if (document.hidden || reducedMotion.matches) return;
        blinkTimer = setTimeout(() => {
            document.body.classList.add('is-blinking');
            blinkEnd = setTimeout(() => {
                document.body.classList.remove('is-blinking');
                scheduleBlink();
            }, 140);
        }, 3500 + Math.random() * 3000);
    };
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) reset();
        scheduleBlink();
    });
    reducedMotion.addEventListener('change', () => {
        reset();
        scheduleBlink();
    });
    scheduleBlink();
})();

// 悬停防抖、退场与打字共用取消机制，快速切换不会串入上一只猫的文字。
(() => {
    const scene = document.querySelector('.cat-scene');
    const bubble = scene?.querySelector('.cat-bubble');
    if (!bubble) return;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    const ghost = document.createElement('span');
    const text = document.createElement('span');
    ghost.className = 'bubble-measure';
    text.className = 'bubble-text';
    ghost.setAttribute('aria-hidden', 'true');
    text.setAttribute('aria-hidden', 'true');
    bubble.replaceChildren(ghost, text);
    let active = null;
    let pending = null;
    let showTimer = 0;
    let hideTimer = 0;
    let typingTimer = 0;
    let exitTimer = 0;
    const position = () => {
        if (!active) return;
        const center = active.offsetLeft + active.offsetWidth / 2;
        const left = Math.max(0, Math.min(scene.clientWidth - bubble.offsetWidth, center - bubble.offsetWidth / 2));
        bubble.style.left = `${left}px`;
        bubble.style.top = `${Math.max(0, active.offsetTop - bubble.offsetHeight - 42)}px`;
        bubble.style.setProperty('--tail-x', `${Math.max(24, Math.min(bubble.offsetWidth - 24, center - left))}px`);
    };
    const hide = () => {
        clearTimeout(showTimer);
        clearTimeout(typingTimer);
        clearTimeout(exitTimer);
        pending = null;
        active?.setAttribute('aria-expanded', 'false');
        active = null;
        bubble.classList.remove('is-visible', 'is-writing');
        exitTimer = setTimeout(() => { bubble.hidden = true; }, reduced.matches ? 0 : 220);
    };
    const show = cat => {
        clearTimeout(exitTimer);
        clearTimeout(typingTimer);
        active?.setAttribute('aria-expanded', 'false');
        active = cat;
        bubble.dataset.cat = ['back', 'middle', 'front', 'small'].find(name => cat.classList.contains(`cat-${name}`));
        pending = null;
        cat.setAttribute('aria-expanded', 'true');
        const message = cat.dataset.catMessage;
        ghost.textContent = message;
        text.textContent = '';
        bubble.setAttribute('aria-label', message);
        bubble.hidden = false;
        position();
        // 先建立初始样式，再启动淡入，避免首次显示直接跳到终态。
        void bubble.offsetWidth;
        bubble.classList.add('is-visible');
        if (reduced.matches) {
            text.textContent = message;
            return;
        }
        const characters = Array.from(message);
        let index = 0;
        bubble.classList.add('is-writing');
        const type = () => {
            text.textContent += characters[index++];
            if (index < characters.length) {
                const pause = /[，。？！～]/.test(characters[index - 1]) ? 160 : 36;
                typingTimer = setTimeout(type, pause);
            } else bubble.classList.remove('is-writing');
        };
        typingTimer = setTimeout(type, 140);
    };
    const enter = cat => {
        clearTimeout(hideTimer);
        clearTimeout(showTimer);
        if (active === cat) return;
        pending = cat;
        if (active) {
            active.setAttribute('aria-expanded', 'false');
            bubble.classList.remove('is-visible', 'is-writing');
            clearTimeout(typingTimer);
        }
        showTimer = setTimeout(() => show(cat), reduced.matches ? 0 : 180);
    };
    const leave = cat => {
        if (pending === cat) {
            clearTimeout(showTimer);
            pending = null;
        }
        if (active === cat) {
            clearTimeout(hideTimer);
            hideTimer = setTimeout(hide, 140);
        }
    };
    scene.querySelectorAll('[data-cat-message]').forEach(cat => {
        cat.addEventListener('pointerenter', event => {
            cat.classList.add('is-petted');
            if (event.pointerType !== 'touch') enter(cat);
        });
        cat.addEventListener('pointerleave', () => {
            cat.classList.remove('is-petted');
            leave(cat);
        });
        cat.addEventListener('focus', () => { cat.classList.add('is-petted'); enter(cat); });
        cat.addEventListener('blur', () => { cat.classList.remove('is-petted'); leave(cat); });
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') hide();
    });
    window.addEventListener('blur', hide);
    document.addEventListener('visibilitychange', () => { if (document.hidden) hide(); });
    window.addEventListener('resize', position);
    reduced.addEventListener('change', hide);
})();
