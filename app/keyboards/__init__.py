"""Клавиатуры бота."""

from __future__ import annotations

from typing import Protocol

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
BTN_REMOVE = "Убрать"
BTN_CHECKOUT = "Оформить заказ"
BTN_PAY = "Оплатить"
BTN_PAID = "Я оплатил"
BTN_OPEN_PAYMENT = "Перейти к оплате"


class _CartKeyboardItem(Protocol):
    id: int
    title: str


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


def cart_keyboard(items: list[_CartKeyboardItem]) -> InlineKeyboardMarkup:
    """Inline-кнопки корзины: «Убрать» у каждой позиции и «Оформить заказ»."""
    rows: list[list[InlineKeyboardButton]] = []
    for item in items:
        label = item.title.strip() or "услуга"
        if len(label) > 28:
            label = label[:27] + "…"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{BTN_REMOVE} · {label}",
                    callback_data=f"cart:remove:{item.id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=BTN_CHECKOUT,
                callback_data="cart:checkout",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def order_pay_keyboard(order_id: int) -> InlineKeyboardMarkup:
    """Кнопка «Оплатить» под оформленным заказом."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=BTN_PAY,
                    callback_data=f"pay:create:{order_id}",
                )
            ]
        ]
    )


def order_payment_keyboard(order_id: int, payment_url: str) -> InlineKeyboardMarkup:
    """Ссылка на оплату и кнопка «Я оплатил»."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BTN_OPEN_PAYMENT, url=payment_url)],
            [
                InlineKeyboardButton(
                    text=BTN_PAID,
                    callback_data=f"pay:check:{order_id}",
                )
            ],
        ]
    )
