import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";
afterEach(() => cleanup());

// jsdom 에 없는 IntersectionObserver 폴리필(전체 작업 무한 스크롤 감시 노드용).
// observe 는 아무것도 하지 않는다 -- 테스트에서 자동 다음-쪽 발화는 없다(그건
// 실제 스크롤 몫). 컴포넌트가 크래시하지 않게만 한다.
if (!("IntersectionObserver" in globalThis)) {
  class IO {
    observe = vi.fn();
    unobserve = vi.fn();
    disconnect = vi.fn();
    takeRecords = () => [];
    root = null; rootMargin = ""; thresholds = [];
  }
  (globalThis as { IntersectionObserver: unknown }).IntersectionObserver = IO;
}
