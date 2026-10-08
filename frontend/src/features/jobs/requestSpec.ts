// 요청 상세 「요청 내용」 카드 + 머리말 root 배지의 판정(2026-10-08, 사용자 요청: "대상·절대경로·요청자·제출대기·
// 수행시간 등은 있는데 더 자세한 요청 옵션들이나 root 권한 실행 등에 대한 내용이 전부 빠져 있다"). 순수 함수만 둔다 --
// 카드(RequestSpecCard)와 배지가 이 한 모델을 읽으므로 둘이 서로 다른 말을 할 수 없다.
//
// 입력은 전부 DB 신뢰 경계 밖이다(create_job 무검증 INSERT 전제): payload·worker_pool·identity·options·precondition
// 어느 것이든 모양이 틀릴 수 있고, 그래도 던지지 않는다. 규약:
//   - truthy 검사를 쓰지 않는다. 참은 `=== true` 만, 수는 정수 검사를 거친다 -- uid 0·gid 0·batch_files 0 은 정상값.
//   - 모름(행 생략·「기록 없음」·「계획 단계에서 정해집니다」), 없음(빈 값 확정), 기본값(서버 기본과 같은 값)은 다른 문구다.
//   - root 판정의 진실은 계획 스냅숏(worker_pool.identity.privileged)이고, 계획 전에는 요청 의도만 본다
//     (identity.privilege_policy 미러: payload.run_as_root === true → 요청, batch_id(파이썬 truthy) → 자격 있으면,
//     그 밖 → 절대 아님). uid 0 은 root 의 근거로 쓰지 않는다.
//   - 고정 단위(「uid 10001」·「gid 10000(주 그룹)」)는 줄바꿈 없는 공백(U+00A0)으로 묶는다 -- 좁은 폭에서 「gid」와
//     숫자가 갈리지 않게. 화면 테스트는 공백을 정규화해 비교한다.
import type { DataJob, RequestDetail, WorkerPool, WorkerPoolIdentity } from "../../lib/types";
import { REQUEST_TERMINAL_STATES, isTerminal } from "../../lib/jobState";
import { absParts, pathParts, type StorageRoots } from "../../lib/storagePaths";
import { toolSummary } from "../../lib/jobTool";
import { groupCaveatsFor, supplementaryGroupsText } from "../../lib/groupCaveats";
import { syncOwnership } from "../../lib/syncOwnership";
import { countText, humanBytes, isPlainObject } from "./format";
import { PRIV_ROOT_HEAD, PRIV_USER, SCAN_INT_FIELDS, SYNC_INT_FIELDS, SYNC_OPTION_HELP } from "./optionRules";

// head 는 첫 「 — 」 앞(lead)만 굵게 그린다(카드). bad = 전부 빨강(root 로 계획됨), badLead = lead 만 빨강(요청됨 --
// 뒤의 「실행되지 않았습니다」 같은 설명까지 경보색으로 칠하지 않는다). head 문구는 전부 이 파일의 상수라(사용자 값이
// 섞이지 않는다) 「 — 」로 갈라도 안전하다.
export type LineTone = "plain" | "sub" | "attn" | "bad" | "badLead";
export interface Line { text: string; tone: LineTone }
/** 실행 노드 한 묶음(dsync = side null, nsync = 소스/목적지). 카드는 이름 하나를 줄바꿈 없이 그린다(「dms-」/「w5」 방지). */
export interface NodeGroup { side: "소스" | "목적지" | null; names: string[]; more: number }

export type OptionsView =
  | { state: "missing" }                     // payload·잡 둘 다 options 가 없다(옛 행)
  | { state: "malformed" }                   // 객체가 아니다(문자열·배열·숫자…)
  | { state: "ok";
      set: { token: string; note: string | null }[];   // 칩 + (출처 있는) 설명
      defaults: string[];                               // 서버 기본과 같은 값 "batch_files=1000000"
      unknown: string[];                                // 카탈로그 밖·모양이 틀린 값의 원문(60자 상한)
      noatimeWarn: boolean };

export type PrivKind = "root" | "user" | "batch";
export type PrivBasis = "plan" | "request";

