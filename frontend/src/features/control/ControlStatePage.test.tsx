import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { ControlStatePage } from "./ControlStatePage";

const server = setupServer(
  // 컨트롤 상태 화면의 빌드 노드는 이제 select다 -- 옵션 목록을 이 엔드포인트에서
  // 읽는다(useNodes). 아래 CS가 가리키는 "dms-w1"이 항상 옵션에 있도록 기본으로 둔다.
  http.get("/api/admin/nodes", () => HttpResponse.json([
    { node_name: "dms-w1", reported_at: "2026-08-05T00:00:00Z", fresh: true, report: {} },
  ])),
  // 슬라이스 36: 현재 상태 카드의 영향 요약(잡 집계)·변경 이력 기본 응답.
  http.get("/api/admin/metrics/jobs", () => HttpResponse.json({ by_state: [] })),
  http.get("/api/admin/control-state/history", () => HttpResponse.json([])),
  // 프록시 제외 힌트(2026-09-09) 기본 응답 -- 사이트 실제 값.
  http.get("/api/admin/control-state/proxy-hints", () => HttpResponse.json({
    registry: "pkg-01:5000", registry_host: "pkg-01",
    nodes: [{ name: "dms-w1", ip: "10.10.10.11" }, { name: "dms-w2", ip: "10.10.10.12" }],
    nodes_known: true,
    suggested_no_proxy: ["pkg-01", "pkg-01:5000", "localhost", "127.0.0.1", ".svc", ".cluster.local",
                         "10.10.10.11", "10.10.10.12"],
    auto_added: ["pkg-01:5000", "pkg-01", "localhost", "127.0.0.1"],
  })),
);
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

const CS = { maintenance: 1, drain: 0, reason: "점검", build_node_name: "dms-w1", build_source_path: "/home/mason/dms-dev/dms", changed_by: "ops", changed_at: "2026-08-05T00:00:00Z" };

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><ControlStatePage /></QueryClientProvider>);
}

test("shows the maintenance banner and changed_by, but no drain banner", async () => {
  server.use(http.get("/api/admin/control-state", () => HttpResponse.json(CS)));
  wrap();
  expect(await screen.findByText((_, node) =>
    Boolean(node?.textContent?.startsWith("유지보수 중") && node.textContent.includes("드레인도 함께 켜세요")))).toBeInTheDocument();
  expect(screen.getByText("ops")).toBeInTheDocument();
  expect(screen.queryByText("드레인 중 — 진행 중인 작업이 더 전진하지 않습니다")).not.toBeInTheDocument();
});

test("toggling drain and saving sends the correct PUT body", async () => {
  let body: any = null;
  server.use(
    http.get("/api/admin/control-state", () => HttpResponse.json(CS)),
    http.put("/api/admin/control-state", async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ ...CS, drain: 1 });
    }));
  wrap();
  await screen.findByLabelText("유지보수");
  await userEvent.click(screen.getByLabelText("드레인"));
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  expect(body).toEqual({ maintenance: true, drain: true, reason: "점검",
                         build_node_name: "dms-w1",
                         build_source_path: "/home/mason/dms-dev/dms",
                         build_http_proxy: null, build_https_proxy: null, build_no_proxy: null,
                         build_host_network: false, build_proxy_ca_path: null });
});

test("프록시 CA 경로를 입력해 저장하면 본문에 실리고 현재 상태에 보인다", async () => {
  let body: any = null;
  let state: any = CS;
  server.use(
    http.get("/api/admin/control-state", () => HttpResponse.json(state)),
    http.put("/api/admin/control-state", async ({ request }) => {
      body = await request.json();
      state = { ...CS, build_proxy_ca_path: "/etc/pki/corp-proxy-ca.pem" };
      return HttpResponse.json(state);
    }));
  wrap();
  await screen.findByLabelText("유지보수");
  await userEvent.type(screen.getByLabelText("프록시 CA 파일 경로"), "/etc/pki/corp-proxy-ca.pem");
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  await waitFor(() => expect(body).not.toBeNull());
  expect(body.build_proxy_ca_path).toBe("/etc/pki/corp-proxy-ca.pem");
  expect(await screen.findByText("/etc/pki/corp-proxy-ca.pem")).toBeInTheDocument();
});

