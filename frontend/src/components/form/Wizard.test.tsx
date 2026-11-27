import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { createPortal } from "react-dom";
import { MemoryRouter, Route, Routes, useLocation, useNavigate, useNavigationType } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { useWizard, WizardActionBar, WizardShell, WizardStepCard, WizardStepper } from "./Wizard";
import type { WizardStep } from "./Wizard";
import {
  buttonsWithoutType, goStep, next, nextButton, prevButton, reasonLine, stepCard, stepHeading,
} from "../../test/wizard";

// 제출 위저드 공용 골격(2026-11-26 단계별 등록)의 계약 -- 화면과 무관한 시험용 4단계 위저드(첫째·둘째·셋째·검토)로 스테퍼 aria·
// 도달 규칙·Enter·포커스·URL·더블클릭·type 불변식을 고정한다. 두 화면(SubmitJob·BatchCreate)의 단계·게이트는 각 화면 시험이 맡는다.

type K = "a" | "b" | "c" | "d";

interface HarnessProps {
  blocked?: Partial<Record<K, boolean>>;
  initial?: K;
  invalid?: boolean;            // 둘째 단계의 「틀린 칸」에 aria-invalid
  nudge?: boolean;              // 둘째 단계 맨 위 「빈 필수 칸」에 data-wizard-nudge(빨강 없는 표지)
  destructive?: boolean;
  onSubmit?: (via: string) => void;
  onRender?: (k: K) => void;
}

function Harness({ blocked: init = {}, initial = "a", invalid = false, nudge = false, destructive = false, onSubmit = () => {},
                   onRender }: HarnessProps) {
  const [blocked, setBlocked] = useState<Record<K, boolean>>({ a: false, b: false, c: false, d: false, ...init });
  const [name, setName] = useState("");
  const [agree, setAgree] = useState(false);
  const anyBlocked = blocked.a || blocked.b || blocked.c;
  const steps: WizardStep<K>[] = [
    { key: "a", title: "첫째", value: "A값", blocked: blocked.a, reason: "첫째 이유" },
    { key: "b", title: "둘째", value: name === "" ? null : name, blocked: blocked.b, reason: "둘째 이유" },
    { key: "c", title: "셋째", value: null, blocked: blocked.c, reason: "셋째 이유" },
    { key: "d", title: "검토", value: null, blocked: anyBlocked, reason: anyBlocked ? "앞 단계를 고치세요" : null },
  ];
  const w = useWizard({ steps, initial });
  onRender?.(w.current);
  const toggle = (k: K) => setBlocked((b) => ({ ...b, [k]: !b[k] }));
  return (
    <>
      <form onSubmit={(e) => { e.preventDefault(); onSubmit("form"); }}>
        <WizardShell {...w.shellProps}>
          <WizardStepper wizard={w} />
          <WizardStepCard wizard={w} lede="설명 한 줄" chips={<span>칩</span>}>
            {w.current === "a" && <input aria-label="첫째 칸" />}
            {w.current === "b" && (<>
              <input aria-label="빈 필수 칸" data-wizard-nudge={nudge || undefined} />
              <input aria-label="이름" value={name} onChange={(e) => setName(e.target.value)} />
              <input aria-label="틀린 칸" aria-invalid={invalid || undefined} />
              <input aria-label="제안 칸" list="harness-opts" />
              <datalist id="harness-opts"><option value="alice" /></datalist>
              <input aria-label="검색 칸" data-wizard-enter="stay" />
              <input type="checkbox" aria-label="동의" checked={agree} onChange={(e) => setAgree(e.target.checked)} />
              <textarea aria-label="메모" />
              <input aria-label="1행 경로" data-row="0" data-col="path" />
              <input aria-label="2행 경로" data-row="1" data-col="path" />
              {createPortal(<input aria-label="시트 칸" />, document.body)}
            </>)}
            {w.current === "d" && <input aria-label="검토 칸" />}
          </WizardStepCard>
          <WizardActionBar wizard={w} doneHint="제출하면 이동합니다." onCancel={() => onSubmit("cancel")}
            submit={{ label: "제출", disabled: anyBlocked, variant: destructive ? "destructive" : "primary", describedBy: "harness-hints",
                      onClick: () => onSubmit("click") }}
            strip={w.current === "d" ? "되돌릴 수 없는 작업입니다." : undefined}
            details={w.current === "d" ? <ul id="harness-hints"><li>힌트 한 줄</li></ul> : undefined} />
        </WizardShell>
      </form>
      <button type="button" onClick={() => toggle("b")}>둘째 막기</button>
      <button type="button" onClick={() => toggle("c")}>셋째 막기</button>
      <button type="button" onClick={() => w.jump("c")}>셋째로 점프</button>
    </>
  );
}