export interface RequestSpec {
  target: string[];                    // pathParts(sync = [출발, 도착], 그 밖 = [대상]) -- 카드가 「 → 」로 잇는다
  abs: string[] | null;                // absParts. null = 행 생략
  options: OptionsView;
  priority: string;
  nodeRequest: string | null;          // 「노드 지정」(배치 자식 payload). null = 행 생략
  privilege: { head: Line; subs: Line[] };
  runAs: { name: string; relation: string; tail: string };
  groups: { text: string; caveats: string[] } | null;
  ownership: string | null;            // 「목적지 소유」(sync). null = 행 생략
  // 「실행 도구」. null = 행 생략. summary 와 fallback 중 하나만 non-null.
  tool: { summary: string | null; tail: string | null; fallback: string | null; nodes: NodeGroup[] | null } | null;
  multiJob: boolean;
  badge: "run" | "requested" | null;   // 머리말 root 배지
}

// ---- 원시 정규화 ----------------------------------------------------------------------------------------------
const obj = (v: unknown): Record<string, unknown> | null => (isPlainObject(v) ? v : null);
const name = (v: unknown): string | null => (typeof v === "string" && v.trim() !== "" ? v.trim() : null);
// boolean 은 typeof 로 배제된다. 0 은 정상값.
const int = (v: unknown): number | null => (typeof v === "number" && Number.isInteger(v) ? v : null);
const posInt = (v: unknown): number | null => { const n = int(v); return n !== null && n > 0 ? n : null; };
const strs = (v: unknown): string[] =>
  (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && x !== "") : []);
/** 파이썬 truthiness(서버 `if req.get("batch_id")` 미러): None·False·0·""·[]·{} 만 거짓 -- " " 도 참이다. */
const pyTruthy = (v: unknown): boolean =>
  !(v === null || v === undefined || v === false || v === 0 || v === ""
    || (Array.isArray(v) && v.length === 0) || (isPlainObject(v) && Object.keys(v).length === 0));
const NB = " ";      // 줄바꿈 없는 공백(고정 단위 묶음)
const LIST_CAP = 12;      // 실행 노드·「그 밖에 기록된 값」 표시 상한(넘친 수는 「외 N…」)

/** 알 수 없는 값의 안전한 원문 `k=v`(60자 상한). 문자열은 따옴표째(숫자 "5" 와 5 를 구별), 직렬화 불가(BigInt·순환)는 "?". */
export function rawText(k: string, v: unknown): string {
  let s: string;
  try {
    s = typeof v === "string" ? JSON.stringify(v)
      : v === undefined ? "undefined" : (JSON.stringify(v) ?? String(v));
  } catch {
    s = "?";
  }
  return cap60(`${k}=${v === null ? "(값 없음)" : s}`);
}
/** 표시 글자 60자 상한(변조 행이 칩 하나에 수 KB 를 싣지 않게). */
function cap60(t: string): string {
  return t.length > 60 ? `${t.slice(0, 59)}…` : t;
}

// ---- 옵션 ------------------------------------------------------------------------------------------------------
// 카탈로그 = domain._OPTION_SPECS 미러(표시 순서도 이 순서). 기본값은 optionRules 의 프리필(= 서버 _OPTION_DEFAULTS,
// test_domain_option_defaults 가 고정)에서 읽는다 -- 사본을 만들지 않는다. 설명(note)은 출처가 있는 문구만:
// delete·contents 는 제출 폼 설명, batch_files 0 은 제출 폼 placeholder, broken_limit 0 은 domain 스펙 주석.
type OptKind = "bool" | "int" | "text";
interface OptSpec { key: string; kind: OptKind; def?: number; note?: (v: number | true) => string | null; omitTrue?: boolean }
const CATALOG: Record<string, OptSpec[]> = {
  sync: [
    { key: "delete", kind: "bool", note: () => SYNC_OPTION_HELP.delete },
    { key: "contents", kind: "bool", note: () => SYNC_OPTION_HELP.contents },
    { key: "direct", kind: "bool" },
    { key: "quiet", kind: "bool" },
    { key: "open_noatime", kind: "bool" },
    { key: "batch_files", kind: "int", def: Number(SYNC_INT_FIELDS.batch_files.prefill),
      note: (v) => (v === 0 ? "배칭 끔" : null) },
    { key: "bufsize", kind: "int", def: Number(SYNC_INT_FIELDS.bufsize.prefill),
      note: (v) => (typeof v === "number" ? humanBytes(v) : null) },
    { key: "chmod", kind: "text" },
    { key: "chown", kind: "text" },
  ],
  scan: [
    { key: "verbose", kind: "bool" },
    { key: "quiet", kind: "bool" },
    { key: "batch_files", kind: "int", def: Number(SCAN_INT_FIELDS.batch_files.prefill),
      note: (v) => (v === 0 ? "배칭 안 함" : null) },
    { key: "broken_limit", kind: "int", def: Number(SCAN_INT_FIELDS.broken_limit.prefill),
      note: (v) => (v === 0 ? "파손 경로 표본을 보관하지 않음(총계는 정확)" : null) },
  ],
  // recursive 는 모든 rm 의 필수 동의 게이트(validate_rm_target)라 플래그로 렌더되지 않는다 -- true 는 칩으로 보이지 않는다.
  rm: [
    { key: "recursive", kind: "bool", omitTrue: true },
    { key: "stat", kind: "bool" },
    { key: "lite", kind: "bool" },
    { key: "quiet", kind: "bool" },
  ],
};

