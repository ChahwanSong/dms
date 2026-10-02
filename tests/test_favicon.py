"""포탈 탭 아이콘(frontend/public/favicon.svg, 2026-10-02) -- 브라우저는 SVG 를 엄격한 XML 로 읽는다.

d150 실 Chrome 실증에서 서버는 200 image/svg+xml 을 줬는데 아이콘이 깨져 보였다: 주석 안의 '--' 가 XML
위반이라 파싱이 실패했다(빌드·타입검사·단위 테스트 어느 것도 잡지 못함). 그래서 여기서 XML 로 읽어 본다.
런타임 airgap 이라 외부 리소스 참조(href/src 의 http(s)://)도 금지 -- xmlns 네임스페이스 선언은 식별자라 예외.
"""
import re
import xml.dom.minidom
from pathlib import Path

FAVICON = Path(__file__).resolve().parent.parent / "frontend" / "public" / "favicon.svg"
INDEX = Path(__file__).resolve().parent.parent / "frontend" / "index.html"


def test_favicon_is_well_formed_svg():
    doc = xml.dom.minidom.parse(str(FAVICON))
    root = doc.documentElement
    assert root.tagName == "svg" and root.getAttribute("viewBox")


def test_favicon_references_nothing_external():
    text = FAVICON.read_text()
    refs = re.findall(r'(?:href|src)\s*=\s*"([^"]*)"', text)
    assert all(not r.startswith(("http://", "https://", "//")) for r in refs), refs


def test_index_html_links_the_bundled_favicon():
    assert '<link rel="icon" type="image/svg+xml" href="/favicon.svg" />' in INDEX.read_text()
