import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MetaLine, StorageSelect, storageOptionLabel } from "./StorageSelect";
import { buildStorageTabs } from "./usageTabs";
import type { Storage, UnregisteredStorage, UsageStorageSummary } from "../../lib/types";

// 스토리지 select(2026-10-29 -- 예전 스토리지 탭 줄): 묶음(optgroup)·옵션 글자·디바운스. 화면과의 배선(URL push·필터 유지)은
// UsageAnalysis.test 가 본다.
const st = (name: string, backend_type = "cephfs"): Storage => ({
  storage_name: name, mount_path: `/${name}`, managed_root: `/${name}/dms`, backend_type, enabled: 1, status: "Ready",
  status_detail: null });
const sum = (name: string, over: Partial<UsageStorageSummary> = {}): UsageStorageSummary => ({
  storage_name: name, registered: true, source: "registered", backend_type: "nfs", mount_path: `/${name}`,
  target_count: 5, succeeded_scan_count: 6, last_scan_at: null, ...over });
const unreg = (name: string, target_count: number): UnregisteredStorage => ({
  storage_name: name, target_count, succeeded_scan_count: target_count, last_scan_at: null, single_count: 1, batches: [],
  request_count: 1, mixed_batches: [], last_known: null });

const TABS = buildStorageTabs({
  registered: [st("k8s-pv-nfs", "nfs"), st("cephfs-secondary")],
  summary: [sum("k8s-pv-nfs"), sum("cephfs-secondary", { backend_type: "cephfs", target_count: 0 }),
            sum("old-gpfs", { registered: false, source: "last_known", backend_type: "gpfs", target_count: 3 })],
  unregistered: [unreg("old-gpfs", 3), unreg("ghost", 0)],
});
const sel = () => screen.getByRole("combobox", { name: "스토리지" });
const texts = (el: HTMLElement) => within(el).getAllByRole("option").map((o) => o.textContent);

afterEach(() => { vi.useRealTimers(); });

