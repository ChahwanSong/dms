import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { ArtifactBasePage } from "./ArtifactBasePage";

const server = setupServer();
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
  expect(screen.getByText("artifact_base_missing")).toBeInTheDocument();
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
