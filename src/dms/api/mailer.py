"""사내 메일 발송 -- 메신저 서버의 Knox 메일 릴레이 경유 (2026-10-01).

웹서버는 Knox Mail API 에 직접 닿지 않는다(메신저 서버에서만 발송 가능). 그래서
메신저 서버에 상주하는 릴레이에 HTTP(POST /send)로 발송을 맡긴다.

  릴레이  /usr/lib/zabbix/alertscripts/knox_mail_dms_certi.py  (systemd knox-mail-dms-certi, TCP 8025)
  설정    같은 디렉터리의 knox_mail_dms_certi.env
  인증    Authorization: Bearer <knox_mail_dms_certi.env 의 RELAY_TOKEN>

이 파일의 자리: routes_auth.py 와 같은 디렉터리(password_transport.py 옆).
routes_auth.py 가 `from .mailer import ...` 로 쓴다.

  render_verification_email()  인증번호 메일의 (제목, HTML 본문)
  send_mail_via_relay()        릴레이 호출. 어떤 실패든 MailerError 하나로 올린다.
  SendThrottle                 수신자별 발송 상한 (코드 발급 전에 검사)
  check_relay_health()         GET /healthz (포탈 메일 설정의 "연결 확인", 2026-10-01 추가)
  render_test_email()          포탈 메일 설정의 "테스트 메일" (제목, HTML 본문) (2026-10-01 추가)

인증번호는 HTML 본문에만 들어간다. 제목·프리헤더·예외 메시지에는 넣지 않는다
(릴레이 로그와 observability 이벤트가 제목·실패 사유를 기록하기 때문).
표준 라이브러리만 쓴다 -- 웹서버에 의존성 추가 없음.
"""
from __future__ import annotations

import http.client
import json
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from string import Template

KST = timezone(timedelta(hours=9), "KST")
DEFAULT_SERVICE_NAME = "DMS"

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """3xx 를 따라가지 않는다(2026-10-01 리뷰, 참고 구현에서 의도적으로 바꾼 곳). urllib 기본 처리기는
    301/302/303 을 GET 으로 다시 보내면서 Authorization(릴레이 토큰)을 리다이렉트 대상에 그대로 싣고, 대상이
    {"ok": true} 를 주면 보내지 않은 메일이 '성공'이 된다. 따라가지 않으면 3xx 는 HTTPError 로 올라와
    relay_http_30x 사유가 된다(릴레이는 리다이렉트할 이유가 없다)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _build_relay_opener() -> urllib.request.OpenerDirector:
    """릴레이는 사내망 직통이다. 웹서버 환경에 http(s)_proxy 가 잡혀 있어도 타지 않게 빈 ProxyHandler 로
    환경 프록시를 끈다(build_opener 는 ProxyHandler 를 주지 않으면 환경변수 프록시를 읽어 넣는다)."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


_RELAY_OPENER = _build_relay_opener()

# 상대가 응답은 했는데 HTTP 가 아니다(포트를 SSH·SMTP 등으로 잘못 넣음) -- "갔을 수도 있다"(relay_no_response)가
# 아니라 relay_bad_response 다(2026-10-01 3차 리뷰: 그대로 두면 보내지도 않은 코드를 저장하고 200 을 줬다).
# RemoteDisconnected 는 BadStatusLine 의 하위(빈 상태 줄 = 응답 전에 끊김)라 호출부가 먼저 잡는다.
_NOT_HTTP = (http.client.BadStatusLine, http.client.LineTooLong, http.client.UnknownProtocol)


