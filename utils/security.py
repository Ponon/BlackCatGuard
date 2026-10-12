import hashlib
import secrets
from pathlib import Path

from fastapi import Request


BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_PATH = BASE_DIR / "data" / "session.key"


def load_session_secret() -> str:
    SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)

    try:
        with SECRET_PATH.open("x", encoding="utf-8") as file:
            file.write(secrets.token_hex(32))
        SECRET_PATH.chmod(0o600)
    except FileExistsError:
        pass

    return SECRET_PATH.read_text(encoding="utf-8").strip()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)

    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=16384,
        r=8,
        p=1,
    )

    return f"scrypt${salt.hex()}${digest.hex()}"


# 用保存的盐重新计算密码哈希，验证登录密码。
def verify_password(password: str, password_hash: str) -> bool:
    try:
        # 数据库保存格式：算法名称$盐$密码哈希。
        algorithm, salt, expected = password_hash.split("$")
        if algorithm != "scrypt":
            return False
        # 使用与创建管理员时相同的算法和参数。
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=bytes.fromhex(salt),
            n=16384, r=8, p=1,
        )
        # 使用恒定时间比较，避免普通字符串比较泄露哈希差异。
        return secrets.compare_digest(digest, bytes.fromhex(expected))
    except (ValueError, TypeError):
        # 哈希格式异常时按验证失败处理。
        return False


def get_csrf_token(request: Request) -> str:
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    return request.session["csrf_token"]


def valid_csrf_token(request: Request, token: str) -> bool:
    expected = request.session.get("csrf_token")

    return (
        isinstance(expected, str)
        and secrets.compare_digest(expected, token)
    )
