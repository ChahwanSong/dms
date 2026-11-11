import { batchPillVariant, batchStatusLabel, buildPillVariant, isTerminal, jobPillVariant, pillVariant,
         storagePillVariant,
         REQUEST_TERMINAL_STATES, TERMINAL_STATES } from "./jobState";
import { test, expect } from "vitest";

test("terminal states", () => {
  ["Succeeded", "Failed", "TimedOut", "Rejected", "Cancelled", "PreviewExpired"]
    .forEach((s) => expect(isTerminal(s)).toBe(true));
  expect(isTerminal("Executing")).toBe(false);
  expect(TERMINAL_STATES.has("Succeeded")).toBe(true);
  // 요청 전용 셋: Conflict 는 요청에만 있는 종단이다(domain.TERMINAL_REQUEST_STATES).
  // 공유 TERMINAL_STATES 에 넣으면 잡 화면의 종단 판정까지 바뀌므로 분리(M5 관례).
  expect(REQUEST_TERMINAL_STATES.has("Conflict")).toBe(true);
  expect(REQUEST_TERMINAL_STATES.has("Succeeded")).toBe(true);
  expect(TERMINAL_STATES.has("Conflict")).toBe(false);  // 잡 셋은 불변(회귀 못)
});

test("pill variant mapping: green=ok, red=bad, violet=busy", () => {
  expect(pillVariant("Succeeded")).toBe("ok");
  expect(pillVariant("Failed")).toBe("bad");
  expect(pillVariant("Rejected")).toBe("bad");
  expect(pillVariant("Cancelled")).toBe("bad");
  // TimedOut 은 실패 종단이다(domain.TERMINAL_DATA_JOB_STATES) -- 빠져 있으면 neutral 회색으로 "진행 중" 처럼 읽혔다.
  expect(pillVariant("TimedOut")).toBe("bad");
  expect(pillVariant("Executing")).toBe("busy");
  expect(pillVariant("ConfirmPending")).toBe("busy");
  expect(pillVariant("Pending")).toBe("neutral");
});

test("pillVariant: Conflict=bad, 실제 진행 단계 Preflight·PreviewRunning=busy (2026-11-11)", () => {
  // Conflict 는 요청 전용 종단(겹쳐 실행 안 됨) -- Pending 과 같은 회색이면 「아직 대기」로 읽혔다(Rejected 와 같은 축).
  expect(pillVariant("Conflict")).toBe("bad");
  // 사전 점검·미리보기 중인 잡은 실제로 도는 중이다 -- 회색이면 대기처럼 보였다(requestOutcome 도 Preflight 를 busy 톤).
  expect(pillVariant("Preflight")).toBe("busy");
  expect(pillVariant("PreviewRunning")).toBe("busy");
  // domain 에 없는 옛 문자열은 특별 취급하지 않는다
  expect(pillVariant("Planning")).toBe("neutral");
  expect(pillVariant("Scheduled")).toBe("neutral");
  // 공유 매핑의 Planned·Running 은 기존대로 neutral(요청 배지 -- M5 테스트가 고정). 잡 Running 은 jobPillVariant 가 busy 로 칠한다.
  expect(pillVariant("Planned")).toBe("neutral");
  expect(pillVariant("Running")).toBe("neutral");
});

test("jobPillVariant: 잡 Running(scan 실행 단계)=busy, 공유 pillVariant 의 요청 Running 은 neutral 그대로 (2026-11-11)", () => {
  // scan 잡 배지가 Preflight(파랑) → Running(회색)으로 「대기로 돌아간」 것처럼 읽혔다 -- sync·rm 의 Executing 과 같은 busy.
  expect(jobPillVariant("Running")).toBe("busy");
  expect(jobPillVariant("Executing")).toBe("busy");
  expect(jobPillVariant("Preflight")).toBe("busy");
  // 나머지는 공유 매핑과 같다(Pending 은 진짜 대기라 neutral)
  for (const s of ["Pending", "Succeeded", "Failed", "TimedOut", "Cancelled", "PreviewExpired", "ConfirmPending"])
    expect(jobPillVariant(s)).toBe(pillVariant(s));
  expect(jobPillVariant("Pending")).toBe("neutral");
  // 공유 pillVariant(요청 배지)는 불변 -- 요청 Running 과 문자열이 겹친다(M5 관례)
  expect(pillVariant("Running")).toBe("neutral");
});

