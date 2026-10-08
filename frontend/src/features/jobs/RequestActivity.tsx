import { useEffect, useId, useRef, useState } from "react";
import { ChevronDown, Info, OctagonAlert, TriangleAlert, type LucideIcon } from "lucide-react";
import { Card } from "../../components/ui/Card";
import { kstStamp } from "../../lib/datetime";
import type { DiagEvent, RequestDetail } from "../../lib/types";
import { Timeline } from "./Timeline";
import { FOCUS_RING } from "./ui";

// 요청 상세 아래쪽 「활동」 영역(2026-10-08 재설계): 요청 전이 이력 + 진단 이벤트. 잡 카드의 단계 구획이 "무엇이
// 언제 어디서" 를 이미 말하므로 원문 기록은 아래로 내렸다(xl 이상 2열).

// 「전이 이력」 h2 는 Card div 의 직계 자식이다 -- RequestDetail.test 가 heading.closest("div") 로 이 카드를 찾고
// 그 안의 listitem 수를 요청 전이 수와 맞춘다. 사이에 div 를 끼우지 않는다.
export function TransitionsCard({ transitions }: { transitions: unknown }) {
  return (
    <Card>
      <h2 className="text-sm font-semibold mb-2">전이 이력</h2>
      <Timeline transitions={transitions} />
    </Card>
  );
}

// I3: stepper.py의 summary_unreadable은 message가 아예 없이 payload={"ref": ref}만
// 있고, terminate_failed는 message=exc.reason_code가 event_type과 완전히 중복돼서
// 실질적으로 payload의 ref가 운영자가 얻을 수 있는 유일한 단서다. payload를 렌더하지
// 않으면 화면에는 event_type 하나만 뜨고 아무 단서도 없다 -- JSON.stringify를 코드
// 블록으로 보여주는 것으로 충분하다(구조를 안다고 가정하지 않는다: dict/array/스칼라/
// null 어떤 모양이 와도 죽지 않아야 한다).
export function EventPayload({ payload }: { payload: unknown }) {
  if (payload === null || payload === undefined) return null;
  let text: string;
  try {
    text = JSON.stringify(payload);
  } catch {
    text = String(payload); // 순환 참조 등 JSON.stringify가 던지는 극단적인 경우의 방어
  }
  if (!text) return null;
  return (
    <pre className="mt-0.5 text-xs text-ink/70 whitespace-pre-wrap break-all">{text}</pre>
  );
}

// 심각도는 모양 + 글자로도 구별한다(색만으로 구분하지 않는다). 색은 기존 규약대로 error 만 bad.
const SEVERITY: Record<string, { Icon: LucideIcon; tone: string; word: string }> = {
  error: { Icon: OctagonAlert, tone: "text-bad", word: "오류" },
  warning: { Icon: TriangleAlert, tone: "text-ink/70", word: "경고" },
  info: { Icon: Info, tone: "text-muted", word: "정보" },
};

// 진단 이벤트 카드. 정보 이벤트만 있으면 기본으로 접는다(정상 sync 도 보조 그룹 재확인 통과 info 가 4건씩 쌓여
// 노이즈다 -- 그 내용은 단계 행 주석이 이미 말한다). 오류·경고가 하나라도 있으면 펼친다. 접혀도 DOM 에 남는다.
export function DiagnosticEvents({ req }: { req: RequestDetail }) {
  // 방어적 정규화: events가 배열이 아니거나(백엔드 오응답) 아예 없으면 빈 배열로
  // 취급한다 -- 배열 아닌 페이로드 하나가 SPA 전체를 흰 화면으로 만든 사고가 있었다.
  const events: DiagEvent[] = Array.isArray(req.events) ? req.events : [];
  const serious = events.some((e) => e?.severity === "error" || e?.severity === "warning");
  const [open, setOpen] = useState(serious);
  const touched = useRef(false);
  const id = useId();
  // 폴링으로 오류·경고가 새로 들어오면(사용자가 직접 접고 펴지 않았다면) 펼친다 -- 처음 모습에 묶여 새 오류를 숨기지 않게.
  useEffect(() => {
    if (serious && !touched.current) setOpen(true);
  }, [serious]);
  if (events.length === 0) return null; // 정상 요청에 빈 카드가 뜨면 노이즈다
  // button 의 자기 텍스트가 정확히 「진단 이벤트」(건수는 자식 span) -- 테스트가 그 텍스트의 closest("div") 로
  // 카드를 찾으므로 h2·button 과 Card 사이에 div 를 끼우지 않는다.
  return (
    <Card>
      <h2 className="text-sm font-semibold">
        <button type="button" aria-expanded={open} aria-controls={id} onClick={() => { touched.current = true; setOpen((v) => !v); }}
                className={`inline-flex items-center gap-2 rounded ${FOCUS_RING}`}>
          진단 이벤트<span className="text-xs font-normal text-ink/70 tabular-nums">{`${events.length}건`}</span>
          <ChevronDown aria-hidden className={`h-4 w-4 text-ink/70 motion-safe:transition-transform ${open ? "rotate-180" : ""}`} />
        </button>
      </h2>
      <div id={id} hidden={!open}>
        <div className="mt-2">
          {req.events_truncated && (
            <p className="text-ink/70 text-xs mb-2">
              최근 100건만 표시됩니다 — 그 이전 이벤트는 보이지 않습니다.
            </p>
          )}
          <ul className="space-y-2 text-sm">
            {events.map((e, i) => {
              const sev = SEVERITY[e?.severity] ?? SEVERITY.info;
              return (
                <li key={e?.id ?? i} className="flex items-start gap-2">
                  <sev.Icon aria-hidden className={`mt-0.5 h-4 w-4 shrink-0 ${sev.tone}`} />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                      <span className="sr-only">{sev.word}</span>
                      <span className="text-ink/70 tabular-nums">{typeof e?.at === "string" ? kstStamp(e.at) : "—"}</span>
                      <span className={e?.severity === "error" ? "text-bad" : "text-ink"}>{e?.event_type}</span>
                      <span className="text-ink/70 text-xs">({e?.component})</span>
                    </div>
                    {e?.message && <p className="mt-0.5 break-keep">{e.message}</p>}
                    <EventPayload payload={e?.payload} />
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      </div>
    </Card>
  );
}

// 진단 이벤트 카드가 없으면 1열(전이 이력 카드가 반쪽 폭으로 남지 않게).
export function ActivitySection({ req }: { req: RequestDetail }) {
  const hasEvents = Array.isArray(req.events) && req.events.length > 0;
  return (
    <div className={hasEvents ? "grid gap-5 xl:grid-cols-2 xl:items-start" : ""}>
      <TransitionsCard transitions={req.transitions} />
      <DiagnosticEvents req={req} />
    </div>
  );
}