/** 옵션 출처 고르기: payload.options 가 객체면 그것, 아니면 잡의 options(계획 때 payload 에서 복사).
    둘 다 객체가 아니면: 둘 다 없음(undefined/null) = missing, 그 밖 = malformed. */
function pickOptions(payloadOpts: unknown, jobOpts: unknown):
    { state: "ok"; opts: Record<string, unknown> } | { state: "missing" } | { state: "malformed" } {
  const o = obj(payloadOpts) ?? obj(jobOpts);
  if (o !== null) return { state: "ok", opts: o };
  // 둘 다 없을 때만 「기록 없음」 -- 어느 쪽이든 객체가 아닌 값이 실려 있으면 기록은 있는데 읽을 수 없는 것이다.
  const absent = (v: unknown) => v === undefined || v === null;
  return absent(payloadOpts) && absent(jobOpts) ? { state: "missing" } : { state: "malformed" };
}

/** 저장된 옵션 → 칩·기본값·원문. 저장된 뒤에는 명시값과 서버가 채운 기본값을 구분할 수 없다(domain.validate_options)
    -- 그래서 기본과 같은 값은 둘 다 「기본값」이다. noatimeWarn: 비 root sync 의 open_noatime 은 남의 파일에서 EPERM 이라
    실패 원인이 될 수 있다(성공한 요청에서는 거짓 경보라 끈다). */
export function normalizeOptions(op: string, src: { payload: unknown; job?: unknown },
                                 ctx: { kind: PrivKind; reqState: unknown }): OptionsView {
  const picked = pickOptions(src.payload, src.job);
  if (picked.state !== "ok") return picked;
  const opts = picked.opts;
  const catalog = Object.prototype.hasOwnProperty.call(CATALOG, op) ? CATALOG[op] : [];
  const set: { token: string; note: string | null }[] = [];
  const defaults: string[] = [];
  const unknown: string[] = [];
  const known = new Set(catalog.map((c) => c.key));
  for (const spec of catalog) {
    if (!Object.prototype.hasOwnProperty.call(opts, spec.key)) continue;   // 옛 행 -- 키가 없으면 말하지 않는다
    const v = opts[spec.key];
    if (spec.kind === "bool") {
      if (v === true) {
        if (spec.omitTrue !== true) set.push({ token: spec.key, note: spec.note ? spec.note(true) : null });
      } else if (v === false && spec.omitTrue === true) {
        // rm recursive false 는 서버가 받지 않는 값(rm_recursive_required)이라 비정상 기록 -- 꺼짐으로 접지 않는다.
        unknown.push(rawText(spec.key, v));
      } else if (v !== false) {
        // 매니페스트는 `is True` 만 렌더한다 -- "yes"·1 은 적용되지 않았다.
        unknown.push(`${rawText(spec.key, v)} (적용 안 됨)`);
      }
    } else if (spec.kind === "int") {
      const n = int(v);
      if (n === null) unknown.push(rawText(spec.key, v));
      // bufsize 는 기본값 줄에도 사람 단위를 붙인다(기본과 다른 값의 칩 설명과 같은 표기).
      else if (spec.def !== undefined && n === spec.def)
        defaults.push(`${spec.key}=${n}${spec.key === "bufsize" ? `(${humanBytes(n)})` : ""}`);
      else set.push({ token: `${spec.key}=${n}`, note: spec.note ? spec.note(n) : null });
    } else if (typeof v === "string") {
      if (v !== "") set.push({ token: cap60(`${spec.key}=${v}`), note: null });
    } else {
      unknown.push(rawText(spec.key, v));
    }
  }
  // 카탈로그 밖 키 -- 변조 행이 키 수천 개를 실어도 글자 수는 상한 안에(넘친 것은 원문을 만들지 않고 개수만 센다).
  let extra = 0;
  for (const [k, v] of Object.entries(opts)) {
    if (known.has(k)) continue;
    if (unknown.length < LIST_CAP) unknown.push(rawText(k, v));
    else extra += 1;
  }
  if (extra > 0) unknown.push(`외 ${extra}개`);
  const noatimeWarn = op === "sync" && opts.open_noatime === true && ctx.kind === "user" && ctx.reqState !== "Succeeded";
  return { state: "ok", set, defaults, unknown, noatimeWarn };
}