// 지금 URL 의 검색 문자열과 마지막 이동 종류. 뒤로·앞으로 가기는 숨은 버튼(역할 질의에는 안 잡힌다)으로 흉내 낸다.
function Probe() {
  const loc = useLocation();
  const nav = useNavigationType();
  const navigate = useNavigate();
  return (
    <div hidden>
      <span data-testid="search">{loc.search}</span><span data-testid="nav">{nav}</span>
      <button type="button" data-testid="back" onClick={() => navigate(-1)} />
      <button type="button" data-testid="forward" onClick={() => navigate(1)} />
      <button type="button" data-testid="push-a" onClick={() => navigate("?step=a")} />
    </div>
  );
}
const search = () => new URLSearchParams(screen.getByTestId("search").textContent ?? "");
const navType = () => screen.getByTestId("nav").textContent;
const back = () => fireEvent.click(screen.getByTestId("back"));
const forward = () => fireEvent.click(screen.getByTestId("forward"));

function renderHarness(props: HarnessProps = {}, entry = "/w") {
  const onSubmit = vi.fn();
  const utils = render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/w" element={<><Harness onSubmit={onSubmit} {...props} /><Probe /></>} />
      </Routes>
    </MemoryRouter>,
  );
  return { ...utils, onSubmit };
}

const stepper = () => screen.getByRole("navigation", { name: "진행 단계" });
const stepButton = (title: string) => within(stepper()).getByRole("button", { name: new RegExp(`^${title}`) });

afterEach(() => vi.restoreAllMocks());

test("구조 — 현재 단계 aria-current, 카드 표지·「n단계 / N」, 바 상태 줄, 모든 버튼 type=button", async () => {
  const { container } = renderHarness();
  expect(within(stepper()).getAllByRole("button")).toHaveLength(4);
  const first = stepButton("첫째");
  expect(first).toHaveAttribute("aria-current", "step");
  expect(first).not.toHaveAttribute("aria-disabled");
  expect(first).toHaveAccessibleName("첫째 A값 (현재 단계)");   // 현재 단계도 방문한 단계라 값이 붙는다
  // lg 미만은 현재 단계만 이름이 보인다 -- 나머지는 hidden 이 아니라 sr-only 라 접근 이름에 남는다
  expect(stepButton("둘째")).toHaveAccessibleName("둘째");
  expect(within(stepButton("둘째")).getByText("둘째")).toHaveClass("sr-only", "lg:not-sr-only", "lg:truncate");
  expect(within(first).getByText("첫째")).not.toHaveClass("sr-only");
  // 카드: data-wizard-card 표지, aria-label·aria-labelledby 없음(단계 제목이 필드 라벨 부분 일치에 걸리지 않게)
  expect(stepCard()).not.toHaveAttribute("aria-label");
  expect(stepCard()).not.toHaveAttribute("aria-labelledby");
  expect(stepHeading()).toHaveTextContent("첫째");
  expect(within(stepCard()).getByText("1단계 / 4")).toBeInTheDocument();
  expect(within(stepCard()).getByText("설명 한 줄")).toBeInTheDocument();
  expect(within(stepCard()).getByText("칩")).toBeInTheDocument();
  expect(container.querySelector("[data-wizard-shell]")).not.toBeNull();
  expect(reasonLine()).toHaveTextContent("다음: 둘째");
  expect(nextButton()).not.toHaveAttribute("aria-disabled");
  expect(prevButton()).toHaveAttribute("aria-disabled", "true");
  // type 불변식 -- 단계마다(검토의 제출·지름길·고치기 포함)
  for (const title of ["둘째", "셋째", "검토"]) {
    expect(buttonsWithoutType(container)).toHaveLength(0);
    await next(title);
  }
  expect(buttonsWithoutType(container)).toHaveLength(0);
});

