import { expect, type Page } from "@playwright/test";

/**
 * 레이아웃 불변식 L1~L4 (설계 §2.4).
 *
 * 단위 테스트(jsdom)는 **기하를 계산하지 않는다** -- 9fbef86(계정 표 뭉개짐)·
 * 6bc2ecb(사이드바 밀림) 두 결함은 그래서 228건을 전부 통과하며 라이브까지 갔다.
 * 여기 네 불변식이 그 사각지대의 그물이고, 각 실패 메시지는 `[L1]`~`[L4]` 라벨로
 * **어느 불변식이 먼저 물었는지**를 남긴다.
 *
 * 단언 재료는 기하·개수·display 뿐이다(설계 §2.1) -- 문구·사유 코드는 vitest 영토.
 */

// L2/L4 의 셀렉터를 상수로 뽑아 둔다: "셀렉터가 아무것도 못 찾으면 조용히 통과"가
// 불가능함을 증명하는 뮤테이션(플랜 Task 4 Step 5)이 이 한 줄만 오염시키면 되도록.
const CELL_SELECTOR = "td, th";
// 한 줄 요소: 두 결함의 공통 증상(줄바꿈·세로 쪼개짐)이 가장 먼저 드러나는 곳.
const NAV_LINK_SELECTOR = "aside a";
const TABLE_BUTTON_SELECTOR = "table button";

// AppShell.tsx:13 의 `md:w-60` = 15rem = 240px. Tailwind preflight 가 border-box 라
// p-3 패딩을 포함해 240 이고, 1280 뷰포트 실측이 이 값과 ±0.5 안에서 일치했다.
// ±0.5 는 서브픽셀 반올림 몫이다 -- 결함 B 는 사이드바를 100px 단위로 줄이므로
// 이 여유가 이빨을 무디게 하지 않는다.
const SIDEBAR_WIDTH_PX = 240;
const SIDEBAR_TOLERANCE_PX = 0.5;

// 데이터 도착(폴링 앱이라 행은 heading 보다 늦게 온다)을 기다리는 상한.
const PRECONDITION_TIMEOUT_MS = 10_000;

export interface LayoutOptions {
  /** L3(사이드바 폭 고정) 적용 여부. md: 분기 아래(모바일)에서는 false. */
  sidebarFixed?: boolean;
  /** L2 의 셀 개수 하한. 표가 있는 화면은 반드시 넘겨야 한다(공허한 초록 방지). */
  minTableCells?: number;
  /**
   * L2 를 위반하는 **이미 알려진** 셀의 개수. 기본 0이고, 0이 아닌 값을 주는 것은
   * "이 화면엔 아직 안 고친 결함 A 가 N개 있다"는 기록이다 -- 정확히 N개일 때만
   * 통과한다(초과=새 회귀, 미만=고쳐졌으니 이 값을 0으로). 부등호가 아니라 등호인
   * 이유: 부등호로 두면 결함이 늘어나도, 고쳐져도 아무도 모른다.
   */
  knownNonTableCells?: number;
}

interface LayoutProbe {
  docScrollWidth: number;
  docClientWidth: number;
  cellCount: number;
  badCells: { where: string; display: string; text: string }[];
  asideWidth: number | null;
  lines: { sel: string; height: number; lineHeight: string; text: string }[];
}

/** 실패 메시지에 화면을 박아 둔다 -- 순회 테스트라 어느 경로에서 물었는지가 반이다. */
function at(page: Page): string {
  try {
    return new URL(page.url()).pathname;
  } catch {
    return page.url();
  }
}

async function probeLayout(page: Page): Promise<LayoutProbe> {
  return page.evaluate(
    ({ cellSelector, lineSelectors }) => {
      const describe = (el: Element) => {
        const cls = typeof el.className === "string" ? el.className.trim() : "";
        return cls ? `${el.tagName.toLowerCase()}[class="${cls}"]` : el.tagName.toLowerCase();
      };
      const text = (el: Element) => (el.textContent ?? "").trim().replace(/\s+/g, " ").slice(0, 40);
      const doc = document.documentElement;
      const cells = Array.from(document.querySelectorAll(cellSelector));
      const aside = document.querySelector("aside");
      return {
        docScrollWidth: doc.scrollWidth,
        docClientWidth: doc.clientWidth,
        cellCount: cells.length,
        badCells: cells
          .map((el) => ({ el, display: getComputedStyle(el).display }))
          .filter((c) => c.display !== "table-cell")
          .map((c) => ({ where: describe(c.el), display: c.display, text: text(c.el) })),
        asideWidth: aside === null ? null : aside.getBoundingClientRect().width,
        lines: lineSelectors.flatMap((sel) =>
          Array.from(document.querySelectorAll(sel)).map((el) => ({
            sel,
            height: el.getBoundingClientRect().height,
            lineHeight: getComputedStyle(el).lineHeight,
            text: text(el),
          }))),
      };
    },
    { cellSelector: CELL_SELECTOR, lineSelectors: [NAV_LINK_SELECTOR, TABLE_BUTTON_SELECTOR] },
  );
}

