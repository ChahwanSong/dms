"""부분 일치 검색의 공용 조각(2026-10-25, 작업 목록 검색) -- 검색어 → 토큰, LIKE 패턴.

LIKE 패턴은 **파라미터 값**으로 만들어 넘긴다. SQL 본문에 '%' 리터럴을 쓰거나 `'%' || :t || '%'` 로 이어 붙이면
psycopg(pyformat, db._adapt 가 :name → %(name)s)가 깨진다. 와일드카드(\\ % _)는 리터럴로 이스케이프하고 SQL 쪽은
`LIKE :p ESCAPE '\\'` 와 짝으로 쓴다 -- sqlite 는 기본 이스케이프 문자가 없고, 이스케이프를 빼면 검색어 '100%' 가 전부
일치로 새고 밑줄이 든 경로(/mgmt_storage)나 계정 이름('a_c')이 한 글자 와일드카드로 넘친다.

대소문자를 무시하려면 컬럼과 패턴 **양쪽**을 **같은 함수**로 소문자로 맞춘다 -- `lower(col) LIKE lower(:p) ESCAPE '\\'`. LIKE
만 쓰면 PG 는 대소문자를 구분하고 sqlite 는 ASCII 만 무시해, 기본 테스트(sqlite)는 초록인데 테스트베드·프로덕션(PG)에서만
결과가 달라진다. 패턴을 파이썬 str.lower() 로 낮추면 안 된다(2026-10-25 리뷰): DB lower() 는 방언·LC_CTYPE 마다 다르고(sqlite·
'C' 로캘 PG 는 ASCII 만, UTF-8 로캘 PG 는 글자 단위) 파이썬은 전체 Unicode 규칙이라, 화면에 보이는 「Ärger」·「ΟΔΟΣ」를 그대로
쳐도 자기 행에 안 걸렸다. lower() 는 \\ % _ 를 바꾸지 않으므로 이스케이프 의미는 그대로다. ILIKE 는 PG 전용이다."""
from __future__ import annotations

import re

# 한 검색칸의 토큰 상한. 토큰마다 LIKE 조각 하나와 바인딩 하나가 붙으므로 길이(라우트 max_length)와 함께 SQL 크기를 묶는다.
# 넘치는 토큰은 버린다 -- 토큰 사이는 AND 라, 버리면 결과가 넓어질 뿐 남의 행을 새로 보이게 하지는 않는다(격리는 목록
# 쿼리의 requester_id 정확 일치가 따로 집행한다).
MAX_SEARCH_TOKENS = 8

# 배치 id(uuid4 hex 32자) 앞부분으로 보이는 토큰 -- 화면은 이름 없는 배치·기록 없는 묶음을 id 앞 12자로 보인다. 6자 미만은
# 낱말(「abc」·「dead」)과 우연히 겹쳐 엉뚱한 배치를 잡으므로 id 로 보지 않는다(6자 접두가 우연히 맞을 확률 16^-6).
BATCH_ID_PREFIX_RE = re.compile(r"[0-9a-f]{6,32}")


def escape_like(value: str) -> str:
    """LIKE 와일드카드를 리터럴로(`ESCAPE '\\'` 짝)."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def contains_pattern(value: str) -> str:
    """부분 일치 패턴 -- 값이 어디에든 들어 있으면."""
    return "%" + escape_like(value) + "%"


def prefix_pattern(value: str) -> str:
    """앞부분 일치 패턴."""
    return escape_like(value) + "%"


def search_tokens(text: str | None) -> list[str]:
    """검색어 → 토큰(공백으로 나눔, 순서 유지·중복 제거, 최대 MAX_SEARCH_TOKENS 개). 빈 값·공백뿐 = [] = 필터 없음.
    대소문자는 **그대로** 둔다 -- 낮추기는 SQL 이 컬럼과 같은 lower() 로 한다(모듈 docstring).

    「일부 단어만 맞아도 보여 달라(exact match 완화)」(2026-10-25 사용자 요청): 토큰마다 **부분** 일치이고 토큰 사이는 AND 다
    -- 「이관 1차」가 「스토리지 이관(1차)」에, 「growth e2e」가 「cephfs-dms:e2e/growth」에 걸린다. 화면 대상 열에서 sync 표기
    (「s1:a → s2:b」)를 통째로 붙여 넣어도 맞도록 화살표는 구분자로 본다. NUL 은 PG text 가 받지 못해(psycopg DataError →
    500) 구분자로 지운다."""
    if text is None:
        return []
    cleaned = text.replace("\x00", " ").replace("→", " ")
    return list(dict.fromkeys(cleaned.split()))[:MAX_SEARCH_TOKENS]
