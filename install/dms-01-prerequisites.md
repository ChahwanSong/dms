# DMS 01 · 사전 준비 (클러스터 · 외부 요건)

DMS control plane을 배포하기 **전에** 클러스터와 외부 인프라에 갖춰야 하는 것들을 정리한다.
이 문서는 **새 운영(production) 클러스터**를 기준으로 쓴다. 테스트베드/개발 편의 옵션은 부연으로만
표기한다.

특히 **DM(데이터 잡: `scan`/`sync`/`rm`)** 은 아래 DM 전용 항목이 하나라도 빠지면 잡이 스케줄되지
않거나 후보 노드가 전부 거부되어 **에러 없이 조용히 미실행**된다(큐에 머물거나 preflight에서
rejected). 파일시스템·쿼터 **RM만** 쓰는 배포는 DM 전용 항목(§6~§9)을 건너뛸 수 있다.

값(호스트명·경로·도메인)은 모두 예시 placeholder다 — `registry.example.internal`, `cluster-a`,
`dc=example,dc=internal`, `/cephfs` 등. 각자 환경 값으로 치환한다.

---

## 한눈에 (체크리스트)

| # | 항목 | 필요 시점 | 없으면 |
|---|------|----------|--------|
| 1 | Kubernetes ≥ 1.26 + cluster-admin `kubectl` 접근 | 항상 | — |
| 2 | 컨테이너 레지스트리 (모든 노드에서 pull 가능) | 항상 | 파드 `ImagePullBackOff` |
| 3 | PostgreSQL 2개 DB (`dms` + `dms_observability`) | 항상 | API/워커/migration 기동 불가 |
| 4 | 관리 파일시스템 스토리지가 RM/DM 워커 노드에 **host-mount** | 파일시스템 RM/DM | mount readiness Missing → RM `missing_rm_readiness` |
| 5 | 노드 **NSS/SSSD** 신원 해석 | 파일시스템 RM(그룹)·DM(요청자) | DM `identity_not_ready_on_node`, RM 그룹 반영 지연 |
| 6 | **Volcano** 설치 (scheduler+controller+admission+CRD) | **DM 전용** | DM 잡 스케줄 불가 |
| 7 | **Queue `dms-data`** + **PriorityClass `dms-low/normal/high`** | **DM 전용** | 잡 영구 Pending / 파드 admission 거부 |
| 8 | DM 잡 네임스페이스 **PodSecurity=`privileged`** | **DM 전용** | DM 잡 파드 admission 거부 |
| 9 | 공유 **RWX artifact FS** — dm-worker·DM 잡 노드에 **동일 경로** | **DM 전용** | dm-worker가 `summary.json`을 못 읽어 잡 실패 |
| 10 | (k8s 쿼터 RM) 타깃 클러스터 kubeconfig + RBAC | 선택 | k8s namespace-quota RM 불가 |

> **MPI Operator는 설치하지 않는다.** DMS는 `DMS_DM_SCHEDULER_BACKEND=volcano-job`
> (control-plane.yaml 기본값)으로 **Volcano 네이티브 Job**(`batch.volcano.sh`)만 사용하며, Volcano
> 하나가 single-node·multi-node MPI 워커 파드를 gang-schedule한다. Kubeflow MPI Operator,
> `MPIJob`, `kubeflow.org` CRD는 **전혀 필요 없다** — 설치하지 말 것.

---

## 1. Kubernetes / kubectl

- control plane(`dms-api`·`dms-planner`·`dms-rm-worker`·`dms-dm-worker`)이 도는 클러스터에
  cluster-admin `kubectl` 접근이 있어야 한다. Kubernetes **≥ 1.26** 권장.
- DM 잡은 **`dms-dm-worker`가 도는 클러스터**에서 in-cluster로 생성된다
  (`DMS_DM_KUBERNETES_MODE=cluster`, 워커가 `--kubeconfig` 없이 잡을 만든다). 따라서 아래
  §6~§9(Volcano/Queue/PSA/artifact FS)는 **`dms-dm-worker`와 같은 클러스터**에 적용하며, 이 클러스터
  노드가 관리 대상 스토리지를 host-mount 해야 한다(§4).

