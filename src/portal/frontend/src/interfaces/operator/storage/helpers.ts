import { ApiError, type MutationObserved, type StorageMapping } from "../../../api";

export function backendType(m: StorageMapping): string {
  const bt = m.backend_template?.["backend_type"];
  return typeof bt === "string" && bt ? bt : "—";
}

// The managed root path (fs backends) — surfaced in the list/detail so the
// storage path isn't only visible in the raw backend_template JSON.
export function managedRoot(m: StorageMapping): string | null {
  const v = m.backend_template?.["managed_root"];
  return typeof v === "string" && v.trim() ? v.trim() : null;
}

// DMS storage backend categories:
// - filesystem (fs): host-mounted directory/quota operations (cephfs/gpfs/wekafs).
//   These REQUIRE mount_path + managed_root (managed_root under mount_path).
// - k8s CSI (csi): namespace ResourceQuota on a CSI StorageClass (ceph-csi/gpfs-csi/
//   weka-csi). The backend_template carries backend_type + csi_driver (+ optional
//   mutation_mode/control_host transport); cluster_name and storage_class_name go in the
//   top-level mapping fields.
export const FS_BACKEND_TYPES = ["cephfs", "gpfs", "wekafs"] as const;
export const CSI_BACKEND_TYPES = ["ceph-csi", "gpfs-csi", "weka-csi"] as const;
export const BACKEND_TYPES = [...FS_BACKEND_TYPES, ...CSI_BACKEND_TYPES] as const;

// RM/DM (agent) readiness is only meaningful for the filesystem backends
// (cephfs/gpfs/wekafs): those run an RM/DM agent on the storage node. k8s CSI
// namespace-quota mappings are agentless (quota applied via kubectl against the
// target cluster's API server), so DMS does not gate them on RM/DM readiness at
// all — control or managed cluster alike (see planner _reject_unsafe_storage_mapping).
// Treat anything that is not a known fs backend as CSI/free-form and hide RM/DM.
export function isFsBackend(m: StorageMapping): boolean {
  const bt = m.backend_template?.["backend_type"];
  return typeof bt === "string" && (FS_BACKEND_TYPES as readonly string[]).includes(bt);
}

// Filesystem SUBTYPE — a portal-only discriminator stored inside backend_template
// (DMS ignores unknown keys; it is not in any DMS field set). Only meaningful for fs
// backends (cephfs/gpfs/wekafs):
//   - "fs-native": a plain host filesystem DMS manages directly. DEFAULT — an absent
//     key is treated as fs-native, so all pre-existing mappings stay fs-native.
//   - "pv": a filesystem that BACKS Kubernetes PV/PVC provisioning. CSI subvolumes live
//     under its managed_root — e.g. CephFS root mounted at mount_path, managed_root set
//     to <mount_path>/volumes, subvolumes at <managed_root>/csi/<subvol>/<uuid>/.
// The portal reads this to drive fs-native vs PV workflows (e.g. future data-move input
// differences: a PV target needs subvolume/uuid, an fs-native target does not).
export const FS_SUBTYPE_KEY = "filesystem_subtype";
export type FsSubtype = "fs-native" | "pv";

export function fsSubtype(m: StorageMapping): FsSubtype | null {
  if (!isFsBackend(m)) return null;
  return m.backend_template?.[FS_SUBTYPE_KEY] === "pv" ? "pv" : "fs-native";
}

export function isForPv(m: StorageMapping): boolean {
  return fsSubtype(m) === "pv";
}

// CSI (k8s namespace-quota) readiness axis. Instead of RM/DM agent evidence, CSI
// mappings are validated by the ResourceQuota mutation TRANSPORT probe: can the
// per-mapping kubectl/ssh-kubectl path reach the cluster and apply ResourceQuotas?
// DMS exposes the verdict at readiness.kubernetes_mutation (Ready/Failed/Unknown)
// and the probe detail at sanity_result.mutation_observed.

// readiness.kubernetes_mutation status for the QUOTA axis dot color.
export function quotaStatus(m: StorageMapping): string {
  const r = m.readiness || m.sanity_result?.readiness;
  return r?.kubernetes_mutation || "Unknown";
}

export function mutationObserved(m: StorageMapping): MutationObserved | undefined {
  return m.sanity_result?.mutation_observed;
}

