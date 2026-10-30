import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Download, FilterX, ScanSearch, Search, SearchX } from "lucide-react";
import { Button } from "../../components/ui/Button";
import { HelpTip } from "../../components/ui/HelpTip";
import { searchTokens } from "../../components/ui/Highlight";
import { Skeleton } from "../../components/ui/Skeleton";
import { storageKindOf } from "../k8s/k8sTerms";
import type { ApiError } from "../../lib/api";
import type { Storage, UsageAttrFilter } from "../../lib/types";
import { downloadCsv, toCsv } from "../../lib/csvExport";
import { LIST_LIMIT, UsageContractError, attrKey, cleanAttr, fetchUsageExport, hasAttr, hasPvAxis, useScanTargets,
         useUsageFacets, usePurgeUnregisteredStorage, type TargetFilter, type UsageSort } from "./useUsage";
import { buildUsageCsv, usageCsvFilename } from "./usageCsv";
import { TargetTable, type PvDisplay } from "./TargetTable";
import { TargetSheet } from "./TargetSheet";
import { UnregisteredBanner } from "./UnregisteredStorages";
import { TotalsLine } from "./TotalsLine";
import { storageLabel, type StorageTab } from "./usageTabs";
import { UsageFilters } from "./UsageFilters";
import { pvInside, usagePvOf } from "./usageStats";
import { Empty } from "./UsageParts";

// 스토리지 하나의 패널(2026-10-20 탭 패널 → 2026-10-29 바깥 카드 없이 페이지 바탕에 바로 -- 카드 안에 카드를 넣지 않는다). 범위 줄
// (스토리지 select + 메타 줄)은 페이지가 위에 그린다(StorageSelect). 위에서 아래로: (미등록이면) 경고 띠 → 도구 줄(검색 · 건수 ·
// CSV) + CSV 결과 한 줄 → 필터 줄(2026-10-29, UsageFilters) → 합계 줄(2026-10-29 TotalsLine -- 예전 「이 스토리지 합계」·「필터
// 합계」 두 블록을 한 줄 + [자세히]로) → 6열 표 → 각주 한 줄. 표의 행은 오른쪽 상세 시트(TargetSheet, ?target=)를 연다 -- 예전의
// 행 아래 펼침과 목록 밖 딥링크 폴백 칸은 시트 하나로 합쳤다(목록 밖이면 시트가 그렇게 말한다). 페이지가 key={storage} 로 그려
// 스토리지를 바꾸면 내보내기 상태가 새로 시작한다. 검색·정렬·필터는 URL 이라 스토리지를 바꿔도 남는다(sync 쌍은 같은 트리를
// 미러하므로 스토리지끼리 같은 경로를 비교하기 쉽다 -- 검색창·필터 줄이 늘 보여 숨은 필터가 되지 않는다).
// 패널은 스토리지 이름으로 이름 붙은 구역이다(예전 탭 패널 -- 포커스가 갈 데가 없을 때의 마지막 자리, 아래 refocus).

const METRIC_SORTS: UsageSort[] = ["bytes", "files", "hot"];
// 검색칸(2026-10-29 사용자 요청 「경로로 검색할 뿐 아니라, pv 이름도 검색 … 일부 단어만 포함해도」): 이름·규칙. 규칙은 서버가 거는
// 뜻 그대로다(useUsage.TargetFilter -- 작업 목록 검색과 같다).
export const SEARCH_LABEL = "경로·PV 이름 검색";
export const SEARCH_RULE = "여러 단어는 모두 들어간 것만 · 대소문자 무시 · 경로나 PV 이름 어디든";
const SEP = "\u00a0· ";
const n = (x: number) => x.toLocaleString("ko-KR");

