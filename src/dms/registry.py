"""컨테이너 레지스트리 v2 태그 조회. 실패 내성이 계약이다 -- 레지스트리가 죽었다고
롤아웃 화면 전체가 죽으면 안 된다(설계 §7). 실패는 예외가 아니라 None으로 알리고,
호출자가 빈 목록+경고로 강등하거나(targets) 검증을 건너뛴다(unknown_tag).

None(응답 불가)과 []( 응답했고 태그가 0개)는 다른 값이다 -- 이 구분이 무너지면
unknown_tag 검증이 조용히 fail-open 이 되거나 반대로 잘못 차단한다. 그래서 []는
**레지스트리가 그렇게 말했을 때만** 돌려준다(2026-10-08): 레지스트리의 「리포 없음」
404(v2 헤더 또는 NAME_UNKNOWN 오류 본문)와 200 {"tags": null}. 레지스트리가 아닌 서버의
404(웹서버·ingress 기본 백엔드)를 []로 접으면 화면은 경고 없이 빈 목록이고 실제로 있는
태그 제출이 unknown_tag 로 막힌다.

페이지 나눔(2026-10-08): 레지스트리가 Link: <...>; rel="next" 로 나눠 주면 같은
레지스트리 안에서만, 상한(_MAX_PAGES)까지 따라간다. 상한에 걸리거나 다음 주소가 다른
호스트면 그때까지의 목록을 truncated=True 로 돌려준다 -- 호출자는 잘린 목록에 없는
태그를 "없다"고 단정하지 않는다(제출은 검증 안 됨으로 통과).

토큰 인증(2026-10-08): 401 + WWW-Authenticate: Bearer realm=... 이면 그 realm 에서
**익명** 토큰을 받아 한 번 재시도한다(자격 증명은 다루지 않는다 -- 빌드 push 가 익명으로
되는 레지스트리면 조회도 익명 토큰으로 된다). 토큰은 로그에 남기지 않는다.

캐시를 두지 않는다: 방금 끝난 빌드의 태그가 드롭다운에 바로 보여야 하고, 무엇보다
제출 경로의 unknown_tag 검증이 낡은 목록을 보면 실제로 존재하는 태그를 잘못
차단한다 -- 설계 §7이 "잘못된 차단이 잘못된 통과보다 나쁘다"고 못박은 그 방향이다.
같은 응답 안에서 리포가 반복되는 것(api/controller가 같은 dms 리포)은 호출자가
요청 단위로 합쳐서 처리한다.
"""
import logging
import re
import time

import httpx

logger = logging.getLogger(__name__)

# 페이지 상한 -- 폴링 화면(targets)이 부르므로 끝없이 따라가지 않는다. 레지스트리 기본
# 페이지가 50~100개라도 수천 개까지는 담는다. 페이지 수와 별도로 둘째 페이지부터는 전체 시간
# 예산도 본다 -- 요청마다 타임아웃이 있어도 페이지 수만큼 곱해지면 api 워커를 분 단위로 문다.
# 첫 페이지는 예산과 무관하게 지금까지처럼 한 번 읽는다(단일 페이지 레지스트리의 동작 불변).
_MAX_PAGES = 50
_PAGES_BUDGET_SECONDS = 8.0


class TagList(list):
    """태그 목록(list 그대로 쓰인다). truncated=True 면 페이지 상한·다른 호스트 다음 주소
    때문에 뒤를 다 읽지 못했다 -- 여기에 없는 태그가 레지스트리에 있을 수 있다."""
    truncated = False

# 폴링 화면(targets)이 부르므로 짧게 잡는다 -- 레지스트리가 블랙홀이면 그동안 api
# 워커 스레드를 하나씩 물고 있게 된다. 같은 LAN의 평문 HTTP 레지스트리라 정상이면
# 수십 ms 안에 답한다. connect를 더 짧게 두는 이유: 죽은 호스트(패킷 드롭)에서
# 가장 오래 매달리는 구간이 connect다.
_TIMEOUT = httpx.Timeout(3.0, connect=2.0)


def _request(method: str, url: str, headers=None):
    # 테스트 심(seam) -- 모든 레지스트리 HTTP 호출이 여기를 지난다(monkeypatch 지점 하나).
    return httpx.request(method, url, headers=headers, timeout=_TIMEOUT)


# 챌린지: 여러 챌린지가 한 헤더로 합쳐져 와도(「Basic …, Bearer …」) Bearer 뒤를 읽는다. 값은 따옴표·토큰
# 형식 둘 다, 이름은 대소문자 무시(RFC 7235).
_BEARER = re.compile(r"(?i)\bbearer\s+(.*)", re.DOTALL)
_CHALLENGE_PARAM = re.compile(r'(\w+)\s*=\s*(?:"([^"]*)"|([^,\s"]+))')
# 헤더에 그대로 실을 수 있는 토큰 글자(보이는 ASCII). 그 밖이면 버린다 -- 실으면 httpx 가 헤더 값을
# 예외 문구에 담아 토큰이 로그로 샌다.
_TOKEN_SAFE = re.compile(r"[\x21-\x7e]+")


