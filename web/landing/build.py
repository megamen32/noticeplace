#!/usr/bin/env python3
"""Build a standalone HTML. Only Python's standard library is needed.
The angular display lettering is original SVG geometry, not an embedded font.
"""
from pathlib import Path
import argparse
import re

ROOT = Path(__file__).resolve().parent
GLYPHS = {
 'А':'M2 22V7L7 2H13L18 7V22M2 14H18',
 'В':'M2 22V2H14L18 6V9L14 12H2M14 12L18 16V18L14 22H2',
 'Г':'M18 2H2V22',
 'Д':'M0 26V22H20V26M3 22L6 2H16V22',
 'Е':'M18 2H2V22H18M2 12H15',
 'Ё':'M18 2H2V22H18M2 12H15M6 -3H6.2M14 -3H14.2',
 'И':'M2 2V22L18 2V22',
 'К':'M2 2V22M18 2L2 13M7 10L18 22',
 'М':'M2 22V2L10 13L18 2V22',
 'Н':'M2 2V22M18 2V22M2 12H18',
 'О':'M6 2H14L18 6V18L14 22H6L2 18V6Z',
 'Р':'M2 22V2H14L18 6V10L14 14H2',
 'С':'M18 2H6L2 6V18L6 22H18',
 'Т':'M0 2H20M10 2V22',
 'У':'M2 2L10 13M18 2L5 22',
 'Ч':'M2 2V10L5 13H18M18 2V22',
 'Ш':'M1 2V22H19V2M10 2V22',
 'Ь':'M2 2V22H14L18 18V15L14 11H2',
}

def display_title() -> str:
    lines = ['КОГДА ВСЁ','НАЧИНАЕТ ШУМИТЬ','ЕСТЬ КУДА СМОТРЕТЬ']
    groups = []
    for row, text in enumerate(lines):
        x = 0
        parts = []
        for char in text:
            if char == ' ':
                x += 13
            else:
                parts.append(f'<path d="{GLYPHS[char]}" transform="translate({x} 0)"/>')
                x += 23
        if row == 1:
            parts.append(f'<path class="cursor-path" d="M{x+1} 22h13"/>')
        cls = ' class="title-accent"' if row==2 else ''
        groups.append(f'<g{cls} transform="translate(2 {5+row*34})">{"".join(parts)}</g>')
    return '<svg class="display-title" viewBox="0 0 397 98" aria-hidden="true" focusable="false">' + ''.join(groups) + '</svg>'


def build(output: Path) -> None:
    html = (ROOT / 'index.html').read_text(encoding='utf-8')
    title = display_title()
    # The modular index also receives the same original lettering.
    if '<!-- DISPLAY_TITLE -->' in html:
        html = html.replace('<!-- DISPLAY_TITLE -->',title)
        html = html.replace('<h1 id="hero-title">','<h1 id="hero-title" class="has-display-title">')
        (ROOT / 'index.html').write_text(html,encoding='utf-8')
    else:
        html = re.sub(r'<svg class="display-title".*?</svg>', lambda _: title, html, count=1, flags=re.S)
        (ROOT / 'index.html').write_text(html,encoding='utf-8')
    css = (ROOT / 'styles.css').read_text(encoding='utf-8')
    js = (ROOT / 'app.js').read_text(encoding='utf-8')
    assert '</style' not in css.lower() and '</script' not in js.lower()
    standalone = html.replace('<link rel="stylesheet" href="styles.css">',f'<style>\n{css}\n</style>')
    standalone = re.sub(r'(?m)^[ \t]*<script src="app.js" defer></script>\n?', '', standalone)
    standalone = standalone.replace('</body>',f'<script>\n{js}\n</script>\n</body>')
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(standalone,encoding='utf-8')
    print(f'{output}: {output.stat().st_size:,} bytes')

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'dist'/'NoticePlace.html')
    build(parser.parse_args().output)