export async function assertLayoutSane(page: Page, opts: LayoutOptions = {}): Promise<void> {
  const sidebarFixed = opts.sidebarFixed ?? true;
  const minTableCells = opts.minTableCells ?? 0;
  const knownNonTableCells = opts.knownNonTableCells ?? 0;
  const where = at(page);

  // L2 개수 하한은 **재시도하는** 단언으로 먼저 건다. 두 가지를 한 번에 한다:
  // (a) 데이터가 도착해 행이 그려질 때까지 기다리는 안정화(heading 은 로딩 중에도
  //     이미 보인다), (b) 셀렉터가 썩으면 0 으로 떨어져 반드시 빨개지는 바닥.
  if (minTableCells > 0) {
    await expect
      .poll(() => page.locator(CELL_SELECTOR).count(), {
        message:
          `[L2] ${where}: 표 셀(${CELL_SELECTOR})이 ${minTableCells}개에 못 미친다 -- ` +
          `행이 아직 안 왔거나 셀렉터가 화면과 어긋났다(이 하한이 없으면 셀렉터가 ` +
          `아무것도 못 찾아도 통과하는 공허한 초록이 된다)`,
        timeout: 10_000,
      })
      .toBeGreaterThanOrEqual(minTableCells);
  }

  const probe = await probeLayout(page);

  // L1 -- 문서 가로 오버플로 금지. 결함 B 의 최종 증상(페이지 전체가 넓어져 가로
  // 스크롤바가 생기고 사이드바가 화면 밖으로 밀림)을 직격한다. ±1 은 서브픽셀.
  expect(
    probe.docScrollWidth,
    `[L1] ${where}: 문서가 가로로 넘친다(scrollWidth=${probe.docScrollWidth} > ` +
      `clientWidth=${probe.docClientWidth}) -- 넓은 콘텐츠가 자기 컨테이너 안에서 ` +
      `스크롤하지 못하고 레이아웃 전체를 밀어냈다`,
  ).toBeLessThanOrEqual(probe.docClientWidth + 1);

  // L2 -- 표 셀 display 불변식. td 가 flex/block 이 되는 순간 표 레이아웃 계산에서
  // 빠져나와 다른 열과 폭을 나눠 갖지 못한다(9fbef86 의 구조 원인).
  expect(
    probe.badCells.length,
    `[L2] ${where}: computed display 가 table-cell 이 아닌 셀 ` +
      `${probe.badCells.length}개(기대 ${knownNonTableCells}개): ` +
      `${JSON.stringify(probe.badCells)}`,
  ).toBe(knownNonTableCells);
  expect(
    probe.cellCount,
    `[L2] ${where}: 표 셀 개수 ${probe.cellCount} < 하한 ${minTableCells}`,
  ).toBeGreaterThanOrEqual(minTableCells);

  // L3 -- 사이드바 폭 고정. 결함 B 는 `md:shrink-0` 이 없어 넓은 표에 눌려
  // 사이드바가 쪼그라들던 것이었다. 240 은 상수다(md:w-60).
  if (sidebarFixed) {
    expect(probe.asideWidth, `[L3] ${where}: aside 를 찾지 못했다`).not.toBeNull();
    const width = probe.asideWidth as number;
    expect(
      Math.abs(width - SIDEBAR_WIDTH_PX) <= SIDEBAR_TOLERANCE_PX,
      `[L3] ${where}: 사이드바 폭 ${width}px (기대 ${SIDEBAR_WIDTH_PX}±` +
        `${SIDEBAR_TOLERANCE_PX}px) -- 표에 눌려 쪼그라들었다`,
    ).toBe(true);
  }

  // L4 -- 한 줄 요소가 두 줄 이상으로 쪼개지지 않는다.
  const navLines = probe.lines.filter((l) => l.sel === NAV_LINK_SELECTOR);
  // 이 하한이 L4 의 공허한 초록 방지다: AppShell 안의 모든 화면은 nav 링크를 가지므로
  // 0 은 "링크가 없다"가 아니라 "셀렉터가 썩었다"는 뜻이다.
  expect(
    navLines.length,
    `[L4] ${where}: nav 링크(${NAV_LINK_SELECTOR})를 하나도 못 찾았다 -- 셀렉터가 ` +
      `화면과 어긋났다(검사한 게 없는데 통과할 수는 없다)`,
  ).toBeGreaterThanOrEqual(1);

  for (const line of probe.lines) {
    const lineHeight = parseFloat(line.lineHeight);
    // "normal" 같은 값은 px 로 못 읽는다 -- 조용히 넘기면 NaN 비교가 되어 검사가
    // 사라지므로, 모르면 통과가 아니라 **실패**다(설계 §4: 모름과 정상을 안 섞는다).
    expect(
      Number.isFinite(lineHeight),
      `[L4] ${where}: ${line.sel} "${line.text}" 의 line-height 를 px 로 읽지 ` +
        `못했다("${line.lineHeight}")`,
    ).toBe(true);
    expect(
      line.height,
      `[L4] ${where}: ${line.sel} "${line.text}" 가 ${line.height}px 로 두 줄 이상 ` +
        `차지한다(line-height ${lineHeight}px) -- 줄바꿈·세로 쪼개짐`,
    ).toBeLessThan(2 * lineHeight);
  }
}

