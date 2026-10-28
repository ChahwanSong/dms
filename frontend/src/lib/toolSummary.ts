import type { ToolActionCounts, ToolFailureSample, ToolSummary } from "./types";

/** summary.json(러너 _build_summary) 판독기 — 2026-10-27 d192 summary v2.
 *
 *  왜 한 모듈인가: 같은 summary 를 요청 상세(결과·미리보기·배너 KPI)·컨펌 창·배치 확인·배치 항목이 읽는다. 화면마다
 *  키를 따로 읽으면 nsync 의 「변경 예정 합」을 한 곳은 「복사 대상」, 다른 곳은 「파일 수」라 부르던 괴리가 다시 생긴다
 *  (포탈 괴리 [1]·[2]·[4]·[5]). 그래서 뜻(도구·단계별)과 라벨을 여기서 한 번 정한다.
 *
 *  규약: 서버 값은 신뢰 입력이 아니다(result_summary 는 unknown) — 숫자는 0 이상 정수만, 그 밖의 모양은 「모름」(null)으로
 *  접고 지어내지 않는다. 0 은 정상값이다(null≠0). 옛 잡(3키만)은 상세가 전부 null 이라 화면은 핵심 수만 보인다.
 */

const KINDS = ["copy", "mkdir", "symlink_update", "meta_update", "remove"] as const;
export type ActionKind = (typeof KINDS)[number];

/** 종류별 한국어 라벨(nsync 의 액션 종류 — 계획·반영 모두). */
export const ACTION_LABELS: Record<ActionKind, string> = {
  copy: "복사",
  mkdir: "디렉토리 생성",
  symlink_update: "링크",
  meta_update: "속성 갱신",
  remove: "삭제",
};

function count(v: unknown): number | null {
  return typeof v === "number" && Number.isInteger(v) && v >= 0 ? v : null;
}

function counts(v: unknown): Partial<Record<ActionKind, number>> | null {
  if (v === null || typeof v !== "object" || Array.isArray(v)) return null;
  const src = v as ToolActionCounts;
  const out: Partial<Record<ActionKind, number>> = {};
  let any = false;
  for (const k of KINDS) {
    const n = count(src[k]);
    if (n !== null) { out[k] = n; any = true; }
  }
  return any ? out : null;
}

export interface ReadSummary {
  /** 원본이 객체였는가(아니면 나머지는 전부 null). */
  present: boolean;
  tool: string | null;
  dryrun: boolean | null;
  quiet: boolean;
  returncode: number | null;
  files: number | null;
  bytes: number | null;
  planned: Partial<Record<ActionKind, number>> | null;
  done: Partial<Record<ActionKind, number>> | null;
  copiedBytes: number | null;
  plannedBytes: number | null;
  sourceItems: number | null;
  copiedItems: number | null;
  copyFailed: number | null;
  /** dsync 가 만들지 못한 항목(mkdir()·symlink()·mknod() failed 줄 수). mknod 실패한 파일은 「Failed to copy」로도 한 번 더
   *  찍힌다 -- copyFailed 와 겹칠 수 있어 둘을 더한 수를 「실패 항목 N개」로 단정하지 않는다(failureCounts). */
  createFailed: number | null;
  removed: number | null;
  removeFailed: number | null;
  targetItems: number | null;
  typeToDir: number | null;
  typeFromDir: number | null;
  typeChangeNeedsDelete: boolean;
  typeChangeRefused: number | null;
  skippedSpecial: number | null;
  failuresTotal: number | null;
  failuresByOp: Record<string, number> | null;
  failureSamples: ToolFailureSample[];
  notAttempted: number | null;
  stoppedEarly: boolean;
  stopCause: string | null;
  /** nsync 의 스캔 오류 수(소스·목적지 합 -- 「Encountered N scan error(s)」). dsync 는 수를 찍지 않는다(scanIncomplete). */
  scanErrors: number | null;
  /** nsync 의 소스 쪽 스캔 오류 수(「Source scan had N error(s): --delete is off」 -- 삭제를 켰을 때만 찍힌다). */
  sourceScanErrors: number | null;
  /** dsync 가 소스 walk 오류를 알렸다(「Errors detected walking source path」 -- 수 없음, 삭제를 켰을 때만 찍힌다). */
  scanIncomplete: boolean;
  deleteDisabled: boolean;
  metadataFailed: number | null;
  metadataIgnored: number | null;
  toolErrors: number | null;
  aborted: boolean;
  reason: string | null;
  summaryUnavailable: boolean;
  /** v2 상세가 계산된 summary 인가(detail_version >= 2). 옛 잡·조기 종료 경로는 false. */
  detailed: boolean;
}

