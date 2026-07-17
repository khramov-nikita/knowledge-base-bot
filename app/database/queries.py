"""Запросы к базе данных: поиск по базе знаний, логирование, статистика, CRUD."""

from __future__ import annotations

from dataclasses import dataclass

from .db import get_connection


@dataclass
class KnowledgeItem:
    id: int
    keywords: str
    title: str
    content: str


async def search_knowledge(text: str, limit: int = 5) -> list[KnowledgeItem]:
    """Поиск записей по ключевым словам, заголовку и содержимому (LIKE).

    Простой полнотекстовый поиск через LIKE — задел под FTS5 в будущем.
    """
    pattern = f"%{text.strip()}%"
    async with get_connection() as db:
        cursor = await db.execute(
            """
            SELECT id, keywords, title, content
            FROM knowledge
            WHERE keywords LIKE ? OR title LIKE ? OR content LIKE ?
            ORDER BY id
            LIMIT ?
            """,
            (pattern, pattern, pattern, limit),
        )
        rows = await cursor.fetchall()

    return [KnowledgeItem(id=r[0], keywords=r[1], title=r[2], content=r[3]) for r in rows]


async def log_query(user_id: int, query: str, found: bool) -> None:
    """Записать факт запроса для статистики."""
    async with get_connection() as db:
        await db.execute(
            "INSERT INTO query_log (user_id, query, found) VALUES (?, ?, ?)",
            (user_id, query, int(found)),
        )
        await db.commit()


async def log_unanswered(user_id: int, query: str) -> None:
    """Зафиксировать запрос, на который не нашлось ответа."""
    async with get_connection() as db:
        await db.execute(
            "INSERT INTO unanswered (user_id, query) VALUES (?, ?)",
            (user_id, query),
        )
        await db.commit()


async def get_popular_queries(limit: int = 10) -> list[tuple[str, int]]:
    """Топ популярных запросов: (текст запроса, количество)."""
    async with get_connection() as db:
        cursor = await db.execute(
            """
            SELECT query, COUNT(*) AS cnt
            FROM query_log
            GROUP BY query
            ORDER BY cnt DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
    return [(r[0], r[1]) for r in rows]


async def count_unanswered(only_unresolved: bool = True) -> int:
    """Количество незакрытых запросов."""
    query = "SELECT COUNT(*) FROM unanswered"
    if only_unresolved:
        query += " WHERE resolved = 0"
    async with get_connection() as db:
        cursor = await db.execute(query)
        row = await cursor.fetchone()
    return row[0] if row else 0


async def add_knowledge(keywords: str, title: str, content: str) -> int:
    """Добавить запись в базу знаний. Возвращает id новой записи."""
    async with get_connection() as db:
        cursor = await db.execute(
            "INSERT INTO knowledge (keywords, title, content) VALUES (?, ?, ?)",
            (keywords, title, content),
        )
        await db.commit()
        return cursor.lastrowid or 0


async def list_knowledge(limit: int = 20) -> list[KnowledgeItem]:
    """Список записей базы знаний."""
    async with get_connection() as db:
        cursor = await db.execute(
            "SELECT id, keywords, title, content FROM knowledge ORDER BY id LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
    return [KnowledgeItem(id=r[0], keywords=r[1], title=r[2], content=r[3]) for r in rows]
