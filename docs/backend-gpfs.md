# IBM GPFS Backend Skeleton

Phase 1 includes a GPFS/Spectrum Scale backend skeleton for Resource
Management and Data Management. The implementation records the same DMS
lifecycle state as other backends, but it does not execute GPFS or Kubernetes
side effects yet.

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
    "csi_driver": "spectrumscale.csi.ibm.com",
    "data_network": "storage-net-a"
  },
  "cluster_name": "cluster-a",
  "storage_class_name": "gpfs-csi",
  "sanity_status": "Unknown"
}
```

`storage_name` remains the DMS-wide logical storage ID. Kubernetes
StorageClass identity remains scoped by `cluster_name + storage_class_name`.

## Implemented Skeleton Boundaries

- `GpfsFilesystemBackendAdapter` implements filesystem create/update/block/
  initialize/delete/check/import/quota-only assignment as no-side-effect
  skeleton operations.
- `GpfsQuotaStrategy` converts DMS quota payloads into a GPFS-shaped quota
  request summary.
- `GpfsKubernetesNamespaceQuotaAdapter` models namespace `ResourceQuota`
  management for GPFS CSI StorageClasses.
- `GpfsDataManagementAdapter` contributes DM worker-pool requirements:
  GPFS mount path, filesystem name, data network, POSIX identity requirement,
  and mpifileutils tool candidates.
- `BackendAdapterRegistry` selects GPFS adapters when a plan references a
  `storage_name` mapped to `backend_type: gpfs`.

## Future Implementation Work

- Replace skeleton results with actual GPFS command/API calls.
- Validate GPFS fileset, mount, quota, and CSI driver state from RM Worker and
  DMS Agent inventory.
- Add bounded timeouts and explicit side-effect recovery verification for each
  GPFS operation.
- Render Kubernetes `ResourceQuota` values with production unit conversion and
  StorageClass-specific hard keys.
- Build Volcano job mount and node-selection manifests from GPFS DM worker
  pool metadata.
- Verify against a testbed or staging cluster that actually has GPFS mounted
  and IBM Spectrum Scale CSI installed.