test("호스트 네트워크 스위치를 켜 저장하면 본문에 실리고 현재 상태에 보인다", async () => {
  let body: any = null;
  let state: any = CS;
  server.use(
    http.get("/api/admin/control-state", () => HttpResponse.json(state)),
    http.put("/api/admin/control-state", async ({ request }) => {
      body = await request.json();
      state = { ...CS, build_host_network: 1 };
      return HttpResponse.json(state);
    }));
  wrap();
  await screen.findByLabelText("유지보수");
  await userEvent.click(screen.getByLabelText("빌드 파드 호스트 네트워크"));
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  await waitFor(() => expect(body).not.toBeNull());
  expect(body.build_host_network).toBe(true);
  expect(await screen.findByText("호스트 네트워크(스위치)")).toBeInTheDocument();
});

test("프록시가 localhost 면 스위치 없이도 「호스트 네트워크(자동)」로 표시한다", async () => {
  server.use(http.get("/api/admin/control-state", () =>
    HttpResponse.json({ ...CS, build_http_proxy: "http://127.0.0.1:7227", build_host_network: 0 })));
  wrap();
  expect(await screen.findByText("호스트 네트워크(자동 — 프록시가 127.0.0.1)")).toBeInTheDocument();
});

test("프록시 제외 힌트에 레지스트리·클러스터 DNS·워커 노드 IP 가 보이고 한 번에 채울 수 있다", async () => {
  let body: any = null;
  server.use(
    http.get("/api/admin/control-state", () => HttpResponse.json(CS)),
    http.put("/api/admin/control-state", async ({ request }) => {
      body = await request.json();
      return HttpResponse.json(CS);
    }));
  wrap();
  const hint = await screen.findByTestId("no-proxy-hint");
  expect(hint).toHaveTextContent("pkg-01,pkg-01:5000,localhost,127.0.0.1,.svc,.cluster.local,10.10.10.11,10.10.10.12");
  expect(hint).toHaveTextContent("워커 노드 2대");
  await userEvent.click(screen.getByRole("button", { name: "권장 값 채우기" }));
  expect(screen.getByLabelText("프록시 제외")).toHaveValue(
    "pkg-01,pkg-01:5000,localhost,127.0.0.1,.svc,.cluster.local,10.10.10.11,10.10.10.12");
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  await waitFor(() => expect(body).not.toBeNull());
  expect(body.build_no_proxy).toBe("pkg-01,pkg-01:5000,localhost,127.0.0.1,.svc,.cluster.local,10.10.10.11,10.10.10.12");
});

test("노드 조회가 실패하면 힌트가 노드 IP 없이 그 사실을 말한다", async () => {
  server.use(
    http.get("/api/admin/control-state", () => HttpResponse.json(CS)),
    http.get("/api/admin/control-state/proxy-hints", () => HttpResponse.json({
      registry: "pkg-01:5000", registry_host: "pkg-01", nodes: [], nodes_known: false,
      suggested_no_proxy: ["pkg-01", "pkg-01:5000", "localhost", "127.0.0.1", ".svc", ".cluster.local"],
      auto_added: ["pkg-01:5000", "pkg-01", "localhost", "127.0.0.1"],
    })));
  wrap();
  const hint = await screen.findByTestId("no-proxy-hint");
  expect(hint).toHaveTextContent("클러스터 조회 실패로 생략");
});

test("프록시가 없으면 「파드 네트워크」다", async () => {
  server.use(http.get("/api/admin/control-state", () => HttpResponse.json(CS)));
  wrap();
  expect(await screen.findByText("파드 네트워크")).toBeInTheDocument();
});

