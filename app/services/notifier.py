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


async def notify_contact_request(
    bot: Bot,
    owner_id: int,
    full_name: str,
    username: str | None,
) -> None:
    """Уведомить владельца о том, что пользователь хочет связаться с человеком."""
    contact_line = f"@{username}" if username else "username не указан"
    text = (
        "Пользователь хочет связаться с человеком\n\n"
        f"Имя: {full_name}\n"
        f"Контакт: {contact_line}"
    )
    await notify_owner(bot, owner_id, text)


async def notify_paid_order(
    bot: Bot,
    owner_id: int,
    user_id: int,
    full_name: str,
    username: str | None,
    order_id: int,
    amount_text: str,
) -> None:
    """Уведомить владельца о новом оплаченном заказе."""
    username_line = f"@{username}" if username else "—"
    text = (
        "Новый оплаченный заказ\n\n"
        f"Заказ: №{order_id}\n"
        f"Сумма: {amount_text}\n\n"
        f"Имя: {full_name}\n"
        f"Username: {username_line}\n"
        f"ID: {user_id}"
    )
    await notify_owner(bot, owner_id, text)
