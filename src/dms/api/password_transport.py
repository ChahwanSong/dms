"""브라우저 → API 비밀번호 **전송 봉인**(2026-09-07, 사용자 결정 "사용자 비밀번호 암호화").

왜 TLS 로 끝나지 않는가: TLS 는 사용자 PC 와 ingress-nginx 사이만 지킨다. ingress 가
TLS 를 벗긴 뒤 dms-api 까지의 클러스터 내부 hop 은 평문이고, TLS 검사 프록시(사내
방화벽)·인증서 경고를 무시하고 들어온 접속·프록시 본문 로그 어디서든 요청 본문의
평문 비밀번호가 보인다. 그래서 비밀번호 **필드만** 브라우저에서 한 번 더 봉인해
dms-api 프로세스 안에서만 열리게 한다. 저장은 여기서 열린 평문을 그대로
accounts.py 가 scrypt 로 해시한다 -- 평문은 요청 처리 스택 프레임 밖으로 나가지 않는다.

구성(ECIES 류, 전부 표준 프리미티브 -- 자체 설계 암호 없음):
  서버 정적 P-256 키 + 브라우저 임시 P-256 키 → ECDH → HKDF-SHA256 → AES-256-GCM.
  서버 키는 DMS_SESSION_SECRET 에서 HKDF 로 **결정적으로 유도**한다: 레플리카가
  여럿이어도 같은 키, DB 테이블·파일 없음, 시크릿 회전이 곧 키 회전(세션 시크릿이
  새면 세션 위조가 이미 가능하므로 이 키를 같은 비밀에 묶어도 위협이 늘지 않는다).
  AAD 에 용도·사용자명을 묶는다 -- 로그인용 봉인을 계정 생성에, alice 용을 bob 에
  재사용할 수 없다.

위협 모델(정직하게): 수동 도청·TLS 종단 이후 평문·프록시/로그 노출을 막는다.
페이지 JS 자체를 바꿔치기할 수 있는 **완전한 능동 MITM 은 막지 못한다** -- 그건
인증서 신뢰(사용자 CA 설치 또는 공인 CA)의 몫이다. 재전송(replay)도 막지 않는다:
봉인을 가로챌 위치의 공격자는 응답의 세션 쿠키도 가로채므로 replay 방어가 더해
주는 것이 없고, 시각 검사는 PC 시계가 틀린 사용자의 로그인을 깨뜨린다.

와이어 형식(요청 본문 password_enc):
  {"version": 1, "kid": <서버 공개키 지문 16hex>, "epk": b64(65B 비압축점),
   "iv": b64(12B), "ct": b64(암호문+GCM 태그)}
공개키는 GET /api/auth/transport-key 로 받는다(무인증 -- 공개키는 비밀이 아니다).
kid 가 서버와 다르면 password_encryption_key_mismatch: 시크릿 회전 직후 브라우저가
옛 키를 캐시한 경우이며 프런트는 키를 다시 받아 한 번 재시도한다.

프런트 frontend/src/lib/passwordTransport.ts 가 이 모듈의 거울이다 -- 상수(salt/
info/AAD 접두)와 절차가 **바이트 단위로** 같아야 한다. 한쪽만 바꾸면 전 로그인이
password_encryption_invalid 로 죽는다(test_password_transport 가 양쪽 상수를 대조).
"""
import base64
import hashlib
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

VERSION = 1
# 비밀번호를 받는 엔드포인트마다 하나 -- AAD 로 묶여 다른 용도로 재사용이 안 된다.
PURPOSES = ("login", "signup", "password_reset", "admin_create")

_CURVE = ec.SECP256R1()
# P-256 군의 위수(n). 유도한 32 바이트를 [1, n-1] 로 접어 유효한 개인키 스칼라로 만든다.
_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
# 아래 세 상수는 프런트 passwordTransport.ts 와 동일해야 한다.
HKDF_SALT = b"dms-password-transport"
SERVER_KEY_INFO = b"dms-password-transport-v1/server-key"
SESSION_KEY_INFO = b"dms-password-transport-v1/aes-256-gcm"
AAD_PREFIX = "dms-password-transport-v1"
_IV_BYTES = 12
_POINT_BYTES = 65


class PasswordTransportError(Exception):
    """복호 실패. reason_code 는 그대로 HTTP 422 detail 이 된다(사유 코드 계약)."""

    def __init__(self, *, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


def _hkdf(info: bytes) -> HKDF:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=HKDF_SALT, info=info)


