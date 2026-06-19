# DMS 세션 핸드오프 (2026-06-18)

다음 세션이 현재 작업의 연장선에서 이어갈 수 있도록 핵심 정보 + 테스트베드 아키텍처를 정리.

> **2026-06-19 UPDATE**: bind-mount stale fix(`1483345`) + DM path base managed_root(`53f81d6`) **커밋·push 완료**
> (브랜치 `dms-dm-dev`, HEAD `53f81d6`). 현재 **`DMS_DM_PATH_BASE=managed_root` 운영 중** — 검증 완료: managed_root
> 모드 scan/dsync/rm(path를 managed_root 기준으로, 예 `scan-test`→`/cephfs/dms/scan-test`) + RM filesystem
> create+quota·mandatory-422 모두 PASS. 제어플레인 이미지 `pkg-01:5000/dms:bd2f4fb-pathbase`. 아래 본문의 일부
> "미커밋"/"mount_path로 복원" 표현은 이 UPDATE 기준으로 읽을 것. 잔존 미커밋: `install/docker/kubectl`(빌드
> 아티팩트, 커밋 금지) + testbed repo(`docs/ARCHITECTURE.md` 등 — 별도 repo, 미커밋).

---

## 0. 지금 어디까지 왔나 (한눈에)

- **identity_mappings 완전 제거 → read-only LDAP + denylist**: 구현·테스트·문서·라이브검증·커밋 완료(`8bc16e2`).
- **DM `sync`**: `dsync`(동일노드) + `nsync`(분리노드 멀티노드 MPI) 모두 **구현·라이브검증 완료**.
  nsync는 preflight 토폴로지 인지 수정(`382e0c7`)으로 통과, 5-rank 멀티노드 MPI 실복사 성공.
- **DM `scan`/`rm`**: 코드 구현 + **라이브 재검증 완료**. scan PASS(5파일/3디렉토리/22528B), rm PASS(preview→
  confirm→execution→실삭제), dsync 회귀 PASS. (검증 데이터: `/cephfs/dms/{scan-test,rmcase,dsynccase}`.)
- **bind-mount stale → 방안 A(HostToContainer) 적용·검증·문서 완료**(아래 §4). dm-worker가 cephfs **마운트포인트**
  를 rslave로 받도록 변경(서브경로 private → 마운트포인트 rslave). job pod 볼륨/`managed-rm-worker`도 동일
  propagation. 단위테스트(264 passed)+라이브(probe 전파/mountinfo `master:N`/scan·rm·dsync) 검증 완료. **방안 C
  (liveness 자가수복)는 미적용**(사용자 결정). **미커밋**(아래 §5).
- **DM path 기준 managed_root 전환 + managed_root 전역 mandatory**: 구현·테스트(272 passed)·라이브검증 완료.
  `DMS_DM_PATH_BASE`(기본 `mount_path`=현행, `managed_root`=옵트인) — planner가 storage별 managed_root suffix를
  prepend(volcano/preflight/result 불변, 원본은 `request_payload`에 보존). **filesystem mapping은 managed_root 명시
  필수**(등록 422, 암묵 `{mount}/dms` fallback 제거; 기존 row는 마이그레이션 backfill, testbed는 이미 명시라 no-op).
  라이브: managed_root 모드 scan `scan-test`→`/cephfs/dms/scan-test` PASS, 기본모드·RM 회귀 PASS. **ConfigMap은 검증
  후 기본 `mount_path`로 복원**(운영 전환은 `DMS_DM_PATH_BASE=managed_root`+planner 재기동). **미커밋**(§5).
- **문서**: `dms/install/4.dms-dm-api.md`는 현재 sync 위주 → **scan/rm 포함 통합 정리 PENDING**(아래 §1). bind-mount
  stale은 4.dms-dm-api.md(§7·§13.2)·ARCHITECTURE.md(§13.1) 갱신 완료(미커밋). 테스트베드 문서 갱신 완료(미커밋).

