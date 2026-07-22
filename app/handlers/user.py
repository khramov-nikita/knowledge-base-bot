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


def _line_match_score(line: str, words: list[str]) -> int:
    """Сколько слов запроса встречается в строке (по полному слову или основе)."""
    lowered = line.lower()
    score = 0
    for word in words:
        if len(word) < 3:
            continue
        if word in lowered:
            score += 2
            continue
        # Грубая основа: отсекаем короткое окончание.
        stem = word[:-2] if len(word) > 5 else word
        if len(stem) >= 4 and stem in lowered:
            score += 1
    return score


def _pick_relevant_lines(
    content: str,
    query: str,
    *,
    line_limit: int,
    char_limit: int,
) -> str:
    """Взять строки, наиболее полезные для ответа на запрос.

    Сначала предпочитаем строки с совпадением по словам запроса (например,
    «Развитие навыков…»), иначе — начало раздела.
    """
    words = [w.lower() for w in re.findall(r"[0-9A-Za-zА-Яа-яЁё]+", query) if len(w) >= 3]
    stop = {
        "какие", "какой", "какая", "были", "было", "есть", "ходе", "этот", "эта",
        "для", "при", "про", "что", "как", "или",
    }
    words = [w for w in words if w not in stop]

    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    if not lines:
        return ""

    if words:
        ranked = sorted(
            (( _line_match_score(ln, words), idx, ln) for idx, ln in enumerate(lines)),
            key=lambda t: (t[0], -t[1]),
            reverse=True,
        )
        chosen_idx: set[int] = set()
        for score, idx, _ln in ranked:
            if score <= 0 and chosen_idx:
                break
            if score <= 0:
                break
            # Берём совпавшую строку и соседей для контекста.
            for j in (idx - 1, idx, idx + 1):
                if 0 <= j < len(lines):
                    chosen_idx.add(j)
            if len(chosen_idx) >= line_limit:
                break
        if chosen_idx:
            selected = [lines[i] for i in sorted(chosen_idx)[:line_limit]]
            body = "\n".join(selected)
            if len(body) > char_limit:
                body = body[:char_limit].rstrip() + "…"
            return body

    body = "\n".join(lines[:line_limit])
    if len(body) > char_limit:
        body = body[:char_limit].rstrip() + "…"
    return body


def _compact_knowledge(
    items: list[queries.KnowledgeItem],
    query: str = "",
    *,
    detailed: bool = False,
) -> list[str]:
    """Краткое представление записей для LLM с приоритетом строк по запросу."""
    chunks: list[str] = []
    line_limit = 16 if detailed else 8
    char_limit = 1600 if detailed else 700
    for item in items:
        title = _context_title(item.title)
        price = _extract_price_line(item.content)
        # Для ценовых вопросов достаточно строки со стоимостью.
        if price and not detailed and not any(
            w in query.lower() for w in ("навык", "опыт", "обязан", "развит", "работ")
        ):
            chunks.append(f"{title}\n{price}")
            continue
        body = _pick_relevant_lines(
            item.content,
            query,
            line_limit=line_limit,
            char_limit=char_limit,
        )
        chunks.append(f"{title}\n{body}" if body else title)
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
    # Если профильный/навыковый вопрос — даём больше строк из разделов.
    detailed = llm.wants_detailed_answer(query) or any(
        m in query.lower() for m in ("навык", "опыт", "развит", "обязан", "достижен")
    )

    reply_text = ""
    answered = False

    if llm.is_enabled() and context_items:
        knowledge = _compact_knowledge(
            context_items,
            query,
            detailed=detailed,
        )
        llm_answer = await llm.answer(query, knowledge, history=history)
        if llm_answer is not None and not llm.is_no_answer(llm_answer):
            reply_text = llm_answer
            await _safe_answer(message, md_to_telegram_html(llm_answer))
            answered = True
        elif context_items:
            # LLM недоступна / вернула NO_ANSWER / ошибка — отвечаем релевантными
            # строками из базы, а не «ответа нет», если данные есть.
            snippets = _compact_knowledge(context_items, query, detailed=True)
            if snippets:
                reply_text = "\n\n".join(snippets)
                await _safe_answer(message, md_to_telegram_html(reply_text))
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
