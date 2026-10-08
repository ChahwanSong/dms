import { Link } from "react-router-dom";
import {
  Ban, BellRing, CircleCheck, CircleHelp, CircleX, Clock, Hourglass, LoaderCircle, TriangleAlert, type LucideIcon,
} from "lucide-react";
import { Card } from "../../components/ui/Card";
import { StatusPill } from "../../components/ui/StatusPill";
import { Button } from "../../components/ui/Button";
import { ApiError, reasonText } from "../../lib/api";
import { kstStamp } from "../../lib/datetime";
import { useNow } from "../../lib/useNow";
import type { DataJob, RequestDetail } from "../../lib/types";
import type { useCancelRequest } from "./useJobs";
import { ConfirmDialog } from "./ConfirmDialog";
import { Elapsed } from "./JobStages";
import { useStageNav } from "./stageNav";
import { msText } from "./format";
import type { JobStagesModel } from "./jobStages";
import { deriveKpi, deriveOutcome, type KpiTile, type OutcomeIcon, type Tone } from "./requestOutcome";
import { LINK_BTN } from "./ui";

// 결과 배너(2026-10-08 재설계 -- 옛 「요청 정보」 카드를 대체). 첫 화면에서 네 가지를 답한다:
// 무슨 일이 있었나(제목 + 요청 pill) · 왜(dl 첫 줄 「사유」) · 무엇을 대상으로(「대상」·「절대경로」) · 다음에
// 무엇을 하나(「다음 할 일」 띠 + 버튼). 아래에 지표 4칸(KPI). 판정은 requestOutcome(순수)이 한다.

const TONE_BORDER: Record<Tone, string> = {
  ok: "border-l-ok", bad: "border-l-bad", busy: "border-l-busy", action: "border-l-attn", neutral: "border-l-line",
};
const TONE_ICON: Record<Tone, string> = {
  ok: "text-ok", bad: "text-bad", busy: "text-busy", action: "text-attn", neutral: "text-ink/70",
};
const TONE_STRIP: Record<Tone, string> = {
  ok: "bg-okbg", bad: "bg-badbg/50", busy: "bg-busybg", action: "bg-attnbg", neutral: "bg-panel",
};
const ICONS: Record<OutcomeIcon, LucideIcon> = {
  check: CircleCheck, x: CircleX, clock: Clock, loader: LoaderCircle, bell: BellRing,
  ban: Ban, hourglass: Hourglass, help: CircleHelp,
};
const KPI_COLS: Record<number, string> = { 1: "sm:grid-cols-1", 2: "sm:grid-cols-2", 3: "sm:grid-cols-3", 4: "sm:grid-cols-4" };

function KpiDl({ tiles }: { tiles: KpiTile[] }) {
  return (
    <dl className={`mt-4 grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-line bg-line ${KPI_COLS[tiles.length] ?? "sm:grid-cols-4"}`}>
      {tiles.map((t, i) => (
        // 홀수 개면 좁은 화면에서 마지막 칸이 두 칸을 차지한다(회색 빈칸이 남지 않게).
        <div key={t.key} className={`min-w-0 bg-surface px-4 py-3 ${tiles.length % 2 === 1 && i === tiles.length - 1 ? "col-span-2 sm:col-span-1" : ""}`}>
          <dt className="text-xs text-ink/70">{t.label}</dt>
          {/* 값은 「120개」·「456 B」처럼 단위를 붙인다 -- 아래 「실행 결과」 타일의 원값("120"·"456 B (456 B)")과
              같은 글자가 되면 정확 일치 단언이 겹친다(스펙 C7). */}
          <dd className="mt-0.5 text-lg font-semibold tabular-nums [overflow-wrap:anywhere]">
            {t.elapsedFrom ? <Elapsed from={t.elapsedFrom} fallback="—" /> : t.value}
          </dd>
          {t.sub && <dd className="text-xs text-ink/70 break-keep">{t.sub}</dd>}
        </div>
      ))}
    </dl>
  );
}

