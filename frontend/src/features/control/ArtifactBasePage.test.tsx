import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { ArtifactBasePage } from "./ArtifactBasePage";

// 이력은 모든 렌더가 조회한다 -- 기본 빈 배열, 이력 테스트만 덮어쓴다
// (resetHandlers 가 이 초기 핸들러로 되돌린다).
const server = setupServer(
  http.get("/api/admin/artifact-base/history", () => HttpResponse.json([])));
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

const BASE = {
  effective: "file:///cephfs/dms/artifacts", source: "env",
  db_value: null, env_value: "file:///cephfs/dms/artifacts", locked_by_jobs: 0,
  checks: {
    api: { ok: true, reason: null },
    controller: { pending: false, ok: true, reason: null, checked_at: "2026-08-10T00:00:00Z" },
    nodes: [
      { node_name: "w1", reported_at: "t", fresh: true, pending: false, exists: true, writable: true },
      { node_name: "w2", reported_at: "t", fresh: true, pending: true, exists: null, writable: null },
    ],
  },
};

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // 라우터: 검증 카드가 마운트 현황 화면으로 가는 링크를 단다(2026-11-23).
  return render(<QueryClientProvider client={qc}><MemoryRouter><ArtifactBasePage /></MemoryRouter></QueryClientProvider>);
}

test("env 소스 배지 + 노드 행의 「확인 대기 중」을 실패와 구분해 렌더한다", async () => {
  server.use(http.get("/api/admin/artifact-base", () => HttpResponse.json(BASE)));
  wrap();
  expect(await screen.findByText("env 기본")).toBeInTheDocument();
  // w2 는 아직 새 경로를 프로브하지 않았다(설계 §4) -- "확인 대기 중"이지
  // "없음/불가"(실패)가 아니다.
  const w2 = screen.getByText("w2").closest("tr")!;
  expect(w2).toHaveTextContent("확인 대기 중");
  expect(w2).not.toHaveTextContent("불가");
  const w1 = screen.getByText("w1").closest("tr")!;
  expect(w1).toHaveTextContent("있음");
  expect(w1).toHaveTextContent("가능");
  // writable 한계 문구(설계 §2.4b: 에이전트 uid 기준)를 화면에 그대로 적는다
  expect(screen.getByText(/에이전트 프로세스\(uid\) 기준/)).toBeInTheDocument();
});

test("DB 소스 배지 + 컨트롤러 홉 실패는 사유 코드와 함께 실패로 렌더된다", async () => {
  server.use(http.get("/api/admin/artifact-base", () => HttpResponse.json({
    ...BASE, source: "db", db_value: "file:///new",
    checks: { ...BASE.checks,
      controller: { pending: false, ok: false, reason: "artifact_base_missing", checked_at: "t" } },
  })));
  wrap();
  expect(await screen.findByText("DB 설정")).toBeInTheDocument();
  expect(screen.getByText("실패")).toBeInTheDocument();
  // 사유 코드는 원시 코드가 아니라 번역 문구로(reasonText -- 슬라이스 38)
  expect(screen.getByText("경로가 존재하지 않습니다")).toBeInTheDocument();
  // 요약 배지: 실패 1(컨트롤러) 이 대기 1(w2) 을 이긴다 -- 뭉개지 않는다
  expect(screen.getByText("문제 1건")).toBeInTheDocument();
});

test("검증 버튼은 저장 없이 validate 만 호출한다", async () => {
  const calls: string[] = [];
  server.use(
    http.get("/api/admin/artifact-base", () => HttpResponse.json(BASE)),
    http.post("/api/admin/artifact-base/validate", () => {
      calls.push("validate");
      return HttpResponse.json({ normalized: "file:///new", ok: true });
    }),
    http.put("/api/admin/artifact-base", () => {
      calls.push("put");
      return HttpResponse.json(BASE);
    }),
  );
  wrap();
  await screen.findByText("env 기본");
  await userEvent.type(screen.getByLabelText("새 경로"), "/new");
  await userEvent.click(screen.getByRole("button", { name: "검증" }));
  expect(await screen.findByText(/검증 통과/)).toBeInTheDocument();
  expect(calls).toEqual(["validate"]);   // PUT 이 나가지 않았다(저장 없음)
});

