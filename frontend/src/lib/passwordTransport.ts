/**
 * 비밀번호 전송 봉인(2026-09-07) -- 서버 src/dms/api/password_transport.py 의 거울.
 *
 * 포탈은 비밀번호를 **평문으로 보내지 않는다**. 서버 공개키(GET /api/auth/transport-key)
 * 와 브라우저 임시 P-256 키로 ECDH → HKDF-SHA256 → AES-256-GCM 봉인을 만들어
 * `password_enc` 로 보낸다. TLS 위에 한 겹 더인 이유: TLS 는 ingress 에서 끝나고
 * 그 뒤 클러스터 내부 hop·TLS 검사 프록시·인증서 경고를 무시한 접속에서는 본문의
 * 평문이 보인다(서버 모듈 docstring 의 위협 모델). AAD 에 용도·사용자명을 묶어
 * 다른 엔드포인트·다른 계정으로의 재사용을 막는다.
 *
 * 상수(salt/info/AAD 접두)와 절차는 서버와 **바이트 단위로** 같아야 한다 -- 한쪽만
 * 바꾸면 전 로그인이 password_encryption_invalid 다.
 *
 * WebCrypto(crypto.subtle) 는 보안 컨텍스트(HTTPS 또는 localhost)에서만 있다. 없으면
 * 평문으로 떨어지지 않고 password_encryption_unavailable 로 멈춘다 -- DMS 는 HTTPS
 * 전용(Secure 쿠키)이라 정상 접속에서는 일어나지 않는다.
 */
import { ApiError, apiGet, apiSend, reasonText } from "./api";

export const TRANSPORT_VERSION = 1;
export const HKDF_SALT = "dms-password-transport";
export const SESSION_KEY_INFO = "dms-password-transport-v1/aes-256-gcm";
export const AAD_PREFIX = "dms-password-transport-v1";

/** 비밀번호를 받는 엔드포인트마다 하나 -- 서버 PURPOSES 와 같은 값. */
export type Purpose = "login" | "signup" | "password_reset" | "admin_create";

export interface TransportKey { version: number; kid: string; public_key: string }
export interface EncryptedPassword {
  version: number; kid: string; epk: string; iv: string; ct: string;
}

const utf8 = (s: string) => new TextEncoder().encode(s);

export function b64encode(bytes: ArrayBuffer | Uint8Array): string {
  const arr = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  let bin = "";
  for (const b of arr) bin += String.fromCharCode(b);
  return btoa(bin);
}

// 반환 타입을 ArrayBuffer 기반으로 고정한다 -- TS 5.7+ 의 Uint8Array<ArrayBufferLike>
// 는 WebCrypto 의 BufferSource 에 대입되지 않는다(SharedArrayBuffer 가능성).
export function b64decode(s: string): Uint8Array<ArrayBuffer> {
  const bin = atob(s);
  const out = new Uint8Array(new ArrayBuffer(bin.length));
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export function aadFor(purpose: Purpose, username: string): string {
  return `${AAD_PREFIX}|${purpose}|${username}`;
}

export function transportAvailable(): boolean {
  return typeof crypto !== "undefined" && crypto !== null && !!crypto.subtle;
}

/** 순수 봉인 -- fetch 없음. 테스트가 임의 키로 검증하고 e2e 헬퍼가 Node 에서 재사용한다. */
export async function encryptWithKey(
  key: TransportKey, password: string, purpose: Purpose, username: string,
  subtle: SubtleCrypto = crypto.subtle,
): Promise<EncryptedPassword> {
  const serverKey = await subtle.importKey(
    "raw", b64decode(key.public_key), { name: "ECDH", namedCurve: "P-256" }, false, []);
  const ephemeral = await subtle.generateKey(
    { name: "ECDH", namedCurve: "P-256" }, true, ["deriveKey", "deriveBits"]);
  const shared = await subtle.deriveBits(
    { name: "ECDH", public: serverKey }, ephemeral.privateKey, 256);
  const hkdfKey = await subtle.importKey("raw", shared, "HKDF", false, ["deriveKey"]);
  const aes = await subtle.deriveKey(
    { name: "HKDF", hash: "SHA-256", salt: utf8(HKDF_SALT), info: utf8(SESSION_KEY_INFO) },
    hkdfKey, { name: "AES-GCM", length: 256 }, false, ["encrypt"]);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await subtle.encrypt(
    { name: "AES-GCM", iv, additionalData: utf8(aadFor(purpose, username)) },
    aes, utf8(password));
  const epk = await subtle.exportKey("raw", ephemeral.publicKey);
  return { version: TRANSPORT_VERSION, kid: key.kid,
           epk: b64encode(epk), iv: b64encode(iv), ct: b64encode(ct) };
}

// 서버 키 캐시. 시크릿 회전으로 키가 바뀌면 서버가 password_encryption_key_mismatch
// 를 내고, 아래 postWithSealedPassword 가 캐시를 비운 뒤 한 번 재시도한다.
let cachedKey: Promise<TransportKey> | null = null;

export function fetchTransportKey(): Promise<TransportKey> {
  if (cachedKey === null) {
    cachedKey = apiGet<TransportKey>("/api/auth/transport-key").catch((e) => {
      cachedKey = null;   // 실패한 약속을 캐시하면 영원히 실패한다
      throw e;
    });
  }
  return cachedKey;
}

export function forgetTransportKey(): void {
  cachedKey = null;
}

/**
 * 본문의 `password` 를 `password_enc` 로 바꿔 POST 한다 -- 비밀번호를 보내는 훅
 * 넷(login/signup/password-reset/admin accounts)이 전부 이 함수를 쓴다. 새 훅이
 * 생기면 여기로 -- apiSend 에 password 를 직접 실으면 그 경로만 평문이다.
 */
export async function postWithSealedPassword<T>(
  path: string, purpose: Purpose,
  body: { username: string; password: string } & Record<string, unknown>,
): Promise<T> {
  if (!transportAvailable()) {
    throw new ApiError(0, "password_encryption_unavailable",
                       reasonText("password_encryption_unavailable"));
  }
  const { password, ...rest } = body;
  const attempt = async (): Promise<T> => {
    const key = await fetchTransportKey();
    const password_enc = await encryptWithKey(key, password, purpose, body.username);
    return apiSend<T>("POST", path, { ...rest, password_enc });
  };
  try {
    return await attempt();
  } catch (e) {
    if (e instanceof ApiError && e.code === "password_encryption_key_mismatch") {
      forgetTransportKey();
      return attempt();
    }
    throw e;
  }
}
