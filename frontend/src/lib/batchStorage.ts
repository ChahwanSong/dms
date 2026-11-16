// 배치의 「대상 스토리지」 표기 한 벌(2026-11-16) — 배치 목록 열(BatchesList)과 배치 스케줄러(목록·추가 창)가 같은 규칙을 쓴다
// (두 화면이 같은 배치를 다르게 말하지 않게; 서버도 판정 하나 — repositories/batches._storage_fields). 값은 서버가 배치 첫 항목
// payload 에서 뽑아 준다(배치는 스토리지 한 종류 — sync 는 한 쌍, 동질성은 서버가 강제).
// 규칙: scan·rm 은 storage, sync 는 「소스 → 목적지」(BatchCreate·작업 목록 대상 열과 같은 구분자). 문자열이 아니거나 빈 글자면
// 모름(null·부재·신뢰 경계 탈락) — 모르는 쪽은 「—」, sync 둘 다 모르면 「— → —」 대신 null 하나(호출측이 「—」 하나로 그린다).
export interface BatchStorageFields {
  storage?: string | null; source_storage?: string | null; destination_storage?: string | null;
}

const nameOf = (v: unknown): string | null => (typeof v === "string" && v !== "" ? v : null);

/** 아는 값이 하나라도 있으면 표기 문자열, 하나도 모르면 null(호출측이 「—」·「항목이 없습니다」 등으로 말한다). */
export function batchStorageLabel(op: string | null | undefined,
                                  f: BatchStorageFields | null | undefined): string | null {
  if (!f) return null;
  if (op === "sync") {
    const src = nameOf(f.source_storage), dst = nameOf(f.destination_storage);
    if (src === null && dst === null) return null;
    return `${src ?? "—"} → ${dst ?? "—"}`;
  }
  return nameOf(f.storage);
}

/** 한 줄 표기 — 모르면 「—」. */
export const storageText = (op: string | null | undefined, f: BatchStorageFields | null | undefined): string =>
  batchStorageLabel(op, f) ?? "—";