describe("StorageSelect", () => {
  it("묶음: 「등록」(이름순) | 「미등록 — 기록만 남음」(이름순, 실패 기록만 남은 이름 포함) -- 옵션 글자에 타입·대상 수", () => {
    render(<StorageSelect tabs={TABS} value="k8s-pv-nfs" onSelect={() => {}} />);
    expect(sel()).toHaveValue("s:k8s-pv-nfs");
    const groups = within(sel()).getAllByRole("group");
    expect(groups.map((g) => g.getAttribute("label"))).toEqual(["등록", "미등록 — 기록만 남음"]);
    expect(texts(groups[0])).toEqual(["cephfs-secondary · cephfs · 대상 0개", "k8s-pv-nfs · nfs · 대상 5개"]);
    expect(texts(groups[1])).toEqual(["ghost · 미등록 · 대상 0개", "old-gpfs · 미등록 · 대상 3개"]);
  });

  it("이름 툴팁은 감싼 칸에 -- select 의 접근 설명이 되지 않는다(낭독기가 옵션 글자 안의 이름을 설명으로 한 번 더 읽지 않게)", () => {
    // 2차 리뷰(2026-10-29): select 에 title 을 두면 접근 이름(aria-label)과 달라 설명이 되어 「스토리지 콤보 상자 k8s-pv-nfs · nfs ·
    // 대상 5개 k8s-pv-nfs」로 읽혔다.
    render(<StorageSelect tabs={TABS} value="k8s-pv-nfs" onSelect={() => {}} />);
    expect(sel()).toHaveAccessibleName("스토리지");
    expect(sel()).not.toHaveAttribute("title");
    expect(sel()).not.toHaveAccessibleDescription();
    expect(sel().parentElement).toHaveAttribute("title", "k8s-pv-nfs");
  });

  it("요약 전엔 수 없이(모름 ≠ 0) · 값을 아직 모르면 「고르는 중」 자리표시 · 집합에 아직 없는 딥링크 값은 이름만으로 한 옵션", () => {
    const early = buildStorageTabs({ registered: [st("k8s-pv-nfs", "nfs")] });
    expect(storageOptionLabel(early[0])).toBe("k8s-pv-nfs · nfs");
    const { rerender } = render(<StorageSelect tabs={early} value={null} onSelect={() => {}} />);
    expect(sel()).toHaveValue("");
    expect(texts(sel())).toEqual(["스토리지 고르는 중…", "k8s-pv-nfs · nfs"]);
    rerender(<StorageSelect tabs={early} value="later" onSelect={() => {}} />);
    expect(sel()).toHaveValue("s:later");
    expect(texts(sel())).toEqual(["later", "k8s-pv-nfs · nfs"]);
    // 빈 이름(변조·구형 행)도 스토리지다 -- 자리표시(빈 값)와 갈린다
    rerender(<StorageSelect tabs={buildStorageTabs({ summary: [sum("", { registered: false, backend_type: null })] })}
                            value="" onSelect={() => {}} />);
    expect(sel()).toHaveValue("s:");
    expect(texts(sel())).toEqual(["(이름 없음) · 미등록 · 대상 5개"]);
  });

  it("바꾸면 300ms 디바운스 뒤에 한 번 -- 그 안의 연속 변경은 마지막 값 하나, 기다리는 동안 고른 값을 보인다", () => {
    vi.useFakeTimers();
    const onSelect = vi.fn();
    render(<StorageSelect tabs={TABS} value="k8s-pv-nfs" onSelect={onSelect} />);
    fireEvent.change(sel(), { target: { value: "s:cephfs-secondary" } });
    expect(sel()).toHaveValue("s:cephfs-secondary");
    act(() => { vi.advanceTimersByTime(200); });
    fireEvent.change(sel(), { target: { value: "s:old-gpfs" } });
    act(() => { vi.advanceTimersByTime(299); });
    expect(onSelect).not.toHaveBeenCalled();
    act(() => { vi.advanceTimersByTime(1); });
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith("old-gpfs");
    // 부모가 값을 아직 바꾸지 않았으면 원래 값으로 돌아간다(URL 이 진실)
    expect(sel()).toHaveValue("s:k8s-pv-nfs");
  });

  it("select 를 떠나면(blur) 기다리지 않고 바로 한 번 -- 남은 타이머는 다시 부르지 않고, 기다리던 것이 없으면 blur 는 아무것도 안 한다", () => {
    vi.useFakeTimers();
    const onSelect = vi.fn();
    render(<StorageSelect tabs={TABS} value="k8s-pv-nfs" onSelect={onSelect} />);
    fireEvent.blur(sel());
    expect(onSelect).not.toHaveBeenCalled();
    fireEvent.change(sel(), { target: { value: "s:old-gpfs" } });
    act(() => { vi.advanceTimersByTime(100); });
    fireEvent.blur(sel());
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith("old-gpfs");
    act(() => { vi.advanceTimersByTime(1000); });
    fireEvent.blur(sel());
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it("디바운스 중에 사라지면(다른 화면으로) 부르지 않는다", () => {
    vi.useFakeTimers();
    const onSelect = vi.fn();
    const { unmount } = render(<StorageSelect tabs={TABS} value="k8s-pv-nfs" onSelect={onSelect} />);
    fireEvent.change(sel(), { target: { value: "s:old-gpfs" } });
    unmount();
    act(() => { vi.advanceTimersByTime(1000); });
    expect(onSelect).not.toHaveBeenCalled();
  });
});

describe("MetaLine", () => {
  it("마운트 · 타입 · 대상 수 · 최근 스캔 -- 이름은 싣지 않는다(옆 select 가 말한다), 모르는 항목은 뺀다", () => {
    const now = Date.parse("2026-10-29T06:11:00Z");
    const [tab] = buildStorageTabs({ summary: [sum("k8s-pv-nfs", { mount_path: "/k8s_pv/nfs",
                                                                   last_scan_at: "2026-10-28T14:31:12Z" })] });
    const { container, rerender } = render(<MetaLine tab={tab} now={now} />);
    expect(container.firstElementChild).toHaveTextContent(/^\/k8s_pv\/nfsnfs대상 5개최근 스캔 15시간 전$/);
    rerender(<MetaLine tab={{ ...tab, summary: null, info: null, targetCount: null }} now={now} />);
    expect(container.firstElementChild).toBeNull();
  });
});