def _anonymous_token(challenge: str) -> "str | None":
    """WWW-Authenticate: Bearer realm="...",service="...",scope="..." → 익명 토큰(없으면 None).
    realm 은 http(s) URL 만 받는다. 실패는 None(호출자가 원래 401 을 실패로 다룬다)."""
    m = _BEARER.search(challenge or "")
    if m is None:
        return None
    params: dict = {}
    for key, quoted, bare in _CHALLENGE_PARAM.findall(m.group(1)):
        params.setdefault(key.lower(), quoted or bare)
    realm = params.get("realm", "")
    if not (realm.startswith("http://") or realm.startswith("https://")):
        return None
    query = {k: params[k] for k in ("service", "scope") if params.get(k)}
    try:
        # realm 에 이미 있는 query(account·client_id 등)는 지키고 service·scope 를 더한다.
        response = _request("GET", str(httpx.URL(realm).copy_merge_params(query)))
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        # 토큰 엔드포인트의 응답 본문·토큰은 남기지 않는다(예외 이름만).
        logger.warning("registry anonymous token fetch failed: %s", type(exc).__name__)
        return None
    token = (data.get("token") or data.get("access_token")) if isinstance(data, dict) else None
    return token if isinstance(token, str) and _TOKEN_SAFE.fullmatch(token) else None


def _send(method: str, url: str, headers=None, auth: "str | None" = None):
    """레지스트리 요청 + 401 Bearer 면 익명 토큰으로 한 번 재시도(모듈 주석). (응답, 쓴 Authorization)
    -- 페이지를 이어 읽는 호출자가 같은 토큰을 다음 요청에 다시 싣는다(페이지마다 401·발급 왕복 금지)."""
    base = dict(headers or {})
    if auth is not None:
        base["Authorization"] = auth
    response = _request(method, url, base or None)
    if response.status_code == 401:
        token = _anonymous_token(response.headers.get("WWW-Authenticate", ""))
        if token is not None:
            auth = f"Bearer {token}"
            response = _request(method, url, {**base, "Authorization": auth})
    return response, auth


def _is_registry_not_found(response) -> bool:
    """레지스트리 자신의 「리포 없음」 404 인가. v2 레지스트리는 오류 응답에도
    Docker-Distribution-Api-Version 헤더를 싣고, 본문은 {"errors":[{"code":"NAME_UNKNOWN"}]}
    다(일부 제품은 NOT_FOUND). 둘 다 없으면 레지스트리가 아닌 서버의 404 다."""
    if response.headers.get("Docker-Distribution-Api-Version"):
        return True
    try:
        errors = response.json().get("errors")
    except Exception:
        return False
    return isinstance(errors, list) and any(
        isinstance(e, dict) and e.get("code") in ("NAME_UNKNOWN", "NOT_FOUND") for e in errors)


_LINK_NEXT = re.compile(r'<([^>]*)>[^<]*?\brel="?next"?', re.IGNORECASE)


def _next_page(link_header: "str | None", current: str) -> "tuple[str | None, bool]":
    """Link 헤더의 다음 페이지 주소. (주소, 잘림). 같은 레지스트리(스킴·호스트·포트)일 때만 따라간다
    -- 다른 호스트로 보내는 다음 주소는 따라가지 않고(요청 위조 방지) 잘림으로 본다. 「next」를 말하는데
    읽을 수 없는 헤더도 잘림이다(모르면 "목록 끝"이라 단정하지 않는다 -- 뒤 페이지 태그를 잘못 막는다)."""
    if not link_header:
        return None, False
    m = _LINK_NEXT.search(link_header)
    if m is None:
        return None, "next" in link_header.lower()
    try:
        base = httpx.URL(current)
        nxt = base.join(m.group(1))
    except Exception:
        return None, True
    if (nxt.scheme, nxt.host, nxt.port) != (base.scheme, base.host, base.port):
        return None, True
    return str(nxt), False


