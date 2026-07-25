"""Тест сохранности данных после «перезапуска» (закрытие соединения и повторное чтение)."""

from __future__ import annotations

from pathlib import Path

from app.database import db, persistence


async def test_data_survives_connection_restart(db_ready: Path, user_id: int) -> None:
    await persistence.add_to_cart(
        user_id, service_id=7, title="Сохранённая услуга", price_text="10 000 ₽"
    )
    checkout = await persistence.checkout_cart(user_id)
    assert checkout is not None
    order_id, _ = checkout

    # Имитация перезапуска процесса: снова указать путь к той же БД и init_db.
    db.configure(str(db_ready))
    await db.init_db()

    order = await persistence.get_order_for_user(order_id, user_id)
    assert order is not None
    assert order.status == "ожидает оплаты"
    assert len(order.items) == 1
    assert order.items[0].title == "Сохранённая услуга"
    assert order.items[0].price_text == "10 000 ₽"

    assert await persistence.list_cart(user_id) == []
