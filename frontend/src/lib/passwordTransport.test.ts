import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, expect, test, vi } from "vitest";
import { ApiError } from "./api";
import {
  aadFor, b64decode, b64encode, encryptWithKey, forgetTransportKey,
  postWithSealedPassword, transportAvailable,
} from "./passwordTransport";
import { isSealed, makeServerKey, openSealed, transportKeyHandler, type TestServerKey } from "../test/transportKey";

const server = setupServer();
let serverKey: TestServerKey;
beforeAll(async () => { server.listen(); serverKey = await makeServerKey(); });
beforeEach(() => forgetTransportKey());   // 모듈 캐시는 테스트 사이에 새지 않아야 한다
afterEach(() => { server.resetHandlers(); vi.unstubAllGlobals(); });
afterAll(() => server.close());

test("base64 왕복", () => {
  const bytes = new Uint8Array([0, 1, 2, 250, 251, 252, 253, 254, 255]);
  expect(b64decode(b64encode(bytes))).toEqual(bytes);
  expect(b64encode(new Uint8Array(0))).toBe("");
});

test("aad 배치는 서버와 같다 -- <접두>|<용도>|<사용자명>", () => {
  expect(aadFor("login", "alice")).toBe("dms-password-transport-v1|login|alice");
});

test("encryptWithKey: 서버 키로 열리고, 용도·사용자명이 다르면 열리지 않는다", async () => {
  const sealed = await encryptWithKey(serverKey.key, "p@ss 한글", "login", "alice");
  expect(isSealed(sealed)).toBe(true);
  expect(sealed.kid).toBe(serverKey.key.kid);
  expect(b64decode(sealed.epk)).toHaveLength(65);
  expect(b64decode(sealed.iv)).toHaveLength(12);
  expect(await openSealed(serverKey, sealed, "login", "alice")).toBe("p@ss 한글");
  await expect(openSealed(serverKey, sealed, "signup", "alice")).rejects.toThrow();
  await expect(openSealed(serverKey, sealed, "login", "bob")).rejects.toThrow();
  // 매번 새 임시 키·iv
  const again = await encryptWithKey(serverKey.key, "p@ss 한글", "login", "alice");
  expect(again.ct).not.toBe(sealed.ct);
  expect(again.epk).not.toBe(sealed.epk);
});

test("postWithSealedPassword: 와이어에 password 는 없고 password_enc 만 실린다", async () => {
  let body: Record<string, unknown> = {};
  server.use(
    transportKeyHandler(serverKey),
    http.post("/api/auth/signup", async ({ request }) => {
      body = await request.json() as Record<string, unknown>;
      return HttpResponse.json({ username: "alice" }, { status: 201 });
    }),
  );
  const out = await postWithSealedPassword<{ username: string }>(
    "/api/auth/signup", "signup", { username: "alice", password: "pw1", code: "1234" });
  expect(out).toEqual({ username: "alice" });
  expect(body.password).toBeUndefined();
  expect(body.username).toBe("alice");
  expect(body.code).toBe("1234");
  expect(isSealed(body.password_enc)).toBe(true);
  expect(await openSealed(serverKey, body.password_enc as never, "signup", "alice")).toBe("pw1");
});

test("서버 키는 한 번만 받아 캐시한다", async () => {
  let fetches = 0;
  server.use(
    http.get("/api/auth/transport-key", () => { fetches += 1; return HttpResponse.json(serverKey.key); }),
    http.post("/api/auth/login", () => HttpResponse.json({ actor: "a", role: "user" })),
  );
  await postWithSealedPassword("/api/auth/login", "login", { username: "a", password: "x" });
  await postWithSealedPassword("/api/auth/login", "login", { username: "a", password: "y" });
  expect(fetches).toBe(1);
});

