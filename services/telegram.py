"""Telegram 只读检测；不修改群权限，不发送群消息。"""
import json
import socket
import ssl
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request

from services.network import open_request


class TelegramError(Exception):
    pass


def call_api(token: str, method: str, *, proxy_url: str = "", **params):
    request = Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=urlencode(params).encode(),
        method="POST",
    )
    try:
        with open_request(request, proxy_url=proxy_url, timeout=10) as response:
            payload = json.load(response)
    except HTTPError as error:
        # 不返回包含 Token 的请求 URL 或 Telegram 原始异常。
        if error.code in (401, 404):
            raise TelegramError("机器人 Token 无效，请检查或重新生成。") from None
        if error.code in (400, 403):
            raise TelegramError("无法访问目标群，请检查群 ID、机器人是否入群及权限。") from None
        if error.code == 429:
            raise TelegramError("Telegram 请求过于频繁，请稍后再检测。") from None
        raise TelegramError("Telegram 服务暂时不可用，请稍后重试。") from None
    except URLError as error:
        if isinstance(error.reason, ssl.SSLCertVerificationError):
            raise TelegramError("Telegram HTTPS 证书校验失败，请检查服务器证书环境或代理证书。") from None
        raise TelegramError("连接 Telegram 失败，请检查代理地址、代理运行状态和服务器网络。") from None
    except ssl.SSLCertVerificationError:
        raise TelegramError("Telegram HTTPS 证书校验失败，请检查服务器证书环境或代理证书。") from None
    except (TimeoutError, socket.timeout):
        raise TelegramError("连接 Telegram 超时，请检查代理线路后重试。") from None
    except OSError:
        raise TelegramError("连接 Telegram 失败，请检查服务器网络后重试。") from None
    except (ValueError, TypeError):
        raise TelegramError("Telegram 返回了无法识别的数据，请稍后重试。") from None
    if not isinstance(payload, dict):
        raise TelegramError("Telegram 返回了无法识别的数据，请稍后重试。")
    if not payload.get("ok"):
        raise TelegramError("Telegram 检测失败，请检查配置与权限。")
    if not isinstance(payload.get("result"), dict):
        raise TelegramError("Telegram 返回了无法识别的数据，请稍后重试。")
    return payload["result"]


def inspect_group(token: str, chat_id: str, proxy_url: str = "") -> dict:
    """按步骤返回结果，后续失败时保留前面已完成的检测。"""
    result = {
        "ok": False,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "checks": [],
        "bot_name": "",
        "group_name": "",
    }
    try:
        bot = call_api(token, "getMe", proxy_url=proxy_url)
        result["bot_name"] = bot.get("first_name", "")
        result["bot_username"] = bot.get("username", "")
        result["checks"].append({"label": "机器人连接", "ok": True, "detail": "Token 有效，机器人连接正常。"})
        chat = call_api(token, "getChat", proxy_url=proxy_url, chat_id=chat_id)
        if chat.get("type") not in ("group", "supergroup"):
            raise TelegramError("目标必须是群组或超级群，不能是私聊或频道。")
        result["group_name"] = chat.get("title", "")
        result["checks"].append({"label": "目标群组", "ok": True, "detail": result["group_name"]})
        member = call_api(token, "getChatMember", proxy_url=proxy_url, chat_id=chat_id, user_id=bot["id"])
        admin = member.get("status") in ("administrator", "creator")
        result["checks"].append({"label": "管理员身份", "ok": admin, "detail": "已是群管理员。" if admin else "请将机器人设为群管理员。"})
        for key, label in (("can_delete_messages", "删除消息"), ("can_restrict_members", "禁言与封禁成员"), ("can_invite_users", "邀请与入群审批")):
            allowed = admin and (member.get("status") == "creator" or bool(member.get(key)))
            result["checks"].append({"label": label, "ok": allowed, "detail": "已授权。" if allowed else "尚未授权，请在 Telegram 群管理员设置中开启。"})
        result["ok"] = all(item["ok"] for item in result["checks"])
        result["message"] = "检测通过，机器人与群组配置正常。" if result["ok"] else "连接正常，但管理员权限尚未配置完整。"
    except TelegramError as error:
        result["message"] = str(error)
        result["checks"].append({"label": "连接检测", "ok": False, "detail": str(error)})
    return result
