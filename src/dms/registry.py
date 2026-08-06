"""컨테이너 레지스트리 v2 태그 조회. 실패 내성이 계약이다 -- 레지스트리가 죽었다고
롤아웃 화면 전체가 죽으면 안 된다(설계 §7). 실패는 예외가 아니라 None으로 알리고,
호출자가 빈 목록+경고로 강등하거나(targets) 검증을 건너뛴다(unknown_tag).

None(응답 불가)과 []( 응답했고 태그가 0개)는 다른 값이다 -- 이 구분이 무너지면
unknown_tag 검증이 조용히 fail-open 이 되거나 반대로 잘못 차단한다.

캐시를 두지 않는다: 방금 끝난 빌드의 태그가 드롭다운에 바로 보여야 하고, 무엇보다
제출 경로의 unknown_tag 검증이 낡은 목록을 보면 실제로 존재하는 태그를 잘못
차단한다 -- 설계 §7이 "잘못된 차단이 잘못된 통과보다 나쁘다"고 못박은 그 방향이다.
같은 응답 안에서 리포가 반복되는 것(api/controller가 같은 dms 리포)은 호출자가
요청 단위로 합쳐서 처리한다.
"""
import logging

import httpx

logger = logging.getLogger(__name__)

# 폴링 화면(targets)이 부르므로 짧게 잡는다 -- 레지스트리가 블랙홀이면 그동안 api
# 워커 스레드를 하나씩 물고 있게 된다. 같은 LAN의 평문 HTTP 레지스트리라 정상이면
# 수십 ms 안에 답한다. connect를 더 짧게 두는 이유: 죽은 호스트(패킷 드롭)에서
# 가장 오래 매달리는 구간이 connect다.
_TIMEOUT = httpx.Timeout(3.0, connect=2.0)


def _get_json(url: str, timeout):
    # 테스트 심(seam) -- monkeypatch 지점을 한 곳으로 모은다.
    response = httpx.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


def fetch_repo_tags(registry: str, repository: str) -> "list[str] | None":
    # 레지스트리는 평문 HTTP다(빌드 스크립트가 --tls-verify=false를 쓰는 그 레지스트리).
    url = f"http://{registry}/v2/{repository}/tags/list"
    try:
        data = _get_json(url, _TIMEOUT)
    except Exception as exc:
        # 여기서 넓게 삼키는 것이 이 모듈의 존재 이유다 -- 연결 실패/타임아웃/404/
        # 비JSON 본문 중 무엇이든 호출자에게는 "레지스트리가 답하지 않았다" 하나다.
        logger.warning("registry tags fetch failed repo=%s: %s", repository, exc)
        return None
    tags = data.get("tags") if isinstance(data, dict) else None
    if not isinstance(tags, list):
        # v2는 태그가 없는 리포에 {"tags": null}을 주기도 한다 -- 형식 불량과 함께
        # None으로 접는다. "태그 0개"를 확신할 수 없으면 검증을 강제하지 않는 쪽이 안전하다.
        return None
    # 정렬해 결정적으로 만든다 -- 레지스트리 응답 순서는 보장이 없다.
    return sorted(str(t) for t in tags)
