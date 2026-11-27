import { Link } from "react-router-dom";
import type { UserStorage } from "../../lib/types";

// 배치 생성의 스토리지 고르기(2026-11-27 단계별 등록 -- dms-k8s 46caffe3 의 BatchStorageCards 이식). 단건 폼의 StoragePicker(jobs/formFields
// -- 드롭다운)와 같은 목록(/api/user/storages — 관리자 응답 = 켜진 스토리지 전부, 관리자 전용 포함)을 라디오 카드로 그린다:
//   - 카드로 그린다(드롭다운 하나뿐인 단계는 2026-10-06 에 4스텝 위저드를 걷어낸 이유 그대로라 ② 스토리지 단계가 카드 묶음이다 --
//     dms-k8s design-wizard §0-2 5). 역할은 <div role="radiogroup" aria-label={label}> + 카드마다 숨긴 radio(sr-only, 카드 전체가
//     누르는 영역). radio 의 접근 이름은 storageOptionText(이름 + 백엔드)라 보조기기·테스트가 이름 앞부분으로 찾는다(test/wizard
//     pickStorageCard).
//   - **묶음마다 다른 라디오 name**(호출측 prop -- batch-storage-single·-src·-dst): 같은 name 이면 목적지를 고르는 순간 소스 선택이
//     풀린다(브라우저 라디오 묶음 규칙).
//   - dms-k8s 는 스토리지 종류(파일시스템 / PV·파일시스템 / PV)로 묶지만 dms-ssc 엔 PV 개념이 없어(백엔드는 등록 라벨일 뿐 런타임은
//     mount_path 만 본다) 묶지 않는다 -- 한 격자에 목록 순서 그대로, 백엔드는 카드의 칩.
//   - 고른 값이 목록에 없으면(그 사이 비활성으로 바뀌어 재조회 목록에서 빠짐) 「(목록에 없음)」 카드를 고른 채로 보인다 — 조용히
//     아무것도 안 고른 것처럼 그리면 바디에는 그 이름이 실리는데 화면은 「선택 안 함」이라 거짓이 된다.
//   - 아래 캡션은 고른 스토리지의 백엔드·관리 디렉토리(입력 경로의 뿌리 — StoragePicker 와 같은 문장). managed_root 를 옛 서버가 안
//     주면 캡션의 그 부분도 없다 — 지어내지 않는다.
//   - 목록이 비었으면 스토리지 관리로 가는 길을 말한다(관리자 화면이다). 단 **조회가 실패했으면 말하지 않는다**(error) -- 실패한
//     첫 조회도 목록은 빈 배열·loading false 라, 그대로 두면 「목록을 못 불렀다」가 「켜진 스토리지가 없다(가서 등록하라)」로 읽힌다
//     (모름·실패 ≠ 0). 실패 사유는 호출측(BatchCreate ② 카드 위)이 이미 보인다.

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

export function BatchStorageCards({ label, name, value, onChange, storages, loading, error }: {
  label: string; name: string; value: string; onChange: (v: string) => void;
  storages: UserStorage[]; loading: boolean; error: boolean;
}) {
  const picked = storages.find((s) => s.storage_name === value);
  const missing = value !== "" && picked === undefined && !loading;
  return (
    <div className="min-w-0 space-y-2">
      <p aria-hidden className="text-sm font-semibold text-ink">{label}</p>
      <div role="radiogroup" aria-label={label} className="space-y-2">
        {loading && <p className="text-xs text-ink/70">불러오는 중…</p>}
        {storages.length > 0 && (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,13rem),1fr))] gap-2">
            {storages.map((s) => (
              <StorageCard key={s.storage_name} name={name} title={s.storage_name} aria={storageOptionText(s)}
                           checked={s.storage_name === value} onPick={() => onChange(s.storage_name)}
                           backend={s.backend_type} adminOnly={s.admin_only === true}
                           root={typeof s.managed_root === "string" ? s.managed_root : null} />
            ))}
          </div>
        )}
        {missing && (
          <StorageCard name={name} title={value} aria={`${value} (목록에 없음)`} checked onPick={() => onChange(value)} missing />
        )}
      </div>
      {picked !== undefined && (
        <p className="text-xs text-muted break-all">
          {picked.backend_type}
          {typeof picked.managed_root === "string" && picked.managed_root !== ""
            && ` · 관리 디렉토리: ${picked.managed_root} — 입력 경로는 이 아래 상대경로입니다`}
        </p>
      )}
      {missing && (
        <p className="text-xs text-bad">
          {`「${value}」는 켜진 스토리지 목록에 없습니다 — 다른 스토리지를 고르거나 스토리지 관리에서 확인하세요`}
        </p>
      )}
      {!loading && !error && storages.length === 0 && (
        <p className="text-xs text-ink/70">
          켜진 스토리지가 없습니다 — <Link className="text-accent underline" to="/admin/storages">스토리지 관리</Link>에서 먼저
          등록하세요.
        </p>
      )}
    </div>
  );
}
