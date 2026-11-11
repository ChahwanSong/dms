import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";
import { agoText, kstStamp } from "../../lib/datetime";
import { useNow } from "../../lib/useNow";
import { clusterStatusMeta } from "./k8sTerms";
import { K8S_CLUSTERS_PATH } from "./K8sTabs";
import { freshnessWarns } from "./pvLocation";
import type { ClusterFreshness } from "./types";

// k8s 연동 화면들이 함께 쓰는 작은 잎 부품. 화면 파일마다 같은 표기를 다시 짓지 않게 한 곳에 둔다.

/** 필터 select 한 칸(첫 옵션 「X 전체」 관용) — PV 탐색·동기화 이력·사용량 분석 필터 줄이 같은 모양이다. */
export const SELECT_FIELD = "max-w-[14rem] rounded-lg border border-line bg-surface px-2 py-1.5 text-sm";

/** 「n분 전」 — 15초 틱(useNow)으로 혼자 다시 그린다(폴링 사이에도 숫자가 늙는다). 모르면 empty 문구(기본 「—」).
 *  절대 시각(KST)은 title 로. offsetMs = 서버 시계 - 브라우저 시계(개요 응답 now 로 잰 값) — 브라우저 시계가 틀려도 거짓 나이를
 *  만들지 않게. */
export function Ago({ iso, empty = "—", offsetMs = 0, className = "" }: {
  iso: string | null | undefined; empty?: string; offsetMs?: number; className?: string;
}) {
  const now = useNow(15_000, true);
  if (!iso) return <span className={className}>{empty}</span>;
  return <span className={className} title={kstStamp(iso)}>{agoText(iso, now + offsetMs)}</span>;
}

/** 작은 꼬리표(종류·출처·권한 수준). 점(dot)을 앞에 달지 않는다(디자인 규약) — 글자와 면 색만. */
export function Tag({ children, tone = "neutral", title }: {
  children: ReactNode; tone?: "neutral" | "info" | "warn" | "bad" | "ok"; title?: string;
}) {
  const cls = {
    neutral: "bg-panel text-ink/80", info: "bg-infobg text-accent", warn: "bg-attnbg text-attn",
    bad: "bg-badbg text-bad", ok: "bg-okbg text-ok",
  }[tone];
  return (
    <span title={title}
          className={`inline-flex items-center whitespace-nowrap rounded-md px-1.5 py-0.5 text-[11px] font-medium ${cls}`}>
      {children}
    </span>
  );
}

