import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props { children: ReactNode }
interface State { error: Error | null }

/** 렌더 중 던진 예외를 잡아 화면 하나만 대체한다. 이것이 없으면 느슨한 백엔드
 *  페이로드 하나가 SPA 전체를 흰 화면으로 만든다(슬라이스 9 에서 실제로 겪었다). */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 서버로 보내지 않는다 -- 수집기가 없다. 콘솔이 유일한 단서다.
    console.error("render crash:", error, info.componentStack);
  }

  render(): ReactNode {
    if (!this.state.error) return this.props.children;
    return (
      <section className="space-y-3">
        <h1 className="text-lg font-semibold">화면을 표시하지 못했습니다</h1>
        <p className="text-muted text-sm">
          이 화면을 그리는 중 오류가 발생했습니다. 다시 시도하거나 다른 화면으로 이동하세요.
        </p>
        <button
          className="rounded-lg border border-black/10 px-3 py-2 text-sm"
          onClick={() => this.setState({ error: null })}
        >
          다시 시도
        </button>
      </section>
    );
  }
}
