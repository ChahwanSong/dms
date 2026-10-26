import { Link } from "react-router-dom";
import { reasonText } from "../../lib/api";
import { kstStamp } from "../../lib/datetime";
import { normTransitions } from "./stageModel";
import { FOCUS_RING } from "./ui";

// 전이 목록(요청 「전이 이력」 카드 + 잡 카드의 「상태 전이 N건」 펼침). 받는 값은 unknown 이다 -- DB 가 신뢰
// 경계라 transitions 가 배열이 아니거나 항목에 to_state 가 없을 수 있다(M5: 무방어 인덱싱 한 번이 화면 전체를
// 죽였다). to_state 가 문자열인 항목만 남긴다.
// `{from} → {to}` 는 span **하나**의 한 텍스트 노드다 -- e2e E6 의 getByText("Succeeded", exact) 가 전이 줄의
// "Planned → Succeeded" 를 배지로 세지 않게(스펙 C1).
// conflictRequestId(2026-10-27): 경로 겹침(path_conflict)으로 끝난 요청이 겹친 상대 요청 id -- 그 전이 줄에 상대 요청 링크를
// 붙인다(전이 행엔 상세 칸이 없어 planner 의 path_conflict 이벤트에서 꺼낸다 -- RequestActivity.conflictRequestId). 상대가 남의
// 요청이면 열었을 때 404 다(비관리자는 자기 요청만 본다) -- id 앞자리는 그래도 「무엇과 겹쳤나」를 말한다.
export function Timeline({ transitions, conflictRequestId = null }: {
  transitions: unknown; conflictRequestId?: string | null;
}) {
  const list = normTransitions(transitions);
  if (!list.length) return <p className="text-ink/70 text-sm">전이 이력이 없습니다</p>;
  return (
    <ol className="space-y-1.5 text-sm">
      {list.map((t, i) => (
        <li key={i} className="flex flex-wrap gap-x-2 gap-y-0.5">
          <span aria-hidden className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-line" />
          <span className="text-ink/70 tabular-nums">{typeof t.at === "string" ? kstStamp(t.at) : "—"}</span>
          <span>{`${t.from_state ?? "—"} → ${t.to_state}`}</span>
          {t.reason_code && <span className="text-bad break-keep">{reasonText(t.reason_code)}</span>}
          {t.reason_code === "path_conflict" && conflictRequestId && (
            <Link to={`/jobs/${encodeURIComponent(conflictRequestId)}`} title={conflictRequestId}
                  className={`text-accent underline ${FOCUS_RING}`}>
              {`겹친 요청 ${conflictRequestId.slice(0, 8)}`}
            </Link>
          )}
          {t.actor && <span className="text-ink/70">({t.actor})</span>}
        </li>
      ))}
    </ol>
  );
}
