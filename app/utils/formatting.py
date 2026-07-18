"""Конвертация Markdown в HTML, поддерживаемый Telegram.

Telegram понимает ограниченный набор HTML-тегов (b, i, u, s, a, code, pre,
blockquote), но не поддерживает заголовки и списки Markdown. Этот модуль
превращает распространённую Markdown-разметку в допустимый Telegram HTML,
чтобы в сообщениях не оставалось «сырых» символов вроде ** или ##.
"""

from __future__ import annotations

import html
import re

_WORD = r"[0-9A-Za-zА-Яа-яЁё]"


def _escape(text: str) -> str:
    return html.escape(text, quote=False)


def md_to_telegram_html(text: str) -> str:
    """Преобразовать Markdown-текст в Telegram-совместимый HTML."""
    if not text:
        return ""

    placeholders: list[str] = []

    def stash(value: str) -> str:
        placeholders.append(value)
        return f"\x00{len(placeholders) - 1}\x00"

    # 1. Блоки кода ```...``` — целиком в <pre>, без внутреннего форматирования.
    def repl_codeblock(match: re.Match[str]) -> str:
        code = match.group(1)
        code = re.sub(r"^[^\n]*\n", "", code, count=1) if "\n" in code else code
        return stash(f"<pre>{_escape(code.strip(chr(10)))}</pre>")

    text = re.sub(r"```(.*?)```", repl_codeblock, text, flags=re.DOTALL)

    # 2. Инлайн-код `...` — в <code>.
    text = re.sub(r"`([^`]+?)`", lambda m: stash(f"<code>{_escape(m.group(1))}</code>"), text)

    # 3. Ссылки [текст](url) — в <a>.
    def repl_link(match: re.Match[str]) -> str:
        label = _escape(match.group(1))
        url = _escape(match.group(2))
        return stash(f'<a href="{url}">{label}</a>')

    text = re.sub(r"\[([^\]]+?)\]\(([^)\s]+?)\)", repl_link, text)

    # 4. Экранируем оставшийся текст (спецсимволы HTML).
    text = _escape(text)

    # 5. Построчная обработка заголовков и списков.
    lines: list[str] = []
    for line in text.split("\n"):
        heading = re.match(r"^\s*#{1,6}\s+(.*)$", line)
        if heading:
            lines.append(f"<b>{heading.group(1).strip()}</b>")
            continue
        bullet = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
        if bullet:
            lines.append(f"{bullet.group(1)}• {bullet.group(2)}")
            continue
        lines.append(line)
    text = "\n".join(lines)

    # 6. Жирный и курсив (после экранирования спецсимволы * и _ сохраняются).
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(rf"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(rf"(?<!{_WORD})_(?!_)(.+?)_(?!{_WORD})", r"<i>\1</i>", text)

    # 7. Возвращаем сохранённые фрагменты (код и ссылки).
    for idx, value in enumerate(placeholders):
        text = text.replace(f"\x00{idx}\x00", value)

    return text.strip()


def bold(text: str) -> str:
    """Обернуть текст в жирный тег с экранированием."""
    return f"<b>{_escape(text)}</b>"
