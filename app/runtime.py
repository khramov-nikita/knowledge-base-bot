"""Разделяемые во время выполнения значения конфигурации.

Заполняется один раз при старте в bot.py, чтобы хэндлеры имели доступ
к настройкам (например, ID владельца) без циклических импортов.
"""

from __future__ import annotations

OWNER_ID: int = 0
OWNER_CONTACT: str = ""


def set_owner_id(owner_id: int) -> None:
    global OWNER_ID
    OWNER_ID = owner_id


def set_owner_contact(owner_contact: str) -> None:
    global OWNER_CONTACT
    OWNER_CONTACT = owner_contact