const EMPTY: ReadSummary = {
  present: false, tool: null, dryrun: null, quiet: false, returncode: null, files: null, bytes: null,
  planned: null, done: null, copiedBytes: null, plannedBytes: null, sourceItems: null, copiedItems: null,
  copyFailed: null, createFailed: null, removed: null, removeFailed: null, targetItems: null,
  typeToDir: null, typeFromDir: null,
  typeChangeNeedsDelete: false, typeChangeRefused: null, skippedSpecial: null, failuresTotal: null,
  failuresByOp: null, failureSamples: [], notAttempted: null, stoppedEarly: false, stopCause: null,
  scanErrors: null, sourceScanErrors: null, scanIncomplete: false, deleteDisabled: false,
  metadataFailed: null, metadataIgnored: null, toolErrors: null,
  aborted: false, reason: null, summaryUnavailable: false, detailed: false,
};

/** result_summary·preview_summary(unknown) → 좁힌 읽기. 객체가 아니면 EMPTY(전부 모름). */
export function readSummary(raw: unknown): ReadSummary {
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) return EMPTY;
  const s = raw as ToolSummary & Record<string, unknown>;
  const rc = typeof s.returncode === "number" && Number.isInteger(s.returncode) ? s.returncode : null;
  const tc = s.type_changes && typeof s.type_changes === "object" ? s.type_changes : null;
  const failures = s.failures && typeof s.failures === "object" ? s.failures : null;
  let byOp: Record<string, number> | null = null;
  if (failures && failures.by_op && typeof failures.by_op === "object" && !Array.isArray(failures.by_op)) {
    byOp = {};
    for (const [k, v] of Object.entries(failures.by_op)) {
      const n = count(v);
      if (n !== null) byOp[k] = n;
    }
    if (Object.keys(byOp).length === 0) byOp = null;
  }
  const samples: ToolFailureSample[] = Array.isArray(s.failure_samples)
    ? s.failure_samples.flatMap((x): ToolFailureSample[] => {
        if (x === null || typeof x !== "object") return [];
        const op = typeof x.op === "string" ? x.op : null;
        const path = typeof x.path === "string" ? x.path : null;
        if (op === null || path === null) return [];
        const errno = typeof x.errno === "number" && Number.isInteger(x.errno) ? x.errno : null;
        return [{ op, path, errno }];
      }).slice(0, 10)
    : [];
  return {
    present: true,
    tool: typeof s.tool === "string" && s.tool !== "" ? s.tool : null,
    dryrun: typeof s.dryrun === "boolean" ? s.dryrun : null,
    quiet: s.quiet === true,
    returncode: rc,
    files: count(s.files),
    bytes: count(s.bytes),
    planned: counts(s.planned),
    done: counts(s.done),
    copiedBytes: count(s.copied_bytes),
    plannedBytes: count(s.planned_bytes),
    sourceItems: count(s.source_items),
    copiedItems: count(s.copied_items),
    copyFailed: count(s.copy_failed),
    createFailed: count(s.create_failed),
    removed: count(s.removed),
    removeFailed: count(s.remove_failed),
    targetItems: count(s.target_items),
    typeToDir: tc ? count(tc.to_dir) : null,
    typeFromDir: tc ? count(tc.from_dir) : null,
    typeChangeNeedsDelete: s.type_change_needs_delete === true,
    typeChangeRefused: count(s.type_change_refused),
    skippedSpecial: count(s.skipped_special),
    failuresTotal: failures ? count(failures.total) : null,
    failuresByOp: byOp,
    failureSamples: samples,
    notAttempted: count(s.not_attempted),
    stoppedEarly: s.stopped_early === true,
    stopCause: typeof s.stop_cause === "string" ? s.stop_cause : null,
    scanErrors: count(s.scan_errors),
    sourceScanErrors: count(s.source_scan_errors),
    scanIncomplete: s.scan_incomplete === true,
    deleteDisabled: s.delete_disabled === true,
    metadataFailed: count(s.metadata_failed),
    metadataIgnored: count(s.metadata_ignored),
    toolErrors: count(s.tool_errors),
    aborted: s.aborted === true,
    reason: typeof s.reason === "string" && s.reason !== "" ? s.reason : null,
    summaryUnavailable: s.summary_unavailable === true,
    detailed: typeof s.detail_version === "number" && s.detail_version >= 2,
  };
}

