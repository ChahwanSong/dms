import { Component, type ErrorInfo, type ReactNode } from "react";

// Catch-all render guard. Without a boundary, any thrown render error (e.g. a
// DMS field arriving as an object and hitting React #31) unmounts the entire
// SPA — the operator is left staring at a blank page with no way back except a
// manual reload. This converts that into a recoverable card. "다시 시도" resets
// the boundary, which remounts the subtree; component-local state that triggered
// the crash (an open modal, a selected row) is reset on remount, so the retry
// path normally lands back in a working console rather than re-crashing.
interface Props {
  children: ReactNode;
}
interface State {
  error: Error | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Surface for diagnosis; the portal has no remote error sink.
    console.error("UI render error:", error, info?.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="error-boundary">
          <h3>화면 렌더링 오류</h3>
          <p className="muted">
            화면을 그리는 중 오류가 발생했습니다. 다시 시도하거나 페이지를 새로고침하세요.
          </p>
          <pre className="error-boundary-detail">{this.state.error.message}</pre>
          <div className="error-boundary-actions">
            <button onClick={() => this.setState({ error: null })}>다시 시도</button>
            <button className="ghost" onClick={() => window.location.reload()}>
              새로고침
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