// Tooltip line for the QUOTA dot:
//   "QUOTA: <status> · <mode>[ @ <control_host>] · can-i: <yes/no>"
export function quotaTitle(m: StorageMapping): string {
  const status = quotaStatus(m);
  const mo = mutationObserved(m);
  const mode = mo?.mode || "kubectl";
  const host = mo?.control_host ? ` @ ${mo.control_host}` : "";
  const canI =
    mo?.can_mutate == null ? "unknown" : mo.can_mutate ? "yes" : "no";
  return `QUOTA: ${status} · ${mode}${host} · can-i: ${canI}`;
}

// backend_template per backend_type, with example values. Per-field meaning,
// required/optional, and defaults are in FIELD_DOCS below (shown under the editor).
//
// Two categories:
// - filesystem (fs): cephfs/gpfs/wekafs are filesystem-ONLY — they carry NO CSI info
//   (no csi_driver / storage_class_name); those belong to csi mappings.
// - k8s CSI (csi): ceph-csi/gpfs-csi/weka-csi carry the CSI StorageClass + driver.
//
// cluster_name (fs+csi) and storage_class_name (csi) live in the template for a single
// edit surface, but DMS reads them from the TOP-LEVEL mapping fields; the form derives
// those on submit. The weka_credentials.password secret stays blank; on edit DMS shows
// "***" and merges the stored secret back when left blank/unchanged.
export const BACKEND_SKELETONS: Record<string, Record<string, unknown>> = {
  // ---- filesystem (fs): only cephfs/wekafs/gpfs drive a real DMS fs adapter ----
  // CephFS is the LEAN backend: CephFsBackendTemplate reads ONLY these 6 keys. It
  // ignores csi_driver/storage_class_name/command_runner/command_timeout_seconds/
  // filesystem_name/data_network — CephFS executor mode/timeout come from the global
  // DMS_FILESYSTEM_MUTATION_MODE / DMS_FILESYSTEM_EXEC_* settings, not the template.
  cephfs: {
    backend_type: "cephfs",
    cluster_name: "cluster-a",
    mount_path: "/mnt/cephfs",
    managed_root: "/mnt/cephfs/dms",
    rm_worker_nodes: ["rm-node-1"],
  },
  // GPFS reads cluster_name only from the top-level field, not the template; it is
  // kept here so the form derives the top-level cluster_name. command_runner default
  // is "ssh-host-exec" for both gpfs and wekafs (needs an RM worker node from
  // rm_worker_nodes / agent Ready candidates); set "local" to run on the worker.
  gpfs: {
    backend_type: "gpfs",
    cluster_name: "cluster-a",
    filesystem_name: "gpfs0",
    mount_path: "/gpfs/gpfs0",
    managed_root: "/gpfs/gpfs0/dms",
    fileset_name_template: "dms-{directory_name}",
    rm_worker_nodes: ["gpfs-rm-1"],
    command_runner: "ssh-host-exec",
    command_timeout_seconds: 60,
  },
  // WekaFS REQUIRES weka_credentials{username,password} (SECRET) for every operation
  // — DMS fails closed without them (it does NOT read ambient WEKA_* env). command_runner
  // default is "ssh-host-exec" (needs an RM worker node from rm_worker_nodes / agent Ready
  // candidates); set "local" to run on the worker. organization defaults to "0".
  wekafs: {
    backend_type: "wekafs",
    cluster_name: "cluster-a",
    filesystem_name: "default",
    mount_path: "/mnt/weka",
    managed_root: "/mnt/weka/dms",
    rm_worker_nodes: ["weka-rm-1"],
    command_runner: "ssh-host-exec",
    command_timeout_seconds: 60,
    weka_profile: "default",
    weka_credentials: { organization: "0", username: "dms-svc", password: "" },
  },
  // ---- k8s CSI (csi): namespace ResourceQuota on a CSI StorageClass ----
  // NOTE: 'ceph-csi'/'gpfs-csi'/'weka-csi' are NOT recognized backend_type values in the
  // DMS source — on the k8s namespace-quota path backend_type is a free-form label (only
  // 'cephfs'/'wekafs'/'gpfs' drive a filesystem adapter). DMS reads backend_type + csi_driver
  // + mutation_mode/control_host from this template; cluster_name/storage_class_name are
  // derived to the top-level fields (sanity StorageClass/provisioner check + namespace-quota lookup).
  // csi_driver has no default for these labels, so set it explicitly — if omitted, the
  // provisioner-match sanity check is silently SKIPPED (StorageClass existence still checked).
  // mutation_mode/control_host pin the ResourceQuota mutation transport per-mapping. The
  // example uses 'ssh-kubectl' + a control host (managed cluster reached via SSH→kubectl).
  // For a cluster DMS reaches directly, drop both → defaults to local 'kubectl'.
  "ceph-csi": {
    backend_type: "ceph-csi",
    cluster_name: "cluster-a",
    storage_class_name: "ceph-rbd",
    csi_driver: "rbd.csi.ceph.com",
    mutation_mode: "ssh-kubectl",
    control_host: "control-host-1",
  },
  "gpfs-csi": {
    backend_type: "gpfs-csi",
    cluster_name: "cluster-a",
    storage_class_name: "gpfs-sc",
    csi_driver: "spectrumscale.csi.ibm.com",
    mutation_mode: "ssh-kubectl",
    control_host: "control-host-1",
  },
  "weka-csi": {
    backend_type: "weka-csi",
    cluster_name: "cluster-a",
    storage_class_name: "weka-sc",
    csi_driver: "csi.weka.io",
    mutation_mode: "ssh-kubectl",
    control_host: "control-host-1",
  },
};