class MailerError(Exception):
    """메일 발송 실패.

    reason: 릴레이가 준 error 코드 또는 이쪽에서 붙인 코드. 사유별 조치:
      relay_misconfigured    mail_relay_url(http://IP:8025 형식)·mail_relay_token 설정 확인
      relay_unreachable      메신저 서버 8025 방화벽 / systemctl status knox-mail-dms-certi (요청이 나가지 않음)
      relay_no_response      요청은 보냈는데 응답 전에 끊기거나 타임아웃 -- **발송됐을 수도 있다**(2026-10-01 추가;
                             인증 메일은 이때 코드를 저장한다. 타임아웃이 릴레이의 Knox 타임아웃보다 짧은지 확인)
      relay_bad_response     8025 에 릴레이가 아닌 다른 것이 떠 있음
      unauthorized           mail_relay_token 과 env 의 RELAY_TOKEN 이 다름
      client_not_allowed     웹서버 IP 가 env 의 RELAY_ALLOWED_CLIENTS 에 없음
      recipient_not_allowed  수신 도메인이 env 의 RELAY_ALLOWED_DOMAINS 에 없음
      rate_limited           릴레이의 수신자별 상한(RELAY_RATE_LIMIT) 초과
      knox_rejected          Knox 가 거부(토큰 만료 등) -- journalctl -u knox-mail-dms-certi
      knox_unreachable       메신저 서버 -> Knox API 연결 실패·타임아웃
    retry_after: reason 이 rate_limited 일 때, 다시 보낼 수 있기까지 남은 초.
    """

    def __init__(self, reason: str, detail: str = "", retry_after: int | None = None):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail
        self.retry_after = retry_after


# --------------------------------------------------------------------- 릴레이 호출
def send_mail_via_relay(*, relay_url: str, token: str, to: str, subject: str, body: str,
                        content_type: str = "HTML", timeout: float = 15.0) -> None:
    """knox_mail_dms_certi 의 POST /send 를 호출해 `to` 한 명에게(TO) 보낸다.
    성공하면 None, 실패하면 MailerError."""
    relay_url = (relay_url or "").strip()
    token = (token or "").strip()  # 시크릿 파일에서 읽으면 끝에 개행이 붙기 쉽다
    if not relay_url.startswith(("http://", "https://")):
        raise MailerError("relay_misconfigured", f"mail_relay_url must be http(s)://host:port, got {relay_url!r}")
    if not token or not (token.isascii() and token.isprintable()):
        # 그대로 헤더에 넣으면 ValueError 가 나고 트레이스백에 토큰이 찍힌다
        raise MailerError("relay_misconfigured", "mail_relay_token is empty or has non-ASCII/control chars")
    payload = json.dumps({"to": [to], "subject": subject, "body": body,
                          "content_type": content_type}).encode("utf-8")
    req = urllib.request.Request(
        relay_url.rstrip("/") + "/send", data=payload, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8",
                 "Authorization": f"Bearer {token}"})
    try:
        with _RELAY_OPENER.open(req, timeout=timeout) as res:
            data = _json_object(res.read())
    except urllib.error.HTTPError as exc:  # 릴레이가 4xx/5xx 로 답함 -> 릴레이의 error 코드를 올린다
        data = _json_object(_safe_read(exc))
        raise MailerError(
            str(data.get("error") or f"relay_http_{exc.code}"), _describe(data),
            _int_or_none(data.get("retry_after") or (exc.headers or {}).get("Retry-After"))) from None
    except http.client.InvalidURL as exc:  # 포트가 숫자가 아님 등 (URL 은 비밀이 아니라 그대로 남긴다)
        raise MailerError("relay_misconfigured", str(exc)) from None
    except urllib.error.URLError as exc:  # 연결 거부·연결 타임아웃 -- urllib 은 요청 송신까지의 실패만 URLError 로 감싼다
        raise MailerError("relay_unreachable", str(getattr(exc, "reason", exc))) from None
    except http.client.RemoteDisconnected as exc:  # 응답 없이 끊김(BadStatusLine 의 하위라 먼저) -- 갔을 수도 있다
        raise MailerError("relay_no_response", f"{type(exc).__name__}: {exc}"[:200]) from None
    except _NOT_HTTP as exc:  # 상대가 HTTP 가 아닌 것을 답했다(SSH 배너 등) -- 릴레이가 아니다, 보내지 않았다
        raise MailerError("relay_bad_response", type(exc).__name__) from None
    except (OSError, http.client.HTTPException) as exc:  # 요청은 나갔고 응답 대기·읽기 중 실패 -- 갔을 수도 있다
        raise MailerError("relay_no_response", f"{type(exc).__name__}: {exc}"[:200]) from None
    except ValueError as exc:  # 잘못된 호스트명(IDNA)·포트 등. 메시지에 헤더 값이 섞일 수 있어 타입만 남긴다
        raise MailerError("relay_misconfigured", type(exc).__name__) from None
    if data.get("ok") is not True:
        raise MailerError("relay_bad_response", str(data)[:200])


