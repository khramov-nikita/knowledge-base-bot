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


def _short_title(title: str) -> str:
    """Короткий заголовок: без хвостовой скобки и без префиксов через ': '."""
    cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()
    return cleaned.split(": ")[-1].strip() or title


def _context_title(title: str) -> str:
    """Заголовок для LLM: без длинных префиксов, но с важными скобками (стаж и т.п.)."""
    paren = ""
    match = re.search(
        r"(\s*\([^)]*(?:стаж|лет|год|месяц)[^)]*\))\s*$",
        title,
        re.IGNORECASE,
    )
    if match:
        paren = match.group(1)
        title = title[: match.start()].rstrip()
    short = title.split(": ")[-1].strip() or title
    return f"{short}{paren}".strip()


def _extract_price_line(content: str) -> str:
    """Достать строку со стоимостью из текста записи, если есть."""
    for line in content.splitlines():
        stripped = line.strip()
        if re.search(r"(стоимость|цена|₽|\d[\d\s]*000)", stripped, re.IGNORECASE):
            return re.sub(r"^\*\*?|\*\*?$", "", stripped).strip()
    return ""


def _compact_knowledge(
    items: list[queries.KnowledgeItem],
    *,
    detailed: bool = False,
) -> list[str]:
    """Краткое представление записей для LLM: заголовок + цена/начало, без простыней."""
    chunks: list[str] = []
    line_limit = 12 if detailed else 2
    char_limit = 1200 if detailed else 280
    for item in items:
        title = _context_title(item.title)
        price = _extract_price_line(item.content)
        if price and not detailed:
            chunks.append(f"{title}\n{price}")
            continue
        lines = [ln.strip() for ln in item.content.splitlines() if ln.strip()][:line_limit]
        body = "\n".join(lines)
        if len(body) > char_limit:
            body = body[:char_limit].rstrip() + "…"
        chunks.append(f"{title}\n{body}")
    return chunks


def _services_summary(items: list[queries.KnowledgeItem]) -> str:
    """Короткий список услуг с ценами (безопасный fallback без выгрузки разделов)."""
    lines: list[str] = ["Предоставляемые услуги:"]
    seen: set[str] = set()
    for item in items:
        title = _short_title(item.title)
        if "стоимость" in title.lower() or "прайс" in title.lower() or "цена" in title.lower():
            for line in item.content.splitlines():
                if "₽" in line or re.search(r"\d[\d\s]*000", line):
                    clean = re.sub(r"^[-*•]\s*", "", line.strip())
                    clean = re.sub(r"^\*\*?|\*\*?$", "", clean).strip()
                    if clean and clean not in seen:
                        lines.append(f"• {clean}")
                        seen.add(clean)
            continue
        price = _extract_price_line(item.content)
        label = f"{title}" + (f" — {price}" if price else "")
        if label not in seen and "портфолио" not in title.lower():
            lines.append(f"• {label}" if price else f"• {title}")
            seen.add(label)
    if len(lines) == 1:
        return "\n\n".join(f"{_short_title(i.title)}\n{i.content}" for i in items[:3])
    return "\n".join(lines)


def _formatted_items(items: list[queries.KnowledgeItem]) -> str:
    """HTML-представление записей базы (fallback, когда LLM недоступен)."""
    if len(items) > 1:
        return md_to_telegram_html(_services_summary(items))
    return "\n\n".join(
        f"{bold(_short_title(item.title))}\n{md_to_telegram_html(item.content)}"
        for item in items
    )


def _plain_items(items: list[queries.KnowledgeItem]) -> str:
    """Простой текст записей базы для сохранения в память диалога."""
    if len(items) > 1:
        return _services_summary(items)
    return "\n\n".join(f"{_short_title(item.title)}\n{item.content}" for item in items)


async def _safe_answer(message: Message, text: str) -> None:
    """Отправить ответ, при ошибке разбора HTML — повторить без форматирования."""
    try:
        await message.answer(text)
    except TelegramBadRequest:
        logger.warning("Ошибка HTML-разметки, отправляю без форматирования.")
        plain = re.sub(r"<[^>]+>", "", text)
        await message.answer(plain, parse_mode=None)


def _owner_id() -> int:
    return runtime.OWNER_ID


async def _send_help(message: Message) -> None:
    items = await queries.list_knowledge(limit=50)
    text = HELP_TEXT
    if items:
        topics = "\n".join(
            f"• {md_to_telegram_html(_short_title(item.title))}" for item in items
        )
        text += "\n\n<b>Доступные темы для запросов:</b>\n" + topics
    await message.answer(text)


async def _send_contact_human(message: Message) -> None:
    """Уведомляем владельца (если это не он сам) и отправляем контакты пользователю."""
    user = message.from_user
    if user is not None and user.id != runtime.OWNER_ID:
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


async def _handle_knowledge_query(
    message: Message,
    state: FSMContext,
    query: str,
) -> None:
    """Обработка текстовых запросов к базе знаний (Сценарии 1-3)."""
    user_id = message.from_user.id if message.from_user else 0

    data = await state.get_data()
    history: list[dict[str, str]] = data.get(_HISTORY_KEY, [])

    targeted = await queries.search_knowledge(query)
    context_items = await queries.select_context_for_query(query)

    reply_text = ""
    answered = False

    if llm.is_enabled() and context_items:
        knowledge = _compact_knowledge(
            context_items,
            detailed=llm.wants_detailed_answer(query),
        )
        llm_answer = await llm.answer(query, knowledge, history=history)
        if llm_answer is not None and not llm.is_no_answer(llm_answer):
            reply_text = llm_answer
            await _safe_answer(message, md_to_telegram_html(llm_answer))
            answered = True
        elif llm_answer is None and context_items:
            reply_text = (
                _services_summary(context_items)
                if len(context_items) > 1
                else _plain_items(context_items)
            )
            await _safe_answer(
                message,
                md_to_telegram_html(reply_text)
                if len(context_items) > 1
                else _formatted_items(context_items),
            )
            answered = True
    elif targeted:
        reply_text = _plain_items(targeted)
        await _safe_answer(message, _formatted_items(targeted))
        answered = True

    if not answered:
        if guardrails.detect_injection(query):
            logger.warning("Промпт-инъекция без ответа — отказ.")
            reply_text = guardrails.REFUSAL_TEXT
            await message.answer(reply_text)
        else:
            await queries.log_unanswered(user_id, query)
            await notify_unanswered(message.bot, _owner_id(), user_id, query)
            reply_text = llm.NO_ANSWER_TEXT
            await message.answer(reply_text)

    await queries.log_query(user_id, query, answered)

    history.append({"role": "user", "content": query})
    history.append({"role": "assistant", "content": reply_text})
    await state.update_data({_HISTORY_KEY: history[-_HISTORY_LIMIT:]})


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Здравствуйте! " + HELP_TEXT,
        reply_markup=main_menu(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await _send_help(message)


@router.message(F.text)
async def dispatch_text(message: Message, state: FSMContext) -> None:
    """Единая точка входа для всего текста — один ответ на одно сообщение."""
    query = (message.text or "").strip()
    if not query or query.startswith("/"):
        return

    if query == BTN_HELP:
        await _send_help(message)
        return

    if query == BTN_CONTACT_HUMAN:
        await _send_contact_human(message)
        return

    await _handle_knowledge_query(message, state, query)