/** 종류별 수의 합(null 은 빼고 더한다 — 하나도 모르면 null). */
export function sumKinds(c: Partial<Record<ActionKind, number>> | null): number | null {
  if (c === null) return null;
  let total = 0;
  let any = false;
  for (const k of KINDS) {
    const n = c[k];
    if (typeof n === "number") { total += n; any = true; }
  }
  return any ? total : null;
}

/** 0 이 아닌 종류만 「복사 3 · 삭제 2」 한 줄로(순서 고정). 전부 0 이면 "변경 없음", 모르면 null. */
export function kindsText(c: Partial<Record<ActionKind, number>> | null): string | null {
  if (c === null) return null;
  const parts = KINDS.filter((k) => typeof c[k] === "number" && (c[k] as number) > 0)
    .map((k) => `${ACTION_LABELS[k]} ${(c[k] as number).toLocaleString("ko-KR")}`);
  return parts.length > 0 ? parts.join(" · ") : "변경 없음";
}

/** 실패 연산 이름(nsync 실패 목록의 op, dsync·drm 오류 줄의 동사) → 한국어. 모르는 이름은 원문 그대로. */
const OP_LABELS: Record<string, string> = {
  copy: "복사",
  mkdir: "디렉토리 생성",
  symlink: "링크",
  // nsync 실패 목록의 op(nsync.c nsync_fail_op_names): scan·type-change·replace-dir·remove·mkdir·symlink·copy·metadata
  metadata: "속성",
  remove: "삭제",
  "replace-dir": "디렉토리 교체",
  "type-change": "타입 교체",
  scan: "스캔(읽기)",
  // dsync·drm 오류 줄의 동사(러너 parsers 가 짧게 정리한 이름)
  unlink: "삭제",
  rmdir: "디렉토리 삭제",
  open: "열기",
  opendir: "디렉토리 열기",
  read: "읽기",
  write: "쓰기",
  stat: "상태 조회",
  chown: "소유자 변경",
  chmod: "권한 변경",
  utime: "시각 변경",
  readlink: "링크 읽기",
  xattr: "확장 속성",
  truncate: "크기 맞춤",
  mknod: "파일 생성",
  seek: "위치 이동",
  other: "기타",
};
export function opLabel(op: string): string {
  // op 는 도구 출력에서 온 문자열(신뢰 경계 밖) -- 「toString」·「constructor」 같은 이름이 프로토타입 함수를 읽지 않게 자기 키만.
  return Object.prototype.hasOwnProperty.call(OP_LABELS, op) ? OP_LABELS[op] : op;
}

/** errno → 사용자가 읽을 이유. 모르는 번호는 「오류 N」. null 은 「이유 모름」. */
const ERRNO_TEXT: Record<number, string> = {
  1: "허용되지 않음(소유자만 가능한 변경)",
  2: "경로 없음",
  5: "입출력 오류",
  13: "권한 없음",
  17: "이미 있음",
  20: "상위가 디렉토리가 아님",
  21: "디렉토리임",
  28: "공간 부족",
  30: "읽기 전용 파일시스템",
  36: "이름이 너무 김",
  39: "디렉토리가 비어 있지 않음",
  40: "심볼릭 링크를 거침",
  95: "지원하지 않는 종류",
  122: "쿼터 초과",
};
export function errnoText(errno: number | null): string {
  if (errno === null) return "이유 모름";
  return ERRNO_TEXT[errno] ?? `오류 ${errno}`;
}