## 2. 컨테이너 레지스트리

control plane·에이전트·DM 잡에 쓰이는 이미지를 **모든 관련 노드가 pull 할 수 있는 레지스트리**가
있어야 한다(control 클러스터 노드 + DM 잡 노드). 이후 `dms-02-core.md`에서 이미지를 빌드·push 하고,
DM 이미지는 `dms-05-dm-jobs.md`에서 다룬다. 빌드는 총 3종이며 **빌드 순서가 있다**(DM 잡 이미지 →
`dms-agent` 이미지 → 기본 `dms` 이미지) — 자세한 절차는 `dms-02-core.md`.

- private 레지스트리라면 각 클러스터 `dms` 네임스페이스에 **imagePullSecret**을 만들고 manifest의
  `imagePullSecrets`에 추가한다(기본 template에는 없다).
- 지금 필요한 것은 **레지스트리가 존재하고 노드에서 도달 가능**하다는 점뿐이다. 실제 pull 검증은
  첫 이미지를 push한 뒤 `dms-02-core.md`에서 임시 파드로 확인한다.

## 3. PostgreSQL (2개 DB)

DMS는 **operational DB `dms`** (요청/plan/run/resource)와 **observability DB `dms_observability`**
(진단 이벤트) 두 개를 쓴다. 운영에서는 **외부 관리형/HA PostgreSQL 16**(백업 포함)을 권장한다. 두
DB를 같은 인스턴스에 둘 수는 있지만 role·database는 분리한다.

초기화 스크립트는 `install/postgresql/init.sql`이며, role 2개(`dms_app`/`dms_obs`), DB 2개
(`dms` owner `dms_app`, `dms_observability` owner `dms_obs`), 스키마 grant, 그리고 세션 안전장치
(`statement_timeout=30s`, `idle_in_transaction_session_timeout=60s`)를 만든다.

### 3.1 비밀번호 치환

수정할 파일: **`install/postgresql/init.sql`**

- `CHANGE_ME_DMS_APP_PASSWORD` → `dms_app` role의 강한 비밀번호
- `CHANGE_ME_DMS_OBS_PASSWORD` → `dms_obs` role의 강한 비밀번호

secret을 git working tree에 남기지 않도록 임시 복사본에 치환해 적용하고 삭제하는 방식을 권장한다.

```bash
cp install/postgresql/init.sql /tmp/dms-init.sql
sed -i 's/CHANGE_ME_DMS_APP_PASSWORD/<강한-APP-비밀번호>/; s/CHANGE_ME_DMS_OBS_PASSWORD/<강한-OBS-비밀번호>/' /tmp/dms-init.sql
```

### 3.2 적용

`init.sql`은 `\connect` meta-command를 쓰므로 **반드시 `psql`로**, DB 생성 권한이 있는 superuser로
실행한다.

```bash
psql "postgresql://postgres@postgres.example.internal:5432/postgres" -f /tmp/dms-init.sql
rm -f /tmp/dms-init.sql
```

> **관리형 PostgreSQL로 role/DB 생성 권한이 제한**되면 DBA에게 다음을 요청한다: login role
> `dms_app`·`dms_obs`, database `dms`(owner `dms_app`)·`dms_observability`(owner `dms_obs`), 각 DB
> public schema의 table/sequence 생성 권한.

### 3.3 접속 확인

```bash
psql "postgresql://dms_app:<강한-APP-비밀번호>@postgres.example.internal:5432/dms" -c 'select 1'
psql "postgresql://dms_obs:<강한-OBS-비밀번호>@postgres.example.internal:5432/dms_observability" -c 'select 1'
```

두 명령 모두 `1`을 출력해야 한다. 이 두 접속 문자열이 `dms-02-core.md`에서 각각
`DMS_DATABASE_URL`·`DMS_OBSERVABILITY_DATABASE_URL`이 된다. **테이블 스키마는 이 스크립트가 아니라
`dms migrate`(dms-02-core의 migration Job)가 만든다.**

