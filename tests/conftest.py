"""Общие фикстуры: временная SQLite и тестовый пользователь."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest_asyncio

# Корень репозитория — чтобы `import app` работал при запуске pytest.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.database import db, persistence


@pytest_asyncio.fixture
async def db_ready(tmp_path: Path):
    """Инициализировать пустую БД во временном файле."""
    db_path = tmp_path / "test.db"
    db.configure(str(db_path))
    await db.init_db()
    return db_path


@pytest_asyncio.fixture
async def user_id(db_ready: Path) -> int:
    """Зарегистрировать тестового пользователя и вернуть его telegram_id."""
    telegram_id = 10001
    await persistence.upsert_user(telegram_id, "test_user", "Test User")
    return telegram_id
