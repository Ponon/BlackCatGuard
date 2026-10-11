import sqlite3
from contextlib import contextmanager
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "data" / "titguard.db"


@contextmanager
def get_db():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row

    try:
        with connection:
            yield connection
    finally:
        connection.close()


def init_db():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS administrators (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)


def has_admin() -> bool:
    with get_db() as db:
        admin = db.execute(
            "SELECT id FROM administrators LIMIT 1"
        ).fetchone()

    return admin is not None


def create_admin(username: str, password_hash: str):
    with get_db() as db:
        db.execute(
            """
            INSERT INTO administrators (
                id,
                username,
                password_hash
            )
            VALUES (1, ?, ?)
            """,
            (username, password_hash),
        )