import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, test } from "vitest";
import contract from "../../lib/progressContract.json";
import { ApiError, reasonText } from "../../lib/api";
import type { JobProgressResponse, ProgressSnapshot, ToolSummary } from "../../lib/types";
import {
  BASIS, CAPTION, ENDED_TITLE, NOTE, PHASE_TITLE, STALE_AFTER_S, UNKNOWN_PHASE_TITLE, WAIT_GRACE_S, nextHeld, pctFmt,
  progressView, quietLineText, showsProgress, staleText, type HeldProgress, type ProgressJobInfo, type ProgressStepInfo,
  type ProgressView,
} from "./progressModel";
import { STATUS_LABEL } from "./stageModel";

// 예 스냅숏·응답은 계약 파일(lib/progressContract.json)에서 그대로 읽는다 -- 손으로 쓴 모의 모양 금지(설계 §11.2). 러너·API 가 같은
// 파일을 시험 원천으로 삼으므로, 계약이 바뀌면 양쪽이 함께 빨개진다.
const SNAP: Record<string, ProgressSnapshot> = contract.examples.snapshots;
const RESP = contract.examples.responses;

const T0 = Date.parse("2026-11-26T08:40:00Z");          // 응답을 받은 시각(dataUpdatedAt)
const EXEC: ProgressStepInfo = { id: "execution", start: "2026-11-26T08:28:56Z", schedWaitSec: 2 };
const PREV: ProgressStepInfo = { id: "preview", start: "2026-11-26T08:28:56Z", schedWaitSec: null };
const ok = (snap: ProgressSnapshot, age = 0): JobProgressResponse =>
  ({ phase: snap.dryrun === true ? "preview" : "execution", available: true, age_seconds: age, progress: snap });
function view(resp: JobProgressResponse | undefined, o: {
  job?: ProgressJobInfo; step?: ProgressStepInfo; after?: number; error?: unknown;
} = {}): ProgressView {
  return progressView(resp, o.error ?? null, o.job ?? {}, o.step ?? EXEC, T0 + (o.after ?? 0) * 1000, T0);
}
const strings = (v: ProgressView): string[] =>
  [v.title, v.basis, v.basisHelp, v.fresh, v.caption, v.note?.text, v.percent !== null ? pctFmt(v.percent) : null, ...v.facts]
    .filter((x): x is string => typeof x === "string");
const nsyncPlan: ToolSummary = { tool: "nsync", dryrun: true, files: 100105, planned: { copy: 100002, mkdir: 103 },
  planned_bytes: 1482824499 };
const dsyncPlan: ToolSummary = { tool: "dsync", dryrun: true, files: 100105, source_items: 100105 };

describe("단계 제목", () => {
  test("계약 snapshot.phases 전부에 제목이 있다(미지 문구로 떨어지는 알려진 phase 0) -- 계약 밖 제목도 없다", () => {
    for (const p of contract.snapshot.phases) {
      const v = view(ok({ v: 1, tool: contract.snapshot.phases_by_tool.nsync.includes(p) ? "nsync" : "dsync", phase: p }));
      expect(v.title, p).toBe(PHASE_TITLE[p]);
      expect(v.title, p).not.toBe(UNKNOWN_PHASE_TITLE);
    }
    expect(Object.keys(PHASE_TITLE).sort()).toEqual([...contract.snapshot.phases].sort());
  });

  test("미지 phase 는 「진행 단계 알 수 없음」 -- Object.prototype 이름(constructor 등)도 자기 키로만 찾는다", () => {
    for (const p of ["zzz", "constructor", "tostring", "hasownproperty", "__proto__", "", "valueof"]) {
      expect(view(ok({ v: 1, tool: "dsync", phase: p })).title, p).toBe(UNKNOWN_PHASE_TITLE);
    }
  });

  test("어떤 제목도 단계 배지·상태 라벨(STATUS_LABEL)과 같지 않다 -- e2e 정확 일치 단언과 겹치지 않게", () => {
    const labels = new Set(Object.values(STATUS_LABEL));
    const titles = [...Object.values(PHASE_TITLE), ENDED_TITLE, UNKNOWN_PHASE_TITLE, "배치 계획", "진행"];
    for (const t of titles) expect(labels.has(t), t).toBe(false);
  });

  test("미리보기 batches 는 「배치 계획」, 실행은 「배치 실행」, 끝 스냅숏은 phase 와 무관하게 「도구 종료 · 결과 정리 중」", () => {
    expect(view(ok(SNAP.nsync_batches_dry), { step: PREV }).title).toBe("배치 계획");
    expect(view(ok(SNAP.nsync_batches_exec)).title).toBe("배치 실행");
    expect(view(ok(SNAP.dsync_ended)).title).toBe(ENDED_TITLE);
    expect(view(ok({ ...SNAP.nsync_batches_exec, ended: true })).title).toBe(ENDED_TITLE);
  });
});