test("빌드 프록시 3종을 입력해 저장하면 PUT 본문에 실리고 현재 상태에 보인다", async () => {
  let body: any = null;
  // 저장 뒤 훅이 control-state 를 무효화해 다시 GET 한다 -- 서버 상태를 흉내낸다.
  let state: any = CS;
  server.use(
    http.get("/api/admin/control-state", () => HttpResponse.json(state)),
    http.put("/api/admin/control-state", async ({ request }) => {
      body = await request.json();
      state = { ...CS, build_http_proxy: "http://proxy.corp:3128",
                build_https_proxy: null, build_no_proxy: ".corp.example" };
      return HttpResponse.json(state);
    }));
  wrap();
  await screen.findByLabelText("유지보수");
  await userEvent.type(screen.getByLabelText("HTTP 프록시"), "http://proxy.corp:3128");
  await userEvent.type(screen.getByLabelText("프록시 제외"), ".corp.example");
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  await waitFor(() => expect(body).not.toBeNull());
  expect(body.build_http_proxy).toBe("http://proxy.corp:3128");
  expect(body.build_https_proxy).toBeNull();
  expect(body.build_no_proxy).toBe(".corp.example");
  expect(await screen.findByText("http://proxy.corp:3128 / — (제외: .corp.example)")).toBeInTheDocument();
});

test("프록시 주소가 거절되면(invalid_proxy_url) 한국어 사유를 보여준다", async () => {
  server.use(
    http.get("/api/admin/control-state", () => HttpResponse.json(CS)),
    http.put("/api/admin/control-state", () =>
      HttpResponse.json({ detail: "invalid_proxy_url" }, { status: 422 })));
  wrap();
  await screen.findByLabelText("유지보수");
  await userEvent.type(screen.getByLabelText("HTTPS 프록시"), "user:pw@proxy");
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  expect(await screen.findByText(/프록시 주소는 http\(s\):\/\/호스트/)).toBeInTheDocument();
});

test("프록시가 없으면 현재 상태에 「없음(직접 연결)」로 표시한다", async () => {
  server.use(http.get("/api/admin/control-state", () => HttpResponse.json(CS)));
  wrap();
  expect(await screen.findByText("없음(직접 연결)")).toBeInTheDocument();
});

test("빌드 노드는 자유 입력이 아니라 보고된 노드 중에서 고른다(select)", async () => {
  server.use(
    http.get("/api/admin/control-state", () => HttpResponse.json(CS)),
    http.get("/api/admin/nodes", () => HttpResponse.json([
      { node_name: "dms-w1", reported_at: "t", fresh: true, report: {} },
      { node_name: "dms-w2", reported_at: "t", fresh: true, report: {} },
    ])),
  );
  wrap();
  const select = (await screen.findByLabelText("빌드 노드")) as HTMLSelectElement;
  const optionValues = Array.from(select.options).map((o) => o.value);
  // "지정 안 함"(빈 값) + 보고된 두 노드만 있고, 자유 입력(텍스트 박스)이 아니다.
  expect(optionValues).toEqual(["", "dms-w1", "dms-w2"]);
  expect(select.tagName).toBe("SELECT");
});

test("빌드 노드를 서버가 거절하면(unknown_build_node) 한글 메시지를 보여준다", async () => {
  server.use(
    http.get("/api/admin/control-state", () => HttpResponse.json(CS)),
    http.put("/api/admin/control-state", () =>
      HttpResponse.json({ detail: "unknown_build_node" }, { status: 422 })),
  );
  wrap();
  await screen.findByLabelText("유지보수");
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  expect(await screen.findByText("등록된 에이전트 노드 중에서 선택해 주세요")).toBeInTheDocument();
});

test("renders no warning banners when both flags are off", async () => {
  server.use(http.get("/api/admin/control-state", () =>
    HttpResponse.json({ maintenance: 0, drain: 0, reason: null, build_node_name: null, changed_by: null, changed_at: null })));
  wrap();
  await screen.findByLabelText("유지보수");
  expect(screen.queryByText((_, node) => Boolean(node?.textContent?.startsWith("유지보수 중")))).not.toBeInTheDocument();
  expect(screen.queryByText("드레인 중 — 진행 중인 작업이 더 전진하지 않습니다")).not.toBeInTheDocument();
});

// ── 슬라이스 36: 현재 상태 카드(적용값 배지·영향 요약) + 변경 이력 ──────────────

