import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, test } from "vitest";
import { MountTipBody, NodeTags, ProblemTag, SharedHeadTipBody, SharedTipBody, StorageTipBody } from "./MountCell";
import { LIVE, NOW_ISO, VARIETY, absent, broken, column, ready, row, sharedOk } from "./mountFixtures";
import { NodeMountSheet } from "./NodeMountSheet";
import type { MountCellData, MountStatusResponse, SharedCellData } from "./types";

// 쓰기 여부 글자(2026-11-23 리뷰): 에이전트는 경로가 없거나 마운트 지점이 아니어도 writable=false 를 보고한다. 그 칸(없음·마운트
// 빠짐)과 공용 디렉터리 「없음」 칸의 툴팁·시트가 「읽기 전용」이라고 말하면 ro 마운트 옵션을 의심하게 만든다 -- 진짜 원인은 빠진
// 마운트다. 읽기 전용 칸은 대조군(「불가(읽기 전용)」이 그대로 나온다 -- 이 검사가 공허하지 않다).

const SHARED_PATH = "/dms_shared/dms/artifacts";
const sharedMissing: SharedCellData = sharedOk({ state: "missing", exists: false, writable: false, mount_point: null, fstype: null });
const readOnly: MountCellData = ready("/ro", { state: "read_only", writable: false });

function tip(cell: MountCellData, path: string) {
  return render(
    <div><MountTipBody nodeName="n1" col={column("s", path)} cell={cell} reportedAt={NOW_ISO} nowMs={Date.now()} /></div>,
  ).container.firstElementChild as HTMLElement;
}

test("툴팁 점검 줄: 없음·마운트 빠짐 칸은 「쓰기 —」 -- 붙지 않은 대상이라 쓰기 여부를 말하지 않는다(읽기 전용이라고도)", () => {
  const abs = tip(absent("/a"), "/a");
  expect(abs).toHaveTextContent("존재 아니오 · 마운트 지점 아니오 · 읽기 아니오 · 쓰기 —");
  expect(abs).not.toHaveTextContent("읽기 전용");
  const notMount = tip(broken("/b", "not_a_mountpoint"), "/b");
  expect(notMount).toHaveTextContent("쓰기 —");
  expect(notMount).not.toHaveTextContent("읽기 전용");
  // 대조군: 정말 ro 로 붙은 칸
  expect(tip(readOnly, "/ro")).toHaveTextContent("쓰기 불가(읽기 전용)");
});

test("공용 디렉터리 툴팁: 없음(exists false)은 쓰기 「—」, 있는데 못 쓰면 「불가(읽기 전용)」", () => {
  const missing = render(
    <div><SharedTipBody nodeName="n1" path={SHARED_PATH} cell={sharedMissing} majority={null} reportedAt={NOW_ISO}
                        nowMs={Date.now()} /></div>,
  ).container.firstElementChild as HTMLElement;
  expect(missing).toHaveTextContent("아니오 · —");
  expect(missing).not.toHaveTextContent("읽기 전용");
  const ro = render(
    <div><SharedTipBody nodeName="n1" path={SHARED_PATH} cell={sharedOk({ state: "read_only", writable: false })}
                        majority={null} reportedAt={NOW_ISO} nowMs={Date.now()} /></div>,
  ).container.firstElementChild as HTMLElement;
  expect(ro).toHaveTextContent("예 · 불가(읽기 전용)");
});

function sheet(cells: Record<string, MountCellData>, shared: SharedCellData) {
  const data: MountStatusResponse = {
    ...LIVE,
    storages: Object.entries(cells).map(([name, c]) => column(name, c.reported_path ?? `/${name}`)),
  };
  render(
    <MemoryRouter>
      <NodeMountSheet row={row("n1", cells, { shared_dir: shared })} data={data} onClose={() => undefined}
                      onCloseAutoFocus={() => undefined} />
    </MemoryRouter>,
  );
  return screen.getByRole("dialog");
}

