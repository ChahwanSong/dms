"""저장용 비밀 봉인(2026-10-01) -- 포탈에서 입력한 비밀(메일 릴레이 토큰)을 DB 에 평문으로 두지 않는다.

키는 DMS_SESSION_SECRET 에서 HKDF 로 유도한다(password_transport 와 같은 원천, 다른 info). DB 덤프·백업만으로는
토큰을 읽을 수 없게 하는 것이 목적이다 -- 제어면(api) 프로세스는 키를 갖고 있으므로 런타임 침해는 막지 않는다.

  seal_at_rest(plain, secret=, purpose=) -> "v1:<base64(nonce|ciphertext)>"
  open_at_rest(blob, secret=, purpose=)  -> 평문, 실패하면 None

purpose 는 키 유도(info)와 AAD 양쪽에 묶인다 -- 한 용도로 봉인한 값을 다른 용도 칼럼에 옮겨 붙여도 열리지
않는다. 세션 시크릿을 바꾸면 기존 봉인은 열리지 않는다(None) -- 호출자는 "다시 입력" 상태로 보여야 한다
(조용히 빈 값처럼 동작하면 안 된다, mail_config.MailConfig.token_unreadable).
"""
from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

PREFIX = "v1:"
_SALT = b"dms-secret-box"
_NONCE_BYTES = 12


def _key(secret: str, purpose: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=_SALT,
                info=f"dms-secret-box-v1/{purpose}".encode("utf-8")).derive(secret.encode("utf-8"))


def seal_at_rest(plain: str, *, secret: str, purpose: str) -> str:
    nonce = os.urandom(_NONCE_BYTES)
    ct = AESGCM(_key(secret, purpose)).encrypt(nonce, plain.encode("utf-8"), purpose.encode("utf-8"))
    return PREFIX + base64.b64encode(nonce + ct).decode("ascii")


def open_at_rest(blob: "str | None", *, secret: str, purpose: str) -> "str | None":
    if not isinstance(blob, str) or not blob.startswith(PREFIX):
        return None
    try:
        raw = base64.b64decode(blob[len(PREFIX):], validate=True)
        nonce, ct = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
        if len(nonce) != _NONCE_BYTES or not ct:
            return None
        return AESGCM(_key(secret, purpose)).decrypt(nonce, ct, purpose.encode("utf-8")).decode("utf-8")
    except Exception:  # noqa: BLE001 -- 형식·태그·키 불일치 무엇이든 "열 수 없음" 하나다
        return None
