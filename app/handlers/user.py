"""Хэндлеры пользователя: приветствие, поиск по базе, сценарии 1-3 из паспорта."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.database import queries
from app.keyboards import main_menu
from app.services import llm
from app.services.notifier import notify_unanswered

logger = logging.getLogger(__name__)

router = Router(name="user")

HELP_TEXT = (
    "Я бот-консультант по базе знаний.\n\n"
    "Просто отправьте мне запрос (ключевое слово или вопрос), "
    "и я найду информацию в базе.\n\n"
    "Команды:\n"
    "/start — начать\n"
    "/help — справка"
)

# Ключ для хранения истории диалога в FSM-контексте (для уточняющих вопросов).
_HISTORY_KEY = "history"
_HISTORY_LIMIT = 10


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Здравствуйте! " + HELP_TEXT,
        reply_markup=main_menu(),
    )


@router.message(Command("help"))
@router.message(F.text.casefold() == "помощь")
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(F.text)
async def handle_query(message: Message, state: FSMContext) -> None:
    """Основной обработчик текстовых запросов (Сценарии 1-3)."""
    query = (message.text or "").strip()
    if not query:
        return

    user_id = message.from_user.id if message.from_user else 0

    # История диалога для контекста уточняющих вопросов (Сценарий 2).
    data = await state.get_data()
    history: list[str] = data.get(_HISTORY_KEY, [])

    results = await queries.search_knowledge(query)
    found = bool(results)
    await queries.log_query(user_id, query, found)

    if found:
        # Сценарий 1: найдено совпадение — отдаём справку.
        parts = [f"<b>{item.title}</b>\n{item.content}" for item in results]
        await message.answer("\n\n".join(parts))
    else:
        # Сценарий 3: ничего не найдено — LLM-заглушка + фиксация + уведомление.
        answer = await llm.ask(query, context=history)
        await queries.log_unanswered(user_id, query)
        await notify_unanswered(message.bot, _owner_id(), user_id, query)
        await message.answer(answer)

    history.append(query)
    await state.update_data({_HISTORY_KEY: history[-_HISTORY_LIMIT:]})


def _owner_id() -> int:
    """Получить ID владельца из глобальной конфигурации, установленной в bot.py."""
    from app import runtime

    return runtime.OWNER_ID
