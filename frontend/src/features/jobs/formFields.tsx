import type { UserStorage } from "../../lib/types";

// SubmitJob 에 살던 공용 폼 조각의 이사처(슬라이스 31 T3, 전제 재확인 #2).
// SubmitScan·ScanPaths 가 SubmitJob 을 import 한 채로 T4 위저드화를 하면 화면
// 하나를 고칠 때 세 화면이 흔들린다 -- 위저드화 전에 결합을 끊는다.
// 렌더 결과(aria-label·옵션 문구)는 원문 그대로: 임포터 테스트 무수정 초록이 계약.

// 보더만 border-black/10 → border-line 토큰으로 스왑(전제 #7 -- field 는 한 곳).
export const field = "mt-1 w-full rounded-lg border border-line px-3 py-2";

export function StoragePicker({ label, value, onChange, storages, loading }: {
  label: string; value: string; onChange: (v: string) => void;
  storages: UserStorage[]; loading: boolean;
}) {
  // 고른 스토리지의 관리 디렉토리(사용자 보고 2026-08-15: "스토리지 이름은 보이는데
  // 관리 디렉토리가 표시가 안 돼서 정확한 path 를 알 수가 없다"). 옵션 텍스트가
  // 아니라 **선택 아래 캡션**인 이유: 옵션에 경로까지 넣으면 드롭다운 한 줄이
  // 길어져 이름·상태가 잘리는데, 정작 알아야 하는 건 "지금 고른 것"의 뿌리 하나다.
  // 함께 적는 "이 아래 상대경로" 한 마디가 사용자가 못 알아냈던 사실 자체다 —
  // 입력란에 절대경로를 적어야 하는지 상대경로를 적어야 하는지가 화면 어디에도
  // 없었다. managed_root 는 관리자 응답에만 실려 오므로(서버 계약) 없으면 캡션도
  // 없다 — 비관리자 화면에 내부 경로가 새지 않는다.
  const root = storages.find((s) => s.storage_name === value)?.managed_root;
  return (
    <label className="text-sm">{label}
      <select aria-label={label} className={field} value={value} disabled={loading}
              onChange={(e) => onChange(e.target.value)}>
        <option value="">{loading ? "불러오는 중…" : "선택하세요"}</option>
        {storages.map((s) => (
          <option key={s.storage_name} value={s.storage_name}>
            {s.status === "Ready" || s.status === "Degraded"
              ? `${s.storage_name} (${s.status})`
              : `${s.storage_name} (${s.status} — 준비 안 됨)`}
          </option>
        ))}
      </select>
      {root && (
        <span className="mt-1 block text-xs text-muted break-all">
          {`관리 디렉토리: ${root} — 입력 경로는 이 아래 상대경로입니다`}
        </span>
      )}
    </label>
  );
}