test("키 불일치(시크릿 회전)는 키를 다시 받아 한 번 재시도한다", async () => {
  const rotated = await makeServerKey();
  let keyServed = 0;
  const kids: string[] = [];
  server.use(
    // 첫 요청엔 옛 키, 그 다음부터 새 키
    http.get("/api/auth/transport-key", () => {
      keyServed += 1;
      return HttpResponse.json(keyServed === 1 ? serverKey.key : rotated.key);
    }),
    http.post("/api/auth/login", async ({ request }) => {
      const b = await request.json() as { password_enc: { kid: string } };
      kids.push(b.password_enc.kid);
      if (b.password_enc.kid !== rotated.key.kid) {
        return HttpResponse.json({ detail: "password_encryption_key_mismatch" }, { status: 422 });
      }
      return HttpResponse.json({ actor: "a", role: "user" });
    }),
  );
  const out = await postWithSealedPassword("/api/auth/login", "login", { username: "a", password: "x" });
  expect(out).toEqual({ actor: "a", role: "user" });
  expect(kids).toEqual([serverKey.key.kid, rotated.key.kid]);
  expect(keyServed).toBe(2);
});

test("키 불일치가 계속되면 두 번째 실패를 그대로 올린다(무한 재시도 없음)", async () => {
  let posts = 0;
  server.use(
    transportKeyHandler(serverKey),
    http.post("/api/auth/login", () => {
      posts += 1;
      return HttpResponse.json({ detail: "password_encryption_key_mismatch" }, { status: 422 });
    }),
  );
  await expect(postWithSealedPassword("/api/auth/login", "login", { username: "a", password: "x" }))
    .rejects.toMatchObject({ code: "password_encryption_key_mismatch" });
  expect(posts).toBe(2);
});

test("다른 오류(401 등)는 재시도 없이 그대로 올린다", async () => {
  let posts = 0;
  server.use(
    transportKeyHandler(serverKey),
    http.post("/api/auth/login", () => {
      posts += 1;
      return HttpResponse.json({ detail: "invalid_credentials" }, { status: 401 });
    }),
  );
  await expect(postWithSealedPassword("/api/auth/login", "login", { username: "a", password: "x" }))
    .rejects.toMatchObject({ status: 401, code: "invalid_credentials" });
  expect(posts).toBe(1);
});

test("키 조회 실패는 캐시되지 않는다 -- 다음 시도가 다시 받는다", async () => {
  let fetches = 0;
  server.use(
    http.get("/api/auth/transport-key", () => {
      fetches += 1;
      return fetches === 1 ? HttpResponse.error() : HttpResponse.json(serverKey.key);
    }),
    http.post("/api/auth/login", () => HttpResponse.json({ actor: "a", role: "user" })),
  );
  await expect(postWithSealedPassword("/api/auth/login", "login", { username: "a", password: "x" }))
    .rejects.toBeInstanceOf(Error);
  await expect(postWithSealedPassword("/api/auth/login", "login", { username: "a", password: "x" }))
    .resolves.toEqual({ actor: "a", role: "user" });
  expect(fetches).toBe(2);
});

test("WebCrypto 가 없으면 평문으로 떨어지지 않고 요청 없이 멈춘다", async () => {
  let requests = 0;
  server.use(
    http.get("/api/auth/transport-key", () => { requests += 1; return HttpResponse.json(serverKey.key); }),
    http.post("/api/auth/login", () => { requests += 1; return HttpResponse.json({}); }),
  );
  vi.stubGlobal("crypto", { getRandomValues: crypto.getRandomValues.bind(crypto) });
  expect(transportAvailable()).toBe(false);
  const err = await postWithSealedPassword("/api/auth/login", "login", { username: "a", password: "x" })
    .catch((e: unknown) => e);
  expect(err).toBeInstanceOf(ApiError);
  expect((err as ApiError).code).toBe("password_encryption_unavailable");
  expect((err as ApiError).message).toMatch(/HTTPS/);
  expect(requests).toBe(0);
});