describe("계약 예 스냅숏 전부", () => {
  test("workers -- 워커 대기 안내, 막대 없음", () => {
    const v = view(ok(SNAP.workers));
    expect(v).toMatchObject({ title: "워커 준비 대기", percent: null, basis: null, facts: [],
      note: { tone: "info", text: NOTE.workers } });
  });

  test("dsync_walk_src(미리보기 행) -- 탐색 수만, 분모가 없어 막대 없음", () => {
    const v = view(ok(SNAP.dsync_walk_src), { step: PREV });
    expect(v).toMatchObject({ title: "소스 탐색", percent: null, facts: ["탐색 100,105 항목"], note: null });
  });

  test("dsync_create -- 단계 % 막대(현재 단계 기준), 생성 수 / 단계 머리 줄의 N, 끝난 탐색 수는 뒤에 보조로", () => {
    const v = view(ok(SNAP.dsync_create));
    expect(v).toMatchObject({ title: "파일 생성", percent: 38, basis: BASIS.step,
      facts: ["생성 38,308 / 100,002 항목", "소스 탐색 100,105 항목", "목적지 탐색 0 항목"] });
  });

  test("dsync_copy_overall -- 전체 항목 막대가 우선이고 단계 % 는 사실 줄로(배치 모드라 복사량은 「이번 배치」)", () => {
    const v = view(ok(SNAP.dsync_copy_overall));
    expect(v.title).toBe("데이터 복사");
    expect(v.percent).toBe(39.958);
    expect(v.basis).toBe(BASIS.overall);
    expect(v.facts).toEqual(["전체 40,000 / 100,105 항목", "현재 단계 73%", "이번 배치 복사 약 1.0 GiB", "평균 51.3 MiB/s",
      "소스 탐색 100,105 항목", "목적지 탐색 0 항목"]);
    expect(pctFmt(v.percent!)).toBe("39.9%");      // 버림 -- 반올림해 끝난 것처럼 보이지 않게
  });

  test("dsync_ended -- 끝 제목·전체 100%·경고 없음(나이가 많아도), 전체 수가 먼저이고 탐색 수는 보조", () => {
    const v = view(ok(SNAP.dsync_ended, 400));
    expect(v).toMatchObject({ title: ENDED_TITLE, percent: 100, basis: BASIS.overall, note: null,
      facts: ["전체 100,105 / 100,105 항목", "소스 탐색 100,105 항목", "목적지 탐색 0 항목"] });
  });

  test("dsync 미리보기 끝 스냅숏 -- 도구가 약 2.4초라 화면이 받는 유일한 스냅숏이어도 제목만이 아니라 탐색 수를 보인다", () => {
    // 2026-11-27 dms-k8s d50 실증 F2(p-el-06): 끝 스냅숏의 walk 를 읽지 않아 「진행 · 도구 종료 · 결과 정리 중」 제목만 남았다. 아래는 그
    // 잡의 preview/progress.json 그대로다.
    const end: ProgressSnapshot = { v: 1, tool: "dsync", dryrun: true, quiet: false, phase: "done", phase_since_s: 0.1,
      elapsed_s: 2.4, since_line_s: 0.1, lines: 6, ended: true, walk: { src_items: 50105, dst_items: 0 } };
    expect(view(ok(end, 1), { step: PREV, job: { tool: "dsync" } })).toMatchObject({ title: ENDED_TITLE, percent: null,
      note: null, facts: ["소스 탐색 50,105 항목", "목적지 탐색 0 항목"] });
  });

  test("nsync_scan -- 스캔 중엔 막대·수치 없음", () => {
    const v = view(ok(SNAP.nsync_scan));
    expect(v).toMatchObject({ title: "스캔·배치 준비", percent: null, facts: [], note: null });
  });

  test("nsync_batches_exec -- 배치 기준 막대, 복사 파일·데이터·평균(미리보기 계획이 있으면 곁에)", () => {
    expect(view(ok(SNAP.nsync_batches_exec))).toMatchObject({ percent: 16.7, basis: BASIS.batch,
      facts: ["끝난 배치 1 / 6", "복사 파일 16,548", "데이터 약 576.6 MiB", "평균 510.4 파일/s"] });
    expect(view(ok(SNAP.nsync_batches_exec), { job: { tool: "nsync", preview_summary: nsyncPlan } }).facts).toEqual([
      "끝난 배치 1 / 6", "복사 파일 16,548 · 미리보기 계획 100,002", "데이터 약 576.6 MiB · 미리보기 계획 1.4 GiB",
      "평균 510.4 파일/s"]);
  });

  test("nsync_batches_dry -- 계획 동작·복사 계획·데이터(planned_* -- 끝난 배치까지 누적이라 「지금까지」)", () => {
    const v = view(ok(SNAP.nsync_batches_dry), { step: PREV });
    expect(v).toMatchObject({ percent: 50, basis: BASIS.batch,
      facts: ["끝난 배치 3 / 6", "지금까지 계획 동작 49,985", "지금까지 복사 계획 49,940 파일", "지금까지 계획 데이터 약 1.2 GiB"] });
  });

  test("quiet_start -- quiet 안내가 먼저(정체 경고보다)", () => {
    const v = view(ok(SNAP.quiet_start, 200));
    expect(v).toMatchObject({ title: "도구 시작", percent: null, facts: [], note: { tone: "info", text: NOTE.quiet } });
  });

  test("계약 응답 예(available·available_preview) 그대로 -- 막대·제목", () => {
    expect(view(RESP.available).percent).toBe(39.958);
    expect(view(RESP.available_preview, { step: PREV }).title).toBe("배치 계획");
    expect(view(RESP.available).fresh).toBe("갱신 4초 전");
  });

  test("모든 예·나이·문맥에서 「남은」·「left」가 어떤 출력에도 없다(계약 ui_forbidden_substrings)", () => {
    const outs: string[] = [];
    const jobs: ProgressJobInfo[] = [{}, { tool: "nsync", preview_summary: nsyncPlan }, { tool: "dsync", preview_summary: dsyncPlan }];
    for (const snap of Object.values(SNAP)) {
      for (const age of [0, 45, 89, 91, 400, 4000]) {
        for (const job of jobs) for (const step of [EXEC, PREV]) outs.push(...strings(view(ok(snap, age), { job, step })));
      }
    }
    for (const after of [0, 30, 61, 600]) {
      for (const step of [EXEC, PREV, { ...EXEC, schedWaitSec: null }]) {
        outs.push(...strings(view(RESP.unavailable, { step, after })), ...strings(view(undefined, { step, after })));
      }
    }
    for (const e of RESP.errors) outs.push(...strings(view(undefined, { error: new ApiError(e.status, e.body.detail, "") })));
    outs.push(...Object.values(NOTE), ...Object.values(CAPTION), staleText("2분"), quietLineText("2분"));
    expect(outs.length).toBeGreaterThan(100);
    for (const s of outs) for (const bad of contract.ui_forbidden_substrings) expect(s, s).not.toContain(bad);
  });
});

