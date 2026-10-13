"""首页展示数据集中生成，模板只负责布局，不发起 Telegram 请求。"""
from dao.models import (
    AISettings,
    EventLog,
    GroupConfig,
    KeywordSettings,
    VerificationSettings,
    WelcomeSettings,
)
from utils.dates import display_time
from services.activity import today_counts
from services.join_runtime import bot_key
from services.runtime_health import health_snapshot, execution_allowed
from services.configuration import (
    ai_is_enabled,
    keyword_is_enabled,
    verification_is_enabled,
    welcome_is_enabled,
)


def feature_state(title, icon, color, href, description, enabled):
    return {
        'title': title,
        'icon': icon,
        'color': color,
        'href': href,
        'description': description,
        'enabled': enabled,
        'status': '已开启设置' if enabled else '未启用',
    }


async def build_dashboard():
    group = await GroupConfig.get_or_none(id=1)
    verification = await VerificationSettings.get_or_none(id=1)
    welcome = await WelcomeSettings.get_or_none(id=1)
    keyword = await KeywordSettings.get_or_none(id=1)
    ai = await AISettings.get_or_none(id=1)
    configured = bool(group and group.bot_token and group.chat_id)
    check = group.check_result if configured and isinstance(group.check_result, dict) else {}
    checks = check.get('checks') or []
    passed = {
        item.get('label')
        for item in checks
        if isinstance(item, dict) and item.get('ok') is True
    }
    detected = bool(check)
    detection_ok = check.get('ok') is True
    if not configured:
        status = '等待配置'
        bot_state = '未配置'
        bot_note = '连接你的 Telegram Bot'
        group_name = '未绑定'
        group_note = '绑定需要守护的群组'
    else:
        if detection_ok:
            status = '最近检测通过'
        elif detected:
            status = '最近检测未通过'
        else:
            status = '尚未检测'
        if '机器人连接' in passed:
            bot_state = '最近连接正常'
        else:
            bot_state = '检测未通过' if detected else '已配置'
        bot_note = check.get('bot_name') or '请到群管理查看连接检测'
        group_name = check.get('group_name') or '已绑定'
        group_note = group.chat_id

    # 先确认群配置和各自必填项，再读取开关；展示文案不参与业务判断。
    verification_enabled = verification_is_enabled(verification, configured)
    welcome_enabled = welcome_is_enabled(welcome, configured)
    keyword_enabled = keyword_is_enabled(keyword, configured)
    ai_enabled = ai_is_enabled(ai, configured)
    keyword_count = len(keyword.keywords) if keyword else 0
    features = [
        feature_state(
            '入群验证', 'verify.svg', 'blue', '/verification',
            '验证新成员身份，减少机器人与恶意账号', verification_enabled,
        ),
        feature_state(
            '关键词拦截', 'block.svg', 'orange', '/antispam',
            f'已保存 {keyword_count} 个关键词，包含即命中', keyword_enabled,
        ),
        feature_state(
            'AI 识别', 'rules.svg', 'green', '/antispam',
            '通过配置的 AI 模型识别广告与垃圾消息', ai_enabled,
        ),
        feature_state(
            '欢迎消息', 'welcome.svg', 'violet', '/verification#welcome-settings',
            '验证通过后发送；关闭验证时入群即发送', welcome_enabled,
        ),
    ]
    enabled_count = sum(item['enabled'] for item in features)
    if not configured:
        binding_state = '等待绑定'
        next_label = '开始配置'
    elif not detection_ok:
        binding_state = '请检测连接与权限'
        next_label = '检查群配置'
    else:
        binding_state = '最近检测通过'
        next_label = '管理入群设置'
    steps = [
        {
            'title': '连接机器人',
            'description': '从 BotFather 获取 Bot Token',
            'state': '已配置' if configured else '等待配置',
            'current': not configured,
        },
        {
            'title': '绑定 Telegram 群组',
            'description': '将机器人加入目标群并设为管理员',
            'state': binding_state,
            'current': configured and not detection_ok,
        },
        {
            'title': '开启防护规则',
            'description': '按需配置验证与反垃圾策略',
            'state': f'已开启 {enabled_count} 项设置' if enabled_count else '等待配置',
            'current': detection_ok and not enabled_count,
        },
    ]
    recent = await EventLog.all().order_by('-created_at', '-id').limit(5)
    events = [
        {
            'time': display_time(item.created_at),
            'type': item.event_type,
            'nickname': item.nickname,
            'user_id': str(item.user_id) if item.user_id is not None else '',
            'content': item.content,
            'result': item.result,
        }
        for item in recent
    ]
    protection_note = '完成机器人与群组配置后，即可开启群组防护。'
    if configured:
        protection_note = f'已开启 {enabled_count} 项设置 入群验证 欢迎语与广告拦截按配置执行'
    members_today, blocked_today = await today_counts(bot_key(group.bot_token), group.chat_id) if configured else ('—', '—')
    health = await health_snapshot()
    if configured and enabled_count and not await execution_allowed(group):
        protection_note = f'已保存 {enabled_count} 项启用设置 当前暂停执行 等待连接恢复'
    elif configured and not enabled_count:
        protection_note = '群组防护已关闭 请按需开启入群与广告拦截设置'
    status = health['label']
    bot_state = health['label']
    return {
        'configured': configured,
        'status': status,
        'health': health,
        'bot_state': bot_state,
        'bot_note': bot_note,
        'group_name': group_name,
        'group_note': group_note,
        'features': features,
        'enabled_count': enabled_count,
        'steps': steps,
        'events': events,
        'setup_label': '管理群配置' if configured else '配置机器人',
        'next_href': '/verification' if detection_ok else '/group',
        'next_label': next_label,
        'members_today': members_today,
        'blocked_today': blocked_today,
        'stats_note': '北京时间今日已接收的群事件' if configured else '配置群组后开始统计',
        'protection_note': protection_note,
    }
