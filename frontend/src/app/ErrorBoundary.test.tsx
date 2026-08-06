import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ErrorBoundary } from "./ErrorBoundary";

function Boom(): JSX.Element { throw new Error("boom"); }

afterEach(() => vi.restoreAllMocks());

describe("ErrorBoundary", () => {
  it("자식이 던지면 폴백을 보여준다", () => {
    // React 가 경계에서 잡은 에러를 콘솔로 다시 뱉는다 -- 테스트 출력만 조용히 시키고
    // 전역 setup 은 건드리지 않는다(다른 곳의 진짜 경고가 묻히면 안 된다)
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(<ErrorBoundary><Boom /></ErrorBoundary>);
    expect(screen.getByText("화면을 표시하지 못했습니다")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "다시 시도" })).toBeInTheDocument();
  });

  it("던지지 않으면 자식을 그대로 렌더한다", () => {
    render(<ErrorBoundary><p>정상</p></ErrorBoundary>);
    expect(screen.getByText("정상")).toBeInTheDocument();
  });

  it("다시 시도를 누르면 경계가 초기화된다", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    let boom = true;
    function Maybe() { if (boom) throw new Error("boom"); return <p>회복</p>; }
    render(<ErrorBoundary><Maybe /></ErrorBoundary>);
    boom = false;
    await userEvent.click(screen.getByRole("button", { name: "다시 시도" }));
    expect(screen.getByText("회복")).toBeInTheDocument();
  });
});