> **connection 사이징:** 각 DMS 프로세스는 URL당 bounded pool을 쓴다(기본 op 8 + obs 3 = 11).
> 표준 배포(프로세스 ≈ 6)면 `≈ 6 × 11 = 66`으로 stock `postgres:16` 기본 `max_connections=100`
> 아래에 머문다. 워커/pool을 크게 늘리면 `max_connections`도 함께 올리고 재검산한다. pool env는
> `dms-06-configuration.md` 참고.

> **부연(테스트베드):** 클러스터 내부에 PostgreSQL StatefulSet + PVC로 띄워도 되지만, 운영에서는
> 외부 관리형 인스턴스를 권장한다.

## 4. 스토리지 host-mount

관리 대상 파일시스템 스토리지(cephfs/gpfs/wekafs)는 **RM/DM 워커 노드에 host-mount** 돼 있어야
한다(스토리지 매핑의 `mount_path`, 예: `/cephfs`). 에이전트가 host mountinfo로 mount readiness를
판정하고, DM 잡 파드는 이 경로를 `hostPath`(`type: Directory`)로 붙인다 — 마운트가 없으면 파드가
기동 실패한다. 각 노드 `fstab` 등으로 부팅 시 자동 마운트되게 한다. 스토리지 매핑 등록은
`dms-03-rm-filesystem.md`.

## 5. 노드 신원 해석 (NSS/SSSD)

DM 잡은 **요청자의 POSIX 신원(uid/gid)** 으로 실행되므로, **DM 잡 노드**에서:

- `dms-dm-agent`가 요청자 계정을 신원-Ready로 확인할 수 있어야 한다(안 되면
  `identity_not_ready_on_node`). agent는 계층형으로 해석한다: 호스트 `chroot /host getent`
  → 호스트 `/etc/passwd`(host-root 마운트) → 컨테이너 NSS. `DMS_AGENT_IDENTITY_USERS`는
  상시 프로빙할 **베이스라인**일 뿐이고, 목록에 없는 요청자도 **온디맨드 프로빙**으로 자동
  확보된다(dm-worker가 요청 시 등록 → agent가 다음 사이클에 프로빙; `dms-05-dm-jobs.md §3`).
- **SSSD/LDAP-backed 노드 유저**를 프로빙하려면 `dms-dm-agent`에 **`SYS_CHROOT` capability**가
  필요하다(호스트 NSS 전체를 `chroot /host getent`로 조회 — nss_sss 포함). 노드-로컬
  `/etc/passwd` 유저는 host-root 마운트만으로 해석되어 capability가 필요 없다.
- 잡 이미지에는 NSS/LDAP이 없어도 된다 — dm-worker가 해석한 uid/gid로 잡 파드가 부팅 시
  요청자를 컨테이너 `/etc/passwd`에 **물질화**하므로 `runuser`/`chown`이 그 이름을 로컬에서
  해석한다.

→ DM 노드 OS에 **NSS/SSSD(또는 동등한 디렉터리 연동)** 를 구성해 요청자 계정이 노드에서
해석되게 한다. (LDAP는 preflight에서 dm-worker가 read-only로 조회하는 별도 경로다 — DM
신원 3요건은 `dms-05-dm-jobs.md`.)

> 파일시스템 **RM**도 노드 SSSD에 의존한다 — RM이 만든 `dms-grp-*` 그룹 멤버십이 전 노드에서 제때
> 보이도록 SSSD 캐시(`entry_cache_timeout`)를 낮춘다. 절차는 `dms-03-rm-filesystem.md`.

---

## 6. Volcano 설치 (DM 전용)

Volcano는 (1) DM 잡의 gang-scheduler이자 (2) DMS가 쓰는 네이티브 잡 타입(`batch.volcano.sh` Job)·
`Queue`·`PodGroup` CRD를 제공한다. **DM을 쓰면 하드 필수.**

