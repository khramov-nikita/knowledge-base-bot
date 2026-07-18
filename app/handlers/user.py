"""Хэндлеры пользователя: приветствие, поиск по базе, сценарии 1-3 из паспорта."""

from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app import runtime
from app.database import queries
from app.keyboards import BTN_CONTACT_HUMAN, BTN_HELP, main_menu
from app.services import guardrails, llm
from app.services.notifier import notify_contact_request, notify_unanswered
from app.utils.formatting import bold, md_to_telegram_html

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

# Память диалога в FSM-контексте: последние сообщения (вопросы и ответы).
_HISTORY_KEY = "history"
_HISTORY_LIMIT = 20


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Здравствуйте! " + HELP_TEXT,
        reply_markup=main_menu(),
    )


def _short_title(title: str) -> str:
    """Короткий заголовок: без хвостовой скобки и без префиксов через ': '.

    Примеры:
        'База знаний: ФИО: Опыт работы (Общий стаж: ~8 лет)' -> 'Опыт работы'
        'Портфолио проектов: Сборка лендинга (Landing Page)' -> 'Сборка лендинга'
    """
    cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()
    return cleaned.split(": ")[-1].strip() or title


def _formatted_items(items: list[queries.KnowledgeItem]) -> str:
    """HTML-представление записей базы (fallback, когда LLM недоступен)."""
    return "\n\n".join(
        f"{bold(_short_title(item.title))}\n{md_to_telegram_html(item.content)}"
        for item in items
    )


def _plain_items(items: list[queries.KnowledgeItem]) -> str:
    """Простой текст записей базы для сохранения в память диалога."""
    return "\n\n".join(f"{_short_title(item.title)}\n{item.content}" for item in items)


@router.message(Command("help"))
@router.message(F.text == BTN_HELP)
async def cmd_help(message: Message) -> None:
    items = await queries.list_knowledge(limit=50)
    text = HELP_TEXT
    if items:
        topics = "\n".join(
            f"• {md_to_telegram_html(_short_title(item.title))}" for item in items
        )
        text += "\n\n<b>Доступные темы для запросов:</b>\n" + topics
    await message.answer(text)


@router.message(F.text == BTN_CONTACT_HUMAN)
async def contact_human(message: Message) -> None:
    """Кнопка «Связаться с человеком»: уведомляем владельца, шлём контакты."""
    user = message.from_user
    if user is not None:
        await notify_contact_request(
            message.bot,
            runtime.OWNER_ID,
            user.id,
            user.full_name,
            user.username,
        )

    contact = runtime.OWNER_CONTACT or "контакты не указаны"
    await message.answer(
        "Ваш запрос передан. С вами свяжется человек.\n\n"
        f"Наши контакты: {contact}"
    )


@router.message(F.text)
async def handle_query(message: Message, state: FSMContext) -> None:
    """Основной обработчик текстовых запросов (Сценарии 1-3)."""
    query = (message.text or "").strip()
    if not query:
        return

    user_id = message.from_user.id if message.from_user else 0

    # Память диалога: список сообщений {"role", "content"} для контекста уточнений.
    data = await state.get_data()
    history: list[dict[str, str]] = data.get(_HISTORY_KEY, [])

    # Точный поиск — для fallback без LLM. Для самой LLM передаём всю базу целиком
    # (она небольшая), чтобы работали вопросы на обобщение: среднее, список, сравнение.
    targeted = await queries.search_knowledge(query)
    all_items = await queries.list_knowledge(limit=50)

    reply_text = ""
    answered = False

    if llm.is_enabled() and all_items:
        knowledge = [f"{item.title}\n{item.content}" for item in all_items]
        llm_answer = await llm.answer(query, knowledge, history=history)
        if llm_answer is not None and not llm.is_no_answer(llm_answer):
            reply_text = llm_answer
            await _safe_answer(message, md_to_telegram_html(llm_answer))
            answered = True
        elif llm_answer is None and targeted:
            # Ошибка LLM, но точное совпадение есть — отдаём базу напрямую.
            reply_text = _plain_items(targeted)
            await _safe_answer(message, _formatted_items(targeted))
            answered = True
    elif targeted:
        # LLM выключен, но есть точное совпадение — отдаём содержимое базы.
        reply_text = _plain_items(targeted)
        await _safe_answer(message, _formatted_items(targeted))
        answered = True

    if not answered:
        if guardrails.detect_injection(query):
            # Попытка обойти инструкции без ответа — вежливый отказ, владельца не беспокоим.
            logger.warning("Промпт-инъекция без ответа — отказ.")
            reply_text = guardrails.REFUSAL_TEXT
            await message.answer(reply_text)
        else:
            # Сценарий 3: ответа нет — фиксируем и уведомляем владельца.
            await queries.log_unanswered(user_id, query)
            await notify_unanswered(message.bot, _owner_id(), user_id, query)
            reply_text = llm.NO_ANSWER_TEXT
            await message.answer(reply_text)

    await queries.log_query(user_id, query, answered)

    history.append({"role": "user", "content": query})
    history.append({"role": "assistant", "content": reply_text})
    await state.update_data({_HISTORY_KEY: history[-_HISTORY_LIMIT:]})


async def _safe_answer(message: Message, text: str) -> None:
    """Отправить ответ, при ошибке разбора HTML — повторить без форматирования."""
    try:
        await message.answer(text)
    except TelegramBadRequest:
        logger.warning("Ошибка HTML-разметки, отправляю без форматирования.")
        plain = re.sub(r"<[^>]+>", "", text)
        await message.answer(plain, parse_mode=None)


def _owner_id() -> int:
    """Получить ID владельца из глобальной конфигурации, установленной в bot.py."""
    return runtime.OWNER_ID
