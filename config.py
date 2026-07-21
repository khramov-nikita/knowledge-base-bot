"""Загрузка и валидация настроек бота из переменных окружения (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


class ConfigError(RuntimeError):
    """Ошибка конфигурации: отсутствует или некорректна обязательная переменная."""


@dataclass(frozen=True)
class Config:
    bot_token: str
    owner_id: int
    owner_contact: str
    db_path: str
    llm_api_key: str | None
    llm_base_url: str
    llm_model: str


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ConfigError(
            f"Не задана обязательная переменная окружения {name}. "
            f"Скопируйте .env.example в .env и заполните значения."
        )
    return value


def load_config() -> Config:
    bot_token = _require("BOT_TOKEN")

    owner_raw = _require("OWNER_ID")
    try:
        owner_id = int(owner_raw)
    except ValueError as exc:
        raise ConfigError("OWNER_ID должен быть числом (Telegram ID владельца).") from exc

    owner_contact = os.getenv("OWNER_CONTACT", "")
    db_path = os.getenv("DB_PATH", "data/knowledge.db")
    llm_api_key = os.getenv("LLM_API_KEY") or None
    llm_base_url = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
    llm_model = os.getenv("LLM_MODEL", "deepseek-v4-flash")

    return Config(
        bot_token=bot_token,
        owner_id=owner_id,
        owner_contact=owner_contact,
        db_path=db_path,
        llm_api_key=llm_api_key,
        llm_base_url=llm_base_url,
        llm_model=llm_model,
    )