describe("막대 우선순위(도구가 준 값만)", () => {
  const dsync = (over: ProgressSnapshot): ProgressSnapshot => ({ v: 1, tool: "dsync", dryrun: false, phase: "copy", ...over });

  test("1) nsync 배치 % -- 도구 % 가 없으면(Progress logging enabled 직후 done 0) 끝난 배치 / 배치 수", () => {
    expect(view(ok(SNAP.nsync_batches_exec)).percent).toBe(16.7);
    const on = view(ok({ v: 1, tool: "nsync", phase: "batches", batch: { done: 0, count: 6 } }));
    expect(on).toMatchObject({ percent: 0, basis: BASIS.batch, facts: ["끝난 배치 0 / 6"] });
    // 배치가 1개면 끝날 때까지 0% 막대가 멈춘 것처럼 보인다 -- 도구 % 가 없는 동안은 막대 없이 수치·안내만, 끝나면 100%
    const single = view(ok({ v: 1, tool: "nsync", phase: "batches", batch: { done: 0, count: 1 } }));
    expect(single).toMatchObject({ percent: null, basis: null, facts: ["끝난 배치 0 / 1"],
      note: { tone: "info", text: NOTE.nsyncSingleBatch } });
    expect(view(ok({ v: 1, tool: "nsync", phase: "batches", batch: { done: 1, count: 1 } })).percent).toBe(100);
    expect(view(ok({ v: 1, tool: "nsync", phase: "batches", batch: { done: 1, count: 1, percent: 100 } })).percent).toBe(100);
    // 배치 수가 0·모름이거나 끝난 수가 배치 수보다 크면(이상값) 막대를 만들지 않는다
    expect(view(ok({ v: 1, tool: "nsync", phase: "batches", batch: { done: 0, count: 0 } })).percent).toBeNull();
    expect(view(ok({ v: 1, tool: "nsync", phase: "batches", batch: { done: 0 } })).percent).toBeNull();
    expect(view(ok({ v: 1, tool: "nsync", phase: "batches", batch: { done: 7, count: 6 } })).percent).toBeNull();
  });

  test("2) dsync 전체 항목이 3) 단계 % 보다 먼저", () => {
    const v = view(ok(dsync({ step: { percent: 73 }, overall: { items: 1, total: 4, percent: 25 } })));
    expect(v).toMatchObject({ percent: 25, basis: BASIS.overall });
    expect(v.facts).toContain("현재 단계 73%");
  });

  test("3) dsync 단계 % 는 % 를 찍는 단계에서만 -- 복사는 바이트·배치 안이라는 설명이 붙는다", () => {
    for (const phase of ["create", "mkdir", "copy", "compare_contents", "delete"]) {
      expect(view(ok(dsync({ phase, step: { percent: 40 } }))).basis, phase).toBe(BASIS.step);
    }
    expect(view(ok(dsync({ phase: "copy", step: { percent: 40 } }))).basisHelp).toMatch(/바이트 기준/);
    expect(view(ok(dsync({ phase: "create", step: { percent: 40 } }))).basisHelp).not.toMatch(/바이트/);
    // walk·metadata 의 step 에는 % 가 없다 -- 있어도(위조·이상값) 막대로 쓰지 않는다
    expect(view(ok(dsync({ phase: "metadata", step: { percent: 40, items: 5 } }))).percent).toBeNull();
  });

  test("3') 도구가 반올림한 「100%」는 그 단계에 done 이 찍히기 전엔 99 -- 아직 도는 단계를 끝난 막대로 보이지 않게", () => {
    // 실측(dsync 실행 로그 151~154행): 1.376 / 1.381 GiB(99.6%)에 「(100%) … 5 secs left」, 27초 뒤에야 「done」
    for (const phase of ["create", "mkdir", "copy", "compare_contents", "delete"]) {
      expect(view(ok(dsync({ phase, step: { percent: 100, done: false } }))).percent, phase).toBe(99);
      expect(view(ok(dsync({ phase, step: { percent: 100 } }))).percent, phase).toBe(99);       // done 모름도 묶는다
      expect(view(ok(dsync({ phase, step: { percent: 100, done: true } }))).percent, phase).toBe(100);
    }
    expect(pctFmt(view(ok(dsync({ step: { percent: 100, done: false } }))).percent!)).toBe("99%");
    expect(view(ok(dsync({ step: { percent: 99.5, done: false } }))).percent).toBe(99.5);       // 100 미만은 그대로
    // 전체 막대일 때 사실 줄 「현재 단계」도 같은 값
    const both = view(ok(dsync({ step: { percent: 100, done: false }, overall: { items: 1, total: 4, percent: 25 } })));
    expect(both.facts).toContain("현재 단계 99%");
    const fin = view(ok(dsync({ step: { percent: 100, done: true }, overall: { items: 1, total: 4, percent: 25 } })));
    expect(fin.facts).toContain("현재 단계 100%");
  });

  test("4) dsync 소스 탐색 추정 -- 실행 행 ∧ 미리보기 source_items > 0, 99% 에서 멈춘다", () => {
    const walk = (items: number) => ok(dsync({ phase: "walk_src", step: { items } }));
    const job = { tool: "dsync", preview_summary: dsyncPlan };
    expect(view(walk(50000), { job })).toMatchObject({ basis: BASIS.walkEstimate,
      facts: ["탐색 50,000 항목", "미리보기 소스 100,105 항목"] });
    expect(view(walk(50000), { job }).percent).toBeCloseTo(49.947, 2);
    expect(view(walk(200000), { job }).percent).toBe(99);                 // 소스가 늘었어도 100% 를 지어내지 않는다
    expect(view(walk(50000), { job, step: PREV }).percent).toBeNull();     // 미리보기 행 자신은 분모가 없다
    expect(view(walk(50000), { job: { preview_summary: { ...dsyncPlan, source_items: 0 } } }).percent).toBeNull();
    expect(view(walk(50000), { job: { preview_summary: null } }).percent).toBeNull();
    // walk_dst 는 소스 분모가 아니다
    expect(view(ok(dsync({ phase: "walk_dst", step: { items: 10 } })), { job }).percent).toBeNull();
  });

  test("5) 그 밖은 막대 없음(수치만) -- 범위 밖·비유한·문자열 % 도 막대로 쓰지 않는다", () => {
    expect(view(ok(dsync({ phase: "copy", step: { bytes: 5 } }))).percent).toBeNull();
    for (const bad of [101, -1, Number.NaN, Number.POSITIVE_INFINITY, "40" as unknown as number]) {
      expect(view(ok(dsync({ phase: "copy", step: { percent: bad } }))).percent).toBeNull();
      expect(view(ok(dsync({ overall: { percent: bad } }))).percent).toBeNull();
    }
    expect(view(ok({ v: 1, tool: "dscan", phase: "copy", step: { percent: 40 } })).percent).toBeNull();
  });
});