test("storagePillVariant: Ready=ok, Degraded=busy(주의), 그 외(Unknown 포함)=neutral", () => {
  expect(storagePillVariant("Ready")).toBe("ok");
  // Degraded 는 bad(적색)가 아니라 busy(황색 주의)다 -- planner 는 Degraded
  // 스토리지에도 잡을 보내므로(planner.py:149) "죽음"으로 칠하면 거짓말이 된다.
  expect(storagePillVariant("Degraded")).toBe("busy");
  expect(storagePillVariant("Unknown")).toBe("neutral");
  expect(storagePillVariant("NotAStatus")).toBe("neutral");
  // 공유 pillVariant 에 Ready 를 추가하는 잘못을 막는 못 -- 잡/요청 배지는 불변이다.
  expect(pillVariant("Ready")).toBe("neutral");
});

test("M5: buildPillVariant marks Pending/Running as busy without touching job pillVariant", () => {
  // 빌드 상태(Pending/Running/Succeeded/Failed)만 다루는 별도 함수다 -- 공유
  // pillVariant를 바꾸면 요청/잡 화면의 Pending/Running(위 테스트가 고정한 neutral)까지
  // 같이 바뀌므로 건드리지 않는다.
  expect(buildPillVariant("Pending")).toBe("busy");
  expect(buildPillVariant("Running")).toBe("busy");
  expect(buildPillVariant("Succeeded")).toBe("ok");
  expect(buildPillVariant("Failed")).toBe("bad");
  // 잡/요청 상태의 pillVariant는 그대로다(회귀 없음).
  expect(pillVariant("Pending")).toBe("neutral");
  expect(pillVariant("Running")).toBe("neutral");
});

test("M5: batchPillVariant — 배치 상태 전용 매핑, 공유 pillVariant 불변", () => {
  // 배치 상태(Running/Previewing/PreviewReady/Completed/Cancelled)는 잡/요청
  // 상태와 문자열이 겹친다(Running·Cancelled) — 공유 pillVariant 를 고치면
  // 잡/요청 배지까지 바뀌므로 별도 함수(buildPillVariant 와 같은 M5 관례).
  expect(batchPillVariant("Completed")).toBe("ok");
  expect(batchPillVariant("Running")).toBe("busy");
  expect(batchPillVariant("Previewing")).toBe("busy");
  // 확인 대기(PreviewReady)는 운영자 행동이 필요한 유일한 상태 -- busy(진행 중)와 구별되는 action(2026-10-07).
  expect(batchPillVariant("PreviewReady")).toBe("action");
  // Cancelled 는 운영자의 의도된 중지지 실패가 아니다 — bad(적색)는 "실패"라는
  // 거짓말이 된다(실패 수는 성공/실패 카운터가 따로 말한다). neutral.
  expect(batchPillVariant("Cancelled")).toBe("neutral");
  // 취소 처리 중(2026-10-16)은 아직 끝나지 않았다 -- busy.
  expect(batchPillVariant("Cancelling")).toBe("busy");
  expect(batchPillVariant("NotAStatus")).toBe("neutral");
  // 공유 pillVariant 는 불변(잡/요청 배지 회귀 없음) — 겹치는 문자열 재확인.
  expect(pillVariant("Running")).toBe("neutral");
  expect(pillVariant("Cancelled")).toBe("bad");
  expect(pillVariant("Completed")).toBe("neutral");
});

test("잔여 상태 매핑(슬라이스 1~4 부채): PreviewExpired=bad, Preflight/PreviewRunning=busy", () => {
  // PreviewExpired 는 isTerminal 로만 단언돼 있었고 배지색은 무그물이었다.
  // 2026-11-11: 옛 단언 「Planning/Scheduled=busy」는 domain(RequestState·DataJobState)에 없는 죽은 문자열을 고정하고 있었다 --
  // 실제 진행 단계 Preflight·PreviewRunning 으로 바꿨다(옛 문자열이 neutral 인 것은 위 Conflict 테스트가 본다).
  expect(pillVariant("PreviewExpired")).toBe("bad");
  expect(pillVariant("Preflight")).toBe("busy");
  expect(pillVariant("PreviewRunning")).toBe("busy");
});

test("batchStatusLabel — 확인 대기·취소 중만 한글 라벨, 나머지는 서버 상태 그대로", () => {
  expect(batchStatusLabel("PreviewReady")).toBe("확인 대기");
  expect(batchStatusLabel("Cancelling")).toBe("취소 중");
  for (const st of ["Previewing", "Running", "Completed", "Cancelled"]) expect(batchStatusLabel(st)).toBe(st);
});
