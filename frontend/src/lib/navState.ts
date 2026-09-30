// 사이드바 그룹 접힘 상태(사용자 결정 2026-09-30): **로그인할 때 전부 펼침으로 리셋**되고,
// 그 뒤로는 화면을 오가도(셸 리마운트)·새로고침·새 탭에서도 사용자가 접고 편 상태가 **유지**된다.
// - 저장소는 localStorage: 탭 단위(sessionStorage)면 새 탭·다른 탭이 "처음" 처럼 전부 펼쳐져
//   "그 후 유지" 가 깨진다. 대신 리셋 시점을 로그인(useLogin 성공)·로그아웃에 명시로 둔다 --
//   다른 사용자가 같은 브라우저로 로그인해도 전부 펼침에서 시작한다.
// - 키 v3: 옛 규칙들(현재 그룹만 열림 v1 / 탭 단위 v2)이 남긴 값은 읽지 않는다.
// - 스토리지 차단(사생활 모드 등)이면 유지 없이 늘 전부 펼침 -- 기능은 산다.
export const NAV_COLLAPSED_KEY = "dms.nav.collapsed.v3";
const LEGACY_SESSION_KEYS = ["dms.nav.collapsed", "dms.nav.collapsed.v2"];

export function loadNavCollapsed(): Record<string, boolean> {
  try {
    const saved = JSON.parse(localStorage.getItem(NAV_COLLAPSED_KEY) ?? "");
    if (saved && typeof saved === "object" && !Array.isArray(saved)) return saved as Record<string, boolean>;
  } catch { /* 저장분 없음·파싱 불가·스토리지 차단 -- 전부 펼침 */ }
  return {};
}

export function saveNavCollapsed(value: Record<string, boolean>): void {
  try { localStorage.setItem(NAV_COLLAPSED_KEY, JSON.stringify(value)); }
  catch { /* 차단 환경 -- 유지 없이 동작 */ }
}

/** 로그인 성공·로그아웃 때 부른다(useAuth) -- 다음 화면은 전부 펼침에서 시작한다. */
export function resetNavCollapsed(): void {
  try { localStorage.removeItem(NAV_COLLAPSED_KEY); } catch { /* 차단 환경 */ }
  try { for (const k of LEGACY_SESSION_KEYS) sessionStorage.removeItem(k); } catch { /* 차단 환경 */ }
}
