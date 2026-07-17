"""Регистрация роутеров хэндлеров."""

from __future__ import annotations

from aiogram import Dispatcher

from . import owner, user


def register_routers(dp: Dispatcher) -> None:
    # Владелец — раньше пользователя, чтобы админ-команды имели приоритет.
    dp.include_router(owner.router)
    dp.include_router(user.router)
