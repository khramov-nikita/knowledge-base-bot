"""Тесты оформления заказа из корзины."""

from __future__ import annotations

from app.database import db, persistence


async def test_checkout_creates_order_and_clears_cart(user_id: int) -> None:
    await persistence.add_to_cart(
        user_id, service_id=1, title="Услуга A", price_text="15 000 ₽"
    )
    await persistence.add_to_cart(
        user_id, service_id=2, title="Услуга B", price_text="5 000 ₽"
    )

    result = await persistence.checkout_cart(user_id)
    assert result is not None
    order_id, items = result
    assert order_id > 0
    assert len(items) == 2

    order = await persistence.get_order_for_user(order_id, user_id)
    assert order is not None
    assert order.status == "ожидает оплаты"
    assert len(order.items) == 2
    assert {item.title for item in order.items} == {"Услуга A", "Услуга B"}

    assert await persistence.list_cart(user_id) == []


async def test_checkout_empty_cart_returns_none(user_id: int) -> None:
    result = await persistence.checkout_cart(user_id)
    assert result is None

    async with db.get_connection() as conn:
        cursor = await conn.execute("SELECT COUNT(*) FROM orders WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
    assert row is not None
    assert row[0] == 0
