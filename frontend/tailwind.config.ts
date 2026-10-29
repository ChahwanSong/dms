import type { Config } from "tailwindcss";

// 큐 흐름 히어로(2026-10-16, 사용자 지시 「애니메이션 색깔 파스텔 흐르는 느낌으로 각 단계별로!」) -- 단계 파스텔은 흐름
// 순서대로 색상환을 걷는다(버터 → 피치 → 코랄 → 라벤더 → 페리윙클 → 아쿠아 → 민트, 사람 관문은 따로 오키드). 면(파스텔)
// 위의 숫자·글자는 짝 잉크(-ink)로만 쓴다: 잉크는 자기 파스텔 위에서도 흰 배경 위에서도 4.5:1 이상이다(실측 4.87~9.93).
// 화면 코드는 hex 를 모른다(theme.test) -- 흐름 리본·후광·오로라도 아래 토큰과 backgroundImage 만 거친다.
const STAGE = {
  batch: "#FCE8A8", planning: "#FFD9BF", pickup: "#FFCFC7", preflight: "#DCCBFF",
  volcano: "#C9D5FF", running: "#BDEBFA", done: "#C8F2DC", confirm: "#F6D0F2",
};
const STAGE_INK = {
  batch: "#8A5A00", planning: "#9A3D12", pickup: "#9F2D20", preflight: "#5B21B6",
  volcano: "#3730A3", running: "#075985", done: "#166534", confirm: "#86198F",
};

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#f5f6f8",            // 페이지 배경 = DS panel 회색(카드가 보더+흰색으로 뜬다)
        surface: "#ffffff", ink: "#333333", muted: "#888888",
        accent: "#1a56db", accenthover: "#1749b8",   // DS primary 블루
        navy: "#0d2b88",                             // 톱바 브랜드 블록
        infobg: "#eef4ff",                           // 연파랑 안내 카드
        panel: "#f5f6f8",                            // 회색 안내 패널
        line: "#e0e2e6",                             // 1px 구분선(그림자 대신 보더 구획)
        ok: "#067647", okbg: "#e7f7ee",              // "정상=초록" 의미 체계 유지
        bad: "#b42318", badbg: "#fee4e2",
        // busy 는 보라→파랑 계열로 오되 accent 와 한 단계 어둡게 구분한다
        // -- 진행 배지가 링크(accent)와 같은 색이면 클릭 가능해 보인다(확정값 ⑤).
        busy: "#1749b8", busybg: "#eef4ff",
        // 운영자 행동 필요(배치 「확인 대기」) -- 주의색(앰버). accent 계열이면 안 된다: 배지가 버튼·링크 색이면
        // 클릭 가능해 보인다(확정값 ⑤), busy 의 연파랑과도 구별돼야 "이미 도는 중" 으로 읽히지 않는다.
        attn: "#b54708", attnbg: "#fef0c7",
        // bg-stage-planning · text-stage-planning-ink … (위 STAGE 주석)
        stage: Object.fromEntries(Object.entries(STAGE).flatMap(([k, v]) => [
          [k, v], [`${k}-ink`, STAGE_INK[k as keyof typeof STAGE_INK]]])),
      },
      borderRadius: {
        card: "0.5rem",   // 카드 12px→8px
        // rounded-lg 는 버튼·인풋 전부에 이미 쓰인다(실측) -- 기본 8px 를 6px 로
        // 오버라이드하면 화면 무접촉으로 DS 질감(버튼·인풋 6px)이 된다.
        lg: "0.375rem",
      },
      boxShadow: {
        soft: "0 1px 2px rgba(16,24,40,.05)",  // 그림자 최소화(보더 중심)
        // 흐름 리본의 안쪽 빛(위 흰 줄 + 아래 옅은 그늘) -- 납작한 띠가 아니라 둥근 관처럼 보이게.
        ribbon: "inset 0 1px 0 rgb(255 255 255 / 0.7), inset 0 -2px 3px rgb(15 23 42 / 0.06)",
        // 역(원)의 들뜬 깊이 -- 카드 그림자(soft)보다 한 단계 위, 퍼짐은 짧게.
        station: "0 1px 2px rgb(16 24 40 / 0.06), 0 6px 14px -6px rgb(16 24 40 / 0.18)",
        // 흐르는 입자의 빛(흰 심 + 번짐)
        glow: "0 0 0 1px rgb(255 255 255 / 0.95), 0 0 6px 2px rgb(255 255 255 / 0.9), 0 0 12px 4px rgb(255 255 255 / 0.45)",
      },
      backgroundImage: {
        // 히어로 바탕 -- 흐름의 첫·가운데·끝 파스텔이 위에서 아주 옅게 번진다(알파 0x30~0x40). 글자 대비는 흰 바탕 기준 그대로.
        aurora: [
          `radial-gradient(55% 140% at 0% 0%, ${STAGE.batch}55 0%, transparent 62%)`,
          `radial-gradient(45% 120% at 48% 0%, ${STAGE.preflight}40 0%, transparent 66%)`,
          `radial-gradient(55% 140% at 100% 0%, ${STAGE.running}55 0%, transparent 62%)`,
          "linear-gradient(180deg, #ffffff 0%, #fcfcfd 100%)",
        ].join(", "),
      },
      keyframes: {
        // 리본: 300% 길이의 반복 그라데이션(A→B→A, 한 주기 = 구간 2배)을 한 주기만큼 민다 -- 끝과 처음이 같은 자리라 이음매가
        // 없고, 멈춘 자리(0)는 A→B 그대로라 움직임 줄이기에서도 단계 순서가 읽힌다.
        "flow-x": { from: { transform: "translateX(-66.6667%)" }, to: { transform: "translateX(0)" } },
        "flow-y": { from: { transform: "translateY(-66.6667%)" }, to: { transform: "translateY(0)" } },
        // 입자: 트랙(구간과 같은 크기)을 자기 길이만큼 민다 -- 점은 트랙 앞끝에 있다. 양 끝은 역 원 밑이라 흐려지며 숨는다.
        "travel-x": {
          "0%": { transform: "translateX(0)", opacity: "0" }, "14%": { opacity: "1" },
          "86%": { opacity: "1" }, "100%": { transform: "translateX(100%)", opacity: "0" },
        },
        "travel-y": {
          "0%": { transform: "translateY(0)", opacity: "0" }, "14%": { opacity: "1" },
          "86%": { opacity: "1" }, "100%": { transform: "translateY(100%)", opacity: "0" },
        },
        // 곡선(확인 관문 고리) 위 입자 -- offset-path 는 그 자리에서 잰 너비로 만든다.
        "travel-path": {
          "0%": { "offset-distance": "0%", opacity: "0" }, "14%": { opacity: "1" },
          "86%": { opacity: "1" }, "100%": { "offset-distance": "100%", opacity: "0" },
        },
        // 건이 있는 역의 숨쉬는 후광 -- 같은 파스텔이 부풀며 사라진다.
        breathe: {
          "0%": { transform: "scale(1)", opacity: "0.7" },
          "70%, 100%": { transform: "scale(1.5)", opacity: "0" },
        },
        // 확인 관문 고리의 점선이 흐름 방향(실행 중 → 확인 대기 → 사전 점검)으로 걷는다 -- 한 주기 = 점선 한 마디(16).
        // stroke-dashoffset 은 합성(컴포지터)되지 않아 도는 동안 매 프레임 스타일·페인트를 부른다(2026-10-16 리뷰 실측) --
        // 화면은 그 다리에 실제 흐름(최근 창 처리량 > 0)이 있을 때만 이 클래스를 붙인다.
        "dash-flow": { from: { "stroke-dashoffset": "16" }, to: { "stroke-dashoffset": "0" } },
        // 고리 줄기(HTML 배경 점선 -- 마디 16px)의 같은 흐름. 실행 중 줄기는 아래로, 사전 점검 줄기는 위로. 리본처럼 한 마디
        // 더 긴 점선 층을 transform 으로 민다(background-position 은 합성되지 않는다) -- 한 마디를 밀면 처음 무늬와 같다.
        "dash-down": { from: { transform: "translateY(0)" }, to: { transform: "translateY(16px)" } },
        "dash-up": { from: { transform: "translateY(16px)" }, to: { transform: "translateY(0)" } },
        // 측면 시트(Dialog side="right", 2026-10-29)가 열릴 때 오른쪽에서 들어온다 -- 닫을 때는 움직임 없음(Radix Presence 가
        // 바로 내린다). motion-safe 에만 붙인다.
        "sheet-in": { from: { transform: "translateX(100%)" }, to: { transform: "translateX(0)" } },
      },
      animation: {
        "flow-x": "flow-x 7s linear infinite",
        "flow-y": "flow-y 7s linear infinite",
        // 입자의 빠르기·시차는 처리량 단계마다 다르다 -- 요소의 --travel-dur·--travel-delay 로 받는다(클래스 조합 금지:
        // purge 가 문자열 조합 클래스를 만들지 않는다).
        "travel-x": "travel-x var(--travel-dur, 3s) linear var(--travel-delay, 0s) infinite",
        "travel-y": "travel-y var(--travel-dur, 3s) linear var(--travel-delay, 0s) infinite",
        "travel-path": "travel-path var(--travel-dur, 3s) linear var(--travel-delay, 0s) infinite",
        breathe: "breathe 2.8s cubic-bezier(0.22, 0.61, 0.36, 1) infinite",
        "dash-flow": "dash-flow 1.2s linear infinite",
        "dash-down": "dash-down 1.2s linear infinite",
        "dash-up": "dash-up 1.2s linear infinite",
        "sheet-in": "sheet-in 150ms ease-out",
      },
    },
  },
  plugins: [],
} satisfies Config;
