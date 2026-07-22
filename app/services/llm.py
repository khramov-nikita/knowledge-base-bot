"""Интеграция с LLM (DeepSeek, OpenAI-совместимый API).

Модель отвечает на вопрос пользователя, опираясь на переданные фрагменты базы
знаний (схема RAG). Если ключ LLM не задан или запрос к API завершился ошибкой,
функция возвращает None — вызывающий код сам решает, что показать пользователю.
"""

from __future__ import annotations

import logging

from openai import AsyncOpenAI

from app.services import guardrails
from config import load_config

logger = logging.getLogger(__name__)

NO_ANSWER_TEXT = (
    "К сожалению, точного ответа в базе пока нет. "
    "Ваш запрос зафиксирован — владелец скоро дополнит базу."
)

# Сигнал модели о том, что ответа в предоставленных данных нет.
NO_ANSWER_SIGNAL = "NO_ANSWER"


def is_no_answer(text: str | None) -> bool:
    """Проверить, сообщила ли модель об отсутствии ответа (сигнал NO_ANSWER)."""
    if not text:
        return False
    normalized = text.strip().strip(".!").upper()
    return normalized == NO_ANSWER_SIGNAL or normalized.startswith(NO_ANSWER_SIGNAL)

# Системный промпт и усиливающее сообщение живут в guardrails (единый источник).
SYSTEM_PROMPT = guardrails.SYSTEM_PROMPT
_INJECTION_REMINDER = guardrails.INJECTION_REMINDER

# Таймаут запроса к LLM в секундах.
_REQUEST_TIMEOUT = 30.0
# Сколько последних сообщений диалога передавать модели.
_CONTEXT_LIMIT = 20
# Лимит токенов ответа: обычный / развёрнутый (для «подробнее»).
_MAX_TOKENS = 600
_MAX_TOKENS_DETAILED = 1200


def wants_detailed_answer(query: str) -> bool:
    """Нужен ли более длинный ответ (подробный рассказ)."""
    lowered = query.lower()
    return any(
        marker in lowered
        for marker in (
            "подробн", "расскажи", "рассказать", "опиши", "исполнител",
            "резюме", "профиль", "обо мне", "о себе",
            "навык", "опыт", "развит", "обязанност",
        )
    )

_config = load_config()
_client: AsyncOpenAI | None = None
if _config.llm_api_key:
    _client = AsyncOpenAI(
        api_key=_config.llm_api_key,
        base_url=_config.llm_base_url,
        timeout=_REQUEST_TIMEOUT,
    )
else:
    logger.warning("LLM_API_KEY не задан — LLM отключён, ответы берутся из базы напрямую.")


def is_enabled() -> bool:
    """Доступна ли генерация через LLM (задан ли ключ)."""
    return _client is not None


async def answer(
    query: str,
    knowledge: list[str],
    history: list[dict[str, str]] | None = None,
) -> str | None:
    """Сгенерировать ответ на основе фрагментов базы знаний (RAG).

    Args:
        query: вопрос пользователя.
        knowledge: релевантные фрагменты базы знаний для опоры.
        history: предыдущие сообщения диалога в виде {"role": ..., "content": ...}
            (роли "user"/"assistant") для памяти уточняющих вопросов.

    Returns:
        Ответ модели, либо None если LLM недоступен или произошла ошибка.
    """
    if _client is None:
        return None

    knowledge_block = "\n\n---\n\n".join(knowledge) if knowledge else "(нет данных)"
    messages: list[dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "system",
            "content": "Фрагменты базы знаний:\n\n" + knowledge_block,
        },
    ]

    # Guardrail: при подозрении на инъекцию усиливаем инструкции.
    if guardrails.detect_injection(query):
        logger.warning("Обнаружена возможная промпт-инъекция в запросе.")
        messages.append({"role": "system", "content": _INJECTION_REMINDER})

    # Память диалога: последние сообщения. Пользовательские — как недоверенные данные.
    for msg in (history or [])[-_CONTEXT_LIMIT:]:
        content = msg.get("content", "")
        if not content:
            continue
        if msg.get("role") == "assistant":
            messages.append({"role": "assistant", "content": content})
        else:
            messages.append({"role": "user", "content": guardrails.wrap_user_input(content)})

    messages.append({"role": "user", "content": guardrails.wrap_user_input(query)})

    try:
        max_tokens = _MAX_TOKENS_DETAILED if wants_detailed_answer(query) else _MAX_TOKENS
        response = await _client.chat.completions.create(
            model=_config.llm_model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.3,
        )
    except Exception:  # noqa: BLE001 — любая ошибка API не должна ронять обработку
        logger.exception("Ошибка запроса к LLM")
        return None

    result = (response.choices[0].message.content or "").strip()

    # Выходной фильтр: не допускаем утечку системного промпта/инструкций.
    filtered = guardrails.filter_output(result)
    if filtered is None and result:
        logger.warning("Ответ модели заблокирован выходным фильтром (утечка/пусто).")
    return filtered
