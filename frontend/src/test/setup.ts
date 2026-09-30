import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";
afterEach(() => cleanup());

// 최신 Node 는 실험 기능인 전역 localStorage(파일 백엔드)를 먼저 깔아 jsdom 의 Storage 를
// 가린다 -- --localstorage-file 없이 돌면 메서드가 없는 껍데기라("localStorage.setItem is not
// a function") 사이드바 접힘 상태(lib/navState) 테스트가 죽는다. 테스트용 메모리 Storage 로
// 바꾼다(브라우저 런타임과 무관 -- 운영 코드는 접근 실패를 try/catch 로 견딘다).
function memoryStorage(): Storage {
  const m = new Map<string, string>();
  return {
    get length() { return m.size; },
    clear: () => m.clear(),
    getItem: (k: string) => (m.has(k) ? m.get(k)! : null),
    key: (i: number) => [...m.keys()][i] ?? null,
    removeItem: (k: string) => { m.delete(k); },
    setItem: (k: string, v: string) => { m.set(k, String(v)); },
  };
}
for (const name of ["localStorage", "sessionStorage"] as const) {
  const current = (globalThis as Record<string, unknown>)[name] as Storage | undefined;
  if (typeof current?.setItem !== "function") {
    Object.defineProperty(globalThis, name, { value: memoryStorage(), configurable: true, writable: true });
  }
}

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
