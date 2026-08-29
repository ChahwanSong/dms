import { useState } from "react";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Table } from "../../components/ui/Table";
import { StatusPill } from "../../components/ui/StatusPill";
import { ApiError, reasonText } from "../../lib/api";
import { kstStampOrDash } from "../../lib/datetime";
import type { Release, ReleaseTarget } from "../../lib/types";
import { RELEASE_ACTIVE_STATES, releasePillVariant, useRefreshTargetsOnSettle,
         useReleases, useReleaseTargets, useSubmitReleases } from "./useReleases";

const field = "w-full rounded-lg border border-black/10 px-3 py-2";

/** 설계 §8: 컨트롤러를 갱신하면 롤아웃을 수행하던 파드 자신이 죽는다. 운영자가
 *  자기유발 정지를 장애로 오해하면 안 되므로 조건 없이 항상 보인다 -- 고른 뒤가
 *  아니라 고르기 전에 읽혀야 하는 문구다. targets가 느릴 때(최악 30초)의 로딩
 *  화면에도 같이 띄운다. */
function ControllerCaution({ strong }: { strong: boolean }) {
  return (
    <p className={`rounded-lg px-3 py-2 text-sm ${strong
        ? "bg-badbg text-bad font-medium" : "bg-busybg text-busy"}`}>
      컨트롤러를 갱신하면 컨트롤러가 재시작되어 롤아웃 추적이 잠시 끊깁니다 — 화면이 멈춘 것은 장애가 아닙니다.
    </p>
  );
}

/** "pkg-01:5000/dms:d22" -> "d22". 레지스트리 호스트에도 ':'(포트)가 있으므로
 *  마지막 ':' 뒤만 태그다. 태그 문자셋에는 ':'이 없다(서버 _TAG_RE). */
function currentTagOf(image: string | null): string | null {
  if (!image) return null;
  const sep = image.lastIndexOf(":");
  const tag = sep > 0 ? image.slice(sep + 1) : "";
  // 태그 없이 리포만 있는 참조(":" 뒤가 비었거나 슬래시가 섞였으면 포트다)
  return tag && !tag.includes("/") ? tag : null;
}

