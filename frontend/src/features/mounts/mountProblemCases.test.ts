import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, test } from "vitest";
import { CELL_META, absentNowhere, cellHelp, cellProblem, cellView, columnFacts, columnProblem, filterColumns } from "./mountTerms";
import { column, row } from "./mountFixtures";
import type { CellState, ColumnSummary, MountCellData, MountRow, StorageColumn } from "./types";

// 서버 ↔ 포탈 교차(2026-11-27): mountProblemCases.json 의 expected 는 서버 build() 출력이다(tests/test_mount_status.py
// test_problem_rules_cross_fixture_matches_the_server 가 같은 파일로 고정). 여기서는 그 출력을 응답 모양으로 펴서, 거울(columnFacts·
// cellProblem·columnProblem)이 서버와 같은 problem_count·problem_columns 를 내는지 본다 -- 서버 규칙이 바뀌면 둘이 같이 빨개진다.

interface CaseCell { state: CellState; last: CellState | null; status: string | null; reason: string | null; is_mountpoint: boolean | null }
interface CaseNode { node_name: string; fresh: boolean; problem_count: number | null; cells: Record<string, CaseCell> }
interface Cases {
  expected: { problem_columns: string[]; nodes: CaseNode[]; storages: { storage_name: string; summary: ColumnSummary }[] };
}

const CASES = JSON.parse(readFileSync(join(dirname(fileURLToPath(import.meta.url)), "mountProblemCases.json"), "utf8")) as Cases;

const toCell = (c: CaseCell): MountCellData => ({
  state: c.state, last: c.last, pending: c.state === "pending" ? "not_reported" : null, status: c.status, reason: c.reason,
  reported_path: null, exists: null, is_mountpoint: c.is_mountpoint, readable: null, writable: null, disk: null,
});
const ROWS: MountRow[] = CASES.expected.nodes.map((n) => row(
  n.node_name, Object.fromEntries(Object.entries(n.cells).map(([k, c]) => [k, toCell(c)])),
  { fresh: n.fresh, problem: n.problem_count === null || n.problem_count > 0, problem_count: n.problem_count, shared_dir: null },
));
const COLS: StorageColumn[] = CASES.expected.storages.map((s) => ({ ...column(s.storage_name, `/${s.storage_name}`), summary: s.summary }));
const FACTS = columnFacts(ROWS);
const cellOf = (node: string, col: string) => ROWS.find((r) => r.node_name === node)!.cells[col];

describe("교차 픽스처(mountProblemCases.json) -- 서버 출력에 거울을 대 본다", () => {
  test("행마다 거울의 문제 칸 수 = 서버 problem_count(리포트 지연 행은 null)", () => {
    const mirror = ROWS.map((r) => (r.fresh
      ? Object.entries(r.cells).filter(([name, c]) => cellProblem(name, c, FACTS)).length : null));
    expect(mirror).toEqual(CASES.expected.nodes.map((n) => n.problem_count));
    // 픽스처가 공허하지 않다: 고치기 전 규칙(끊긴 마운트 = 없음, 없음은 늘 문제 아님)이면 0·2·1, 흔적을 신선한 정상·읽기 전용으로만
    // 세던 규칙(한 노드의 지연·끊김을 퍼뜨림)이면 5·6·6
    expect(mirror).toEqual([3, 5, 4, null]);
  });

  test("문제 있는 스토리지(columnProblem·filterColumns) = 서버 problem_columns", () => {
    expect(COLS.filter((c) => columnProblem(c, ROWS)).map((c) => c.storage_name)).toEqual(CASES.expected.problem_columns);
    expect(filterColumns(COLS, ROWS, { q: "", problemsOnly: true }).map((c) => c.storage_name))
      .toEqual(CASES.expected.problem_columns);
    // summary.broken 지름길 없이도 같다(칸 판정만으로 -- 열 요약이 빠진 응답에서도 거울이 맞다)
    const noBroken = COLS.map((c) => ({ ...c, summary: { ...c.summary, broken: 0 } }));
    expect(noBroken.filter((c) => columnProblem(c, ROWS)).map((c) => c.storage_name)).toEqual(CASES.expected.problem_columns);
  });

  test("끊긴 마운트 칸(broken + missing_mount_path + is_mountpoint true)은 「끊긴 마운트」 -- 「없음」이 아니다", () => {
    const dead = cellOf("n2", "dead");
    expect([dead.state, dead.reason, dead.is_mountpoint]).toEqual(["broken", "missing_mount_path", true]);
    const view = cellView(dead);
    expect(view.label).toBe("끊긴 마운트");
    expect(view.tone).toBe("bad");
    expect(view.Icon).not.toBe(CELL_META.absent.Icon);
    expect(cellHelp(dead, { mountPath: "/dead", ago: "방금" })).toMatch(/^끊긴 마운트 — 호스트에는 마운트가 있는데 경로에 접근할 수 없습니다/);
  });

  test("「없음」 표시는 열에 따라: 어디에도 흔적 없는 열은 경고 아이콘·빨강·낭독 뜻, 다른 노드에 흔적 있는 열은 회색 그대로", () => {
    const nowhere = cellOf("n1", "nowhere");
    expect(absentNowhere("nowhere", nowhere, FACTS)).toBe(true);
    expect(cellView(nowhere, true)).toMatchObject({ label: "없음", tone: "bad", aria: "없음(어느 노드에도 마운트 흔적 없음)" });
    expect(cellView(nowhere, true).Icon).not.toBe(CELL_META.absent.Icon);      // 색만으로 구분하지 않는다
    for (const [node, col] of [["n2", "split"], ["n2", "ro-absent"], ["n2", "legacy-absent"], ["n1", "stale-only"],
                               ["n3", "dead-only"]] as const) {
      const cell = cellOf(node, col);
      expect(absentNowhere(col, cell, FACTS), `${node}/${col}`).toBe(false);
      expect(cellView(cell, false)).toMatchObject({ label: "없음", tone: "muted", aria: "없음" });
    }
    // 흔적의 범위(서버 _attached): 지연 노드의 마지막 값 정상·끊긴 마운트는 흔적, 지연 노드의 마지막 값 없음·점검 불가는 아니다
    expect(cellOf("n4", "stale-only")).toMatchObject({ state: "stale", last: "ready" });
    expect(cellOf("n2", "dead-only")).toMatchObject({ state: "broken", reason: "missing_mount_path", is_mountpoint: true });
    expect(cellOf("n4", "nowhere")).toMatchObject({ state: "stale", last: "absent" });
    expect(cellOf("n2", "hrm-absent")).toMatchObject({ state: "broken", reason: "host_root_missing" });
    expect(["stale-only", "dead-only", "nowhere", "hrm-absent"].map((c) => FACTS.mounted.has(c))).toEqual([true, true, false, false]);
    expect(absentNowhere("hrm-absent", cellOf("n1", "hrm-absent"), FACTS)).toBe(true);
  });
});
