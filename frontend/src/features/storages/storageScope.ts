import type { Storage } from "../../lib/types";

// 스토리지 사용 범위(2026-09-30 사용자 요청: "비활성화 종류가 두 가지 — 완전 비활성화,
// 사용자에게만 비활성화"). 서버 정의는 repositories.storages.storage_open_to_users 하나다:
//   all   = enabled=1, user_enabled=1 → 관리자·사용자 모두 작업에 쓴다
//   admin = enabled=1, user_enabled=0 → 관리자 전용(사용자 피커에서 빠지고 제출·계획 거부)
//   off   = enabled=0                 → 완전 비활성(누구도 새 작업 불가)
// user_enabled 부재·null(옛 서버·컬럼 이전 행)은 사용자 공개로 읽는다(서버와 같은 규칙).
export type StorageScope = "all" | "admin" | "off";

export function scopeOf(s: Pick<Storage, "enabled" | "user_enabled">): StorageScope {
  if (s.enabled !== 1) return "off";
  return s.user_enabled === 0 ? "admin" : "all";
}

/** PUT/POST 바디의 두 플래그. off 는 user_enabled 를 **생략**(서버 = 현재 값 유지) -- 비활성
    동안에도 이전 범위(관리자 전용 여부)가 DB 에 남는다. 다시 켤 때는 포탈이 고른 범위를 명시로
    보내므로(all/admin), 화면은 비활성 행에 그 이전 범위를 보여 준다(StoragesList). */
export function flagsFor(scope: StorageScope): { enabled: boolean; user_enabled?: boolean } {
  if (scope === "off") return { enabled: false };
  return { enabled: true, user_enabled: scope === "all" };
}

// tone = 셀렉트·선택된 라디오의 채움색, text·dot = 범례 카드의 글자색·점(범례는 흰 카드 --
// 0건인 상태까지 배경색으로 칠하면 "완전 비활성 0" 이 경보처럼 보인다).
export const SCOPES: {
  value: StorageScope; label: string; help: string; tone: string; text: string; dot: string;
}[] = [
  { value: "all", label: "전체 사용",
    help: "관리자와 사용자 모두 작업에 쓸 수 있습니다",
    tone: "text-ok bg-okbg border-ok/30", text: "text-ok", dot: "bg-ok" },
  { value: "admin", label: "관리자 전용",
    help: "사용자에게만 비활성 — 사용자 화면에서 숨겨지고 사용자 작업은 거부됩니다",
    tone: "text-busy bg-busybg border-busy/30", text: "text-busy", dot: "bg-busy" },
  { value: "off", label: "완전 비활성",
    help: "관리자·사용자 모두 새 작업을 낼 수 없습니다(진행 중 작업은 그대로)",
    tone: "text-bad bg-badbg border-bad/30", text: "text-bad", dot: "bg-bad" },
];

export const scopeMeta = (scope: StorageScope) => SCOPES.find((x) => x.value === scope)!;