test("409 잠금이면 확인 다이얼로그를 강제하고, 강제 변경만 force=true 를 보낸다", async () => {
  const bodies: unknown[] = [];
  server.use(
    http.get("/api/admin/artifact-base", () =>
      HttpResponse.json({ ...BASE, locked_by_jobs: 3 })),
    http.put("/api/admin/artifact-base", async ({ request }) => {
      const body = (await request.json()) as { uri: string; force: boolean };
      bodies.push(body);
      if (!body.force) {
        return HttpResponse.json({ detail: "artifact_base_locked" }, { status: 409 });
      }
      return HttpResponse.json(BASE);
    }),
  );
  wrap();
  await screen.findByText("env 기본");
  await userEvent.type(screen.getByLabelText("새 경로"), "/new");
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  // 설계 §3: N 건과 "열람이 깨집니다"를 확인시킨 뒤에만 force 재요청
  expect(await screen.findByText(/기존 잡 3건/)).toBeInTheDocument();
  expect(screen.getByText(/아티팩트·로그 열람이 깨집니다/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "강제 변경" }));
  expect(bodies).toEqual([{ uri: "/new", force: false }, { uri: "/new", force: true }]);
});

test("잠금 사전 고지: 잡 N건 참조를 저장 전에 보여주고, 0건이면 잠금 없음 문구", async () => {
  server.use(http.get("/api/admin/artifact-base", () =>
    HttpResponse.json({ ...BASE, locked_by_jobs: 117 })));
  wrap();
  // 409 를 맞기 전에 화면이 먼저 말한다(설계 §2.3)
  expect(await screen.findByText("잡 117건")).toBeInTheDocument();
  expect(screen.getByText(/강제 확인을 거칩니다/)).toBeInTheDocument();
});

test("입력을 고치면 옛 검증 통과 문구가 사라진다(stale 방지)", async () => {
  server.use(
    http.get("/api/admin/artifact-base", () => HttpResponse.json(BASE)),
    http.post("/api/admin/artifact-base/validate", () =>
      HttpResponse.json({ normalized: "file:///new", ok: true })),
  );
  wrap();
  await screen.findByText("env 기본");
  await userEvent.type(screen.getByLabelText("새 경로"), "/new");
  await userEvent.click(screen.getByRole("button", { name: "검증" }));
  expect(await screen.findByText(/검증 통과/)).toBeInTheDocument();
  // 한 글자만 바뀌어도 "검증 통과"는 다른 입력의 이야기다 -- 그 자리에서 지운다
  await userEvent.type(screen.getByLabelText("새 경로"), "x");
  expect(screen.queryByText(/검증 통과/)).not.toBeInTheDocument();
});

test("저장 성공은 문구로 확인시키고 입력을 비운다", async () => {
  server.use(
    http.get("/api/admin/artifact-base", () => HttpResponse.json(BASE)),
    http.put("/api/admin/artifact-base", () => HttpResponse.json(BASE)),
  );
  wrap();
  await screen.findByText("env 기본");
  await userEvent.type(screen.getByLabelText("새 경로"), "/new");
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  expect(await screen.findByText(/저장됨/)).toBeInTheDocument();
  expect(screen.getByLabelText("새 경로")).toHaveValue("");
});

