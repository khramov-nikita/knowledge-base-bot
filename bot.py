"""Точка входа бота-консультанта по базе знаний."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from app import runtime
from app.database import db
from app.handlers import register_routers
from app.utils.logging import setup_logging
from config import ConfigError, load_config

logger = logging.getLogger(__name__)


async def main() -> None:
    setup_logging()

    config = load_config()
    runtime.set_owner_id(config.owner_id)
    runtime.set_owner_contact(config.owner_contact)
    runtime.set_yookassa(config.yookassa_shop_id, config.yookassa_secret_key)

    db.configure(config.db_path)
    await db.init_db()
    logger.info("База данных инициализирована: %s", config.db_path)

    from app.services import yookassa_payments

    yookassa_payments.configure(config.yookassa_shop_id, config.yookassa_secret_key)

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    me = await bot.get_me()
    if me.username:
        runtime.set_bot_username(me.username)

    dp = Dispatcher(storage=MemoryStorage())
    register_routers(dp)

    logger.info("Бот запускается (polling)...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ConfigError as exc:
        logging.getLogger(__name__).error("Ошибка конфигурации: %s", exc)
    except (KeyboardInterrupt, SystemExit):
        logging.getLogger(__name__).info("Бот остановлен.")
