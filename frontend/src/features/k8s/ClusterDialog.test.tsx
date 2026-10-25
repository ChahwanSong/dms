import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, test } from "vitest";
import { Button } from "../../components/ui/Button";
import { forgetTransportKey } from "../../lib/passwordTransport";
import { isSealed, makeServerKey, openSealed, transportKeyHandler, type TestServerKey } from "../../test/transportKey";
import { ClusterDialog, apiServerError, caError, clusterNameError } from "./ClusterDialog";
import { CLUSTER_OK } from "./k8sFixtures";
import type { ClusterSummary } from "./types";

// 클러스터 등록·편집(설계 §5·§7): 토큰은 봉인(k8s_cluster_token · AAD 사용자 자리 = 클러스터 이름)으로만 실리고 평문이 본문
// 어디에도 없다, 주소를 바꾸면 토큰 재입력, CA 비움 = 유지(생략)·방식 변경 = null, 검증 안 함 경고, 리더 계정 YAML·복사.

let serverKey: TestServerKey;
const server = setupServer();
beforeAll(async () => { server.listen(); serverKey = await makeServerKey(); });
beforeEach(() => { forgetTransportKey(); server.use(transportKeyHandler(serverKey)); });
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const PEM = "-----BEGIN CERTIFICATE-----\nMIIBszCCAVmgAwIBAgIU\n-----END CERTIFICATE-----";

async function openDialog(mode: "create" | "edit", cluster?: ClusterSummary) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}>
    <ClusterDialog mode={mode} cluster={cluster} trigger={<Button>열기</Button>} />
  </QueryClientProvider>);
  await userEvent.click(screen.getByRole("button", { name: "열기" }));
  return screen.findByRole("dialog");
}

describe("검증 함수", () => {
  test("API 서버 주소는 https://host[:port] 만", () => {
    expect(apiServerError("https://10.10.10.20:6443")).toBeNull();
    expect(apiServerError("https://k8s-api.example.com")).toBeNull();
    expect(apiServerError("")).toBeNull();                              // 빈 값은 「필수」로 따로
    expect(apiServerError("http://10.10.10.20:6443")).not.toBeNull();
    expect(apiServerError("https://10.10.10.20:6443/api")).not.toBeNull();
    expect(apiServerError("https://u:p@host:6443")).not.toBeNull();
  });
  test("이름·CA", () => {
    expect(clusterNameError("prod-a")).toBeNull();
    expect(clusterNameError("a b")).not.toBeNull();
    expect(clusterNameError("a:b")).not.toBeNull();
    expect(caError(PEM)).toBeNull();
    expect(caError(btoa(PEM))).toBeNull();
    expect(caError("not a cert!")).not.toBeNull();
  });
});