test("변경 이력: 무엇→무엇 + 강제 배지, 첫 설정의 before 는 env 기본으로 표기", async () => {
  server.use(
    http.get("/api/admin/artifact-base", () => HttpResponse.json(BASE)),
    http.get("/api/admin/artifact-base/history", () => HttpResponse.json([
      { at: "2026-08-18T01:00:00Z", actor: "mason",
        before: { artifact_base_uri: "file:///a" },
        after: { artifact_base_uri: "file:///b", forced: true, affected_jobs: 7 } },
      { at: "2026-08-17T01:00:00Z", actor: "admin",
        before: { artifact_base_uri: null },
        after: { artifact_base_uri: "file:///a", forced: false, affected_jobs: 0 } },
    ])),
  );
  wrap();
  expect(await screen.findByText("file:///a → file:///b")).toBeInTheDocument();
  // 강제 통과의 대가(영향 잡 수)가 이력에서 보인다(설계 §2.3)
  expect(screen.getByText(/강제 · 잡 7건 영향/)).toBeInTheDocument();
  // before null = 당시 env 유효 -- 값을 지어내지 않고 사실을 밝힌다
  expect(screen.getByText("(env 기본) → file:///a")).toBeInTheDocument();
  expect(screen.getByText("mason")).toBeInTheDocument();
});

test("3홉 요약 배지: 실패 없이 대기만 있으면 「확인 대기 N건」", async () => {
  server.use(http.get("/api/admin/artifact-base", () => HttpResponse.json(BASE)));
  wrap();
  // BASE: api ok·컨트롤러 ok·w1 ok·w2 pending -- 대기 1, 실패 0
  expect(await screen.findByText("확인 대기 1건")).toBeInTheDocument();
});

test("경로 변경 주의점은 경고 툴팁 -- 트리거가 aria-describedby 로 내용을 가리키고 클릭으로 열린다", async () => {
  // 2026-09-30 사용자 요청: "아티팩트의 경로변경에 대한 주의점을 툴팁으로 경고성으로".
  server.use(http.get("/api/admin/artifact-base", () => HttpResponse.json(BASE)));
  wrap();
  const trigger = await screen.findByRole("button", { name: "변경 전 주의" });
  const tip = document.getElementById(trigger.getAttribute("aria-describedby")!)!;
  expect(tip).toHaveAttribute("role", "tooltip");
  expect(tip.className).toMatch(/\bhidden\b/);                 // 평소엔 숨김(hover·focus 로 열림)
  // 실제 동작에 근거한 주의점 -- 기존 기록 비이동, other x 필수, 공유 마운트, 허용 접두.
  expect(tip).toHaveTextContent("옮겨지지 않습니다");
  expect(tip).toHaveTextContent("다른 사용자 실행(x) 권한 필수(711 또는 755)");
  // 보조 그룹(2026-10-07 D6/D12): 그룹 쓰기·ACL 금지와, 그룹으로 여는 750/710 도 거부된다는 정정.
  expect(tip).toHaveTextContent("그룹 쓰기(g+w)·POSIX ACL 쓰기 항목·기본 ACL 금지");
  expect(tip).toHaveTextContent("artifact_base_group_writable");
  expect(tip).toHaveTextContent("750/710 도 이제 거부");
  expect(tip).not.toHaveTextContent("700/750/770");
  expect(tip).toHaveTextContent("모든 노드에 같은 경로로 마운트된 공유 스토리지");
  expect(tip).toHaveTextContent("DMS_ARTIFACT_BASE_ALLOWED_PREFIXES");
  await userEvent.click(trigger);                                // 터치·클릭으로 열림
  expect(trigger).toHaveAttribute("aria-expanded", "true");
  expect(tip.className).not.toMatch(/\bhidden\b/);
  await userEvent.keyboard("{Escape}");                          // WCAG 1.4.13: Esc 로 닫힌다
  expect(trigger).toHaveAttribute("aria-expanded", "false");
  expect(tip.className).toMatch(/\bhidden\b/);
});

