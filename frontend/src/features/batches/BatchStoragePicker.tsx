import { Link } from "react-router-dom";
import type { UserStorage } from "../../lib/types";
import { KIND_LABEL, storageKindOf } from "../k8s/k8sTerms";
import type { StorageKind } from "../k8s/types";

// 배치 생성의 스토리지 고르기(2026-10-26 BACKLOG C 「배치 생성 폼의 스토리지 드롭다운」 → 2026-11-26 단계별 등록의 라디오 카드).
// 단건 폼의 StoragePicker(jobs/formFields)와 같은 목록(/api/user/storages — 관리자 응답 = 켜진 스토리지 전부, 관리자 전용 포함)이지만
// 배치는 관리자 이관·정리 도구라 **종류**를 함께 보인다: 이름이 비슷한 스토리지(gpfs 이관 원본·대상, PV 전용 등)를 고를 때 무엇인지가
// 이름만으로 갈리지 않았다.
//   - 카드로 그린다(드롭다운 하나뿐인 단계는 2026-10-06 에 위저드를 걷어낸 이유 그대로라 ② 스토리지 단계가 카드 묶음이다 --
//     design-wizard §0-2 5). 역할은 <div role="radiogroup" aria-label={label}> + 카드마다 숨긴 radio(sr-only, 카드 전체가 누르는 영역).
//   - **묶음마다 다른 라디오 name**(호출측 prop -- batch-storage-single·-src·-dst): 같은 name 이면 목적지를 고르는 순간 소스 선택이
//     풀린다(브라우저 라디오 묶음 규칙).
//   - 종류(파일시스템 / PV·파일시스템 / PV)로 묶는다(role="group" + 종류 이름 -- 소제목은 aria-hidden 이 아니다). radio 의 접근 이름은
//     storageOptionText(이름 + 백엔드)라 보조기기·테스트가 이름 앞부분으로 찾는다. 단건 폼은 PV 전용을 직접 경로 선택지에서 빼지만
//     (storageKindOf !== "pv"), 배치는 「PV 에서 경로 찾기」(PvPathPicker)가 PV 의 직접 경로를 넣으면서 그 PV 의 스토리지(PV 전용일 수
//     있다)로 채우므로 여기선 빼지 않는다 — 빼면 넣은 값이 선택지에 없어 빈칸으로 보였다.
//   - 고른 값이 목록에 없으면(비활성으로 바뀜·PV 시트가 넣은 이름이 목록 밖) 「(목록에 없음)」 카드를 고른 채로 보인다 — 조용히
//     아무것도 안 고른 것처럼 그리면 바디에는 그 이름이 실리는데 화면은 「선택 안 함」이라 거짓이 된다.
//   - 아래 캡션은 고른 스토리지의 백엔드·종류·관리 디렉토리(입력 경로의 뿌리 — StoragePicker 와 같은 문장).
//   - 목록이 비었으면 스토리지 관리로 가는 길을 말한다(관리자 화면이다).
const KIND_ORDER: StorageKind[] = ["filesystem", "both", "pv"];

export function storageOptionText(s: UserStorage): string {
  return `${s.storage_name} (${s.backend_type})${s.admin_only ? " · 관리자 전용" : ""}`;
}

const CARD = "relative flex min-w-0 cursor-pointer flex-col gap-1 rounded-xl border-[1.5px] bg-surface px-3 py-2.5 transition "
  + "has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 "
  + "has-[:focus-visible]:outline-accent";

