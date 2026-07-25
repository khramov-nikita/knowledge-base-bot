"""Хэндлеры пользователя: приветствие, поиск по базе, сценарии 1-3 из паспорта."""

from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, User

from app import runtime
from app.database import persistence, queries
from app.database.persistence import CartItem
from app.keyboards import (
    BTN_ADD_TO_CART,
    BTN_CART,
    BTN_CHECKOUT,
    BTN_CONTACT_HUMAN,
    BTN_PAID,
    BTN_PAY,
    BTN_REMOVE,
    BTN_SHOWCASE,
    add_to_cart_keyboard,
    cart_keyboard,
    main_menu,
    order_pay_keyboard,
    order_payment_keyboard,
)
from app.services import guardrails, llm
from app.services import yookassa_payments
from app.services.notifier import (
    notify_contact_request,
    notify_paid_order,
    notify_unanswered,
)
from app.utils.formatting import bold, md_to_telegram_html
from app.utils.pricing import format_money, total_from_price_texts

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


def _strip_md_bold(text: str) -> str:
    """Убрать markdown-жирный из одной строки (в т.ч. вид **Цель:** …)."""
    cleaned = text.strip()
    cleaned = re.sub(r"\*\*(.+?)\*\*", r"\1", cleaned)
    return cleaned.replace("**", "").strip()


def _extract_price_line(content: str) -> str:
    """Достать строку со стоимостью из текста записи, если есть."""
    for line in content.splitlines():
        stripped = line.strip()
        if re.search(r"(стоимость|цена|₽|\d[\d\s]*000)", stripped, re.IGNORECASE):
            return _strip_md_bold(stripped)
    return ""


CART_EMPTY_TEXT = (
    "Ваша корзина пока пуста.\n"
    f"Откройте «{BTN_SHOWCASE}», чтобы выбрать услуги."
)

ORDER_STATUS_AWAITING = "ожидает оплаты"
ORDER_STATUS_PAID = "оплачен"


def _format_cart_message(items: list[CartItem]) -> str:
    """Текст корзины: позиции с ценами и итоговая сумма."""
    lines = ["Ваша корзина:"]
    for item in items:
        line = f"• {item.title}"
        if item.price_text:
            line += f" — {item.price_text}"
        lines.append(line)
    total = total_from_price_texts([item.price_text for item in items])
    if total is not None:
        lines.append(f"\nИтого: {format_money(total)}")
    else:
        lines.append("\nИтого: сумма уточняется при оформлении.")
    return "\n".join(lines)


def _format_order_summary(order_id: int, items: list[CartItem]) -> tuple[str, int | None]:
    """Сообщение после оформления заказа и распознанная сумма (если есть)."""
    lines = [
        f"Заказ №{order_id} оформлен.",
        f"Статус: {ORDER_STATUS_AWAITING}.",
        "",
        "Состав заказа:",
    ]
    for item in items:
        line = f"• {item.title}"
        if item.price_text:
            line += f" — {item.price_text}"
        lines.append(line)
    total = total_from_price_texts([item.price_text for item in items])
    if total is not None:
        lines.append(f"\nИтого: {format_money(total)}")
        lines.append("\nНажмите «Оплатить», чтобы перейти к оплате.")
    else:
        lines.append("\nИтого: сумма будет уточнена.")
        lines.append(
            "\nОнлайн-оплата недоступна: не удалось определить сумму. "
            "Свяжитесь с исполнителем."
        )
    return "\n".join(lines), total


def _payment_return_url() -> str:
    username = runtime.BOT_USERNAME
    if username:
        return f"https://t.me/{username}"
    return "https://t.me/"


def _order_total_rub(order: persistence.Order) -> int | None:
    return total_from_price_texts([item.price_text for item in order.items])