/** 도구가 시작되기 전에 멈춘 사유 — 데이터는 바뀌지 않았다(포탈 괴리 [10]·[12]·[16]). 실행 단계 실패라도 이 코드면
 *  「일부 파일이 이미 복사·삭제됐을 수 있다」 고 말하지 않는다. 제출 실패 접두(…_submit_failed:…)는 접두로 따로 본다
 *  (isPreToolReason). 큐 대기 관문(…_at_step 중 실행 vcjob PENDING 재검사)은 호출측(requestOutcome.toolNeverStarted)이 가른다.
 *
 *  dms-k8s 집합(2026-10-28, dms-ssc d192 에서 이식): 대조한 출처는 셋이다 --
 *  - 러너·preflight 의 경로 봉쇄(d191 통일 규칙): source_path_redirected·destination_path_redirected(sync), target_path_redirected
 *    (rm·PV scan 공용 -- 넓은 뜻 그대로), 워커 준비 실패 workers_unreachable. 셋 다 mpirun·도구 exec 전에 끝난다.
 *  - stepper._step_one 의 제출 전 fail-closed(unknown_tool·storage_missing_at_step·identity_missing_at_step·chown_not_allowed
 *    (잡 행 chown·chmod 변조)·chown_name_not_supported·node_excluded_at_step·node_cpu_insufficient_at_step·privilege_not_requested)
 *    와 PENDING 재검사(_raise_if_blocked·_raise_if_cpu_short -- 같은 두 *_at_step).
 *  - PV·결과 소유 관문: pv_owner_unknown·pv_owner_changed(stepper._record_pv_owner -- preflight·exec_preflight 로그), 컨펌·계획의
 *    k8s_access.recheck_request 거부(pv_moved·pv_not_found·pv_target_outside·k8s_access_denied·storage_owner_changed·
 *    invalid_storage_owner·pv_owner_not_applicable)와 root 자격 회수(privileged_not_authorized). 대개 컨펌·사전 점검 단계라 배너가
 *    이미 「데이터는 변경되지 않았습니다」로 말하지만, 실행 단계로 보이는 경우(전이 기록 추정)에도 같은 말을 하도록 둔다.
 *  LDAP 전용 사유(identity_groups_not_applied·identity_changed_at_step·artifact_base_group_writable·ldap_*)는 dms-k8s 에서 나지
 *  않아 뺐다. artifact_base_not_traversable 은 preflight 마커라 남긴다. */
export const PRE_TOOL_REASONS: ReadonlySet<string> = new Set([
  "workers_unreachable",
  "target_path_redirected",
  "source_path_redirected",
  "destination_path_redirected",
  "unknown_tool",
  "storage_missing_at_step",
  "identity_missing_at_step",
  "chown_not_allowed",
  "chmod_not_allowed",
  "chown_name_not_supported",
  "node_excluded_at_step",
  "node_cpu_insufficient_at_step",
  "privilege_not_requested",
  "privileged_not_authorized",
  "artifact_base_not_traversable",
  "pv_owner_unknown",
  "pv_owner_changed",
  "pv_owner_not_applicable",
  "storage_owner_changed",
  "invalid_storage_owner",
  "pv_moved",
  "pv_not_found",
  "pv_target_outside",
  "k8s_access_denied",
]);

export function isPreToolReason(code: string | null | undefined): boolean {
  if (!code) return false;
  if (PRE_TOOL_REASONS.has(code)) return true;
  const prefix = code.split(":")[0];
  return prefix.endsWith("_submit_failed");
}

const KNOWN_TOOLS: ReadonlySet<string> = new Set(["dscan", "dsync", "nsync", "drm"]);

/** 이 summary 를 낸 도구 -- **잡 행의 tool 이 먼저**(data_jobs.tool / 배치 조인 job_tool), 그게 네 도구가 아니면 v2 의
 *  tool 키, 둘 다 아니면 null. 화면이 도구별 뜻(nsync = 변경 예정·반영, dsync = 소스 전체·복사, drm = 지울 대상·지운 수)을
 *  가르는 유일한 근거다.
 *  왜 행이 먼저인가(d192 리뷰 control-10): 행은 플래너의 결정이고 러너는 그 행으로 만든 매니페스트대로 돈다 -- summary.json
 *  은 도구 쪽 데이터라 둘이 어긋나면(변조) 행이 권위다. 서버(batch_orchestrator._preview_tool, 배치 확인 감사 행의
 *  files_by_tool)도 같은 순서라, 이 순서가 다르면 같은 컨펌의 창 합계와 감사 행이 서로 다른 도구에 항목을 붙인다.
 *  허용 목록 밖의 문자열(변조 행·빈 문자열)은 어느 쪽이든 「모름」 -- 그대로 넘기지 않는다. */