/**
 * 전제 단언(설계 §2.4 "검사의 반"): 표가 **실제로 컨테이너보다 넓다**.
 *
 * 이게 없으면 L1~L4 는 "아무것도 안 넘치는 좁은 표"를 보고 초록을 낼 뿐이다 --
 * 두 결함은 표가 넘칠 때만 발현한다. 실패 메시지를 `E2E_SEED_TOO_NARROW:` 로 시작해
 * 불변식 위반(`[L*]`)과 **다른 메시지**로 만든다: "시드가 좁다"와 "레이아웃이
 * 깨졌다"를 뭉개면 원인 진단이 통째로 어긋난다(설계 §4).
 */
export async function assertTableOverflows(page: Page): Promise<void> {
  const where = at(page);
  const wrapper = page.locator(".overflow-x-auto").first();
  // 래퍼 부재는 "시드가 좁다"가 아니라 화면·셀렉터 문제다 -- 접두를 붙이지 않는다.
  await expect(
    wrapper,
    `${where}: 표 래퍼(.overflow-x-auto)가 없다 -- 화면이나 셀렉터를 확인하라`,
  ).toBeVisible();

  const deadline = Date.now() + PRECONDITION_TIMEOUT_MS;
  for (;;) {
    if (await wrapper.evaluate((el) => el.scrollWidth > el.clientWidth)) return;
    if (Date.now() >= deadline) break;
    await page.waitForTimeout(200);
  }

  // 여기부터가 이 함수의 핵심이다: "래퍼가 안 넘친다"는 관측 하나에 **원인이 둘**이라
  // 메시지를 고르기 전에 반드시 갈라야 한다(설계 §4: 서로 다른 실패를 뭉개지 않는다).
  //   (a) 레이아웃 붕괴 -- 결함 B 처럼 min-w-0 이 없으면 flex 자식이 표만큼 넓어져
  //       래퍼의 overflow-x-auto 가 **발동하지 못하고** 문서 전체가 넘친다. 실측으로
  //       배운 것: 이 상태에서 그냥 "시드가 좁다"고 말하면 정반대 방향(시드 늘리기)의
  //       수리를 지시하게 되고, 진짜 원인인 사이드바 밀림은 그대로 남는다.
  //   (b) 진짜로 시드가 좁다 -- 문서는 멀쩡한데 표가 컨테이너를 못 채운 경우.
  const probe = await probeLayout(page);
  expect(
    probe.docScrollWidth,
    `[L1] ${where}: 표가 자기 컨테이너 안에서 스크롤하지 못하고 문서 전체가 넘쳤다` +
      `(scrollWidth=${probe.docScrollWidth} > clientWidth=${probe.docClientWidth}) ` +
      `-- 시드가 좁은 게 아니라 레이아웃이 무너진 것이다`,
  ).toBeLessThanOrEqual(probe.docClientWidth + 1);
  throw new Error(
    `E2E_SEED_TOO_NARROW: ${where}: 첫 .overflow-x-auto 래퍼가 넘치지 않는다(문서는 ` +
      `넘치지 않았다) -- 시드 데이터가 표를 컨테이너보다 넓게 만들지 못했다. ` +
      `불변식을 완화하지 말고 시드(global-setup 의 이메일 길이)를 늘려라`,
  );
}
