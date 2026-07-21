"""Запросы к базе данных: поиск по базе знаний, логирование, статистика, CRUD."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .db import get_connection

# Слова, которые не несут смысла при поиске.
# Важно: короткие служебные («про», «при») дают ложные совпадения через LIKE %про%.
_SEARCH_STOPWORDS = {
    "и", "или", "для", "по", "на", "в", "во", "с", "со", "а", "но", "как",
    "что", "кто", "это", "его", "её", "их", "мне", "мой", "она", "они",
    "какие", "какой", "какая", "где", "когда", "почему", "есть", "быть",
    "про", "при", "под", "над", "без", "через", "между", "после", "перед",
    "ещё", "еще", "чем", "эти", "эта", "этот", "тот", "там", "тут",
    "расскажи", "рассказать", "скажи", "подробнее", "подробно", "пожалуйста",
    "the", "and", "for", "with", "what", "who", "how", "are", "is", "about",
}


def _tokenize(text: str) -> list[str]:
    """Разбить запрос на значимые слова (без стоп-слов и коротких токенов)."""
    words = re.findall(r"[0-9A-Za-zА-Яа-яЁё]+", text.lower())
    return [w for w in words if len(w) >= 3 and w not in _SEARCH_STOPWORDS]


# Частые окончания (рус./англ.), отсекаемые для грубого сопоставления по основе.
# Отсортированы по длине: сначала пробуем срезать более длинное окончание.
_ENDINGS = sorted(
    (
        "иями", "ями", "ами", "ього", "его", "ого", "ому", "ему", "ыми", "ими",
        "ая", "яя", "ое", "ее", "ый", "ий", "ой", "ым", "им", "ом", "ем",
        "ах", "ях", "ов", "ев", "ью", "ия", "ие", "ей", "уй",
        "ing", "ies", "ers", "ed", "es", "er", "s",
        "а", "я", "ы", "и", "у", "ю", "е", "о", "ь", "й",
    ),
    key=len,
    reverse=True,
)


def _stem(word: str) -> str:
    """Грубая основа слова: отсекаем частое окончание, сохраняя корень >= 3 симв.

    Нужно, чтобы «языки»/«язык», «цена»/«цены», «услуг»/«услуги» сопоставлялись.
    """
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            return word[: -len(ending)]
    return word


@dataclass
class KnowledgeItem:
    id: int
    keywords: str
    title: str
    content: str


# Слова-сигналы обобщающих вопросов (проверяются как отдельные слова/основы).
_AGGREGATION_HINTS = (
    "средн", "сумм", "итого", "всего", "прайс", "сравн",
    "дорог", "дешев", "миним", "максим", "список", "перечисл",
)
# Отдельные короткие слова — только целиком, иначе «все» ловится внутри других слов.
_AGGREGATION_WORDS = frozenset({"все", "весь", "вся"})

# Вопросы про услуги/портфолио — берём только соответствующие разделы.
_SERVICES_HINTS = (
    "услуг", "сервис", "портфолио", "проект", "предостав", "заказ", "разработк",
)

# Вопросы про исполнителя / резюме — профиль (без портфолио-проектов).
_PROFILE_HINTS = (
    "исполнител", "резюме", "профиль", "кандидат", "соискател",
    "опыт", "навык", "образован", "о себе", "обо мне", "кто ты", "кто он",
)

# Маркеры разделов про услуги/цены в заголовке или keywords.
_SERVICE_SECTION_MARKERS = (
    "портфолио", "сборка", "стоимость", "цена", "прайс", "услуг", "лендинг",
    "telegram", "трендвотч", "trend",
)

# Маркеры профильных разделов.
_PROFILE_SECTION_MARKERS = (
    "общая информация", "навык", "опыт", "образован", "курс", "контакт",
)


def _is_aggregation_query(text: str) -> bool:
    """Нужно ли расширить контекст разделами про цены/услуги."""
    lowered = text.lower()
    tokens = _tokenize(lowered) or re.findall(r"[0-9A-Za-zА-Яа-яЁё]+", lowered)
    if any(token in _AGGREGATION_WORDS for token in tokens):
        return True
    return any(hint in lowered for hint in _AGGREGATION_HINTS)


def _is_services_query(text: str) -> bool:
    """Вопрос про перечень услуг / портфолио / цены на услуги."""
    lowered = text.lower()
    return any(hint in lowered for hint in _SERVICES_HINTS)


def _is_profile_query(text: str) -> bool:
    """Вопрос про исполнителя / резюме / опыт / навыки."""
    lowered = text.lower()
    # «расскажи подробнее про исполнителя» и похожие формулировки.
    if "исполнител" in lowered or "резюме" in lowered or "профиль" in lowered:
        return True
    return any(hint in lowered for hint in _PROFILE_HINTS)


def _match_score(item: KnowledgeItem, words: list[str]) -> int:
    """Оценка релевантности: совпадения в title/keywords весят больше, чем в content."""
    title_kw = f"{item.keywords} {item.title}".lower()
    content = item.content.lower()
    score = 0
    for word in words:
        stem = _stem(word)
        in_title = word in title_kw or (stem != word and stem in title_kw)
        in_content = word in content or (stem != word and stem in content)
        if in_title:
            score += 3
        elif in_content:
            score += 1
    return score


async def search_knowledge(text: str, limit: int = 5) -> list[KnowledgeItem]:
    """Поиск релевантных записей по словам запроса с ранжированием.

    Запрос разбивается на слова; ищутся записи, содержащие слово или его основу
    в ключевых словах, заголовке или тексте. Совпадения в title/keywords важнее.
    """
    words = _tokenize(text)
    if not words:
        stripped = text.strip().lower()
        if not stripped:
            return []
        words = [stripped]

    # Ищем и по слову, и по основе («языки» → «язык»), чтобы не пропускать записи.
    patterns: list[str] = []
    for word in words:
        # Слишком короткие основы (2 символа) дают ложные совпадения — не используем.
        patterns.append(f"%{word}%")
        stem = _stem(word)
        if stem != word and len(stem) >= 4:
            patterns.append(f"%{stem}%")

    conditions = " OR ".join(
        ["lower(keywords || ' ' || title || ' ' || content) LIKE ?"] * len(patterns)
    )

    async with get_connection() as db:
        cursor = await db.execute(
            f"SELECT id, keywords, title, content FROM knowledge WHERE {conditions}",
            patterns,
        )
        rows = await cursor.fetchall()

    scored: list[tuple[int, KnowledgeItem]] = []
    for r in rows:
        item = KnowledgeItem(id=r[0], keywords=r[1], title=r[2], content=r[3])
        score = _match_score(item, words)
        if score <= 0:
            continue
        scored.append((score, item))

    scored.sort(key=lambda pair: (pair[0], -pair[1].id), reverse=True)
    return [item for _, item in scored[:limit]]


async def _items_by_markers(markers: tuple[str, ...], limit: int = 8) -> list[KnowledgeItem]:
    """Выбрать записи, у которых в title/keywords есть один из маркеров."""
    all_items = await list_knowledge(limit=50)
    matched = [item for item in all_items if any(m in f"{item.keywords} {item.title}".lower() for m in markers)]
    return matched[:limit]


async def select_context_for_query(text: str, limit: int = 5) -> list[KnowledgeItem]:
    """Подобрать фрагменты базы, полезные для ответа LLM (не всю БД).

    - Вопросы про исполнителя/резюме → профиль (информация, навыки, опыт, образование).
    - Вопросы про услуги/портфолио → только разделы услуг и прайса.
    - Обобщающие вопросы (среднее, прайс) → разделы про цены.
    - Остальное → обычный ранжированный поиск.
    """
    if _is_profile_query(text):
        profile = await _items_by_markers(_PROFILE_SECTION_MARKERS, limit=limit + 3)
        if profile:
            return profile

    if _is_services_query(text) or _is_aggregation_query(text):
        services = await _items_by_markers(_SERVICE_SECTION_MARKERS, limit=limit + 3)
        if services:
            return services

    primary = await search_knowledge(text, limit=limit)
    return primary


async def log_query(user_id: int, query: str, found: bool) -> None:
    """Записать факт запроса для статистики."""
    async with get_connection() as db:
        await db.execute(
            "INSERT INTO query_log (user_id, query, found) VALUES (?, ?, ?)",
            (user_id, query, int(found)),
        )
        await db.commit()


async def log_unanswered(user_id: int, query: str) -> None:
    """Зафиксировать запрос, на который не нашлось ответа."""
    async with get_connection() as db:
        await db.execute(
            "INSERT INTO unanswered (user_id, query) VALUES (?, ?)",
            (user_id, query),
        )
        await db.commit()


async def get_popular_queries(limit: int = 10) -> list[tuple[str, int]]:
    """Топ популярных запросов: (текст запроса, количество)."""
    async with get_connection() as db:
        cursor = await db.execute(
            """
            SELECT query, COUNT(*) AS cnt
            FROM query_log
            GROUP BY query
            ORDER BY cnt DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
    return [(r[0], r[1]) for r in rows]


async def count_unanswered(only_unresolved: bool = True) -> int:
    """Количество незакрытых запросов."""
    query = "SELECT COUNT(*) FROM unanswered"
    if only_unresolved:
        query += " WHERE resolved = 0"
    async with get_connection() as db:
        cursor = await db.execute(query)
        row = await cursor.fetchone()
    return row[0] if row else 0


async def add_knowledge(keywords: str, title: str, content: str) -> int:
    """Добавить запись в базу знаний. Возвращает id новой записи."""
    async with get_connection() as db:
        cursor = await db.execute(
            "INSERT INTO knowledge (keywords, title, content) VALUES (?, ?, ?)",
            (keywords, title, content),
        )
        await db.commit()
        return cursor.lastrowid or 0


async def list_knowledge(limit: int = 20) -> list[KnowledgeItem]:
    """Список записей базы знаний."""
    async with get_connection() as db:
        cursor = await db.execute(
            "SELECT id, keywords, title, content FROM knowledge ORDER BY id LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
    return [KnowledgeItem(id=r[0], keywords=r[1], title=r[2], content=r[3]) for r in rows]