export function summaryTool(s: ReadSummary, jobTool: unknown): string | null {
  if (typeof jobTool === "string" && KNOWN_TOOLS.has(jobTool)) return jobTool;
  return s.tool !== null && KNOWN_TOOLS.has(s.tool) ? s.tool : null;
}

// dsync·drm 실패 예시(오류 줄의 동사 -- 러너 parsers._mfu_op)를 덮는 수. 복사 실패 한 건은 원인 줄(열기·읽기·쓰기 …)을
// 예시로 남기고 「Failed to copy」(errno 없음)로 셈에 든다. 여기 없는 연산(속성 chmod·chown·utime·xattr, walk 의 opendir,
// 모르는 줄 other 등)은 어느 수에도 들지 않는다.
const COPY_SAMPLE_OPS: ReadonlySet<string> = new Set(["copy", "open", "read", "write", "seek", "truncate", "stat"]);
const CREATE_SAMPLE_OPS: ReadonlySet<string> = new Set(["mkdir", "symlink", "mknod"]);
const REMOVE_SAMPLE_OPS: ReadonlySet<string> = new Set(["unlink", "rmdir", "remove"]);

/** 연산별 실패 수(결과의 「실패 항목」 칩과 총계). nsync 는 도구가 센 by_op 그대로(한국어 라벨), dsync·drm 은 summary 의 실패
 *  키로(복사·생성·삭제) -- 같은 사실을 두 번 세지 않는다(dsync 의 타입 교체 거부는 「삭제 실패」의 일부라 칩이 아니라 주의
 *  줄로 말한다). 결과 화면과 배너가 이 한 판정으로 「실패 항목」 유무를 가른다.
 *  총계는 아는 수가 실패를 **다 덮을 때만** 더한다(d192 리뷰 portal-jobs-13): dsync 는 속성 적용(chmod·utime …)·walk 오류를
 *  세는 키가 없어, 그런 예시만 있는 결과에 「실패 항목 0개」가 붙었다. 그래서 (1) 어느 수에도 들지 않는 연산의 예시가 있거나,
 *  (2) 예시가 아는 수보다 많거나, (3) 복사 실패와 생성 실패가 함께 있으면(mknod 를 못 한 파일은 「Failed to copy」로도 찍혀
 *  두 수에 다 든다 -- 겹친 수를 모른다) 총계는 null(제목은 수 없이 「실패 항목」). 하나도 모르면 null -- 0 을 지어내지 않는다. */
export function failureCounts(s: ReadSummary, tool: string | null): { total: number | null; byOp: [string, number][] } {
  if (s.failuresByOp !== null) {
    const byOp = Object.entries(s.failuresByOp).filter(([, n]) => n > 0).map(([op, n]) => [opLabel(op), n] as [string, number]);
    return { total: s.failuresTotal ?? byOp.reduce((a, [, n]) => a + n, 0), byOp };
  }
  const byOp: [string, number][] = [];
  const copyFailed = tool !== "drm" ? s.copyFailed : null;
  const createFailed = tool !== "drm" ? s.createFailed : null;
  const covered = new Set<string>();
  if (copyFailed !== null && copyFailed > 0) {
    byOp.push(["복사", copyFailed]);
    COPY_SAMPLE_OPS.forEach((op) => covered.add(op));
  }
  if (createFailed !== null && createFailed > 0) {
    byOp.push(["생성", createFailed]);
    CREATE_SAMPLE_OPS.forEach((op) => covered.add(op));
  }
  if (s.removeFailed !== null && s.removeFailed > 0) {
    byOp.push(["삭제", s.removeFailed]);
    REMOVE_SAMPLE_OPS.forEach((op) => covered.add(op));
  }
  if (s.failuresTotal !== null) return { total: s.failuresTotal, byOp };
  const known = [copyFailed, createFailed, s.removeFailed].filter((n): n is number => n !== null);
  const sum = known.length ? known.reduce((a, b) => a + b, 0) : null;
  const overlap = copyFailed !== null && copyFailed > 0 && createFailed !== null && createFailed > 0;
  const uncovered = s.failureSamples.some((x) => !covered.has(x.op));
  const total = sum === null || overlap || uncovered || s.failureSamples.length > sum ? null : sum;
  return { total, byOp };
}

