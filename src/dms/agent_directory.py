"""에이전트 nslcd 에 내려줄 LDAP 디렉터리 설정 블록(제어면 측, 2026-09-29).

왜 보고 응답인가: 포탈 릴리스는 DaemonSet 의 **이미지만** 패치하므로(rollout_runner
.image_patch_body) env 로 배선한 설정을 바꾸려면 매니페스트 apply 가 따로 필요했다.
에이전트는 이미 60초마다 /api/agent/report 를 부르고 그 응답으로 스토리지·신원 프로브
대상·artifact_base_path 를 받는다 -- 디렉터리 설정도 같은 길로 받으면 **이미지 릴리스만으로**
에이전트가 제어면과 같은 디렉터리·같은 검색 계정으로 수렴한다(bind env 가 없는 옛
DaemonSet 에서도). 값은 identity_ldap.ldap_directory_config 한 곳에서 나온다.

비밀번호 노출 최소화:
- 능력 선언: 보고 본문에 "directory" 객체를 실은 에이전트(= nslcd 를 관리할 수 있는
  새 에이전트)에게만 블록을 준다. 옛 에이전트에겐 키 자체가 없다.
- 해시 게이트: 에이전트는 적용 중인 설정의 해시를 보고하고, 서버는 해시가 **다를 때만**
  비밀번호가 든 전체 블록을, 같으면 {"hash"} 만 준다(평상시 응답엔 비밀번호가 없다).
- 해시는 HMAC(session_secret) -- 매 응답에 평문으로 실리므로 평문 sha256 이면 오프라인
  대입으로 약한 비밀번호를 되찾을 수 있다. 키는 서버에만 있고 에이전트는 받은 값을
  되돌려 보낼 뿐이다.
- 정직한 한계: 에이전트 채널은 클러스터 내부 평문 HTTP(http://dms-api:8080)다. 에이전트
  해시는 메모리에만 있어 None 에서 시작하고 성공한 적용 뒤에만 올라가므로, 비밀번호는
  **노드마다 에이전트가 (재)시작할 때 한 번**(이미지 릴리스·노드 재부팅·OOM 포함), **설정
  또는 session_secret 이 바뀔 때**, 그리고 **적용이 계속 실패하는 동안 매 보고 주기**에 평문으로
  지난다. 같은 채널로 관리자급 공유 토큰이 매 주기 이미 지나가므로 신뢰 경계를 넓히진 않는다
  (채널 TLS 화는 BACKLOG). 실패는 에이전트 로그와 노드 보고 directory.error 로 보인다.
"""
import hashlib
import hmac
import json
import os

from .identity_ldap import ldap_directory_config

# nslcd 렌더러(deploy/docker/agent-entrypoint.sh)가 쓰는 키만 싣는다 -- 그룹 멤버 속성은
# 신원 준비 판정(이름 해석)에 쓰이지 않고 nslcd 기본이 memberUid·member 를 함께 본다.
_PAYLOAD_KEYS = ("uris", "user_base", "group_base", "start_tls", "bind_dn", "bind_pw")
_HASH_DOMAIN = b"dms-agent-directory-v1\0"
_FALLBACK_KEY = os.urandom(32)   # session_secret 이 없는 duck-typed settings(테스트) 용


def _key(settings) -> bytes:
    secret = getattr(settings, "session_secret", "") or ""
    return secret.encode() if secret else _FALLBACK_KEY


def directory_payload(settings):
    cfg = ldap_directory_config(settings)
    if cfg is None:
        return None
    return {k: cfg[k] for k in _PAYLOAD_KEYS}


def directory_hash(payload, settings) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(_key(settings), _HASH_DOMAIN + canonical, hashlib.sha256).hexdigest()[:32]


def directory_block(settings, reported_hash):
    """보고 응답의 "directory" 값. None = 디렉터리 미구성(에이전트는 부트스트랩 설정 유지).
    reported_hash 가 현재 해시와 같으면 비밀번호 없는 {"hash"} 만."""
    payload = directory_payload(settings)
    if payload is None:
        return None
    current = directory_hash(payload, settings)
    if reported_hash == current:
        return {"hash": current}
    return {"hash": current, **payload}
