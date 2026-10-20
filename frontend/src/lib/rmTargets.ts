// rm 배치 대상 겹침 즉답(2026-10-21) — 서버 domain._reject_overlapping_rm_targets(422 batch_rm_targets_overlap)의
// 화면 쪽 미러. 한 rm 배치 안에 같은 경로가 두 번 있거나 한 경로가 다른 경로를 포함하면(조상·자손) 서버가 생성·항목
// 추가·수정·교체를 거부한다: 자식 충돌 판정은 resource_key 가 정확히 같을 때만이라, 그대로 두면 확인 창의 「삭제 대상」
// 합계가 겹쳐 부풀고 a 를 먼저 지운 뒤 a/b 는 실패 항목이 된다. 화면은 제출 전에 **어느 행이 어느 행과** 겹치는지
// 말한다(서버 422 는 배치 단위 한 문장뿐이라 수백 행 CSV 에서 찾을 수 없다).
//
// 판정 규칙은 서버와 같다: posixpath.normpath 처럼 빈 성분·「.」 을 걷어 낸 뒤 경로 **성분** 단위로 비교한다 — a 와
// ab·「a b」·a.b 는 겹치지 않는다. 형식이 틀린 경로(relativePathProblem — 「/」 시작·「..」·관리 디렉토리 자신)와 빈
// 입력은 건너뛴다: 그 사유는 따로 말하고(서버도 형식 검사가 같은 이유를 낸다), 여기서 겹침으로 또 세면 한 행에 두
// 문장이 붙는다. 서버가 최종 심판이고 이건 표시·제출 잠금용이다.
import { relativePathProblem } from "./storagePaths";

export interface RmTargetEntry {
  label: string;     // 화면이 행을 부르는 이름(「3행」·「기존 #2」·「추가할 경로」) — 문장에서 조사 없이 쓴다
  target: string;    // 서버에 보낼 값 그대로(호출측이 trim 한 값)
}

/** 겹침 판정용 정규화 — 서버 validate_relative_path 의 normpath 와 같은 결과. 판정 밖(형식 오류·빈값)이면 null. */
export function normalizedRmTarget(target: string): string | null {
  if (target.trim() === "" || relativePathProblem(target) !== null) return null;
  const parts = target.split("/").filter((p) => p !== "" && p !== ".");
  return parts.length === 0 ? null : parts.join("/");
}

/** 겹치는 항목마다 한 문장(입력 순서). 겹침이 없으면 빈 배열. 같은 경로는 뒤에 나온 쪽이, 조상·자손은 자손 쪽이
 *  문장의 주어다 — 「어느 행을 지우거나 고치면 풀리는가」가 그 행이다(상위 경로가 하위를 함께 지운다).
 *  focus 를 주면 그 항목이 낀 겹침만 말한다(항목 추가 — 새 경로 하나만 판정 대상이고, 기존 항목끼리의 옛 겹침은 이
 *  화면이 고칠 수 없다). */
export function rmTargetOverlaps(entries: RmTargetEntry[], focus?: number): string[] {
  const involved = (i: number, j: number) => focus === undefined || i === focus || j === focus;
  const norms = entries.map((e) => normalizedRmTarget(e.target));
  const firstAt = new Map<string, number>();    // 정규화 경로 → 처음 나온 항목 번호
  norms.forEach((n, i) => { if (n !== null && !firstAt.has(n)) firstAt.set(n, i); });
  const out: string[] = [];
  entries.forEach((e, i) => {
    const n = norms[i];
    if (n === null) return;
    const seen = firstAt.get(n) as number;
    if (seen !== i) {
      if (involved(i, seen)) out.push(`${e.label}: 같은 경로(${n})가 ${entries[seen].label}에도 있습니다`);
      return;
    }
    const parts = n.split("/");
    for (let k = 1; k < parts.length; k += 1) {
      const anc = parts.slice(0, k).join("/");
      const owner = firstAt.get(anc);
      if (owner !== undefined) {
        if (involved(i, owner)) {
          out.push(`${e.label}: ${entries[owner].label}의 경로(${anc}) 아래입니다 — 상위 경로를 지우면 함께 `
            + "지워지니 하나만 남기세요");
        }
        return;
      }
    }
  });
  return out;
}