test("「다음」 → 다음 단계 제목으로 포커스, 지난 단계는 완료(✓) + 값", async () => {
  renderHarness();
  await next("둘째");
  expect(stepHeading()).toHaveFocus();
  expect(stepButton("첫째")).toHaveAccessibleName("첫째 A값 (완료)");
  expect(stepButton("둘째")).toHaveAttribute("aria-current", "step");
  // 값은 방문했거나 지난 단계만 -- 셋째·검토는 값이 없다
  await userEvent.type(screen.getByLabelText("이름"), "bob");
  expect(stepButton("둘째")).toHaveAccessibleName("둘째 bob (현재 단계)");
});

test("막힌 「다음」은 aria-disabled + 이유가 설명 — 눌러도 단계 그대로, aria-invalid 칸이 없으면 포커스도 그대로", async () => {
  renderHarness({ blocked: { a: true } });
  expect(nextButton()).toHaveAttribute("aria-disabled", "true");
  expect(nextButton()).toHaveAccessibleDescription("첫째 이유");
  expect(reasonLine()).toHaveTextContent("첫째 이유");
  await userEvent.click(nextButton());
  expect(stepHeading()).toHaveTextContent("첫째");
  expect(nextButton()).toHaveFocus();
  // 이유 줄 강조(움직임은 motion-safe 에만, 굵기는 늘)
  expect(reasonLine()).toHaveClass("font-semibold");
  expect(reasonLine()!.querySelector("span")!.className).toContain("motion-safe:animate-nudge");
  // 뒤 단계는 잠김 -- 눌러도 동작 없음
  for (const t of ["둘째", "셋째", "검토"]) {
    expect(stepButton(t)).toHaveAttribute("aria-disabled", "true");
    expect(stepButton(t)).toHaveAccessibleName(`${t} (앞 단계를 먼저 마치세요)`);
  }
  await userEvent.click(stepButton("셋째"));
  expect(stepHeading()).toHaveTextContent("첫째");
});

test("막힌 「다음」을 누르면 카드 안 첫 aria-invalid 칸으로 포커스", async () => {
  renderHarness({ blocked: { b: true }, invalid: true });
  await next("둘째");
  await userEvent.click(nextButton());
  expect(stepHeading()).toHaveTextContent("둘째");
  expect(screen.getByLabelText("틀린 칸")).toHaveFocus();
});

test("막힌 「다음」 — 빨강 없는 「빈 필수 칸」 표지(data-wizard-nudge)도 포커스 대상이고, 오류 칸과 함께 있으면 문서 순서의 첫 칸", async () => {
  // 아직 비운 필수 칸(예: 배치 rm 자동 실행의 「되돌릴 수 없음」 동의 체크): aria-invalid 없이 처음부터 빨갛게 칠하지 않지만 막힌
  // 「다음」은 그리로 보낸다.
  const { unmount } = renderHarness({ blocked: { b: true }, nudge: true });
  await next("둘째");
  expect(screen.getByLabelText("빈 필수 칸")).not.toHaveAttribute("aria-invalid");
  await userEvent.click(nextButton());
  expect(stepHeading()).toHaveTextContent("둘째");
  expect(screen.getByLabelText("빈 필수 칸")).toHaveFocus();
  unmount();
  // 둘 다 있으면 위에 있는 칸(여기선 표지가 오류 칸보다 위)
  renderHarness({ blocked: { b: true }, nudge: true, invalid: true });
  await next("둘째");
  await userEvent.click(nextButton());
  expect(screen.getByLabelText("빈 필수 칸")).toHaveFocus();
});

