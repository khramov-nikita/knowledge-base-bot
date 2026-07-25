"""Создание и проверка платежей через API ЮKassa."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass

from yookassa import Configuration, Payment
from yookassa.domain.exceptions import UnauthorizedError

logger = logging.getLogger(__name__)

# Статусы, при которых ссылку на оплату ещё можно переиспользовать.
ACTIVE_PAYMENT_STATUSES = frozenset({"pending", "waiting_for_capture"})


class YooKassaAuthError(RuntimeError):
    """Неверные shop_id / secret_key ЮKassa."""


@dataclass(frozen=True)
class PaymentInfo:
    payment_id: str
    status: str
    confirmation_url: str | None


def configure(shop_id: str, secret_key: str) -> None:
    """Настроить SDK ЮKassa (вызывается один раз при старте)."""
    Configuration.configure(shop_id, secret_key)
    if "*" in secret_key:
        logger.warning(
            "YOOKASSA_SECRET_KEY содержит символ '*': похоже на замаскированный ключ. "
            "Вставьте полный секретный ключ из кабинета ЮKassa."
        )


def _create_payment_sync(
    order_id: int,
    amount_rub: int,
    return_url: str,
) -> PaymentInfo:
    payment = Payment.create(
        {
            "amount": {
                "value": f"{amount_rub:.2f}",
                "currency": "RUB",
            },
            "confirmation": {
                "type": "redirect",
                "return_url": return_url,
            },
            "capture": True,
            "description": f"Заказ №{order_id}",
            "metadata": {
                "order_id": str(order_id),
            },
        },
        uuid.uuid4().hex,
    )
    confirmation = getattr(payment, "confirmation", None)
    confirmation_url = None
    if confirmation is not None:
        confirmation_url = getattr(confirmation, "confirmation_url", None)
    return PaymentInfo(
        payment_id=str(payment.id),
        status=str(payment.status),
        confirmation_url=confirmation_url,
    )


def _get_payment_sync(payment_id: str) -> PaymentInfo:
    payment = Payment.find_one(payment_id)
    confirmation = getattr(payment, "confirmation", None)
    confirmation_url = None
    if confirmation is not None:
        confirmation_url = getattr(confirmation, "confirmation_url", None)
    return PaymentInfo(
        payment_id=str(payment.id),
        status=str(payment.status),
        confirmation_url=confirmation_url,
    )


async def create_payment(
    order_id: int,
    amount_rub: int,
    return_url: str,
) -> PaymentInfo:
    """Создать платёж в ЮKassa на сумму заказа."""
    try:
        return await asyncio.to_thread(
            _create_payment_sync,
            order_id,
            amount_rub,
            return_url,
        )
    except UnauthorizedError as exc:
        logger.exception("Неверные ключи ЮKassa при создании платежа №%s", order_id)
        raise YooKassaAuthError(
            "ЮKassa отклонила ключи магазина (invalid_credentials)."
        ) from exc
    except Exception:
        logger.exception("Не удалось создать платёж ЮKassa для заказа №%s", order_id)
        raise


async def get_payment(payment_id: str) -> PaymentInfo:
    """Получить актуальный статус платежа в ЮKassa."""
    try:
        return await asyncio.to_thread(_get_payment_sync, payment_id)
    except UnauthorizedError as exc:
        logger.exception("Неверные ключи ЮKassa при проверке платежа %s", payment_id)
        raise YooKassaAuthError(
            "ЮKassa отклонила ключи магазина (invalid_credentials)."
        ) from exc
    except Exception:
        logger.exception("Не удалось получить платёж ЮKassa %s", payment_id)
        raise


def is_active_payment(status: str) -> bool:
    """Можно ли ещё оплатить этот платёж по сохранённой ссылке."""
    return status in ACTIVE_PAYMENT_STATUSES