test("현재 상태 카드가 적용값 배지·사유·영향 요약을 보여준다", async () => {
  server.use(
    http.get("/api/admin/control-state", () => HttpResponse.json(CS)),
    // 영향 요약은 창 없는 비종단 집계(live_by_state)를 센다(2026-11-11) -- 24h 창(by_state)이면 창보다 오래 도는 잡이 빠진다.
    // by_state 는 다른 수로 둬 어느 쪽을 읽는지 가른다.
    http.get("/api/admin/metrics/jobs", () => HttpResponse.json({
      by_state: [{ state: "Running", count: 7 }, { state: "Pending", count: 8 }],
      live_by_state: [{ state: "Running", count: 2 }, { state: "Pending", count: 3 }] })),
  );
  wrap();
  expect(await screen.findByText("유지보수 ON")).toBeInTheDocument();   // 적용값 배지
  expect(screen.getByText("드레인 OFF")).toBeInTheDocument();
  expect(screen.getByText("점검")).toBeInTheDocument();                  // 사유
  expect(await screen.findByText("2")).toBeInTheDocument();              // 실행 중
  expect(screen.getByText("3")).toBeInTheDocument();                     // 대기
  expect(screen.queryByText("7")).not.toBeInTheDocument();
});

test("변경 이력이 diff 요약으로 나열된다", async () => {
  server.use(
    http.get("/api/admin/control-state", () => HttpResponse.json(CS)),
    http.get("/api/admin/control-state/history", () => HttpResponse.json([
      { at: "2026-08-18T08:33:42Z", actor: "mason",
        before: { ...CS, maintenance: 0, reason: null },
        after: { ...CS, maintenance: 1, reason: "테스트" } },
      { at: "2026-08-18T08:00:00Z", actor: "ops", before: null, after: CS },
    ])),
  );
  wrap();
  expect(await screen.findByText("유지보수 OFF→ON · 사유 —→'테스트'")).toBeInTheDocument();
  expect(screen.getByText("초기 설정")).toBeInTheDocument();   // before 없음
  expect(screen.getByText("mason")).toBeInTheDocument();
});

// ── 2026-11-11 전수조사 B04·B74·B63 ─────────────────────────────────────────────