test("막힘이 풀리면 상태 줄이 「다음: <이름>」 — 「다음」이 다시 움직인다", async () => {
  renderHarness({ blocked: { b: true } });
  await next("둘째");
  expect(reasonLine()).toHaveTextContent("둘째 이유");
  await userEvent.click(screen.getByRole("button", { name: "둘째 막기" }));
  expect(reasonLine()).toHaveTextContent("다음: 셋째");
  await next("셋째");
});

test("도달 규칙 — 현재 앞 단계가 나중에 막혀도 끌어내리지 않는다(빨간 점·이전·다음·「n단계에서 고치기」)", async () => {
  renderHarness();
  await next("둘째");
  await next("셋째");
  await userEvent.click(screen.getByRole("button", { name: "둘째 막기" }));
  // 셋째에 머물고, 현재 단계는 잠김이 아니다
  expect(stepHeading()).toHaveTextContent("셋째");
  expect(stepButton("셋째")).toHaveAttribute("aria-current", "step");
  expect(stepButton("셋째")).not.toHaveAttribute("aria-disabled");
  expect(stepButton("둘째")).toHaveAccessibleName("둘째 (고칠 것이 있음)");
  // 「다음」은 현재(셋째) 게이트만 본다
  await next("검토");
  expect(screen.getByRole("button", { name: "제출" })).toBeDisabled();
  expect(reasonLine()).toHaveTextContent("앞 단계를 고치세요");
  await userEvent.click(prevButton());
  expect(stepHeading()).toHaveTextContent("셋째");
  await next("검토");
  await userEvent.click(screen.getByRole("button", { name: "2단계에서 고치기" }));
  expect(stepHeading()).toHaveTextContent("둘째");
  expect(stepHeading()).toHaveFocus();
});

test("잠김은 현재보다 뒤이면서 현재부터 센 첫 막힘보다 뒤인 단계에만 붙는다", async () => {
  renderHarness({ blocked: { c: true } });
  // 첫째에서: 첫 막힘은 셋째 -- 둘째·셋째는 갈 수 있고 검토만 잠김
  expect(stepButton("둘째")).not.toHaveAttribute("aria-disabled");
  expect(stepButton("셋째")).not.toHaveAttribute("aria-disabled");
  expect(stepButton("검토")).toHaveAttribute("aria-disabled", "true");
  await goStep("셋째");
  expect(stepHeading()).toHaveTextContent("셋째");
  expect(stepButton("검토")).toHaveAttribute("aria-disabled", "true");
  // 뒤로는 늘 간다
  await goStep("첫째");
  expect(stepHeading()).toHaveTextContent("첫째");
});

test("완료한 단계를 스테퍼로 다시 연다 — 제목 포커스", async () => {
  renderHarness();
  await next("둘째");
  await next("셋째");
  await goStep("첫째");
  expect(stepHeading()).toHaveTextContent("첫째");
  expect(stepHeading()).toHaveFocus();
  expect(stepButton("둘째")).toHaveAccessibleName("둘째 (완료)");
});

test("Enter — 텍스트 칸에서 유효하면 다음 단계(기본 동작은 막는다), 막혔으면 그 자리", async () => {
  renderHarness({ blocked: { b: true } });
  expect(fireEvent.keyDown(screen.getByLabelText("첫째 칸"), { key: "Enter" })).toBe(false);
  expect(stepHeading()).toHaveTextContent("둘째");
  const nameField = screen.getByLabelText("이름");
  nameField.focus();
  expect(fireEvent.keyDown(nameField, { key: "Enter" })).toBe(false);
  expect(stepHeading()).toHaveTextContent("둘째");
  expect(nameField).toHaveFocus();     // aria-invalid 칸이 없으면 입력 자리를 잃지 않는다
});

