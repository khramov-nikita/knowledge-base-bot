"""Хэндлеры владельца: статистика и управление записями базы знаний."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import BaseFilter, Command
from aiogram.types import Message

from app import runtime
from app.database import queries

logger = logging.getLogger(__name__)

router = Router(name="owner")


class IsOwner(BaseFilter):
    """Пропускает только сообщения от владельца бота."""

    async def __call__(self, message: Message) -> bool:
        return bool(message.from_user and message.from_user.id == runtime.OWNER_ID)


# Все хэндлеры этого роутера доступны только владельцу.
router.message.filter(IsOwner())


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    popular = await queries.get_popular_queries()
    unanswered = await queries.count_unanswered()

    lines = ["<b>Статистика</b>", f"Незакрытых запросов: {unanswered}", ""]
    if popular:
        lines.append("Популярные запросы:")
        for i, (query, count) in enumerate(popular, start=1):
            lines.append(f"{i}. {query} — {count}")
    else:
        lines.append("Запросов пока не было.")

    await message.answer("\n".join(lines))


@router.message(Command("add"))
async def cmd_add(message: Message) -> None:
    """Добавить запись: /add ключевые слова | заголовок | текст."""
    payload = (message.text or "").removeprefix("/add").strip()
    parts = [p.strip() for p in payload.split("|")]
    if len(parts) != 3 or not all(parts):
        await message.answer(
            "Формат: /add ключевые слова | заголовок | текст ответа"
        )
        return

    keywords, title, content = parts
    new_id = await queries.add_knowledge(keywords, title, content)
    await message.answer(f"Запись добавлена (id={new_id}).")


@router.message(Command("list"))
async def cmd_list(message: Message) -> None:
    items = await queries.list_knowledge()
    if not items:
        await message.answer("База знаний пуста.")
        return

    lines = ["<b>Записи базы знаний</b>"]
    for item in items:
        lines.append(f"#{item.id} — {item.title}")
    await message.answer("\n".join(lines))
