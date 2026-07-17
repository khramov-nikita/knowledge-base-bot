"""Наполнение базы знаний демонстрационными данными.

Запуск из корня проекта:
    python -m scripts.seed          # добавит примеры, если база пуста
    python -m scripts.seed --force  # добавит примеры даже если записи уже есть
    python -m scripts.seed --reset  # очистит таблицу knowledge и зальёт примеры заново
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from app.database import db, queries
from app.utils.logging import setup_logging
from config import load_config

logger = logging.getLogger(__name__)

# Демонстрационные записи: (keywords, title, content)
DEMO_ITEMS: list[tuple[str, str, str]] = [
    (
        "отпуск, отдых, заявление",
        "Как оформить отпуск",
        "Заявление на отпуск подаётся не менее чем за 2 недели через отдел кадров "
        "или в электронной системе. Минимальная продолжительность части отпуска — 14 дней.",
    ),
    (
        "больничный, болезнь, нетрудоспособность",
        "Оформление больничного листа",
        "О болезни сообщите руководителю в первый день. Электронный больничный "
        "автоматически поступает работодателю; бумажный принесите в отдел кадров после выздоровления.",
    ),
    (
        "зарплата, выплата, аванс, сроки",
        "Сроки выплаты зарплаты",
        "Аванс выплачивается 25-го числа текущего месяца, окончательный расчёт — "
        "10-го числа следующего месяца. При совпадении с выходным выплата переносится на предыдущий рабочий день.",
    ),
    (
        "пропуск, доступ, офис, вход",
        "Заказ пропуска в офис",
        "Пропуск для гостя заказывается через заявку на ресепшн минимум за 1 рабочий день. "
        "Для сотрудников постоянный пропуск оформляется в службе безопасности.",
    ),
    (
        "vpn, удалёнка, доступ, подключение",
        "Подключение к корпоративному VPN",
        "Установите корпоративный VPN-клиент из внутреннего портала, войдите под доменной "
        "учётной записью. При проблемах с доступом обратитесь в ИТ-поддержку.",
    ),
    (
        "поддержка, ит, helpdesk, помощь",
        "Как обратиться в ИТ-поддержку",
        "Заявки принимаются через портал helpdesk или по внутреннему номеру 1000. "
        "Срочные инциденты (недоступность систем) отмечайте приоритетом «высокий».",
    ),
    (
        "командировка, поездка, авансовый отчёт",
        "Оформление командировки",
        "Командировка согласуется с руководителем и оформляется приказом. "
        "Авансовый отчёт с чеками сдаётся в бухгалтерию в течение 3 рабочих дней после возвращения.",
    ),
    (
        "справка, 2-ндфл, документы, бухгалтерия",
        "Получение справок и документов",
        "Справку о доходах (2-НДФЛ) и справку с места работы можно заказать в бухгалтерии "
        "или в кадровом портале. Срок подготовки — до 3 рабочих дней.",
    ),
]


async def _clear_knowledge() -> None:
    async with db.get_connection() as conn:
        await conn.execute("DELETE FROM knowledge")
        await conn.commit()


async def seed(force: bool = False, reset: bool = False) -> None:
    config = load_config()
    db.configure(config.db_path)
    await db.init_db()

    if reset:
        await _clear_knowledge()
        logger.info("Таблица knowledge очищена.")

    existing = await queries.list_knowledge(limit=1)
    if existing and not force and not reset:
        logger.info(
            "База уже содержит записи — пропускаю. "
            "Используйте --force для добавления или --reset для перезаписи."
        )
        return

    for keywords, title, content in DEMO_ITEMS:
        new_id = await queries.add_knowledge(keywords, title, content)
        logger.info("Добавлена запись #%s: %s", new_id, title)

    logger.info("Готово. Добавлено записей: %d", len(DEMO_ITEMS))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Наполнение базы знаний демо-данными.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Добавить примеры, даже если в базе уже есть записи.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Очистить таблицу knowledge и залить примеры заново.",
    )
    return parser.parse_args()


def main() -> None:
    setup_logging()
    args = _parse_args()
    asyncio.run(seed(force=args.force, reset=args.reset))


if __name__ == "__main__":
    main()
