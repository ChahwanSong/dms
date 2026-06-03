# IBM GPFS / IBM Storage Scale Backend

Phase 13 turns the GPFS filesystem backend from a no-side-effect skeleton into
an IBM Storage Scale command adapter. DMS keeps `backend_type: gpfs`, but the
implementation expects a fileset-backed directory model and Storage Scale
commands on the RM worker node or an SSH-reachable GPFS administration node.

The current testbed has no IBM Storage Scale cluster, so live GPFS validation is
not marked done. Command rendering, parseable `-Y` output parsing, capability
failure, quota drift, sync, import preflight, and delete policy are covered by a
fake executor regression suite.

## Storage Mapping

Register GPFS storage with `backend_type: gpfs`.

```json
{
  "storage_name": "gpfs-a",
  "backend_template": {
    "backend_type": "gpfs",
    "filesystem_name": "gpfs0",
    "mount_path": "/gpfs/gpfs0",
    "fileset_root": "/gpfs/gpfs0/dms",
    "quota_scope": "fileset",
    "fileset_name_template": "dms-{directory_name}",
    "rm_worker_nodes": ["gpfs-rm-1"],
    "ssh_host": "gpfs-rm-1",
    "command_runner": "ssh-host-exec",
    "csi_driver": "spectrumscale.csi.ibm.com",
    "data_network": "storage-net-a"
  },
  "cluster_name": "cluster-a",
  "storage_class_name": "gpfs-csi",
  "sanity_status": "Ready"
}
```

`storage_name` remains the DMS-wide logical storage ID. Kubernetes
StorageClass identity remains scoped by `cluster_name + storage_class_name`.

Required filesystem RM fields:

- `filesystem_name`: IBM Storage Scale filesystem/device name.
- `mount_path`: mounted GPFS filesystem root visible from the command runner.
- `fileset_root`: parent directory under `mount_path` where DMS links filesets.
- `quota_scope`: must be `fileset` in Phase 13.
- `fileset_name_template`: must render to `[A-Za-z0-9._-]+`.
- `command_runner`: `local` or `ssh-host-exec`.
- `ssh_host` or `rm_worker_nodes[0]`: required when using `ssh-host-exec`.

## Filesystem Operations

`GpfsFilesystemBackendAdapter` now executes command-backed operations:

- create: `mmcrfileset`, optional `mmsetquota`, `mmlinkfileset`, `chgrp`,
  `chmod`, marker write, read-back verification.
- update: apply fileset quota with `mmsetquota` and verify through
  `mmlsquota -j <fileset> -v -Y <filesystem>`.
- check: read fileset and quota state, report `Consistent`, `Drifted`, or
  `Missing`.
- sync: read live quota and update DMS desired state to match the backend.
- import / assign-quota: require an existing linked fileset junction via
  `mmlsfileset <fs> -J <junction> -L -Y`.
- block / unblock: apply POSIX mode changes on the junction path.
- delete: unlink and delete only DMS-managed full resources with
  `mmunlinkfileset` then `mmdelfileset`; imported or quota-only resources are
  fail-closed.

The adapter probes command availability and quota support before write
operations. Missing commands, disabled filesystem quota, disabled per-fileset
quota, unsafe fileset names, and junction paths outside `fileset_root` fail
closed before backend side effects.

If a command succeeds and a later verification step fails, DMS records the run
as recovery-required / unknown-after-side-effect rather than pretending the
operation is safe.

## Quota Model

DMS filesystem quota maps to GPFS fileset quota:

- `capacity_bytes` -> `mmsetquota --block <soft>:<hard>`, rounded up to KiB.
- `file_count` -> `mmsetquota --files <soft>:<hard>`.

Soft and hard limits are rendered equal in Phase 13. `mmlsquota` usage values
are recorded only as backend evidence; filesystem API usage admission remains
removed and is not reintroduced for GPFS.

## Kubernetes and Data Management

Kubernetes namespace quota for GPFS CSI StorageClasses is a Kubernetes
`ResourceQuota` concern, not an IBM Storage Scale fileset-quota concern. Phase
17 routes GPFS CSI namespace quota requests through the same live
`KubernetesNamespaceQuotaLiveAdapter` used by CephFS, Longhorn, and future CSI
StorageClass backends. The previous `gpfs-kubernetes-quota-stub` path has been
removed from production/live registry selection.

This separation is intentional:

- GPFS filesystem resources use IBM Storage Scale commands such as
  `mmcrfileset`, `mmsetquota`, `mmlinkfileset`, `mmlsfileset`, and `mmlsquota`.
- GPFS CSI namespace quotas use Kubernetes `ResourceQuota/dms-storage-quota`
  with StorageClass-specific hard keys such as
  `gpfs-csi.storageclass.storage.k8s.io/requests.storage`.
- No Storage Scale `mm*` command should run for a Kubernetes namespace quota
  create/update/block/delete/check/sync/import/audit operation.

`GpfsDataManagementAdapter` contributes worker-pool metadata for future DM
phases: GPFS mount path, filesystem name, data network, POSIX identity
requirement, and candidate tools (`dsync`, `nsync`, `drm`, `dscan`).

## Verification

Local regression:

```bash
pytest -q tests/test_gpfs_backend.py
```

Current coverage includes:

- command rendering for fileset create and quota apply
- parseable `-Y` output parsing
- quota rounding and read-back state
- missing command fail-closed
- quota and per-fileset quota disabled fail-closed
- read-back mismatch after side effect marked recovery-required
- check drift and sync from live quota
- import requiring a linked fileset
- quota-only delete refusal
- GPFS CSI namespace quota routing through the live Kubernetes ResourceQuota
  adapter
- GPFS DM worker-pool metadata

Live GPFS validation still requires a staging or production IBM Storage Scale
cluster with the documented commands available to the RM worker. GPFS CSI
`ResourceQuota` behavior can be verified independently with a Kubernetes
StorageClass using `spectrumscale.csi.ibm.com`.
