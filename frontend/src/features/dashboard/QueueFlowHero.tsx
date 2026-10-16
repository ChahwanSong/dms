import { useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { ChevronDown, ChevronRight, ChevronUp, CornerUpLeft, Pause } from "lucide-react";
import { HelpTip } from "../../components/ui/HelpTip";
import type {
  QueueControl, QueueFlow, QueueFlowEdge, QueueFlowExitStage, QueuePipeline, QueueStage,
} from "../../lib/types";
import { STAGE_HELP, STAGE_LABEL, waitText, waitTone, type Tone } from "./queueStage";

// 큐 흐름 히어로(2026-10-16, 사용자 지시 「큐 대기가 일종의 파이프라인 이잖아? 계획 대기부터 잡 픽업하고 사전점검하고 ... 이
// 흐름을 단순히 숫자만 카드형태로 보여쥬는게 아니라, 시각적으로 흐름 형식으로 애니메이션화해서 예쁘고 직관적으로」 + 「대시보드의
// 제일 위에 … 애니메이션 색깔 파스텔 흐르는 느낌으로 각 단계별로!」).
//
// 그림의 뜻(지어내지 않는다 -- null ≠ 0):
//   역(원) 숫자   = 지금 그 단계에 있는 건수(pipeline). 모르면 점선 원·「—」·「알 수 없음」.
//   리본         = 단계 순서. 이웃한 두 역의 파스텔이 흐름 방향으로 밀려간다(장식 -- 늘 같은 빠르기).
//   흰 입자      = 최근 창(flow.window_seconds) 동안 그 구간을 지나간 수를 단계화한 것(0 → 없음, 1–2 → 하나 느리게,
//                 3–9 → 둘, 10+ → 셋 빠르게). flow 를 모르면 입자가 없다. Volcano 대기 → 실행 중은 전이로 남지 않아
//                 측정이 없다 -- 실행 중인 잡이 있을 때만 느린 하나.
//   후광         = 건이 있는 역. 색 문턱(waitTone)을 넘으면 테두리 색 + 「주의」/「경고」(sr-only).
//   확인 관문     = 본선 아래 고리: 실행 중(미리보기 끝) → 확인 대기 → 사전 점검(승인 뒤 실행 전 재점검). 배치 held 자식의
//                 같은 경로(running>batch · batch>preflight)도 이 고리로 흐른다 -- 배치 확인도 승인 관문이다.
//   드레인        = stepper 가 움직이는 구간(픽업 → 사전 점검 → Volcano, 실행 중 → 완료·확인)의 리본이 멈추고 흐려진다.
//
// 움직임 규칙: 모든 키프레임은 motion-safe 변형(움직임 줄이기면 정지 -- 그라데이션 방향·화살표가 흐름을 말한다). 움직이는 것은
// 전부 aria-hidden 장식 자식뿐이다 -- 역·HelpTip 자체에 무한 transform 을 걸면 Playwright 의 stable 판정이 끝나지 않는다.
// 한 구간(리본 구간 · 고리의 내려가는/올라가는 다리)의 리본·점선·입자는 한 규칙으로 함께 서고 함께 움직인다: 흐름(flow)을
// 모르면 전부 정지, 드레인이면 stepper 구간만 정지, 고리 점선·입자는 그 다리에 최근 창 흐름이 있을 때만 움직인다(리본은
// 단계 순서 장식이라 흐름을 알면 늘 흐른다). 대시보드가 떠 있는 내내 도는 것은 합성(컴포지터)되는 transform·opacity
// 키프레임뿐이다 -- 합성되지 않는 stroke-dashoffset(고리 점선)·offset-distance(곡선 입자)는 흐름이 있는 다리에만 붙는다
// (2026-10-16 리뷰 실측: 조건 없이 돌던 점선이 메인 스레드를 60fps 로 돌렸다).
// 5초 폴링 리렌더에도 키(역·구간·입자 순번)가 고정이라 DOM 이 유지되고 애니메이션이 처음부터 다시 돌지 않는다. aria-live 없음
// (5초마다 낭독 폭주). 숫자는 역마다 정확히 한 요소(QueueSection.test 의 within(역).getByText 고정점) -- 단계 번호 배지 금지.
// 상한에 찬 흐름(flow.saturated)은 하한이다 -- 흐름에서 나온 숫자(이탈·완료·요약)는 전부 「+」를 붙인다.

type FlowStage = QueueStage | "done";
type LineStage = Exclude<FlowStage, "confirm">;

const DONE_LABEL = "완료";
const DONE_HELP = "최근 5분 동안 성공으로 끝난 잡 수입니다. 실패·거부·취소·만료로 중간에 떠난 잡은 그 단계의 「이탈」로 셉니다.";
const LEGEND_HELP = "역의 숫자는 지금 그 단계에 있는 건수입니다. 리본 위를 흐르는 흰 점은 최근 5분 동안 그 구간을 지나간 "
  + "양입니다 — 많을수록 촘촘하고 빠릅니다. Volcano 대기에서 실행으로 넘어가는 순간은 기록되지 않아 실행 중인 잡이 있을 "
  + "때만 느린 점 하나로 그립니다. 아래 고리는 미리보기를 마친 잡이 확인(승인)을 거쳐 실행 전 재점검으로 돌아가는 길입니다.";

// 본선(왼쪽 → 오른쪽, 좁은 폭에선 위 → 아래) 중 완료 앞까지. 그 뒤 DOM 순서는 확인 관문(실행 중 아래 가지) → 완료다 --
// 좁은 폭의 세로 순서가 곧 DOM 순서이고, lg(1024) 이상에선 격자 자리(확인 = 둘째 줄, 완료 = 첫 줄 끝)로 옮긴다. 가로 배치는
// 원래 xl(1280) 부터였다 -- 1024–1279 에선 세로 레일이 카드 오른쪽을 비워 두어(2026-10-17 사용자 지시로 정리) lg 로 내렸다.
const MAIN: Exclude<LineStage, "done">[] = ["batch", "planning", "pickup", "preflight", "volcano_wait", "running"];

// 정적 클래스 문자열만(purge 가 조합 문자열을 만들지 않는다). quiet = 0 건 역(읽었고 비었다) -- 같은 파스텔을 옅게 해 건이 있는
// 역이 한눈에 떠오르게 한다. 숫자 잉크는 그대로 둔다(옅은 면 위에서 더 진해질 뿐 -- 좁은 폭 숫자 18px 는 「큰 글자」 문턱
// 아래라 4.5:1 이 필요하다). 옅은 면은 흰 바탕 위 반투명 그라데이션이다 -- 면 자체를 반투명으로 칠하면 뒤의 리본이 원을 가로질러
// 비친다.
const SKIN: Record<FlowStage, { fill: string; ink: string; quiet: string }> = {
  batch: { fill: "bg-stage-batch", ink: "text-stage-batch-ink",
           quiet: "bg-surface bg-gradient-to-b from-stage-batch/55 to-stage-batch/55 text-stage-batch-ink" },
  planning: { fill: "bg-stage-planning", ink: "text-stage-planning-ink",
              quiet: "bg-surface bg-gradient-to-b from-stage-planning/55 to-stage-planning/55 "
                + "text-stage-planning-ink" },
  pickup: { fill: "bg-stage-pickup", ink: "text-stage-pickup-ink",
            quiet: "bg-surface bg-gradient-to-b from-stage-pickup/55 to-stage-pickup/55 text-stage-pickup-ink" },
  preflight: { fill: "bg-stage-preflight", ink: "text-stage-preflight-ink",
               quiet: "bg-surface bg-gradient-to-b from-stage-preflight/55 to-stage-preflight/55 "
                 + "text-stage-preflight-ink" },
  volcano_wait: { fill: "bg-stage-volcano", ink: "text-stage-volcano-ink",
                  quiet: "bg-surface bg-gradient-to-b from-stage-volcano/55 to-stage-volcano/55 "
                    + "text-stage-volcano-ink" },
  running: { fill: "bg-stage-running", ink: "text-stage-running-ink",
             quiet: "bg-surface bg-gradient-to-b from-stage-running/55 to-stage-running/55 text-stage-running-ink" },
  done: { fill: "bg-stage-done", ink: "text-stage-done-ink",
          quiet: "bg-surface bg-gradient-to-b from-stage-done/55 to-stage-done/55 text-stage-done-ink" },
  confirm: { fill: "bg-stage-confirm", ink: "text-stage-confirm-ink",
             quiet: "bg-surface bg-gradient-to-b from-stage-confirm/55 to-stage-confirm/55 text-stage-confirm-ink" },
};

// 역 → 다음 역 구간. grad = A→B→A(tailwind.config 의 flow-x 주석), edges = 입자 처리량의 재료(null = 측정 없음),
// stepper = 드레인이면 멈추는 구간(드레인은 JobStepper.run_once 만 멈춘다 -- planner·오케스트레이터·Volcano·사람의 컨펌은
// 그대로 움직인다).
const SEGMENT: Partial<Record<LineStage, { grad: string; edges: QueueFlowEdge[] | null; stepper: boolean }>> = {
  batch: { grad: "from-stage-batch via-stage-planning to-stage-batch", edges: ["batch>planning"], stepper: false },
  planning: { grad: "from-stage-planning via-stage-pickup to-stage-planning", edges: ["planning>pickup"],
              stepper: false },
  pickup: { grad: "from-stage-pickup via-stage-preflight to-stage-pickup", edges: ["pickup>preflight"],
            stepper: true },
  preflight: { grad: "from-stage-preflight via-stage-volcano to-stage-preflight", edges: ["preflight>volcano_wait"],
               stepper: true },
  volcano_wait: { grad: "from-stage-volcano via-stage-running to-stage-volcano", edges: null, stepper: false },
  running: { grad: "from-stage-running via-stage-done to-stage-running", edges: ["running>done"], stepper: true },
};
const LOOP_DOWN: QueueFlowEdge[] = ["running>confirm", "running>batch"];
const LOOP_UP: QueueFlowEdge[] = ["confirm>preflight", "batch>preflight"];

// 확인 관문 고리의 두 다리의 처리량(움직임의 재료). 내려가는 다리(실행 중 → 확인: 미리보기 끝을 stepper 가 기록한다)는
// 드레인이면 0, 올라가는 다리(확인 → 사전 점검: 사람·오케스트레이터의 컨펌 -- 드레인이 멈추지 않는다)는 그대로. 흐름을
// 모르면 둘 다 0 이다(edgeSum). 줄기·점선·입자가 모두 이 값 하나를 본다 -- 한 다리의 반쪽만 멈춘 그림이 생기지 않게.
function loopLegs(flow: QueueFlow | null, paused: boolean): { down: number; up: number } {
  return { down: paused ? 0 : edgeSum(flow, LOOP_DOWN), up: edgeSum(flow, LOOP_UP) };
}
const EXIT_STAGES = new Set<string>(["planning", "pickup", "preflight", "running", "confirm", "batch"]);
const WAIT_STAGES: QueueStage[] = ["batch", "planning", "pickup", "preflight", "volcano_wait", "confirm"];

// 처리량 → 입자 수·한 바퀴 초. 같은 단계 안에서는 개수·빠르기가 그대로라 폴링마다 입자가 다시 태어나지 않는다.
function particlesFor(rate: number): { n: number; dur: number } {
  if (rate >= 10) return { n: 3, dur: 1.9 };
  if (rate >= 3) return { n: 2, dur: 2.8 };
  if (rate >= 1) return { n: 1, dur: 4.2 };
  return { n: 0, dur: 0 };
}
function edgeSum(flow: QueueFlow | null, keys: QueueFlowEdge[]): number {
  if (flow === null) return 0;
  return keys.reduce((a, k) => a + (flow.edges?.[k] ?? 0), 0);
}
function travelStyle(dur: number, i: number, n: number): CSSProperties {
  // 음수 지연 = 마운트 순간 이미 트랙 위에 고르게 퍼져 있다(한꺼번에 출발하지 않는다).
  return { "--travel-dur": `${dur}s`, "--travel-delay": `${-(dur * i) / n}s` } as CSSProperties;
}

// 역 숫자 크기 -- 원 안에 들어가게 자릿수로 줄인다(작은 원 = 좁은 폭, 큰 원 = lg 이상).
function countSize(text: string): string {
  if (text.length <= 2) return "text-lg lg:text-[22px]";
  if (text.length === 3) return "text-base lg:text-lg";
  if (text.length === 4) return "text-sm lg:text-base";
  return "text-xs lg:text-sm";
}
// 좁은 칸(lg 의 ≈96–130px)에서 끝 마디만 홀로 다음 줄로 넘어가지 않게(「…33분」/「20초」, 「확인 대기 배치」/「1」,
// 「Volcano 실행」/「중」) 마지막 공백을 줄바꿈 없는 공백으로 바꾼다. 숫자로 시작하는 끝 마디(시간·건수)와, 낱말이 셋 이상인
// 라벨의 끝 낱말에만. 한 텍스트 노드는 그대로다(정확 일치 단언의 공백 정규화가 줄바꿈 없는 공백도 보통 공백으로 읽는다).
function glueNumberTail(text: string): string {
  return text.replace(/ (\d[^ ]*)$/, "\u00a0$1");
}
function glueLastWord(label: string): string {
  return (label.match(/ /g) ?? []).length >= 2 ? label.replace(/ ([^ ]+)$/, "\u00a0$1") : label;
}

const TONE_RING: Record<Tone, string> = {
  none: "", attn: "outline outline-2 outline-offset-4 outline-attn", bad: "outline outline-2 outline-offset-4 outline-bad",
};
const SUB_TONE: Record<Tone, string> = { none: "text-ink/70", attn: "text-attn", bad: "text-bad" };

// atLeast = 하한(상한에 찬 흐름에서 온 수) -- 「7+」.
function Disc({ stage, count, tone, atLeast = false }: {
  stage: FlowStage; count: number | null; tone: Tone; atLeast?: boolean;
}) {
  const skin = SKIN[stage];
  const text = count === null ? "—" : `${count}${atLeast ? "+" : ""}`;
  return (
    <span className="relative z-10 col-start-1 row-span-2 row-start-1 grid h-11 w-11 shrink-0 place-items-center
                     lg:mt-2 lg:h-14 lg:w-14">
      {count !== null && count > 0 && (
        <span aria-hidden
              className={`absolute inset-0 rounded-full ${skin.fill} scale-125 opacity-40 motion-safe:animate-breathe`} />
      )}
      <span className={`relative grid h-full w-full place-items-center rounded-full ring-4 ring-white shadow-station
                        ${count === null ? "border-2 border-dashed border-ink/25 bg-surface text-ink/45"
                          : count === 0 ? skin.quiet : `${skin.fill} ${skin.ink}`} ${TONE_RING[tone]}`}>
        <span className={`font-bold leading-none tabular-nums ${countSize(text)}`}>{text}</span>
        {tone !== "none" && <span className="sr-only">{tone === "bad" ? "경고" : "주의"}</span>}
      </span>
    </span>
  );
}

// lg(1024–1279)의 칸은 ≈96–130px 라 「Volcano 실행 중」이 한 줄에 못 든다 -- 그 폭의 본선 라벨 줄은 두 줄 자리(40px, 아래 정렬)로
// 높이를 고정해 원·리본(SEG_POS 의 lg 값)이 라벨 줄 수와 상관없이 같은 높이에 선다. 확인 관문 라벨(짧다)은 lg 에서도 한 줄
// (single) -- 고리 바닥(LOOP_Y)이 그 줄 높이에 묶여 있다.
// xl 라벨 줄은 원·리본·고리의 높이 기준(SEG_POS·LOOP_Y 의 24px)이라 늘 한 줄이어야 한다 -- 줄바꿈을 막고(nowrap) 물음표
// 버튼 오른쪽의 투명 여백(아이콘 16 둘레 4px)을 음수 여백으로 접어 가로 몫을 줄인다. 가장 좁은 xl(뷰포트 1280 + 일반
// 스크롤바 15px = 안쪽 1265, 칸 129px · 라벨 줄 117px)에서 가장 긴 「Volcano 실행 중」(글자 93 + 틈 2 + 버튼 24)이 두 줄로
// 접혀 24px 줄을 넘치고 원 링에 붙었다(2026-10-16 리뷰). 지금은 그 폭에서 115px 로 줄 안이고, 더 좁은 글꼴 대체가 와도
// 한 줄로 남아 칸 자신의 좌우 여백(px-1.5) 쪽으로 고르게 넘칠 뿐이다.
function StationLabel({ label, help, single = false }: { label: string; help: string; single?: boolean }) {
  return (
    <div className={`col-start-2 row-start-1 flex min-h-[1.5rem] min-w-0 items-center gap-0.5 lg:justify-center
                     ${single ? "lg:h-6" : "lg:h-10 lg:items-end xl:h-6 xl:items-center"}`}>
      <span className="text-[13px] font-medium leading-tight text-ink break-keep lg:text-center xl:whitespace-nowrap">
        {glueLastWord(label)}
      </span>
      <HelpTip label={`${label} 설명`} head={label} help={help} className="lg:-mr-1" />
    </div>
  );
}

// 리본 한 구간(역 중심 → 다음 역 중심). pos = 자리 클래스(좁은 폭 세로 / lg 이상 가로), round = 끝 모양(이어 붙이는 통과
// 리본은 위끝이 각져야 앞 구간의 둥근 끝을 덮어 잘록한 이음매가 생기지 않는다).
// still = 흐름(flow)을 모른다 -- 리본도 멈춘다(모르는 흐름을 움직임으로 꾸미지 않는다). 그라데이션 방향은 그대로 남는다.
function Segment({ grad, rate, paused, still, pos, round = "rounded-full" }: {
  grad: string; rate: number; paused: boolean; still: boolean; pos: string; round?: string;
}) {
  const { n, dur } = paused || still ? { n: 0, dur: 0 } : particlesFor(rate);
  return (
    <span aria-hidden className={`pointer-events-none absolute ${pos}`}>
      <span className={`absolute inset-0 overflow-hidden ${round} ${paused ? "opacity-50 saturate-50" : ""}`}>
        <span className={`absolute left-0 top-0 h-[300%] w-full bg-gradient-to-b bg-[length:100%_66.6667%] bg-repeat-y
                          lg:h-full lg:w-[300%] lg:bg-gradient-to-r lg:bg-[length:66.6667%_100%] lg:bg-repeat-x ${grad}
                          ${paused || still ? "" : "motion-safe:animate-flow-y lg:motion-safe:animate-flow-x"}`} />
        <span className={`absolute inset-0 ${round} shadow-ribbon`} />
      </span>
      {paused ? (
        <span className="absolute inset-0 grid place-items-center">
          <Pause className="h-2.5 w-2.5 fill-ink/50 text-ink/50" strokeWidth={3} />
        </span>
      ) : (
        <span className="absolute inset-0 grid place-items-center motion-safe:hidden">
          <ChevronDown className="h-3 w-3 text-ink/40 lg:hidden" strokeWidth={3} />
          <ChevronRight className="hidden h-3 w-3 text-ink/40 lg:block" strokeWidth={3} />
        </span>
      )}
      {Array.from({ length: n }, (_, i) => (
        <span key={i} style={travelStyle(dur, i, n)}
              className="absolute inset-0 hidden motion-safe:block motion-safe:animate-travel-y
                         lg:motion-safe:animate-travel-x">
          <span className="absolute left-1/2 top-0 h-[7px] w-[7px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-white
                           shadow-glow lg:left-0 lg:top-1/2" />
        </span>
      ))}
    </span>
  );
}

// 곡선 위 입자(확인 관문 고리) -- 곡선은 그 자리에서 잰 좌표의 path() 다. 입자는 컨테이너 왼쪽 위(0,0)에 두어 path 좌표의
// 원점이 컨테이너와 같게 한다. rate 는 loopLegs 가 드레인·모름을 이미 접은 값이다 -- 0 이면 입자 자체가 없다(offset-distance
// 키프레임은 합성되지 않으므로 흐름 없는 다리에서 돌지 않게).
function PathParticles({ path, rate }: { path: string; rate: number }) {
  const { n, dur } = particlesFor(rate);
  return (
    <>
      {Array.from({ length: n }, (_, i) => (
        <span key={i} style={{ ...travelStyle(dur, i, n), offsetPath: `path("${path}")`, offsetRotate: "0deg" }}
              className="absolute left-0 top-0 hidden h-[7px] w-[7px] rounded-full bg-white shadow-glow
                         motion-safe:block motion-safe:animate-travel-path" />
      ))}
    </>
  );
}

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(el.clientWidth);
    if (typeof ResizeObserver !== "function") return;     // jsdom -- 고리를 그리지 않는다(장식)
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

type Lines = { stat: { count: number; oldest_seconds: number | null } | null; tone: Tone; sub: string; extra: string[] };

function stationLines(stage: QueueStage, pipeline: QueuePipeline | null): Lines {
  const stat = pipeline?.[stage] ?? null;
  const tone = stat && stat.count > 0 ? waitTone(stage, stat.oldest_seconds) : "none";
  // 0 건이면 「없음」, 모르면 「알 수 없음」 -- 둘을 같은 「—」로 접지 않는다(null ≠ 0).
  let sub: string;
  if (stat === null) sub = "알 수 없음";
  else if (stat.count === 0) sub = stage === "running" ? "없음" : "대기 없음";
  else sub = `${stage === "running" ? "가장 긴" : "가장 오래"} ${waitText(stat.oldest_seconds)}`;
  const extra: string[] = [];
  if (pipeline?.confirm && stage === "confirm" && pipeline.confirm.count > 0) {
    // 역 아래 칸은 좁다 -- 「가장 빠른 만료까지 …」는 세 줄로 접혔다. 여기서는 「다음 만료」가 같은 뜻이다.
    const sec = pipeline.confirm.soonest_expiry_seconds;
    if (sec !== null) extra.push(sec === 0 ? "곧 만료" : `다음 만료 ${waitText(sec)} 후`);
  }
  if (pipeline?.batch && stage === "batch" && typeof pipeline.batch.held === "number" && pipeline.batch.held > 0) {
    extra.push(`미리보기 마친 항목 ${pipeline.batch.held}`);
  }
  if (pipeline?.batch && stage === "batch" && pipeline.batch.preview_ready_batches > 0) {
    extra.push(`확인 대기 배치 ${pipeline.batch.preview_ready_batches}`);
  }
  if (pipeline?.running && stage === "running" && pipeline.running.includes_volcano_wait) {
    extra.push("Volcano 대기 포함");
  }
  return { stat, tone, sub, extra };
}

function TextLines({ stage, lines, flow }: { stage: QueueStage; lines: Lines; flow: QueueFlow | null }) {
  const exits = flow !== null && EXIT_STAGES.has(stage) ? flow.exits?.[stage as QueueFlowExitStage] ?? 0 : 0;
  return (
    <>
      <p className={`col-start-2 text-xs leading-snug break-keep lg:mt-2.5 lg:text-center ${SUB_TONE[lines.tone]}`}>
        {glueNumberTail(lines.sub)}
      </p>
      {lines.extra.map((t) => (
        <p key={t} className={`col-start-2 text-xs leading-snug break-keep lg:mt-0.5 lg:text-center ${SKIN[stage].ink}`}>
          {glueNumberTail(t)}
        </p>
      ))}
      {exits > 0 && flow !== null && (
        <p className="col-start-2 text-[11px] leading-snug text-ink/70 break-keep lg:mt-0.5 lg:text-center">
          {`최근 ${waitText(flow.window_seconds)} 이탈 ${exits}${flow.saturated ? "+" : ""}`}
        </p>
      )}
    </>
  );
}

// 역 li 공통 -- 좁은 폭: [원 | 글자] 격자(원이 라벨·sub 두 줄에 걸친다), lg 이상: 세로로 쌓인 가운데 정렬(라벨 → 원 → 글자).
const LI = "relative grid grid-cols-[2.75rem_minmax(0,1fr)] content-start gap-x-3 lg:flex lg:flex-col lg:items-center "
  + "lg:gap-0 lg:px-1.5";
// 리본 자리: 좁은 폭은 원 중심(22px)에서 다음 li 원 중심까지(li 높이 + ol gap 1rem) 세로, lg 이상은 원 중심(라벨 줄 + 여백 8 +
// 반지름 28 - 리본 반폭 7 = lg 69px · xl 53px -- 라벨 줄이 lg 40 · xl 24)에서 오른쪽 이웃 칸 중심까지(칸 너비 = 100%) 가로.
const SEG_POS = "left-[15px] top-[22px] h-[calc(100%+1rem)] w-3.5 lg:left-1/2 lg:top-[69px] lg:h-3.5 lg:w-full "
  + "xl:top-[53px]";

// 고리 줄기(lg 이상): 사전 점검·실행 중 칸의 글자 밑에서 칸 바닥(+ 줄 간격)까지 -- 아래 줄 확인 관문 고리의 다리가 여기서 이어진다
// (윗줄 높이는 가장 긴 칸이 정하므로 다리를 고리 쪽에서만 그리면 짧은 칸 밑에 빈틈이 남았다). 실행 중 줄기는 내려가고(시작 점),
// 사전 점검 줄기는 올라간다(화살촉). 점선은 고리 SVG 와 같은 마디(6 + 10)를 배경으로 그려 같은 빠르기로 흐른다 -- 한 마디(16px)
// 더 긴 점선 층을 잘라 낸 창 안에서 transform 으로 민다(합성된다). moving = 그 다리에 흐름이 있다(loopLegs).
function LoopStub({ dir, moving }: { dir: "up" | "down"; moving: boolean }) {
  return (
    <span aria-hidden className="pointer-events-none relative hidden lg:-mb-3 lg:mt-2 lg:block lg:min-h-[14px] lg:w-2
                                 lg:flex-1">
      <span className="absolute inset-0 rounded-t-full bg-stage-confirm" />
      <span className="absolute inset-y-0 left-1/2 w-[1.75px] -translate-x-1/2 overflow-hidden">
        <span className={`absolute inset-x-0 -top-4 bottom-0 bg-gradient-to-b from-stage-confirm-ink/45 from-[37.5%]
                          to-transparent to-[37.5%] bg-[length:100%_16px] bg-repeat-y
                          ${!moving ? "" : dir === "down" ? "motion-safe:animate-dash-down" : "motion-safe:animate-dash-up"}`} />
      </span>
      {dir === "down"
        ? <span className="absolute -top-0.5 left-1/2 h-2 w-2 -translate-x-1/2 rounded-full bg-stage-confirm-ink/50" />
        : <ChevronUp className="absolute -top-2 left-1/2 h-3.5 w-3.5 -translate-x-1/2 text-stage-confirm-ink/80"
                     strokeWidth={2.5} />}
    </span>
  );
}

function LineStation({ stage, pipeline, flow, paused }: {
  stage: Exclude<LineStage, "done">; pipeline: QueuePipeline | null; flow: QueueFlow | null; paused: boolean;
}) {
  const lines = stationLines(stage, pipeline);
  const seg = SEGMENT[stage];
  let rate = 0;
  if (seg) {
    // 측정 없는 구간(Volcano 대기 → 실행 중)은 실행 중인 잡이 있을 때만 느린 하나 -- flow 를 모르면 아무것도.
    rate = seg.edges === null ? (flow !== null && (pipeline?.running?.count ?? 0) > 0 ? 1 : 0) : edgeSum(flow, seg.edges);
  }
  return (
    <li data-testid={`queue-stage-${stage}`} data-tone={lines.tone} className={LI}>
      {seg && <Segment grad={seg.grad} rate={rate} paused={paused && seg.stepper} still={flow === null}
                       pos={SEG_POS} />}
      <StationLabel label={STAGE_LABEL[stage]} help={STAGE_HELP[stage]} />
      <Disc stage={stage} count={lines.stat === null ? null : lines.stat.count} tone={lines.tone} />
      <TextLines stage={stage} lines={lines} flow={flow} />
      {stage === "running" && <LoopStub dir="down" moving={loopLegs(flow, paused).down > 0} />}
      {stage === "preflight" && <LoopStub dir="up" moving={loopLegs(flow, paused).up > 0} />}
    </li>
  );
}

// 고리 기하(lg 이상): 확인 관문 li 는 사전 점검·Volcano 대기·실행 중 세 칸에 걸친다 -- 다리는 칸 중심(1/6·5/6), 바닥은 확인 역
// 중심 높이(위 여백 4 + 라벨 24(lg 에서도 한 줄 -- StationLabel single) + 여백 8 + 반지름 28).
const LOOP_Y = 64;
const LOOP_R = 20;

function ConfirmStation({ pipeline, flow, paused }: {
  pipeline: QueuePipeline | null; flow: QueueFlow | null; paused: boolean;
}) {
  const lines = stationLines("confirm", pipeline);
  const [ref, w] = useWidth<HTMLLIElement>();
  const { down, up } = loopLegs(flow, paused);
  const xl = w / 6, xr = (5 * w) / 6, xc = w / 2, y = LOOP_Y, r = LOOP_R;
  // 다리 위끝(y 0)은 위 줄 사전 점검·실행 중 칸의 고리 줄기(LoopStub)에 이어진다.
  const loop = `M ${xr} 0 V ${y - r} Q ${xr} ${y} ${xr - r} ${y} H ${xl + r} Q ${xl} ${y} ${xl} ${y - r} V 0`;
  // 두 다리는 따로 움직이므로(loopLegs) 점선도 두 path 다. 이음매(xc)는 확인 역 원 밑이라 보이지 않는다.
  const legDown = `M ${xr} 0 V ${y - r} Q ${xr} ${y} ${xr - r} ${y} H ${xc}`;
  const legUp = `M ${xc} ${y} H ${xl + r} Q ${xl} ${y} ${xl} ${y - r} V 0`;
  // 좁은 폭의 가지: 실행 중 원 밑(본선 오른쪽 가장자리 x 29, 이 행 위 20px -- 줄 간격 16 + 실행 중 칸의 원 아래 여백)에서
  // 갈라져 들여쓴 확인 역 원의 왼쪽 끝(x 48, 원 중심 높이 22)으로 휘어 든다. li 좌표. 실행 중 칸이 두 줄뿐이면 시작점이 그 원
  // 밑(원이 위에 그려진다)이라 원에서 갈라져 나오는 모양이 된다.
  const branch = "M 29 -20 C 33 2 34 22 48 22";
  return (
    <li ref={ref} data-testid="queue-stage-confirm" data-tone={lines.tone}
        className={`${LI} pl-12 lg:col-span-3 lg:col-start-4 lg:row-start-2 lg:pl-0 lg:pt-1`}>
      {/* 좁은 폭: 본선은 이 가지 행 옆을 지나 완료로 이어진다(실행 중 → 완료 구간의 나머지). */}
      {/* 실행 중 구간(위 li)은 이 행의 원 중심 높이(22)에서 둥글게 끝난다 -- 통과 리본은 그 둥근 끝(15~22)부터 각진 위끝으로
          덮고 완료 역 중심까지 간다. 색은 완료 쪽(민트) 한 가지라 멈춘 그림에서도 앞 구간의 끝색(민트)과 이어진다. */}
      <Segment grad="from-stage-done via-stage-done to-stage-done" rate={edgeSum(flow, ["running>done"])}
               paused={paused} still={flow === null} round="rounded-b-full"
               pos="left-[15px] top-[15px] h-[calc(100%+1rem+7px)] w-3.5 lg:hidden" />
      <span aria-hidden className="pointer-events-none absolute left-0 top-0 lg:hidden">
        <svg className="absolute -top-5 left-0 overflow-visible" width="52" height="44" viewBox="0 -20 52 44">
          <path d={branch} fill="none" className="stroke-stage-confirm" strokeWidth="6" strokeLinecap="round" />
          <path d={branch} fill="none"
                className={`stroke-stage-confirm-ink/50 ${down > 0 ? "motion-safe:animate-dash-flow" : ""}`}
                strokeWidth="1.5" strokeDasharray="4 4" strokeLinecap="round" />
        </svg>
        <PathParticles path={branch} rate={down} />
      </span>
      {/* lg 이상: 실행 중에서 내려와 확인 역을 지나 사전 점검으로 올라가는 고리. 너비를 재기 전(0)엔 그리지 않는다. */}
      {w > 0 && (
        <span aria-hidden className="pointer-events-none absolute left-0 top-0 hidden lg:block"
              style={{ width: w, height: y + 4 }}>
          <svg className="absolute inset-0 overflow-visible" width={w} height={y + 4}>
            <path d={loop} fill="none" className="stroke-stage-confirm" strokeWidth="8" strokeLinecap="round"
                  strokeLinejoin="round" />
            {([[legDown, down], [legUp, up]] as const).map(([d, rate], i) => (
              <path key={i} d={d} fill="none"
                    className={`stroke-stage-confirm-ink/45 ${rate > 0 ? "motion-safe:animate-dash-flow" : ""}`}
                    strokeWidth="1.75" strokeDasharray="6 10" strokeLinecap="round" />
            ))}
          </svg>
          <PathParticles path={legDown} rate={down} />
          <PathParticles path={legUp} rate={up} />
        </span>
      )}
      <StationLabel label={STAGE_LABEL.confirm} help={STAGE_HELP.confirm} single />
      <Disc stage="confirm" count={lines.stat === null ? null : lines.stat.count} tone={lines.tone} />
      <TextLines stage="confirm" lines={lines} flow={flow} />
      <p className="col-start-2 mt-0.5 flex items-center gap-1 text-xs leading-snug text-stage-confirm-ink break-keep
                    lg:absolute lg:left-[calc(16.667%+6px)] lg:top-[24px] lg:mt-0 lg:block lg:w-[calc(33.333%-34px)]
                    lg:text-center lg:text-[10px] xl:left-[calc(16.667%+12px)] xl:top-[26px] xl:w-[calc(33.333%-46px)]
                    xl:text-[11px]">
        <CornerUpLeft aria-hidden className="h-3.5 w-3.5 shrink-0 lg:hidden" />
        {/* lg 이상에선 고리 안 좁은 자리라 두 줄(낱말 사이에서) -- 좁은 폭에선 한 줄. */}
        <span className="lg:block">승인되면</span>
        <span className="lg:block">사전 점검으로</span>
      </p>
    </li>
  );
}

function DoneStation({ flow }: { flow: QueueFlow | null }) {
  // 흐름을 모르면 「—」와 「읽지 못함」 -- 「알 수 없음」 낱말은 단계 칸(pipeline)의 모름에만 쓴다(옛 응답 모양에서 흐름 키가
  // 없을 뿐인데 「알 수 없」 문구가 뜨면, 단계를 다 읽은 상태의 「모름 없음」 판정이 흐려진다).
  const sub = flow === null ? "읽지 못함"
    : `최근 ${waitText(flow.window_seconds)}${flow.saturated ? " · 하한값" : ""}`;
  return (
    <li data-testid="queue-stage-done" data-tone="none" className={`${LI} lg:col-start-7 lg:row-start-1`}>
      <StationLabel label={DONE_LABEL} help={DONE_HELP} />
      <Disc stage="done" count={flow === null ? null : flow.done} tone="none" atLeast={flow?.saturated === true} />
      <p className="col-start-2 text-xs leading-snug text-ink/70 break-keep lg:mt-2.5 lg:text-center">{sub}</p>
    </li>
  );
}

/** 머리줄 요약 「대기 N · 실행 M · 최근 5분 완료 K」 -- 한 텍스트 노드(대시보드의 정확 일치 「대기」·「실행 중」 타일과 겹치지
 *  않게). 모르는 단계가 섞이면 대기는 하한(+), 흐름을 모르면 완료를 뺀다. 아무것도 모르면 null. PodGroup 을 못 읽어 실행 수에
 *  Volcano 대기가 섞였으면(includes_volcano_wait) 그 사실을 실행 수 옆에 적는다 -- 역 안의 같은 표기만으로는 맨 위 요약이
 *  과대값을 확정값처럼 말했다(2026-10-16 리뷰). */
export function flowSummary(pipeline: QueuePipeline | null, flow: QueueFlow | null): string | null {
  const parts: string[] = [];
  const known = WAIT_STAGES.map((s) => pipeline?.[s] ?? null).filter((s) => s !== null);
  if (known.length > 0) {
    const n = known.reduce((a, s) => a + s.count, 0);
    parts.push(`대기 ${n}${known.length < WAIT_STAGES.length ? "+" : ""}`);
  }
  if (pipeline?.running) {
    parts.push(`실행 ${pipeline.running.count}${pipeline.running.includes_volcano_wait ? "(Volcano 대기 포함)" : ""}`);
  }
  if (flow !== null) parts.push(`최근 ${waitText(flow.window_seconds)} 완료 ${flow.done}${flow.saturated ? "+" : ""}`);
  // 좁은 폭에서 줄은 「 · 」 뒤에서만 바뀐다 -- 마디 안은 줄바꿈 없는 공백(375 에서 「(Volcano 대기 포」/「함)」으로 갈렸다).
  // 한 텍스트 노드는 그대로다(정확 일치 단언의 공백 정규화가 줄바꿈 없는 공백도 보통 공백으로 읽는다).
  return parts.length > 0 ? parts.map((p) => p.replace(/ /g, "\u00a0")).join("\u00a0· ") : null;
}

export function FlowLegend() {
  return <HelpTip label="흐름 그림 설명" head="흐름 그림 읽는 법" help={LEGEND_HELP} />;
}

export function QueueFlowHero({ pipeline, flow, control }: {
  pipeline: QueuePipeline | null; flow: QueueFlow | null; control: QueueControl | null;
}) {
  const paused = control?.drain === true;
  return (
    <div className="relative overflow-hidden rounded-xl bg-aurora px-4 py-5 ring-1 ring-inset ring-line/70
                    lg:px-5 lg:pb-6 lg:pt-6">
      <ol aria-label="요청 처리 흐름"
          className="relative flex flex-col gap-4 lg:grid lg:grid-cols-7 lg:gap-x-0 lg:gap-y-3">
        {MAIN.map((s) => <LineStation key={s} stage={s} pipeline={pipeline} flow={flow} paused={paused} />)}
        <ConfirmStation key="confirm" pipeline={pipeline} flow={flow} paused={paused} />
        <DoneStation key="done" flow={flow} />
      </ol>
    </div>
  );
}
