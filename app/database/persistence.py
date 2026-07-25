"""Персистентность пользователей, корзины, заказов и истории диалога."""

from __future__ import annotations

from dataclasses import dataclass

import aiosqlite

from .db import get_connection

# Сколько последних сообщений отдаём в контекст LLM (как раньше в FSM).
DIALOG_HISTORY_LIMIT = 20


@dataclass
class CartItem:
    id: int
    user_id: int
    service_id: int
    title: str
    price_text: str


@dataclass
class OrderItemInput:
    service_id: int | None
    title: str
    price_text: str
    quantity: int = 1


@dataclass
class OrderItem:
    id: int
    order_id: int
    service_id: int | None
    title: str
    price_text: str
    quantity: int


@dataclass
class Order:
    id: int
    user_id: int
    status: str
    payment_id: str | None
    items: list[OrderItem]


async def upsert_user(
    telegram_id: int,
    username: str | None,
    full_name: str | None,
) -> None:
    """Создать пользователя или обновить профиль и last_seen_at."""
    async with get_connection() as db:
        await db.execute(
            """
            INSERT INTO users (telegram_id, username, full_name)
            VALUES (?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username = excluded.username,
                full_name = excluded.full_name,
                last_seen_at = datetime('now')
            """,
            (telegram_id, username, full_name),
        )
        await db.commit()


async def add_to_cart(
    user_id: int,
    service_id: int,
    title: str,
    price_text: str,
) -> bool:
    """Добавить услугу в корзину. False — уже есть (без дубля)."""
    async with get_connection() as db:
        try:
            await db.execute(
                """
                INSERT INTO cart_items (user_id, service_id, title, price_text)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, service_id, title, price_text),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            # UNIQUE(user_id, service_id) — услуга уже в корзине.
            return False


async def list_cart(user_id: int) -> list[CartItem]:
    """Содержимое корзины пользователя."""
    async with get_connection() as db:
        cursor = await db.execute(
            """
            SELECT id, user_id, service_id, title, price_text
            FROM cart_items
            WHERE user_id = ?
            ORDER BY id
            """,
            (user_id,),
        )
        rows = await cursor.fetchall()
    return [
        CartItem(
            id=r[0],
            user_id=r[1],
            service_id=r[2],
            title=r[3],
            price_text=r[4] or "",
        )
        for r in rows
    ]


async def clear_cart(user_id: int) -> None:
    """Очистить корзину пользователя."""
    async with get_connection() as db:
        await db.execute("DELETE FROM cart_items WHERE user_id = ?", (user_id,))
        await db.commit()


async def remove_from_cart(user_id: int, cart_item_id: int) -> bool:
    """Удалить позицию из корзины. False — уже нет / чужой id."""
    async with get_connection() as db:
        cursor = await db.execute(
            "DELETE FROM cart_items WHERE id = ? AND user_id = ?",
            (cart_item_id, user_id),
        )
        await db.commit()
        return (cursor.rowcount or 0) > 0


async def create_order(user_id: int, status: str = "new") -> int:
    """Создать заказ. Возвращает id."""
    async with get_connection() as db:
        cursor = await db.execute(
            "INSERT INTO orders (user_id, status) VALUES (?, ?)",
            (user_id, status),
        )
        await db.commit()
        return cursor.lastrowid or 0


async def add_order_items(order_id: int, items: list[OrderItemInput]) -> None:
    """Добавить позиции в заказ."""
    if not items:
        return
    async with get_connection() as db:
        await db.executemany(
            """
            INSERT INTO order_items
                (order_id, service_id, title, price_text, quantity)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (order_id, item.service_id, item.title, item.price_text, item.quantity)
                for item in items
            ],
        )
        await db.commit()