// ---- 우선순위 --------------------------------------------------------------------------------------------------
const RANK: Record<string, number> = { low: 0, mid: 1, high: 2 };

/** 요청 우선순위(원문 low/mid/high) + 계획이 실제로 적용한 클래스(dms-<p>). 정책 상한으로 깎였을 때만 덧붙인다
    (placement._clamp_priority). 한국어 라벨은 새로 만들지 않는다(제출 폼·배치 상세와 같은 원문). */
export function priorityText(requested: unknown, priorityClass: unknown): string {
  const r = name(requested);
  const m = /^dms-(low|mid|high)$/.exec(typeof priorityClass === "string" ? priorityClass : "");
  const eff = m === null ? null : m[1];
  if (r === null) return eff === null ? "모름" : `${eff} 적용`;
  if (eff === null || eff === r) return r;
  if (Object.prototype.hasOwnProperty.call(RANK, r) && RANK[eff] < RANK[r]) return `${r} 요청 → ${eff} 적용(정책 상한)`;
  return `${r} 요청 → ${eff} 적용`;
}

// ---- 실행 권한 -------------------------------------------------------------------------------------------------
export interface PrivilegeInput {
  op: string;
  planned: boolean | null;     // identity.privileged (=== true / === false 만, 그 밖 = 모름)
  askedRoot: boolean;          // payload.run_as_root === true
  batch: boolean;              // 배치 자식
  jobs: unknown[] | null;      // null = 잡 조회 실패(모름)
  reqTerminal: boolean;
  jobTerminal?: boolean | null; // 가장 최근 작업이 종단인가(null·생략 = 작업 없음/모름)
  authMethod: unknown;
  // 계획 스냅숏은 root 인데 요청에 근거(run_as_root·배치)가 없어 stepper 가 다음 제출 전에 끊은 작업
  // (reason privilege_not_requested -- 2026-09-30 규칙 변경 때 진행 중이던 옛 작업·변조 행). root 로 「실행」됐다고 말하지 않는다.
  rootStopped?: boolean;
}

// 비 root 를 말로 확정한다(사용자 보고: "root 권한 실행 등에 대한 내용이 전부 빠져 있다" -- 비 root 화면에 「root」라는
// 글자가 하나도 없으면 관리자가 남의 신원으로 낸 요청(2026-09-30 사고 모양)이 root 로 돌았는지 문장에서 추론해야 한다).
// 배지는 여전히 root 일 때만(D3) -- 이건 카드 안 문장이다. PRIV_USER 상수 자체는 제출 요약과 같은 바이트로 둔다.
const USER_HEAD = `root 아님 — ${PRIV_USER}`;
// 2026-09-30(49791c3) 전에는 특권 목록의 관리자 세션 요청이 run_as_root 없이 root 로 계획됐다 -- 그 행은 지금도 DB 에
// 남아 있고(requests 는 보존 대상이 아니다) 그때 규칙대로 정상 실행된 기록이다. 변조 행과 화면에서 구별할 수 없으므로
// 두 원인을 다 말한다. 「서버가 거부합니다」는 아직 끝나지 않은 작업에만 -- stepper 가 다음 제출 직전에 재확인해 끊는다
// (PrivilegeNotRequestedAtStep). 이미 끝난 작업에 그 말을 붙이면 사실과 다르다.
const LEGACY_ROOT_NOTE = "요청 기록에는 root 지정이 없습니다 — 2026-09-30 규칙 변경(root 는 명시 요청만) 전에 계획된 작업이거나 비정상 기록입니다.";
const LEGACY_ROOT_LIVE = "요청 기록에는 root 지정이 없습니다 — 2026-09-30 규칙 변경 전에 계획된 작업이거나 비정상 기록이며, 이 상태의 root 실행은 서버가 다음 단계 제출 전에 거부합니다.";
export interface PrivilegeView {
  kind: PrivKind; basis: PrivBasis;
  privilege: { head: Line; subs: Line[] };
  badge: "run" | "requested" | null;
}

