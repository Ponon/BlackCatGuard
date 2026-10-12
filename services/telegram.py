"""Telegram API 通用请求与只读群配置检测。"""
import json
import socket
import time
import ssl
import errno
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request

from services.network import open_request


class TelegramError(Exception):
    def __init__(self, message, *, retryable=False, uncertain=False, retry_after=0):
        super().__init__(message)
        self.retryable = retryable
        self.uncertain = uncertain
        self.retry_after = retry_after


def call_api(token: str, method: str, *, proxy_url: str = "", request_timeout: int = 10, **params):
    retry_safe = method in {'getUpdates', 'getChat', 'getMe', 'getChatMember', 'deleteMessage', 'deleteMessages', 'restrictChatMember', 'banChatMember', 'unbanChatMember', 'answerCallbackQuery'}
    request = Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=urlencode(params).encode(),
        method="POST",
    )
    try:
        with open_request(request, proxy_url=proxy_url, timeout=request_timeout) as response:
            payload = json.load(response)
    except HTTPError as error:
        if error.code == 400 and method == 'deleteMessage':
            try:
                description = json.load(error).get('description', '')
            except (ValueError, TypeError, AttributeError):
                description = ''
            if 'message to delete not found' in description.lower():
                return True
        # 不返回包含 Token 的请求 URL 或 Telegram 原始异常。
        if error.code in (401, 404):
            raise TelegramError("机器人 Token 无效，请检查或重新生成。") from None
        if error.code in (400, 403):
            raise TelegramError("无法访问目标群，请检查群 ID、机器人是否入群及权限。") from None
        if error.code == 429:
            try:
                delay = int(json.load(error).get('parameters', {}).get('retry_after', 5))
            except (ValueError, TypeError, AttributeError):
                delay = 5
            raise TelegramError('Telegram 请求过于频繁', retryable=True, retry_after=max(1, delay)) from None
        if error.code == 409:
            raise TelegramError('机器人事件接收冲突 请检查是否存在其他接收程序或 Webhook') from None
        raise TelegramError('Telegram 服务暂时不可用', retryable=retry_safe and error.code >= 500, uncertain=method == 'sendMessage') from None
    except URLError as error:
        if isinstance(error.reason, ssl.SSLCertVerificationError):
            raise TelegramError("Telegram HTTPS 证书校验失败，请检查服务器证书环境或代理证书。") from None
        safe = isinstance(error.reason, (ConnectionRefusedError, socket.gaierror)) or getattr(error.reason, 'errno', None) in (errno.ENETUNREACH, errno.EHOSTUNREACH)
        raise TelegramError('连接 Telegram 失败请检查代理和网络', retryable=safe or retry_safe, uncertain=not safe and method == 'sendMessage') from None
    except ssl.SSLCertVerificationError:
        raise TelegramError("Telegram HTTPS 证书校验失败，请检查服务器证书环境或代理证书。") from None
    except (TimeoutError, socket.timeout):
        raise TelegramError('连接 Telegram 超时', retryable=retry_safe, uncertain=method == 'sendMessage') from None
    except OSError:
        raise TelegramError('连接 Telegram 中断', retryable=retry_safe, uncertain=method == 'sendMessage') from None
    except (ValueError, TypeError):
        raise TelegramError('Telegram 返回数据无法识别', uncertain=method == 'sendMessage') from None
    if not isinstance(payload, dict):
        raise TelegramError('Telegram 返回数据无法识别', uncertain=method == 'sendMessage')
    if not payload.get("ok"):
        raise TelegramError("Telegram 操作失败请检查配置与权限")
    result = payload.get('result')
    expected = list if method == 'getUpdates' else (bool if method in {
        'restrictChatMember', 'banChatMember', 'unbanChatMember', 'deleteMessages', 'deleteMessage', 'answerCallbackQuery',
    } else dict)
    if not isinstance(result, expected) or (expected is bool and result is not True):
        raise TelegramError('Telegram 返回数据无法识别', uncertain=method == 'sendMessage')
    return result


def inspect_group(token: str, chat_id: str, proxy_url: str = "", *, request_timeout: int = 10) -> dict:
    """按步骤返回结果，后续失败时保留前面已完成的检测。"""
    result = {
        "ok": False,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "checks": [],
        "bot_name": "",
        "group_name": "",
    }
    try:
        started = time.perf_counter()
        bot = call_api(token, "getMe", proxy_url=proxy_url, request_timeout=request_timeout)
        result["latency_ms"] = max(1, round((time.perf_counter() - started) * 1000))
        result["bot_name"] = bot.get("first_name", "")
        result["bot_username"] = bot.get("username", "")
        result["checks"].append({"label": "机器人连接", "ok": True, "detail": "Token 有效，机器人连接正常。"})
        chat = call_api(token, "getChat", proxy_url=proxy_url, request_timeout=request_timeout, chat_id=chat_id)
        if chat.get("type") not in ("group", "supergroup"):
            raise TelegramError("目标必须是群组或超级群，不能是私聊或频道。")
        result["group_name"] = chat.get("title", "")
        result["checks"].append({"label": "目标群组", "ok": True, "detail": result["group_name"]})
        member = call_api(token, "getChatMember", proxy_url=proxy_url, request_timeout=request_timeout, chat_id=chat_id, user_id=bot["id"])
        admin = member.get("status") in ("administrator", "creator")
        result["checks"].append({"label": "管理员身份", "ok": admin, "detail": "已是群管理员。" if admin else "请将机器人设为群管理员。"})
        for key, label in (("can_delete_messages", "删除消息"), ("can_restrict_members", "禁言与封禁成员"), ("can_invite_users", "邀请与入群审批")):
            allowed = admin and (member.get("status") == "creator" or bool(member.get(key)))
            result["checks"].append({"label": label, "ok": allowed, "detail": "已授权。" if allowed else "尚未授权，请在 Telegram 群管理员设置中开启。"})
        result["ok"] = all(item["ok"] for item in result["checks"])
        result["message"] = "机器人与群配置检测通过" if result["ok"] else "机器人已连接但管理员权限不足"
    except TelegramError as error:
        result["message"] = str(error)
        result["checks"].append({"label": "连接检测", "ok": False, "detail": str(error)})
    return result