export function StoragePanel({
  tab, windowLimit, input, setInput, searchQ, sort, order, onSort, attr, onAttrChange, activeTarget, sheet, storages,
  purge, progress, setProgress, onResultFocus, now,
}: {
  tab: StorageTab;
  windowLimit: number | null;
  input: string; setInput: (s: string) => void; searchQ: string;
  sort: UsageSort; order: "desc" | "asc"; onSort: (c: UsageSort) => void;
  attr: UsageAttrFilter; onAttrChange: (a: UsageAttrFilter) => void;
  // 상세 시트의 URL 동작(페이지가 정한다 -- UsageAnalysis): 열기 push · 이웃으로 replace · 닫기 -1 또는 replace · 시트 안 필터는
  // 필터 + 시트 닫기를 한 번의 replace.
  activeTarget: string | null;
  sheet: { open: (t: string) => void; move: (t: string) => void; close: () => void; filter: (a: UsageAttrFilter) => void };
  storages: Storage[] | undefined;
  purge: ReturnType<typeof usePurgeUnregisteredStorage>;
  progress: number | null; setProgress: (n: number | null) => void; onResultFocus: () => void;
  now: number;
}) {
  const storage = tab.name;
  const searchRuleId = useId();
  const filter: TargetFilter = { storage, q: searchQ, order, sort, attr };
  const targets = useScanTargets(filter);
  const page = targets.data;
  const rows = page?.rows ?? [];
  const filtersOn = hasAttr(attr);
  // 필터 선택지: 기록이 있거나(대상 수 > 0·모름) 필터가 켜졌을 때만(기록 0 스토리지는 빈 상태만 보인다).
  const facetsQ = useUsageFacets(storage, tab.targetCount !== 0 || filtersOn);
  const facets = facetsQ.data;
  // PV 표시(「PV 이름」 열·시트 머리의 소속): 이 스토리지에 PV 안 대상이 있는가 -- 선택지(facets, 최근 스캔 순 창)가 말하거나 받은
  // 행에 있으면(창 밖의 오래된 PV 대상이 목록에 올 수 있다). 둘 다 아니면 열이 없다(filesystem·미등록).
  const rowPvs = rows.map((r) => usagePvOf(r.pv));
  const pvDisplay: PvDisplay = {
    pvColumn: (facets !== undefined && facets.scope.inventory + facets.scope.recorded > 0) || rowPvs.some(pvInside),
    multiCluster: facets !== undefined ? facets.clusters.length >= 2
      : new Set(rowPvs.flatMap((pv) => pv?.volumes.map((v) => v.cluster) ?? [])).size >= 2,
    requesterFiltered: attr.requester !== undefined,
  };
  // 검색이 어디서 걸리나(2026-10-29 -- 서버 usage_attribution.may_attribute_pv 의 거울): PV 이름이 생길 수 있는 스토리지(PV·둘 다
  // 종류의 등록 스토리지, 또는 PV 소속이 보이는 -- 기록 PV 포함 -- 스토리지)에선 토큰이 PV 이름에만 맞을 수 있어 서버가 SQL 로 먼저
  // 거르지 못하고 PV 판정 창 안에서 거른다 -- 창이 넘쳐도 검색어가 모두 경로에 든 대상은 창 밖까지 SQL 로 더한다(리뷰 반영,
  // routes_usage._with_path_hits -- 경로 검색은 예전 path 처럼 창에 묶이지 않는다). 그 밖(filesystem·미등록)은 경로뿐이라 SQL 로 먼저,
  // 정확히 건다. 포탈은 종류(등록 목록의 kind)와 보이는 소속으로 가린다 -- 기록뿐인 PV 가 창 밖에만 있는 드문 스토리지에선 범위 문구만
  // 어긋난다(거르는 것은 서버다).
  const reg = tab.registered ? storages?.find((s) => s.storage_name === storage) : undefined;
  const pvAttributable = (reg !== undefined && storageKindOf(reg) !== "filesystem") || pvDisplay.pvColumn;
  const searchInWindow = searchQ !== "" && pvAttributable;
  const pvFilterOn = hasPvAxis(attr) || attr.scope !== undefined;
  // PV 축·구분, 또는 창 안에서 거는 검색이 있으면 서버는 최근 스캔된 창(window_limit) 안에서 PV 소속을 판정한다(routes_usage) --
  // 창을 넘은 결과의 말.
  const pvPath = pvFilterOn || searchInWindow;
  // 그 창의 이름(2026-10-29 리뷰): 서버는 요청자(와 PV 이름이 생길 수 없는 스토리지의 검색)를 SQL 로 먼저 걸고, 그 결과의 **최근
  // 스캔 순** 창 안에서만 PV 소속·검색을 판정한다 -- 정렬(오래된 순·스캔 횟수·지표)과 무관하다(routes_usage._select_heads; /facets·
  // 필터 합계·내보내기도 같은 창). 화면 문단·지표 정렬 각주·빈 상태·CSV 문구가 이 한 벌로 범위를 말한다(예전엔 SQL 로 먼저 좁힌 것을
  // 빼고 말했다).
  const narrowOf = (q: string) => [q !== "" && !pvAttributable && "검색", attr.requester !== undefined && "요청자"]
    .filter(Boolean).join("·");
  const sqlNarrow = narrowOf(searchQ);
  const pvRange = (w: number, narrow = sqlNarrow) =>
    (narrow ? `${narrow}에 맞는 대상 중 최근 스캔된 ${n(w)}개` : `최근 스캔된 ${n(w)}개 대상`);
  const pvOver = (w: number) => (sqlNarrow ? `${sqlNarrow}에 맞는 대상이 ${n(w)}개를 넘어` : `대상이 ${n(w)}개를 넘는 스토리지라`);
  // 판정 창이 넘친 결과(matched 모름)에서 창 안에서만 판정한 것의 이름(문단·CSV 문구의 주어)과 그 동사, 그리고 경로 쪽의 말(2026-10-29
  // 리뷰): 창 안에서 거는 검색이 끼면 서버는 검색어가 모두 경로에 든 대상을 창 밖까지 SQL 로 찾는다(PV 필터가 함께면 그 경로 일치끼리의
  // 최근 창에서 소속을 판정해 거른다 -- 예전 「경로 검색 + PV 필터」와 같다). 그래서 창에 묶이는 것은 PV 필터와 「PV 이름으로 맞는」
  // 대상뿐이고, 행에 창 밖의 경로 일치가 섞이므로 「최근 N개 안에서 k개 맞음」이라 말하지 않는다. PV 필터만이면 검색칸(늘 보인다)이
  // 범위를 좁히는 길이다(검색어가 경로에 든 대상끼리의 창) -- 요청자 필터는 권하지 않는다(선택지가 하나면 그 칸이 없다, 리뷰).
  const [windowedTopic, windowedVerb, windowedDone] = !searchInWindow ? ["PV 필터는", "거릅니다", "걸렀습니다"]
    : pvFilterOn ? ["PV 필터와 PV 이름 검색은", "판정합니다", "판정했습니다"]
    : ["PV 이름으로 맞는 대상은", "찾습니다", "찾았습니다"];
  const pathAside = (w: number) => !searchInWindow ? "검색으로 범위를 좁히세요"
    : pvFilterOn ? `검색어가 모두 경로에 든 대상은 그것끼리 최근 스캔된 ${n(w)}개 안에서 PV 필터로 거릅니다`
    : "검색어가 모두 경로에 든 대상은 범위 밖까지 찾습니다";

  // 포커스 복귀(2026-10-29 리뷰): 「필터 지우기」(필터 줄·빈 상태)와 값 때문에만 보이던 칸(단일 클러스터 스토리지에서 namespace 를
  // 고르면 나타나는 클러스터 select 등)은 누르거나 고르는 순간 사라진다 -- 브라우저는 포커스를 <body> 로 떨어뜨리고 화면 낭독기는
  // 자리를 잃는다(JobsList 의 같은 결함). 이 패널이 낸 필터 변경이 URL 을 거쳐 그려진 뒤에도 포커스가 <body> 면 필터 줄(group)로,
  // 줄이 없으면 툴바로, 툴바도 없으면 패널 자체(tabIndex -1)로 옮긴다(스크롤 없이). 포커스가 살아 있는 칸에 있으면(보통의 선택)
  // 건드리지 않는다. 새 필터의 목록이 자리 잡을 때까지(첫 응답·실패) 이 확인을 렌더마다 되풀이한다(2차 리뷰): 기록 0 스토리지에서 필터를
  // 지우면 응답 전엔 툴바가 있다가 응답이 「기록 없음」이면 툴바가 사라져 옮겨 둔 포커스가 다시 <body> 로 떨어졌다.
  const filtersRef = useRef<HTMLDivElement>(null);
  const toolbarRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const refocusFor = useRef<string | null>(null);
  const changeAttr = (a: UsageAttrFilter) => { refocusFor.current = attrKey(cleanAttr(a)); onAttrChange(a); };
  const currentKey = attrKey(attr);
  const listSettled = !targets.isPending && !targets.isPlaceholderData;
  useEffect(() => {
    if (refocusFor.current === null || refocusFor.current !== currentKey) return;
    if (listSettled) refocusFor.current = null;
    if (document.activeElement !== null && document.activeElement !== document.body) return;
    (filtersRef.current ?? toolbarRef.current ?? panelRef.current)?.focus({ preventScroll: true });
  });

  // 시트 포커스 복귀(2026-10-29): 닫히면(Esc·✕·덮개·뒤로 가기) 마지막으로 보던 경로의 행 버튼으로 -- ↑↓로 옮겼으면 마지막 경로.
  // 그 행이 지금 표에 없으면(목록 밖 딥링크, 시트 안 필터로 목록이 바뀌는 중) 위의 필터 줄 → 툴바 → 패널 순서로. Radix 는 닫힌 뒤
  // 한 틱 늦게 이것을 부른다 -- 그때의 표를 본다.
  const rowButtons = useRef(new Map<string, HTMLButtonElement>());
  const lastTarget = useRef<string | null>(activeTarget);
  useEffect(() => { if (activeTarget !== null) lastTarget.current = activeTarget; }, [activeTarget]);
  const returnFocus = (e: Event) => {
    e.preventDefault();
    const btn = lastTarget.current === null ? undefined : rowButtons.current.get(lastTarget.current);
    if (btn?.isConnected) btn.focus();
    else (filtersRef.current ?? toolbarRef.current ?? panelRef.current)?.focus({ preventScroll: true });
  };
  // 시트 안 소속 글자(PV 이름·권한 사용자) = 그 필터. 다른 필터는 남기고, PV 축과 함께 걸 수 없는 구분(「PV 밖」·「모름」 -- 늘
  // 0건)만 푼다(UsageFilters 의 위계와 같은 규칙). 필터 변경이라 포커스 지킴(refocusFor)도 함께 건다.
  const sheetFilter = (a: UsageAttrFilter) => {
    const next: UsageAttrFilter = { ...attr, ...a };
    if (next.scope !== undefined && next.scope !== "pv") delete next.scope;
    refocusFor.current = attrKey(cleanAttr(next));
    sheet.filter(next);
  };
  // 배포 틈(옛 API·필터 무시 -- useUsage): 목록이 틀렸을 수 있으니 CSV 도 막는다(같은 엔드포인트 계열).
  const contractError = targets.error instanceof UsageContractError ? targets.error : null;

  // CSV 내보내기(2026-10-02 "사용량 분석 전체의 모든 정보, 항목당 한 줄" → 2026-10-20 스토리지 하나씩): 지금 스토리지 + 검색
  // + 필터 + 정렬 그대로, 행 상한 없이(서버 상한을 넘으면 잘렸다고 말한다). 조립은 usageCsv(화면과 같은 hot 비율·구간 규칙 -- 열은
  // 검색과 무관하다, k8s_* 열이 PV 소속 전부). 잘림은 둘이고 함께일 수 있다(2026-10-29 2차 리뷰): 알려진 결과가 상한을 넘음(봉투
  // capped -- 「상한 N개까지만」, 검색·필터로 좁힌다) / PV 필터·창 안 검색이 판정 창을 넘은 스토리지(matched 모름 -- 창 밖은 PV
  // 소속을 판정하지 않았다, 창 문구). 창 안 검색은 서버가 창 밖의 경로 일치까지 더하므로(routes_usage._with_path_hits) 창이 넘친 채로
  // 상한에서도 잘릴 수 있다 -- 예전엔 창 문구만 내 「PV 이름 일치만 창 안」으로 읽혀, 잘린 경로 일치가 안 보였다. 행 수가 상한과 같은
  // 것만으로는 「잘렸다」고 하지 않는다(서버가 자르지 않았는데 꼭 상한만큼일 수 있다 -- capped 가 판정).
  const [exporting, setExporting] = useState(false);
  const [exportMsg, setExportMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const exportCsv = async () => {
    setExporting(true); setExportMsg(null);
    try {
      const body = await fetchUsageExport(filter);
      const at = Date.now();
      const { columns, records } = buildUsageCsv(body.rows, storages, at);
      downloadCsv(usageCsvFilename(at, storage, filtersOn), toCsv(columns, records));
      const w = page?.window_limit ?? windowLimit;
      const windowed = !(pvPath && body.matched_count === null) ? null
        : w !== null ? `${pvOver(w)} ${windowedTopic} ${pvRange(w)} 안에서만 ${windowedDone}`
        : `대상이 많은 스토리지라 ${windowedTopic} 최근 스캔된 대상 안에서만 ${windowedDone}`;
      const capped = `상한 ${n(body.count)}개까지만 내보냈습니다`;
      setExportMsg({ ok: true, text: !body.truncated ? `${n(body.count)}개 항목을 내보냈습니다`
        : windowed === null ? `${capped} — 검색·필터로 범위를 좁히세요`
        : body.capped ? `${capped}. ${windowed} — 검색·필터로 범위를 좁히세요`
        : `${windowed} — CSV 가 전부가 아닐 수 있습니다` });
    } catch (e) {
      setExportMsg({ ok: false, text: (e as ApiError).message || "내보내기에 실패했습니다" });
    } finally {
      setExporting(false);
    }
  };

  // 건수 문구(2026-10-29 -- 서버 봉투의 matched_count, 스토리지의 대상 수가 아니다: 필터·검색이 걸리면 그 수는 거짓이다). 필터·검색이
  // 없으면 「9개」 또는 목록 상한(100)에 닿아 「상위 100개 표시 · 전체 N개」, 있으면 「필터에 맞는 N개」(+ 「· 상위 100개 표시」).
  // matched 모름(PV 필터·창 안 검색이 판정 창을 넘은 스토리지)이면 건수 대신 그 사실을 한 문단으로(아래 pvWindowNote).
  const full = rows.length >= LIST_LIMIT;
  const matched = page?.matched_count ?? null;
  const narrowed = filtersOn || searchQ !== "";
  const countText = page === undefined || matched === null ? null
    : !narrowed ? (rows.length < matched ? `상위 ${n(rows.length)}개 표시 · 전체 ${n(matched)}개` : `${n(matched)}개`)
    : `필터에 맞는 ${n(matched)}개${rows.length < matched ? ` · 상위 ${n(rows.length)}개 표시` : ""}`;
  // 자리표시(같은 필터 안에서 검색·정렬을 바꿔 응답을 기다리는 동안 남긴 이전 결과)는 건수처럼 수를 말하지 않는다(2차 리뷰:
  // 문단·각주가 이전 결과의 「N개 맞음」을 지금 입력의 범위 이름으로 말했다 -- 아무도 확인하지 않은 수와 이유). settled = 지금
  // 필터·검색의 응답일 때만의 봉투.
  const settled = targets.isPlaceholderData ? undefined : page;
  const shownCount = full ? `${n(LIST_LIMIT)}개 이상` : `${n(rows.length)}개`;
  const pvWindowNote = settled === undefined || matched !== null ? null
    : !searchInWindow
      ? `${pvRange(settled.window_limit)} 안에서 ${shownCount} 맞음 — ${pvOver(settled.window_limit)} ${windowedTopic} 이 범위에서만 `
        + `${windowedVerb}. ${pathAside(settled.window_limit)}`
      : `${shownCount} 맞음 — ${pvOver(settled.window_limit)} ${windowedTopic} ${pvRange(settled.window_limit)} 안에서만 `
        + `${windowedVerb}. ${pathAside(settled.window_limit)}`;
  // 지금 행을 낸 검색(봉투 메아리의 q -- 자리표시는 같은 필터 안에서만 남으므로 요청자는 지금과 같다): 강조·빈 상태의 범위 이름은
  // 이것으로 말한다(자리표시 동안 지금 입력으로 강조하면 이전 결과의 글자에 새 검색어를 칠한다).
  const shownQ = targets.isPlaceholderData ? (page?.filters.q ?? "") : searchQ;
  const highlight = searchTokens(shownQ);
  // PV 판정 창이 넘친 결과(맞는 수 모름) -- 각주·빈 상태가 「필터 → 창」이 아니라 「창 → 필터」 순서로 말한다. 빈 상태는 자리표시
  // 동안에도 보이므로(이전 결과가 0행) 「없다」를 단정하지 않게 이전 결과의 창 여부를 그대로 쓴다.
  const pvWindowed = page !== undefined && page.window_truncated && (pvFilterOn || (shownQ !== "" && pvAttributable));

  const metricSort = METRIC_SORTS.includes(sort);
  // 창 안 정렬 각주: SQL 경로(요청자·PV 이름이 생길 수 없는 스토리지의 검색)는 그것을 건 **뒤의** 대상에서 창을 만든다 -- 그
  // 수(matched)가 창을 넘을 때만 「필터에 맞는 대상 중 최근 N개 안에서」. PV 필터만 건 PV 경로는 거꾸로 창을 먼저 만들고 그 안에서
  // PV 필터를 건다 -- 판정 창이 넘쳤으면 「최근 N개 대상 안에서 PV 필터에 맞는 것끼리」(위 문단과 같은 범위). 창 안 검색이 끼면 서버가
  // 창 밖의 경로 일치까지 더한 뒤 그 최근 N개 안에서 정렬한다(routes_usage._with_path_hits) -- SQL 경로와 같은 말. 봉투가 정확한 수를
  // 주므로 조건부 추정 문구는 없다. 자리표시 동안은 말하지 않는다(위 settled -- 범위 이름은 지금 입력, 창 여부는 이전 결과라 섞인다).
  const overWindow = settled !== undefined
    && (settled.window_truncated || (matched !== null && matched > settled.window_limit));
  const windowNote = !overWindow ? null
    : pvWindowed && !searchInWindow ? `${pvRange(settled!.window_limit)} 안에서 PV 필터에 맞는 것끼리 정렬`
    : `${narrowed ? "필터에 맞는 대상 중 " : ""}최근 스캔된 ${n(settled!.window_limit)}개 안에서 정렬`;
  const failedOnly = tab.unregistered !== null && tab.unregistered.target_count === 0;
  // 기록이 하나도 없다 = 검색도 필터도 없이 빈 목록. 필터가 켜진 빈 목록은 「필터에 맞는 대상이 없다」 -- 툴바(필터 줄)를 남겨
  // 풀 수 있게 한다.
  const noRecords = page !== undefined && !targets.isPlaceholderData && rows.length === 0
    && searchQ === "" && input === "" && !filtersOn;

  return (
    <section ref={panelRef} tabIndex={-1} aria-label={storageLabel(storage)} className="space-y-4 outline-none">
      {!tab.registered && (
        <UnregisteredBanner name={tab.name} row={tab.unregistered} summary={tab.summary} purge={purge} progress={progress}
                            setProgress={setProgress} onResultFocus={onResultFocus} now={now} />
      )}

      {/* 기록이 하나도 없으면(검색 없이 빈 목록) 검색·CSV 는 할 일이 없다 -- 빈 상태 안내만. 도구 줄: 넓은 폭은 [검색 20rem][건수]
          ……[CSV], 375 는 [검색 ……][CSV 아이콘] 다음 줄에 건수(order -- DOM 순서는 검색 → 건수 → CSV 그대로). 그 아래 CSV 결과 한 줄,
          그 아래 필터 줄. */}
      {!noRecords && <div ref={toolbarRef} tabIndex={-1} className="outline-none">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          {/* 검색칸 + 규칙 설명(HelpTip -- 터치·마우스, 입력칸의 aria-describedby -- 낭독기는 칸에 들어갈 때 듣는다). 길이 상한은 서버
              q 의 max_length(넘으면 422). */}
          <div className="order-1 flex min-w-0 flex-1 items-center gap-0.5 sm:flex-none">
            <label className="relative min-w-0 flex-1 sm:w-80 sm:flex-none">
              <span className="sr-only">{SEARCH_LABEL}</span>
              <Search aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" />
              <input value={input} onChange={(e) => setInput(e.target.value)} type="search" maxLength={512}
                     placeholder={`${SEARCH_LABEL}…`} aria-describedby={searchRuleId}
                     className="w-full rounded-lg border border-line bg-surface py-1.5 pl-8 pr-3 text-sm
                                placeholder:text-muted focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20" />
            </label>
            <span id={searchRuleId} hidden>{SEARCH_RULE}</span>
            <HelpTip label={`${SEARCH_LABEL} 설명`} head={SEARCH_LABEL} help={SEARCH_RULE} />
          </div>
          {/* 검색·정렬을 바꾸는 동안(이전 행이 자리표시) 건수는 옛 목록의 것이라 말하지 않는다. */}
          <span className="order-3 basis-full whitespace-nowrap text-sm text-muted sm:order-2 sm:basis-auto">
            {targets.isPlaceholderData ? "검색 중…" : countText}
          </span>
          {/* 375 는 아이콘 버튼(글자는 sr-only 로 남아 이름이 된다 -- 「내보내는 중…」도 낭독된다). */}
          <Button type="button" variant="outline" className="order-2 ml-auto gap-1.5 whitespace-nowrap py-1.5 sm:order-3"
                  disabled={exporting || contractError !== null} title={contractError?.message}
                  onClick={() => { void exportCsv(); }}>
            <Download aria-hidden className="h-4 w-4" />
            <span className="max-sm:sr-only">{exporting ? "내보내는 중…" : "CSV 내보내기"}</span>
          </Button>
        </div>
        {exportMsg && (
          <p role={exportMsg.ok ? "status" : "alert"}
             className={`mt-2 text-right text-xs ${exportMsg.ok ? "text-muted" : "text-bad"}`}>{exportMsg.text}</p>
        )}
        <UsageFilters attr={attr} facets={facets} onChange={changeAttr} groupRef={filtersRef} />
        {pvWindowNote !== null && <p className="mt-2 text-xs text-attn break-keep">{pvWindowNote}</p>}
      </div>}

      {/* 합계 줄(2026-10-21 합계 → 2026-10-29 한 줄 + [자세히]) -- 기록이 있다고 알거나(대상 수 > 0) 모를 때(요약 실패 -- null)만.
          0 이면 빈 상태가 말한다(실패 기록만 남은 미등록 스토리지도 0). 미등록 스토리지는 옛 기록의 합계(df 는 없음). 필터가 켜지면 같은
          자리가 필터 합계가 된다(검색은 반영하지 않는다 -- 검색어가 있으면 줄이 그렇게 말한다). */}
      {tab.targetCount !== 0 && <TotalsLine storage={storage} now={now} attr={attr} searchQ={searchQ} />}

      {targets.isLoading || (page === undefined && !targets.isError) ? (
        <div className="space-y-2.5 py-1">
          <span className="sr-only">불러오는 중…</span>
          {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-5" />)}
        </div>
      ) : targets.isError && page === undefined ? (
        <p className="text-sm text-bad">{(targets.error as Error).message}</p>
      ) : rows.length === 0 ? (
        filtersOn ? (
          // PV 판정 창이 넘쳤으면 「없다」를 단정하지 않는다 -- 그 창 안에 없을 뿐이다(창 밖은 판정하지 않았다).
          <Empty icon={<FilterX className="h-5 w-5" />}
                 title={pvWindowed ? `${pvRange(page!.window_limit, narrowOf(shownQ))} 안에는 필터에 맞는 대상이 없습니다`
                   : "필터에 맞는 대상이 없습니다"}
                 hint={shownQ !== "" ? `검색 ‘${shownQ}’ 도 함께 걸려 있습니다` : undefined}
                 action={<Button type="button" variant="ghost" className="mt-1 py-1.5"
                                 onClick={() => changeAttr({})}>필터 지우기</Button>} />
        ) : searchQ !== "" ? (
          // 창 안에서 건 검색(PV 이름이 생길 수 있는 스토리지)이 판정 창을 넘었으면 PV 이름 쪽은 그 창 안에 없을 뿐이다 -- 「없다」를
          // 단정하지 않는다. 경로 쪽은 창 밖까지 찾았다(서버 _with_path_hits)는 것을 보조 줄로.
          <Empty icon={<SearchX className="h-5 w-5" />}
                 title={pvWindowed ? `${pvRange(page!.window_limit, narrowOf(shownQ))} 안에는 ‘${searchQ}’ 검색 결과가 없습니다`
                   : `‘${searchQ}’ 검색 결과가 없습니다`}
                 hint={pvWindowed ? "검색어가 모두 경로에 든 대상은 범위 밖까지 찾았습니다" : undefined}
                 action={<Button type="button" variant="ghost" className="mt-1 py-1.5"
                                 onClick={() => setInput("")}>검색 지우기</Button>} />
        ) : failedOnly ? (
          <Empty icon={<ScanSearch className="h-5 w-5" />} title="성공한 scan 기록이 없습니다"
                 hint="실패·취소된 작업 기록만 남아 있습니다 — 위의 「기록 정리」로 지울 수 있습니다." />
        ) : (
          <Empty icon={<ScanSearch className="h-5 w-5" />} title="이 스토리지에는 아직 성공한 scan 이 없습니다"
                 hint={<>scan 작업이 끝나면 경로별 실 사용량과 데이터 온도가 여기에 쌓입니다.{" "}
                   <Link to="/jobs/new" className="text-accent hover:underline">단일 작업</Link></>} />
        )
      ) : (
        <TargetTable rows={rows} activeTarget={activeTarget} onOpen={sheet.open} buttonRefs={rowButtons}
                     sort={sort} order={order} onSort={onSort} now={now} pvDisplay={pvDisplay} highlight={highlight} />
      )}
      {targets.isError && page !== undefined && (
        <p className="text-sm text-bad">{`목록을 새로 고치지 못했습니다 — ${(targets.error as Error).message}`}</p>
      )}

      {/* 각주 한 줄: 값의 기준 + 경과 색 범례(글자색이 곧 범례). 지표 정렬이면 모름의 자리, 맞는 대상 수가 서버 창
          (window_limit)을 넘으면 창 안에서 정렬한다는 사실 -- 숫자는 서버 값이다(하드코딩한 「5,000」이 상한 변경과 어긋났다).
          구분 기호 앞은 줄바꿈 없는 공백이라 접힌 줄이 「·」로 시작하지 않는다. */}
      {rows.length > 0 && (
        <p className="text-xs text-muted break-keep">
          최근 성공 scan 기준{SEP}최근 스캔 <span className="font-medium text-attn">30일↑</span>
          {SEP}<span className="font-medium text-bad">90일↑</span>
          {metricSort && <>{SEP}<span>값 모름(—)은 맨 뒤</span></>}
          {metricSort && windowNote !== null && <>{SEP}<span>{windowNote}</span></>}
        </p>
      )}

      {/* 경로 상세 시트(?target=). 목록 밖 경로(옛 딥링크·검색으로 빠진 경로)도 target 만으로 열린다 -- 예전의 표 아래 폴백
          칸 대신 시트가 「지금 목록 밖」이라 말한다. 목록이 자리 잡기 전(첫 응답·자리표시)엔 그 판정을 미룬다. */}
      <TargetSheet storage={storage} target={activeTarget} rows={rows} listSettled={settled !== undefined}
                   pvDisplay={pvDisplay} onMove={sheet.move} onClose={sheet.close} onFilter={sheetFilter}
                   onCloseAutoFocus={returnFocus} />
    </section>
  );
}
