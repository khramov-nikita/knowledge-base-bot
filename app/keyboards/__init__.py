"""Клавиатуры бота."""

from __future__ import annotations

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

# Текст кнопок (используется и в клавиатуре, и в фильтрах хэндлеров).
BTN_HELP = "Помощь"
BTN_CONTACT_HUMAN = "Связаться с человеком"


def main_menu() -> ReplyKeyboardMarkup:
    """Основное меню пользователя."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CONTACT_HUMAN)],
            [KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Введите запрос...",
    )