/** 긴 식별자(pvc-uuid·API 주소·경로) — 등폭 + 아무 데서나 줄바꿈(좁은 칸을 뚫지 않게). */
export function Mono({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <span className={`font-mono text-[12px] [overflow-wrap:anywhere] ${className}`}>{children}</span>;
}

/** 관리 화면(PV 탐색·사용자 권한) 표의 「클러스터 없음」 — 등록은 「클러스터」 탭에서만 한다(2026-11-11 개요에서 분리). 링크
 *  글자는 「클러스터 탭」(탭 링크 「클러스터」와 이름이 겹치지 않게). */
export function NoClustersText() {
  return (
    <>
      등록된 클러스터가 없습니다 — <Link to={K8S_CLUSTERS_PATH} className="text-accent hover:underline">클러스터 탭</Link>에서
      클러스터를 등록하세요.
    </>
  );
}

/** 가입 신원 경고(2026-10-26 보안 리뷰): 권한이 「계정 아이디 == RoleBinding 의 ID」로 묶여, 인증 메일이 실제로 가지
 *  않으면(stub — 코드를 화면에 보여 줌, open — 인증 꺼짐) 아직 계정이 없는 사람의 아이디로 누구나 먼저 가입해 그 사람의 PV 권한을
 *  가져갈 수 있다. 기동을 막지는 않는다(테스트베드는 stub 로 돈다) — 운영자가 보게만 한다. 개요(첫 화면)와 클러스터 탭(등록 버튼이
 *  있는 화면 — 위험이 생기는 순간이 클러스터 등록이다, deploy/README §6-2) 둘 다 그린다(2026-11-11 탭 분리). 긴 env 이름은 375 에서
 *  테두리를 뚫지 않게 아무 데서나 줄바꿈한다. */
export function SignupGuardNote({ guard }: { guard: string | undefined }) {
  if (guard !== "stub" && guard !== "open") return null;
  return (
    <div role="note" aria-label="가입 신원 경고" className="rounded-lg border border-attn/40 bg-attnbg/50 px-4 py-3 text-sm text-attn">
      <p className="font-medium [overflow-wrap:anywhere]">
        {guard === "stub"
          ? "가입 인증 메일이 실제로 발송되지 않습니다(stub — 인증번호를 화면에 보여 줍니다)"
          : "가입 인증이 꺼져 있습니다(DMS_ACCOUNT_VERIFICATION_REQUIRED=false)"}
      </p>
      <p className="mt-1 text-ink/80">
        k8s 연동에서는 계정 아이디가 곧 RoleBinding 의 ID 라, 아직 DMS 계정이 없는 사람의 아이디로 누구나 먼저 가입하면 그 사람의 PV
        권한을 가져갑니다. 운영에서는 <Link to="/admin/mail" className="text-accent hover:underline">메일 설정</Link>에서 Knox 릴레이를
        켜 <span className="font-mono">아이디@회사 도메인</span> 메일로 본인을 확인하게 하세요(테스트베드라면 무시해도 됩니다).
      </p>
    </div>
  );
}

const NO_CLUSTERS_PLACE = "등록된 클러스터가 없습니다 — 「k8s 연동 → 클러스터」에서 클러스터를 등록하세요.";

/** 스냅숏 기준 시각 줄 — PV 탐색·사용자 권한은 「마지막 성공 동기화 기준」 데이터라, 그 기준을 클러스터마다 보인다.
 *  synced_at null = 한 번도 성공하지 않음(모름 — 「없음」과 다르다), stale = 2×주기보다 오래됨.
 *  「클러스터 없음」 기본 문구는 위치 글이다 — 단일 작업 화면(LocationField — 일반 사용자도 본다)에도 그려져 관리 화면 링크를 걸지
 *  않는다. 관리 화면(PV 탐색·사용자 권한)은 표 아래에 링크 있는 NoClustersText 를 따로 그리므로 empty={null} 로 이 줄을 끈다 —
 *  같은 지시가 한 화면에 두 문구로 겹치지 않게(2026-11-11 리뷰).
 *  줄바꿈 금지는 「n분 전 성공」에만 건다(2026-11-11): 클러스터 하나의 글 전체(이름 · 오래됨 · 이후 변경은… · 지연)를 nowrap 으로
 *  묶으면 flex 항목이 그 폭 아래로 줄지 못해 390 폭에서 본문을 넘고, 이름이 10자 남짓만 돼도 페이지가 가로로 스크롤됐다. 경고
 *  꼬리는 낱말 사이에서, 긴 이름(최대 63자)은 아무 데서나 접힌다. */
export function FreshnessLine({ clusters, empty = NO_CLUSTERS_PLACE }: {
  clusters: ClusterFreshness[]; empty?: ReactNode | null;
}) {
  if (clusters.length === 0) {
    return empty === null ? null : <p className="text-sm text-ink/70">{empty}</p>;
  }
  return (
    <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink/70">
      <span className="font-medium text-ink/80">스냅숏 기준</span>
      {clusters.map((c) => {
        // 오래됐거나 지연·실패면 경고색(2026-10-26 (c) -- 막지 않고 경고만, pvLocation.freshnessWarns 와 같은 조건)
        const tone = c.synced_at === null ? "text-bad" : freshnessWarns(c) ? "text-attn" : "text-ink/80";
        return (
          <span key={c.name} className={`min-w-0 [overflow-wrap:anywhere] ${tone}`}>
            <span className="font-medium">{c.name}</span>{" "}
            {c.synced_at === null
              ? "아직 동기화 안 됨(모름)"
              : <><span className="whitespace-nowrap"><Ago iso={c.synced_at} /> 성공</span>{c.stale ? " · 오래됨" : ""}{freshnessWarns(c) ? " · 이후 변경은 반영 안 됐을 수 있음" : ""}</>}
            {c.status !== "ok" && c.synced_at !== null && ` · ${clusterStatusMeta(c.status).label}`}
          </span>
        );
      })}
    </p>
  );
}

/** 정의 목록 한 줄(상세 창) — 라벨 열은 max-content, 값 열은 minmax(0,1fr) 라 긴 토큰이 칸을 밀지 않는다. */
export function DefList({ children }: { children: ReactNode }) {
  return <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">{children}</dl>;
}
export function DefRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="contents">
      <dt className="text-ink/70">{label}</dt>
      <dd className="min-w-0 [overflow-wrap:anywhere]">{children}</dd>
    </div>
  );
}

/** 표 위 요약 숫자 한 칸(조밀한 타일). null 은 「—」(모름). to 를 주면 타일 전체가 그 화면으로 가는 링크다(개요의 「클러스터」 →
 *  클러스터 탭) — 링크임은 꺾쇠 아이콘과 hover 테두리로 보이고, 링크 이름은 타일 글자(라벨·값·부제) 그대로다. */
export function Stat({ label, value, sub, to }: { label: string; value: string; sub?: ReactNode; to?: string }) {
  const body = (
    <>
      <div className="flex items-center justify-between gap-1 text-xs text-ink/70">
        <span>{label}</span>
        {to && <ChevronRight aria-hidden="true" className="h-3.5 w-3.5 shrink-0 text-ink/50" />}
      </div>
      <div className="mt-0.5 text-xl font-semibold tabular-nums">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-ink/70">{sub}</div>}
    </>
  );
  const box = "min-w-0 rounded-card border border-line bg-surface px-4 py-3";
  if (to) {
    return (
      <Link to={to} className={`${box} block transition-colors hover:border-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent`}>
        {body}
      </Link>
    );
  }
  return <div className={box}>{body}</div>;
}