// On edit, surface the mapping's top-level cluster_name/storage_class_name inside
// the editable template (if not already present) so they round-trip instead of
// resetting to null.
export function templateForEdit(m: {
  backend_template?: Record<string, unknown>;
  cluster_name?: string | null;
  storage_class_name?: string | null;
}): Record<string, unknown> {
  const t: Record<string, unknown> = { ...(m.backend_template ?? {}) };
  if (t.cluster_name === undefined && m.cluster_name != null)
    t.cluster_name = m.cluster_name;
  if (t.storage_class_name === undefined && m.storage_class_name != null)
    t.storage_class_name = m.storage_class_name;
  return t;
}

// Pull a top-level string field out of the template (DMS reads these top-level).
export function pickStr(template: Record<string, unknown>, key: string): string | null {
  const v = template[key];
  return typeof v === "string" && v.trim() ? v.trim() : null;
}

// Per-field reference shown under the editor: required/optional, meaning, default.
// "required" = required at registration / first side-effect per the DMS source.
export interface FieldDoc {
  name: string;
  required: boolean;
  desc: string;
  default: string;
  secret?: boolean;
}

const RM_NODE_DOCS: FieldDoc[] = [
  {
    name: "rm_worker_nodes",
    required: true,
    desc: "RM 작업 대상 노드 목록. 이 중 agent Ready 노드에서 RM 명령 실행",
    default: "—",
  },
];

const CSI_FIELD_DOCS: FieldDoc[] = [
  {
    name: "backend_type",
    required: true,
    desc: "CSI 라벨 (DMS는 free-form으로 취급; filesystem 어댑터는 구동하지 않음)",
    default: "—",
  },
  { name: "cluster_name", required: true, desc: "대상 StorageClass가 있는 클러스터", default: "—" },
  {
    name: "storage_class_name",
    required: false,
    desc: "이 매핑이 관리하는 CSI StorageClass 이름 (namespace quota 대상)",
    default: "—",
  },
  {
    name: "csi_driver",
    required: false,
    desc: "StorageClass provisioner와 매칭할 driver. 생략하면 provisioner 일치 검사를 건너뜀(권장: 명시)",
    default: "—",
  },
  {
    name: "mutation_mode",
    required: false,
    desc: "이 클러스터의 ResourceQuota 적용 경로(per-mapping). 'kubectl'(로컬 — in-cluster/직접 도달) 또는 'ssh-kubectl'(control_host로 SSH 후 kubectl). 생략 시 전역 DMS_KUBERNETES_MUTATION_MODE(기본 'kubectl') 사용. agent 없는 managed 클러스터는 'ssh-kubectl'로 지정",
    default: "전역값(기본 kubectl)",
  },
  {
    name: "control_host",
    required: false,
    desc: "mutation_mode='ssh-kubectl'일 때 RM worker가 SSH할 대상 클러스터 control 호스트(대상 kubectl admin 보유). bare hostname/IPv4만 허용. ssh-kubectl인데 없으면 422. 반대로 control_host만 있고 mutation_mode가 없어도 422(기본 kubectl에선 무시 → mutation_mode='ssh-kubectl' 명시 필요)",
    default: "—",
  },
];