git(dms): 브랜치 **`dms-dm-dev`**, HEAD **`bd2f4fb`**(이번 stale fix는 미커밋, §5), 푸시본은 전부 반영.
제어플레인 현재 이미지 **`pkg-01:5000/dms:bd2f4fb-pathbase`**(stale fix + path-base 빌드; api/planner/dm-worker/
sanity-reconciler/rm-worker 모두 Ready). 이전: `bd2f4fb-mntprop`(stale fix만) → `382e0c7`(베이스).

---

## 1. 다음에 할 일 (PENDING — 우선순위순)

1. **`dms/install/4.dms-dm-api.md` 통합 정리** (#31, 사용자 명시 요청):
   - 현재 문서는 sync 위주(제목 "sync (dsync/nsync)"). **scan/rm까지 포함**해 현재 구현 기준으로 통합.
   - **과거 히스토리 clean up** (phase 표기, "후속 문서로 분리" 등 제거).
   - **API 예시**(scan/rm/sync 각각 요청·응답) + **전제조건**(마운트 조건, storage-mapping 등록, 워커노드 readiness 등) 추가.
   - 참고: `GET /api/v1/data-management/help`가 3 연산 요약 제공. scan=preview없음/dscan, rm=preview+confirm/drm/recursive, sync=preview+confirm/dsync|nsync.
   - (bind-mount stale propagation은 §7·§13.2에 이미 반영됨 — 통합 시 유지.)

2. **이번 stale fix 커밋**(미커밋, §5): 사용자 요청 시 커밋(브랜치 `dms-dm-dev`) + testbed repo `docs/ARCHITECTURE.md`.

**완료된 직전 작업** (이번 세션):
- **bind-mount stale 방안 A 적용·검증** (§4) ✓ — 사용자 지시(방안 A만, C 제외).
- **scan/rm 라이브 재검증** (#29/#30) ✓ PASS. scan(preview 없음/dscan), rm(preview→confirm→execution, **디렉토리 rm은 `options.recursive=true` 필수**, 없으면 422). ※ rm은 **대상의 부모 디렉토리 쓰기 권한** 필요 — `/cephfs/dms`(root:root 755) 직속은 cocoa.song도 `posix_permission_denied`(권한검사 작동). cocoa.song 소유 부모 밑(`rmcase/victim`)이라야 실삭제.
- 검증 스크립트: `$CLAUDE_JOB_DIR/tmp/{scan_rm_verify,rm_recursive_verify,dsync_smoke}.py`(API는 api Pod 내 `python3 urllib`, §3).
- (미수행/선택) outsider uid 10010 음성 케이스 — cocoa.song 부모권한 거부로 권한검사 작동은 이미 확인됨.

---

## 2. 테스트베드 아키텍처 (핵심)

호스트 **`luminous`**(Ubuntu 24.04, KVM/libvirt). 단일 호스트에 11개 VM = 2 k8s 클러스터(`dms`, `ddz26`) + 패키지노드 `pkg-01`. 상세: `testbed/docs/ARCHITECTURE.md`, `testbed/README.md`.

### 노드 / 네트워크
- **dms 클러스터**(작업 대상): `dms-cp1`(10.10.10.10, cp) + `dms-w1..w5`(10.10.10.11~15, worker). cri-o, k8s 1.34.6, Cilium, Volcano.
- `ddz26` 클러스터(10.10.10.20~23) — 본 작업과 무관.
- **`pkg-01`**(10.10.10.30): 어느 클러스터에도 join 안 함. **Ceph 서버 + OpenLDAP + PostgreSQL + 로컬 도커 레지스트리(`pkg-01:5000`)**.
- NAT `10.10.10.0/24`. luminous에서 `kubectl`(컨텍스트 `dms`) 사용 가능. ansible: `testbed/inventory/hosts.ini`(key `testbed/files/id_ed25519`, user ubuntu, host_key_checking off).

### 스토리지 (CephFS, 단일 fs `dmsfs` @ pkg-01) — **현재 라이브 토폴로지(전부 Ready)**
| DMS storage | 마운트 | dmsfs subtree | 노드 | 역할 |
|---|---|---|---|---|
| `cephfs-dms` | `/cephfs` | `/`(root) | **w1~5 전체** | 공유 **artifact**(`/cephfs/dms/artifacts`) + 일반/`dsync` |
| `cephfs-third` | `/cephfs-third` | `/third` | w1~3 | nsync **source** |
| `cephfs-secondary` | `/cephfs-secondary` | `/secondary` | w4~5 | nsync **destination** |
- 커널 ceph 마운트 `10.10.10.30:6789:/<subtree>`, client `dmsfs`(rwp), secret `/etc/ceph/dmsfs.secret`.
- **nsync 동작 조건**: source/dest는 disjoint 노드, artifact는 모든 참여노드 공통 마운트(=cephfs-dms). 이 토폴로지가 그 조건을 충족.
- 재현(ansible): `cd testbed && make cephfs-nsync` (= `playbooks/cephfs-nsync.yml`). inventory 그룹 `dms_source_workers`(w1-3)/`dms_destination_workers`(w4-5), 변수 `cephfs_nsync_source`/`cephfs_nsync_destination`(`group_vars/all.yml`). **`make cephfs-dms` 먼저** 필요. ARCHITECTURE §13.1.

### Identity / DB
- **LDAP**: `ldap://10.10.10.30:389`, base `dc=dms,dc=local`, users `ou=People`, filter `(uid={username})`. bind는 secret `dms-secrets`. 사용자: **cocoa.song**(uid 10003, gid 10000=dmsusers), outsider(10010/10001), alice(10001), bob(10002 — ※실제 LDAP엔 있음. 과거 "bob 없음" 테스트는 임의 미존재 유저 사용), seongje.jang(10004) 등.
  - DMS_AGENT_IDENTITY_USERS = `cocoa.song,outsider` (노드측 identity 증거 보유 유저). 검증은 보통 **cocoa.song** 사용.
- **운영 DB**: PostgreSQL `10.10.10.30:5432/dms` (URL은 secret `dms-secrets` `DMS_DATABASE_URL`).

### 빌드 / 배포 (luminous에서)
- 로컬 `docker`(sudo 무암호)가 insecure registry `pkg-01:5000` 신뢰. luminous 직접 인터넷 가능.
- **올바른 Dockerfile은 `install/docker/Dockerfile.testbed`** (kubectl 바이너리 COPY + `pip install '.[postgres,ldap,kubernetes]'` 포함, ~481MB). ❌ `deploy/Dockerfile` 쓰지 말 것(kubectl/kubernetes 누락 → DM 런타임preflight·storage-mapping·CM동기화 깨짐. 과거 회귀 사례 §아래).
  - `install/docker/kubectl`은 repo에 없음(gitignore 아님, 미커밋 빌드아티팩트). 없으면 `curl -sSL -o install/docker/kubectl https://dl.k8s.io/release/v1.34.6/bin/linux/amd64/kubectl && chmod +x` 로 복원.
- 절차:
  ```bash
  cd /home/mason/dms-dev/dms; SHA=$(git rev-parse --short HEAD)
  sudo docker build -f install/docker/Dockerfile.testbed -t pkg-01:5000/dms:$SHA .
  sudo docker push pkg-01:5000/dms:$SHA
  for d in dms-api dms-planner dms-dm-worker dms-sanity-reconciler dms-rm-worker; do
    kubectl set image -n dms deploy/$d $(kubectl get deploy -n dms $d -o jsonpath='{.spec.template.spec.containers[0].name}')=pkg-01:5000/dms:$SHA; done
  for d in ...; do kubectl rollout status -n dms deploy/$d; done
  ```
  - migrate Job: `dms migrate`(envFrom cm `dms-runtime-config` + secret `dms-secrets`). 스키마 변경 시 실행. (startup 시 migrate_all도 돔.)
  - 컨테이너명: api→`api`, planner→`planner`, dm-worker→`dm-worker`, sanity-reconciler→`sanity-reconciler`, rm-worker→`rm-worker`.

---

## 3. 라이브 검증 방법 (반복 사용)

- **API 인증**: shared token(secret `dms-secrets` `DMS_AUTH_SHARED_TOKEN`) `Authorization: Bearer <tok>` **+** `x-dms-actor: <name>` 헤더 **둘 다** 필요(actor 없으면 401 "missing actor evidence"). mTLS off.
- **api Pod엔 `curl` 없음** → Pod 내 `python3 urllib`로 호출. 패턴:
  ```bash
  API=$(kubectl get pods -n dms --field-selector=status.phase=Running -o name | grep dms-api | head -1 | cut -d/ -f2)
  TOKEN=$(kubectl get secret -n dms dms-secrets -o jsonpath='{.data.DMS_AUTH_SHARED_TOKEN}' | base64 -d)
  B64=$(base64 -w0 script.py); kubectl exec -n dms "$API" -c api -- sh -lc "echo $B64 | base64 -d > /tmp/s.py; TOK='$TOKEN' python3 /tmp/s.py"
  ```
- **잡 조회**: POST는 202 + `request_id` 반환(job_id 아님). `GET /api/v1/data-management/{scan|sync|rm}?requester_id=<r>&limit=50` → `request_id`로 잡 매칭. 상태 폴링은 `state` 필드.
- **confirm**(sync/rm): `POST /api/v1/data-management/jobs/{job_id}:confirm` body `{"requester_id":..,"confirm":true,"preview_observed_hash":<result_summary.preview.fingerprint>}`. `dm_confirm_require_preview_fingerprint=true`라 fingerprint 필수.
- **Volcano gang 주의**: terminal 잡의 leftover vcjob/worker pod가 큐·gang 용량을 점유해 다음 잡을 Pending으로 막음 → `kubectl delete vcjob -n dms <name>`으로 정리. (`DMS_DM_JOB_DELETE_ON_TERMINAL=false` 기본.)
- ansible 노드 명령: `cd testbed && ansible -i inventory/hosts.ini <group> -b -m shell -a "..."` (예: 마운트/파일 확인). 워커 그룹: `dms_workers`, `dms-w1` 등.

---

## 4. 해결됨: dm-worker bind-mount stale → 방안 A(HostToContainer) 적용·검증

- **원인(과거 현상)**: dm-worker가 `/cephfs/dms/artifacts`(= `/cephfs` 마운트의 **서브경로**)를 hostPath로 bind +
  propagation `None`(private). host에서 `/cephfs`를 언/재마운트하면 bind가 pod 시작 시점 마운트에 고정돼 새 마운트
  미가시 → 다른 노드 job artifact 못 봄. sync는 observed_state fallback으로 통과하나 scan은 로컬 artifact 파싱
  강제라 `summary artifact is missing or invalid` 실패.
- **적용한 해결(방안 A)**: dm-worker가 cephfs **마운트포인트**(`/cephfs`)를 `mountPropagation: HostToContainer`
  (rslave)로 마운트. 컨테이너는 `/cephfs/dms/artifacts`를 그대로 접근(ConfigMap base uri 불변). job pod의 모든
  hostPath 볼륨(source/dest/target/artifact)도 동일 propagation(volcano.py `_host_volume_mount` 헬퍼). `managed-rm-
  worker.yaml`도 동일. **방안 C(liveness 자가수복)는 미적용**(사용자 결정).
- **변경 파일**(미커밋, §5): `src/dms/adapters/volcano.py`(헬퍼+11개 마운트), `install/kubernetes/control-plane.yaml`
  (dm-worker 마운트포인트+rslave, hostPath `type: Directory`), `install/kubernetes/managed-rm-worker.yaml`,
  `tests/{test_data_management_scan,test_data_management_sync_rm,test_install_manifest_mount_propagation}.py`
  (3 신규/갱신). 전체 264 passed.
- **라이브 배포**: 이미지 `pkg-01:5000/dms:bd2f4fb-mntprop`. dm-worker는 **JSON patch**로 마운트포인트+rslave+이미지,
  나머지는 `set image`. ⚠ 레포 `control-plane.yaml`은 example placeholder 템플릿(`*.example.internal`,
  `file:///artifacts/dms`)이고 **라이브는 그걸 치환 배포**한 상태라, 라이브는 `kubectl apply`(템플릿 덮어쓰기) 금지 —
  `kubectl patch`로 외과적 변경. (라이브 base uri는 이미 `file:///cephfs/dms/artifacts`.)
- **검증(완료)**:
  - mountinfo: dm-worker `/cephfs`가 마운트포인트 전체(`root /`, mountpoint `/cephfs`) + `master:448`(rslave).
    변경 전은 서브경로(`/dms/artifacts`→`/cephfs/dms/artifacts`) + private(optional field 없음).
  - probe 전파(비파괴): host `/cephfs/dms/_probe_mp` tmpfs가 컨테이너 mountinfo로 전파 + marker 읽힘 →
    HostToContainer 실작동 실증.
  - 기능: scan PASS(다른 노드 artifact 읽기), rm PASS(실삭제), dsync PASS(실복사) — job pod propagation 회귀 없음.
- **미실측(저위험)**: host `/cephfs` **전체 동일경로 언→재마운트** 전파는 파괴적(노드 전체 pod 영향)이라 probe
  sub-mount 전파로 대체 증명. 마운트포인트를 rslave로 받았으므로 peer-group 통해 전체 재마운트도 전파 기대.
- **프로덕션 영향**: 부팅 fstab 마운트라 평상시 무관, 세션 중 마운트 변경/일시 커널 재마운트에만 관여.

---

## 5. 미커밋/잔존물

- **dms repo**(`dms-dm-dev`, HEAD bd2f4fb): 미커밋 = **[이번 stale fix]** `src/dms/adapters/volcano.py`,
  `install/kubernetes/control-plane.yaml`, `install/kubernetes/managed-rm-worker.yaml`,
  `tests/test_data_management_scan.py`, `tests/test_data_management_sync_rm.py`,
  `tests/test_install_manifest_mount_propagation.py`(신규), `handoff.md`(이 파일) **+ [path-base]** `src/dms/config.py`,
  `src/dms/domain.py`, `src/dms/planner/{_base,__init__,_core}.py`, `src/dms/cli.py`, `src/dms/backends/{cephfs,weka}.py`,
  `src/dms/migrations.py`, `src/dms/api/routers/resource_management.py`, `tests/test_dm_managed_root_path.py`(신규),
  `tests/test_inventory.py`, `tests/test_mtls_auth.py`, `install/4.dms-dm-api.md`(§7·§10·§14.9·path설명)
  + `install/docker/kubectl`(빌드아티팩트, **커밋 금지**), `nsync-topology-verify-report.md`(보존용). 272 tests passed.
  커밋은 사용자 요청 시.
- **testbed repo**(`master`, **미커밋**): `docs/ARCHITECTURE.md`(§13.1 마운트 propagation 추가) + `Makefile`,
  `README.md`, `group_vars/all.yml`, `inventory/hosts.ini` 수정 + `playbooks/cephfs-nsync.yml` 신규(= nsync 멀티스토리지
  코드화·문서화). + 이전 세션의 미커밋 변경(roles/*, terraform.tfvars, CEPH-PLAN.md 등 — 내 것 아님). 커밋은 사용자 요청 시(master 직접 말고 브랜치 권장).
- 라이브 잔존 테스트 데이터: `/cephfs/dms/{scan-test,rm-test,dsync-job,nsync-job}`, `/cephfs-third/proj`, `/cephfs-secondary/proj`. terminal 잡 row 다수(무해).

---

## 6. 메모
- 사용자: 한국어 답변 선호(자동메모리 `respond-in-korean.md`). 이메일 skychahwan@gmail.com. 오늘 2026-06-18.
- 전체 검증 리포트: `dms/nsync-topology-verify-report.md`(nsync 토폴로지·이슈·수정 이력 상세).
- mpifileutils 이미지: `pkg-01:5000/dms-mpifileutils:e3bfee1-u1`, 소스 `github.com/ChahwanSong/mpifileutils.git`(HEAD e3bfee1, `dsync/dcp/dscan/drm/nsync` + OpenMPI). `install/docker/Dockerfile.mpifileutils`로 빌드.
- dm-worker는 **root**(securityContext runAsUser:0, 잠긴 artifact 읽기용), api/planner/rm-worker는 비-root(65532).
