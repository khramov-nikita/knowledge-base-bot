"""Тесты оплаты: статус «оплачен» только при подтверждённом платеже."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from app.database import persistence
from app.handlers import user as user_handlers
from app.services.yookassa_payments import PaymentInfo


async def _prepare_awaiting_order(user_id: int) -> int:
    await persistence.add_to_cart(
        user_id, service_id=1, title="Услуга", price_text="15 000 ₽"
    )
    result = await persistence.checkout_cart(user_id)
    assert result is not None
    order_id, _ = result
    await persistence.set_order_payment(order_id, "pay_test_123")
    return order_id


async def test_set_order_status_paid_only_from_awaiting(user_id: int) -> None:
    order_id = await _prepare_awaiting_order(user_id)

    updated = await persistence.set_order_status(
        order_id, "оплачен", only_if_status="ожидает оплаты"
    )
    assert updated is True

    order = await persistence.get_order_for_user(order_id, user_id)
    assert order is not None
    assert order.status == "оплачен"

    again = await persistence.set_order_status(
        order_id, "оплачен", only_if_status="ожидает оплаты"
    )
    assert again is False


def _make_callback(user_id: int, order_id: int) -> MagicMock:
    user = MagicMock()
    user.id = user_id
    user.username = "test_user"
    user.full_name = "Test User"

    message = MagicMock()
    message.answer = AsyncMock()

    callback = MagicMock()
    callback.from_user = user
    callback.data = f"pay:check:{order_id}"
    callback.message = message
    callback.bot = MagicMock()
    callback.answer = AsyncMock()
    return callback


async def test_on_pay_check_marks_paid_when_succeeded(user_id: int) -> None:
    order_id = await _prepare_awaiting_order(user_id)
    callback = _make_callback(user_id, order_id)
    payment = PaymentInfo(
        payment_id="pay_test_123",
        status="succeeded",
        confirmation_url="https://example.com/pay",
    )

    with (
        patch.object(user_handlers.yookassa_payments, "get_payment", AsyncMock(return_value=payment)),
        patch.object(user_handlers, "notify_paid_order", AsyncMock()) as notify_mock,
        patch.object(user_handlers.runtime, "OWNER_ID", 99999),
        patch.object(user_handlers, "_log_exchange", AsyncMock()),
    ):
        await user_handlers.on_pay_check(callback)

    order = await persistence.get_order_for_user(order_id, user_id)
    assert order is not None
    assert order.status == "оплачен"
    notify_mock.assert_awaited_once()
    callback.answer.assert_awaited()


async def test_on_pay_check_keeps_awaiting_when_pending(user_id: int) -> None:
    order_id = await _prepare_awaiting_order(user_id)
    callback = _make_callback(user_id, order_id)
    payment = PaymentInfo(
        payment_id="pay_test_123",
        status="pending",
        confirmation_url="https://example.com/pay",
    )

    with (
        patch.object(user_handlers.yookassa_payments, "get_payment", AsyncMock(return_value=payment)),
        patch.object(user_handlers, "notify_paid_order", AsyncMock()) as notify_mock,
        patch.object(user_handlers.runtime, "OWNER_ID", 99999),
        patch.object(user_handlers, "_log_exchange", AsyncMock()),
    ):
        await user_handlers.on_pay_check(callback)

    order = await persistence.get_order_for_user(order_id, user_id)
    assert order is not None
    assert order.status == "ожидает оплаты"
    notify_mock.assert_not_awaited()