export function resolvePrivilege(i: PrivilegeInput): PrivilegeView {
  // 공유 토큰으로 만든 배치의 자식은 계획 전이어도 비 root 가 확정이다 -- root 자격(privilege_eligible)은 세션 인증만이고
  // 자식은 배치 생성 시점의 auth_method 를 물려받는다(batch_orchestrator._materialize). 「자격이 있으면 root」라고
  // 얼버무리면 바로 아래 「root 실행 불가」 줄과 한 칸에서 서로 다른 말을 한다.
  const tokenBatch = i.batch && i.authMethod === "token";
  const kind: PrivKind = i.planned === true ? "root" : i.planned === false ? "user"
    : i.askedRoot ? "root" : i.batch && !tokenBatch ? "batch" : "user";
  const basis: PrivBasis = i.planned !== null ? "plan" : "request";
  const nJobs = i.jobs === null ? null : i.jobs.length;
  const plain = (text: string): Line => ({ text, tone: "plain" });
  const sub = (text: string): Line => ({ text, tone: "sub" });
  let head: Line;
  const subs: Line[] = [];
  if (kind === "root" && basis === "plan" && i.rootStopped === true) {
    head = { tone: "badLead", text: "root(특권)로 계획됨 — 요청에 root 지정이 없어 서버가 실행을 중단했습니다" };
  } else if (kind === "root" && basis === "plan") {
    // sync 의 소유 결과(소스 소유 그대로)는 같은 카드의 「목적지 소유」 행이 말한다 -- 여기엔 root 라는 사실만.
    head = { text: PRIV_ROOT_HEAD, tone: "bad" };
    if (i.op === "rm") subs.push({ text: "삭제가 root 권한으로 수행됩니다", tone: "bad" });
  } else if (kind === "root") {
    // 실행되지 않은(계획 전·거부) root 요청은 "요청됨" 시제 -- root 로 돌았다고 단정하지 않는다.
    head = { tone: "badLead", text: nJobs === null ? "root(특권) 요청됨"
      : nJobs === 0 ? (i.reqTerminal ? "root(특권) 요청됨 — 작업이 만들어지지 않아 실행되지 않았습니다"
        : "root(특권) 요청됨 — 작업이 계획되면 확정됩니다")
      : "root(특권) 요청됨 — 작업 기록에 권한 정보가 없습니다" };
  } else if (kind === "batch") {
    head = plain(nJobs === null
      ? (i.reqTerminal ? "배치 항목 — 작업 기록을 불러오지 못해 실행 권한을 확인할 수 없습니다"
        : "배치 항목 — 배치를 만든 관리자에게 root 자격이 있으면 root(특권), 없으면 실행 신원 권한으로 실행됩니다(계획 단계에서 확정)")
      : nJobs === 0 ? (i.reqTerminal ? "배치 항목 — 작업이 만들어지지 않아 실행되지 않았습니다"
        : "배치 항목 — 배치를 만든 관리자에게 root 자격이 있으면 root(특권), 없으면 실행 신원 권한으로 실행됩니다(계획 단계에서 확정)")
      : "배치 항목 — 작업 기록에 권한 정보가 없습니다");
  } else if (i.planned === null && nJobs === null && !i.batch) {
    // 단건인데 작업 기록을 못 읽었다 -- 요청엔 root 지정이 없지만 2026-09-30 전 관리자 요청은 그것 없이 root 로 돌았다
    // (LEGACY_ROOT_NOTE). 공유 토큰 배치는 언제나 root 자격이 없어 아래 확정 문구 그대로다.
    head = plain("root 지정 없음 — 작업 기록을 불러오지 못해 실제 실행 권한은 확인하지 못했습니다");
  } else {
    head = plain(USER_HEAD);
  }
  if (i.batch && i.planned === true) subs.push(sub("배치 항목 — 배치를 만든 관리자의 root 자격이 적용됐습니다."));
  if (i.batch && i.planned === false)
    subs.push(sub("배치 항목 — 배치를 만든 관리자에게 root 자격이 없어 실행 신원 권한이 적용됐습니다."));
  if (tokenBatch && i.planned === null && !i.askedRoot)
    subs.push(sub(i.reqTerminal && nJobs === 0
      ? "배치 항목 — 작업이 만들어지지 않아 실행되지 않았습니다(공유 토큰 배치라 root 자격 없음)."
      : "배치 항목 — 공유 토큰(API)으로 만든 배치라 root 자격이 없어 실행 신원 권한으로 실행됩니다."));
  // 계획 스냅숏과 요청 의도가 어긋나면 조용히 덮지 않는다(DB 신뢰 경계 + 2026-09-30 이전 행).
  if (i.planned === true && !i.askedRoot && !i.batch && i.rootStopped !== true) {
    subs.push(i.jobTerminal === true ? sub(LEGACY_ROOT_NOTE) : { text: LEGACY_ROOT_LIVE, tone: "attn" });
  }
  if (i.planned === false && i.askedRoot)
    subs.push({ text: "요청은 root(특권)였지만 작업은 실행 신원 권한으로 계획됐습니다 — 비정상 기록입니다.", tone: "attn" });
  // 공유 토큰 배치가 계획된 뒤의 강등 이유. 단건은 head 가 이미 확정(비 root)이라 같은 사실의 반복이고, 계획 전은 위
  // 배치 줄이 같은 사실을 말한다.
  if (tokenBatch && i.planned === false) subs.push(sub("공유 토큰(API)으로 제출 — root 실행 불가"));
  const badge = kind !== "root" || i.rootStopped === true ? null : basis === "plan" ? "run" : "requested";
  return { kind, basis, privilege: { head, subs }, badge };
}