async def checkout_cart(
    user_id: int,
    status: str = "ожидает оплаты",
) -> tuple[int, list[CartItem]] | None:
    """Оформить заказ из корзины в одной транзакции.

    Возвращает (order_id, позиции) или None, если корзина пуста.
    """
    async with get_connection() as db:
        cursor = await db.execute(
            """
            SELECT id, user_id, service_id, title, price_text
            FROM cart_items
            WHERE user_id = ?
            ORDER BY id
            """,
            (user_id,),
        )
        rows = await cursor.fetchall()
        if not rows:
            return None

        items = [
            CartItem(
                id=r[0],
                user_id=r[1],
                service_id=r[2],
                title=r[3],
                price_text=r[4] or "",
            )
            for r in rows
        ]

        cursor = await db.execute(
            "INSERT INTO orders (user_id, status) VALUES (?, ?)",
            (user_id, status),
        )
        order_id = cursor.lastrowid or 0
        if not order_id:
            await db.rollback()
            return None

        await db.executemany(
            """
            INSERT INTO order_items
                (order_id, service_id, title, price_text, quantity)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (order_id, item.service_id, item.title, item.price_text, 1)
                for item in items
            ],
        )
        await db.execute("DELETE FROM cart_items WHERE user_id = ?", (user_id,))
        await db.commit()
        return order_id, items


async def get_order_for_user(order_id: int, user_id: int) -> Order | None:
    """Заказ пользователя с позициями. None — нет или чужой."""
    async with get_connection() as db:
        cursor = await db.execute(
            """
            SELECT id, user_id, status, payment_id
            FROM orders
            WHERE id = ? AND user_id = ?
            """,
            (order_id, user_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None

        items_cursor = await db.execute(
            """
            SELECT id, order_id, service_id, title, price_text, quantity
            FROM order_items
            WHERE order_id = ?
            ORDER BY id
            """,
            (order_id,),
        )
        item_rows = await items_cursor.fetchall()

    items = [
        OrderItem(
            id=r[0],
            order_id=r[1],
            service_id=r[2],
            title=r[3],
            price_text=r[4] or "",
            quantity=r[5] or 1,
        )
        for r in item_rows
    ]
    return Order(
        id=row[0],
        user_id=row[1],
        status=row[2],
        payment_id=row[3],
        items=items,
    )


async def set_order_payment(order_id: int, payment_id: str) -> None:
    """Сохранить id платежа ЮKassa для заказа."""
    async with get_connection() as db:
        await db.execute(
            """
            UPDATE orders
            SET payment_id = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (payment_id, order_id),
        )
        await db.commit()


async def set_order_status(
    order_id: int,
    status: str,
    *,
    only_if_status: str | None = None,
) -> bool:
    """Обновить статус заказа. True — строка обновлена.

    Если only_if_status задан, меняем только при совпадении текущего статуса
    (защита от гонки повторного «Я оплатил»).
    """
    async with get_connection() as db:
        if only_if_status is None:
            cursor = await db.execute(
                """
                UPDATE orders
                SET status = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (status, order_id),
            )
        else:
            cursor = await db.execute(
                """
                UPDATE orders
                SET status = ?, updated_at = datetime('now')
                WHERE id = ? AND status = ?
                """,
                (status, order_id, only_if_status),
            )
        await db.commit()
        return (cursor.rowcount or 0) > 0


async def append_dialog(user_id: int, role: str, content: str) -> None:
    """Дописать одно сообщение в историю диалога."""
    async with get_connection() as db:
        await db.execute(
            """
            INSERT INTO dialog_messages (user_id, role, content)
            VALUES (?, ?, ?)
            """,
            (user_id, role, content),
        )
        await db.commit()


async def append_dialog_exchange(
    user_id: int,
    user_text: str,
    assistant_text: str,
) -> None:
    """Дописать пару реплик user + assistant."""
    async with get_connection() as db:
        await db.execute(
            """
            INSERT INTO dialog_messages (user_id, role, content)
            VALUES (?, 'user', ?)
            """,
            (user_id, user_text),
        )
        await db.execute(
            """
            INSERT INTO dialog_messages (user_id, role, content)
            VALUES (?, 'assistant', ?)
            """,
            (user_id, assistant_text),
        )
        await db.commit()


async def get_recent_dialog(
    user_id: int,
    limit: int = DIALOG_HISTORY_LIMIT,
) -> list[dict[str, str]]:
    """Последние сообщения диалога в хронологическом порядке."""
    async with get_connection() as db:
        cursor = await db.execute(
            """
            SELECT role, content FROM (
                SELECT id, role, content
                FROM dialog_messages
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
            )
            ORDER BY id ASC
            """,
            (user_id, limit),
        )
        rows = await cursor.fetchall()
    return [{"role": r[0], "content": r[1]} for r in rows]