/** 결과에 「실패 항목」이 있나(예시·총계·연산별 수 중 하나라도) -- 상세 없는 옛 요약은 늘 false. */
export function hasFailureItems(s: ReadSummary, tool: string | null): boolean {
  if (!s.detailed) return false;
  const { total, byOp } = failureCounts(s, tool);
  return s.failureSamples.length > 0 || (total !== null && total > 0) || byOp.length > 0;
}

/** 0 이 아닌 종류만 ["복사 3개", "삭제 2개"](순서 고정) -- 배너 문장(「실패 전까지 복사 3개·삭제 2개 반영」)용.
 *  모르면(null) 빈 배열이다(「반영 없음」과 다르다 -- 호출측이 c === null 을 먼저 가른다). */
export function kindsCountParts(c: Partial<Record<ActionKind, number>> | null): string[] {
  if (c === null) return [];
  return KINDS.filter((k) => typeof c[k] === "number" && (c[k] as number) > 0)
    .map((k) => `${ACTION_LABELS[k]} ${(c[k] as number).toLocaleString("ko-KR")}개`);
}

/** 읽지 못한 곳의 이름(스캔 오류 문장의 주어) -- 수를 지어내지 않는다(d192 리뷰 portal-jobs-18·runner-2).
 *  - nsync: 「Encountered N scan error(s)」는 소스·목적지 합이다 -- 소스 쪽 수(Source scan had N …, 삭제를 켰을 때만 찍힌다)가
 *    합과 같을 때만 「소스 N곳」, 아니면 「소스 또는 목적지 N곳」.
 *  - dsync: 수를 찍지 않는다(「Errors detected walking source path」 -- scan_incomplete) -- 「소스 일부」. 옛 v2 초안의 자리표시
 *    scan_errors=1 도 수로 읽지 않는다.
 *  아무 오류도 없으면 null. */
export function scanSubject(s: ReadSummary, tool: string | null): string | null {
  const n = s.scanErrors;
  if (tool === "nsync" && n !== null && n > 0) {
    const src = s.sourceScanErrors;
    return src !== null && src === n ? `소스 ${n.toLocaleString("ko-KR")}곳` : `소스 또는 목적지 ${n.toLocaleString("ko-KR")}곳`;
  }
  if (s.scanIncomplete || (n !== null && n > 0)) return "소스 일부";
  return null;
}

/** 목적격 조사(「소스 2곳을」·「소스 일부를」) -- scanSubject 의 두 꼴(…곳 / …일부)만 온다. */
export function withObj(subject: string): string {
  return `${subject}${subject.endsWith("곳") ? "을" : "를"}`;
}

/** 미리보기(dry-run)가 이미 아는, 컨펌 전에 사람이 알아야 할 것(포탈 괴리 [1]·[5]·[6]·[17]). 컨펌 창·미리보기 결과·배너가
 *  같은 문장을 쓴다(화면마다 따로 쓰면 한 곳만 고쳐진다). sync 만 -- rm 미리보기(drm walk)는 경고할 상세가 없다.
 *  **성공한 미리보기 전용**이다: 문장이 「…합니다」(앞으로 일어날 일)라, 실패해 실행되지 않을 미리보기에는 붙이지 않는다
 *  (호출측이 가른다 -- StageResults.PreviewResult 의 failedAt, d192 리뷰 portal-jobs-21).
 *  - 삭제 예정(nsync remove): 삭제 옵션이 꺼져 있으면 그 수는 전부 형식이 바뀐 항목의 교체다(nsync 는 타입이 다르면 늘
 *    REMOVE 를 낸다 -- 파일↔링크처럼 「Type changes」 줄에 안 잡히는 것도). 꺼진 채 「목적지에서 N개를 삭제합니다」만 말하면
 *    왜 지우는지 모른다(리뷰 portal-jobs-23) -- 그 이유를 문장에 넣는다. 켜져 있으면 목적지 전용 항목 + 교체.
 *  - 비어 있지 않은 디렉토리 → 파일·링크(삭제 꺼짐): 실행이 그 항목에서 실패한다(d189-C8). 미리보기 WARN 은 「비어 있지 않은
 *    것은」 이라는 조건문이라 확정이 아니다 -- 문장도 조건으로 말한다.
 *  - FIFO·소켓·장치는 복사하지 않는다(rc 0, 조용히 빠졌다). nsync 는 목적지의 같은 이름 항목을 삭제 옵션과 무관하게 남긴다.
 *  - 읽기 오류: 읽지 못한 부분은 반영되지 않고, 소스 쪽이면 삭제 옵션은 그 실행 동안 꺼진다(delete_disabled). 주어는
 *    scanSubject(수를 지어내지 않는다). dsync 는 읽지 못한 경로를 「실패 항목」 예시로 남기지 않는다(walk 오류 줄은
 *    작은따옴표 형식) -- 출력의 그 줄을 가리킨다.
 *  - dsync 미리보기는 지울 수를 세지 않는다 -- 삭제를 켰다면 그 사실만 말한다(수를 지어내지 않는다). */