// ---- 조립 ------------------------------------------------------------------------------------------------------
function nodeGroup(side: NodeGroup["side"], list: string[]): NodeGroup {
  return { side, names: list.slice(0, LIST_CAP), more: Math.max(0, list.length - LIST_CAP) };
}

/** 실행 노드 묶음 → 한 줄 텍스트(테스트 비교용 -- 카드는 이름마다 줄바꿈 없는 span 으로 같은 글자를 그린다). */
export function nodesText(groups: NodeGroup[]): string {
  return `실행 노드 ${groups.map((g) =>
    `${g.side !== null ? `${g.side} ` : ""}${g.names.join(", ")}${g.more > 0 ? ` 외 ${g.more}대` : ""}`).join(" · ")}`;
}

export function deriveRequestSpec(req: RequestDetail, jobs: DataJob[] | null,
                                  roots: StorageRoots, backends: Record<string, string>): RequestSpec {
  const r = (obj(req) ?? {}) as Record<string, unknown>;
  const p = obj(r.payload) ?? {};
  const op = typeof r.operation === "string" ? r.operation : "";
  const requester = name(r.requester_id);
  const batch = pyTruthy(r.batch_id);                   // identity.privilege_policy 의 `if req.get("batch_id")` 미러
  const reqTerm = REQUEST_TERMINAL_STATES.has(r.state as string);
  const list = Array.isArray(jobs) ? jobs : null;
  // 가장 최근 작업(list_jobs ORDER BY created_at DESC) -- 잡 카드 순서와 같다.
  const job0 = list !== null && list.length > 0 ? (obj(list[0]) ?? {}) : null;
  const job0Term = job0 !== null && isTerminal(job0.state as string);
  const wp = obj(job0?.worker_pool);
  const ident = obj(wp?.identity);
  // "true" 문자열·1 은 모름이다(계획 스냅숏은 JSON bool).
  const planned = ident?.privileged === true ? true : ident?.privileged === false ? false : null;
  const askedRoot = p.run_as_root === true;            // 단건의 유일한 root 근거
  const uid = int(ident?.uid);
  const gid = int(ident?.gid);                          // privileged 와 독립으로 찍는다

  const job0Reason = typeof job0?.reason_code === "string" ? job0.reason_code.split(":")[0] : null;
  const rootStopped = planned === true && !askedRoot && !batch && job0Reason === "privilege_not_requested";
  const pv = resolvePrivilege({ op, planned, askedRoot, batch, jobs: list, reqTerminal: reqTerm,
    jobTerminal: job0 === null ? null : job0Term, authMethod: r.auth_method, rootStopped });
  const { kind, basis } = pv;

  // 실행 신원 이름: 계획 스냅숏 → 계획 전제 → 요청 지정 → 요청자.
  const runAsName = name(ident?.username) ?? name(obj(job0?.precondition)?.owner) ?? name(p.owner_username) ?? requester;
  const self = requester !== null && runAsName === requester;
  const relation = runAsName === null ? "" : self ? " (요청자 본인)" : " (관리자가 지정한 다른 사용자)";
  const uidText = `uid${NB}${uid ?? "모름"}`;
  const gidText = `gid${NB}${gid ?? "모름"}`;
  let tail: string;
  if (kind === "root" && basis === "plan") {
    // 비 root 꼬리와 같은 모양(이름 · uid · gid) + 이름이 권한을 뜻하지 않는다는 한 마디. 「{이름} 권한」이라고 쓰지 않는다.
    // root 라는 사실은 「실행 권한」이 말한다 -- 남의 신원을 지정했을 때만 그 이름이 권한이 아니라는 한 마디(2026-09-30 사고 모양).
    tail = `${NB}· ${uidText}${NB}· ${gidText}${self ? "" : " — 이름만 기록되고 권한은 root"}`;
  } else if (kind === "root") {
    tail = ` — 이름만 기록되며 root(uid${NB}0)로 요청됨`;
  } else if (uid !== null || gid !== null) {
    tail = `${NB}· ${uidText}${NB}· ${gidText}(주${NB}그룹)`;
  } else if (list === null) {
    tail = "";
  } else if (list.length === 0) {
    tail = reqTerm ? " · 계획되지 않아 uid/gid 가 정해지지 않았습니다" : " · uid/gid 는 계획 단계에서 정해집니다";
  } else if (!job0Term && wp === null) {
    tail = " · uid/gid 는 계획 단계에서 정해집니다";
  } else {
    tail = " · uid/gid 기록 없음";
  }

  // 옵션(+ 목적지 소유가 읽는 원본 객체)
  const options = normalizeOptions(op, { payload: p.options, job: job0?.options }, { kind, reqState: r.state });
  const rawOpts = obj(p.options) ?? obj(job0?.options);

  // 노드 지정(배치 자식 payload -- planner 가 정책 상한까지만 min-캡한다). sync 가 nsync(공존 노드 없음 폴백)로 돌면
  // resolve_fanout 이 출발·목적지 **각각**에 캡한다 -- 총 노드는 요청의 최대 2배라 그 사실을 붙인다(BatchCreate 캡션과
  // 같은 사실). 도구를 아직 모르면(계획 전) 조건문으로.
  const nc = posInt(p.node_count);
  const ppn = posInt(p.procs_per_node);
  const toolName = name(wp?.tool) ?? name(job0?.tool);
  const perSide = op === "sync"
    && (toolName === "nsync" || int(wp?.source_count) !== null || int(wp?.destination_count) !== null);
  const ncNote = perSide ? "(출발·목적지 각각)" : op === "sync" && toolName === null ? "(nsync 로 돌면 출발·목적지 각각)" : "";
  const nodeRequest = nc === null && ppn === null ? null
    : `${[nc !== null ? `노드 ${nc}${ncNote}` : null, ppn !== null ? `노드당 프로세스 ${ppn}` : null]
      .filter((x) => x !== null).join(" · ")} — 정책 상한까지만 적용`;

  // 보조 그룹 -- root 는 그룹 개념이 없다(행을 그리지 않는다). 상태 키가 없으면 모름(행 없음).
  let groups: RequestSpec["groups"] = null;
  if (kind !== "root" && ident !== null && ident.supplementary_gids_status !== "privileged") {
    const text = supplementaryGroupsText(ident as WorkerPoolIdentity);
    if (text !== null) {
      const caveats = ident.supplementary_gids_status === "applied" && job0 !== null
        ? groupCaveatsFor([job0.storage_name, job0.source_storage, job0.destination_storage] as (string | null | undefined)[],
                          backends)
        : [];
      groups = { text, caveats };
    }
  }

  // 목적지 소유(sync) -- execution_manifests._auto_chown 미러(lib/syncOwnership). 실행 규칙을 단정하는 줄이라 그 규칙이
  // 정해지지 않았거나 실행이 없었던 요청엔 그리지 않는다:
  //   - 배치 계획 전(root 여부 미정), root 요청됨(계획 전·거부·권한 정보 없음 -- 「실행 권한」이 「요청됨」 시제다)
  //   - 작업 없이 끝난 요청, 작업 조회 실패 + 종단(돌았는지 모른다)
  const nothingRan = reqTerm && (list === null || list.length === 0);
  let ownership: string | null = null;
  if (op === "sync" && kind !== "batch" && !(kind === "root" && basis === "request") && !nothingRan && !rootStopped
      && options.state === "ok" && rawOpts !== null) {
    const chownOk = !("chown" in rawOpts) || typeof rawOpts.chown === "string";
    const chmodOk = !("chmod" in rawOpts) || typeof rawOpts.chmod === "string";
    if (chownOk && chmodOk) {
      const chown = typeof rawOpts.chown === "string" ? rawOpts.chown : "";
      const chmod = typeof rawOpts.chmod === "string" ? rawOpts.chmod : "";
      const s = syncOwnership({ chown, chmod, root: kind === "root", runAs: runAsName, self }).short;
      // 자동 chown(uid:주 gid)은 「실행 신원」 행의 이름을 되풀이하지 않고 결과 숫자만 -- 같은 사실을 두 번 말하지 않는다.
      ownership = kind === "user" && chown.trim() === "" && uid !== null && gid !== null
        ? `실행 신원 소유 — ${uid}:${gid}(주${NB}그룹)` : s;
    }
  }

  // 실행 도구(잡이 하나일 때만 -- 여럿이면 각 잡 카드가 말한다)
  let tool: RequestSpec["tool"] = null;
  const multiJob = list !== null && list.length > 1;
  if (list !== null && list.length === 1 && job0 !== null) {
    const summary = toolSummary({ tool: name(job0.tool), worker_pool: wp as WorkerPool | null });
    let toolTail: string | null = null;
    let nodes: NodeGroup[] | null = null;
    if (summary !== null) {
      const pc = int(wp?.process_count);
      const n = posInt(wp?.node_count);
      if (pc !== null) toolTail = `프로세스 ${countText(pc)}${n !== null && pc % n === 0 ? `(노드당 ${pc / n})` : ""}`;
      const cand = obj(wp?.candidates);
      const primary = strs(cand?.primary);
      const source = strs(cand?.source);
      const dest = strs(cand?.destination);
      if (primary.length > 0) nodes = [nodeGroup(null, primary)];
      else if (source.length > 0 || dest.length > 0) {
        nodes = [source.length > 0 ? nodeGroup("소스", source) : null, dest.length > 0 ? nodeGroup("목적지", dest) : null]
          .filter((g): g is NodeGroup => g !== null);
      }
    }
    tool = {
      summary, tail: toolTail, nodes,
      // 도구 이름으로 시작하지 않는다(계획 전 도구 표시 금지 계약).
      fallback: summary !== null ? null : job0Term ? "기록 없음" : "계획 중 — 실행 노드가 정해지면 표시됩니다",
    };
  }

  return {
    target: pathParts(op, p),
    abs: absParts(op, p, roots),
    options,
    priority: priorityText(r.priority, wp?.priority_class),
    nodeRequest,
    privilege: pv.privilege,
    runAs: { name: runAsName ?? "—", relation, tail },
    groups,
    ownership,
    tool,
    multiJob,
    badge: pv.badge,
  };
}
