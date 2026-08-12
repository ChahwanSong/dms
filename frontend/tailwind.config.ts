import type { Config } from "tailwindcss";
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
      },
      borderRadius: {
        card: "0.5rem",   // 카드 12px→8px
        // rounded-lg 는 버튼·인풋 전부에 이미 쓰인다(실측) -- 기본 8px 를 6px 로
        // 오버라이드하면 화면 무접촉으로 DS 질감(버튼·인풋 6px)이 된다.
        lg: "0.375rem",
      },
      boxShadow: { soft: "0 1px 2px rgba(16,24,40,.05)" },  // 그림자 최소화(보더 중심)
    },
  },
  plugins: [],
} satisfies Config;