def fetch_repo_tags(registry: str, repository: str) -> "TagList | None":
    # 레지스트리는 평문 HTTP다(빌드 스크립트가 --tls-verify=false를 쓰는 그 레지스트리).
    url = f"http://{registry}/v2/{repository}/tags/list"
    tags: set = set()
    truncated: "str | None" = None          # 잘린 이유(로그용). None = 끝까지 읽었다
    seen = {url}
    auth = None
    deadline = time.monotonic() + _PAGES_BUDGET_SECONDS
    for page in range(_MAX_PAGES):
        if page > 0 and time.monotonic() > deadline:
            truncated = f"time budget {_PAGES_BUDGET_SECONDS}s"
            break
        try:
            response, auth = _send("GET", url, auth=auth)
            if response.status_code == 404:
                if page == 0 and _is_registry_not_found(response):
                    # 2026-09-09 사용자 보고(신규 사이트): 레지스트리는 살아 있는데 아직 아무
                    # 이미지도 push 되지 않아 리포가 없다(404 NAME_UNKNOWN). 이것을 "연결
                    # 불가"로 접으면 포탈이 첫 빌드 전까지 잘못된 오류를 보인다 -- 도달은
                    # 됐고 태그가 0개인 것이므로 빈 목록이다(None ≠ [] 규약).
                    return TagList()
                raise ValueError("404 from a non-registry server (no v2 header / NAME_UNKNOWN body)"
                                 if page == 0 else f"404 on page {page + 1}")
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or "tags" not in data:
                raise ValueError("malformed body")
            page_tags = data["tags"]
            if page_tags is None:
                # v2 는 태그가 하나도 없는 리포(전부 지운 뒤 등)에 {"tags": null} 을 준다 --
                # 레지스트리가 답했고 태그가 0개다(None 은 응답 불가 전용).
                page_tags = []
            if not isinstance(page_tags, list):
                raise ValueError("malformed tags")
        except Exception as exc:
            if page == 0:
                # 여기서 넓게 삼키는 것이 이 모듈의 존재 이유다 -- 연결 실패/타임아웃/
                # 비JSON 본문 중 무엇이든 호출자에게는 "레지스트리가 답하지 않았다" 하나다.
                logger.warning("registry tags fetch failed repo=%s: %s", repository, exc)
                return None
            # 둘째 페이지부터의 실패는 이미 읽은 목록을 버리지 않는다 -- 뒤를 모를 뿐이다(잘림).
            truncated = f"page {page + 1} failed: {exc}"
            break
        tags.update(str(t) for t in page_tags)
        nxt, cut = _next_page(response.headers.get("Link"), url)
        if nxt is None:
            truncated = "unreadable or off-registry next link" if cut else None
            break
        if nxt in seen:
            truncated = "next link loops"
            break
        seen.add(nxt)
        url = nxt
    else:
        truncated = f"more than {_MAX_PAGES} pages"
    if truncated is not None:
        logger.warning("registry tags list truncated repo=%s: %s", repository, truncated)
    # 정렬해 결정적으로 만든다 -- 레지스트리 응답 순서는 보장이 없다(화면 순서는 호출자가 정한다).
    out = TagList(sorted(tags))
    out.truncated = truncated is not None
    return out


# 매니페스트 조회/삭제 Accept 헤더. buildah 가 만든 이미지는 OCI 매니페스트라
# docker v2 헤더만으로는 404 가 난다(실측) -- OCI 와 docker 를 모두 받는다. 삭제는
# 태그가 아니라 digest 를 대상으로 하므로(레지스트리 v2 계약), 먼저 digest 를 뽑는다.
_MANIFEST_ACCEPT = ", ".join((
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
))


def manifest_digest(registry: str, repository: str, tag: str) -> "str | None":
    """태그의 content digest(sha256:...) 또는 None(응답 불가/없음). 삭제의 전제다."""
    url = f"http://{registry}/v2/{repository}/manifests/{tag}"
    try:
        response = _head_manifest(url)
        response.raise_for_status()
    except Exception as exc:
        logger.warning("registry digest fetch failed repo=%s tag=%s: %s",
                       repository, tag, exc)
        return None
    return response.headers.get("Docker-Content-Digest")


def _head_manifest(url: str):
    # 테스트 심 -- HEAD 는 본문 없이 digest 헤더만 받는다(전송량 최소). 401 이면 익명 토큰 재시도.
    return _send("HEAD", url, {"Accept": _MANIFEST_ACCEPT})[0]


def delete_manifest(registry: str, repository: str, digest: str) -> str:
    """digest 로 매니페스트를 삭제한다. 결과를 코드 문자열로 돌려준다(예외 아님):
    'ok'(202/204), 'disabled'(405 -- storage.delete.enabled 꺼짐), 'not_found'(404),
    'error'(그 외). 블롭 회수(garbage-collect)는 별개다 -- 여기선 태그를 지운다."""
    url = f"http://{registry}/v2/{repository}/manifests/{digest}"
    try:
        response = _delete_manifest(url)
    except Exception as exc:
        logger.warning("registry delete failed repo=%s digest=%s: %s",
                       repository, digest, exc)
        return "error"
    if response.status_code in (202, 204):
        return "ok"
    if response.status_code == 405:
        return "disabled"
    if response.status_code == 404:
        return "not_found"
    logger.warning("registry delete unexpected status=%s repo=%s digest=%s",
                   response.status_code, repository, digest)
    return "error"


def _delete_manifest(url: str):
    # 테스트 심. 401 이면 익명 토큰 재시도(삭제 권한이 없는 익명 토큰이면 다시 401 → 'error').
    return _send("DELETE", url)[0]