test("등록: 토큰은 봉인(k8s_cluster_token / 클러스터 이름)으로만 실리고 평문은 본문 어디에도 없다", async () => {
  let sent: Record<string, unknown> | null = null;
  server.use(http.post("/api/admin/k8s/clusters", async ({ request }) => {
    sent = await request.json() as Record<string, unknown>;
    return HttpResponse.json(CLUSTER_OK, { status: 201 });
  }));
  await openDialog("create");
  await userEvent.type(screen.getByLabelText("클러스터 이름"), "dmsk8s");
  await userEvent.type(screen.getByLabelText("API 서버 주소"), "https://10.10.10.20:6443");
  await userEvent.click(screen.getByLabelText("CA 인증서(PEM)"));
  await userEvent.paste(PEM);
  await userEvent.type(screen.getByLabelText("TLS 서버 이름"), "kubernetes.default");
  await userEvent.type(screen.getByLabelText("API 토큰"), "eyJhbGciOi.secret-token");
  await userEvent.click(screen.getByRole("button", { name: "등록" }));
  await waitFor(() => expect(sent).not.toBeNull());
  const body = sent!;
  expect(body).toMatchObject({ name: "dmsk8s", api_server: "https://10.10.10.20:6443", tls_server_name: "kubernetes.default",
                               ca_pem: PEM, insecure_skip_tls_verify: false, enabled: true });
  expect("token" in body).toBe(false);
  expect(JSON.stringify(body)).not.toContain("secret-token");
  expect(isSealed(body.token_enc)).toBe(true);
  expect(await openSealed(serverKey, body.token_enc as never, "k8s_cluster_token", "dmsk8s"))
    .toBe("eyJhbGciOi.secret-token");
  // 다른 클러스터 이름으로는 열리지 않는다(AAD 에 이름이 묶임)
  await expect(openSealed(serverKey, body.token_enc as never, "k8s_cluster_token", "other")).rejects.toBeDefined();
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

test("등록: 필수 칸이 비면 잠기고 무엇이 필요한지 말한다, 잘못된 주소는 칸 옆에", async () => {
  await openDialog("create");
  const submit = screen.getByRole("button", { name: "등록" });
  expect(submit).toBeDisabled();
  expect(screen.getByText("필요: 클러스터 이름 · API 서버 주소 · CA 인증서 · API 토큰")).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("API 서버 주소"), "http://10.10.10.20:6443/x");
  expect(screen.getByLabelText("API 서버 주소")).toHaveAttribute("aria-invalid", "true");
  expect(screen.getByLabelText("API 서버 주소")).toHaveAccessibleDescription(/https:\/\/주소\[:포트\] 형식/);
});

test("등록: 토큰은 필수(서버 422 k8s_token_required 와 같은 계약), 시스템 기본 신뢰로 내면 ca_pem 은 null", async () => {
  let sent: Record<string, unknown> | null = null;
  server.use(http.post("/api/admin/k8s/clusters", async ({ request }) => {
    sent = await request.json() as Record<string, unknown>;
    return HttpResponse.json(CLUSTER_OK);
  }));
  await openDialog("create");
  await userEvent.type(screen.getByLabelText("클러스터 이름"), "pub");
  await userEvent.type(screen.getByLabelText("API 서버 주소"), "https://k8s.example.com");
  await userEvent.click(screen.getByLabelText("시스템 기본 신뢰"));
  expect(screen.queryByLabelText("CA 인증서(PEM)")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "등록" })).toBeDisabled();
  expect(screen.getByText("필요: API 토큰")).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("API 토큰"), "pub-token");
  await userEvent.click(screen.getByRole("button", { name: "등록" }));
  await waitFor(() => expect(sent).not.toBeNull());
  const { token_enc: tokenEnc, ...rest } = sent!;
  expect(rest).toEqual({ name: "pub", api_server: "https://k8s.example.com", tls_server_name: null,
                         insecure_skip_tls_verify: false, enabled: true, ca_pem: null });
  expect(await openSealed(serverKey, tokenEnc as never, "k8s_cluster_token", "pub")).toBe("pub-token");
});

test("편집: 주소를 바꾸면 토큰을 다시 넣어야 저장된다, CA 칸을 비우면 저장된 CA 유지(ca_pem 생략)", async () => {
  let sent: { path: string; body: Record<string, unknown> } | null = null;
  server.use(http.put("/api/admin/k8s/clusters/:name", async ({ request, params }) => {
    sent = { path: String(params.name), body: await request.json() as Record<string, unknown> };
    return HttpResponse.json(CLUSTER_OK);
  }));
  await openDialog("edit", CLUSTER_OK);
  expect(screen.getByLabelText("클러스터 이름")).toBeDisabled();
  expect(screen.getByText(/지금 토큰: 설정됨 — 값은 다시 볼 수 없습니다/)).toBeInTheDocument();
  expect(screen.getByLabelText("CA 인증서(PEM)")).toHaveAttribute("placeholder", "비우면 저장된 CA 를 그대로 씁니다");
  const addr = screen.getByLabelText("API 서버 주소");
  await userEvent.clear(addr);
  await userEvent.type(addr, "https://k8s-cp1:6443");
  const token = screen.getByLabelText("API 토큰");
  expect(token).toHaveAttribute("aria-invalid", "true");
  expect(screen.getByText(/API 서버 주소를 바꾸려면 토큰을 다시 넣어야 합니다/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "저장" })).toBeDisabled();
  await userEvent.type(token, "new-token");
  expect(screen.getByRole("button", { name: "저장" })).toBeEnabled();
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  await waitFor(() => expect(sent).not.toBeNull());
  expect(sent!.path).toBe("dmsk8s");
  expect(sent!.body.api_server).toBe("https://k8s-cp1:6443");
  expect("ca_pem" in sent!.body).toBe(false);
  expect(await openSealed(serverKey, sent!.body.token_enc as never, "k8s_cluster_token", "dmsk8s")).toBe("new-token");
});

test("편집: 주소·검증 그대로면 토큰 없이 저장(토큰 유지 — token_enc 없음)", async () => {
  let sent: Record<string, unknown> | null = null;
  server.use(http.put("/api/admin/k8s/clusters/:name", async ({ request }) => {
    sent = await request.json() as Record<string, unknown>;
    return HttpResponse.json(CLUSTER_OK);
  }));
  await openDialog("edit", CLUSTER_OK);
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  await waitFor(() => expect(sent).not.toBeNull());
  expect("token_enc" in sent!).toBe(false);
});