describe("null 과 0", () => {
  test("bytes 없음 → 데이터 조각 없음, items 0 → 「0 항목」(정상값)", () => {
    const d = (step: ProgressSnapshot["step"], phase = "copy") => view(ok({ v: 1, tool: "dsync", phase, step })).facts;
    expect(d({ percent: 10 })).toEqual([]);
    expect(d({ bytes: 0, rate_bps: 0 })).toEqual(["복사 약 0 B", "평균 0 B/s"]);
    expect(d({ items: 0, total: 0 }, "create")).toEqual(["생성 0 / 0 항목"]);
    expect(d({ items: 0 }, "metadata")).toEqual(["적용 0 항목"]);
    expect(d({ items: 0 }, "walk_dst")).toEqual(["탐색 0 항목"]);
    expect(d({ total: 7 }, "create")).toEqual(["대상 7 항목"]);
  });

  test("배치 모드(전체 블록이 있다)의 생성·복사 수치는 배치 안 값이라 「이번 배치」를 붙인다 -- 없으면(단일 실행) 붙이지 않는다", () => {
    const overall = { items: 250, total: 1001, percent: 24.975 };
    const d = (step: ProgressSnapshot["step"], phase: string) =>
      view(ok({ v: 1, tool: "dsync", phase, step, overall })).facts;
    expect(d({ items: 38, total: 250 }, "create")).toEqual(["전체 250 / 1,001 항목", "이번 배치 생성 38 / 250 항목"]);
    expect(d({ total: 1 }, "mkdir")).toEqual(["전체 250 / 1,001 항목", "이번 배치 대상 1 디렉토리"]);
    expect(d({ bytes: 1024 }, "copy")).toEqual(["전체 250 / 1,001 항목", "이번 배치 복사 약 1.0 KiB"]);
    // 전체 블록이 없으면 단계 수치가 곧 전체다
    expect(view(ok({ v: 1, tool: "dsync", phase: "create", step: { items: 38, total: 250 } })).facts).toEqual(["생성 38 / 250 항목"]);
  });

  test("미리보기 계획 0 은 「미리보기 계획 0」(비율 없음), 요약 null·planned_bytes 없음 → 계획 조각 없음", () => {
    const zero = view(ok(SNAP.nsync_batches_exec), { job: { preview_summary: { planned: { copy: 0 } } } }).facts;
    expect(zero).toContain("복사 파일 16,548 · 미리보기 계획 0");
    expect(zero).toContain("데이터 약 576.6 MiB");                          // planned_bytes 없음 → 0 B 로 그리지 않는다
    const none = view(ok(SNAP.nsync_batches_exec), { job: { preview_summary: null } }).facts;
    expect(none.join(" ")).not.toContain("미리보기 계획");
    // DB 가 신뢰 경계 -- 요약이 객체가 아니어도 죽지 않고 계획 조각만 뺀다
    const junk = view(ok(SNAP.nsync_batches_exec), { job: { preview_summary: "x" as unknown as ToolSummary } }).facts;
    expect(junk.join(" ")).not.toContain("미리보기 계획");
  });

  test("dsync 탐색 완료 수(walk)는 어느 단계에서든 키가 있을 때만 -- 0 은 「0 항목」, 그 쪽 완료 수가 있으면 탐색 중 수는 접는다", () => {
    const d = (over: ProgressSnapshot) => view(ok({ v: 1, tool: "dsync", phase: "done", ended: true, ...over })).facts;
    expect(d({})).toEqual([]);
    expect(d({ walk: {} })).toEqual([]);
    expect(d({ walk: { src_items: 0 } })).toEqual(["소스 탐색 0 항목"]);
    expect(d({ walk: { dst_items: 7 } })).toEqual(["목적지 탐색 7 항목"]);
    expect(d({ walk: { src_items: "5", dst_items: -1 } as unknown as ProgressSnapshot["walk"] })).toEqual([]);
    expect(d({ walk: [] as unknown as ProgressSnapshot["walk"] })).toEqual([]);
    for (const phase of ["copy", "metadata", "timestamps", "zzz"]) {
      expect(view(ok({ v: 1, tool: "dsync", phase, walk: { src_items: 3 } })).facts, phase).toContain("소스 탐색 3 항목");
    }
    // 소스 탐색 완료 줄 뒤에도 다음 머리 줄까지 단계는 walk_src 이고 step.items 는 마지막 진행 줄(완료 수보다 작다) -- 완료 수만
    expect(view(ok({ v: 1, tool: "dsync", phase: "walk_src", step: { items: 49000 }, walk: { src_items: 50105 } })).facts)
      .toEqual(["소스 탐색 50,105 항목"]);
    // 같은 창의 실행 행(미리보기 source_items 있음 -- 추정 막대): 막대도 접힌 진행 수(49,000)가 아니라 완료 수로 잰다 -- 같은 수 둘
    // 곁에 97.7% 막대가 서지 않는다(같으면 99% 에서 멈춘다). 완료 수 0 도 정상 분자다(null≠0 -- 진행 수로 갈음하지 않는다).
    const est = { tool: "dsync", preview_summary: { ...dsyncPlan, source_items: 50105 } };
    expect(view(ok({ v: 1, tool: "dsync", phase: "walk_src", step: { items: 49000 }, walk: { src_items: 50105 } }), { job: est }))
      .toMatchObject({ percent: 99, basis: BASIS.walkEstimate, facts: ["미리보기 소스 50,105 항목", "소스 탐색 50,105 항목"] });
    expect(view(ok({ v: 1, tool: "dsync", phase: "walk_src", step: { items: 49000 }, walk: { src_items: 25000 } }), { job: est })
      .percent).toBeCloseTo(49.895, 2);
    expect(view(ok({ v: 1, tool: "dsync", phase: "walk_src", step: { items: 49000 }, walk: { src_items: 0 } }), { job: est })
      .percent).toBe(0);
    // 완료 수가 없으면(모양이 틀린 값 포함) 진행 수가 분자 그대로
    expect(view(ok({ v: 1, tool: "dsync", phase: "walk_src", step: { items: 49000 },
      walk: { src_items: "x" } as unknown as ProgressSnapshot["walk"] }), { job: est }).percent).toBeCloseTo(97.794, 2);
    // 목적지 탐색 중엔 그 진행 수가 먼저, 끝난 소스 탐색 수가 뒤
    expect(view(ok({ v: 1, tool: "dsync", phase: "walk_dst", step: { items: 120 }, walk: { src_items: 50105 } })).facts)
      .toEqual(["탐색 120 항목", "소스 탐색 50,105 항목"]);
    // nsync 의 스캔 수는 scan 이 말한다 -- walk 를 읽지 않는다
    expect(view(ok({ v: 1, tool: "nsync", phase: "finishing", walk: { src_items: 3 } })).facts).toEqual([]);
  });

  test("하위 객체가 객체가 아니거나 비면 그 조각만 빠진다", () => {
    const v = view(ok({ v: 1, tool: "nsync", phase: "batches", batch: [] as unknown as ProgressSnapshot["batch"] }));
    expect(v).toMatchObject({ percent: null, facts: [] });
    expect(view(ok({ v: 1, tool: "dsync", phase: "copy", step: {} })).facts).toEqual([]);
  });
});

