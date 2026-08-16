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