def check_relay_health(*, relay_url: str, timeout: float = 5.0) -> None:
    """GET /healthz -- 릴레이가 {"ok": true} 로 답하면 None, 아니면 MailerError(send 와 같은 사유 체계).

    포탈 메일 설정의 "연결 확인"이 쓴다(2026-10-01). 토큰은 싣지 않는다 -- healthz 는 인증 없이 열려
    있고(운영 확인: curl http://<IP>:8025/healthz), 토큰 검사는 테스트 메일(send)이 본다."""
    relay_url = (relay_url or "").strip()
    if not relay_url.startswith(("http://", "https://")):
        raise MailerError("relay_misconfigured", f"mail_relay_url must be http(s)://host:port, got {relay_url!r}")
    req = urllib.request.Request(relay_url.rstrip("/") + "/healthz", method="GET")
    try:
        with _RELAY_OPENER.open(req, timeout=timeout) as res:
            data = _json_object(res.read())
    except urllib.error.HTTPError as exc:
        data = _json_object(_safe_read(exc))
        raise MailerError(str(data.get("error") or f"relay_http_{exc.code}"), _describe(data)) from None
    except http.client.InvalidURL as exc:
        raise MailerError("relay_misconfigured", str(exc)) from None
    except urllib.error.URLError as exc:
        raise MailerError("relay_unreachable", str(getattr(exc, "reason", exc))) from None
    except http.client.RemoteDisconnected as exc:
        raise MailerError("relay_no_response", f"{type(exc).__name__}: {exc}"[:200]) from None
    except _NOT_HTTP as exc:
        raise MailerError("relay_bad_response", type(exc).__name__) from None
    except (OSError, http.client.HTTPException) as exc:
        raise MailerError("relay_no_response", f"{type(exc).__name__}: {exc}"[:200]) from None
    except ValueError as exc:
        raise MailerError("relay_misconfigured", type(exc).__name__) from None
    if data.get("ok") is not True:
        raise MailerError("relay_bad_response", str(data)[:200])


class SendThrottle:
    """수신자별 발송 상한 (프로세스 메모리, 스레드 안전): window_seconds 동안 limit 통.

    인증번호를 *발급하기 전에* 검사해야 한다. 발급 뒤에 막히면 메일은 안 갔는데 새 코드가
    이전 코드를 대체해, 사용자 메일함에 있던 코드까지 죽을 수 있다.
    워커 프로세스가 여럿이면 워커마다 따로 센다 -- 릴레이의 상한(기본 10통/10분)이 최후 방어선.
    """

    def __init__(self, limit: int, window_seconds: float):
        self.limit = limit
        self.window = window_seconds
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def acquire(self, key: str) -> int:
        """여유가 있으면 1회를 기록하고 0, 없으면 다시 시도할 수 있기까지 남은 초."""
        if self.limit <= 0:
            return 0
        now = time.monotonic()
        key = key.lower()
        with self._lock:
            retry = self._peek_locked(key, now)
            if retry == 0:
                self._record_locked(key, now)
        return retry

    # 여러 상한을 "모두 통과할 때만 모두 기록"하려는 호출자용(2026-10-01 리뷰: 수신자 상한을 기록한 뒤 IP
    # 상한에 걸리면 거절된 요청이 남의 수신자 몫을 태웠다). peek 은 키를 만들지 않는다. 여러 SendThrottle 을
    # 한 번에 보려면 호출자가 자기 락으로 peek~record 를 감싼다(routes_auth 의 mail_throttle_lock).
    def peek(self, key: str) -> int:
        """기록하지 않고 본다: 여유가 있으면 0, 없으면 남은 초."""
        if self.limit <= 0:
            return 0
        with self._lock:
            return self._peek_locked(key.lower(), time.monotonic())

    def record(self, key: str) -> None:
        """1회 기록(peek 으로 여유를 확인한 뒤)."""
        if self.limit <= 0:
            return
        with self._lock:
            self._record_locked(key.lower(), time.monotonic())

    def _peek_locked(self, key: str, now: float) -> int:
        q = self._hits.get(key)
        if not q:
            return 0
        while q and now - q[0] >= self.window:
            q.popleft()
        if len(q) >= self.limit:
            return int(self.window - (now - q[0])) + 1
        return 0

    def _record_locked(self, key: str, now: float) -> None:
        self._hits[key].append(now)
        if len(self._hits) > 10000:  # 오래된 키 정리
            for k in [k for k, v in self._hits.items() if not v or now - v[-1] >= self.window]:
                del self._hits[k]