```bash
# 버전은 조직 표준에 맞춰 핀한다(테스트베드 검증 버전 = v1.15.0). ⚠️ 확인 필요.
VOLCANO_VERSION=v1.15.0
kubectl apply -f https://raw.githubusercontent.com/volcano-sh/volcano/${VOLCANO_VERSION}/installer/volcano-development.yaml
```

검증:

```bash
kubectl -n volcano-system get deploy          # volcano-admission / -controllers / -scheduler 가 Ready
kubectl api-resources | grep volcano          # jobs.batch.volcano.sh, queues.scheduling.volcano.sh, podgroups...
```

MPI Operator는 설치하지 않는다(상단 callout 참조). `DMS_DM_SCHEDULER_BACKEND`는 `volcano-job`으로
둔다.

> **부연(제한망/air-gapped):** `raw.githubusercontent.com`에 직접 접근이 안 되면 매니페스트를 내부로
> 받아오고, Volcano 컨테이너 이미지도 내부 레지스트리로 미러링한 뒤 매니페스트의 이미지 참조를
> 그 레지스트리로 치환해 apply 한다.

## 7. Queue + PriorityClass (DM 전용)

DMS가 만드는 모든 DM 잡은 이름으로 참조한다: `spec.queue = DMS_DM_POLICY_DEFAULT_QUEUE`(기본
`dms-data`), `spec.priorityClassName = DMS_DM_POLICY_DEFAULT_PRIORITY_CLASS`(기본 `dms-normal`).
**큐가 없으면 잡이 영구 Pending**, **PriorityClass가 없으면 kube-apiserver가 파드를 admission에서
거부**한다. 별도 매니페스트로 제공한다:

```bash
kubectl apply -f install/kubernetes/volcano-queue-priorityclasses.yaml
kubectl get queue.scheduling.volcano.sh dms-data          # STATE=Open
kubectl get priorityclass | grep dms                      # dms-low(50) / dms-normal(100) / dms-high(200)
```

이름을 바꾸려면 **두 곳을 함께** 맞춘다:
- `install/kubernetes/volcano-queue-priorityclasses.yaml` — Queue/PriorityClass 이름
- `install/kubernetes/control-plane.yaml` → ConfigMap `dms-runtime-config` →
  `DMS_DM_POLICY_DEFAULT_QUEUE` / `DMS_DM_POLICY_DEFAULT_PRIORITY_CLASS`
- (DMS API로 per-tool policy를 등록했다면 그쪽 값도)

## 8. DM 네임스페이스 PodSecurity = privileged (DM 전용)

DM 잡 파드는 **root로 기동 → `SYS_CHROOT` capability 추가 → `runuser`로 요청자 uid/gid 강하** 하여
데이터를 다룬다. 클러스터 기본 Pod Security Admission이 `baseline`/`restricted`면 이 파드를
**거부**한다.

기본 DM 네임스페이스 `dms`에는 `install/kubernetes/control-plane.yaml`의 `Namespace/dms`에 이미
라벨이 있어 `dms-02-core.md` apply 시 함께 적용된다:

```yaml
metadata:
  name: dms
  labels:
    pod-security.kubernetes.io/enforce: privileged
```

DM 잡을 **다른 네임스페이스**(`DMS_DM_NAMESPACE`)에서 돌린다면 그 네임스페이스에 동일 라벨을 붙인다:

```bash
kubectl label namespace <dm-namespace> pod-security.kubernetes.io/enforce=privileged --overwrite
```

## 9. 공유 RWX artifact FS (DM 전용)

DM 잡 파드는 결과(`summary.json`)와 로그를 `DMS_DM_ARTIFACT_BASE_URI`(기본 `file:///artifacts/dms`)
아래에 쓰고, **`dms-dm-worker`가 그 파일을 로컬로 읽어** 잡을 분류한다. 따라서 artifact 경로는
**하나의 공유 RWX 파일시스템**이 **`dms-dm-worker` 노드와 모든 DM 잡 노드에 동일한 경로**로 마운트돼
있어야 한다(노드별 로컬 hostPath면 워커와 잡이 다른 노드에 뜰 때 깨진다).