test("Enter — 마지막 단계에서는 아무 일도 없다(제출하지 않는다 — 암묵 제출도 막힌다)", async () => {
  const { onSubmit } = renderHarness();
  await goStep("검토");
  const field = screen.getByLabelText("검토 칸");
  await userEvent.click(field);
  await userEvent.keyboard("{Enter}");
  expect(onSubmit).not.toHaveBeenCalled();
  expect(stepHeading()).toHaveTextContent("검토");
  expect(fireEvent.keyDown(field, { key: "Enter" })).toBe(false);
});

test("Enter — 한글 조합 확정은 건드리지 않는다(preventDefault 도 안 함)", () => {
  renderHarness();
  const field = screen.getByLabelText("첫째 칸");
  expect(fireEvent.keyDown(field, { key: "Enter", keyCode: 229 })).toBe(true);
  expect(fireEvent.keyDown(field, { key: "Enter", isComposing: true })).toBe(true);
  expect(stepHeading()).toHaveTextContent("첫째");
});

test("Enter — list 칸·검색 칸(data-wizard-enter=stay)·체크박스는 막기만 한다: 단계·포커스 그대로", async () => {
  renderHarness();
  await next("둘째");
  for (const label of ["제안 칸", "검색 칸", "동의"]) {
    const el = screen.getByLabelText(label);
    el.focus();
    expect(fireEvent.keyDown(el, { key: "Enter" })).toBe(false);
    expect(stepHeading()).toHaveTextContent("둘째");
    expect(el).toHaveFocus();
  }
  expect(screen.getByLabelText("동의")).not.toBeChecked();
});

test("Enter — textarea·버튼은 기본 동작, 포털(시트) 안 칸은 셸이 무시한다", async () => {
  renderHarness();
  await next("둘째");
  expect(fireEvent.keyDown(screen.getByLabelText("메모"), { key: "Enter" })).toBe(true);
  expect(fireEvent.keyDown(prevButton(), { key: "Enter" })).toBe(true);
  expect(fireEvent.keyDown(screen.getByLabelText("시트 칸"), { key: "Enter" })).toBe(true);
  expect(stepHeading()).toHaveTextContent("둘째");
});

test("Enter — 행 칸(data-row·data-col)은 다음 행의 같은 열로, 마지막 행이면 다음 단계", async () => {
  renderHarness();
  await next("둘째");
  expect(fireEvent.keyDown(screen.getByLabelText("1행 경로"), { key: "Enter" })).toBe(false);
  expect(screen.getByLabelText("2행 경로")).toHaveFocus();
  expect(stepHeading()).toHaveTextContent("둘째");
  fireEvent.keyDown(screen.getByLabelText("2행 경로"), { key: "Enter" });
  expect(stepHeading()).toHaveTextContent("셋째");
});

test("더블클릭 — 두 번째 클릭은 같은 자리·같은 노드의 제출에 떨어지고 바가 무시한다(화면 onClick 에 가드가 없어도)", async () => {
  const { onSubmit } = renderHarness();
  await next("둘째");
  await next("셋째");
  const btn = nextButton();
  await userEvent.dblClick(btn);
  expect(stepHeading()).toHaveTextContent("검토");
  expect(onSubmit).not.toHaveBeenCalled();
  // 노드가 그대로라 두 번째 클릭이 실제로 제출에 떨어졌다(시험이 헛돌지 않는다)
  expect(btn).toBe(screen.getByRole("button", { name: "제출" }));
  await userEvent.click(btn);       // 새 클릭(user-event 는 호출마다 클릭 횟수를 되돌린다) = 의도한 제출
  expect(onSubmit).toHaveBeenCalledTimes(1);
  expect(onSubmit).toHaveBeenCalledWith("click");
  fireEvent.click(btn, { detail: 0 });   // 키보드 누르기(detail 0)는 통과
  expect(onSubmit).toHaveBeenCalledTimes(2);
});

