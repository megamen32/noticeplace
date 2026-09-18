"""Render the Markdown subset used by agent reports as safe Bot API HTML."""
from __future__ import annotations

import html
import re


_INLINE = re.compile(r"(`+)(.+?)\1|\*\*(.+?)\*\*|__(.+?)__|\[([^\]\n]+)\]\((https?://[^\s)]+)\)")


def _inline(text: str) -> str:
    parts: list[str] = []
    end = 0
    for match in _INLINE.finditer(text):
        parts.append(html.escape(text[end:match.start()]))
        code, code_text, bold, bold_alt, label, url = match.groups()
        if code:
            parts.append(f"<code>{html.escape(code_text)}</code>")
        elif bold is not None or bold_alt is not None:
            parts.append(f"<b>{_inline(bold if bold is not None else bold_alt)}</b>")
        else:
            parts.append(f'<a href="{html.escape(url, quote=True)}">{_inline(label)}</a>')
        end = match.end()
    parts.append(html.escape(text[end:]))
    return "".join(parts)


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def telegram_html(text: str) -> str:
    """Keep literal logs safe, format headings/code, turn tables into mobile lists."""
    lines = text.splitlines()
    rendered: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        fence = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fence:
            marker = fence.group(1)
            code: list[str] = []
            i += 1
            while i < len(lines) and not re.fullmatch(r"\s*" + re.escape(marker[0]) + "{" + str(len(marker)) + r",}\s*", lines[i]):
                code.append(lines[i])
                i += 1
            rendered.append("<pre>" + html.escape("\n".join(code)) + "</pre>")
        elif "|" in line and i + 1 < len(lines) and all(
            re.fullmatch(r":?-{3,}:?", cell) for cell in _cells(lines[i + 1])
        ) and len(_cells(lines[i + 1])) == len(_cells(line)):
            headers = _cells(line)
            rendered.append("<b>" + " · ".join(_inline(cell) for cell in headers) + "</b>")
            i += 2
            while i < len(lines) and "|" in lines[i] and len(_cells(lines[i])) == len(headers):
                cells = _cells(lines[i])
                values = [_inline(cells[0])]
                values.extend((f"{_inline(headers[j])}: " if len(headers) > 2 else "") + _inline(cell) for j, cell in enumerate(cells[1:], 1))
                rendered.append("• " + " — ".join(values))
                i += 1
            continue
        else:
            heading = re.match(r"^\s{0,3}#{1,6}\s+(.+?)(?:\s+#+)?$", line)
            if heading:
                rendered.append(f"<b>{_inline(heading.group(1))}</b>")
            elif re.match(r"^\s*[-*+]\s+", line):
                rendered.append(_inline(re.sub(r"^(\s*)[-*+]\s+", r"\1• ", line)))
            elif line.endswith(":") and line in {"Последний запрос пользователя:", "Последний ответ агента:", "По пунктам приказа:"}:
                rendered.append(f"<b>{html.escape(line)}</b>")
            else:
                rendered.append(_inline(line))
        i += 1
    return "\n".join(rendered)