- 수정할 파일: `install/kubernetes/control-plane.yaml` → `dms-dm-worker` Deployment →
  `dm-artifacts` hostPath. 기본 `path: /artifacts`(`type: Directory`, `HostToContainer` 전파)를
  실제 공유 FS **마운트포인트**로 치환한다(예: `/cephfs`). 서브디렉터리가 아니라 **마운트포인트
  자체**여야 host 재마운트가 컨테이너로 전파된다.
- 잡 파드도 같은 경로를 hostPath로 붙인다(워커가 자동 생성하는 잡 매니페스트가 처리).
- **베이스 서브디렉 생성 (1회)**: `DMS_DM_ARTIFACT_BASE_URI`의 베이스 디렉토리(예 `/artifacts/dms`)를 공유
  FS에 **`root:root 0755`로 미리 생성**한다 — 요청자 uid job pod가 per-job 디렉토리로 내려갈 수 있게.
  명령은 [`dms-05-dm-jobs.md §6`](dms-05-dm-jobs.md).

---

## 10. (선택) k8s namespace-quota RM 타깃 클러스터

Kubernetes namespace-quota RM을 쓰면 타깃 클러스터에 `dms-remote` ServiceAccount + RBAC
(`install/kubernetes/target-cluster-rbac.yaml`)와 그 kubeconfig가 필요하다. 절차는
`dms-04-rm-k8s-quota.md`.

---

## 11. 사전 준비 검증 (배포 전 빠른 확인)

```bash
# 3) PostgreSQL 2개 DB 접속
psql "postgresql://dms_app:***@postgres.example.internal:5432/dms" -c 'select 1'
psql "postgresql://dms_obs:***@postgres.example.internal:5432/dms_observability" -c 'select 1'

# 6) Volcano
kubectl -n volcano-system get deploy
kubectl api-resources | grep volcano

# 7) Queue + PriorityClass
kubectl get queue.scheduling.volcano.sh dms-data          # STATE=Open
kubectl get priorityclass | grep dms                      # dms-low/normal/high

# 8) PodSecurity (DM 네임스페이스)
kubectl get ns dms -o jsonpath='{.metadata.labels.pod-security\.kubernetes\.io/enforce}{"\n"}'   # = privileged

# 4/9) 공유 FS·스토리지 + 아티팩트 베이스 디렉토리 (dm-worker 예정 노드에서)
mount | grep <artifact-mountpoint>     # 예: /cephfs — dm-worker 노드와 DM 잡 노드에 동일 경로
ls -ld /artifacts/dms                  # 아티팩트 베이스: root:root 0755 여야 함(dms-05 §6). 없으면:
                                       #   sudo mkdir -p /artifacts/dms && sudo chown root:root /artifacts/dms && sudo chmod 0755 /artifacts/dms
# (파일시스템 RM만: managed_root(/cephfs/dms 등)는 dms-03 §4 — CephFS/Weka 자동 0711, GPFS 사전 생성)

# 5) 노드 신원 (DM 노드에서)
getent passwd <requester-user>         # 해석돼야 함
```

이 항목들이 준비되면 `dms-02-core.md`로 코어 배포를 진행한다.

---

## 다음 문서

- **[`dms-02-core.md`](dms-02-core.md)** — 코어 배포(이미지 빌드·순서, secret, control-plane, mTLS, ingress, migration)
- [`dms-03-rm-filesystem.md`](dms-03-rm-filesystem.md) — 파일시스템 RM 설정(스토리지 매핑, host-mount, SSSD)
- [`dms-04-rm-k8s-quota.md`](dms-04-rm-k8s-quota.md) — k8s namespace-quota RM(타깃 클러스터 kubeconfig/RBAC)
- [`dms-05-dm-jobs.md`](dms-05-dm-jobs.md) — DM(데이터 잡) 설정(DM 이미지·에이전트·신원 3요건)
- [`dms-06-configuration.md`](dms-06-configuration.md) — 환경변수 레퍼런스
- [`README.md`](README.md) — 설치 인덱스