async def _safe_edit_message(
    message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Обновить сообщение корзины; игнорировать «message is not modified».

    None для reply_markup снимает inline-клавиатуру (пустая разметка).
    """
    markup = (
        reply_markup
        if reply_markup is not None
        else InlineKeyboardMarkup(inline_keyboard=[])
    )
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            logger.warning("Не удалось обновить сообщение корзины: %s", exc)


def _extract_service_description(content: str) -> str:
    """Короткое описание услуги: строка «Цель», иначе начало текста без цены."""
    for line in content.splitlines():
        stripped = _strip_md_bold(line)
        match = re.match(r"^Цель:\s*(.+)$", stripped, re.IGNORECASE)
        if match:
            return match.group(1).strip()

    for line in content.splitlines():
        stripped = _strip_md_bold(line)
        if not stripped:
            continue
        if re.search(r"(стоимость|цена|₽)", stripped, re.IGNORECASE):
            continue
        if stripped.lower().startswith("стек"):
            continue
        return stripped
    return ""


def _format_service_card(item: queries.KnowledgeItem) -> str:
    """HTML-карточка услуги для витрины."""
    title = bold(_short_title(item.title))
    description = _extract_service_description(item.content)
    price = _extract_price_line(item.content)
    parts = [title]
    if description:
        parts.append(md_to_telegram_html(description))
    if price:
        parts.append(md_to_telegram_html(price))
    return "\n".join(parts)


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


async def _ensure_user(user: User | None) -> int | None:
    """Зарегистрировать/обновить пользователя в БД. Возвращает telegram_id."""
    if user is None:
        return None
    await persistence.upsert_user(user.id, user.username, user.full_name)
    return user.id


async def _log_exchange(user_id: int | None, user_text: str, assistant_text: str) -> None:
    """Сохранить пару реплик в историю диалога (для контекста LLM)."""
    if user_id is None:
        return
    await persistence.append_dialog_exchange(user_id, user_text, assistant_text)


async def _send_help(message: Message) -> None:
    items = await queries.list_knowledge(limit=50)
    text = HELP_TEXT
    if items:
        topics = "\n".join(
            f"• {md_to_telegram_html(_short_title(item.title))}" for item in items
        )
        text += "\n\n<b>Доступные темы для запросов:</b>\n" + topics
    await message.answer(text)


async def _send_showcase(message: Message, user_id: int | None) -> None:
    """Показать карточки услуг из базы знаний."""
    services = await queries.list_catalog_services()
    if not services:
        reply = "Пока нет услуг для отображения."
        await message.answer(reply)
        await _log_exchange(user_id, BTN_SHOWCASE, reply)
        return

    await message.answer("Наши услуги:")
    summary_lines: list[str] = ["Наши услуги:"]
    for item in services:
        await message.answer(
            _format_service_card(item),
            reply_markup=add_to_cart_keyboard(item.id),
        )
        title = _short_title(item.title)
        price = _extract_price_line(item.content)
        summary_lines.append(f"• {title}" + (f" — {price}" if price else ""))

    await _log_exchange(user_id, BTN_SHOWCASE, "\n".join(summary_lines))


async def _send_cart(message: Message, user_id: int | None) -> None:
    """Показать содержимое корзины из БД с кнопками управления."""
    if user_id is None:
        await message.answer(CART_EMPTY_TEXT)
        return

    items = await persistence.list_cart(user_id)
    if not items:
        await message.answer(CART_EMPTY_TEXT)
        await _log_exchange(user_id, BTN_CART, CART_EMPTY_TEXT)
        return

    reply = _format_cart_message(items)
    await message.answer(reply, reply_markup=cart_keyboard(items))
    await _log_exchange(user_id, BTN_CART, reply)


async def _send_contact_human(message: Message, user_id: int | None) -> None:
    """Уведомляем владельца (если это не он сам) и отправляем контакты пользователю."""
    user = message.from_user
    if user is not None and user.id != runtime.OWNER_ID:
        await notify_contact_request(
            message.bot,
            runtime.OWNER_ID,
            user.full_name,
            user.username,
        )

    contact = runtime.OWNER_CONTACT or "контакты не указаны"
    reply = (
        "Ваш запрос передан. С вами свяжется человек.\n\n"
        f"Наши контакты: {contact}"
    )
    await message.answer(reply)
    await _log_exchange(user_id, BTN_CONTACT_HUMAN, reply)


async def _handle_knowledge_query(message: Message, query: str) -> None:
    """Обработка текстовых запросов к базе знаний (Сценарии 1-3)."""
    user_id = message.from_user.id if message.from_user else 0
    history = await persistence.get_recent_dialog(
        user_id,
        limit=persistence.DIALOG_HISTORY_LIMIT,
    )

    targeted = await queries.search_knowledge(query)
    context_items = await queries.select_context_for_query(query)
    # Если профильный/навыковый вопрос — даём больше строк из разделов.
    detailed = llm.wants_detailed_answer(query) or any(
        m in query.lower() for m in ("навык", "опыт", "развит", "обязан", "достижен")
    )

    reply_text = ""
    answered = False

    # LLM нужна и при пустой выборке из БЗ, если есть история диалога
    # (вопросы вроде «о чём мы говорили?», «что в корзине?»).
    if llm.is_enabled() and (context_items or history):
        knowledge = (
            _compact_knowledge(context_items, query, detailed=detailed)
            if context_items
            else []
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
        elif history and (llm_answer is None or llm.is_no_answer(llm_answer)):
            # Fallback: кратко напомнить последние реплики бота из истории.
            recent = [
                m["content"]
                for m in history
                if m.get("role") == "assistant" and m.get("content")
            ]
            if recent:
                reply_text = "Недавно в диалоге:\n" + "\n\n".join(recent[-3:])
                await message.answer(reply_text)
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
    await persistence.append_dialog_exchange(user_id, query, reply_text)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    # Историю диалога и корзину не сбрасываем — только upsert пользователя.
    await _ensure_user(message.from_user)
    await message.answer(
        "Здравствуйте! " + HELP_TEXT,
        reply_markup=main_menu(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await _ensure_user(message.from_user)
    await _send_help(message)


@router.callback_query(F.data.startswith("cart:add:"))
async def on_add_to_cart(callback: CallbackQuery) -> None:
    """Добавить услугу в корзину (без дублей)."""
    user_id = await _ensure_user(callback.from_user)
    raw = (callback.data or "").removeprefix("cart:add:")
    try:
        service_id = int(raw)
    except ValueError:
        await callback.answer("Услуга не найдена", show_alert=True)
        return

    item = await queries.get_knowledge_by_id(service_id)
    if item is None:
        await callback.answer("Услуга не найдена", show_alert=True)
        return

    title = _short_title(item.title)
    price_text = _extract_price_line(item.content)
    user_action = f"{BTN_ADD_TO_CART}: {title}"

    if user_id is None:
        await callback.answer("Не удалось определить пользователя", show_alert=True)
        return

    added = await persistence.add_to_cart(user_id, service_id, title, price_text)
    if added:
        reply = f"Добавлено в корзину: {title}"
        if price_text:
            reply += f" ({price_text})"
        await callback.answer(reply)
    else:
        reply = f"Уже в корзине: {title}"
        await callback.answer(reply, show_alert=True)

    await _log_exchange(user_id, user_action, reply)


@router.callback_query(F.data.startswith("cart:remove:"))
async def on_remove_from_cart(callback: CallbackQuery) -> None:
    """Убрать позицию из корзины и обновить сообщение."""
    user_id = await _ensure_user(callback.from_user)
    if user_id is None:
        await callback.answer("Не удалось определить пользователя", show_alert=True)
        return

    raw = (callback.data or "").removeprefix("cart:remove:")
    try:
        cart_item_id = int(raw)
    except ValueError:
        await callback.answer("Позиция не найдена", show_alert=True)
        return

    removed = await persistence.remove_from_cart(user_id, cart_item_id)
    if removed:
        await callback.answer("Убрано")
        user_action = f"{BTN_REMOVE}: #{cart_item_id}"
        log_reply = "Позиция убрана из корзины."
    else:
        await callback.answer("Позиция уже удалена")
        user_action = f"{BTN_REMOVE}: #{cart_item_id}"
        log_reply = "Позиция уже была удалена."

    items = await persistence.list_cart(user_id)
    message = callback.message
    if message is None or not isinstance(message, Message):
        await _log_exchange(user_id, user_action, log_reply)
        return

    if not items:
        await _safe_edit_message(message, CART_EMPTY_TEXT, reply_markup=None)
        await _log_exchange(user_id, user_action, CART_EMPTY_TEXT)
        return

    reply = _format_cart_message(items)
    await _safe_edit_message(message, reply, reply_markup=cart_keyboard(items))
    await _log_exchange(user_id, user_action, reply)


@router.callback_query(F.data == "cart:checkout")
async def on_checkout(callback: CallbackQuery) -> None:
    """Оформить заказ из корзины (статус «ожидает оплаты»)."""
    user_id = await _ensure_user(callback.from_user)
    if user_id is None:
        await callback.answer("Не удалось определить пользователя", show_alert=True)
        return

    result = await persistence.checkout_cart(user_id)
    message = callback.message

    if result is None:
        await callback.answer("Корзина пуста", show_alert=True)
        if message is not None and isinstance(message, Message):
            await _safe_edit_message(message, CART_EMPTY_TEXT, reply_markup=None)
        await _log_exchange(user_id, BTN_CHECKOUT, CART_EMPTY_TEXT)
        return

    order_id, items = result
    summary, total = _format_order_summary(order_id, items)
    pay_markup = order_pay_keyboard(order_id) if total is not None else None
    await callback.answer("Заказ оформлен")

    if message is not None and isinstance(message, Message):
        await _safe_edit_message(
            message,
            f"Заказ №{order_id} оформлен. Корзина очищена.",
            reply_markup=None,
        )

    if callback.message is not None:
        await callback.message.answer(summary, reply_markup=pay_markup)
    await _log_exchange(user_id, BTN_CHECKOUT, summary)


@router.callback_query(F.data.startswith("pay:create:"))
async def on_pay_create(callback: CallbackQuery) -> None:
    """Создать платёж ЮKassa (или переиспользовать активный) и отправить ссылку."""
    user_id = await _ensure_user(callback.from_user)
    if user_id is None:
        await callback.answer("Не удалось определить пользователя", show_alert=True)
        return

    raw = (callback.data or "").removeprefix("pay:create:")
    try:
        order_id = int(raw)
    except ValueError:
        await callback.answer("Некорректный заказ", show_alert=True)
        return

    order = await persistence.get_order_for_user(order_id, user_id)
    if order is None:
        await callback.answer("Заказ не найден", show_alert=True)
        return

    if order.status == ORDER_STATUS_PAID:
        await callback.answer("Заказ уже оплачен", show_alert=True)
        return

    if order.status != ORDER_STATUS_AWAITING:
        await callback.answer("Этот заказ нельзя оплатить", show_alert=True)
        return

    amount = _order_total_rub(order)
    if amount is None or amount <= 0:
        await callback.answer(
            "Не удалось определить сумму заказа",
            show_alert=True,
        )
        return

    payment_url: str | None = None
    try:
        if order.payment_id:
            existing = await yookassa_payments.get_payment(order.payment_id)
            if existing.status == "succeeded":
                updated = await persistence.set_order_status(
                    order_id,
                    ORDER_STATUS_PAID,
                    only_if_status=ORDER_STATUS_AWAITING,
                )
                if updated and callback.from_user is not None:
                    if callback.from_user.id != runtime.OWNER_ID:
                        await notify_paid_order(
                            callback.bot,
                            runtime.OWNER_ID,
                            callback.from_user.id,
                            callback.from_user.full_name,
                            callback.from_user.username,
                            order_id,
                            format_money(amount),
                        )
                await callback.answer("Заказ уже оплачен", show_alert=True)
                if callback.message is not None:
                    await callback.message.answer(
                        f"Заказ №{order_id} уже оплачен. Спасибо!"
                    )
                return
            if yookassa_payments.is_active_payment(existing.status):
                payment_url = existing.confirmation_url

        if not payment_url:
            created = await yookassa_payments.create_payment(
                order_id,
                amount,
                _payment_return_url(),
            )
            await persistence.set_order_payment(order_id, created.payment_id)
            payment_url = created.confirmation_url
    except yookassa_payments.YooKassaAuthError:
        await callback.answer(
            "Ошибка ключей ЮKassa. Проверьте YOOKASSA_SHOP_ID и "
            "YOOKASSA_SECRET_KEY в .env (полный ключ без «*»).",
            show_alert=True,
        )
        return
    except Exception:  # noqa: BLE001 — не ломаем диалог из‑за API
        await callback.answer(
            "Не удалось создать платёж. Попробуйте позже.",
            show_alert=True,
        )
        return

    if not payment_url:
        await callback.answer(
            "Не удалось получить ссылку на оплату",
            show_alert=True,
        )
        return

    reply = (
        f"Оплата заказа №{order_id} на сумму {format_money(amount)}.\n\n"
        "Откройте страницу оплаты по кнопке ниже. "
        "После оплаты нажмите «Я оплатил»."
    )
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            reply,
            reply_markup=order_payment_keyboard(order_id, payment_url),
        )
    await _log_exchange(user_id, BTN_PAY, reply)


@router.callback_query(F.data.startswith("pay:check:"))
async def on_pay_check(callback: CallbackQuery) -> None:
    """Проверить статус платежа в ЮKassa по кнопке «Я оплатил»."""
    user_id = await _ensure_user(callback.from_user)
    if user_id is None:
        await callback.answer("Не удалось определить пользователя", show_alert=True)
        return

    raw = (callback.data or "").removeprefix("pay:check:")
    try:
        order_id = int(raw)
    except ValueError:
        await callback.answer("Некорректный заказ", show_alert=True)
        return

    order = await persistence.get_order_for_user(order_id, user_id)
    if order is None:
        await callback.answer("Заказ не найден", show_alert=True)
        return

    amount = _order_total_rub(order)
    amount_text = format_money(amount) if amount is not None else "—"

    if order.status == ORDER_STATUS_PAID:
        await callback.answer("Заказ уже оплачен")
        if callback.message is not None:
            await callback.message.answer(
                f"Заказ №{order_id} уже оплачен. Спасибо!"
            )
        return

    if not order.payment_id:
        await callback.answer(
            "Сначала нажмите «Оплатить», чтобы получить ссылку.",
            show_alert=True,
        )
        return

    try:
        payment = await yookassa_payments.get_payment(order.payment_id)
    except Exception:  # noqa: BLE001
        await callback.answer(
            "Не удалось проверить оплату. Попробуйте позже.",
            show_alert=True,
        )
        return

    if payment.status == "succeeded":
        updated = await persistence.set_order_status(
            order_id,
            ORDER_STATUS_PAID,
            only_if_status=ORDER_STATUS_AWAITING,
        )
        reply = (
            f"Спасибо! Заказ №{order_id} оплачен.\n"
            f"Сумма: {amount_text}."
        )
        await callback.answer("Оплата подтверждена")
        if callback.message is not None:
            await callback.message.answer(reply)
        if updated and callback.from_user is not None:
            if callback.from_user.id != runtime.OWNER_ID:
                await notify_paid_order(
                    callback.bot,
                    runtime.OWNER_ID,
                    callback.from_user.id,
                    callback.from_user.full_name,
                    callback.from_user.username,
                    order_id,
                    amount_text,
                )
        await _log_exchange(user_id, BTN_PAID, reply)
        return

    reply = (
        "Оплата ещё не поступила. "
        "Завершите оплату по ссылке и нажмите «Я оплатил» снова."
    )
    await callback.answer(reply, show_alert=True)
    await _log_exchange(user_id, BTN_PAID, reply)


@router.message(F.text)
async def dispatch_text(message: Message) -> None:
    """Единая точка входа для всего текста — один ответ на одно сообщение."""
    query = (message.text or "").strip()
    if not query or query.startswith("/"):
        return

    user_id = await _ensure_user(message.from_user)

    if query == BTN_SHOWCASE:
        await _send_showcase(message, user_id)
        return

    if query == BTN_CART:
        await _send_cart(message, user_id)
        return

    if query == BTN_CONTACT_HUMAN:
        await _send_contact_human(message, user_id)
        return

    await _handle_knowledge_query(message, query)