// ---- API 파드 자체 점검 · 두 관점(2026-11-23 사고: API 파드가 공유 스토리지 미마운트 노드에 떠 아티팩트를 못 읽었는데, 컨트롤러
// 점검은 자기 노드 기준이라 정상이었다) ----
const ago = (s: number) => new Date(Date.now() - s * 1000).toISOString();
const API_OK = {
  ok: true, reason: null, posture: null, source: "monitor", instance: "dms-api-7c9d-abcde", node_name: "ion2110",
  checked_at: ago(23), fstype: "ceph", local_fs: false, interval_seconds: 60,
  availability: { ok: true, reason: null, jobs_checked: 5, jobs_found: 5 },
  gate: { enabled: true, state: "passed", at: ago(3600) },
};
const API_DOWN = {
  ...API_OK, ok: false, reason: "artifact_base_jobs_missing", instance: "dms-api-7c9d-xyz12", node_name: "s-ssc-dms-26001",
  fstype: "xfs", local_fs: true, availability: { ok: false, reason: "artifact_base_jobs_missing", jobs_checked: 5, jobs_found: 0 },
};
const CTRL_OK = { pending: false, ok: true, reason: null, checked_at: ago(10), node_name: "ion2110" };
const withChecks = (checks: Record<string, unknown>) => ({ ...BASE, checks: { ...BASE.checks, ...checks } });
const panel = (name: string) => screen.getByRole("region", { name });

test("두 관점 패널: API 파드(파드·노드·파일시스템·확인 주기·시작 점검)와 컨트롤러(노드)를 따로 보인다", async () => {
  server.use(http.get("/api/admin/artifact-base", () => HttpResponse.json(withChecks({ api: API_OK, controller: CTRL_OK }))));
  wrap();
  await screen.findByText("env 기본");
  const api = panel("API 파드 관점");
  expect(within(api).getByText("정상")).toBeInTheDocument();
  expect(within(api).getByText("dms-api-7c9d-abcde")).toBeInTheDocument();
  expect(within(api).getByText("ion2110")).toBeInTheDocument();
  expect(within(api).getByText("ceph")).toBeInTheDocument();
  expect(within(api).queryByText(/로컬 디스크로 보임/)).toBeNull();
  expect(within(api).getByText(/^\d+초 전 확인 · 60초 주기$/)).toBeInTheDocument();
  expect(within(api).getByText("· 최근 작업 기록 5/5개 확인")).toBeInTheDocument();
  expect(within(api).getByText("통과")).toBeInTheDocument();
  const ctrl = panel("컨트롤러 관점");
  expect(within(ctrl).getByText("정상")).toBeInTheDocument();
  expect(within(ctrl).getByText("ion2110")).toBeInTheDocument();
  expect(screen.queryByRole("note")).toBeNull();                       // 엇갈림 없음
});

test("사고 모양: API 만 못 읽고 컨트롤러는 정상 -- 엇갈림 상자가 API 노드를 짚고, 로컬 디스크 주의가 뜬다", async () => {
  server.use(http.get("/api/admin/artifact-base", () =>
    HttpResponse.json(withChecks({ api: API_DOWN, controller: CTRL_OK }))));
  wrap();
  await screen.findByText("env 기본");
  const api = panel("API 파드 관점");
  expect(within(api).getByText("실패")).toBeInTheDocument();
  expect(within(api).getByText(/^최근 작업의 기록 디렉터리가 이 경로에 없습니다/)).toBeInTheDocument();
  expect(within(api).getByText("· 최근 작업 기록 0/5개 확인")).toBeInTheDocument();
  expect(within(api).getByText(/로컬 디스크로 보임/)).toBeInTheDocument();
  expect(screen.getByRole("note")).toHaveTextContent(
    "API 파드(노드 s-ssc-dms-26001)만 아티팩트 경로를 읽지 못합니다. 컨트롤러(노드 ion2110)는 같은 경로를 정상으로 봅니다 — "
    + "노드 s-ssc-dms-26001 에 공유 스토리지가 마운트돼 있는지 확인하세요");
  expect(within(screen.getByRole("note")).getByRole("link", { name: "마운트 현황 보기" })).toHaveAttribute("href", "/admin/mounts");
  expect(screen.getByText("문제 1건")).toBeInTheDocument();
});

