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
    </label>
  );
}
