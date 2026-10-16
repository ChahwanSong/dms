import { test, expect } from "vitest";
import { toolSummary } from "./jobTool";

test("단면 배치(dsync/dscan/drm)는 도구 + 총 노드 수", () => {
  expect(toolSummary({ tool: "dsync", worker_pool: { node_count: 2, process_count: 8 } }))
    .toBe("dsync · 2 노드");
});

test("양면 배치(nsync)는 소스·목적지 노드 수를 각각 말한다", () => {
  expect(toolSummary({ tool: "nsync", worker_pool: {
    source_count: 2, destination_count: 3, node_count: 5, process_count: 20 } }))
    .toBe("nsync · 소스 2 + 목적지 3 노드");
});

test("tool 이 null/부재면 null — 계획 전은 표시 자체를 생략한다", () => {
  expect(toolSummary({ tool: null, worker_pool: { node_count: 2 } })).toBeNull();
  expect(toolSummary({})).toBeNull();
  expect(toolSummary(undefined)).toBeNull();
});

test("worker_pool 이 없거나 수치가 아니면 도구 이름만 — 수치를 지어내지 않는다", () => {
  expect(toolSummary({ tool: "dscan" })).toBe("dscan");
  expect(toolSummary({ tool: "dscan", worker_pool: null })).toBe("dscan");
  expect(toolSummary({ tool: "dscan", worker_pool: { node_count: null } })).toBe("dscan");
});

test("노드 수 0 은 정상값이라 그대로 적는다(null≠0)", () => {
  expect(toolSummary({ tool: "dsync", worker_pool: { node_count: 0 } }))
    .toBe("dsync · 0 노드");
  expect(toolSummary({ tool: "nsync", worker_pool: { source_count: 1, destination_count: 0 } }))
    .toBe("nsync · 소스 1 + 목적지 0 노드");
});

test("한쪽 면 수치만 있으면 양면 문구를 만들지 않고 총 노드로 물러선다(반쪽 진실 금지)", () => {
  expect(toolSummary({ tool: "nsync", worker_pool: { source_count: 2, node_count: 4 } }))
    .toBe("nsync · 4 노드");
});

// ---- 노드 CPU 상한(2026-10-16): 워커는 프로세스 수만큼 CPU 코어를 요청하고 planner·stepper 가 노드가 담는 만큼으로 줄인다 ----

test("procs_per_node 가 있으면 노드당 프로세스를 붙인다", () => {
  expect(toolSummary({ tool: "dsync", worker_pool: { node_count: 4, process_count: 4,
    procs_per_node: 1, procs_per_node_requested: 1 } })).toBe("dsync · 4 노드 × 1 프로세스");
  expect(toolSummary({ tool: "nsync", worker_pool: { source_count: 2, destination_count: 3, node_count: 5,
    procs_per_node: 2, procs_per_node_requested: 2 } })).toBe("nsync · 소스 2 + 목적지 3 노드 × 2 프로세스");
});

test("노드 CPU 상한으로 줄었으면 요청값을 함께 말한다(조용히 줄이지 않는다)", () => {
  expect(toolSummary({ tool: "dsync", worker_pool: { node_count: 4, process_count: 4,
    procs_per_node: 1, procs_per_node_requested: 2 } })).toBe("dsync · 4 노드 × 1 프로세스 (요청 2 → 노드 CPU 상한 1)");
  expect(toolSummary({ tool: "nsync", worker_pool: { source_count: 1, destination_count: 1,
    procs_per_node: 3, procs_per_node_requested: 8 } }))
    .toBe("nsync · 소스 1 + 목적지 1 노드 × 3 프로세스 (요청 8 → 노드 CPU 상한 3)");
});

test("요청값을 모르면(키 없음·null·숫자 아님) 클램프 문구를 지어내지 않는다", () => {
  expect(toolSummary({ tool: "dscan", worker_pool: { node_count: 2, procs_per_node: 1 } }))
    .toBe("dscan · 2 노드 × 1 프로세스");
  expect(toolSummary({ tool: "dscan", worker_pool: { node_count: 2, procs_per_node: 1, procs_per_node_requested: null } }))
    .toBe("dscan · 2 노드 × 1 프로세스");
  expect(toolSummary({ tool: "dscan", worker_pool: { node_count: 2, procs_per_node: 1,
    procs_per_node_requested: "8" as unknown as number } })).toBe("dscan · 2 노드 × 1 프로세스");
});

test("이 기능 전에 계획된 잡(procs_per_node 없음)은 종전 그대로 — process_count 로 노드당 값을 지어내지 않는다", () => {
  expect(toolSummary({ tool: "dsync", worker_pool: { node_count: 2, process_count: 8 } })).toBe("dsync · 2 노드");
  expect(toolSummary({ tool: "dsync", worker_pool: { node_count: 2, procs_per_node: null } })).toBe("dsync · 2 노드");
  // 노드 수를 모르면 도구 이름만(노드당 값만 떠도는 문구를 만들지 않는다)
  expect(toolSummary({ tool: "dsync", worker_pool: { procs_per_node: 1 } })).toBe("dsync");
});
