/**
 * 비밀번호 전송 봉인의 테스트 서버 쪽(2026-09-07). msw 핸들러에 서버 공개키를
 * 내고, 캡처한 password_enc 를 열어 "어떤 비밀번호가 실렸는가"를 단언하게 한다.
 * 절차는 src/dms/api/password_transport.py 의 decrypt 와 같다(ECDH → HKDF → AES-GCM).
 */
import { http, HttpResponse } from "msw";
import {
  AAD_PREFIX, HKDF_SALT, SESSION_KEY_INFO, b64decode, b64encode,
  type EncryptedPassword, type Purpose, type TransportKey,
} from "../lib/passwordTransport";

export interface TestServerKey { key: TransportKey; privateKey: CryptoKey }

const utf8 = (s: string) => new TextEncoder().encode(s);

export async function makeServerKey(): Promise<TestServerKey> {
  const pair = await crypto.subtle.generateKey(
    { name: "ECDH", namedCurve: "P-256" }, true, ["deriveKey", "deriveBits"]);
  const raw = await crypto.subtle.exportKey("raw", pair.publicKey);
  const digest = await crypto.subtle.digest("SHA-256", raw);
  const kid = Array.from(new Uint8Array(digest).slice(0, 8))
    .map((b) => b.toString(16).padStart(2, "0")).join("");
  return { key: { version: 1, kid, public_key: b64encode(raw) }, privateKey: pair.privateKey };
}

/** GET /api/auth/transport-key 핸들러. 비밀번호를 보내는 화면·훅 테스트마다 필요하다. */
export function transportKeyHandler(server: TestServerKey) {
  return http.get("/api/auth/transport-key", () => HttpResponse.json(server.key));
}

/** 캡처한 봉인을 서버처럼 연다 -- AAD 가 다르면(용도·사용자명 불일치) throw. */
export async function openSealed(
  server: TestServerKey, sealed: EncryptedPassword, purpose: Purpose, username: string,
): Promise<string> {
  const epk = await crypto.subtle.importKey(
    "raw", b64decode(sealed.epk), { name: "ECDH", namedCurve: "P-256" }, false, []);
  const shared = await crypto.subtle.deriveBits(
    { name: "ECDH", public: epk }, server.privateKey, 256);
  const hk = await crypto.subtle.importKey("raw", shared, "HKDF", false, ["deriveKey"]);
  const aes = await crypto.subtle.deriveKey(
    { name: "HKDF", hash: "SHA-256", salt: utf8(HKDF_SALT), info: utf8(SESSION_KEY_INFO) },
    hk, { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
  const plain = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: b64decode(sealed.iv),
      additionalData: utf8(`${AAD_PREFIX}|${purpose}|${username}`) },
    aes, b64decode(sealed.ct));
  return new TextDecoder().decode(plain);
}

export function isSealed(value: unknown): value is EncryptedPassword {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return v.version === 1 && ["kid", "epk", "iv", "ct"].every((k) => typeof v[k] === "string");
}
