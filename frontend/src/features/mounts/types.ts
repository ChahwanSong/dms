import type { NodeExclusion } from "../../lib/types";

// 마운트 현황(2026-11-23) 응답 타입 -- 공용 코어(dms-ssc 와 같은 모양)만. 서버 계약은 src/dms/mount_status.py 모듈 docstring 이고,
// dms-k8s 덧붙임 키(스토리지 열의 kind·pv)는 k8sMountParts.tsx 가 따로 선언한다(이 파일은 k8s 를 모른다). DMS 파드 덧붙임
// (control_pods·roles 의 api_pod·controller_pod -- src/dms/mount_status_pods.py)은 두 라인 공용 재료라 여기 둔다(없는 응답이면 undefined).

export type CellState = "ready" | "read_only" | "absent" | "broken" | "pending" | "stale";
export type SharedState =
  | "ok" | "ok_unverified" | "local" | "differs" | "missing" | "read_only" | "pending" | "stale";

export interface MountDisk { total_bytes: number; used_bytes: number }

/** 노드 × 스토리지 한 칸. status·reason 은 에이전트 원문(화면은 mountTerms 로 번역), 점검 값은 null = 모름(옛 에이전트·보고 없음). */
export interface MountCellData {
  state: CellState;
  last: CellState | null;                          // stale 일 때 마지막 보고의 판정
  pending: "not_reported" | "path_changed" | null;
  status: string | null;
  reason: string | null;
  reported_path: string | null;
  exists: boolean | null;
  is_mountpoint: boolean | null;
  readable: boolean | null;
  writable: boolean | null;
  disk: MountDisk | null;                          // 마운트된 칸에서만
}

/** 노드 × DMS 공용 디렉터리(아티팩트 base) 한 칸. mount_point·fstype null = 모름(옛 에이전트·direct 모드). */
export interface SharedCellData {
  state: SharedState;
  last: SharedState | null;
  reported_path: string | null;
  exists: boolean | null;
  writable: boolean | null;
  mount_point: string | null;
  fstype: string | null;
}

/** 열 머리 요약 -- ready/total/status/detail 은 스토리지 목록 상태(reconciler)와 같은 규칙, 뒤 넷은 신선한 칸의 판정 수. */
export interface ColumnSummary {
  ready: number; total: number; status: string; detail: string;
  broken: number; read_only: number; absent: number; pending: number;
}

export interface StorageColumn {
  storage_name: string;
  mount_path: string;
  backend_type: string | null;
  user_enabled: boolean;
  summary: ColumnSummary;
}

export interface MountRow {
  node_name: string;
  reported_at: string;
  fresh: boolean;
  probe_mode: string | null;
  exclusion: NodeExclusion | null;
  k8s_node: Record<string, unknown> | null;
  roles: string[];                                 // 열린 목록: build · api_pod·controller_pod(파드 덧붙임) · (사이트 덧붙임) · 모르는 값은 원문
  control_plane: boolean | null;                   // null = 모름(k8s 노드 목록 없음·클러스터에 없음)
  in_cluster: boolean | null;
  problem: boolean;
  /** 문제 수(마운트 문제 + 다른 노드가 쓰기 가능으로 붙인 스토리지의 읽기 전용 + 공용 디렉터리 문제 -- 이 노드 DMS 파드의 경로 읽기
   *  실패도 공용 디렉터리 한 칸으로, 서버 mount_status_pods) -- null = 리포트 지연(모름). */
  problem_count: number | null;
  cells: Record<string, MountCellData>;
  shared_dir: SharedCellData | null;
}

export interface MountTotals {
  nodes: number; fresh_nodes: number; stale_nodes: number;
  ready: number; read_only: number; absent: number; broken: number; pending: number;
  shared_ok: number; shared_unverified: number; shared_problem: number; shared_pending: number;
  problem_nodes: number;
}

export interface SharedMajority { mount_point: string | null; fstype: string | null; nodes: number }

/** dms-api·컨트롤러 파드 하나가 뜬 곳과 그 파드 자신의 아티팩트 경로 점검(서버가 신선한 기록만 싣는다). ok·reason 은 지금 base 를
 *  본 점검일 때만(아니면 null = 모름), read_failed = 경로 점검 실패(권한 사유 제외 -- 서버 판정; 컨트롤러의 쓰기 왕복 실패
 *  artifact_base_not_writable 도 든다), in_table = 그 노드가 표의 행인가. */
export interface ControlPod {
  role: "api_pod" | "controller_pod" | string;
  instance: string | null;                         // 컨트롤러는 기록되지 않는다(null)
  node_name: string;
  ok: boolean | null;
  reason: string | null;
  read_failed: boolean;
  checked_at: string | null;
  gate_state: string | null;                       // API 파드만(시작 점검 -- waiting·passed·started_degraded·disabled·off)
  in_table: boolean;
}

export interface MountStatusResponse {
  generated_at: string;
  stale_seconds: number;
  report_interval_seconds: number;
  shared_dir: { path: string; state_root: string | null; majority: SharedMajority | null } | null;
  storages: StorageColumn[];
  disabled_storages: string[];
  nodes: MountRow[];
  totals: MountTotals;
  cluster: {
    known: boolean;
    reason: string | null;
    observed_at: string | null;
    agentless_nodes: { node_name: string; control_plane: boolean }[];
  };
  control_pods?: ControlPod[];
}