test("잡 확인 전에 실패하면(입출력 오류·시간 초과) 「확인할 최근 작업 없음」을 붙이지 않는다", async () => {
  // jobs_found null 은 「확인할 잡 없음」과 「그 단계 전에 실패」 둘 다다 -- 실패 쪽에 「작업 없음」을 붙이면 거짓(2026-11-23 리뷰).
  const unreadable = { ...API_DOWN, ok: false, reason: "artifact_base_unreadable",
                       availability: { ok: false, reason: "artifact_base_unreadable", jobs_checked: 5, jobs_found: null } };
  server.use(http.get("/api/admin/artifact-base", () =>
    HttpResponse.json(withChecks({ api: unreadable, controller: CTRL_OK }))));
  wrap();
  await screen.findByText("env 기본");
  const api = panel("API 파드 관점");
  expect(within(api).getByText(/^경로를 읽을 수 없습니다/)).toBeInTheDocument();
  expect(within(api).queryByText(/확인할 최근 작업 없음/)).toBeNull();
  expect(within(api).queryByText(/최근 작업 기록/)).toBeNull();
});

test("반대 방향: 컨트롤러만 못 읽으면 컨트롤러 노드를 짚는다 -- 노드를 모르면 「노드 모름」", async () => {
  server.use(http.get("/api/admin/artifact-base", () => HttpResponse.json(withChecks({
    api: API_OK, controller: { pending: false, ok: false, reason: "artifact_base_missing", checked_at: ago(5), node_name: null },
  }))));
  wrap();
  await screen.findByText("env 기본");
  expect(screen.getByRole("note")).toHaveTextContent(
    "컨트롤러(노드 모름)만 아티팩트 경로를 읽지 못합니다. API 파드(노드 ion2110)는 같은 경로를 정상으로 봅니다 — "
    + "컨트롤러가 뜬 노드에 공유 스토리지가 마운트돼 있는지 확인하세요");
  expect(within(panel("컨트롤러 관점")).getByText("모름")).toBeInTheDocument();
});

test("권한(posture) 사유는 엇갈림 상자에 넣지 않는다 -- API 는 읽기 정상 + 권한 주의, 컨트롤러 권한 실패도 마찬가지", async () => {
  server.use(http.get("/api/admin/artifact-base", () => HttpResponse.json(withChecks({
    api: { ...API_OK, ok: false, reason: "artifact_base_group_writable", posture: "artifact_base_group_writable" },
    controller: { ...CTRL_OK, ok: false, reason: "artifact_base_world_writable" },
  }))));
  wrap();
  await screen.findByText("env 기본");
  const api = panel("API 파드 관점");
  expect(within(api).getByText("주의")).toBeInTheDocument();
  expect(within(api).getByText(/^아티팩트 경로\(base\)에 그룹 쓰기 권한/)).toBeInTheDocument();
  expect(screen.queryByRole("note")).toBeNull();
  // 요약은 API 를 하나로 센다(권한 문제도 문제) -- API 1 + 컨트롤러 1
  expect(screen.getByText("문제 2건")).toBeInTheDocument();
});

test("시작 점검 문구: 대기·형제 없음·비상구·주기 점검 꺼짐(즉석 확인)", async () => {
  const cases: [Record<string, unknown>, RegExp][] = [
    [{ gate: { enabled: true, state: "waiting", at: null } }, /^Ready 대기 중$/],
    [{ gate: { enabled: true, state: "started_degraded", at: null } }, /^형제 파드가 없어 점검 실패인 채 서비스 시작$/],
    [{ gate: { enabled: false, state: "disabled", at: null } }, /^비상구로 꺼짐$/],
    [{ gate: { enabled: false, state: "off", at: null }, source: "instant" }, /^주기 점검 꺼짐$/],
  ];
  for (const [over, text] of cases) {
    server.use(http.get("/api/admin/artifact-base", () =>
      HttpResponse.json(withChecks({ api: { ...API_OK, ...over }, controller: CTRL_OK }))));
    const view = wrap();
    await screen.findByText("env 기본");
    expect(within(panel("API 파드 관점")).getByText(text)).toBeInTheDocument();
    if (over.source === "instant") expect(within(panel("API 파드 관점")).getByText("조회 시 즉석 확인")).toBeInTheDocument();
    view.unmount();
  }
});