// 컨펌 유효기간. 30초 틱(남은 시간이 분 단위라 충분하다), 1시간 미만이면 주의색, 지났으면 경고 문구.
function ExpiryLine({ job }: { job: DataJob }) {
  const now = useNow(30_000, true);
  const exp = job.preview_expires_at ? Date.parse(job.preview_expires_at) : NaN;
  if (Number.isNaN(exp)) return null;
  const left = exp - now;
  if (left <= 0) {
    return <p className="mt-1 font-medium text-attn break-keep">유효기간이 지났습니다 — 지금 컨펌하면 거부될 수 있습니다</p>;
  }
  return (
    <p className={`mt-1 break-keep ${left < 3_600_000 ? "font-medium text-attn" : "text-ink/70"}`}>
      {`유효기간 ${kstStamp(job.preview_expires_at!)} · ${msText(left) ?? "—"} 남음`}
    </p>
  );
}

export function OutcomeCard({ req, jobs, models, abs, target, cancelRequest }: {
  req: RequestDetail; jobs: DataJob[] | null; models: JobStagesModel[];
  abs: string | null; target: string; cancelRequest: ReturnType<typeof useCancelRequest>;
}) {
  const outcome = deriveOutcome(req, jobs, models);
  const tiles = deriveKpi(req, jobs, models, Date.now());
  const focus = outcome.focus !== null && jobs ? jobs[outcome.focus] : null;
  const nav = useStageNav(focus?.job_id ?? "");
  const Icon = ICONS[outcome.icon];
  const n = outcome.next;
  const actions = n?.actions ?? [];
  const primary = actions[0];
  return (
    <Card className={`border-l-4 ${TONE_BORDER[outcome.tone]}`}>
      <div className="flex flex-wrap items-start gap-3">
        <Icon aria-hidden className={`mt-0.5 h-6 w-6 shrink-0 ${TONE_ICON[outcome.tone]} ${outcome.icon === "loader" ? "motion-safe:animate-spin" : ""}`} />
        <div className="min-w-0 flex-1">
          <h2 className="text-lg font-semibold break-keep [text-wrap:balance]">{outcome.title}</h2>
          {outcome.subtitle && <p className="mt-0.5 text-sm text-ink/70 break-keep">{outcome.subtitle}</p>}
        </div>
        {/* 요청 pill 은 페이지에서 여기 한 번만(e2e E6: Succeeded 배지 = 요청 1 + 잡 N). */}
        <span className="ml-auto shrink-0"><StatusPill state={req.state} /></span>
      </div>
      <dl className="mt-3 grid grid-cols-1 gap-x-4 gap-y-1 text-sm sm:grid-cols-[max-content_minmax(0,1fr)]">
        {/* 사유(사용자 보고 2026-08-16): 실패한 요청에서 사용자가 가장 먼저 찾는
            값이라 dl 의 첫 줄이다(상태 pill 바로 아래 = 상태→사유 순). 소스는
            **요청 자신의** 종단 사유(백엔드가 results 행에서, 없으면 마지막 전이
            에서 싣는다) — 잡의 reason_code 가 아니다: 잡이 만들어지기 전에 거부된
            요청(플래너 어드미션)은 잡이 아예 없어 사유를 실을 곳이 없었다.
            없으면(비종단·사유 없는 종단·구버전 응답) 줄 자체를 그리지 않는다 --
            "—" 도 거짓 표시다. 「사유」 dt 는 페이지에서 여기 하나뿐이다(단계 행의 실패
            문장엔 라벨을 달지 않는다). */}
        {req.reason_code && (<>
          <dt className="text-ink/70">사유</dt>
          <dd className="text-bad break-keep">{reasonText(req.reason_code)}</dd>
        </>)}
        {/* 대상: payload 가 담은 그대로(스토리지:상대경로). 완료된 작업을 볼 때
            화면 어디에도 무엇을 대상으로 돌았는지 없었다(사용자 보고). */}
        <dt className="text-ink/70">대상</dt>
        <dd className="font-mono text-xs break-all">{target}</dd>
        {/* 절대경로는 **지금의** managed_root 로 조합한다 — payload 에 박아 두면
            스토리지 경로가 바뀐 뒤 존재하지 않는 경로를 사실처럼 보인다. 뿌리를
            모르면(비관리자·조회 실패) 줄 자체를 안 그린다(거짓 경로 금지). */}
        {abs !== null && (<>
          <dt className="text-ink/70">절대경로</dt>
          <dd className="font-mono text-xs break-all text-ink/70">{abs}</dd>
        </>)}
      </dl>
      {n && (
        <div className={`mt-4 rounded-lg px-4 py-3 text-sm ${TONE_STRIP[outcome.tone]}`}>
          <p className="text-xs font-semibold text-ink">다음 할 일</p>
          {n.batchNotice ? (
            // 배치 자식은 단건 컨펌을 못 한다(서버 409 batch_child_confirm_via_batch, 2026-10-07) -- 배치 확인 1회가
            // 자식 전부를 대표하고 특권 게이트도 그쪽에 있다. 컨펌 버튼 대신 어디서 확인하는지를 말한다.
            <p className="mt-1 break-keep">
              배치 항목입니다 — 실행 확인은 항목별이 아니라 <Link className="text-accent underline" to={`/admin/batches/${req.batch_id}`}>배치 상세</Link>에서
              배치 단위로 합니다. 배치가 「확인 대기」면 「배치 확인」으로 실행되고, 이미 확인된 배치면 동시 실행 상한만큼씩 차례로 실행됩니다.
            </p>
          ) : n.text ? <p className="mt-1 break-keep">{n.text}</p> : null}
          {n.expiry && focus && <ExpiryLine job={focus} />}
          {n.groupWarning && (
            <p className="mt-2 flex items-start gap-1.5 text-bad break-keep">
              <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
              보조 그룹으로 쓰기 권한을 받은 디렉토리에서는 다른 사용자의 파일도 지워질 수 있습니다.
            </p>
          )}
          {actions.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              {actions.map((a) => {
                const variant = a === primary ? "primary" : "ghost";
                switch (a) {
                  case "confirm":
                    // ConfirmDialog 무변경(트리거 「작업 컨펌」). 이 이름의 버튼은 페이지에 하나뿐이다(관문 줄은 2개 이상일 때만).
                    return focus ? <ConfirmDialog key={a} job={focus} /> : null;
                  case "showPreview":
                    return <Button key={a} variant={variant} onClick={() => nav.requestReveal("previewResult")}>미리보기 결과 보기</Button>;
                  case "liveLog":
                    return outcome.liveTarget ? (
                      <Button key={a} variant={variant}
                              onClick={() => nav.requestReveal(outcome.liveTarget!.stage, outcome.liveTarget!.key)}>진행 중인 로그 보기</Button>
                    ) : null;
                  case "failLog":
                    return outcome.failTarget ? (
                      <Button key={a} variant={variant}
                              onClick={() => nav.requestReveal(outcome.failTarget!.stage, outcome.failTarget!.key)}>실패 지점 로그 보기</Button>
                    ) : null;
                  case "newJob":
                    return <Link key={a} to="/jobs/new" className={LINK_BTN}>새 작업 제출</Link>;
                  case "cancelRequest":
                    return (
                      // 잡이 하나도 없을 때만 요청 단위 취소를 보여준다 — 잡이 있으면 잡 단위 취소가 그 역할을 한다.
                      <Button key={a} variant="ghost" disabled={cancelRequest.isPending}
                              onClick={() => cancelRequest.mutate()}>요청 취소</Button>
                    );
                }
              })}
            </div>
          )}
          {cancelRequest.isError && (
            <p className="text-bad text-sm mt-1">{(cancelRequest.error as ApiError).message}</p>
          )}
        </div>
      )}
      <KpiDl tiles={tiles} />
    </Card>
  );
}