test("재조회(창 포커스 등)로 q.data 가 바뀌어도 편집 중인 입력을 덮어쓰지 않는다", async () => {
  // 컨트롤러가 30초마다 artifact_base_check_at 을 갱신해 재조회 응답이 늘 새 객체다 -- 그 필드만 바뀐 재조회.
  let gets = 0;
  server.use(http.get("/api/admin/control-state", () => {
    gets += 1;
    return HttpResponse.json({ ...CS, artifact_base_check_at: `2026-11-11T10:2${gets}:00Z` });
  }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><ControlStatePage /></QueryClientProvider>);
  await screen.findByLabelText("유지보수");
  await userEvent.type(screen.getByLabelText("HTTP 프록시"), "http://proxy.corp:3128");
  await userEvent.click(screen.getByLabelText("드레인"));
  const before = gets;
  await qc.invalidateQueries({ queryKey: ["control-state"] });
  await waitFor(() => expect(gets).toBeGreaterThan(before));
  expect(screen.getByLabelText("HTTP 프록시")).toHaveValue("http://proxy.corp:3128");
  expect(screen.getByLabelText("드레인")).toBeChecked();
});

test("마운트 때 캐시가 낡았으면 폼은 재조회로 온 서버 값으로 확정되고, 그 전엔 저장할 수 없다(2026-11-11 리뷰)", async () => {
  // ['control-state'] 캐시는 빌드 화면도 채운다 -- 그사이 남이 드레인·프록시를 바꿨으면 캐시는 낡은 값이다. 옛 캐시로 폼을
  // 확정하면 그대로 「저장」할 때 남의 변경이 되돌려진다.
  let release!: () => void;
  const gate = new Promise<void>((r) => { release = r; });
  server.use(http.get("/api/admin/control-state", async () => {
    await gate;
    return HttpResponse.json({ ...CS, drain: 1, build_http_proxy: "http://new.proxy:3128" });
  }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(["control-state"], CS, { updatedAt: Date.now() - 60_000 });
  render(<QueryClientProvider client={qc}><ControlStatePage /></QueryClientProvider>);
  expect(await screen.findByLabelText("유지보수")).toBeChecked();       // 조회 중엔 캐시로 미리 채운다
  expect(screen.getByLabelText("드레인")).not.toBeChecked();
  expect(screen.getByRole("button", { name: "저장" })).toBeDisabled();  // 낡은 값 저장 금지
  release();
  await waitFor(() => expect(screen.getByLabelText("드레인")).toBeChecked());
  expect(screen.getByLabelText("HTTP 프록시")).toHaveValue("http://new.proxy:3128");
  expect(screen.getByRole("button", { name: "저장" })).toBeEnabled();
});

test("신선한 캐시(마운트 조회 없음)면 그 값으로 바로 확정해 저장할 수 있다", async () => {
  // isFetchedAfterMount 로 기다리면 조회가 없는 이 경우 폼이 영영 빈 채로 남는다 -- 그 함정을 고정한다.
  let gets = 0;
  server.use(http.get("/api/admin/control-state", () => { gets += 1; return HttpResponse.json(CS); }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 60_000 } } });
  qc.setQueryData(["control-state"], { ...CS, build_http_proxy: "http://cached:3128" });
  render(<QueryClientProvider client={qc}><ControlStatePage /></QueryClientProvider>);
  expect(await screen.findByLabelText("HTTP 프록시")).toHaveValue("http://cached:3128");
  await waitFor(() => expect(screen.getByRole("button", { name: "저장" })).toBeEnabled());
  expect(gets).toBe(0);
});

test("저장에 성공하면 폼을 서버가 정규화해 돌려준 값으로 다시 맞춘다", async () => {
  let state: any = CS;
  server.use(
    http.get("/api/admin/control-state", () => HttpResponse.json(state)),
    http.put("/api/admin/control-state", async () => {
      // 서버 정규화를 흉내낸다(예: 프록시 주소 끝 슬래시 정리).
      state = { ...CS, build_http_proxy: "http://proxy.corp:3128" };
      return HttpResponse.json(state);
    }));
  wrap();
  await screen.findByLabelText("유지보수");
  await userEvent.type(screen.getByLabelText("HTTP 프록시"), "http://proxy.corp:3128/");
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  await waitFor(() => expect(screen.getByLabelText("HTTP 프록시")).toHaveValue("http://proxy.corp:3128"));
});

test("현재 상태 카드의 경로·프록시 값은 좁은 폭에서 줄바꿈된다(break-all)", async () => {
  const noProxy = "10.112.0.50,10.112.0.50:5000,localhost,127.0.0.1,.svc,.cluster.local,10.10.10.21,10.10.10.22";
  server.use(http.get("/api/admin/control-state", () => HttpResponse.json({
    ...CS, build_http_proxy: "http://proxy.corp:3128", build_no_proxy: noProxy,
    build_proxy_ca_path: "/etc/pki/corp-proxy-ca.pem" })));
  wrap();
  const proxy = await screen.findByText(`http://proxy.corp:3128 / — (제외: ${noProxy})`);
  expect(proxy).toHaveClass("break-all");
  expect(screen.getByText("/home/mason/dms-dev/dms", { selector: "span" })).toHaveClass("break-all");
  expect(screen.getByText("/etc/pki/corp-proxy-ca.pem", { selector: "span" })).toHaveClass("break-all");
});

test("빌드 소스 경로 안내가 실제 거절 조건('..' 불가·300자)을 말한다", async () => {
  server.use(http.get("/api/admin/control-state", () => HttpResponse.json(CS)));
  wrap();
  expect(await screen.findByText(/300자 이하, '\.\.' 불가/)).toBeInTheDocument();
});

test("동일 값 재저장은 「변경 없음」으로 정직하게 표기한다", async () => {
  server.use(
    http.get("/api/admin/control-state", () => HttpResponse.json(CS)),
    http.get("/api/admin/control-state/history", () => HttpResponse.json([
      { at: "2026-08-18T08:40:00Z", actor: "mason", before: CS, after: CS },
    ])),
  );
  wrap();
  expect(await screen.findByText("변경 없음")).toBeInTheDocument();
});