test("API 파드별 카드: 다른 파드가 있을 때만, 보고 끊김은 흐리게 -- 다른 파드의 읽기 실패도 엇갈림 상자가 짚는다", async () => {
  const pod = (o: Record<string, unknown>) => ({
    instance: "p", node_name: "n", uri: "file:///cephfs/dms/artifacts", ok: true, reason: null, posture: null,
    fstype: "ceph", local_fs: false, gate_state: "passed", started_at: ago(7200), checked_at: ago(30), ready_at: ago(7100),
    fresh: true, this_pod: false, ...o });
  server.use(http.get("/api/admin/artifact-base", () => HttpResponse.json(withChecks({
    api: API_OK, controller: CTRL_OK,
    api_pods: [
      pod({ instance: "dms-api-7c9d-abcde", node_name: "ion2110", this_pod: true }),
      pod({ instance: "dms-api-new-q1w2e", node_name: "s-ssc-dms-26001", ok: 0, reason: "artifact_base_jobs_missing",
            gate_state: "waiting", ready_at: null, fstype: "xfs", local_fs: true }),
      pod({ instance: "dms-api-old-zz9", node_name: "ion2111", fresh: false }),
    ],
  }))));
  wrap();
  await screen.findByText("env 기본");
  const self = screen.getByRole("listitem", { name: "API 파드 dms-api-7c9d-abcde" });
  expect(within(self).getByText("이 응답")).toBeInTheDocument();
  const waiting = screen.getByRole("listitem", { name: "API 파드 dms-api-new-q1w2e" });
  expect(within(waiting).getByText("실패")).toBeInTheDocument();               // DB 정수 0 = 실패
  expect(within(waiting).getByText("Ready 대기 중")).toBeInTheDocument();
  expect(within(waiting).getByText("· Ready 전")).toBeInTheDocument();
  expect(within(waiting).getByText(/로컬 디스크로 보임/)).toBeInTheDocument();
  const stale = screen.getByRole("listitem", { name: "API 파드 dms-api-old-zz9" });
  expect(stale.className).toContain("opacity-60");
  expect(within(stale).getByText("보고 끊김")).toBeInTheDocument();
  expect(waiting.className).not.toContain("opacity-60");
  expect(screen.getByRole("note")).toHaveTextContent("API 파드(노드 s-ssc-dms-26001)만 아티팩트 경로를 읽지 못합니다");
  // 요약은 다른 API 파드(fresh)의 실패도 센다 -- Ready 를 기다리는 새 파드가 실패하는데 「모두 정상」·「확인 대기」만 뜨면 아래
  // 엇갈림 상자·카드와 모순(2026-11-23 라이브 확인). 보고 끊긴 파드(dms-api-old-zz9)는 세지 않는다.
  expect(screen.getByText("문제 1건")).toBeInTheDocument();
});

test("엇갈림 상자: 실패한 API 파드의 노드에 정상 파드(컨트롤러·옛 API 파드)가 있으면 노드 마운트가 아니라 파드 쪽을 짚는다", async () => {
  // dms-k8s e25a2dfb(d40 실증) 이식: 같은 노드의 컨트롤러·옛 API 파드는 정상인데 「노드 … 에 공유 스토리지가 마운트돼 있는지」라고 했다.
  const pod = (o: Record<string, unknown>) => ({
    instance: "p", node_name: "n", uri: "file:///cephfs/dms/artifacts", ok: true, reason: null, posture: null,
    fstype: "ceph", local_fs: false, gate_state: "passed", started_at: ago(7200), checked_at: ago(30), ready_at: ago(7100),
    fresh: true, this_pod: false, ...o });
  server.use(http.get("/api/admin/artifact-base", () => HttpResponse.json(withChecks({
    api: API_OK, controller: CTRL_OK,                                        // 둘 다 ion2110
    api_pods: [
      pod({ instance: "dms-api-7c9d-abcde", node_name: "ion2110", this_pod: true }),
      pod({ instance: "dms-api-new-q1w2e", node_name: "ion2110", ok: 0, reason: "artifact_base_jobs_missing",
            gate_state: "waiting", ready_at: null, fstype: "xfs", local_fs: true }),
    ],
  }))));
  wrap();
  await screen.findByText("env 기본");
  const note = screen.getByRole("note");
  expect(note).toHaveTextContent("API 파드(노드 ion2110)만 아티팩트 경로를 읽지 못합니다");
  expect(note).toHaveTextContent("같은 노드의 다른 DMS 파드(컨트롤러 노드 ion2110)는 정상입니다");
  expect(note).toHaveTextContent("노드 마운트가 아니라 그 파드의 hostPath·마운트 전파");
  expect(note).not.toHaveTextContent("공유 스토리지가 마운트돼 있는지 확인하세요");
});

