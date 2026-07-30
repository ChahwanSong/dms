import { ApiError, type StorageMapping } from "../../../api";

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
// - filesystem (fs): host-mounted directory data operations (cephfs/gpfs/wekafs).
//   These REQUIRE mount_path + managed_root (managed_root under mount_path).
// - k8s CSI (csi): a PVC-backed data-sync target on a CSI StorageClass (ceph-csi/
//   gpfs-csi/weka-csi). The backend_template carries backend_type + csi_driver;
//   cluster_name and storage_class_name go in the top-level mapping fields.
export const FS_BACKEND_TYPES = ["cephfs", "gpfs", "wekafs"] as const;
export const CSI_BACKEND_TYPES = ["ceph-csi", "gpfs-csi", "weka-csi"] as const;
export const BACKEND_TYPES = [...FS_BACKEND_TYPES, ...CSI_BACKEND_TYPES] as const;

// DM (agent) readiness is only meaningful for the filesystem backends
// (cephfs/gpfs/wekafs): those run a DM agent on the storage node. k8s CSI mappings
// are agentless, so DMS does not gate them on DM readiness at all — control or
// managed cluster alike (see planner _reject_unsafe_storage_mapping).
// Treat anything that is not a known fs backend as CSI/free-form and hide DM.
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
// those on submit.
export const BACKEND_SKELETONS: Record<string, Record<string, unknown>> = {
  // ---- filesystem (fs): cephfs/wekafs/gpfs are the host-mounted data-job targets ----
  // Only the keys DMS actually reads are offered. The RM-era execution keys
  // (rm_worker_nodes/ssh_host/command_runner/command_timeout_seconds/quota_scope/
  // fileset_name_template/weka_profile/weka_credentials) are gone with resource
  // management — nothing reads them any more, so the form must not collect them.
  cephfs: {
    backend_type: "cephfs",
    cluster_name: "cluster-a",
    mount_path: "/mnt/cephfs",
    managed_root: "/mnt/cephfs/dms",
  },
  // GPFS reads cluster_name only from the top-level field, not the template; it is
  // kept here so the form derives the top-level cluster_name. filesystem_name is the
  // GPFS device the DM worker addresses.
  gpfs: {
    backend_type: "gpfs",
    cluster_name: "cluster-a",
    filesystem_name: "gpfs0",
    mount_path: "/gpfs/gpfs0",
    managed_root: "/gpfs/gpfs0/dms",
  },
  wekafs: {
    backend_type: "wekafs",
    cluster_name: "cluster-a",
    filesystem_name: "default",
    mount_path: "/mnt/weka",
    managed_root: "/mnt/weka/dms",
  },
  // ---- k8s CSI (csi): a PVC↔PVC data-sync target on a CSI StorageClass ----
  // NOTE: 'ceph-csi'/'gpfs-csi'/'weka-csi' are NOT recognized backend_type values in the
  // DMS source — backend_type is a free-form label here (only 'cephfs'/'wekafs'/'gpfs'
  // drive a filesystem adapter). DMS reads backend_type + csi_driver from this template;
  // cluster_name/storage_class_name are derived to the top-level fields (sanity
  // StorageClass/provisioner check).
  // csi_driver has no default for these labels, so set it explicitly — if omitted, the
  // provisioner-match sanity check is silently SKIPPED (StorageClass existence still checked).
  "ceph-csi": {
    backend_type: "ceph-csi",
    cluster_name: "cluster-a",
    storage_class_name: "ceph-rbd",
    csi_driver: "rbd.csi.ceph.com",
  },
  "gpfs-csi": {
    backend_type: "gpfs-csi",
    cluster_name: "cluster-a",
    storage_class_name: "gpfs-sc",
    csi_driver: "spectrumscale.csi.ibm.com",
  },
  "weka-csi": {
    backend_type: "weka-csi",
    cluster_name: "cluster-a",
    storage_class_name: "weka-sc",
    csi_driver: "csi.weka.io",
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

// For a NEW filesystem mapping, default cluster_name (the "agent cluster" — the cluster
// whose DM agents report this storage) to the real DMS control cluster instead of the
// static "cluster-a" skeleton placeholder, which is a common misconfiguration. No-op when
// the control cluster is unknown or the backend is CSI (whose cluster_name is a real k8s
// target the operator must choose).
export function applyAgentClusterDefault(
  template: Record<string, unknown>,
  backend: string,
  controlCluster?: string | null,
): void {
  if (controlCluster && (FS_BACKEND_TYPES as readonly string[]).includes(backend)) {
    template.cluster_name = controlCluster;
  }
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
    desc: "이 매핑이 관리하는 CSI StorageClass 이름",
    default: "—",
  },
  {
    name: "csi_driver",
    required: false,
    desc: "StorageClass provisioner와 매칭할 driver. 생략하면 provisioner 일치 검사를 건너뜀(권장: 명시)",
    default: "—",
  },
];

export const FIELD_DOCS: Record<string, FieldDoc[]> = {
  cephfs: [
    { name: "backend_type", required: true, desc: "백엔드 타입 식별자", default: '"cephfs"' },
    { name: "cluster_name", required: true, desc: "에이전트 클러스터 — DM 에이전트가 보고하는 클러스터 (보통 기본 클러스터)", default: "기본 클러스터(자동 채움)" },
    { name: "mount_path", required: true, desc: "CephFS 마운트 경로", default: "—" },
    {
      name: "managed_root",
      required: true,
      desc: "DMS 관리 루트. mount_path 하위여야 하며 격리 경계가 됨",
      default: "—",
    },
  ],
  gpfs: [
    { name: "backend_type", required: true, desc: "백엔드 타입 식별자", default: '"gpfs"' },
    { name: "cluster_name", required: true, desc: "에이전트 클러스터 — DM 에이전트가 보고하는 클러스터 (보통 기본 클러스터)", default: "기본 클러스터(자동 채움)" },
    { name: "mount_path", required: true, desc: "GPFS 마운트 경로", default: "—" },
    { name: "managed_root", required: true, desc: "관리 루트 (mount_path 하위)", default: "—" },
    {
      name: "filesystem_name",
      required: true,
      desc: "GPFS 파일시스템(device) 이름. mm* 명령 대상. GPFS는 등록 필수",
      default: "—",
    },
  ],
  wekafs: [
    { name: "backend_type", required: true, desc: "백엔드 타입 식별자", default: '"wekafs"' },
    { name: "cluster_name", required: true, desc: "에이전트 클러스터 — DM 에이전트가 보고하는 클러스터 (보통 기본 클러스터)", default: "기본 클러스터(자동 채움)" },
    { name: "mount_path", required: true, desc: "WEKA 마운트 경로", default: "—" },
    { name: "managed_root", required: true, desc: "관리 루트 (mount_path 하위)", default: "—" },
    {
      name: "filesystem_name",
      required: false,
      desc: "WEKA 파일시스템 이름",
      default: "storage_name",
    },
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
