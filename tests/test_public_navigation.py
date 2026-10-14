"""Public product navigation must never advertise an operator's private cabinet."""
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
import pytest

WEB = Path(__file__).resolve().parents[1] / 'web' / 'landing'

class Links(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.items = []
        self.feed(html)
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.items.append(dict(attrs))

@pytest.mark.parametrize('source', ['index.html', 'dist/NoticePlace.html'])
def test_public_only_navigation(source):
    html = (WEB / source).read_text()
    urls = [urlsplit(a.get('href', '')) for a in Links(html).items]
    for url in urls:
        assert url.hostname != 'agent.bezrabotnyi.com'
        assert not (url.hostname == 'notify.bezrabotnyi.com' and url.path.startswith('/admin'))
    assert 'Открыть консоль' not in html
    assert 'Установить Notice Place' in html
    assert 'https://github.com/megamen32/noticeplace/blob/main/deploy/README.md' in html

@pytest.mark.parametrize('source', ['index.html', 'dist/NoticePlace.html'])
def test_airlock_is_official_partner_link(source):
    html = (WEB / source).read_text()
    urls = [urlsplit(a.get('href', '')) for a in Links(html).items]
    assert any(url.hostname == 'airlockrun.ru' for url in urls)
    assert not any(url.hostname in {'airlockrun.bezrabotnyi.com', 'airlock.bezrabotnyi.com'} for url in urls)
    assert 'Airlock · партнёрская интеграция' in html
