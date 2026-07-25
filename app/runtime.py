"""Разделяемые во время выполнения значения конфигурации.

Заполняется один раз при старте в bot.py, чтобы хэндлеры имели доступ
к настройкам (например, ID владельца) без циклических импортов.
"""

from __future__ import annotations

OWNER_ID: int = 0
OWNER_CONTACT: str = ""
YOOKASSA_SHOP_ID: str = ""
YOOKASSA_SECRET_KEY: str = ""
BOT_USERNAME: str = ""


def set_owner_id(owner_id: int) -> None:
    global OWNER_ID
    OWNER_ID = owner_id


def set_owner_contact(owner_contact: str) -> None:
    global OWNER_CONTACT
    OWNER_CONTACT = owner_contact


def set_yookassa(shop_id: str, secret_key: str) -> None:
    global YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY
    YOOKASSA_SHOP_ID = shop_id
    YOOKASSA_SECRET_KEY = secret_key


def set_bot_username(username: str) -> None:
    global BOT_USERNAME
    BOT_USERNAME = username
