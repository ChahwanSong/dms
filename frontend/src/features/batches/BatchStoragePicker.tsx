import { Link } from "react-router-dom";
import type { UserStorage } from "../../lib/types";
import { field } from "../jobs/formFields";
import { KIND_LABEL, storageKindOf } from "../k8s/k8sTerms";
import type { StorageKind } from "../k8s/types";

// 배치 생성의 스토리지 고르기(2026-10-26 BACKLOG C 「배치 생성 폼의 스토리지 드롭다운」). 단건 폼의 StoragePicker
// (jobs/formFields)와 같은 목록(/api/user/storages — 관리자 응답 = 켜진 스토리지 전부, 관리자 전용 포함)이지만 배치는
// 관리자 이관·정리 도구라 **종류**를 함께 보인다: 이름이 비슷한 스토리지(gpfs 이관 원본·대상, PV 전용 등)를 고를 때 무엇인지가
// 이름만으로 갈리지 않았다.
//   - 종류(파일시스템 / PV·파일시스템 / PV)는 optgroup 으로 묶는다 — 옵션 글자(접근 이름)는 이름 + 백엔드라 보조기기·테스트가
//     이름 앞부분으로 찾는다. 단건 폼은 PV 전용을 직접 경로 선택지에서 뺀다(storageKindOf !== "pv")지만, 배치는 「PV 에서 경로
//     찾기」(PvPathPicker)가 PV 의 직접 경로를 넣으면서 그 PV 의 스토리지(PV 전용일 수 있다)로 채우므로 여기선 빼지 않는다 —
//     빼면 넣은 값이 선택지에 없어 빈칸으로 보였다.
//   - 고른 값이 목록에 없으면(비활성으로 바뀜·PV 패널이 넣은 이름이 목록 밖) 「(목록에 없음)」 선택지로 그대로 보인다 —
//     select 가 조용히 빈칸으로 그리면 바디에는 그 이름이 실리는데 화면은 「선택하세요」라 거짓이 된다.
//   - 아래 캡션은 고른 스토리지의 백엔드·종류·관리 디렉토리(입력 경로의 뿌리 — StoragePicker 와 같은 문장).
//   - 목록이 비었으면 스토리지 관리로 가는 길을 말한다(관리자 화면이다).
const KIND_ORDER: StorageKind[] = ["filesystem", "both", "pv"];

export function storageOptionText(s: UserStorage): string {
  return `${s.storage_name} (${s.backend_type})${s.admin_only ? " · 관리자 전용" : ""}`;
}

export function BatchStoragePicker({ label, value, onChange, storages, loading }: {
  label: string; value: string; onChange: (v: string) => void;
  storages: UserStorage[]; loading: boolean;
}) {
  const picked = storages.find((s) => s.storage_name === value);
  const missing = value !== "" && picked === undefined && !loading;
  const groups = KIND_ORDER.map((k) => ({ kind: k, rows: storages.filter((s) => storageKindOf(s) === k) }))
    .filter((g) => g.rows.length > 0);
  return (
    <label className="text-sm">{label}
      <select aria-label={label} className={field} value={value} disabled={loading}
              onChange={(e) => onChange(e.target.value)}>
        <option value="">{loading ? "불러오는 중…" : storages.length === 0 ? "등록된 스토리지가 없습니다" : "선택하세요"}</option>
        {groups.map((g) => (
          <optgroup key={g.kind} label={KIND_LABEL[g.kind]}>
            {g.rows.map((s) => <option key={s.storage_name} value={s.storage_name}>{storageOptionText(s)}</option>)}
          </optgroup>
        ))}
        {missing && <option value={value}>{`${value} (목록에 없음)`}</option>}
      </select>
      {picked !== undefined && (
        <span className="mt-1 block text-xs text-muted break-all">
          {`${picked.backend_type} · ${KIND_LABEL[storageKindOf(picked)]}`}
          {typeof picked.managed_root === "string" && picked.managed_root !== ""
            && ` · 관리 디렉토리: ${picked.managed_root} — 입력 경로는 이 아래 상대경로입니다`}
        </span>
      )}
      {missing && (
        <span className="mt-1 block text-xs text-bad">
          {`「${value}」는 켜진 스토리지 목록에 없습니다 — 다른 스토리지를 고르거나 스토리지 관리에서 확인하세요`}
        </span>
      )}
      {!loading && storages.length === 0 && (
        <span className="mt-1 block text-xs text-ink/70">
          켜진 스토리지가 없습니다 — <Link className="text-accent underline" to="/admin/storages">스토리지 관리</Link>에서 먼저
          등록하세요.
        </span>
      )}
    </label>
  );
}