def _b64d(value) -> bytes:
    if not isinstance(value, str):
        raise ValueError("not a string")
    return base64.b64decode(value, validate=True)


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def aad_for(purpose: str, username: str) -> bytes:
    return f"{AAD_PREFIX}|{purpose}|{username}".encode("utf-8")


def key_id(public_key_raw: bytes) -> str:
    return hashlib.sha256(public_key_raw).hexdigest()[:16]


def derive_private_key(secret: str) -> ec.EllipticCurvePrivateKey:
    """세션 시크릿 → P-256 개인키(결정적). 같은 시크릿이면 어느 프로세스든 같은 키."""
    seed = _hkdf(SERVER_KEY_INFO).derive(secret.encode("utf-8"))
    scalar = int.from_bytes(seed, "big") % (_ORDER - 1) + 1
    return ec.derive_private_key(scalar, _CURVE)


def _public_raw(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.X962,
                            serialization.PublicFormat.UncompressedPoint)


class PasswordTransport:
    def __init__(self, secret: str):
        self._private = derive_private_key(secret)
        self.public_key_raw = _public_raw(self._private.public_key())
        self.kid = key_id(self.public_key_raw)

    def public_info(self) -> dict:
        """GET /api/auth/transport-key 응답 -- 프런트 TransportKey 와 같은 모양."""
        return {"version": VERSION, "kid": self.kid,
                "public_key": _b64e(self.public_key_raw)}

    def decrypt(self, payload, *, purpose: str, username: str) -> str:
        """봉인을 열어 평문 비밀번호를 돌려준다. 실패는 전부 PasswordTransportError.

        kid 불일치만 따로 구분한다(프런트가 키를 다시 받아 재시도할 유일한 경우).
        나머지 -- 형식·점·태그·인코딩 -- 는 한 사유로 뭉갠다: 세분하면 오류 메시지가
        복호 오라클이 된다."""
        if not isinstance(payload, dict) or payload.get("version") != VERSION:
            raise PasswordTransportError(reason_code="password_encryption_invalid")
        if payload.get("kid") != self.kid:
            raise PasswordTransportError(reason_code="password_encryption_key_mismatch")
        try:
            epk = _b64d(payload.get("epk"))
            iv = _b64d(payload.get("iv"))
            ct = _b64d(payload.get("ct"))
            if len(iv) != _IV_BYTES or len(epk) != _POINT_BYTES:
                raise ValueError("bad lengths")
            peer = ec.EllipticCurvePublicKey.from_encoded_point(_CURVE, epk)
            shared = self._private.exchange(ec.ECDH(), peer)
            key = _hkdf(SESSION_KEY_INFO).derive(shared)
            plain = AESGCM(key).decrypt(iv, ct, aad_for(purpose, username))
            return plain.decode("utf-8")
        except Exception:
            # ValueError/TypeError/binascii.Error/InvalidTag/UnicodeDecodeError --
            # 어느 것도 클라이언트에 구분해 알릴 이유가 없다(위 docstring).
            raise PasswordTransportError(reason_code="password_encryption_invalid")


def seal(public_key_raw: bytes, password: str, *, purpose: str, username: str) -> dict:
    """브라우저 쪽 봉인의 파이썬 판 -- 테스트와 운영 스크립트(curl 대신)가 쓴다.
    프런트 encryptWithKey 와 절차가 같다(임시 키 → ECDH → HKDF → AES-GCM)."""
    ephemeral = ec.generate_private_key(_CURVE)
    peer = ec.EllipticCurvePublicKey.from_encoded_point(_CURVE, public_key_raw)
    shared = ephemeral.exchange(ec.ECDH(), peer)
    key = _hkdf(SESSION_KEY_INFO).derive(shared)
    iv = os.urandom(_IV_BYTES)
    ct = AESGCM(key).encrypt(iv, password.encode("utf-8"), aad_for(purpose, username))
    return {"version": VERSION, "kid": key_id(public_key_raw),
            "epk": _b64e(_public_raw(ephemeral.public_key())),
            "iv": _b64e(iv), "ct": _b64e(ct)}


def seal_with_info(info: dict, password: str, *, purpose: str, username: str) -> dict:
    """public_info() 응답(또는 GET /api/auth/transport-key 본문)으로 바로 봉인한다."""
    return seal(base64.b64decode(info["public_key"]), password,
                purpose=purpose, username=username)
