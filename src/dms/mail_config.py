"""메일 발송 설정의 단일 해석기(2026-10-01) -- 포탈 메일 설정(DB, mail_settings) > env(Settings) > 기본값.

인증 메일(routes_auth.request_verification_code)과 포탈 메일 설정 화면(routes_mail_settings)이 모두 이 함수만
통과한다 -- 아티팩트 base(artifact_base.resolve_artifact_base)와 같은 "DB 가 env 를 이긴다" 규칙이고, 요청마다
DB 를 다시 읽어 재시작 없이 반영된다. 칸별로 따로 고른다(예: 타임아웃·서비스명은 포탈로, 나머지는 env).

예외 하나 -- 토큰은 주소에 묶인다(2026-10-01 리뷰): env 토큰(DMS_MAIL_RELAY_TOKEN)은 주소가 env URL 그대로일
때만 쓰고, 포탈이 IP·포트·프로토콜 중 하나라도 정하면 env 토큰을 쓰지 않는다(env_token_unbound -- 포탈에 키를
넣어야 한다). "토큰은 Secret env, IP·포트는 포탈" 같은 나눔은 그래서 안 된다: 관리자 권한만으로 주소를 자기
서버로 돌려 env 토큰을 받아내는 경로가 되기 때문이다. 토큰을 env 로 두려면 주소도 env(DMS_MAIL_RELAY_URL)로 둔다.

릴레이 주소는 화면에서 IP·포트·프로토콜로 나눠 받는다(사용자 요청: "relay용 knox 서버 IP, relay port(8025)").
env 는 mailer 참고 구현대로 URL 하나(DMS_MAIL_RELAY_URL)라, 파싱해 같은 칸으로 맞춘 뒤 칸별로 고른다.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .domain import DomainValidationError
from .secret_box import open_at_rest, seal_at_rest

MAILER_BACKENDS = ("stub", "knox_relay")
RELAY_SCHEMES = ("http", "https")
DEFAULT_RELAY_SCHEME = "http"
DEFAULT_RELAY_PORT = 8025
# secret_box 의 용도(키 유도·AAD) -- 봉인 전송(password_transport PURPOSES)의 같은 이름과 별개 축이다.
TOKEN_PURPOSE = "mail_relay_token"

_HOST_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


@dataclass(frozen=True)
class MailConfig:
    backend: str
    relay_scheme: str
    relay_host: str
    relay_port: int
    relay_url: str              # host 를 모르면 "" -- mailer 가 relay_misconfigured 로 막는다
    relay_token: str            # 없거나 열 수 없으면 ""
    timeout_seconds: float
    service_name: str
    token_source: "str | None"  # "portal" | "env" | None
    token_unreadable: bool      # 포탈에 봉인은 있는데 열 수 없음(세션 시크릿 교체 등) -- 다시 입력해야 한다
    endpoint_source: str = "env"   # 릴레이 주소를 포탈이 정했나("portal") env URL 그대로인가("env")
    env_token_unbound: bool = False  # env 토큰이 있지만 포탈 주소에는 쓰지 않음(아래 resolve 주석)
    sources: dict = field(default_factory=dict)   # 칸 -> "portal" | "env"


_SCHEME_DEFAULT_PORT = {"http": 80, "https": 443}


def _env_relay_parts(settings):
    """DMS_MAIL_RELAY_URL("http://IP:8025") -> (scheme, host, port|None). 비었거나 깨졌으면 (None, None, None)."""
    raw = (getattr(settings, "mail_relay_url", "") or "").strip()
    if not raw:
        return None, None, None
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return None, None, None
    return (parts.scheme or None), (parts.hostname or None), port


def compose_relay_url(scheme: str, host: str, port: int) -> str:
    if not host:
        return ""
    shown = f"[{host}]" if ":" in host else host     # IPv6 는 대괄호
    return f"{scheme}://{shown}:{port}"


def resolve_mail_config(repos, settings) -> MailConfig:
    return resolve_from_row(repos.mail_settings.get() or {}, settings)


def resolve_from_row(row: dict, settings) -> MailConfig:
    """칸별 해석. 릴레이 주소와 토큰은 묶어서 다룬다(2026-10-01 리뷰):

    - 포탈이 프로토콜·IP·포트 중 하나도 정하지 않았으면 env DMS_MAIL_RELAY_URL 을 **그대로** 쓴다(참고 구현과
      같은 의미 -- 경로·스킴 기본 포트를 바꾸지 않는다). 하나라도 정했으면 포탈 칸 + env 에서 읽은 칸 + 기본값
      (http·8025)으로 조립한다.
    - env 토큰(DMS_MAIL_RELAY_TOKEN)은 env 주소에만 쓴다. 포탈이 주소를 바꿨는데 env 토큰을 그대로 실으면, 관리자
      권한만으로 주소를 자기 서버로 돌려 비밀(k8s Secret)을 받아낼 수 있다 -- 그 경우 포탈에 키를 넣어야 한다.
    - 발송 방식은 env 값을 그대로 쓴다(빈 문자열도) -- 오타·빈 값은 라우트가 500 mailer_misconfigured 로 막는다
      (조용히 stub 이 되면 인증번호가 화면에 에코된다)."""
    sources: dict = {}

    def pick(col, env_value, default):
        value = row.get(col)
        if value is not None:
            sources[col] = "portal"
            return value
        sources[col] = "env"
        return env_value if env_value not in (None, "") else default

    env_scheme, env_host, env_port = _env_relay_parts(settings)
    portal_endpoint = any(row.get(c) is not None for c in ("relay_scheme", "relay_host", "relay_port"))
    scheme = pick("relay_scheme", env_scheme, DEFAULT_RELAY_SCHEME)
    host = pick("relay_host", env_host, "")
    if row.get("relay_port") is not None:
        port = int(pick("relay_port", None, DEFAULT_RELAY_PORT))
    elif env_port is not None:
        port = int(pick("relay_port", env_port, DEFAULT_RELAY_PORT))
    elif env_host and not portal_endpoint:
        sources["relay_port"] = "env"
        port = _SCHEME_DEFAULT_PORT.get(scheme, DEFAULT_RELAY_PORT)   # env URL 에 포트가 없으면 스킴 기본 포트
    else:
        port = int(pick("relay_port", None, DEFAULT_RELAY_PORT))
    if portal_endpoint:
        relay_url, endpoint_source = compose_relay_url(scheme, host, port), "portal"
    else:
        relay_url, endpoint_source = (getattr(settings, "mail_relay_url", "") or "").strip(), "env"

    if row.get("backend") is not None:
        backend, sources["backend"] = row["backend"], "portal"
    else:
        backend, sources["backend"] = getattr(settings, "mailer_backend", "stub"), "env"
    timeout = float(pick("timeout_seconds", getattr(settings, "mail_relay_timeout_seconds", 20.0), 20.0))
    service = pick("service_name", getattr(settings, "mail_service_name", ""), "DMS")

    env_token = (getattr(settings, "mail_relay_token", "") or "").strip()
    token, token_source, unreadable, unbound = "", None, False, False
    if row.get("relay_token_enc"):
        token_source = "portal"
        opened = open_at_rest(row["relay_token_enc"], secret=settings.session_secret, purpose=TOKEN_PURPOSE)
        if opened is None:
            unreadable = True
        else:
            token = opened
    elif env_token and endpoint_source == "env":
        token_source, token = "env", settings.mail_relay_token
    elif env_token:
        unbound = True
    return MailConfig(backend=backend, relay_scheme=scheme, relay_host=host, relay_port=port,
                      relay_url=relay_url, relay_token=token, timeout_seconds=timeout,
                      service_name=service, token_source=token_source, token_unreadable=unreadable,
                      endpoint_source=endpoint_source, env_token_unbound=unbound, sources=sources)


# ------------------------------------------------------------------ 입력 검증(PUT)
# 실패는 DomainValidationError(reason_code) -- 라우트가 422 로 나른다. 사유 코드는 reasonCodes.json·api.ts 양쪽 등록.

def validate_backend(value: str) -> str:
    if value not in MAILER_BACKENDS:
        raise DomainValidationError("invalid_mail_backend", repr(value))
    return value


def validate_scheme(value: str) -> str:
    if value not in RELAY_SCHEMES:
        raise DomainValidationError("invalid_mail_relay_scheme", repr(value))
    return value


def validate_host(value: str) -> str:
    """IP(v4/v6) 또는 호스트명. 스킴·포트·경로·공백을 섞어 넣는 실수(http://1.2.3.4:8025)를 거부한다."""
    host = (value or "").strip()
    # '%'(IPv6 zone ID)는 ip_address 가 임의 문자를 받아 주므로 따로 막는다 -- 주소에 공백·개행이 섞인다.
    if not host or len(host) > 253 or "%" in host:
        raise DomainValidationError("invalid_mail_relay_host", repr(value))
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    labels = host.rstrip(".").split(".")
    if not all(_HOST_LABEL.match(label) for label in labels):
        raise DomainValidationError("invalid_mail_relay_host", repr(value))
    return host


def validate_port(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise DomainValidationError("invalid_mail_relay_port", repr(value))
    return value


def validate_timeout(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 1 <= value <= 120:
        raise DomainValidationError("invalid_mail_timeout", repr(value))
    return float(value)


def validate_service_name(value: str) -> str:
    name = (value or "").strip()
    if not name or len(name) > 60 or not name.isprintable():
        raise DomainValidationError("invalid_mail_service_name", repr(value))
    return name


def validate_token(value: str) -> str:
    """mailer.send_mail_via_relay 가 헤더에 싣는 조건과 같다(ASCII·출력 가능) + 공백 금지·길이 상한.
    값은 오류 메시지에도 싣지 않는다."""
    token = (value or "").strip()
    if (not token or len(token) > 4096 or not token.isascii() or not token.isprintable()
            or any(ch.isspace() for ch in token)):
        raise DomainValidationError("invalid_mail_relay_token")
    return token


def seal_token(token: str, settings) -> str:
    return seal_at_rest(token, secret=settings.session_secret, purpose=TOKEN_PURPOSE)
