"""Интеграция с LLM (DeepSeek, OpenAI-совместимый API).

Если ключ LLM не задан или запрос к API завершился ошибкой, возвращается
безопасный fallback-ответ, чтобы бот не падал и запрос был зафиксирован.
"""

from __future__ import annotations

import logging

from openai import AsyncOpenAI

from config import load_config

logger = logging.getLogger(__name__)

NO_ANSWER_TEXT = (
    "К сожалению, точного ответа в базе пока нет. "
    "Ваш запрос зафиксирован — владелец скоро дополнит базу."
)

SYSTEM_PROMPT = (
    "Ты — бот-консультант по базе знаний компании. "
    "Отвечай кратко, вежливо и по делу на русском языке. "
    "Если не уверен в ответе, честно сообщи об этом и не выдумывай факты."
)

# Таймаут запроса к LLM в секундах.
_REQUEST_TIMEOUT = 30.0
# Ограничение длины истории диалога, передаваемой в модель.
_CONTEXT_LIMIT = 6

_config = load_config()
_client: AsyncOpenAI | None = None
if _config.llm_api_key:
    _client = AsyncOpenAI(
        api_key=_config.llm_api_key,
        base_url=_config.llm_base_url,
        timeout=_REQUEST_TIMEOUT,
    )
else:
    logger.warning("LLM_API_KEY не задан — LLM отключён, используется fallback-ответ.")


async def ask(query: str, context: list[str] | None = None) -> str:
    """Сгенерировать ответ на запрос через LLM.

    Args:
        query: текст запроса пользователя.
        context: предыдущие запросы пользователя для контекста уточнений.

    Returns:
        Ответ модели либо безопасный fallback-текст при отсутствии ключа/ошибке.
    """
    if _client is None:
        return NO_ANSWER_TEXT

    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for prev in (context or [])[-_CONTEXT_LIMIT:]:
        messages.append({"role": "user", "content": prev})
    messages.append({"role": "user", "content": query})

    try:
        response = await _client.chat.completions.create(
            model=_config.llm_model,
            messages=messages,
        )
    except Exception:  # noqa: BLE001 — любая ошибка API не должна ронять обработку
        logger.exception("Ошибка запроса к LLM")
        return NO_ANSWER_TEXT

    answer = (response.choices[0].message.content or "").strip()
    return answer or NO_ANSWER_TEXT
