import { useParams } from "react-router-dom";
import { Card } from "../../components/ui/Card";
import { StatusPill } from "../../components/ui/StatusPill";
import { ApiError, reasonText } from "../../lib/api";
import { buildPillVariant, isTerminal } from "../../lib/jobState";
import { formatDuration, spanMs } from "../../lib/duration";
import { kstStampOrDash } from "../../lib/datetime";
import { useBuild, useBuildLog } from "./useBuilds";

// 콘텐츠 컬럼. 폭은 제한하되(로그·메타가 화면 끝까지 늘어지면 읽기 어렵다) **왼쪽
// 기준선**이다 -- mx-auto 를 쓰던 것은 이 앱에서 빌드 화면들뿐이라, 이력에서
// 「상세」로 들어오면 글줄이 혼자 가운데로 튀어 다른 화면처럼 읽혔다(사용자 지적).
// 이제 빌드하기·이력·상세 셋 다 왼쪽에서 시작한다.
const COLUMN = "w-full max-w-3xl";

// 메타 한 줄(dt/dd 짝). 목록에서 뺀 것들이 **여기로 모인다** -- 열을 지우기만 하고
// 상세에 안 넣으면 밀도를 낮춘 게 아니라 정보를 잃은 것이다.
function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-3">
      <dt className="w-24 shrink-0 text-muted">{label}</dt>
      <dd className="min-w-0 break-all">{children}</dd>
    </div>
  );
}

export function BuildDetail() {
  const { buildId = "" } = useParams();
  const q = useBuild(buildId);
  const b = q.data;
  const isActive = b !== undefined && !isTerminal(b.state);
  const logQ = useBuildLog(buildId, isActive);
  // "지금"은 마지막 성공 조회 시각이다 -- 같은 데이터가 돌아오면 재렌더가 없어
  // Date.now() 로는 경과가 멈춘 것처럼 보인다(BuildsPage 와 같은 이유).
  const spent = b === undefined ? null
    : spanMs(b.created_at, isActive ? q.dataUpdatedAt : b.finished_at);

  return (
    <section className={`${COLUMN} space-y-4`}>
      <header>
        <h1 className="text-2xl font-bold">빌드 {buildId.slice(0, 8)}</h1>
        <p className="text-muted mt-1">이 빌드의 대상·결과와 전체 로그입니다</p>
      </header>
      {q.isLoading ? (
        <p className="text-muted">불러오는 중…</p>
      ) : q.isError ? (
        <p className="text-bad">{(q.error as ApiError).message}</p>
      ) : (
        <>
          <Card className="space-y-3 text-sm">
            <div className="flex items-center gap-3">
              <StatusPill state={b?.state ?? "—"} variant={b ? buildPillVariant(b.state) : undefined} />
              {/* 슬라이스 21 §3: Pending 은 적합성 확인(프리플라이트 프로브, 최대
                  DMS_BUILD_PREFLIGHT_TIMEOUT_SECONDS=180s)을 포함한다 -- 별도 상태
                  기계는 만들지 않는다. 실패는 어차피 고유 사유 코드로 드러난다. */}
              {b?.state === "Pending" && (
                <span className="text-muted">적합성 확인(프리플라이트) 포함 — 최대 약 3분</span>
              )}
            </div>
            {/* 사유는 목록에선 한 줄로 잘린다 -- 전문의 집이 여기다. 자르지 않고,
                메타 dl 안에 섞지도 않는다: 실패했다면 이 화면에서 가장 먼저 읽혀야
                할 한 줄이다. null(사유 모름)이면 아예 그리지 않는다. */}
            {b?.reason_code && (
              <p className="text-bad font-medium">{reasonText(b.reason_code)}</p>
            )}
            <dl className="space-y-2">
              {/* 로컬 소스 빌드는 소스가 경로다. 옛 git 시절 행(git_ref !== "local")
                  은 저장소 URL·브랜치가 그대로 실린다 -- 라벨은 중립인 「소스」로. */}
              <Row label="소스">
                <span className="font-mono">{b?.source_path ?? "—"}</span>
                {b && b.git_ref !== "local" ? ` (${b.git_ref})` : null}
              </Row>
              <Row label="commit">
                {/* -dirty 접미는 미커밋 변경 포함 빌드의 표시다 -- 자르면 정보가
                    사라지므로 전체를 그대로 둔다(등폭·select-all 로 복사 배려). */}
                <span className="font-mono select-all">{b?.commit_sha ?? "—"}</span>
              </Row>
              <Row label="이미지">
                {b && (b.images ?? []).length > 0 ? (b.images ?? []).join(", ") : "—"}
              </Row>
              <Row label="노드">{b?.node_name ?? "—"}</Row>
              {/* 배포 때 손으로 옮기는 값 -- airgap 이라 clipboard API 를 못 쓰므로
                  클릭 한 번에 전체 선택되는 등폭 텍스트로 둔다(BuildsPage 와 같다). */}
              <Row label="태그">
                <span className="font-mono select-all">{b?.tag ?? "—"}</span>
              </Row>
              <Row label="생성 시각">{kstStampOrDash(b?.created_at)}</Row>
              <Row label="종료 시각">{kstStampOrDash(b?.finished_at)}</Row>
              {/* 경과(진행 중)·소요(종단). 종단인데 finished_at 이 없으면 "—"다 --
                  지금 시각을 끝으로 삼아 이미 끝난 빌드의 시간을 불리지 않는다.
                  "지금"은 마지막 성공 조회 시각(3s 폴링)이라 최대 3초 뒤처진다. */}
              <Row label={isActive ? "경과 시간" : "소요 시간"}>
                {spent === null ? "—" : formatDuration(spent)}
              </Row>
            </dl>
          </Card>
          <Card>
            <h2 className="font-medium mb-2">로그</h2>
            {logQ.isLoading ? (
              <p className="text-muted">불러오는 중…</p>
            ) : logQ.isError ? (
              <p className="text-bad">{(logQ.error as ApiError).message}</p>
            ) : logQ.data?.log ? (
              <pre className="overflow-x-auto text-xs whitespace-pre-wrap">{logQ.data.log}</pre>
            ) : (
              <p className="text-muted">로그가 아직 없습니다</p>
            )}
          </Card>
        </>
      )}
    </section>
  );
}