test("시트: 없음·마운트 빠짐 칸과 없는 공용 디렉터리에 「읽기 전용」이 없다(붙지 않은 대상의 쓰기는 「—」, 공용은 「존재 아니오」)", () => {
  const dlg = sheet({ a: absent("/a"), b: broken("/b", "not_a_mountpoint") }, sharedMissing);
  expect(dlg).not.toHaveTextContent("읽기 전용");
  expect(dlg).toHaveTextContent("존재 아니오 · 쓰기 — · 덮는 마운트 모름");
  // 스토리지별 목록은 표가 아니라 세로 쌓임이다(2026-11-23 d38 실증 — 640px 시트에서 6열 표의 경로 열이 한 글자 폭으로 찌그러졌다)
  expect(within(dlg).queryByRole("table")).toBeNull();
  const items = within(within(dlg).getByRole("list", { name: "n1 스토리지별 마운트" })).getAllByRole("listitem");
  const writeLine = (li: HTMLElement) => [...li.querySelectorAll("p")].map((p) => p.textContent ?? "").find((t) => t.startsWith("쓰기 "));
  expect(items.map(writeLine)).toEqual(["쓰기 —", "쓰기 —"]);
  expect(items[0]).toHaveTextContent("/a");
});

test("시트 대조군: 읽기 전용 칸의 쓰기 열은 「불가(읽기 전용)」", () => {
  const dlg = sheet({ r: readOnly }, sharedOk());
  expect(dlg).toHaveTextContent("불가(읽기 전용)");
});

// 열 머리 툴팁(2026-11-25 -- 고정 폭 머리에서 잘리는 전체 이름·경로 + 요약 문장 + 기준, 사이트 덧붙임은 extra 슬롯).
test("열 머리 툴팁: 전체 이름·경로·요약 문장·기준, extra 슬롯은 요약과 기준 사이", () => {
  const long = column("stor-007-gpfs-project-archive-cold", "/mgmt_storage/very/long/mount/path", {
    ready: 3, total: 4, detail: "ready_nodes=3/4", broken: 1, read_only: 1,
  });
  const el = render(<div><StorageTipBody col={long} extra={<p>덧붙임 슬롯</p>} /></div>).container.firstElementChild as HTMLElement;
  expect(el).toHaveTextContent("stor-007-gpfs-project-archive-cold");
  expect(el).toHaveTextContent("/mgmt_storage/very/long/mount/path");
  expect(el).toHaveTextContent("마운트 3/4대 · 마운트 문제 1칸 · 읽기 전용 1칸");
  expect(el).toHaveTextContent(/기준: 신선한 보고만 — 스토리지 목록의 상태\(ready_nodes=3\/4\)/);
  expect(el.textContent!.indexOf("덧붙임 슬롯")).toBeGreaterThan(el.textContent!.indexOf("읽기 전용 1칸"));
  expect(el.textContent!.indexOf("덧붙임 슬롯")).toBeLessThan(el.textContent!.indexOf("기준:"));
  const sh = render(<div><SharedHeadTipBody shared={LIVE.shared_dir!} /></div>).container.firstElementChild as HTMLElement;
  expect(sh).toHaveTextContent("/dms_shared/dms/artifacts");
  expect(sh).toHaveTextContent("대부분 노드: /dms_shared (nfs4)");
});

test("행 꼬리표 lead: 맨 앞에 그리고, 다른 꼬리표가 없어도 lead 만으로 그린다(없으면 null)", () => {
  const broken = VARIETY.nodes[1];     // n-broken: 배치 제외·cordon 배지
  const el = render(<div><NodeTags row={broken} lead={<ProblemTag row={broken} />} /></div>).container.firstElementChild!;
  const tags = el.firstElementChild!;
  expect(tags.firstElementChild).toHaveTextContent("문제 3");
  expect(tags).toHaveTextContent("배치 제외");
  const bare = row("n0", {}, { k8s_node: null, problem: true, problem_count: 1 });
  expect(render(<div><NodeTags row={bare} /></div>).container.firstElementChild!.textContent).toBe("");
  expect(render(<div><NodeTags row={bare} lead={<ProblemTag row={bare} />} /></div>).container.firstElementChild)
    .toHaveTextContent(/^문제 1$/);
});
