import { expect, test } from "vitest";
import { HELP_SUMMARY, policyCaptionAlert } from "./optionRules";

// optionRules 의 단계별 등록 몫(2026-11-27 dms-k8s ed7fe322·b1eddc3c 이식) -- 화면과 무관한 순수 판정·문구 계약. 요약 상수의 첫 문장
// 계약·키 집합은 components/form/wizardParts.test 가 맡는다.

const rows = (r: Record<string, number>) => new Map(Object.entries(r).map(([tool, enabled]) => [tool, { enabled }]));

test("policyCaptionAlert — 조회 중은 경고가 아니고(모름 ≠ 실패), 조회 실패는 경고", () => {
  expect(policyCaptionAlert(undefined, "sync", true)).toBe(false);
  expect(policyCaptionAlert(rows({ dsync: 0 }), "sync", true)).toBe(false);   // 조회 중이 다른 판정보다 앞선다
  expect(policyCaptionAlert(undefined, "sync", false)).toBe(true);
  expect(policyCaptionAlert(undefined, "rm", false)).toBe(true);
});

test("policyCaptionAlert — scan·rm 은 그 도구 행 하나: 없으면·비활성이면 경고, 활성이면 접는다", () => {
  for (const op of ["scan", "rm"]) {
    expect(policyCaptionAlert(rows({}), op, false)).toBe(true);
    expect(policyCaptionAlert(rows({ [op]: 0 }), op, false)).toBe(true);
    expect(policyCaptionAlert(rows({ [op]: 1 }), op, false)).toBe(false);
    // 다른 도구 행은 보지 않는다
    expect(policyCaptionAlert(rows({ [op]: 1, dsync: 0, nsync: 0 }), op, false)).toBe(false);
  }
});

test("policyCaptionAlert — sync 는 dsync·nsync: 있는 행 중 하나라도 비활성이면 경고, 행 부재는 둘 다 없을 때만", () => {
  expect(policyCaptionAlert(rows({ dsync: 1, nsync: 1 }), "sync", false)).toBe(false);
  expect(policyCaptionAlert(rows({ dsync: 1 }), "sync", false)).toBe(false);      // 한쪽만 있으면 캡션의 「미조회」로 둔다
  expect(policyCaptionAlert(rows({ nsync: 1 }), "sync", false)).toBe(false);
  expect(policyCaptionAlert(rows({ dsync: 0, nsync: 1 }), "sync", false)).toBe(true);
  expect(policyCaptionAlert(rows({ dsync: 1, nsync: 0 }), "sync", false)).toBe(true);
  expect(policyCaptionAlert(rows({ nsync: 0 }), "sync", false)).toBe(true);
  expect(policyCaptionAlert(rows({ scan: 1, rm: 1 }), "sync", false)).toBe(true);  // dsync·nsync 둘 다 없음
  // enabled 는 1 만 활성(서버 정수 플래그 -- truthy 로 뭉개지 않는다)
  expect(policyCaptionAlert(rows({ dsync: 2, nsync: 1 }), "sync", false)).toBe(true);
});

test("실행 신원 요약 — 배치(runAs)만 「root 로 실행」(자격이 빠지면 그 신원 권한), 단일 작업 문장은 다른 실행 신원의 기본이 그 사용자 권한임을 말한다", () => {
  // 단일 작업 화면에 「root 로 실행」을 보이면 2026-09-30 사고 경로(관리자 + 다른 실행 신원 = root)를 다시 여는 문장이 된다(CLAUDE.md).
  expect(HELP_SUMMARY.runAs).toContain("배치는 root 로 실행됩니다");
  // ssc 의 배치 자식은 계획 시점 자격이 있을 때만 root(identity.PRIVILEGE_IF_ELIGIBLE) -- dms-k8s 의 「늘 root」는 여기서 거짓이다.
  expect(HELP_SUMMARY.runAs).not.toContain("늘 root");
  expect(HELP_SUMMARY.runAs).toContain("자격이 빠진 드문 경우만 이 신원 권한");
  expect(HELP_SUMMARY.runAsSingle).not.toContain("root 로 실행");
  expect(HELP_SUMMARY.runAsSingle).not.toContain("늘 root");
  expect(HELP_SUMMARY.runAsSingle).toContain("다른 사용자를 적으면 기본은 그 사용자 권한(root 아님)");
  expect(HELP_SUMMARY.runAsRoot).not.toContain("늘 root");
  // 요약은 전문(SubmitJob 「비우면 …」·「켜면 잡이 root 로 …」, BatchCreate 「배치는 관리자 이관·정리용이라 …」)과 다른 문장이다 --
  // 화면 시험의 startsWith 단언이 요약 줄에 겹치지 않게.
  for (const s of [HELP_SUMMARY.runAs, HELP_SUMMARY.runAsSingle, HELP_SUMMARY.runAsRoot]) {
    for (const head of ["비우면 ", "켜면 잡이 root 로", "배치는 관리자 이관·정리용이라"]) expect(s.startsWith(head)).toBe(false);
  }
});