def _safe_read(resp) -> bytes:
    try:
        return resp.read()
    except Exception:  # noqa: BLE001 -- 오류 응답 본문은 못 읽어도 그만
        return b""


def _json_object(raw: bytes) -> dict:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _describe(data: dict) -> str:
    if data.get("detail"):
        return str(data["detail"])[:200]
    if "upstream_status" in data:  # knox_rejected: Knox 가 준 상태·응답 앞부분
        return f"knox HTTP {data['upstream_status']} {str(data.get('upstream_body', ''))[:150]}".strip()
    return ""


def _int_or_none(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------- 인증번호 메일 본문
@dataclass(frozen=True)
class _PurposeCopy:
    label: str        # 제목·요청 구분에 쓰는 이름
    lead: str         # 인사 다음 안내 문장
    if_not_you: str   # "본인이 요청하지 않았다면 ..." 뒤에 붙는 안심 문구


_PURPOSE_COPY = {
    "signup": _PurposeCopy(
        "회원가입", "회원가입을 계속하려면 아래 인증번호를 입력해 주세요.",
        "인증번호를 입력하지 않으면 계정은 만들어지지 않습니다."),
    "password_reset": _PurposeCopy(
        "비밀번호 재설정", "비밀번호를 재설정하려면 아래 인증번호를 입력해 주세요.",
        "인증번호를 입력하지 않으면 비밀번호는 바뀌지 않습니다."),
}
_FALLBACK_COPY = _PurposeCopy(
    "본인 확인", "본인 확인을 위해 아래 인증번호를 입력해 주세요.",
    "인증번호를 입력하지 않으면 아무것도 변경되지 않습니다.")

# Outlook 은 첫 번째 글꼴이 없으면 Times New Roman 으로 떨어지므로 Windows 글꼴을 맨 앞에.
_FONT = "'Malgun Gothic','맑은 고딕','Apple SD Gothic Neo',AppleSDGothicNeo,sans-serif"
_MONO = "Consolas,'SF Mono',Menlo,'Liberation Mono','Courier New',monospace"


def render_verification_email(*, code: str, purpose: str, username: str, ttl_seconds: int,
                              service_name: str = DEFAULT_SERVICE_NAME,
                              now: datetime | None = None) -> tuple[str, str]:
    """인증번호 메일의 (제목, HTML 본문)을 만든다. 인증번호는 본문에만 들어간다."""
    copy = _PURPOSE_COPY.get(purpose, _FALLBACK_COPY)
    issued = (now or datetime.now(KST)).astimezone(KST)
    expires = issued + timedelta(seconds=ttl_seconds)
    minutes = max(1, ttl_seconds // 60)
    subject = f"[{service_name}] {copy.label} 인증번호 안내"
    body = _VERIFICATION_HTML.substitute(
        font=_FONT, mono=_MONO,  # 고정 상수라 이스케이프하지 않는다
        title=escape(subject),
        preheader=escape(f"{copy.label} 인증번호를 확인하고 {minutes}분 안에 입력해 주세요."),
        service_name=escape(service_name),
        purpose_label=escape(copy.label),
        lead=escape(copy.lead),
        if_not_you=escape(copy.if_not_you),
        username=escape(username),
        code=escape(str(code)),
        ttl_minutes=minutes,
        expires_at=expires.strftime("%H:%M"),
        requested_at=issued.strftime("%Y-%m-%d %H:%M"),
    )
    return subject, body


def render_test_email(*, service_name: str = DEFAULT_SERVICE_NAME, requested_by: str = "",
                      now: datetime | None = None) -> tuple[str, str]:
    """포탈 메일 설정의 "테스트 메일" (제목, HTML 본문) -- 인증번호 메일과 같은 경로·형식(HTML)으로
    릴레이 → Knox 를 끝까지 태워 본다(2026-10-01). 비밀(토큰·코드)은 넣지 않는다."""
    sent = (now or datetime.now(KST)).astimezone(KST)
    subject = f"[{service_name}] 메일 발송 설정 테스트"
    body = _TEST_HTML.substitute(
        font=_FONT, title=escape(subject), service_name=escape(service_name),
        requested_by=escape(requested_by or "-"), sent_at=sent.strftime("%Y-%m-%d %H:%M"))
    return subject, body


# 메일 클라이언트(Outlook·Knox 웹메일·모바일) 호환을 위해 table 레이아웃 + 인라인 스타일만 쓴다.
# <style> 블록·외부 이미지·스크립트 없음. 치환자는 string.Template 의 ${name}.
_VERIFICATION_HTML = Template("""\
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="light only">
<meta name="supported-color-schemes" content="light only">
<title>${title}</title>
</head>
<body style="margin:0;padding:0;background-color:#f4f5f7;-webkit-text-size-adjust:100%;">
<div style="display:none;max-height:0;max-width:0;overflow:hidden;opacity:0;font-size:1px;line-height:1px;color:#f4f5f7;mso-hide:all;">${preheader}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#f4f5f7;">
<tr>
<td align="center" style="padding:40px 16px;">

  <table role="presentation" width="560" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:560px;background-color:#ffffff;border:1px solid #e5e8eb;border-radius:12px;">
    <tr>
      <td style="height:4px;line-height:4px;font-size:0;background-color:#1d4ed8;border-radius:12px 12px 0 0;">&nbsp;</td>
    </tr>
    <tr>
      <td style="padding:32px 40px 0;font-family:${font};word-break:keep-all;font-size:14px;line-height:20px;font-weight:700;color:#1d4ed8;">${service_name}</td>
    </tr>
    <tr>
      <td style="padding:10px 40px 0;font-family:${font};word-break:keep-all;font-size:22px;line-height:32px;font-weight:700;color:#191f28;">${purpose_label} 인증번호 안내</td>
    </tr>
    <tr>
      <td style="padding:16px 40px 0;font-family:${font};word-break:keep-all;font-size:15px;line-height:26px;color:#4e5968;">
        안녕하세요, <strong style="color:#191f28;">${username}</strong> 님.<br>
        ${lead}
      </td>
    </tr>
    <tr>
      <td style="padding:24px 40px 0;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr>
            <td align="center" style="padding:22px 0 22px 14px;background-color:#f2f5ff;border:1px solid #dbe3ff;border-radius:10px;font-family:${mono};font-size:36px;line-height:44px;font-weight:700;letter-spacing:14px;color:#191f28;">${code}</td>
          </tr>
        </table>
      </td>
    </tr>
    <tr>
      <td align="center" style="padding:12px 40px 0;font-family:${font};word-break:keep-all;font-size:13px;line-height:20px;color:#8b95a1;">
        인증번호는 <strong style="color:#4e5968;">${ttl_minutes}분간</strong> 유효합니다 (${expires_at} KST까지)
      </td>
    </tr>
    <tr>
      <td style="padding:28px 40px 0;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-top:1px solid #eef0f3;">
          <tr>
            <td width="84" style="padding:16px 0 0;font-family:${font};word-break:keep-all;font-size:13px;line-height:20px;color:#8b95a1;">계정 ID</td>
            <td style="padding:16px 0 0;font-family:${font};word-break:keep-all;font-size:13px;line-height:20px;color:#191f28;">${username}</td>
          </tr>
          <tr>
            <td width="84" style="padding:6px 0 0;font-family:${font};word-break:keep-all;font-size:13px;line-height:20px;color:#8b95a1;">요청 구분</td>
            <td style="padding:6px 0 0;font-family:${font};word-break:keep-all;font-size:13px;line-height:20px;color:#191f28;">${purpose_label}</td>
          </tr>
          <tr>
            <td width="84" style="padding:6px 0 0;font-family:${font};word-break:keep-all;font-size:13px;line-height:20px;color:#8b95a1;">요청 시각</td>
            <td style="padding:6px 0 0;font-family:${font};word-break:keep-all;font-size:13px;line-height:20px;color:#191f28;">${requested_at} (KST)</td>
          </tr>
        </table>
      </td>
    </tr>
    <tr>
      <td style="padding:24px 40px 36px;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr>
            <td style="padding:14px 16px;background-color:#f9fafb;border-radius:8px;font-family:${font};word-break:keep-all;font-size:12px;line-height:20px;color:#6b7684;">
              본인이 요청하지 않았다면 이 메일을 무시해 주세요. ${if_not_you}<br>
              인증번호는 다른 사람에게 알려주지 마세요. ${service_name} 담당자도 인증번호를 묻지 않습니다.
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>

  <table role="presentation" width="560" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:560px;">
    <tr>
      <td align="center" style="padding:20px 24px 0;font-family:${font};word-break:keep-all;font-size:12px;line-height:18px;color:#8b95a1;">
        본 메일은 ${service_name}에서 자동 발송된 발신 전용 메일입니다. 회신하셔도 확인되지 않습니다.
      </td>
    </tr>
  </table>

</td>
</tr>
</table>
</body>
</html>
""")

# 테스트 메일 -- 인증번호 메일과 같은 골격(table + 인라인 스타일)의 짧은 본문.
_TEST_HTML = Template("""\
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="light only">
<title>${title}</title>
</head>
<body style="margin:0;padding:0;background-color:#f4f5f7;-webkit-text-size-adjust:100%;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#f4f5f7;">
<tr>
<td align="center" style="padding:40px 16px;">
  <table role="presentation" width="560" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:560px;background-color:#ffffff;border:1px solid #e5e8eb;border-radius:12px;">
    <tr>
      <td style="height:4px;line-height:4px;font-size:0;background-color:#1d4ed8;border-radius:12px 12px 0 0;">&nbsp;</td>
    </tr>
    <tr>
      <td style="padding:32px 40px 0;font-family:${font};word-break:keep-all;font-size:14px;line-height:20px;font-weight:700;color:#1d4ed8;">${service_name}</td>
    </tr>
    <tr>
      <td style="padding:10px 40px 0;font-family:${font};word-break:keep-all;font-size:22px;line-height:32px;font-weight:700;color:#191f28;">메일 발송 설정 테스트</td>
    </tr>
    <tr>
      <td style="padding:16px 40px 36px;font-family:${font};word-break:keep-all;font-size:15px;line-height:26px;color:#4e5968;">
        이 메일이 보이면 포탈 → 메신저 서버 릴레이 → Knox 메일 경로가 정상입니다.<br>
        요청한 관리자: <strong style="color:#191f28;">${requested_by}</strong><br>
        보낸 시각: ${sent_at} (KST)
      </td>
    </tr>
  </table>
</td>
</tr>
</table>
</body>
</html>
""")