test("편집: 검증을 끄거나 TLS 서버 이름을 바꾸면 토큰을 다시 넣어야 한다(서버와 같은 규칙 — 2026-10-26 보안 리뷰)", async () => {
  let sent: Record<string, unknown> | null = null;
  server.use(http.put("/api/admin/k8s/clusters/:name", async ({ request }) => {
    sent = await request.json() as Record<string, unknown>;
    return HttpResponse.json(CLUSTER_OK);
  }));
  await openDialog("edit", CLUSTER_OK);
  await userEvent.click(screen.getByLabelText("검증 안 함"));
  expect(screen.getByText(/인증서를 검증하지 않으면 같은 주소를 가로챈 쪽이/)).toBeInTheDocument();
  expect(screen.getByText(/인증서 검증을 끄려면 토큰을 다시 넣어야 합니다/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "저장" })).toBeDisabled();
  await userEvent.type(screen.getByLabelText("API 토큰"), "again");
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  await waitFor(() => expect(sent).not.toBeNull());
  expect(sent).toMatchObject({ insecure_skip_tls_verify: true, ca_pem: null });
  expect("token_enc" in sent!).toBe(true);
});

test("편집 중에 개요 폴링으로 cluster 가 바뀌어도 입력칸을 되돌리지 않는다", async () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(<QueryClientProvider client={qc}>
    <ClusterDialog mode="edit" cluster={CLUSTER_OK} trigger={<Button>열기</Button>} />
  </QueryClientProvider>);
  await userEvent.click(screen.getByRole("button", { name: "열기" }));
  const addr = await screen.findByLabelText("API 서버 주소");
  await userEvent.clear(addr);
  await userEvent.type(addr, "https://k8s-cp1:6443");
  await userEvent.type(screen.getByLabelText("API 토큰"), "typed");
  view.rerender(<QueryClientProvider client={qc}>
    <ClusterDialog mode="edit" cluster={{ ...CLUSTER_OK, last_attempt_at: "2026-10-25T03:05:00Z" }}
                   trigger={<Button>열기</Button>} />
  </QueryClientProvider>);
  expect(screen.getByLabelText("API 서버 주소")).toHaveValue("https://k8s-cp1:6443");
  expect(screen.getByLabelText("API 토큰")).toHaveValue("typed");
});

test("서버 거절은 창 안 role=alert 하나로(사유 문구)", async () => {
  server.use(http.post("/api/admin/k8s/clusters", () =>
    HttpResponse.json({ detail: "k8s_cluster_exists" }, { status: 409 })));
  await openDialog("create");
  await userEvent.type(screen.getByLabelText("클러스터 이름"), "dmsk8s");
  await userEvent.type(screen.getByLabelText("API 서버 주소"), "https://10.10.10.20:6443");
  await userEvent.click(screen.getByLabelText("검증 안 함"));
  await userEvent.type(screen.getByLabelText("API 토큰"), "tok");
  await userEvent.click(screen.getByRole("button", { name: "등록" }));
  const alerts = await screen.findAllByRole("alert");
  expect(alerts).toHaveLength(1);
  expect(alerts[0]).not.toHaveTextContent(/^$/);
  expect(screen.getByRole("dialog")).toBeInTheDocument();         // 실패하면 창은 그대로
});

test("리더 계정 YAML: 읽기 전용 ClusterRole·SA·토큰 Secret 과 복사(선택 + execCommand) 버튼", async () => {
  await openDialog("create");
  const yaml = screen.getByLabelText("리더 계정 YAML") as HTMLTextAreaElement;
  expect(yaml).toHaveAttribute("readonly");
  expect(yaml.value).toContain("kind: ClusterRole");
  expect(yaml.value).toContain("type: kubernetes.io/service-account-token");
  expect(yaml.value).toContain('verbs: ["list"]');
  expect(yaml.value).not.toMatch(/create|update|patch|delete/);     // 쓰기 동사 없음
  await userEvent.click(screen.getByRole("button", { name: "복사" }));
  // jsdom 엔 execCommand 가 없다 -- 전체 선택 + 단축키 안내로 떨어진다(clipboard API 는 쓰지 않는다).
  expect(await screen.findByText(/복사했습니다|전체 선택했습니다/)).toBeInTheDocument();
  expect(yaml.selectionStart).toBe(0);
  expect(yaml.selectionEnd).toBe(yaml.value.length);
});