test("URL — 「다음」마다 step 을 push(다른 쿼리 보존), 뒤로·앞으로 가기는 단계를 따라간다", async () => {
  renderHarness({}, "/w?keep=x");
  expect(search().get("step")).toBeNull();
  await next("둘째");
  await waitFor(() => expect(search().get("step")).toBe("b"));
  expect(search().get("keep")).toBe("x");
  expect(navType()).toBe("PUSH");
  await next("셋째");
  await waitFor(() => expect(search().get("step")).toBe("c"));
  back();
  await waitFor(() => expect(stepHeading()).toHaveTextContent("둘째"));
  expect(stepHeading()).toHaveFocus();
  back();
  await waitFor(() => expect(stepHeading()).toHaveTextContent("첫째"));
  expect(search().get("step")).toBeNull();
  forward();
  await waitFor(() => expect(stepHeading()).toHaveTextContent("둘째"));
});

test("URL — 앞으로 가기라도 도달할 수 없는 단계면 현재부터 센 첫 막힘으로 맞추고 replace", async () => {
  renderHarness();
  await next("둘째");
  await next("셋째");
  await waitFor(() => expect(search().get("step")).toBe("c"));
  back();
  await waitFor(() => expect(stepHeading()).toHaveTextContent("둘째"));
  await userEvent.click(screen.getByRole("button", { name: "둘째 막기" }));
  forward();
  await waitFor(() => expect(navType()).toBe("REPLACE"));
  expect(search().get("step")).toBe("b");
  expect(stepHeading()).toHaveTextContent("둘째");
});

test("URL — 뒤로·앞으로(POP)가 아닌 URL 변화(push·replace)는 단계를 건드리지 않는다", async () => {
  // 자기 push 가 전환으로 늦게 커밋돼도 그것을 「뒤로 가기」로 오인해 앞 단계로 되돌리지 않는다(design-wizard §4 근거 5).
  renderHarness();
  await next("둘째");
  await next("셋째");
  fireEvent.click(screen.getByTestId("push-a"));
  await waitFor(() => expect(search().get("step")).toBe("a"));
  expect(navType()).toBe("PUSH");
  expect(stepHeading()).toHaveTextContent("셋째");
});

test("URL — 처음 렌더: step 은 min(URL, 첫 막힘)으로 replace, 모르는 값은 첫 단계, 없으면 깨끗한 주소", async () => {
  const first = renderHarness({ blocked: { b: true } }, "/w?step=d");
  expect(stepHeading()).toHaveTextContent("둘째");
  await waitFor(() => expect(search().get("step")).toBe("b"));
  expect(navType()).toBe("REPLACE");
  expect(stepButton("첫째")).toHaveAccessibleName("첫째 A값 (완료)");
  expect(stepHeading()).not.toHaveFocus();       // 처음 마운트는 포커스를 옮기지 않는다
  first.unmount();

  const unknown = renderHarness({}, "/w?step=zzz");
  expect(stepHeading()).toHaveTextContent("첫째");
  await waitFor(() => expect(search().get("step")).toBe("a"));
  unknown.unmount();

  renderHarness({}, "/w");
  expect(stepHeading()).toHaveTextContent("첫째");
  expect(screen.getByTestId("search").textContent).toBe("");
  expect(navType()).toBe("POP");
});

test("URL — initial 이 첫 단계가 아니면(딥링크) 앞 단계를 방문으로 두고 step 을 replace 로 더한다", async () => {
  renderHarness({ initial: "b" }, "/w?keep=x");
  expect(stepHeading()).toHaveTextContent("둘째");
  expect(stepButton("첫째")).toHaveAccessibleName("첫째 A값 (완료)");
  await waitFor(() => expect(search().get("step")).toBe("b"));
  expect(search().get("keep")).toBe("x");
  expect(navType()).toBe("REPLACE");
});

test("빠른 연속 「다음」 두 번 — 단계가 앞 단계로 되돌았다 오지 않는다", async () => {
  const seen: K[] = [];
  renderHarness({ onRender: (k) => seen.push(k) });
  fireEvent.click(nextButton());
  fireEvent.click(nextButton());
  await waitFor(() => expect(search().get("step")).toBe("c"));
  expect(stepHeading()).toHaveTextContent("셋째");
  const order: K[] = ["a", "b", "c", "d"];
  const idx = seen.map((k) => order.indexOf(k));
  expect(idx.every((v, i) => i === 0 || v >= idx[i - 1]), `렌더 순서 ${seen.join(",")}`).toBe(true);
});

