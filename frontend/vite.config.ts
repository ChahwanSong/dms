import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8000" } },
  build: { outDir: "dist" },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    // e2e(Playwright, frontend/e2e/*.spec.ts)를 vitest가 집어삼키지 않도록 명시한다
    // -- vitest 기본 include 는 **/*.{test,spec}.* 라 spec 파일이 어디 있든 위험하다.
    // 현 49개 테스트 파일은 전부 이 글롭과 일치한다(ts 5 + tsx 44, 실측).
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