function StorageCard({ name, title, aria, checked, onPick, backend, adminOnly, root, missing = false }: {
  name: string; title: string; aria: string; checked: boolean; onPick: () => void;
  backend?: string; adminOnly?: boolean; root?: string | null; missing?: boolean;
}) {
  const tone = missing ? "border-bad bg-badbg/60"
    : checked ? "border-accent bg-infobg/50 ring-2 ring-infobg" : "border-line hover:border-muted/60";
  return (
    <label className={`${CARD} ${tone}`}>
      <input type="radio" name={name} value={title} checked={checked} aria-label={aria} onChange={onPick} className="sr-only" />
      <span className="flex min-w-0 flex-wrap items-center gap-1.5">
        <span aria-hidden className={`grid h-4 w-4 shrink-0 place-items-center rounded-full border-2 ${
          checked ? "border-accent" : "border-line"}`}>
          {checked && <span className="h-1.5 w-1.5 rounded-full bg-accent" />}
        </span>
        <span className="min-w-0 text-sm font-semibold text-ink [overflow-wrap:anywhere]">{title}</span>
        {backend !== undefined && (
          <span className="whitespace-nowrap rounded-full border border-line bg-panel px-1.5 text-[11px] text-ink">{backend}</span>
        )}
        {adminOnly && <span className="whitespace-nowrap rounded-full bg-attnbg px-1.5 text-[11px] text-attn">관리자 전용</span>}
        {missing && <span className="whitespace-nowrap text-[11px] text-bad">목록에 없음</span>}
      </span>
      {typeof root === "string" && root !== "" && (
        <code className="pl-[22px] text-xs text-muted [overflow-wrap:anywhere]">{root}</code>
      )}
    </label>
  );
}

export function BatchStorageCards({ label, name, value, onChange, storages, loading }: {
  label: string; name: string; value: string; onChange: (v: string) => void;
  storages: UserStorage[]; loading: boolean;
}) {
  const picked = storages.find((s) => s.storage_name === value);
  const missing = value !== "" && picked === undefined && !loading;
  const groups = KIND_ORDER.map((k) => ({ kind: k, rows: storages.filter((s) => storageKindOf(s) === k) }))
    .filter((g) => g.rows.length > 0);
  return (
    <div className="min-w-0 space-y-2">
      <p aria-hidden className="text-sm font-semibold text-ink">{label}</p>
      <div role="radiogroup" aria-label={label} className="space-y-3">
        {loading && <p className="text-xs text-ink/70">불러오는 중…</p>}
        {groups.map((g) => (
          <div key={g.kind} role="group" aria-label={KIND_LABEL[g.kind]} className="space-y-1.5">
            <p className="text-xs font-semibold text-muted">{KIND_LABEL[g.kind]}</p>
            <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,13rem),1fr))] gap-2">
              {g.rows.map((s) => (
                <StorageCard key={s.storage_name} name={name} title={s.storage_name} aria={storageOptionText(s)}
                             checked={s.storage_name === value} onPick={() => onChange(s.storage_name)}
                             backend={s.backend_type} adminOnly={s.admin_only === true}
                             root={typeof s.managed_root === "string" ? s.managed_root : null} />
              ))}
            </div>
          </div>
        ))}
        {missing && (
          <StorageCard name={name} title={value} aria={`${value} (목록에 없음)`} checked onPick={() => onChange(value)} missing />
        )}
      </div>
      {picked !== undefined && (
        <p className="text-xs text-muted break-all">
          {`${picked.backend_type} · ${KIND_LABEL[storageKindOf(picked)]}`}
          {typeof picked.managed_root === "string" && picked.managed_root !== ""
            && ` · 관리 디렉토리: ${picked.managed_root} — 입력 경로는 이 아래 상대경로입니다`}
        </p>
      )}
      {missing && (
        <p className="text-xs text-bad">
          {`「${value}」는 켜진 스토리지 목록에 없습니다 — 다른 스토리지를 고르거나 스토리지 관리에서 확인하세요`}
        </p>
      )}
      {!loading && storages.length === 0 && (
        <p className="text-xs text-ink/70">
          켜진 스토리지가 없습니다 — <Link className="text-accent underline" to="/admin/storages">스토리지 관리</Link>에서 먼저
          등록하세요.
        </p>
      )}
    </div>
  );
}