test("jump — replace, 앞 단계 방문 표시, 포커스는 옮기지 않는다", async () => {
  renderHarness();
  const trigger = screen.getByRole("button", { name: "셋째로 점프" });
  await userEvent.click(trigger);
  expect(stepHeading()).toHaveTextContent("셋째");
  await waitFor(() => expect(search().get("step")).toBe("c"));
  expect(navType()).toBe("REPLACE");
  expect(stepButton("첫째")).toHaveAccessibleName("첫째 A값 (완료)");
  expect(stepButton("둘째")).toHaveAccessibleName("둘째 (완료)");
  expect(trigger).toHaveFocus();
});

test("「검토로 돌아가기」 — 검토를 방문했고 지금 도달할 수 있을 때만 앞 단계 바에 보인다", async () => {
  renderHarness();
  expect(screen.queryByRole("button", { name: "검토로 돌아가기" })).toBeNull();
  await goStep("검토");
  await goStep("둘째");
  await userEvent.click(screen.getByRole("button", { name: "검토로 돌아가기" }));
  expect(stepHeading()).toHaveTextContent("검토");
  await goStep("둘째");
  await userEvent.click(screen.getByRole("button", { name: "셋째 막기" }));
  expect(screen.queryByRole("button", { name: "검토로 돌아가기" })).toBeNull();
});

test("검토 바 — strip·details·doneHint, 제출 props(disabled·describedBy·variant)", async () => {
  const { unmount } = renderHarness();
  await goStep("검토");
  expect(screen.getByText("되돌릴 수 없는 작업입니다.")).toBeVisible();
  expect(screen.getByText("힌트 한 줄")).toBeVisible();
  expect(reasonLine()).toHaveTextContent("제출하면 이동합니다.");
  const submit = screen.getByRole("button", { name: "제출" });
  // details(이유 목록·제출 오류)는 버튼 줄 **위**(design-wizard §1-5 순서 strip · 이유 목록 · 오류 · 제출) -- 상태 줄보다도 위다
  const follows = (a: Node, b: Node) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
  expect(follows(screen.getByText("되돌릴 수 없는 작업입니다."), screen.getByText("힌트 한 줄"))).toBe(true);
  expect(follows(screen.getByText("힌트 한 줄"), reasonLine()!)).toBe(true);
  expect(follows(screen.getByText("힌트 한 줄"), submit)).toBe(true);
  expect(submit).toBeEnabled();
  expect(submit).toHaveAttribute("aria-describedby", "harness-hints");
  expect(submit.className).toContain("bg-accent");
  expect(screen.queryByRole("button", { name: /단계에서 고치기$/ })).toBeNull();
  unmount();

  renderHarness({ destructive: true });
  await goStep("검토");
  expect(screen.getByRole("button", { name: "제출" }).className).toMatch(/(^|\s)bg-bad(\s|$)/);
});

test("취소·이전 — 첫 단계의 「이전」은 잠김, 그 뒤로는 한 단계 앞으로", async () => {
  const { onSubmit } = renderHarness();
  await userEvent.click(prevButton());
  expect(stepHeading()).toHaveTextContent("첫째");
  await next("둘째");
  await userEvent.click(prevButton());
  expect(stepHeading()).toHaveTextContent("첫째");
  await userEvent.click(screen.getByRole("button", { name: "취소" }));
  expect(onSubmit).toHaveBeenCalledWith("cancel");
});

test("포커스 보정 — 크기를 모르면(jsdom 0) 스크롤하지 않는다", async () => {
  const scrollBy = vi.spyOn(window, "scrollBy").mockImplementation(() => {});
  renderHarness();
  await next("둘째");
  screen.getByLabelText("이름").focus();
  await next("셋째");
  await goStep("검토");
  expect(scrollBy).not.toHaveBeenCalled();
});
