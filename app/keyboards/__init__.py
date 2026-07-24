"""Клавиатуры бота."""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

# Текст кнопок (используется и в клавиатуре, и в фильтрах хэндлеров).
BTN_SHOWCASE = "Витрина"
BTN_CART = "Корзина"
BTN_CONTACT_HUMAN = "Связаться с человеком"
BTN_ADD_TO_CART = "Добавить в корзину"


def main_menu() -> ReplyKeyboardMarkup:
    """Основное постоянное меню пользователя."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_SHOWCASE), KeyboardButton(text=BTN_CART)],
            [KeyboardButton(text=BTN_CONTACT_HUMAN)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Введите запрос...",
    )


def add_to_cart_keyboard(item_id: int) -> InlineKeyboardMarkup:
    """Inline-кнопка под карточкой услуги."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=BTN_ADD_TO_CART,
                    callback_data=f"cart:add:{item_id}",
                )
            ]
        ]
    )
