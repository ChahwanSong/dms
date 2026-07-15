# DMS 설치 가이드 (install/)

이 디렉토리는 DMS를 **프로덕션 Kubernetes 클러스터**에 설치하는 문서 + Kubernetes manifest + 설정
예시 + helper script를 모은다. 모든 절차는 **production 기준**이며, 테스트베드/dev 옵션은 각 문서에서
부연설명(secondary note)으로만 다룬다.

- **DMS 설치와 Portal 설치는 별도**다. Portal은 DMS API만 소비하는 독립 앱이며 DMS 설치 이후 진행한다.
- **설치 절차**만 여기(`install/`)에 있다. **API 사용법과 운영 런북**은 `docs/`에 있다.

---

## DMS 설치 순서

1. **[dms-01-prerequisites.md](dms-01-prerequisites.md)** — 클러스터/외부 사전 준비
   (Kubernetes, PostgreSQL 2개 DB, **Volcano + Queue `dms-data` + PriorityClass**, DM 네임스페이스
   PodSecurity=privileged, 공유 RWX artifact FS, 노드 NSS/SSSD, 레지스트리)
2. **[dms-02-core.md](dms-02-core.md)** — 코어 배포
   (이미지 3종 빌드/push, secret, `control-plane.yaml` 편집·apply, **mTLS 인증서** + ingress, DB migration)
3. **[dms-03-rm-filesystem.md](dms-03-rm-filesystem.md)** — 파일시스템 RM 설정 *(파일시스템 스토리지 관리 시)*
   (CephFS/WekaFS/GPFS 백엔드, ssh/sudoers, 백엔드별 LDAP, 스토리지 매핑, RM agent)
4. **[dms-04-rm-k8s-quota.md](dms-04-rm-k8s-quota.md)** — Kubernetes 네임스페이스 쿼터 RM 설정 *(k8s 쿼터 관리 시)*
   (agentless, target cluster RBAC + kubeconfig, 쿼터 정책)
5. **[dms-05-dm-jobs.md](dms-05-dm-jobs.md)** — 데이터 잡(scan/sync/rm) 활성화 *(DM 사용 시)*
   (Volcano 스케줄링, DM job 이미지 + dms-agent 이미지, `DMS_AGENT_IDENTITY_USERS`, dm-worker, artifact FS)
6. **[dms-06-configuration.md](dms-06-configuration.md)** — 환경변수 레퍼런스

> RM(3·4)과 DM(5)은 필요한 것만 선택 활성화한다. 최소 설치는 1·2·6.

## Portal 설치 (별도)

- **[portal-01-setup.md](portal-01-setup.md)** — DMS Portal (운영자/사용자 웹 UI) 설치·구성.

## 재배포 (소스 수정 후)

- **[redeploy.md](redeploy.md)** — DMS 코어 또는 Portal **소스코드 수정 후 재배포** 빠른 참조
  (이미지 빌드 → rollout, 대상 워크로드·컨테이너명, schema/Secret 주의). 무중단·백업·rollback 포함 정식
  업그레이드는 [../docs/operations-runbook.md](../docs/operations-runbook.md) §8~§10.

---

## 사용법 · 운영 (`docs/`)

설치가 아니라 **운영 중 사용법**은 `docs/`에 있다.

- **[../docs/api/README.md](../docs/api/README.md)** — DMS API 개요 + **인증(production = mTLS-verified)**
- [../docs/api/resource-management-fs.md](../docs/api/resource-management-fs.md) — 파일시스템 RM API
- [../docs/api/resource-management-k8s.md](../docs/api/resource-management-k8s.md) — k8s 쿼터 RM API
- [../docs/api/data-management.md](../docs/api/data-management.md) — DM scan/sync/rm API
- [../docs/api/operations.md](../docs/api/operations.md) — operations 조회 API
- **[../docs/operations-runbook.md](../docs/operations-runbook.md)** — 운영 런북 (점검·장애 대응·업그레이드·rollback)

---

## 인증 프로필 (요약)

운영은 **mTLS-verified 프로필**이다: control-plane에 `DMS_REQUIRE_MTLS_HEADER=true` +
`DMS_REQUIRE_MTLS_VERIFIED_HEADER=true`. 신뢰 ingress가 클라이언트 인증서를 검증·전달하고, DMS는
**인증서 subject에서 actor를 파생**한다(`mtls:` prefix). 평문 `x-dms-actor`는 신뢰하지 않으며
`DMS_DEFAULT_ACTOR`는 비워 둔다(설정 시 API startup 실패). 자세한 건 `dms-06-configuration.md` +
`../docs/api/README.md`.

## 스케줄러 (요약)

DM 잡은 **Volcano 네이티브 Job**(`DMS_DM_SCHEDULER_BACKEND=volcano-job`)으로 스케줄된다. Volcano
하나가 MPI worker를 gang-schedule하므로 **Kubeflow MPI Operator는 필요 없다**.

---

## 매니페스트 · 설정 · 스크립트 (참조)

- **`kubernetes/`** — `control-plane.yaml`(네임스페이스·ConfigMap·Secret·RBAC·Deployment·Service·
  NetworkPolicy·migrate Job), `agent-daemonset.yaml`(RM/DM agent), `volcano-queue-priorityclasses.yaml`,
  `target-cluster-rbac.yaml`, `dms-api-volcano-rbac.yaml`, `ingress.example.yaml`, `retention.yaml`,
  `sanity-reconciler.yaml`, `managed-rm-worker.yaml`
- **`config/`** — `dms-runtime.env.example`, `agent-storages.example.json`, `storage-mappings.example.json`,
  `cluster-kubeconfigs.example.json`, `default-quota-policies.example.json`, `identity-denylist.example.json`
- **`docker/`** — `Dockerfile`(dms 코어), `Dockerfile.mpifileutils`(DM 잡 이미지), `Dockerfile.agent`(DM agent 이미지)
- **`postgresql/init.sql`**, **`scripts/`** (dms-planned-shutdown·dms-resume·verify-install 등)
