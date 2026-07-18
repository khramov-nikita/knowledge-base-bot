"""Импорт базы знаний из Markdown-файла в таблицу knowledge.

Файл разбивается на записи по заголовкам второго уровня (## ). Каждый такой
раздел (вместе с вложенными подразделами ### ) становится отдельной записью:
заголовок → title, тело раздела → content, автоматически подобранные слова из
заголовка и раздела верхнего уровня → keywords.

Запуск из корня проекта:
    python -m scripts.import_md                     # импорт из БАЗА_ЗНАНИЙ.md, если база пуста
    python -m scripts.import_md --reset             # очистить knowledge и импортировать заново
    python -m scripts.import_md --force             # добавить, даже если записи уже есть
    python -m scripts.import_md --file путь.md      # импорт из указанного файла
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
from dataclasses import dataclass

from app.database import db, queries
from app.utils.logging import setup_logging
from config import load_config

logger = logging.getLogger(__name__)

DEFAULT_FILE = "БАЗА_ЗНАНИЙ.md"

# Стоп-слова, которые не несут смысла как ключевые.
_STOPWORDS = {
    "и", "или", "для", "по", "на", "в", "с", "также", "как", "что",
    "the", "and", "for", "with",
}


@dataclass
class Section:
    h1: str | None
    title: str
    body: str


def _strip_numbering(text: str) -> str:
    """Убрать ведущую нумерацию вида '1. ' из заголовка."""
    return re.sub(r"^\s*\d+[.)]\s*", "", text).strip()


def parse_markdown(text: str) -> list[Section]:
    """Разбить markdown на разделы по заголовкам '## '."""
    sections: list[Section] = []
    current_h1: str | None = None
    current: Section | None = None
    body_lines: list[str] = []

    def flush() -> None:
        nonlocal current, body_lines
        if current is not None:
            current.body = "\n".join(body_lines).strip()
            if current.body or current.title:
                sections.append(current)
        current = None
        body_lines = []

    for line in text.splitlines():
        if line.startswith("## "):
            flush()
            current = Section(h1=current_h1, title=_strip_numbering(line[3:]), body="")
        elif line.startswith("# "):
            flush()
            current_h1 = _strip_numbering(line[2:])
        elif current is not None:
            body_lines.append(line)

    flush()
    return sections


def build_keywords(section: Section) -> str:
    """Сформировать список ключевых слов из заголовка и контекста раздела."""
    source = f"{section.title} {section.h1 or ''}"
    words = re.findall(r"[0-9A-Za-zА-Яа-яЁё]+", source.lower())
    seen: list[str] = []
    for word in words:
        if len(word) >= 3 and word not in _STOPWORDS and word not in seen:
            seen.append(word)
    return ", ".join(seen)


async def _clear_knowledge() -> None:
    async with db.get_connection() as conn:
        await conn.execute("DELETE FROM knowledge")
        await conn.commit()


async def import_md(path: str, force: bool = False, reset: bool = False) -> None:
    config = load_config()
    db.configure(config.db_path)
    await db.init_db()

    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        logger.error("Файл не найден: %s", path)
        return

    sections = parse_markdown(text)
    if not sections:
        logger.warning("В файле не найдено разделов (## ) для импорта.")
        return

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

    count = 0
    for section in sections:
        if not section.body:
            continue
        title = section.title
        if section.h1 and section.h1.lower() not in title.lower():
            title = f"{section.h1}: {section.title}"
        keywords = build_keywords(section)
        new_id = await queries.add_knowledge(keywords, title, section.body)
        logger.info("Добавлена запись #%s: %s", new_id, title)
        count += 1

    logger.info("Готово. Импортировано записей: %d", count)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Импорт базы знаний из Markdown-файла.")
    parser.add_argument("--file", default=DEFAULT_FILE, help="Путь к markdown-файлу.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Добавить записи, даже если в базе уже есть данные.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Очистить таблицу knowledge и импортировать заново.",
    )
    return parser.parse_args()


def main() -> None:
    setup_logging()
    args = _parse_args()
    asyncio.run(import_md(path=args.file, force=args.force, reset=args.reset))


if __name__ == "__main__":
    main()
