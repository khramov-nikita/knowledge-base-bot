"""Парсинг и форматирование сумм из текстовых цен услуг."""

from __future__ import annotations

import re


def parse_price_amount(price_text: str) -> int | None:
    """Извлечь сумму из текста цены (например «Стоимость: 15 000 ₽»)."""
    if not price_text:
        return None
    normalized = price_text.replace("\u00a0", " ").replace("\u202f", " ")
    match = re.search(r"(\d[\d\s]*)", normalized)
    if not match:
        return None
    digits = re.sub(r"\s+", "", match.group(1))
    if not digits.isdigit():
        return None
    return int(digits)


def format_money(amount: int) -> str:
    """Форматировать сумму с пробелами тысяч: 15000 → «15 000 ₽»."""
    grouped = f"{amount:,}".replace(",", " ")
    return f"{grouped} ₽"


def total_from_price_texts(price_texts: list[str]) -> int | None:
    """Сумма распарсенных цен. None — ни одна позиция не распознана."""
    total = 0
    parsed_any = False
    for text in price_texts:
        amount = parse_price_amount(text)
        if amount is not None:
            total += amount
            parsed_any = True
    return total if parsed_any else None
