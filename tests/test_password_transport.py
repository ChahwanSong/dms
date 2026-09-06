"""비밀번호 전송 봉인 단위 테스트(api/password_transport.py). 프런트
passwordTransport.ts 와의 상수 일치도 여기서 대조한다 -- 한쪽만 바뀌면 전 로그인이
깨지는데 그 어긋남은 두 스택을 함께 돌리기 전엔 안 보인다."""
import base64
import re
from pathlib import Path

import pytest
from dms.api import password_transport as pt
from dms.api.password_transport import (PasswordTransport, PasswordTransportError,
                                        seal, seal_with_info)

FRONT = Path(__file__).resolve().parent.parent / "frontend" / "src" / "lib" / "passwordTransport.ts"


def test_key_is_derived_deterministically_from_the_secret():
    a, b, c = (PasswordTransport("sess-secret"), PasswordTransport("sess-secret"),
               PasswordTransport("another-secret"))
    assert a.kid == b.kid and a.public_key_raw == b.public_key_raw
    assert a.kid != c.kid
    assert len(a.public_key_raw) == 65 and a.public_key_raw[0] == 0x04
    assert re.fullmatch(r"[0-9a-f]{16}", a.kid)


def test_public_info_shape_matches_frontend_transport_key():
    info = PasswordTransport("s").public_info()
    assert set(info) == {"version", "kid", "public_key"}
    assert info["version"] == pt.VERSION == 1
    assert base64.b64decode(info["public_key"]) == PasswordTransport("s").public_key_raw


@pytest.mark.parametrize("password", ["pw", "", "p@ss wörd 한글 🔐", "x" * 512])
def test_seal_roundtrip(password):
    t = PasswordTransport("s")
    sealed = seal_with_info(t.public_info(), password, purpose="login", username="alice")
    assert set(sealed) == {"version", "kid", "epk", "iv", "ct"}
    assert sealed["kid"] == t.kid
    assert t.decrypt(sealed, purpose="login", username="alice") == password


def test_each_seal_is_fresh():
    # 같은 입력이라도 임시 키·iv 가 새로 나와 봉인이 매번 다르다(패턴 노출 없음).
    t = PasswordTransport("s")
    a = seal(t.public_key_raw, "pw", purpose="login", username="alice")
    b = seal(t.public_key_raw, "pw", purpose="login", username="alice")
    assert a["ct"] != b["ct"] and a["epk"] != b["epk"] and a["iv"] != b["iv"]


@pytest.mark.parametrize("purpose,username", [
    ("signup", "alice"), ("password_reset", "alice"), ("admin_create", "alice"),
    ("login", "bob"), ("login", "alice "), ("login", "Alice")])
def test_aad_binds_purpose_and_username(purpose, username):
    t = PasswordTransport("s")
    sealed = seal(t.public_key_raw, "pw", purpose="login", username="alice")
    with pytest.raises(PasswordTransportError) as e:
        t.decrypt(sealed, purpose=purpose, username=username)
    assert e.value.reason_code == "password_encryption_invalid"


def test_kid_mismatch_is_the_only_distinguished_failure():
    # 시크릿 회전 뒤 옛 키로 봉인한 경우 -- 프런트가 키를 다시 받아 재시도하는 신호.
    old, new = PasswordTransport("old"), PasswordTransport("new")
    sealed = seal(old.public_key_raw, "pw", purpose="login", username="alice")
    with pytest.raises(PasswordTransportError) as e:
        new.decrypt(sealed, purpose="login", username="alice")
    assert e.value.reason_code == "password_encryption_key_mismatch"


def _tamper_b64(value: str, index: int = 3) -> str:
    raw = bytearray(base64.b64decode(value))
    raw[index] ^= 0x01
    return base64.b64encode(bytes(raw)).decode()


@pytest.mark.parametrize("mutate", [
    lambda s: {**s, "ct": _tamper_b64(s["ct"])},
    lambda s: {**s, "iv": _tamper_b64(s["iv"])},
    lambda s: {**s, "epk": _tamper_b64(s["epk"], 10)},
    lambda s: {**s, "iv": base64.b64encode(b"short").decode()},
    lambda s: {**s, "epk": base64.b64encode(b"\x04" + b"\x00" * 64).decode()},  # 곡선 밖
    lambda s: {**s, "ct": "not base64!!"},
    lambda s: {**s, "ct": None},
    lambda s: {**s, "version": 2},
    lambda s: {**s, "version": "1"},
    lambda s: {k: v for k, v in s.items() if k != "iv"},
    lambda s: "just a string",
    lambda s: None,
], ids=["ct", "iv", "epk", "iv-len", "epk-off-curve", "ct-b64", "ct-none",
        "version", "version-str", "missing-iv", "not-dict", "none"])
def test_any_malformed_or_tampered_payload_is_one_invalid_reason(mutate):
    t = PasswordTransport("s")
    sealed = seal(t.public_key_raw, "pw", purpose="login", username="alice")
    with pytest.raises(PasswordTransportError) as e:
        t.decrypt(mutate(sealed), purpose="login", username="alice")
    assert e.value.reason_code == "password_encryption_invalid"


def test_constants_mirror_the_frontend_module():
    """salt/info/AAD 접두/버전이 TS 쪽 리터럴과 같은지 원문 대조. TS 를 파싱하지
    않고 리터럴 문자열의 존재만 본다 -- 값이 바뀌면 이 단언이 먼저 빨간불이다."""
    src = FRONT.read_text()
    assert f'HKDF_SALT = "{pt.HKDF_SALT.decode()}"' in src
    assert f'SESSION_KEY_INFO = "{pt.SESSION_KEY_INFO.decode()}"' in src
    assert f'AAD_PREFIX = "{pt.AAD_PREFIX}"' in src
    assert f"TRANSPORT_VERSION = {pt.VERSION}" in src
    # 용도 집합도 같아야 한다(AAD 의 일부).
    for purpose in pt.PURPOSES:
        assert f'"{purpose}"' in src
    assert 'namedCurve: "P-256"' in src and 'length: 256' in src


def test_aad_layout_is_stable():
    # 프런트 aadFor 와 같은 배치: <접두>|<용도>|<사용자명>
    assert pt.aad_for("login", "alice") == b"dms-password-transport-v1|login|alice"