test("엇갈림 상자: 같은 노드의 정상 파드가 컨트롤러가 아니면 「다른 API 파드」, 노드 모르는 실패 파드가 섞이면 파드 쪽 단정 안 함", async () => {
  // 2026-11-27 리뷰: 컨트롤러가 다른 노드(ion2110)인데 「컨트롤러 노드 ion2110 는 정상」이라고 같은 노드처럼 불렀다.
  const pod = (o: Record<string, unknown>) => ({
    instance: "p", node_name: "n", uri: "file:///cephfs/dms/artifacts", ok: true, reason: null, posture: null,
    fstype: "ceph", local_fs: false, gate_state: "passed", started_at: ago(7200), checked_at: ago(30), ready_at: ago(7100),
    fresh: true, this_pod: false, ...o });
  const failing = { ok: false, reason: "artifact_base_jobs_missing", gate_state: "waiting", ready_at: null };
  server.use(http.get("/api/admin/artifact-base", () => HttpResponse.json(withChecks({
    api: { ...API_OK, node_name: "w7" }, controller: CTRL_OK,          // 이 응답 파드는 w7, 컨트롤러는 ion2110
    api_pods: [
      pod({ instance: "dms-api-self", node_name: "w7", this_pod: true }),
      pod({ instance: "dms-api-new", node_name: "w7", ...failing }),
    ],
  }))));
  const { unmount } = wrap();
  await screen.findByText("env 기본");
  expect(screen.getByRole("note")).toHaveTextContent("같은 노드의 다른 DMS 파드(다른 API 파드)는 정상입니다");
  unmount();
  server.use(http.get("/api/admin/artifact-base", () => HttpResponse.json(withChecks({
    api: { ...API_OK, node_name: "w7" }, controller: CTRL_OK,
    api_pods: [
      pod({ instance: "dms-api-self", node_name: "w7", this_pod: true }),
      pod({ instance: "dms-api-new", node_name: "w7", ...failing }),
      pod({ instance: "dms-api-nonode", node_name: null, ...failing }),
    ],
  }))));
  wrap();
  await screen.findByText("env 기본");
  expect(screen.getByRole("note")).not.toHaveTextContent("hostPath");
});

test("엇갈림 상자: 실패 노드 중 하나라도 정상 파드가 없으면 노드 마운트를 짚는다(보고 끊긴 정상 파드는 세지 않는다)", async () => {
  const pod = (o: Record<string, unknown>) => ({
    instance: "p", node_name: "n", uri: "file:///cephfs/dms/artifacts", ok: true, reason: null, posture: null,
    fstype: "ceph", local_fs: false, gate_state: "passed", started_at: ago(7200), checked_at: ago(30), ready_at: ago(7100),
    fresh: true, this_pod: false, ...o });
  server.use(http.get("/api/admin/artifact-base", () => HttpResponse.json(withChecks({
    api: API_OK, controller: CTRL_OK,
    api_pods: [
      pod({ instance: "dms-api-7c9d-abcde", node_name: "ion2110", this_pod: true }),
      pod({ instance: "dms-api-a", node_name: "ion2110", ok: false, reason: "artifact_base_jobs_missing", gate_state: "waiting" }),
      pod({ instance: "dms-api-b", node_name: "s-ssc-dms-26001", ok: false, reason: "artifact_base_jobs_missing" }),
      pod({ instance: "dms-api-gone", node_name: "s-ssc-dms-26001", fresh: false }),
    ],
  }))));
  wrap();
  await screen.findByText("env 기본");
  const note = screen.getByRole("note");
  expect(note).toHaveTextContent("API 파드(노드 ion2110, 노드 s-ssc-dms-26001)만 아티팩트 경로를 읽지 못합니다");
  expect(note).toHaveTextContent("노드 ion2110, 노드 s-ssc-dms-26001 에 공유 스토리지가 마운트돼 있는지 확인하세요");
  expect(note).not.toHaveTextContent("hostPath");
});