export function previewWarnings(s: ReadSummary, opts: { op: string; tool: string | null; deleteOn: boolean }): string[] {
  if (opts.op !== "sync") return [];
  const out: string[] = [];
  const nsync = opts.tool === "nsync";
  const remove = s.planned?.remove;
  const fromDir = s.typeFromDir;
  const typeChanges = (fromDir !== null && fromDir > 0) || (s.typeToDir !== null && s.typeToDir > 0);
  if (typeof remove === "number" && remove > 0) {
    const n = remove.toLocaleString("ko-KR");
    // 삭제 꺼짐 + 비어 있지 않은 디렉토리 경고(아래)가 함께 뜨면 「교체합니다」 단정이 그 경고와 부딪힌다(d192 실화면 N2) --
    // 그때는 「교체를 시도합니다」로 말하고 예외를 다음 줄에 맡긴다.
    out.push(opts.deleteOn
      ? `목적지에서 ${n}개를 삭제합니다${typeChanges ? "(형식이 바뀐 항목의 교체 포함)" : ""} — 되돌릴 수 없습니다`
      : s.typeChangeNeedsDelete
        ? `형식이 바뀐 목적지 항목 ${n}개를 지우고 소스 형식으로 교체를 시도합니다(삭제 옵션과 무관) — 지워진 항목은 되돌릴 수 없습니다`
        : `형식이 바뀐 목적지 항목 ${n}개를 지우고 소스 형식으로 교체합니다(삭제 옵션과 무관) — 되돌릴 수 없습니다`);
  }
  if (s.typeChangeNeedsDelete) {
    const n = fromDir !== null && fromDir > 0 ? `디렉토리 ${fromDir.toLocaleString("ko-KR")}개가` : "일부 디렉토리가";
    out.push(`${n} 파일·링크로 바뀝니다 — 삭제를 켜지 않으면 비어 있지 않은 디렉토리는 바뀌지 않고 실패로 남습니다`);
  } else if (fromDir !== null && fromDir > 0) {
    out.push(`디렉토리 ${fromDir.toLocaleString("ko-KR")}개가 파일·링크로 바뀌어 교체됩니다 — 그 안의 목적지 항목도 함께 지워집니다`);
  }
  if (s.skippedSpecial !== null && s.skippedSpecial > 0) {
    out.push(`FIFO·소켓·장치 파일 ${s.skippedSpecial.toLocaleString("ko-KR")}개는 복사하지 않습니다`
      + (nsync ? " — 목적지의 같은 이름 항목은 삭제를 켜도 그대로 둡니다" : ""));
  }
  const unread = scanSubject(s, opts.tool);
  if (unread !== null) {
    out.push(`${withObj(unread)} 읽지 못했습니다(권한 등) — 읽지 못한 부분은 반영되지 않습니다`
      + (s.deleteDisabled ? ". 이 때문에 이번 실행에서는 삭제를 하지 않습니다" : "")
      + (opts.tool === "dsync" ? ". 읽지 못한 경로는 미리보기 출력(stdout.log)의 'Failed to open directory'·'Failed to stat' 줄에 있습니다" : ""));
  }
  if (opts.tool === "dsync" && opts.deleteOn) {
    out.push("dsync 미리보기는 지울 항목 수를 세지 않습니다 — 삭제가 켜져 있어 목적지에만 있는 항목은 실행 때 지워집니다");
  }
  return out;
}
