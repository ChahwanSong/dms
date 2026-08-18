import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
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
  return render(<QueryClientProvider client={qc}><ArtifactBasePage /></QueryClientProvider>);
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
