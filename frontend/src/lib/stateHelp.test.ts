// 단계·상태 설명 문구 규칙(2026-10-12). 키가 서버 상태 목록과 같은지는 백엔드 tests/test_state_help_contract.py 가 고정한다.
import { expect, test } from "vitest";
import { STATE_HELP, batchStateHelp, jobStateHelp, requestStateHelp, stageHelp } from "./stateHelp";

const STATE_NAMES = ["Pending", "Planned", "Running", "Succeeded", "Failed", "Rejected", "Cancelled", "Conflict",
  "Preflight", "PreviewRunning", "ConfirmPending", "Executing", "TimedOut", "PreviewExpired", "Previewing",
  "PreviewReady", "Completed", "Queued", "Materialized"];

const allTexts = (): string[] => Object.values(STATE_HELP).flatMap((t) => Object.values(t as Record<string, string>));

test("문구: 비어 있지 않은 한 줄, 길이 상한, 영문 상태명 없음(e2e 정확 일치·행 수렴 단언 보호)", () => {
  for (const text of allTexts()) {
    expect(text.trim().length).toBeGreaterThan(0);
    expect(text).not.toMatch(/\n/);
    expect(text.length).toBeLessThanOrEqual(110);
    for (const name of STATE_NAMES) expect(text).not.toMatch(new RegExp(`\\b${name}\\b`));
  }
});

test("모르는 상태는 null(설명을 지어내지 않는다), 배치 자식의 컨펌 대기는 배치 확인 문구", () => {
  expect(requestStateHelp("Weird")).toBeNull();
  expect(jobStateHelp("Weird")).toBeNull();
  expect(batchStateHelp("Weird")).toBeNull();
  expect(jobStateHelp("ConfirmPending")).toMatch(/「작업 컨펌」/);
  expect(jobStateHelp("ConfirmPending", { batchChild: true })).toMatch(/「배치 확인」/);
  expect(jobStateHelp("Executing", { batchChild: true })).toBe(jobStateHelp("Executing"));   // 나머지는 같다
});

test("단계 설명은 흐름을 따른다(scan 은 미리보기 언급 없음)", () => {
  expect(stageHelp("pre", "scan")).not.toMatch(/미리보기/);
  expect(stageHelp("pre", "previewed")).toMatch(/미리보기/);
  expect(stageHelp("exec", "scan")).toMatch(/사용량/);
  expect(stageHelp("gate", "previewed", { batchChild: true })).toMatch(/배치 확인/);
});