describe("신선도와 안내", () => {
  test("신선도 = 서버 나이 + 받은 뒤 흐른 시간 -- 90초까지는 경고 없음, 넘으면 attn", () => {
    const snap = { ...SNAP.dsync_create, since_line_s: 0 };
    expect(view(ok(snap, 10), { after: 20 }).fresh).toBe("갱신 30초 전");
    expect(view(ok(snap, 30), { after: STALE_AFTER_S - 30 }).note?.tone).not.toBe("attn");
    const stale = view(ok(snap, 30), { after: STALE_AFTER_S - 29 });
    expect(stale.note).toEqual({ tone: "attn", text: staleText("1분 31초") });
    // 브라우저 시계가 거꾸로 가도(받은 시각보다 앞) 음수 나이를 만들지 않는다
    expect(view(ok(snap, 5), { after: -100 }).fresh).toBe("갱신 5초 전");
  });

  test("age_seconds 가 없으면(모름) 0 으로 치지 않는다 -- 신선도 없음, 나이에 기대는 안내(정체·조용한 줄·배치마다)도 없음", () => {
    const noAge = (snap: ProgressSnapshot) =>
      ({ phase: "execution", available: true, progress: snap }) as unknown as JobProgressResponse;
    const quiet = view(noAge({ ...SNAP.dsync_copy_overall, since_line_s: 500 }), { after: 1000 });
    expect(quiet.fresh).toBeNull();
    expect(quiet.note).toBeNull();
    expect(quiet.percent).toBe(39.958);                                        // 스냅숏 자체는 그대로 그린다
    expect(view(noAge({ ...SNAP.nsync_batches_exec, since_line_s: 500 }), { after: 1000 }).note).toBeNull();
    expect(view(noAge({ v: 1, tool: "dsync", phase: "start", lines: 0 }), { after: 1000 }).note).toBeNull();
    // 나이와 무관한 안내는 그대로
    expect(view(noAge({ v: 1, tool: "dsync", phase: "compare" })).note).toEqual({ tone: "info", text: NOTE.dsyncSilentPhase });
    expect(view(noAge(SNAP.quiet_start)).note).toEqual({ tone: "info", text: NOTE.quiet });
  });

  test("정체 문턱은 러너 심장박동(dms_job_runner/progress.py HEARTBEAT_SECONDS)의 3배 이상 -- 심장박동을 늘리면 여기서 빨개진다", () => {
    // 계약 파일(progressContract.json)은 계약 리드만 고치므로 러너 소스의 상수를 직접 읽는다(읽기 전용 -- 값이 바뀌면 이 문턱을 같이 본다).
    const here = dirname(fileURLToPath(import.meta.url));
    const src = readFileSync(join(here, "../../../../src/dms_job_runner/progress.py"), "utf8");
    const m = /^HEARTBEAT_SECONDS\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*(?:#.*)?$/m.exec(src);
    expect(m, "러너 progress.py 에서 HEARTBEAT_SECONDS 를 찾지 못했다").not.toBeNull();
    const heartbeat = Number(m![1]);
    expect(heartbeat).toBeGreaterThan(0);
    expect(STALE_AFTER_S).toBeGreaterThanOrEqual(3 * heartbeat);
  });

  test("workers 에서 ageNow 200초 → 정체 경고 없음 + 워커 대기 안내(이 단계엔 심장박동이 없다)", () => {
    const v = view(ok(SNAP.workers, 150), { after: 50 });
    expect(v.note).toEqual({ tone: "info", text: NOTE.workers });
  });

  test("ended → 정체 경고 없음(러너가 더 쓰지 않는다)", () => {
    expect(view(ok({ ...SNAP.dsync_create, ended: true }, 500)).note).toBeNull();
  });

  test("start(mpirun 뒤)는 심장박동이 있어 정체 경고가 맞는 신호다", () => {
    expect(view(ok({ v: 1, tool: "dsync", phase: "start", lines: 0 }, 120)).note?.tone).toBe("attn");
  });

  test("nsync 배치 1개 안내 -- 끝날 때 한 번 표시", () => {
    const one = { v: 1, tool: "nsync", phase: "batches", since_line_s: 0.2, batch: { done: 0, count: 1 } };
    expect(view(ok(one)).note).toEqual({ tone: "info", text: NOTE.nsyncSingleBatch });
    expect(view(ok({ ...one, batch: { done: 1, count: 1, percent: 100 } })).note).toBeNull();
  });

  test("nsync 배치 여럿 -- 「nsync 는 배치가 끝날 때마다 갱신됩니다」는 나이와 무관하게 늘 캡션(안내 note 가 아니다)", () => {
    // 2026-11-27 dms-k8s d49 실증: 마지막 줄 뒤 30초가 넘을 때만 안내로 뜨던 때는 배치 3.5~8.6초(N1)에선 한 번도, 배치 약 30초(N1b·N1c)에선
    // 1초 안에 지워져 사실상 안 보였다.
    expect(CAPTION.nsyncPerBatch).toBe("nsync 는 배치가 끝날 때마다 갱신됩니다");
    for (const [since, age] of [[0, 0], [0.4, 0], [20, 5], [20, 11], [60, 25]]) {
      const v = view(ok({ ...SNAP.nsync_batches_exec, since_line_s: since }, age));
      expect(v.caption, `since ${since} age ${age}`).toBe(CAPTION.nsyncPerBatch);
      expect(v.note, `since ${since} age ${age}`).toBeNull();
    }
    // 미리보기(배치 계획)도 같다
    expect(view(ok(SNAP.nsync_batches_dry), { step: PREV }).caption).toBe(CAPTION.nsyncPerBatch);
    // 배치 수를 아는 순간(스캔 끝 Batch mode 줄)부터 -- 스캔 단계·배치 진행 켜짐(done 0) 직후에도
    expect(view(ok({ v: 1, tool: "nsync", phase: "scan", batch: { count: 6 } })).caption).toBe(CAPTION.nsyncPerBatch);
    expect(view(ok({ v: 1, tool: "nsync", phase: "batches", batch: { done: 0, count: 2 } })).caption).toBe(CAPTION.nsyncPerBatch);
  });

  test("nsync 캡션이 없는 경우: 배치 1개(따로 안내)·배치 수 모름·끝 스냅숏·배치 뒤 단계·dsync·스냅숏 없음", () => {
    const single = view(ok({ v: 1, tool: "nsync", phase: "batches", since_line_s: 1, batch: { done: 0, count: 1 } }));
    expect(single.caption).toBeNull();
    expect(single.note).toEqual({ tone: "info", text: NOTE.nsyncSingleBatch });
    expect(view(ok(SNAP.nsync_scan)).caption).toBeNull();                                      // 배치 수를 아직 모른다
    expect(view(ok({ ...SNAP.nsync_batches_exec, ended: true })).caption).toBeNull();          // 더 갱신되지 않는다
    // 러너는 batch 를 단계가 바뀌어도 지우지 않는다 -- 모든 배치가 끝난 뒤(finishing·ended 아닌 done)엔 끝날 배치가 더 없다
    for (const phase of ["finishing", "done"]) {
      expect(view(ok({ v: 1, tool: "nsync", phase, batch: { done: 6, count: 6 } })).caption, phase).toBeNull();
    }
    expect(view(ok({ ...SNAP.nsync_batches_exec, batch: { done: 1, count: "6" } } as unknown as ProgressSnapshot)).caption).toBeNull();
    for (const snap of [SNAP.dsync_copy_overall, SNAP.dsync_create]) expect(view(ok(snap)).caption).toBeNull();
    expect(view(RESP.unavailable, { after: 600 }).caption).toBeNull();
    expect(view(undefined).caption).toBeNull();
  });

  test("nsync 배치 여럿이 90초 넘게 낡으면 정체 경고(attn 안내)와 갱신 주기 캡션이 따로 선다 -- 섞이지 않는다", () => {
    const v = view(ok({ ...SNAP.nsync_batches_exec, since_line_s: 50 }, STALE_AFTER_S + 5));
    expect(v.note).toEqual({ tone: "attn", text: staleText("1분 35초") });
    expect(v.caption).toBe(CAPTION.nsyncPerBatch);
  });

  test("dsync 진행 줄 없는 단계 안내, 그 밖은 마지막 줄 뒤 60초가 넘으면 단정하지 않는 안내", () => {
    for (const phase of ["compare", "copy_prep", "metadata_dirs", "timestamps"]) {
      expect(view(ok({ v: 1, tool: "dsync", phase, since_line_s: 1 })).note, phase)
        .toEqual({ tone: "info", text: NOTE.dsyncSilentPhase });
    }
    const copy = { ...SNAP.dsync_copy_overall, since_line_s: 50 };
    expect(view(ok(copy, 10)).note).toBeNull();
    expect(view(ok(copy, 11)).note).toEqual({ tone: "info", text: quietLineText("1분 1초") });
  });

  test("available:false -- 60초 전 「기다리는 중」, 뒤는 단계별 중립 문구(늘 info, 단정 없음)", () => {
    const start = Date.parse(EXEC.start!) + EXEC.schedWaitSec! * 1000;
    const at = (sec: number) => (start + sec * 1000 - T0) / 1000;       // 실행 시작 뒤 sec 초
    expect(view(RESP.unavailable, { after: at(WAIT_GRACE_S - 1) }).note).toEqual({ tone: "info", text: NOTE.waiting });
    // 실행 + 스케줄 대기 관측(launcher 가 떠 있다) → 이전 이미지·기록 실패 가능성
    expect(view(RESP.unavailable, { after: at(WAIT_GRACE_S + 1) }).note).toEqual({ tone: "info", text: NOTE.noneRunning });
    // 미리보기(스케줄 대기를 따로 보지 않는다 -- Volcano Pending 일 수 있다) → 스케줄 대기 가능성
    const prevStart = Date.parse(PREV.start!);
    const pv = view(RESP.unavailable, { step: PREV, after: (prevStart + 61_000 - T0) / 1000 });
    expect(pv.note).toEqual({ tone: "info", text: NOTE.noneYet });
    expect(view(RESP.unavailable, { step: PREV, after: (prevStart + 59_000 - T0) / 1000 }).note?.text).toBe(NOTE.waiting);
    // 대기를 모르는 옛 실행 잡도 단정하지 않는다
    expect(view(RESP.unavailable, { step: { ...EXEC, schedWaitSec: null }, after: 600 }).note?.text).toBe(NOTE.noneYet);
    // 시작 시각을 모르면 「60초가 지났는지」 모른다 -- 경과를 지어내지 않고 중립 문구
    expect(view(RESP.unavailable, { step: { ...PREV, start: null } }).note?.text).toBe(NOTE.noneYet);
    for (const v of [pv, view(RESP.unavailable, { after: 600 })]) {
      expect(v).toMatchObject({ title: null, percent: null, fresh: null, facts: [] });
      expect(v.note?.tone).toBe("info");
      expect(v.note?.text).not.toMatch(/남기지 않았습니다|실패/);
    }
  });

  test("응답 전(첫 조회 진행 중) → 불러오는 중", () => {
    expect(view(undefined).note).toEqual({ tone: "info", text: NOTE.loading });
  });

  test("available:true 인데 progress 가 객체가 아니면 available:false 와 같다(모양 방어)", () => {
    const bad = { phase: "execution", available: true, age_seconds: 3, progress: "x" } as unknown as JobProgressResponse;
    expect(view(bad, { after: 600 }).note?.text).toBe(NOTE.noneRunning);
  });

  test("오류(503 등)는 사유 문구 -- 마지막 스냅숏이 있으면 그대로 두고 안내만 오류로", () => {
    for (const e of RESP.errors) {
      const err = new ApiError(e.status, e.body.detail, reasonText(e.body.detail));
      expect(view(undefined, { error: err }).note).toEqual({ tone: "attn", text: reasonText(e.body.detail) });
    }
    const err = new ApiError(503, "artifact_base_unavailable", "");
    const kept = view(ok(SNAP.dsync_create), { error: err });
    expect(kept).toMatchObject({ title: "파일 생성", percent: 38,
      note: { tone: "attn", text: reasonText("artifact_base_unavailable") } });
    // 네트워크 단절: dms-ssc 의 request() 는 fetch 거절을 ApiError 로 감싸지 않는다(dms-k8s 73d29c1e 의 network_error 는 이식
    // 범위 밖) -- 브라우저의 TypeError 문구가 다른 작업 화면(요청 상세·전체 작업 등)과 같은 길로 안내 줄에 실린다.
    expect(view(undefined, { error: new TypeError("Failed to fetch") }).note).toEqual({ tone: "attn", text: "Failed to fetch" });
  });
});

describe("한 번 받은 스냅숏은 같은 잡·단계에서 놓지 않는다(held)", () => {
  // 2026-11-27 dms-k8s d50 실증 F3(net.jsonl 18:16:44.846Z): 실행 중(copy 85%) 응답 하나만 200 available:false 였다(앞뒤 응답의 나이로 보면 러너의
  // rename 과 같은 순간 -- 공유 NFS 의 읽기 실패로 추정). 그때 화면이 스냅숏을 버리고 5초 동안 「진행 정보가 없습니다 — … 이전 잡
  // 이미지로 실행 중이거나…」를 보였다.
  const KEY = "j1/execution";
  const held = (snap: ProgressSnapshot, age: number, atMs: number): HeldProgress => ({ key: KEY, resp: ok(snap, age), atMs });
  const at = (resp: JobProgressResponse | undefined, h: HeldProgress | null, o: { after?: number; error?: unknown; step?: ProgressStepInfo } = {}) =>
    progressView(resp, o.error ?? null, {}, o.step ?? EXEC, T0 + (o.after ?? 0) * 1000, T0, h);

  test("available:false 한 번 -- 마지막 성공 스냅숏을 그대로 그리고 「이전 잡 이미지」 문구를 내지 않는다", () => {
    const v = at(RESP.unavailable, held(SNAP.dsync_copy_overall, 5, T0 - 5000));
    expect(v).toMatchObject({ title: "데이터 복사", percent: 39.958, basis: BASIS.overall, note: null, fresh: "갱신 10초 전" });
    expect(v.facts[0]).toBe("전체 40,000 / 100,105 항목");
    for (const s of strings(v)) expect(s).not.toMatch(/진행 정보가|이전 잡 이미지/);
  });

  test("유지하는 동안 갱신 나이는 그 스냅숏 기준으로 계속 늘어 정체 경고 규칙을 그대로 탄다", () => {
    const h = held({ ...SNAP.dsync_copy_overall, since_line_s: 0 }, 5, T0);
    expect(at(RESP.unavailable, h, { after: 30 }).fresh).toBe("갱신 35초 전");
    expect(at(RESP.unavailable, h, { after: STALE_AFTER_S - 5 }).note?.tone).not.toBe("attn");
    expect(at(RESP.unavailable, h, { after: STALE_AFTER_S - 4 }).note).toEqual({ tone: "attn", text: staleText("1분 31초") });
    // 끝 스냅숏을 쥐고 있으면 정체 경고 없음(러너가 더 쓰지 않는다)
    expect(at(RESP.unavailable, held(SNAP.dsync_ended, 1, T0), { after: 600 }).note).toBeNull();
  });

  test("오류도 스냅숏은 유지하고 안내만 사유 문구로(TanStack 의 마지막 data 가 available:false 여도)", () => {
    const err = new ApiError(503, "artifact_base_unavailable", "");
    const v = at(RESP.unavailable, held(SNAP.dsync_create, 1, T0), { error: err });
    expect(v).toMatchObject({ title: "파일 생성", percent: 38, note: { tone: "attn", text: reasonText("artifact_base_unavailable") } });
  });

  test("이번 응답이 스냅숏이면 그것이 우선(쥐고 있던 값보다 새것) -- 나이도 이번 응답 기준", () => {
    const v = at(ok(SNAP.dsync_create, 2), held(SNAP.dsync_copy_overall, 5, T0 - 60_000), { after: 1 });
    expect(v).toMatchObject({ title: "파일 생성", fresh: "갱신 3초 전" });
  });

  test("성공을 한 번도 못 받았으면(held 없음) 지금처럼 단정하지 않는 문구", () => {
    expect(at(RESP.unavailable, null, { after: 600 }).note).toEqual({ tone: "info", text: NOTE.noneRunning });
    expect(at(undefined, null).note).toEqual({ tone: "info", text: NOTE.loading });
  });

  test("nextHeld -- available:true 응답만 쥐고, 헛걸음·오류(undefined)엔 그대로, 다른 잡·단계면 버린다", () => {
    const a = ok(SNAP.dsync_create, 1);
    const h1 = nextHeld(null, KEY, a, T0);
    expect(h1).toEqual({ key: KEY, resp: a, atMs: T0 });
    expect(nextHeld(h1, KEY, a, T0)).toBe(h1);                                // 같은 응답이면 같은 객체(상태를 흔들지 않는다)
    expect(nextHeld(h1, KEY, RESP.unavailable, T0 + 5000)).toBe(h1);
    expect(nextHeld(h1, KEY, undefined, T0 + 5000)).toBe(h1);
    const bad = { phase: "execution", available: true, age_seconds: 3, progress: "x" } as unknown as JobProgressResponse;
    expect(nextHeld(h1, KEY, bad, T0 + 5000)).toBe(h1);                        // 모양이 틀린 응답은 스냅숏이 아니다
    expect(nextHeld(null, KEY, RESP.unavailable, T0)).toBeNull();
    const b = ok(SNAP.dsync_copy_overall, 0);
    expect(nextHeld(h1, KEY, b, T0 + 5000)).toEqual({ key: KEY, resp: b, atMs: T0 + 5000 });
    expect(nextHeld(h1, "j2/execution", RESP.unavailable, T0)).toBeNull();
    expect(nextHeld(h1, "j1/preview", undefined, T0)).toBeNull();
  });
});

describe("showsProgress(어느 행에 그리나)", () => {
  const refs = { preflight: "pod/a", preview: "vcjob/b", exec_preflight: "pod/c", execution: "vcjob/d" };
  const row = (id: string, status = "running") => ({ id, status, phase: id });
  test("진행 중인 실행·미리보기 행 ∧ dsync·nsync ∧ 그 단계 ref", () => {
    expect(showsProgress(row("execution"), false, "dsync", refs)).toBe(true);
    expect(showsProgress(row("preview"), false, "nsync", refs)).toBe(true);
  });
  test("끝났거나·진행 중이 아니거나·다른 단계·다른 도구·ref 없음이면 그리지 않는다", () => {
    expect(showsProgress(row("execution"), true, "dsync", refs)).toBe(false);
    expect(showsProgress(row("execution", "waiting"), false, "dsync", refs)).toBe(false);
    expect(showsProgress(row("exec_preflight"), false, "dsync", refs)).toBe(false);
    expect(showsProgress(row("preflight"), false, "dsync", refs)).toBe(false);
    for (const tool of ["dscan", "drm", null, undefined, ""]) {
      expect(showsProgress(row("execution"), false, tool, refs)).toBe(false);
    }
    expect(showsProgress(row("execution"), false, "dsync", { preflight: "pod/a" })).toBe(false);
    expect(showsProgress({ id: "execution", status: "running", phase: null }, false, "dsync", refs)).toBe(false);
  });
});