export const FIELD_DOCS: Record<string, FieldDoc[]> = {
  cephfs: [
    { name: "backend_type", required: true, desc: "백엔드 타입 식별자", default: '"cephfs"' },
    { name: "cluster_name", required: true, desc: "클러스터 이름 (sanity·RM readiness 조회)", default: "—" },
    { name: "mount_path", required: true, desc: "CephFS 마운트 경로", default: "—" },
    {
      name: "managed_root",
      required: true,
      desc: "DMS 관리 루트. mount_path 하위여야 하며 격리 경계가 됨",
      default: "—",
    },
    ...RM_NODE_DOCS,
  ],
  gpfs: [
    { name: "backend_type", required: true, desc: "백엔드 타입 식별자", default: '"gpfs"' },
    { name: "cluster_name", required: true, desc: "클러스터 이름 (sanity·RM readiness 조회)", default: "—" },
    { name: "mount_path", required: true, desc: "GPFS 마운트 경로", default: "—" },
    { name: "managed_root", required: true, desc: "관리 루트 (mount_path 하위)", default: "—" },
    {
      name: "filesystem_name",
      required: true,
      desc: "GPFS 파일시스템(device) 이름. mm*(quota/fileset) 명령 대상. GPFS는 등록 필수",
      default: "—",
    },
    {
      name: "fileset_name_template",
      required: false,
      desc: "fileset 이름 템플릿. {directory_name}·{storage_name} 치환",
      default: '"dms-{directory_name}"',
    },
    ...RM_NODE_DOCS,
    {
      name: "command_runner",
      required: false,
      desc: "명령 실행 방식: 'ssh-host-exec' | 'local'",
      default: '"ssh-host-exec"',
    },
    { name: "command_timeout_seconds", required: false, desc: "명령 타임아웃(초)", default: "60" },
  ],
  wekafs: [
    { name: "backend_type", required: true, desc: "백엔드 타입 식별자", default: '"wekafs"' },
    { name: "cluster_name", required: true, desc: "클러스터 이름 (sanity·RM readiness 조회)", default: "—" },
    { name: "mount_path", required: true, desc: "WEKA 마운트 경로", default: "—" },
    { name: "managed_root", required: true, desc: "관리 루트 (mount_path 하위)", default: "—" },
    {
      name: "weka_credentials",
      required: true,
      secret: true,
      desc: "WEKA 자격증명 {organization, username, password}. 모든 weka 작업에 필수(시크릿)",
      default: "organization=\"0\"",
    },
    {
      name: "filesystem_name",
      required: false,
      desc: "WEKA 파일시스템 이름. quota 명령 대상",
      default: "storage_name",
    },
    ...RM_NODE_DOCS,
    {
      name: "command_runner",
      required: false,
      desc: "명령 실행 방식: 'ssh-host-exec' | 'local'",
      default: '"ssh-host-exec"',
    },
    { name: "command_timeout_seconds", required: false, desc: "명령 타임아웃(초)", default: "60" },
    { name: "weka_profile", required: false, desc: "weka CLI '--profile' 이름", default: "—" },
  ],
  "ceph-csi": CSI_FIELD_DOCS,
  "gpfs-csi": CSI_FIELD_DOCS,
  "weka-csi": CSI_FIELD_DOCS,
};

export function formatApiError(e: unknown): string {
  if (e instanceof ApiError) {
    const base = typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail);
    if (e.status === 409) return `진행 중인 작업이 있어 거부됨 (409): ${base}`;
    if (e.status === 422) return `유효성 오류 (422): ${base}`;
    if (e.status === 503) return `DMS 미연동 (503): ${base}`;
    if (e.status === 404) return `대상을 찾을 수 없음 (404): ${base}`;
    return `오류 ${e.status}: ${base}`;
  }
  return e instanceof Error ? e.message : "알 수 없는 오류";
}