test("응답한 파드 하나뿐이면 파드 카드 목록을 그리지 않는다(패널과 같은 내용)", async () => {
  server.use(http.get("/api/admin/artifact-base", () => HttpResponse.json(withChecks({
    api: API_OK, controller: CTRL_OK,
    api_pods: [{ instance: "dms-api-7c9d-abcde", node_name: "ion2110", uri: null, ok: true, reason: null, posture: null,
                 fstype: null, local_fs: null, gate_state: "passed", started_at: ago(10), checked_at: ago(5), ready_at: ago(5),
                 fresh: true, this_pod: true }],
  }))));
  wrap();
  await screen.findByText("env 기본");
  expect(screen.queryByText("API 파드별 점검")).toBeNull();
});

test("「지금 다시 점검」은 POST :recheck 이고 응답으로 화면을 바로 바꾼다", async () => {
  let posted = 0;
  server.use(
    http.get("/api/admin/artifact-base", () => HttpResponse.json(withChecks({ api: API_DOWN, controller: CTRL_OK }))),
    http.post("/api/admin/artifact-base:recheck", () => {
      posted += 1;
      return HttpResponse.json(withChecks({ api: API_OK, controller: CTRL_OK }));
    }),
  );
  wrap();
  await screen.findByText("env 기본");
  expect(within(panel("API 파드 관점")).getByText("실패")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "지금 다시 점검" }));
  await waitFor(() => expect(within(panel("API 파드 관점")).getByText("정상")).toBeInTheDocument());
  expect(posted).toBe(1);
  expect(screen.queryByRole("note")).toBeNull();
});

test("다시 점검 실패는 사유를 한 줄로 알린다", async () => {
  server.use(
    http.get("/api/admin/artifact-base", () => HttpResponse.json(BASE)),
    http.post("/api/admin/artifact-base:recheck", () => HttpResponse.json({ detail: "http_500" }, { status: 500 })),
  );
  wrap();
  await screen.findByText("env 기본");
  await userEvent.click(screen.getByRole("button", { name: "지금 다시 점검" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/^다시 점검하지 못했습니다 — /);
});

test("노드 에이전트 표는 가로 넘침 래퍼 안이고, 각주가 새 에이전트의 덮는 마운트(로컬 디렉터리 판정)와 옛 에이전트의 한계를 말한다", async () => {
  server.use(http.get("/api/admin/artifact-base", () => HttpResponse.json(BASE)));
  wrap();
  await screen.findByText("env 기본");
  expect(screen.getByText("w1").closest("table")!.parentElement!.className).toContain("overflow-x-auto");
  const note = screen.getByText(/^이 표는 존재·쓰기만 보입니다 — 덮는 마운트를 보고하는 새 에이전트면/);
  expect(note).toHaveTextContent(/마운트 현황 화면이 경로를 덮는 마운트까지 보고, 그것이 노드 루트\(\/\)면\(공유 스토리지 미마운트\) 「로컬 디렉터리」로 짚습니다/);
  expect(note).toHaveTextContent(/옛 에이전트는 존재·쓰기만 확인하므로 마운트가 빠진 노드의 빈 로컬 디렉터리도 정상으로 보일 수 있음$/);
  expect(within(note).getByRole("link", { name: "마운트 현황" })).toHaveAttribute("href", "/admin/mounts");
});
