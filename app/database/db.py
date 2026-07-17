"""Подключение к SQLite и инициализация схемы базы данных."""

from __future__ import annotations

import os

import aiosqlite

_DB_PATH: str = "data/knowledge.db"


def configure(db_path: str) -> None:
    """Задать путь к файлу БД (вызывается один раз при старте)."""
    global _DB_PATH
    _DB_PATH = db_path


def get_connection() -> aiosqlite.Connection:
    """Открыть асинхронное соединение с БД.

    Использовать как контекстный менеджер:
        async with get_connection() as db:
            ...
    """
    return aiosqlite.connect(_DB_PATH)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keywords TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS unanswered (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    query TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    resolved INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS query_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    query TEXT NOT NULL,
    found INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


async def init_db() -> None:
    """Создать таблицы, если их ещё нет, и папку для файла БД."""
    directory = os.path.dirname(_DB_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)

    async with get_connection() as db:
        await db.executescript(_SCHEMA)
        await db.commit()
