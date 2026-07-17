"""Уведомления владельцу бота о незакрытых запросах."""

from __future__ import annotations

import logging

from aiogram import Bot

logger = logging.getLogger(__name__)


async def notify_owner(bot: Bot, owner_id: int, text: str) -> None:
    """Отправить владельцу текстовое уведомление.

    Ошибки отправки логируются, но не прерывают обработку запроса пользователя.
    """
    try:
        await bot.send_message(owner_id, text)
    except Exception:  # noqa: BLE001 — не должно ломать основной поток
        logger.exception("Не удалось отправить уведомление владельцу (id=%s)", owner_id)


async def notify_unanswered(bot: Bot, owner_id: int, user_id: int, query: str) -> None:
    """Уведомить владельца о запросе без ответа."""
    text = (
        "Новый запрос без ответа в базе\n\n"
        f"От пользователя: {user_id}\n"
        f"Запрос: {query}"
    )
    await notify_owner(bot, owner_id, text)