export function ReleasesPage() {
  const targetsQ = useReleaseTargets();
  const releasesQ = useReleases();
  const submit = useSubmitReleases();
  const [picks, setPicks] = useState<Record<string, string>>({});

  // 배열이 아닌 페이로드가 SPA 전체를 흰 화면으로 만든 전례가 있다 -- 렌더 전에
  // 전부 정규화한다.
  const targets: ReleaseTarget[] =
    Array.isArray(targetsQ.data?.targets) ? targetsQ.data.targets : [];
  const history: Release[] =
    Array.isArray(releasesQ.data?.history) ? releasesQ.data.history : [];
  const current: Record<string, Release> = releasesQ.data?.current ?? {};
  const active = history.some((r) => RELEASE_ACTIVE_STATES.has(r.state));
  useRefreshTargetsOnSettle(active, releasesQ.isSuccess);

  // 제출 순서는 서버가 ROLLOUT_ORDER로 강제한다 -- 운영자는 "무엇을"만 고른다.
  // items를 picks가 아니라 targets 순서로 만드는 이유: 선택 순서가 요청 본문에
  // 새어 나가지 않고, 목록에서 사라진 컴포넌트를 실수로 보내지도 않는다.
  const items = targets
    .map((t) => ({ component: t.component, tag: picks[t.component] ?? "" }))
    .filter((i) => i.tag !== "");
  const controllerPicked = items.some((i) => i.component === "dms-controller");

  const pick = (component: string, tag: string) =>
    setPicks((prev) => ({ ...prev, [component]: tag }));

  const start = () => {
    // 202를 받으면 선택을 비운다 -- 같은 배치를 두 번 눌러 rollout_in_progress를
    // 자초하지 않게. 실패면 선택을 남겨 고쳐서 다시 낼 수 있게 둔다.
    submit.mutate({ items }, { onSuccess: () => setPicks({}) });
  };

  // 대상 목록이 오기 전에는 고를 것이 없다 -- 화면 뼈대만 먼저 그리면 운영자가 빈
  // 표를 "컴포넌트가 없다"로 읽는다. 경고는 이 동안에도 보여준다.
  if (targetsQ.isLoading) {
    return (
      <section className="space-y-4">
        <ControllerCaution strong={false} />
        <p className="text-muted">불러오는 중…</p>
      </section>
    );
  }

  return (
    <section className="space-y-4">
      <div className="flex items-center gap-2">
        <h1 className="text-2xl font-bold">릴리스</h1>
        {active && (
          <span className="inline-flex rounded-full bg-busybg px-2.5 py-0.5 text-xs font-semibold text-busy">
            진행 중
          </span>
        )}
      </div>

      <Card>
        <div className="space-y-3 text-sm">
          {/* dms-controller를 고른 순간 이 경고는 "참고"가 아니라 "지금 일어날 일"이
              된다 -- 문구를 두 번 쓰지 않고 강조만 바꾼다. */}
          <ControllerCaution strong={controllerPicked} />
          {/* job-image 행(슬라이스 35): 워크로드가 아니라 "다음 잡 파드가 쓸
              이미지"의 오버라이드다 -- 적용 즉시(재시작·파드 교체 없음) 다음
              잡부터 반영된다. 이 구분을 말하지 않으면 "왜 얘만 롤아웃이 안
              보이지"가 된다. */}
          <p className="text-muted text-xs">
            job-image 는 잡 실행 이미지(dms-mpifileutils)입니다 — 적용 즉시 다음
            잡부터 반영되며 파드 재시작이 없습니다.
          </p>
          {targetsQ.data && targetsQ.data.registry_ok === false && (
            <p className="text-bad">{reasonText("registry_unreachable")}</p>
          )}

          {targetsQ.isError ? (
            <p className="text-bad">{(targetsQ.error as ApiError).message}</p>
          ) : (
            <Table>
              <thead>
                <tr className="text-muted">
                  <th className="py-2">컴포넌트</th><th>현재 이미지</th>
                  <th>새 태그</th><th>상태</th>
                </tr>
              </thead>
              <tbody>
                {targets.map((t) => {
                  const cur = current[t.component];
                  const curTag = currentTagOf(t.current_image);
                  return (
                    <tr key={t.component} className="border-t border-black/5">
                      <td className="py-2">{t.component}</td>
                      <td className="text-muted">{t.current_image ?? "—"}</td>
                      <td className="py-2">
                        {/* 순서를 고르는 UI는 없다 -- 서버가 강제하므로 고르게 하면
                            지켜지지 않는 약속을 화면이 하는 셈이 된다. */}
                        <select aria-label={t.component} className={field}
                                value={picks[t.component] ?? ""}
                                onChange={(e) => pick(t.component, e.target.value)}>
                          <option value="">변경 없음</option>
                          {(t.tags ?? []).map((tag) => (
                            // 현재 태그를 막지는 않는다(서버가 same_tag로 거절한다)
                            // -- 프론트는 build_registry를 몰라 이미지 동일성을
                            // 단정할 수 없다. 대신 눈에 보이게 표시만 한다.
                            <option key={tag} value={tag}>
                              {tag === curTag ? `${tag} (현재)` : tag}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        {cur ? (
                          <span className="inline-flex items-center gap-2">
                            <StatusPill state={cur.state} variant={releasePillVariant(cur.state)} />
                            <span className="text-muted">{reasonText(cur.reason_code)}</span>
                          </span>
                        ) : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </Table>
          )}

          {submit.isError && <p className="text-bad">{(submit.error as ApiError).message}</p>}
          {/* fail-open 비침묵화(슬라이스 28): 서버가 태그 존재를 검증하지 못한 채
              접수했다 -- 202 라서 성공처럼 보이는 바로 그 순간에 보여야 한다.
              레지스트리 전면 다운이면 드롭다운이 비어 여기까지 못 오고, 이 배너가
              잡는 실 창은 목록 로드 후 제출 전 장애(TOCTOU)와 리포별 부분 침묵이다. */}
          {submit.data?.tag_verified === false && (
            <p className="rounded-lg bg-busybg px-3 py-2 text-busy">
              {reasonText("tag_unverified")}
            </p>
          )}
          <div className="flex justify-end pt-2">
            <Button onClick={start} disabled={items.length === 0 || submit.isPending}>
              롤아웃 시작
            </Button>
          </div>
        </div>
      </Card>

      <section className="space-y-2">
        <h2 className="text-base font-semibold">이력</h2>
        {/* 설계대로 롤백은 없다 -- 한 배치가 중간에 실패해도 앞 컴포넌트는 새
            이미지로 남는다. 그 혼합 상태를 운영자가 행 단위로 읽어야 한다. */}
        <p className="text-muted text-sm">
          롤아웃이 중간에 실패해도 이미 적용된 앞 컴포넌트는 되돌리지 않습니다 — 컴포넌트별 상태를 각각 확인하세요.
        </p>
        {releasesQ.isLoading ? (
          <p className="text-muted">불러오는 중…</p>
        ) : releasesQ.isError ? (
          <p className="text-bad">{(releasesQ.error as ApiError).message}</p>
        ) : (
          <Table>
            <thead>
              <tr className="text-muted">
                <th className="py-2">시각</th><th>컴포넌트</th><th>태그</th>
                <th>상태</th><th>사유</th><th>actor</th>
              </tr>
            </thead>
            <tbody>
              {history.map((r) => (
                <tr key={r.id} className="border-t border-black/5">
                  <td className="py-2">{kstStampOrDash(r.applied_at)}</td>
                  <td>{r.component}</td>
                  <td>{r.tag ?? "—"}</td>
                  <td><StatusPill state={r.state} variant={releasePillVariant(r.state)} /></td>
                  <td className="text-muted">{reasonText(r.reason_code) || "—"}</td>
                  <td className="text-muted">{r.actor ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </section>
    </section>
  );
}
