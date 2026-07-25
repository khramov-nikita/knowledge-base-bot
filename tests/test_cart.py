"""Тесты корзины: добавление, удаление, сумма, дубликаты."""

from __future__ import annotations

from app.database import persistence
from app.utils.pricing import total_from_price_texts


async def test_add_service_to_cart(user_id: int) -> None:
    added = await persistence.add_to_cart(
        user_id, service_id=1, title="Консультация", price_text="15 000 ₽"
    )
    assert added is True

    items = await persistence.list_cart(user_id)
    assert len(items) == 1
    assert items[0].service_id == 1
    assert items[0].title == "Консультация"
    assert items[0].price_text == "15 000 ₽"


async def test_remove_service_from_cart(user_id: int) -> None:
    await persistence.add_to_cart(
        user_id, service_id=1, title="Консультация", price_text="15 000 ₽"
    )
    items = await persistence.list_cart(user_id)
    assert len(items) == 1

    removed = await persistence.remove_from_cart(user_id, items[0].id)
    assert removed is True
    assert await persistence.list_cart(user_id) == []


async def test_cart_total_sum(user_id: int) -> None:
    await persistence.add_to_cart(
        user_id, service_id=1, title="Услуга A", price_text="15 000 ₽"
    )
    await persistence.add_to_cart(
        user_id, service_id=2, title="Услуга B", price_text="5 000 ₽"
    )
    items = await persistence.list_cart(user_id)
    total = total_from_price_texts([item.price_text for item in items])
    assert total == 20_000


async def test_duplicate_add_same_service(user_id: int) -> None:
    first = await persistence.add_to_cart(
        user_id, service_id=1, title="Консультация", price_text="15 000 ₽"
    )
    second = await persistence.add_to_cart(
        user_id, service_id=1, title="Консультация", price_text="15 000 ₽"
    )
    assert first is True
    assert second is False

    items = await persistence.list_cart(user_id)
    assert len(items) == 1


async def test_remove_from_empty_cart(user_id: int) -> None:
    removed = await persistence.remove_from_cart(user_id, cart_item_id=999)
    assert removed is False
    assert await persistence.list_cart(user_id) == []
