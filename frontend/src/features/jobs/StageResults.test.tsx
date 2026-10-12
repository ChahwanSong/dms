// rm 미리보기 결과(2026-10-12): drm --dryrun 이 삭제 요약을 찍지 않아 「삭제 대상」이 늘 「모름」이었다 -- 러너가 walk 항목
// 수를 싣게 됐으니 그 수가 무엇을 센 것인지(대상 경로 포함 합계)를 말하고, 모르면(옛 잡) 목록이 있는 곳을 말한다.
import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { PreviewResult } from "./StageResults";

test("rm 미리보기: 삭제 대상 수 + 무엇을 센 수인지, 크기는 「—」(도구가 바이트를 보고하지 않음)", () => {
  render(<PreviewResult job={{ operation: "rm" }} summary={{ returncode: 0, files: 6, bytes: null }}
                        failedAt={false} headingId="h" />);
  expect(screen.getByText("삭제 대상").nextElementSibling).toHaveTextContent("6");
  expect(screen.getByText("크기").nextElementSibling).toHaveTextContent("—");
  expect(screen.getByText(/대상 경로 자신과 그 아래 파일·디렉터리·링크의 합계입니다/)).toBeInTheDocument();
});

test("rm 미리보기 0 은 「모름」이 아니라 「대상 경로를 찾지 못함」(walk 는 빈 디렉터리도 대상 자신을 세 1)", () => {
  render(<PreviewResult job={{ operation: "rm" }} summary={{ returncode: 0, files: 0, bytes: null }}
                        failedAt={false} headingId="h" />);
  expect(screen.getByText("삭제 대상").nextElementSibling).toHaveTextContent("0");
  expect(screen.getByText(/대상 경로를 찾지 못했습니다\(오타·이미 삭제·권한\) — 지울 것이 없습니다/)).toBeInTheDocument();
  expect(screen.queryByText(/개수를 읽지 못한/)).toBeNull();
  expect(screen.queryByText(/합계입니다/)).toBeNull();
});

test("실패 시점 결과(failedAt)에는 rm 설명 줄이 없다 — 도구가 돌지 않았을 수 있다", () => {
  render(<PreviewResult job={{ operation: "rm" }} summary={{ returncode: null, files: null, bytes: null }}
                        failedAt headingId="h" />);
  expect(screen.getByText("미리보기 결과 (실패 시점)")).toBeInTheDocument();
  expect(screen.queryByText(/개수를 읽지 못한|합계입니다|찾지 못했습니다/)).toBeNull();
});

test("개수를 못 읽은 옛 rm 미리보기는 「모름」 + 목록이 있는 곳", () => {
  render(<PreviewResult job={{ operation: "rm" }} summary={{ returncode: 0, files: null, bytes: null }}
                        failedAt={false} headingId="h" />);
  expect(screen.getByText("삭제 대상").nextElementSibling).toHaveTextContent("모름");
  expect(screen.getByText(/개수를 읽지 못한 미리보기입니다 — 아래 「미리보기」 단계 출력\(stdout\.log\)의 'Walked N items' 줄이 지울 항목 수/))
    .toBeInTheDocument();
});

test("sync 미리보기에는 rm 설명 줄이 없다", () => {
  render(<PreviewResult job={{ operation: "sync" }} summary={{ returncode: 0, files: 3, bytes: 10 }}
                        failedAt={false} headingId="h" />);
  expect(screen.getByText("복사 대상")).toBeInTheDocument();
  expect(screen.queryByText(/대상 경로 자신과/)).toBeNull();
});
