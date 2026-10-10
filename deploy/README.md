# DMS testbed deployment runbook

> **프로덕션 설치는 이 파일(716줄, 테스트베드 런북)을 읽지 마세요.**
> `deploy/overlays/prod/README.md` **하나**만 보면 됩니다 — 프리플라이트 →
> `values.env` 채우기 → 시크릿/TLS → `deploy/install.sh` → `deploy/verify.sh`.
> 이 문서는 테스트베드(개별 파일 apply)·시나리오·HTTPS 원리의 상세 참고용입니다.

Deploys the clean-slate DMS control plane (`dms api` / `dms controller` /
`dms agent`, execution backend = Volcano) onto the `dms` testbed. All
manifests here are written against the **new** CLI/config (`src/dms/cli.py`,
`src/dms/config.py`). (The old implementation's `legacy/install/` was removed
from the tree 2026-08-30 -- see git history if needed.)

Testbed facts baked into these assets (see repo `CLAUDE.md` for the source of
truth):

| thing | value |
|---|---|
| build/registry node | `pkg-01` (10.10.10.30), has `podman` |
| registry | `pkg-01:5000`, insecure |
| PostgreSQL | `postgresql://dmsapp:<PW>@10.10.10.30:5432/dmsdb` — PW 는 testbed `secrets.yml`(gitignored, 2026-08-30 보안 정리) |
| LDAP | `ldap://10.10.10.30:389`, base `dc=dms,dc=local` |
| CephFS `cephfs-dms` | `/cephfs`, mounted on w1-5 |
| CephFS `cephfs-third` | `/cephfs-third`, mounted on w1-3 |
| CephFS `cephfs-secondary` | `/cephfs-secondary`, mounted on w4-5 |
| artifact base | `file:///cephfs/dms/artifacts` |
| namespace | `dms`, PSA enforce=privileged |
| Volcano | v1.15.0 installed; queue/priority classes NOT yet installed |

---

## 이미지 빌드 전 게이트: 로컬 풀스택 e2e (수기, 슬라이스 23)

이미지를 만들기 **전에** 개발 머신에서 아래를 돌리고 초록을 확인한다 — §1(비상 빌드)
이든 §8(포탈 빌드)이든 똑같이 적용된다. 빨강이면 빌드를 제출하지 않는다.

```bash
cd frontend && npm run test:e2e
# = npm run build && tsc -p tsconfig.e2e.json && playwright test  (빌드까지 한 방)
```

싸다 — 시나리오 9건에 **약 30초**(빌드 포함, 2026-08-12 실측; 2026-10-08 부터 작업 삭제 06 을 더해
늘었고 2026-10-10 배치 단위 삭제까지 16건에 약 1분). 건너뛸 이유가 되는 비용이 아니다. 하네스가 백엔드를 띄우고 끝나면 스스로 죽인다(tmp DB 포함 정리).

**이 게이트는 수기다 — CI 는 없다.** 이 저장소에는 GitHub Actions 도, 이 스위트를
자동으로 돌려주는 어떤 것도 없다. 아무도 대신 돌려주지 않으니 **사람이 빌드 전에
돌리는 것**이 유일한 강제력이다(CI 구축은 별도 슬라이스 몫이다 — 그 사실을 숨기지
않으려고 이 문단을 여기 둔다).

**무엇을 잡는가.** 프론트 단위 테스트(`npx vitest run`, 228건)가 구조적으로 못 보는
네 가지다:

- **기하** — 넓은 표가 레이아웃을 밀어내는 유형의 회귀. 실제로 두 번 났다
  (`9fbef86` 계정 표, `6bc2ecb` 사이드바 밀림). jsdom 에는 레이아웃이 없어 단위
  테스트로는 영원히 안 잡힌다.
- **실 HTTP 왕복** — 세션 쿠키 로그인, 그리고 **`dist` 서빙 + SPA fallback**
  (`api/app.py`). vite dev 서버가 이 코드 경로를 가리므로 개발 중엔 절대 안 돈다.
- **폴링** — 요청 목록의 수렴, 상세 잡의 **종단 뒤 중지**(안 멈추면 무한 폴링).
- **풀스택 부팅** — `migrate` → `api` → `controller` → `agent` 리포트 → 스토리지
  `Ready` 까지가 한 번에 서는지. 하네스가 이걸 못 세우면 스위트는 skip 이 아니라
  실패한다.

클러스터도 PostgreSQL 도 쓰지 않는다(tmp sqlite + `execution_backend=stub`). 그래서
**§7 의 실 클러스터 시나리오를 대체하지 않는다** — 그 앞단에서 싸게 거르는 그물이다.

**전제.**

- 개발 머신에 **Google Chrome**. 러너는 시스템 크롬을 쓴다
  (`frontend/playwright.config.ts` 의 `channel:"chrome"`) — 브라우저 다운로드 0 이
  기본이다. 크롬이 없는 머신에서만 `npx playwright install chromium` 으로 번들
  크로미엄을 받고, 채널을 **빈 값**으로 비워 실행한다:
  `DMS_E2E_BROWSER_CHANNEL= npm run test:e2e`.
- `frontend/node_modules`(`npm ci`)와 저장소 루트 `.venv` 의 `dms` 편집 설치 —
  하네스가 `<repo>/.venv/bin/dms` 를 찾아 백엔드를 직접 띄운다.
- **포트 8093 이 비어 있을 것.** 누가 듣고 있으면 하네스가 즉시 실패한다. 낡은 서버에
  붙어 초록이 나는 것이 e2e 에서 가장 조용한 거짓말이라 의도적으로 거절한다 — 포트를
  바꾸지 말고 이전 실행의 잔재(python/node)를 정리한 뒤 다시 돌린다.

## 0. Registry setup (once)

On `pkg-01`, start the insecure registry and point every cluster node's
CRI-O at it:

```bash
# on pkg-01:
./deploy/docker/registry-setup.sh registry

# from anywhere with ssh to the k8s nodes (or kubectl --context dms configured):
./deploy/docker/registry-setup.sh nodes
```

Verify: `crictl pull pkg-01:5000/dms:dev` from any cluster node should not
error with "http: server gave HTTP response to HTTPS client".

## 1. Build and push images (비상용 — 평상시엔 §8 포탈 빌드를 쓴다)

> 슬라이스 21 부터 **평상시 빌드는 포탈**에서 한다(§8). 이 절은 **비상용**이다 —
> 클러스터가 아직 없거나(최초 부트스트랩), 포탈/컨트롤러가 죽어 빌드를 제출할 수
> 없을 때만 쓴다. 포탈 빌드와 달리 적합성 프리플라이트도, 리소스 봉투도 없다.

빌드하기 전에 위 「이미지 빌드 전 게이트」(`cd frontend && npm run test:e2e`)를
돌린다 — 비상 빌드라고 건너뛰지 않는다.

Also on `pkg-01` (build context = repo root, run from anywhere in the repo):

```bash
REGISTRY=pkg-01:5000 TAG=dev ./deploy/docker/build-and-push.sh
```

Builds and pushes, in order (agent depends on the other two):
`pkg-01:5000/dms-mpifileutils:dev`, `pkg-01:5000/dms:dev`,
`pkg-01:5000/dms-agent:dev`.

If you use a `TAG` other than `dev`, update it in `deploy/k8s/20-config.yaml`
(`DMS_JOB_IMAGE`) and the `image:` fields in `30-migrate-job.yaml`,
`40-api.yaml`, `41-controller.yaml`, `50-agent-daemonset.yaml` to match --
these are static manifests, not templated.

## 2. Mount CephFS on the worker nodes

> **에이전트 DaemonSet 은 스토리지마다 hostPath 를 나열하지 않는다**(2026-09-16).
> `50-agent-daemonset.yaml` 은 호스트 `/` 를 `/host/root` 에 `readOnly`(최상위 바인드 =
> 호스트 루트 fs; 전파된 스토리지 하위 마운트는 종전 hostPath 처럼 호스트 옵션 rw 유지)·
> `mountPropagation: HostToContainer` 로 한 번만 붙이고, 프로브가 `mount_path` 를 그
> 접두로 번역한다(`DMS_AGENT_HOST_ROOT`). 새 스토리지는 **노드에 마운트 + 포탈 등록**
> 만으로 1~2 보고 주기 안에 Ready 가 된다 — 매니페스트를 고치지 마라(계약 테스트가
> 막는다). 전제: 호스트 `/` 가 shared 마운트(systemd 기본, `findmnt -no PROPAGATION /`).
> 아니면 그 노드는 `propagation_stale` 을 보고한다(`mount --make-rshared /` 뒤 에이전트
> 재시작). 기존에 손으로 넣은 스토리지 hostPath 패치가 있으면 제거한다.

If not already mounted (testbed IaC target):

```bash
cd ~/dms-dev/testbed
make cephfs-nsync   # or whatever combination of cephfs-* targets mounts
                     # cephfs-dms/cephfs-third/cephfs-secondary on w1-5/w1-3/w4-5
```

Then create the shared-FS layout the manifests/seed step below assume exist.
On the testbed this is **codified** (idempotent Ansible, uses the testbed's
inventory; the testbed's own `make ceph` only mounts CephFS):

```bash
ANSIBLE_CONFIG=~/dms-dev/testbed/ansible.cfg ansible-playbook \
  -e ansible_ssh_private_key_file=$HOME/dms-dev/testbed/files/id_ed25519 \
  deploy/testbed/dms-shared-fs.yml
```

which produces (see the playbook header for the rationale, §2b for the rules):

```bash
# by hand, on any node with /cephfs mounted (root):
mkdir -p /cephfs/dms/artifacts /cephfs/managed
chown root:root /cephfs/dms /cephfs/dms/artifacts /cephfs/managed
chmod 770 /cephfs/dms            # DMS common dir -- requesters must NOT traverse it (§2b-3)
                                 #   (testbed only: no gidNumber-0 LDAP group; elsewhere 750, or 700
                                 #    with a gidNumber-0 LDAP group -- never 711/755, §2b-3)
chmod 755 /cephfs/dms/artifacts  # artifact base -- 3-hop rejects o+w / non-root owner (§2b-1)
chmod 755 /cephfs/managed        # data root (managed_root of cephfs-dms) -- OUTSIDE the common dir
mkdir -p /cephfs-third/managed /cephfs-secondary/managed   # only with `make cephfs-nsync`
```

The playbook also lays down the LDAP e2e fixtures under `/cephfs/managed/ldap-e2e`
(alice/bob/cocoa.song private/shared trees, growth files) that the portal
verification scripts use.

(`managed_root` for each registered storage below lives under its
`mount_path` -- `StoragesRepository._validate` in
`src/dms/repositories/storages.py` requires `managed_root == mount_path` or
a subdirectory of it. `dms-controller`/`dms-api` need `/cephfs/dms/artifacts`
readable -- see `DMS_ARTIFACT_BASE_URI`.)

## 2b. 제어면 root 전제 (2026-09-09)

`dms-api`·`dms-controller` 컨테이너는 **uid 0** 으로 돈다(`40-api.yaml`·
`41-controller.yaml` 컨테이너 securityContext: capabilities 전부 drop, 이미지 fs
읽기 전용; `migrate` initContainer·`30-migrate-job` 은 이미지 USER 65532 그대로).
운영 아티팩트 base(`/cephfs/dms/artifacts`)가 `root:root` 라 65532 로는 3홉 쓰기
왕복 검증이 항상 실패했기 때문이다. 이 결정에 따라오는 **배포 전제** 네 가지:

1. **base 와 `<base>/<job_id>` 는 root:root · 요청자 쓰기 불가(비-world-writable ·
   비-group-writable · POSIX ACL 쓰기 항목/default ACL 없음), 단 base 는 other 실행(x)
   필수** (`chmod 755` 또는 `711`; **700/710/750/770 · g+w · ACL 금지** — 2026-09-30 감사
   정정(예전 "권장 700" 은 틀렸다) + 2026-10-07 보조 그룹(§2c)). 러너가 `<job_id>` 를 root 로
   만들고 `<phase>` 만 요청자에게 chown 한다. 비 root 잡의 도구(preflight·워커 rank)는 요청자
   uid·주 gid **와 계획 시점 LDAP 보조 그룹**으로 돌지만, launcher 의 mpirun 은 **보조 그룹
   없이** `<phase>` 의 mpi-hostfile 을 읽는다 — base 를 other-x 로 통과하지 못하면 비 root
   잡(일반 사용자 전부, 실행 신원을 지정한 관리자 잡)이 preview/execution 에서 "unable to
   open the hostfile" 로 죽는다. 그래서 그룹으로 여는 750/710 은 이제 통하지 않는다(그룹을
   가진 preflight 는 통과하고 launcher 가 죽는다). 또 잡이 보조 그룹을 달고 돌므로 base 의
   `g+w` 는 그 그룹의 **모든 요청자**에게 base 쓰기를 준다 — 보조 gid 0 도 인정하므로(§2c)
   root 그룹 `g+w` 도 마찬가지다.
   **강제 위치 두 곳**:
   - **제어면**(3홉 API·컨트롤러 홉 + base 저장 PUT/validate 422,
     `artifact_base.roundtrip_artifact_base`): 소유자가 제어면 euid(root)가 아니면
     `artifact_base_not_owned`, `o+w` 면 `artifact_base_world_writable`(sticky 여도),
     `g+w`(gid 무관)·POSIX ACL 의 named 쓰기 항목·default ACL 이 있으면
     `artifact_base_group_writable`, other-x 가 없으면 `artifact_base_not_traversable`.
     **예전 이 문서가 허용하던 750/710(및 770) base 도 이제 거부된다** — 저장이 막히고 3홉
     화면이 빨갛다. 이 판정 자체는 표시·저장용이라 잡 제출을 막지 않는다.
   - **잡 단위(컨트롤러 정적 관문, 보조 그룹이 실린 비 root 잡)**: 매 제출 직전(stepper
     `_build_spec`) 컨트롤러가 base 에 위와 같은 g+w·POSIX ACL(default ACL 포함)·other-x 판정
     (`artifact_base.static_base_problem`)을 직접 적용해, 걸리면 `artifact_base_group_writable`·
     `artifact_base_not_traversable` 로 종단한다(이벤트 `artifact_base_unsafe_at_step`). 컨트롤러가
     base 를 stat 하지 못하면 이 판정은 preflight 에 맡긴다(통과로 치지 않는다).
   - **잡 단위(preflight)**: 모든 비 root 잡은 실행 신원으로 `test -x`
     (`artifact_base_not_traversable`), 보조 그룹이 있는 잡은 base 의 other-x **비트**와
     `test -w`(쓰기 가능하면 `artifact_base_group_writable`)까지 본다. `test -w` 는 access(2)
     라 base **자체**에 걸린 NFSv4/GPFS ACL 까지 반영하지만 **상속은 못 본다** — base 엔 쓰기가
     없고 default POSIX ACL·NFSv4/GPFS inheritable ACE 만 있으면 통과하고, 러너가 root 로 만드는
     `<job_id>/<phase>` 가 그것을 물려받아 그 그룹의 다른 사용자가 남의 rank.sh 를 바꿔치기할 수
     있다. POSIX default ACL 은 **보조 그룹이 실린 잡에 한해** 위 컨트롤러 관문이 제출 전에 막는다.
     보조 그룹이 없는 비 root 잡(그룹 없는 사용자·스위치 off·256개 초과)은 잡 단위로 default ACL 을 보지
     않고(3홉 화면만 빨개진다), **NFSv4/GPFS 상속 ACE 는 어느 잡에도 남는 위험**이다(그런 스토리지에 base 를
     두면 상속 ACE 가 없는지 직접 확인할 것).
   고치는 법: `chown root:root <base>; chmod 755 <base>`(g+w 제거), POSIX ACL 은 **재귀로**
   `setfacl -R -b -k <base>` — base 만 지우면 그 사이 만들어진 `<job_id>` 디렉터리에 상속된 ACL 이 남는다
   (`getfacl -R -s <base>` 로 남은 항목이 없는지 확인). 상속 default ACL(POSIX/GPFS/NFSv4)이 `<job_id>`·`<phase>` 를 좁히면
   요청자 통과와 제어면의 other 읽기가 함께 깨지므로 확인할 것. base 가 쓰기 가능하면
   요청자가 `<job_id>` 를 미리 만들어 봉쇄 기준을 옮길 수 있다(위 제어면 판정이 그 강제다).
   (테스트베드 base 는 65532 시절의 777 을 d128 에서 755 로 정정했다.)
2. **`fs.protected_hardlinks=1` · `fs.protected_symlinks=1`** — 공유 FS 를 마운트하고
   사용자가 `link(2)` 를 부를 수 있는 **모든** 호스트(k8s 워커뿐 아니라 로그인·계산
   노드 포함). 하드링크 허용 여부는 **링크를 만드는 쪽 커널**이 결정하므로(MDS 는
   검사하지 않는다) 워커만 켜 두면 충분조건이 아니다. 에이전트 프로브는 k8s 워커만
   본다. 확인: `sysctl fs.protected_hardlinks fs.protected_symlinks` (둘 다 1).
   코드는 nlink>1·남의 소유 파일을 404 로 거르지만(`src/dms/artifact_files.py`) 이
   sysctl 이 근본 방어다.
3. **공용 디렉터리(base 의 부모, 예 `/cephfs/dms`)는 root:root 로 잠그되 그룹 쓰기·other
   통과는 금지 — `750`(gidNumber 0 LDAP 그룹이 있으면 `700`)**(2026-09-09 d129 에 "770 까지" 로
   시작, 2026-10-07 보조 그룹으로 정정, 2026-10-08 리뷰로 711 제거). **요청자는 이 디렉터리를
   통과하지 못해야 한다** — other-x(`711`·`755`)를 주면 모든 uid 가 base(755)를 지나 0755 잡
   디렉터리와 0644 아티팩트(stdout/stderr 로그·rank.sh·summary.json·dscan 리포트 — 남의 데이터
   경로·파일 통계)를 직접 읽어 API 의 소유자 검사(`artifact_files.open_artifact_fd`)를 우회한다
   (711/755 는 base **자체**의 선택지다, 위 1). 제어면·에이전트·러너(launcher)는 root 라 통과하고, 요청자 uid 로
   도는 도구(dscan/dsync)·rank.sh 는 잡 파드가 base 를 **전용 hostPath 볼륨**
   (`/dms-artifact-base`, `artifact_base.ARTIFACT_MOUNT`)으로 받아 그 부모를 지나가지
   않는다. **770 은 더 이상 잠금이 아니다**: 보조 gid 0 을 인정하므로(§2c) gidNumber 0 인
   LDAP posixGroup 의 멤버는 잡 안에서 root 그룹 권한을 가져 770 부모 아래에 base 옆
   디렉터리를 만들 수 있다 — 그런 그룹이 있는 사이트는 `700`(planner 가 그런 잡에 warning
   이벤트 `identity_groups_root_group` 을 남긴다). 테스트베드 `/cephfs/dms` 는 770 그대로다
   (테스트베드 LDAP 에 gidNumber 0 그룹이 없다 — 750 으로 조일지는 사용자 결정 사항).
   단 **스토리지의 `managed_root`(사용자 데이터 root)는 공용 디렉터리 아래에
   두지 마라** -- preflight 가 요청자 uid 로 `test -r/-w` 하므로 `source_not_readable`
   류로 거부된다(테스트베드는 §6 시드대로 `cephfs-dms` 의 managed_root 가
   `/cephfs/managed` 여야 한다; `/cephfs/dms` 로 드리프트해 있던 것을 d129 실증에서
   되돌렸다). 실증: `/cephfs/dms` 770 + 데이터 `/cephfs/managed` 에서 alice sync Succeeded.
4. **`DMS_ARTIFACT_BASE_ALLOWED_PREFIXES`**(`20-config.yaml`, 기본 `/cephfs`; 오버레이는
   `/<SHARED_FS>`) — 포탈에서 고를 수 있는 base 를 파드가 실제로 마운트한 공유 FS 로
   묶는다. root 라 파일시스템이 더는 경로를 걸러 주지 않는다. 허용 밖 경로는 422
   `artifact_base_outside_allowlist`, 이미 저장된 base 가 밖이면 3홉 화면의 API·컨트롤러
   홉이 같은 사유로 실패를 낸다.

**재기동·재프로비저닝 영속성**(2026-09-09 실증): 위 레이아웃은 CephFS(pkg-01 OSD 볼륨)에
있어 VM 재부팅·`make vm-down/vm-up` 에도 남고(k8s 노드 6대 + pkg-01 동시 재부팅 뒤 마운트·
권한·DMS 파드·3홉·스토리지 전부 그대로 복귀), 스토리지 managed_root 등 DB 상태는 PostgreSQL
(pkg-01)에 있어 DMS 재기동(`kubectl rollout restart`)과 무관하다. **사라지는 경우는 `make
destroy` 로 Ceph 를 새로 만들 때뿐**이며 그때는 `deploy/testbed/dms-shared-fs.yml`(§2) →
§3~§6(README §6 시드는 이미 `/cephfs/managed`) 순으로 되돌린다. 재부팅 직후 첫 잡은 에이전트
첫 보고(30s)·프로브(60s) 전이면 `no_ready_sync_candidate` 로 거부될 수 있다 — 1~2분 뒤 재제출.

**적용 방법 주의**: securityContext 는 매니페스트 필드라 포탈 릴리스(이미지 patch)만으로는
실리지 않는다 — d128 이상으로 올릴 때 사이트 오버레이 `kubectl apply -k
deploy/overlays/<site>` 를 한 번 반드시 거친다(드리프트 배지는 이미지만 비교한다).

배포 후 확인:

```bash
kubectl -n dms exec deploy/dms-api -c api -- sh -c 'id; grep -E "^Cap(Eff|Bnd)" /proc/1/status; touch /app/.w'
# uid=0(root) gid=0(root) / CapEff: 0000000000000000 / touch: Read-only file system
kubectl -n dms get pod -l app.kubernetes.io/name=dms-api \
  -o jsonpath='{.items[0].spec.initContainers[0].securityContext}{"\n"}'   # {} (migrate 는 65532)
```

되돌리기(비root 로): 운영 노드에서 `chown 65532 <artifact_base>` 로 base 소유자를
바꾸고 40/41 의 컨테이너 securityContext 를 제거한다(계약 테스트도 함께 뒤집어야
한다). 그 상태에선 root:root base 마다 `artifact_base_not_writable` 이 난다.

## 2c. 보조 그룹(LDAP gidNumber) 인정 (2026-10-07)

비 root 잡(일반 사용자 전부, 실행 신원을 지정한 관리자 잡)은 실행 신원의 **LDAP 보조 그룹**
(posixGroup 의 gidNumber)을 달고 돈다 — "프로젝트 그룹에 쓰기 권한이 있는 디렉터리" 로의
sync·scan 이 된다. root 잡은 무관하다(root 는 그룹이 의미 없다). 규칙의 진실은
`src/dms/identity.py`·`identity_ldap.py`·`stepper.py` 모듈 docstring 과 `docs/ARCHITECTURE.md`
(§4 보조 그룹 재확인, §6, §7 불변식 5·9·10, §8)이다. 운영자가 알아야 할 것만:

**동작 요약**
- **계획 시점에 확정된다**: planner 가 요청을 계획할 때 LDAP 에서 그룹을 읽어 잡에 얼린다
  (`worker_pool.identity` 의 `supplementary_gids`·`_status`·`_excluded`·`_found`, 잡 상세의
  '보조 그룹(gid)' 행). 그 뒤 LDAP 에 새로 들어간 그룹은 그 잡에 **늘지 않는다**(재신청).
- **무엇이 실리나**: objectClass `posixGroup` 이고 gidNumber 가 숫자 하나인 그룹의 gid 만.
  gidNumber 가 없는 그룹(앱 그룹 등)·중첩 그룹(그룹 안의 그룹)은 제외한다. 그룹 이름은 실행에
  쓰지 않는다(denylist 이름 매칭 전용). 그룹 검색 멤버 속성은 `DMS_LDAP_GROUP_MEMBER_ATTR`
  (기본 uniqueMember) 그대로다.
- **범위**: 하한 없음(gid 0·65534 도 인정 — 사용자 결정). gid > 2147483647 은 k8s 가 파드를
  거부하므로 **제외**(화면·이벤트 `identity_groups_filtered` 에 보인다). 주 gid 와 같은 값은 뺀다.
- **256개 초과면 통째로 미적용**(status `over_limit` — 주 gid 만, 이 기능 이전과 같음) + 잡 상세·
  이벤트 표시.
- **실행 직전 재확인**: 그룹이 실린 잡은 매 제출 직전(preflight·preview·exec_preflight·execution)
  과 Volcano 큐 대기(PENDING) 중에 LDAP 를 다시 본다. 스냅숏의 그룹에서 탈퇴했거나 uid/gid 가
  바뀌었거나 계정이 지워졌으면 `identity_changed_at_step` 으로 종단(이벤트에 빠진 gid).
  LDAP 장애면 **상태를 바꾸지 않고 보류**해 60초 이상 간격으로 **3번 재시도**하고(이벤트
  `identity_recheck_deferred` 가 시도 횟수), 그래도 안 되면 `ldap_unavailable` 로 종단한다.
- **자동 chown 은 여전히 uid:주 gid**. 결과를 프로젝트 그룹 소유로 남기려면 sync 옵션에 chown
  `uid:<프로젝트 gid>` 를 명시한다 — 그 gid 가 실행 신원의 주 gid 또는 적용된 보조 gid 가
  아니면 계획 시점에 `chown_group_not_member`(예전엔 복사 뒤 EPERM 실패).
- **같이 들어간 fail-closed**(스위치로 안 꺼진다 — 아래 표): LDAP 주 gid 가 0 인 비 root 실행
  거부(`identity_root_group_without_privilege`), LDAP 사용자 엔트리 중복·비성공 결과 코드
  (sizelimit·noSuchObject(32, base DN 오구성 포함) 등)·그룹 1만 개 초과는 `ldap_unavailable`
  (예전엔 첫 엔트리·부분 결과를 조용히 썼다), 실행 신원 지정 자격의 계획 시점 재확인.

**스위치 범위표** — `DMS_IDENTITY_SUPPLEMENTARY_GROUPS`(코드 기본 `true`, 오타는 꺼짐 쪽):

| 변경 | `DMS_IDENTITY_SUPPLEMENTARY_GROUPS=false` 로 꺼지나 | 되돌리는 법 |
|---|---|---|
| 새 계획의 보조 gid 스냅숏(status disabled, 목록 []) | **예**(계획 시점만; 진행 중 잡은 스냅숏대로) | 스위치 |
| 진행 중 잡의 재확인·적용(스냅숏이 이미 있는 잡) | 아니오 | drain 후 취소 또는 이미지 롤백 |
| 리졸버 fail-closed(중복 엔트리·비성공 결과 코드·base 오구성 32·페이지 상한·posixGroup 만) | 아니오 | 이미지 롤백 |
| 비특권 주 gid 0 거부 | 아니오 | 이미지 롤백 |
| planner owner_username 선검사·자격 재확인 | 아니오 | 이미지 롤백 |
| chown_group_not_member(스위치 off 면 주 gid 만 허용) | 아니오(더 좁아짐) | 이미지 롤백 |
| artifact base 3홉·PUT 판정(g+w·ACL·o+x, §2b-1) | 아니오 | base 를 고치거나 이미지 롤백 |
| resolve 마감·틱 LDAP 예산 | 아니오 | 이미지 롤백 |

**비상 끄기**(이미지 유지): 테스트베드는 `deploy/overlays/testbed/patch-config.yaml` 에
`DMS_IDENTITY_SUPPLEMENTARY_GROUPS: "false"` 를 넣고 `kubectl apply -k deploy/overlays/testbed`(§4 의 가드·migrate
순서대로), 프로덕션은 `values.env` 의
`IDENTITY_SUPPLEMENTARY_GROUPS=false` → `sh deploy/install.sh`(render.sh 렌더 + apply; 미리보기는
`sh deploy/overlays/prod/render.sh`) — 그다음 `kubectl -n dms rollout restart deploy/dms-controller`(planner 만 읽는다). 이미 계획된
잡을 멈추는 kill switch 는 없다(BACKLOG) — 멈추려면 그 잡을 취소한다.

**이미지 롤백 절차**(이 기능 이전 이미지로): 옛 controller 는 스냅숏 키를 무시해 주 gid 로만
돈다 — 새 controller 가 그룹을 실어 preflight 를 통과시킨 잡을 옛 controller 가 그룹 없이
실행하면 단계 사이가 갈라진다. 그래서 ① 포탈 관리 → 컨트롤 상태에서 **drain** 켬 ② 위험군 잡을
취소(또는 완료 대기) — (a) 보조 gid ≠ [] 이고 `exec_preflight` ref 는 있으나 `execution` ref 가
없는 Executing sync/rm, (b) 보조 gid ≠ [] 인 Preflight scan ③ 이미지 롤백(포탈 릴리스) ④ 새
파드로 완전히 교체됐는지 확인 ⑤ drain 끔. ConfirmPending 잡은 옛 exec_preflight 가 그룹 없이
다시 검사하므로 안전하다.

**스토리지별 조건** — DMS 가 그룹을 실어도 **인정 여부는 스토리지가 정한다**(포탈은 등록
`backend_type` 라벨 기준 주의문만 보인다). preflight 의 `test -r/-w` 는 access(2) 라 도구와
같은 판정을 받으므로, 스토리지가 그룹을 무시하면 대개 preflight 가 `source_not_readable`·
`target_not_readable`·`destination_*` 로 먼저 거부한다(조용한 실행 실패가 아니다).

| 스토리지 | 확인할 것 |
|---|---|
| CephFS | 커널 클라이언트는 클라이언트 커널이 판정(테스트베드 caps `rwp`). caps 에 `uid=`/`gids=` 제한이 있으면 MDS 가 gid 목록을 검사한다. ceph-fuse 면 `client_permissions`·`fuse_set_user_groups` 확인 |
| GPFS(Spectrum Scale) | 클라이언트가 프로세스 자격으로 판정. NFSv4 ACL 모드, 멀티클러스터 원격 마운트의 uid/gid 재매핑 확인 |
| WekaFS | **미검증** — 판정 위치(클라이언트/백엔드)·그룹 수 상한을 벤더 문서·실측으로 확인 |
| DDN Lustre | MDS 가 그룹을 다시 계산할 수 있다: `lctl get_param mdt.*.identity_upcall` — `l_getidentity` 면 MDS 의 NSS(같은 LDAP 를 보면 그 멤버십, 모르면 그룹 소실), `NONE` 이면 RPC suppgid 만. nodemap squash/idmap 도 영향 |
| NFS(Pure Storage·NetApp) | AUTH_SYS 는 보조 gid 를 **16개까지만** 싣는다(넘치면 조용히 잘림). 서버 측 확장 그룹(ONTAP `-auth-sys-extended-groups`, knfsd `manage-gids`)이 켜져 있으면 클라이언트 목록을 버리고 서버 name-service 가 정한다(서버가 같은 LDAP 를 못 보면 그룹 전부 소실). `sec=krb5`·볼륨 security style(ntfs/mixed)도 결과를 바꾼다. 음성 사례: 테스트베드 luminous(manage-gids=y, 서버에서 `getent passwd alice` 실패) |

**진단**
- 잡 상세 '보조 그룹(gid)' 행 / 요청 이벤트 `identity_groups_filtered`(제외·초과),
  `identity_groups_root_group`(gid 0 포함 — warning), `identity_recheck_deferred`·
  `identity_recheck_failed`·`identity_changed_at_step`(재확인).
- **DMS 가 실었나**: preflight 파드 `kubectl -n dms get pod <dms-preflight-…> -o
  jsonpath='{.spec.securityContext.supplementalGroups}{"\n"}{.status.containerStatuses[0].user.linux.supplementalGroups}'`
  (k8s 가 실제 적용한 목록), 워커 로그의 `dms: groups=…` 줄.
- `identity_groups_not_applied`: 컨테이너의 실제 그룹(`id -G`)이 {주 gid} ∪ 스냅숏과 **같은
  집합**이 아니다 — 어드미션 웹훅(Kyverno 류)이 supplementalGroups·fsGroup·runAsUser 를 바꿨거나,
  사이트 커스텀 잡 이미지의 계정이 이미지 그룹에 속해 있다(그 이미지는 정리 필요).
- 워커 stderr `dms: account <user> exists with a different uid/gid` + exit 1(→ preview_failed/
  execution_failed, 전용 사유 코드 없음): 잡 이미지에 같은 이름의 계정이 다른 uid/gid 로 있다 —
  예전엔 조용히 **이미지 계정의 신원**으로 돌았다. 이미지에서 그 계정을 지우거나 이름을 바꾼다.
  root 잡이어도 owner 이름이 겹치면 같다.
- 스토리지가 그룹을 무시: preflight 가 그룹을 실었는데(위 jsonpath) `*_not_readable`/
  `destination_*` 면 위 스토리지 표를 본다.
- 느린 LDAP: 컨트롤러 stderr `stepper: tick …s ldap …s/10s circuit=…`(느린 틱·서킷·예산 소진만).

**`DMS_LDAP_TIMEOUT_SECONDS` 는 6 이하 권장**(기본 5): 한 틱의 LDAP 시간 최악 = 예산 10s + 진행
중 한 URI 시도(연결·StartTLS·bind = 3 × 타임아웃 — bind 뒤 서버 정보(스키마) 읽기는 끈다)라 루프
리스 30s 안에 들어야 한다(ARCHITECTURE §4 틱 LDAP 시간). 넘기면 리스가 넘어가 두 번째 컨트롤러가
같은 루프를 동시에 돌 수 있다. 다중 URI 는 sssd 처럼 **마지막으로 붙은 URI 부터** 시도한다(프로세스
수명 동안 기억) — 앞쪽 URI 가 타임아웃형으로 죽어도 resolve 마다 그 타임아웃을 다시 내지 않는다.
resolve 하나의 자체 마감(10s)에 걸려 뒤쪽 URI 를 못 가 본 resolve 는 `ldap_unavailable` 이지만, 다음
resolve 는 못 가 본 URI 부터 시작한다. 틱 예산의 남은 몫에 걸려 멈춘 resolve 는 장애로 치지 않는다
(planner 는 요청을 Pending 으로 다음 틱에, stepper 는 미계수 보류).

**배포 직후 확인**(프로덕션 사전 점검을 하지 않기로 했으므로 사후 확인으로 대신):
1. 릴리스 직후 몇 틱 동안 planner `ldap_unavailable` 거부·`identity_recheck_deferred`·
   `stepper: tick … ldap …` 줄이 급증하지 않는지(새 fail-closed: 중복 엔트리·비성공 결과 코드·
   base DN 오구성 32·페이지 상한). 원인은 컨트롤러 stderr 의 `planner: <rid> rejected
   ldap_unavailable: <원인>` 줄로 가른다(`duplicate user entries: N`·`ldap user/group search result
   <코드> …`·`ldap group search exceeded page cap`·URI 연결 오류 — LDAP 원문이라 요청 이벤트·결과엔
   싣지 않는다; stepper 쪽은 `identity recheck failed job=… phase=…: <원인>` 경고 로그). 급증하면
   스위치로는 안 꺼진다(위 표) → 원인 수정 또는 이미지 롤백.
2. 프로젝트 그룹이 있는 대표 사용자로 비 root 요청 1건 → 잡 상세 '보조 그룹(gid)'
   (`/api/user/requests/{rid}/jobs` 의 `worker_pool.identity.supplementary_gids_status`).
   `none` 이면 그 그룹에 objectClass posixGroup 이 없을 가능성부터.
3. 3홉 화면(포탈 관리 → 아티팩트 경로): API·컨트롤러 홉이 g+w/ACL/o+x 로 빨간지 — 775/770 만이
   아니라 **750/710 base 도 이제 빨간불**이다. 잡은 계속 돌지만 base 저장이 막히고 그룹이 실린
   잡은 제출 직전에 종단된다(컨트롤러 정적 관문·preflight) → §2b-1 대로 chmod/setfacl.

**남는 위험**(의도적 범위 밖 — 상세는 BACKLOG):
- **gidNumber 0 LDAP 그룹**은 인정된다(사용자 결정) — 그 멤버의 잡은 root 그룹 권한을 가져 root:root
  770 디렉터리(예: 테스트베드 `/cephfs/dms`)가 열리고, 스토리지 mount_path 아래면 사용자 심링크를
  거쳐 닿을 수 있다. 로그인 노드 SSSD 는 기본(min_id=1, filter_groups=root)으로 gid 0 을 주지 않아
  **DMS 가 로그인 노드보다 넓게 준다**. 완화: 부모 디렉터리 그룹 쓰기 금지(§2b-3), warning 이벤트.
- StartTLS 인증서 검증 안 함(sssd `ldap_tls_reqcert = never` 미러) — LDAP 중간자는 그룹 멤버십도
  주입할 수 있고 재확인도 같은 채널이다.
- NFSv4/GPFS 고유 ACL 은 제어면 base 검사 밖이다. preflight `test -w` 는 base **자체**의 쓰기만
  잡 단위로 막고, 그런 ACL 의 **상속**(inheritable ACE 가 `<job_id>/<phase>` 에 주는 쓰기)은 어디서도
  보지 않는다. POSIX default ACL 은 보조 그룹이 실린 잡만 컨트롤러 관문이 막고, 그룹 없는 비 root 잡은
  잡 단위로 보지 않는다(3홉 화면만 빨갛다 — §2b-1).
- LDAP 사용자가 스스로 그룹에 가입할 수 있는 selfwrite ACL 은 점검하지 않았다.
- 큐 대기 재확인이 LDAP 장애로 건너뛴 사이 탈퇴하고 그대로 RUNNING 이 되면 반영되지 않는다(실행
  중 잡은 재확인하지 않는다). nsync 의 preflight 파드 쌍 대기는 큐 재확인 대상이 아니다.
- dscan/dsync 출력의 그룹 이름이 `dmsg<gid>` 로 보일 수 있다(사소).

## 3. Apply manifests, in order

```bash
kubectl apply -f deploy/k8s/00-namespace.yaml
kubectl apply -f deploy/k8s/05-volcano-queue-priorityclass.yaml
kubectl apply -f deploy/k8s/10-rbac.yaml
kubectl apply -f deploy/k8s/20-config.yaml
```

`20-config.yaml` holds only non-secret config, so it is safe to re-apply on a
running cluster. Never apply `20-secret.example.yaml` — it carries
placeholders, and applying it over a live `dms-secrets` replaces the real
credentials (the api/controller then CrashLoopBackOff on
`password authentication failed for user "dmsapp"`).

## 3b. Create the secret (once, out-of-band)

`dms-secrets` is never committed. Create it directly, with the real DB
password and freshly generated tokens:

```bash
kubectl -n dms create secret generic dms-secrets \
  --from-literal=DMS_DATABASE_URL='postgresql://dmsapp:<DB_PASSWORD>@10.10.10.30:5432/dmsdb' \
  --from-literal=DMS_SHARED_TOKEN="$(openssl rand -hex 24)" \
  --from-literal=DMS_ADMIN_TOKEN="$(openssl rand -hex 24)" \
  --from-literal=DMS_SESSION_SECRET="$(openssl rand -base64 32)" \
  --from-literal=DMS_LDAP_BIND_PW='<LDAP_SEARCH_PASSWORD>'
```

`DMS_SHARED_TOKEN` grants `role=admin` on every API call (`Bearer <token>`),
so generate it — don't copy one from the repo. Startup rejects any value
containing `CHANGE_ME` or `REPLACE_WITH_` (`src/dms/config.py`
`_is_placeholder`), so a skipped or half-filled secret fails loud instead of
coming up with a publicly known admin token.

Read the value back later with:

```bash
kubectl -n dms get secret dms-secrets \
  -o jsonpath='{.data.DMS_SHARED_TOKEN}' | base64 -d; echo
```

## 4. Migrate / 5. Bring up api / controller / agent (testbed overlay)

`deploy/k8s` 의 `image:`·`DMS_JOB_IMAGE` 는 **사이트 중립 자리표시자**
(`set-by-overlay.invalid/<img>:set-by-overlay`, 2026-09-14)다 -- 실 레지스트리·태그는 오버레이가 넣는다.
테스트베드는 `deploy/overlays/testbed`(newTag = 이 테스트베드의 태그, 태그 bump 는 이
파일 커밋), 프로덕션은 `deploy/overlays/prod|ssc`(values.env). **base 를 raw 로
`kubectl apply -f deploy/k8s/…` 하지 마라** -- 자리표시자 이미지라 pull 에서 즉시
실패한다(다른 사이트 값을 조용히 배포하던 사고를 막기 위한 의도된 실패).

```bash
kubectl -n dms delete job dms-migrate --ignore-not-found   # Job 은 불변이라 재실행 전 삭제
sh deploy/overlays/guard-images.sh deploy/overlays/testbed # 렌더 이미지 != 라이브면 거부(포탈 릴리스 되돌림 방지)
kubectl apply -k deploy/overlays/testbed                   # migrate + api + controller + agent (+ config/rbac/ingress)
kubectl wait --for=condition=complete job/dms-migrate -n dms --timeout=120s
kubectl logs job/dms-migrate -n dms   # expect: "migrated"

kubectl -n dms rollout status deployment/dms-api
kubectl -n dms rollout status deployment/dms-controller
kubectl -n dms rollout status daemonset/dms-agent

kubectl -n dms port-forward svc/dms-api 8080:8080 &
curl -sf http://localhost:8080/healthz   # {"status":"ok"}
```

Give the agent DaemonSet 1-2 report cycles (`DMS_AGENT_INTERVAL_SECONDS=30`)
before seeding -- the planner's admission gate needs at least one fresh
`/api/agent/report` per node before any storage/tool/identity reads as
"Ready" (`src/dms/placement.py::eligible_nodes`).

## 6. Seed storages and policies

All admin calls below authenticate with `Authorization: Bearer
$DMS_SHARED_TOKEN` -- `auth.py::current_identity` maps that bearer straight
to `Identity(role="admin")`, no signup/login needed for scripted seeding.

```bash
# Read the live token instead of pasting one -- never commit a real token.
export DMS_SHARED_TOKEN=$(kubectl -n dms get secret dms-secrets \
  -o jsonpath='{.data.DMS_SHARED_TOKEN}' | base64 -d)
export API=http://localhost:8080
AUTH=(-H "Authorization: Bearer $DMS_SHARED_TOKEN" -H "x-dms-actor: seed-script")

# storages (backend_type must be one of cephfs/gpfs/wekafs/lustre/purestorage/netapp
# -- a label only, every backend is mounted on nodes as a plain POSIX path;
# managed_root must be mount_path or a subdirectory of it)
curl -sf -X POST "$API/api/admin/storages" "${AUTH[@]}" -H 'content-type: application/json' -d '{
  "storage_name": "cephfs-dms", "mount_path": "/cephfs",
  "managed_root": "/cephfs/managed", "backend_type": "cephfs"}'

curl -sf -X POST "$API/api/admin/storages" "${AUTH[@]}" -H 'content-type: application/json' -d '{
  "storage_name": "cephfs-third", "mount_path": "/cephfs-third",
  "managed_root": "/cephfs-third/managed", "backend_type": "cephfs"}'

curl -sf -X POST "$API/api/admin/storages" "${AUTH[@]}" -H 'content-type: application/json' -d '{
  "storage_name": "cephfs-secondary", "mount_path": "/cephfs-secondary",
  "managed_root": "/cephfs-secondary/managed", "backend_type": "cephfs"}'

# policies -- tool names per repositories/control.py POLICY_TOOLS =
# ("scan", "dsync", "nsync", "rm")  (note: "dsync", not "sync")
for tool in scan dsync nsync rm; do
  curl -sf -X PUT "$API/api/admin/policies/$tool" "${AUTH[@]}" -H 'content-type: application/json' -d '{
    "max_nodes": 3, "procs_per_node": 2, "queue": "dms-data",
    "default_priority": "mid", "max_priority": "high",
    "execution_timeout_seconds": 3600, "enabled": true}'
done

# 사용자 sync 허용 스토리지 쌍(2026-09-30, d143~) -- **기본 전부 불가**라, 쌍이 하나도 없으면
# 일반 사용자의 sync 는 전부 거부된다(sync_pair_not_allowed; 관리자·배치는 제한 없음). 방향이
# 있다(A->B 와 B->A 는 별개, A->A 도 한 쌍). 포탈 관리 -> 정책 화면의 매트릭스로도 편집한다.
curl -sf -X POST "$API/api/admin/sync-pairs" "${AUTH[@]}" -H 'content-type: application/json' -d '{
  "source_storage": "cephfs-dms", "destination_storage": "cephfs-dms"}'

curl -sf "$API/api/admin/storages" "${AUTH[@]}" | python3 -m json.tool
curl -sf "$API/api/admin/policies" "${AUTH[@]}" | python3 -m json.tool
curl -sf "$API/api/admin/sync-pairs" "${AUTH[@]}" | python3 -m json.tool
```

> **업그레이드 주의(d143, 2026-09-30)**: 이 버전부터 사용자 sync 는 관리자가 허용한 스토리지 쌍
> 안에서만 된다. 기배포 사이트는 업그레이드 직후 허용 쌍이 비어 있어 **일반 사용자의 sync 제출이
> 전부 403 `sync_pair_not_allowed`** 가 된다(이미 계획된 사용자 잡도 컨펌 단계에서 막힌다) --
> 롤아웃 직후 포탈 관리 → 정책의 「사용자 Sync 허용 스토리지 쌍」에서 필요한 조합을 허용하라.
> 롤아웃 시점에 **대기(Pending) 중이던 사용자 sync** 는 새 planner 의 첫 틱에서 `sync_pair_not_allowed`
> 로 종단(Rejected)된다 -- 새 API 가 뜨기 전에는 쌍을 미리 넣을 수 없으니, 쌍을 허용한 뒤 재제출하라.

## 7. Scenarios

Same `AUTH`/`API` env as above. Every request goes through
`planner -> job-stepper` on `dms-controller`'s loops (default
`DMS_PLANNER_INTERVAL_SECONDS=10`, `DMS_STEPPER_INTERVAL_SECONDS=5`) --
poll `GET .../requests/{id}` a few times rather than expecting an instant
terminal state.

### scan (single storage, no confirm step)

`target`은 storage의 `managed_root` 아래 **실재하는** 상대 경로여야 한다 -- 없으면
preflight가 `target_not_readable`로 잡을 Rejected 시킨다(슬라이스 18 실증에서
`managed`가 실재하지 않아 한 번 밟았다). 현재 `cephfs-dms`(`/cephfs/dms`) 아래에
있는 것: `team`, `artifacts`, `s16-verify`, `s17-live` 등 -- `ls`로 먼저 확인할 것.
그리고 `x-dms-actor`는 **LDAP에 있는 사용자**여야 한다(`alice` 등); 임의 문자열이면
플래너가 `ldap_identity_not_found`로 거절한다.

```bash
RID=$(curl -sf -X POST "$API/api/user/requests" "${AUTH[@]}" -H 'content-type: application/json' -d '{
  "operation": "scan", "storage": "cephfs-dms", "target": "team",
  "priority": "mid"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["request_id"])')

for i in $(seq 1 12); do curl -sf "$API/api/user/requests/$RID" "${AUTH[@]}"; echo; sleep 5; done
# state progression: Pending -> Planned -> ... job: Pending -> Preflight -> Running -> Succeeded
kubectl -n dms get pods,vcjob -l dms.io/job-id  # (job_id from the jobs list below)
curl -sf "$API/api/user/requests/$RID/jobs" "${AUTH[@]}" | python3 -m json.tool
```

### sync (same-node candidates -> tool=dsync, needs confirm)

`cephfs-dms` (w1-5) and `cephfs-third` (w1-3) overlap on w1-3, so
`select_tool_and_candidates` (`src/dms/placement.py`) picks co-located nodes
and tool `dsync`:

```bash
RID=$(curl -sf -X POST "$API/api/user/requests" "${AUTH[@]}" -H 'content-type: application/json' -d '{
  "operation": "sync", "source_storage": "cephfs-dms", "source": "managed/scratch",
  "destination_storage": "cephfs-third", "destination": "managed/copy",
  "priority": "mid"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["request_id"])')

# poll until job state == ConfirmPending, then read preview_fingerprint:
JOB=$(curl -sf "$API/api/user/requests/$RID/jobs" "${AUTH[@]}" | python3 -c 'import sys,json;print(json.dumps(json.load(sys.stdin)[0]))')
JOB_ID=$(echo "$JOB" | python3 -c 'import sys,json;print(json.load(sys.stdin)["job_id"])')
FP=$(echo "$JOB" | python3 -c 'import sys,json;print(json.load(sys.stdin)["preview_fingerprint"])')

curl -sf -X POST "$API/api/user/jobs/$JOB_ID:confirm" "${AUTH[@]}" -H 'content-type: application/json' \
  -d "{\"fingerprint\": \"$FP\"}"
# -> Executing -> Succeeded
```

### nsync (disjoint node pools -> tool=nsync, gang-scheduled)

`cephfs-third` (w1-3) and `cephfs-secondary` (w4-5) share NO node, so
`select_tool_and_candidates` falls through to `nsync` with
`candidates={"source": [...w1-3], "destination": [...w4-5]}` -- this is the
`_build_nsync_job` path in `execution_manifests.py` (separate
source-worker/destination-worker Volcano tasks):

```bash
RID=$(curl -sf -X POST "$API/api/user/requests" "${AUTH[@]}" -H 'content-type: application/json' -d '{
  "operation": "sync", "source_storage": "cephfs-third", "source": "managed/scratch",
  "destination_storage": "cephfs-secondary", "destination": "managed/copy",
  "priority": "mid"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["request_id"])')

# same confirm flow as above once ConfirmPending; then:
kubectl -n dms get vcjob -o wide   # expect tasks: launcher, source-worker, destination-worker
```

### cancel

```bash
# while a job is Executing/Running/PreviewRunning:
curl -sf -X POST "$API/api/user/jobs/$JOB_ID:cancel" "${AUTH[@]}"
# -> {"state": "Cancelled"}; verify the Volcano Job/preflight Pod is gone:
kubectl -n dms get vcjob,pods -l "dms.io/job-id=$JOB_ID"
```

## 8. 포탈에서 이미지 빌드 (슬라이스 11)

> **동작한다(슬라이스 21, 2026-08-11 실증).** 이 문서는 한동안 "구조적으로 불가"라고
> 적고 있었다 -- 빌드 노드(=dms 워커)에 인터넷이 없다는 이유였다. 운영 방식이
> 정해지면서 그 전제가 바뀌었다: **빌드할 때만 운영자가 그 워커에 인터넷을 열고**,
> 포탈이 착수 전에 **적합성 프리플라이트**로 실제 개방 여부를 확인한다(§8-7).
> 빌드 `824ce0e2` 가 `pkg-01:5000/dms:b824ce0e2` 를 실제로 push 했다.
>
> §1 의 pkg-01 podman 경로는 **비상용으로 남긴다** -- 클러스터가 없거나 포탈이 죽은
> 상태에서 이미지를 만들어야 할 때만 쓴다. 평상시 빌드는 포탈에서 한다.

**빌드를 제출하기 전에 위 「이미지 빌드 전 게이트」(`cd frontend && npm run test:e2e`)를
돌린다.** 포탈 빌드는 GitHub 에 push 된 커밋을 대상으로 하지만(아래 1번), 그 커밋이
게이트를 통과했는지 확인해 주는 CI 는 없다 — 제출자가 로컬에서 돌리는 것이 전부다.

포탈이 `dms-mpifileutils`/`dms`/`dms-agent` 이미지를 빌드 노드 위 bare Pod로 빌드해
`DMS_BUILD_REGISTRY`(기본 `pkg-01:5000`)로 push하는 기능. `20-config.yaml`의
`DMS_BUILD_*` 4개 키가 이 기능의 설정 전부이고, 빌드 노드 자체는 ConfigMap에 **없다**
(아래 참고).

**0) 빌드 노드와 소스 경로를 먼저 지정한다.** 포탈 「컨트롤 상태」 화면(`PUT
/api/admin/control-state`, `build_node_name`·`build_source_path`)에서 지정하며,
`control_state` 테이블에 저장된다 — ConfigMap이 아니다, 운영자가 포탈에서 언제든
바꾸는 값이라 재적용마다 되돌아가면 안 되기 때문이다. 지정 전에 「빌드」 화면에서
제출하면 API가 `422 build_node_not_set`/`build_source_not_set`으로 거절한다.

**1) 빌드는 빌드 노드의 로컬 소스에서 뜬다(슬라이스 33).** 빌드 파드가
`build_source_path`(예: `/home/mason/dms-dev/dms`)를 hostPath 로 **읽기 전용**
마운트하고, 시작 시점에 tar 스냅샷을 떠 `/src` 에서 빌드한다
(`src/dms/build_manifests.py`). git 연동이 없다 — **커밋·push 하지 않은 작업
트리도 그대로 빌드된다.** 커밋 SHA 는 마운트의 `.git` 에서 읽어 기록하고, 작업
트리에 미커밋 변경이 있으면 `-dirty` 접미를 붙인다(워크트리 경로처럼 SHA 를 읽을
수 없으면 `unknown`). 테스트베드에서 이 경로는 호스트 작업 트리의 NFS ro 마운트다
(`testbed` 저장소 `make storage`) — 실 클러스터에서는 빌드 노드의 로컬 체크아웃을
그대로 지정하면 된다.

**2) 태그는 지정하거나 파생된다(드리프트 방지 내장, 슬라이스 34).** 「빌드」 폼의
(선택) 태그 입력에 관례 태그(예: `d75`)를 지정하면 그 태그로 push 된다. **빌드는
빌드하는 이미지의 동봉 매니페스트(`deploy/k8s`) 태그를 이 빌드 태그로 자동
스탬프**하므로(`build_manifests._SCRIPT`), 그 태그로 배포하면 **live == 동봉
매니페스트가 되어 드리프트 배지가 안 뜬다** — 예전처럼 손으로 `deploy/k8s` 를 먼저
bump 해 빌드하지 않아도 된다. 단 그 태그를 실제로 굴리려면 **사이트 오버레이의 태그**
(테스트베드 `deploy/overlays/testbed/kustomization.yaml` 의 `newTag`, prod/ssc 는
values.env 의 `DMS_TAG`)도 그 태그로 맞춰 `kubectl apply -k` 해야 새 태그가 배포된다
(이미지 안 스탬프는 드리프트 판정용, 오버레이 값은 apply 대상; base 는 자리표시자). 태그를 비우면 `b<build_id 앞 8자>`
(`build_tag()`)가 파생된다 — 이 자동 태그도 스탬프되므로 릴리스 화면으로 굴리면
드리프트가 없지만, 관례 태그(dNN)를 권한다. 주의: `30-migrate-job.yaml`/
`40-api.yaml`/`41-controller.yaml`/`50-agent-daemonset.yaml`이 전부
`imagePullPolicy: IfNotPresent`이므로, **이미 노드에 있는 태그를 다시 push 해도
클러스터는 새로 집어오지 않는다** — 재빌드는 새 태그로.

**2b) 이미지·이력 정리(슬라이스 34).** 「빌드 > 이미지 관리」 화면에서 레지스트리
태그를 열람·삭제한다(`GET/DELETE /api/admin/registry/images`). **사용 중 태그**
(지금 배포돼 도는 또는 매니페스트가 가리키는)는 서버가 409 로 막는다. 삭제는
레지스트리의 태그(매니페스트)만 지운다 — 디스크 블롭 회수(`registry
garbage-collect`)와 노드 pull 캐시(`crictl rmi`)는 별개의 운영자 작업이고, 시간
기반 자동 GC 는 두지 않는다. 레지스트리 삭제가 `405` 면 pkg-01 의 registry 에
`storage.delete.enabled`(env `REGISTRY_STORAGE_DELETE_ENABLED=true`)가 꺼진
것이다. 빌드 이력 행은 「빌드 이력」 화면에서 다중 선택 삭제(종단 빌드만).

**3) 빌드 노드는 인터넷 egress가 필요하다.** 빌드가 hermetic하지 않다 — Buildah
빌드(privileged 컨테이너) 안에서 npm install(포탈 프론트엔드), `dl.k8s.io`(kubectl 등
설치), PyPI(파이썬 의존성), Debian bookworm 미러(apt 패키지), docker.io(베이스
이미지)에 접근한다. **빌드할 때만 운영자가 그 워커에 인터넷을 열면 된다** — 상시
개방이 아니어도 된다.

**필요한 egress 는 전부 빌드 파드 경로다.** 빌더 이미지는
`pkg-01:5000/buildah:stable` **로컬 미러**를 쓰므로(`20-config.yaml`) 노드(kubelet/
CRI-O)는 인터넷 없이도 빌더를 받는다. 슬라이스 21 실증에서 이걸 안 하면 무슨 일이
나는지 확인했다: 프리플라이트 프로브는 **파드 네트워크**로 검사하는데 빌더 이미지
pull 은 **노드 네트워크**로 일어나, 노드 egress 만 막으면 프로브는 통과하고 빌드
파드가 `ImagePullBackOff` 로 앉는다. 미러 갱신은 `20-config.yaml` 주석의 3줄.

**3b) 착수 전에 적합성 프리플라이트가 돈다(슬라이스 21, 슬라이스 33에서 소스 검사
추가).** 제출하면 빌드 노드 위에 단발 프로브 파드(`dms-build-pf-<build_id[:12]>`,
job image 라 인터넷 없이도 뜬다)가 네 가지를 검사하고 실패하면 **수 초~수십 초
안에** 고유 사유 코드로 끝낸다 — 2시간 generic 타임아웃을 기다리지 않는다:
- 소스 경로에 `deploy/docker/Dockerfile.dms` 존재 → `build_source_unavailable`
  (경로 오타·마운트 소실을 빌드 전에 잡는다)
- egress(`quay.io`·`registry-1.docker.io` TCP 443) → `build_node_no_egress`
  (로그에 실패 호스트 전부)
- `pkg-01:5000` 도달 → `build_registry_unreachable`
- 노드 fs 여유(`avail ≥ 0.15·total + 12GiB`) → `build_node_disk_low`(실측 바이트 기록)

실증(슬라이스 21 당시, 소스가 git 이던 시절): 인터넷을 막은 상태로 제출하니 **45초**
만에 `build_node_no_egress` + `unreachable_443=github.com,quay.io,registry-1.docker.io`.
지금 검사 대상은 `quay.io`·`registry-1.docker.io` 둘이다(소스는 로컬이라 빠졌다).

**3b-1) 빌드 노드 프록시(2026-09-08).** 에어갭 사이트에서 빌드 노드만 프록시를
거쳐 인터넷에 닿는 경우, 포탈 **컨트롤 상태** 화면의 `HTTP 프록시`/`HTTPS 프록시`/
`프록시 제외(no_proxy)`(DB `control_state.build_*_proxy`, 재시작 없이 다음 빌드부터)
를 채운다. 값은 빌드·프리플라이트 파드의 `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY`
(+소문자) env 가 되고, 서버가 사내 레지스트리 호스트(포트 유무 둘 다)·`localhost`·
`127.0.0.1` 을 제외 목록에 자동으로 보탠다(push 는 프록시를 타지 않는다). buildah 는
자기 환경의 프록시 env 를 `RUN` 단계 컨테이너와 베이스 이미지 pull 에 그대로
전파하므로(`--http-proxy` 기본 true) npm·pip·apt·curl·git 이 전부 같은 값을 읽는다.
프리플라이트는 프록시가 있으면 egress 를 **프록시 경유 HTTP CONNECT** 로 검사하고
프록시 자체에 못 닿으면 `build_proxy_unreachable` 로 끝낸다. 인증 프록시
(`user:pass@`)는 저장이 거절된다(평문 자격증명을 DB·이력·화면에 두지 않는다 —
프록시 쪽 IP allowlist 로 푼다). 실증(테스트베드, luminous 의 proxy.py 3128):
아래 CHANGELOG 「빌드 프록시」 항목.

**프록시가 빌드 노드의 localhost 에만 있는 경우(ssh -R 리버스 터널, 2026-09-09).**
빌드 파드는 자기 네트워크 네임스페이스를 가져 파드 안의 `127.0.0.1` 은 파드
자신이다 — 빌드 노드 호스트에서 `ssh -R 7227:...` 로 건 터널(sshd 기본
`GatewayPorts no` 라 호스트 loopback 에만 바인드)에는 원리상 닿지 못한다. 그래서
프록시 호스트가 `localhost`/`127.0.0.1`/`::1` 이면 서버가 **자동으로 호스트 네트워크
모드**를 켠다: 빌드·프리플라이트 파드 `hostNetwork: true` + `dnsPolicy:
ClusterFirstWithHostNet` + `buildah bud --network=host`. 셋이 한 스위치인 이유:
파드만 hostNetwork 여도 buildah 의 `RUN` 단계는 기본(`--network=private`)으로
자기 netns 를 또 만들어 거기서 보는 localhost 는 RUN 컨테이너 자신이다. loopback 이
아니지만 호스트에서만 닿는 주소(호스트 전용 인터페이스 등)는 컨트롤 상태의 「빌드
파드 호스트 네트워크」 스위치로 켠다. 노출 표면: 빌드 파드는 원래 privileged 라
늘지 않고, 프로브 파드(비특권)가 호스트 loopback 서비스에 닿게 되는 점만 인지할 것.
대안은 터널을 노드 IP 에 바인드(`GatewayPorts yes` 또는 `-R 10.x.x.x:7227:...`)해
파드 네트워크에서 `http://<노드 IP>:7227` 로 닿게 하는 것 — 프록시 포트가 다른
노드에도 열리는 대신 hostNetwork 가 필요 없다.

**사내 프록시 CA(TLS 가로채기 프록시, 2026-09-09).** 사내 방화벽/프록시가 https 를
가로채 자기 CA 로 재서명하면, 빌드 안의 베이스 이미지 pull·npm·pip·curl·git 이
전부 인증서 오류로 죽는다(`--tls-verify=false` 는 push 전용이라 해결책이 아니다).
컨트롤 상태의 「프록시 CA 파일 경로 (빌드 노드)」에 **빌드 노드 위 PEM 파일의 절대
경로**를 넣으면(파일은 빌드 노드에만 있으면 된다 — API/컨트롤러 파드는 보지 않는다):
- 프리플라이트 프로브가 그 파일의 부모 디렉토리를 hostPath 로 마운트해 존재·PEM 을
  검사하고(`build_proxy_ca_missing`), 프록시 CONNECT 터널 위에서 시스템 CA + 사내 CA
  로 실제 TLS 핸드셰이크를 해 본다(`build_proxy_tls_failed`, 로그에 발급자).
- 빌드 파드는 파일을 `type: File` hostPath 로 `/etc/dms-proxy-ca/ca.crt` 에 싣고,
  스크립트가 **시스템 번들 + 사내 CA** 합본(`/tmp/dms-proxy-ca/bundle.pem`)을 만든다
  — 사내 CA 만 주면 가로채지 않는 사이트에서 진짜 인증서 검증이 깨지기 때문이다.
  buildah 자신(Go)은 `SSL_CERT_FILE`, RUN 단계 컨테이너는 `-v` 마운트 +
  `--env NODE_EXTRA_CA_CERTS/npm_config_cafile/PIP_CERT/REQUESTS_CA_BUNDLE/
  CURL_CA_BUNDLE/GIT_SSL_CAINFO/SSL_CERT_FILE` 로 그 번들을 읽고, `--unsetenv` 로
  최종 이미지에서 걷어낸다(런타임에 없는 경로가 남으면 TLS 전체가 깨진다).
- 인증 프록시(Basic)는 여전히 미지원. 실증: CHANGELOG 「사내 프록시 CA」.

**no_proxy 힌트(2026-09-09).** 컨트롤 상태의 프록시 제외 입력 아래에 이 사이트의
실제 값 — 레지스트리 host / host:port, localhost, 127.0.0.1, .svc, .cluster.local,
워커 노드 InternalIP — 이 「권장 값」으로 뜨고 버튼 하나로 채워진다. 노드 IP 는 API
가 k8s Node 를 읽어 오므로 `10-rbac.yaml` 의 ClusterRole
`dms-api-nodes-readonly` 가 적용돼 있어야 한다(없으면 노드 IP 만 생략하고 그 사실을
표시). 레지스트리·localhost 는 저장 시 서버가 자동으로 보태므로 힌트를 그대로 넣어도
중복은 무해하다.

**3b-2) 신규 사이트의 매니페스트 기준값(2026-09-08).** 이미지에 동봉된
`deploy/k8s` 는 "그 이미지를 만든 소스 트리"의 값이라, 포탈 밖에서 부트스트랩한
이미지는 테스트베드 태그(`pkg-01:5000/dms:d119`)를 담고 있다. 동봉 이미지의
레지스트리가 사이트의 `DMS_BUILD_REGISTRY` 와 다르면 서버가 "모름"으로 접어 드리프트
배지·레지스트리 "사용 중"·빌드 화면 어디에도 남의 태그가 새지 않고, 포탈 빌드의
스탬프는 레지스트리까지 치환하므로 첫 포탈 빌드부터 동봉값이 그 사이트 것이 된다.
빌드 화면의 제안 태그는 라이브·레지스트리·빌드 이력의 dNN 중 최대+1, 하나도 없으면
**d1** 이다.

**3c) 빌드는 데이터 잡과 같은 워커에서 동시에 돈다.** 빌드 노드를 잡 풀에서 빼지
않는다. 빌드 파드는 봉투(cpu 250m/1000m, mem 128Mi/1Gi, eph 10Gi/12Gi)와
PriorityClass `dms-build`(10 < `dms-low` 50)를 달고 돈다 — cpu limit 이 실질
보호막이고(allocatable 1800m 중 최소 800m 이 남는다), 노드가 압박받으면 **빌드가
먼저 죽는다**. 실증: 빌드 중 scan 잡이 평시와 동일한 대기(`sched_wait=5`)로 완료,
잡 파드 축출 0건.

**4) 만들어진 태그를 실제로 쓰려면 매니페스트를 손으로 바꿔 apply해야 한다.**
빌드 성공은 레지스트리에 새 태그를 push하는 것으로 끝난다 — 그 태그로의 자동
롤아웃(매니페스트의 `image:`를 갱신해서 재배포하는 것)은 **이 슬라이스 범위 밖**이다
(다음 슬라이스에서 다룬다). 새로 빌드한 `dms:b<...>`를 실제로 띄우려면, 위 §1의
방식대로 `40-api.yaml`/`41-controller.yaml`/`30-migrate-job.yaml`의 `image:`
필드를 그 태그로 직접 고치고 `kubectl apply`한 뒤 `kubectl rollout restart`(또는
재적용에 따른 자연 롤아웃)로 반영해야 한다. `dms-agent`/`DMS_JOB_IMAGE`는 별도
태그 계열(`dms-agent:dev5`, `dms-mpifileutils:job3`)이라 이 흐름과 독립적이다.

**5) `dms-mpifileutils`는 기본 선택이 아니다.** mpifileutils를 소스에서
`make -j2`로 컴파일하기 때문에 다른 두 이미지보다 훨씬 오래 걸린다 — 「빌드」 화면에서
기본으로 체크돼 있지 않다, 필요할 때(예: mpifileutils 자체를 바꿨을 때)만 명시적으로
포함시킬 것. `dms-agent`는 앞의 두 이미지를 `FROM`하므로, `dms-agent`만 새로 빌드하려면
그 두 태그가 이미 레지스트리에 있어야 한다 — `BUILD_IMAGES` 순서
(`dms-mpifileutils` → `dms` → `dms-agent`, `src/dms/repositories/builds.py`)가 실행
순서를 강제하고, 빌드 스크립트가 `dms-agent`를 빌드할 때 `--build-arg
DMS_IMAGE=$DMS_BUILD_REGISTRY/dms:$DMS_BUILD_TAG`/`MFU_IMAGE=...`로 **이 빌드가 push할
바로 그 태그**를 `FROM`에 고정한다(`src/dms/build_manifests.py`) — 그래서 앞의 둘이 같은
빌드 안에 없거나 그 태그가 레지스트리에 없으면 buildah가 pull에 실패해 시끄럽게 죽는다
(Dockerfile.agent의 `ARG` 기본값 `:dev`로 조용히 폴백하지 않는다).

**6) 동시 빌드는 하나로 제한된다.** 이미 진행 중(`Pending`/`Running`)인 빌드가 있는
채로 `POST /api/admin/builds`를 부르면 `409 build_in_progress`. 새 빌드를 넣기
전에 「빌드 이력」에서 이전 빌드가 종단 상태(`Succeeded`/`Failed`)인지 확인할 것 —
진행 중인 빌드가 있으면 「빌드하기」 화면 위에 배너로도 먼저 알려 준다.

화면: 「빌드」는 하위 페이지 둘이다 — 「빌드하기」(`/admin/builds`, 기본, 제출 폼)와
「빌드 이력」(`/admin/builds/history`, 목록·상태 필터). 제출에 성공하면 이력으로
넘어간다. 빌드 상세(`/admin/builds/:buildId`)에 로그 뷰어가 있고, 빌드 노드 지정은
「컨트롤 상태」 화면.

## 9. 포탈에서 릴리스(롤아웃) (슬라이스 13)

§8의 「빌드」가 레지스트리에 태그를 만드는 데서 끝났다면, 「릴리스」 화면
(`/admin/releases`)은 그 태그를 **실제로 클러스터에 올린다** — 운영자가
`kubectl apply`/`rollout restart` 없이 포탈 안에서 워크로드의 `image:`를 갱신한다.
설정은 `20-config.yaml`의 `DMS_ROLLOUT_INTERVAL_SECONDS`/`DMS_ROLLOUT_TIMEOUT_SECONDS`
둘뿐이고, 레지스트리는 빌드와 같은 `DMS_BUILD_REGISTRY`를 쓴다.

**1) 흐름: 한 배치로 제출하고, 순서는 서버가 강제한다.** 「릴리스」 화면은 세 컴포넌트
(`dms-agent`/`dms-api`/`dms-controller`) 행마다 select를 주고, 올리고 싶은 것만 태그를
골라 **한 번에** 제출한다(`POST /api/admin/releases`). 제출 순서가 무엇이든 서버가
`ROLLOUT_ORDER = ("dms-agent", "dms-api", "dms-controller")`로 정렬해
`releases.seq`에 **DB로 지속**시키고(`src/dms/repositories/releases.py`), 컨트롤러의
RolloutWatcher가 그 seq 순서대로 하나씩 patch → 수렴 확인 → 다음으로 넘어간다. 순서가
행에 박혀 있으므로 배치 중간에 컨트롤러가 죽어도 새 파드가 seq만 보고 이어간다. 동시
롤아웃은 하나로 제한된다 — 진행 중인 배치가 있으면 `409 rollout_in_progress`.

**2) `dms-controller`를 갱신하면 컨트롤러가 자기 자신을 재시작시킨다.** 그래서
`dms-controller`가 배치의 **마지막**이다. 컨트롤러가 자기 Deployment를 patch하는 순간
옛 파드는 종료되고, 「릴리스」 화면의 갱신이 리스 재획득(최대 ~30초 + 파드 기동)만큼
멈춘다 — **장애가 아니다.** 컨트롤러는 patch 전에 행을 `Applying`으로 먼저 기록하므로
(record-then-patch), 새 파드가 그 `Applying` 행을 이어받아 수렴을 확인하고 `Applied`로
닫는다. 화면이 잠시 얼어 있어도 기다릴 것 — 이것이 이 기능의 정상 동작이다.
간격을 늘리면(`DMS_ROLLOUT_INTERVAL_SECONDS`) per-loop 리스가 함께 길어져 이 정지
구간이 몇 배로 늘어난다.

**3) 같은 태그 재롤아웃은 거절된다(`same_tag`).** 현재 워크로드에 걸린 것과 같은 태그를
고르면 `422 same_tag`로 막는다 — 모든 매니페스트가 `imagePullPolicy: IfNotPresent`라
같은 태그를 다시 밀어봐야 파드 스펙이 그대로여서 아무 일도 일어나지 않기 때문이다(§8-2와
같은 함정). 레지스트리에 없는 태그는 `422 unknown_tag`, 모르는 컴포넌트는
`422 unknown_component`.

**4) 롤아웃 성공 후 매니페스트의 `image:`를 손으로 맞춰야 한다(설계 §9).** 정적 YAML이
여전히 **선언적 진실**이다. 롤아웃은 살아 있는 클러스터 오브젝트만 바꾸므로, 파일을
그대로 두면 다음 `kubectl apply -k deploy/overlays/<site>`가 클러스터를 옛 태그로
**되돌린다.** 2026-09-14 부터 `deploy/overlays/guard-images.sh`(install.sh 가 apply 직전에
부른다; 테스트베드는 §4/§5 절차)가 "렌더 이미지 != 라이브" 면 apply 를 거부하므로 조용히
되돌아가지는 않는다 -- 의도한 이미지 변경은 `ALLOW_IMAGE_CHANGE=1`. 성공한 배치마다
사이트 오버레이의 태그를 맞춘다(base `deploy/k8s` 는 자리표시자라 손대지 않는다):

- 테스트베드: `deploy/overlays/testbed/kustomization.yaml` 의 `newTag`(dms 계보 = api·
  controller·migrate 가 한 항목, dms-agent 계보 = 다른 항목) + `patch-config.yaml` 의
  `DMS_JOB_IMAGE`
- prod/ssc: `values.env` 의 `DMS_TAG`/`DMS_AGENT_TAG`/`MFU_TAG`
  - 설치 뒤로는 셋 중 무엇이든 `live` 로 둘 수 있다(2026-10-06): `render.sh` 가 지금 클러스터에서
    도는 태그를 읽어 채운다(DMS_TAG←deploy/dms-api, DMS_AGENT_TAG←ds/dms-agent,
    MFU_TAG←ConfigMap dms-config 의 DMS_JOB_IMAGE). 포탈 릴리스는 `values.env` 를 읽지 않으므로
    평소엔 이 파일을 안 고쳐도 되고, 재-apply(설정 변경 등) 때도 `live` 면 렌더 이미지=라이브라
    가드를 통과해 옛 태그로 되돌리지 않는다. 첫 설치는 라이브 워크로드가 없어 명시 태그를 써야
    한다(render.sh 가 자리표시자/부재를 감지해 거부). MFU `live` 는 ConfigMap(apply 가 쓰는
    부트스트랩 기본값)을 읽는다 -- 포탈의 잡 이미지 릴리스는 DB 를 바꾸고 런타임은 그 DB 값을
    쓰므로, MFU `live` 는 "ConfigMap 을 재-apply 로 되돌리지 않는다"는 뜻이지 런타임 잡 이미지와
    항상 같다는 뜻은 아니다(가드는 워크로드 이미지만 보고 ConfigMap 은 보지 않는다).

이 슬라이스는 파일을 자동으로 고치지 않는다 — 컨트롤러 파드 안에 저장소가 없다.
어긋남을 화면에 표시하는 것은 슬라이스 14 대시보드의 몫이다. Helm/kustomize는 도입하지
않는다 — 이 README에 기록된 설계 결정이다(§1, "Unresolved values" 참고).

**5) 태그 계보 세 개는 서로 독립이고, `DMS_JOB_IMAGE`는 롤아웃 대상이 아니다.**
`dms:`(api/controller/migrate가 공유), `dms-agent:`, `dms-mpifileutils:`는 각각 다른
레지스트리 리포이고 버전이 같이 갈 이유가 없다 — 「릴리스」 화면은 컴포넌트별로 자기
리포의 태그 목록만 보여준다(`COMPONENTS[*].repository`). `DMS_JOB_IMAGE`
(`dms-mpifileutils`)는 **롤아웃 대상이 아니다**: 워크로드 이미지 패치가 아니라 ConfigMap
갱신 + 소비자 재시작이 필요해서 범위 밖이다(설계 §10). 바꾸려면 지금처럼
`20-config.yaml`을 고쳐 apply한다.

**6) RBAC은 세 워크로드로 좁혀져 있다.** 컨트롤러 Role의 apps `get`/`patch`는
`resourceNames: ["dms-api", "dms-controller", "dms-agent"]`로 한정된다
(`10-rbac.yaml`) — 컨트롤러가 네임스페이스의 임의 워크로드를 건드릴 수 없다.
api Role은 같은 세 이름에 **읽기 전용 `get`만** 받는다(「릴리스」 화면이 현재
이미지를 보여주기 위한 것) — patch는 컨트롤러에만 있다. `list`와 `*/status`
규칙은 **두지 않는다**: 코드는 `read_namespaced_deployment`/`_daemon_set`으로
메인 리소스만 읽고(상태는 그 안에 담겨 온다) 어디서도 apps를 list하지 않는다.
특히 `list`는 `resourceNames`를 따르지 않아, 두면 Role이 네임스페이스의 모든
워크로드로 조용히 넓어져 위의 "세 워크로드로 좁혀져 있다"가 사실이 아니게 된다.
새 태그를 쓰기 전에 `10-rbac.yaml`을 apply해야 한다. 확인:

```bash
kubectl --context dms auth can-i patch deployments.apps/dms-controller \
  --as=system:serviceaccount:dms:dms-controller -n dms      # yes
kubectl --context dms auth can-i patch deployments.apps \
  --as=system:serviceaccount:dms:dms-api -n dms             # no
kubectl --context dms auth can-i list deployments.apps \
  --as=system:serviceaccount:dms:dms-controller -n dms      # no (의도적)
```

**7) 없는 태그를 강제로 넣으면 시끄럽게 실패한다.** 레지스트리가 다운이면 태그 검증이
fail-open이라(설계 §7) 존재하지 않는 태그가 통과할 수 있다. 그 경우 파드가
`ImagePullBackOff`에 빠지고, Deployment는 `ProgressDeadlineExceeded`로, DaemonSet은
`DMS_ROLLOUT_TIMEOUT_SECONDS` 벽시계로 `Failed`가 된다(DaemonSet에는
`progressDeadlineSeconds`가 없어 이 값이 유일한 실패 수단이다). 실패하면 배치의 남은
컴포넌트는 `rollout_aborted`로 닫히고 반쯤 섞인 버전 조합이 생기지 않는다 — 복구는
이력에서 옛 태그를 골라 다시 롤아웃하면 된다(별도 롤백 버튼이 없는 이유).

### 실증 체크리스트 (설계 §11 — 테스트베드에서 수행)

- [ ] 1. `GET /api/admin/releases/targets`가 세 컴포넌트의 **현재 이미지**와 레지스트리
      태그 목록을 준다.
- [ ] 2. 현재와 같은 태그 제출이 `same_tag`로 거절된다(`IfNotPresent` 함정).
- [ ] 3. 레지스트리에 없는 태그가 `unknown_tag`로 거절된다.
- [ ] 4. **`dms-agent` 롤아웃**(`dev5` → 새 태그) — DaemonSet 세대 게이트와 4조건 수렴
      판정이 실제로 동작해 `Applied`로 넘어간다. 5노드 순차 롤링이 600초를 정상적으로
      넘긴다면 `DMS_ROLLOUT_TIMEOUT_SECONDS`를 올릴 것(여기서 실측한다).
- [ ] 5. **`dms-api` 롤아웃** — Deployment 조건 기반 판정(세대 게이트 → PDE → 3조건).
- [ ] 6. **`dms-controller` 자기 갱신 — 이 슬라이스의 핵심 실증.** 컨트롤러가 자기
      Deployment를 patch해 죽은 뒤, **새 파드가 `Applying` 행을 이어받아 `Applied`로
      수렴**시킨다. 화면 정지 구간(리스 재획득 ~30초 + 기동)을 실제로 재고, 그 뒤
      배치가 스스로 닫히는지 확인한다.
- [ ] 7. 감사 로그에 `mutation_class=release`가 남는다.
- [ ] 8. 존재하지 않는 태그로 강제 패치 시 `ImagePullBackOff` 후 타임아웃 또는
      `ProgressDeadlineExceeded`로 `Failed`가 된다.

화면: 「릴리스」(`/admin/releases`) — 컴포넌트 3행 + 태그 select, 한 배치 제출, 진행
중에는 폴링, 아래에 롤아웃 이력.

---

## 프로덕션 배포 — kustomize 오버레이

사내 클러스터 배포는 **`deploy/overlays/prod`** 를 쓴다. 테스트베드 매니페스트를
base 로 두고 사이트별 값(레지스트리·태그·공유 FS 경로·LDAP·이메일 도메인·포탈
도메인·VIP)만 오버레이에서 덮는다 -- 자리표시자를 채우고 `kubectl apply -k`.
전제조건·값 표·Secret/TLS 생성·적용 순서는 **`deploy/overlays/prod/README.md`**.
이 §(테스트베드 개별 파일 apply)는 그대로 유효하다 -- 오버레이는 base 를 건드리지
않는다.

---

## 10. 포탈 HTTPS 노출 — nginx ingress + TLS + 공인 VIP(L2) (2026-08-19 완증)

경로: `브라우저 → https://dms.local (공인 VIP 10.20.20.100) → 상위 라우팅이
노드 세그먼트로 배달 → MetalLB L2 가 ARP 응답(선출 노드) → ingress-nginx(TLS
종단, replicas 2) → svc dms-api:8080`. 앱 구조 무변경 — FastAPI 가 지금처럼
SPA+API 를 서빙하고 ingress 는 프록시만 한다.

**BGP 미사용(사용자 결정)**: 이 환경은 상위 라우팅이 공인 VIP 를 노드
세그먼트로 배달하고 마지막 홉이 ARP 로 주인을 찾는다("노드에 VIP 를 얹으면
동작"하는 환경) — MetalLB L2 모드가 정확히 그 ARP 에 응답하므로 VIP 가 노드와
다른 서브넷이어도 BGP 가 필요 없다. BGP 가 필요해지는 유일한 경우(마지막 홉이
next-hop 경로를 요구)의 검증된 구성은 git 이력 c127c1f(47-metallb-bgp.yaml +
luminous FRR)에 있다.

선행 컴포넌트(설치 완료, 이미지는 pkg-01:5000 미러): MetalLB v0.16.0,
ingress-nginx v1.15.1(IngressClass `nginx`).

절차:

1. TLS secret (git 밖 — 개인키). 리허설은 자체 CA, 프로덕션은 사내 PKI 발급분:
   `kubectl -n dms create secret tls dms-portal-tls --cert=tls.crt --key=tls.key`
   (SAN: DNS dms.local + IP 10.20.20.100. 리허설 CA·인증서 사본:
   luminous `~/.claude/jobs/b182a2ed/tmp/tls/`)
2. `kubectl apply -f deploy/k8s/47-metallb-public-pool.yaml` (공인 풀
   autoAssign=false + L2Advertisement) + ingress svc 에 풀 지정:
   `kubectl -n ingress-nginx annotate svc ingress-nginx-controller metallb.io/address-pool=dms-public-pool`

   **공인 존 제약**(2026-08-20, 사용자 확인: 공인 IP 라우팅은 특정 세그먼트
   노드에만 닿는다): 입구만 고정하면 되고 백엔드 파드는 자유 배치다.
   - 노드 라벨: `kubectl label node dms-w1 dms-w2 network-zone=public`
   - VIP 광고 자격: 47 의 `L2Advertisement.nodeSelectors` (비공인 노드가
     선출되면 ARP 를 라우터가 못 들어 VIP 블랙홀 — 구조적으로 차단)
   - ingress 컨트롤러 고정·분산(업스트림 설치본이라 kubectl 패치, 재설치 시 재적용):
     `nodeSelector: network-zone=public` + `topologySpreadConstraints`(hostname,
     maxSkew 1, DoNotSchedule — preferred anti-affinity 는 실측에서 한 노드에
     몰렸다) + `strategy.rollingUpdate: {maxSurge: 0, maxUnavailable: 1}`
     (2노드-2레플리카에서 서지 파드가 낄 자리가 없다)
   - 실증: 3레플리카 확장 시 전부 public 노드로만, w2 차단(cordon)+파드 삭제 시
     대체 파드는 비공인으로 가지 않고 w1 로, 광고자 w2→w1 자동 재선출·무중단.
3. `kubectl apply -f deploy/k8s/46-ingress.yaml` — 어노테이션(20m 바디·300s
   타임아웃)은 그 파일 주석에.
4. 앱: 20-config 의 `DMS_SESSION_COOKIE_SECURE: "true"`(세션 쿠키 Secure) 적용
   후 api 롤아웃. 평문 NodePort 서비스는 제거됐다(구 45-api-nodeport.yaml) —
   자동화·비상 접근은 Bearer 토큰(shared/admin) 또는 https://dms.local.
5. 접속 주소: **IP 직접 https 가 1급 경로다** — Ingress 는 host 무제한
   (catch-all)이고 컨트롤러에 `--default-ssl-certificate=dms/dms-portal-tls`
   (SAN 에 IP 포함)를 지정해 SNI 없는 IP 접속도 정식 인증서를 받는다.
   `force-ssl-redirect` 명시 필수 — catch-all 은 tls 항목과 매칭되지 않아 기본
   ssl-redirect 가 발동하지 않는다(없으면 http 가 조용히 평문 서빙되어 Secure
   쿠키 로그인이 소리 없이 깨진다). dms.local 로 접속하려면 hosts/DNS 에
   `10.20.20.100 dms.local`. (테스트베드에선 luminous 가 상위 라우팅을 모사:
   `ip route replace 10.20.20.100/32 dev dmsbr0` — onlink 라우트라 dmsbr0 에서
   VIP 를 ARP 로 찾는다. 외부 PC·Tailscale 사용자의 옛 주소 호환용으로
   luminous iptables 가 자기 IP 의 80/443/8080 을 VIP 로 DNAT 한다 — 재부팅
   시 재적용 필요, 규칙은 `~/.claude/jobs/b182a2ed/tmp/setup-relay.sh`·
   `relay-tailscale.sh`·`relay-8080.sh`.)
6. 검증(전부 실증됨): `curl --cacert ca.crt https://dms.local/` = 200(검증
   통과), `http://` = 308, 로그인 Set-Cookie 에 `Secure`, 로그인·admin API·SPA
   딥링크 https 로 200, `ip neigh show 10.20.20.100` = 선출 노드 MAC.
   **페일오버 실측**: 광고 노드의 컨트롤러 파드 강제 종료 → 11 초 다운 후 남은
   레플리카 노드로 ARP 재선출·자동 복구(svc 가 externalTrafficPolicy=Local 이라
   선출은 컨트롤러 파드가 있는 노드 중에서만).

7. **웹 인증 하드닝(2026-09-07, d119)** — 20-config 의 세 키가 라이브 자세다:
   - `DMS_PASSWORD_ENCRYPTION_REQUIRED: "true"` — 포탈은 비밀번호를 WebCrypto 로
     봉인해(`password_enc`, ECDH P-256 + HKDF + AES-256-GCM) 보내고 서버는 평문
     `password` 를 422 로 거절한다. 서버 키는 `DMS_SESSION_SECRET` 에서 유도되므로
     시크릿 회전 = 키 회전(포탈이 자동 재수신). **첫 관리자 부트스트랩**(x-admin-token
     curl)만 평문 허용: `curl -H 'x-admin-token: <ADMIN_TOKEN>' -d '{"username":..,
     "password":..}' https://<포탈>/api/admin/accounts`. 자동화가 로그인 세션이
     필요하면 `python -c 'from dms.api.password_transport import seal_with_info'`
     로 봉인해 보낸다(예: `~/.claude/jobs/.../tmp/login-sealed.py`).
   - `DMS_LOGIN_RATE_LIMIT_ATTEMPTS: "10"` / `..._WINDOW_SECONDS: "60"` — 사용자명·
     IP 별 실패 10회/분 초과 시 429 + Retry-After(창이 지나면 자동 해제, 잠금 아님).
     ingress-nginx 가 X-Real-IP 를 채우므로 IP 키가 정확하다. 관찰만 하려면 ATTEMPTS
     를 0 으로.
   - 검증(실증 절차): 평문 로그인 `curl -d '{"username":"u","password":"p"}'
     /api/auth/login` = 422 `password_encryption_required`; 봉인 로그인 = 200 +
     Set-Cookie; 틀린 비밀번호 봉인 10회 후 11번째(정답) = 429; 포탈 화면 로그인
     정상 + 개발자도구 네트워크 탭 본문에 `password` 없음.

---

## 11. 인증 메일 — 사내 Knox 메일 릴레이 (2026-10-01)

회원가입·비밀번호 재설정 인증번호 메일은 **메신저 서버의 릴레이**가 보낸다(웹서버는 Knox Mail API 에 직접
닿지 않는다): `/usr/lib/zabbix/alertscripts/knox_mail_dms_certi.py`(systemd `knox-mail-dms-certi`, root, TCP
8025)와 같은 디렉터리의 `knox_mail_dms_certi.env`. DMS 쪽 구현은 `src/dms/api/mailer.py`.

설정은 **포탈 관리 → 메일 설정**에서 한다(재시작 불필요, DB 가 env 를 이긴다 -- 칸을 비우면 아래 env·기본값):

| 포탈 칸 | env 기본값 | 비고 |
|---|---|---|
| 발송 방식 | `DMS_MAILER_BACKEND` (`stub`) | `stub` = 메일 없이 화면에 코드 표시(개발용 -- **운영 금지**: 아이디만 알면 누구나 가입·재설정), `knox_relay` = 실메일. 운영은 env 를 `knox_relay` 로 두고, 포탈의 stub 전환은 확인 체크 뒤에만 저장된다 |
| 릴레이 서버 IP·포트·프로토콜 | `DMS_MAIL_RELAY_URL` (`http://<메신저 서버 IP>:8025`) | 포트 기본 8025, http |
| 인증 키 | `DMS_MAIL_RELAY_TOKEN` (Secret 권장) | `knox_mail_dms_certi.env` 의 `RELAY_TOKEN` 과 같은 값. 포탈 입력은 봉인 전송·암호화 저장, 다시 볼 수 없음. **키는 주소에 묶인다**: env 키는 주소가 `DMS_MAIL_RELAY_URL` 그대로일 때만 쓰이고(포탈에서 IP·포트·프로토콜 중 하나라도 정하면 포탈에 키를 넣어야 한다), 포탈 키가 있을 때 주소를 바꾸면 같은 저장에서 키를 다시 입력해야 한다 |
| 타임아웃(초) | `DMS_MAIL_RELAY_TIMEOUT_SECONDS` (20) | 릴레이의 KNOX_CONNECT_TIMEOUT_SECONDS(3)+KNOX_TIMEOUT_SECONDS(10) 보다 길게 |
| 서비스명 | `DMS_MAIL_SERVICE_NAME` (`Supercom 포털`) | 메일 제목·본문 표시 |
| (읽기 전용) 받는 도메인 | `DMS_ACCOUNT_EMAIL_DOMAIN` | 받는 주소 = `<아이디>@<도메인>` |

릴레이 쪽 준비(메신저 서버):
- `RELAY_ALLOWED_CLIENTS` 에는 **릴레이가 보는 DMS 의 접속 IP** 를 넣는다 -- 파드 송신은 보통 노드 IP 로 SNAT
  되므로 dms-api 파드가 뜰 수 있는 노드 IP 들이다(비우면 토큰만 검사).
- `RELAY_ALLOWED_DOMAINS` 에 받는 도메인(`DMS_ACCOUNT_EMAIL_DOMAIN`)이 있어야 한다.
- 연결 확인: **웹서버(DMS 노드)에서** `curl -s http://<메신저 서버 IP>:8025/healthz` → `{"ok": true}`. 포탈의
  「연결 확인」(GET /healthz)·「테스트 메일」(POST /send, 키·허용 IP·도메인까지) 버튼이 같은 경로를 dms-api 파드에서
  확인한다(실패하면 사유와 조치가 화면에 나온다 -- `mailer.MailerError` 표). 릴레이 호출은 프록시를 타지 않고
  리다이렉트를 따라가지 않는다(키가 다른 주소로 새지 않게 -- 3xx 는 `relay_http_3xx` 실패). 포트를 잘못 넣어
  SSH·SMTP 같은 다른 서비스가 답하면 `relay_bad_response`(인증 메일 502), 요청은 갔는데 응답 전에 타임아웃·끊김이면
  `relay_no_response`(메일이 갔을 수 있어 코드를 저장하고 화면은 "발송 확인이 늦어지고 있다").

포탈 메일 설정의 저장·연결 확인·테스트 메일은 **세션으로 로그인한 관리자만** 된다(공유 토큰·API 토큰은 조회만,
403 `mail_settings_session_required`). 여러 관리자가 동시에 고치다가, 키를 저장할 때 화면이 보던 릴레이 주소가 그
사이 바뀌었으면 409 `mail_settings_changed` 로 거절된다 -- 화면이 설정을 자동으로 다시 불러오고 입력한 키는 칸에
남으니, 바뀐 주소를 확인한 뒤 다시 저장한다.

포탈에 저장한 키는 `DMS_SESSION_SECRET` 에서 파생한 키로 암호화돼 있어 **세션 시크릿을 바꾸면 포탈 키가 열리지
않는다** -- 화면에 「읽을 수 없습니다」가 뜨고 knox_relay 발송은 `relay_misconfigured` 로 실패하니, 시크릿 교체
뒤에는 메일 설정에서 키를 다시 입력한다.

운영 신호: 인증 메일마다 events 에 `verification_email_sent` / `_failed`(사유) / `_uncertain`(요청은 갔는데 응답을
못 받음 -- 코드는 저장, 화면은 "발송 확인이 늦어지고 있다") 가 남고, `_throttled` 는 같은 상한·키에 10분에 한 번만
남는다(인증번호는 어디에도 없다). 사용자 화면은 실패 시 502 `verification_email_failed`(인증번호는 발송 성공 뒤에만 저장하므로 실패가 메일함의
이전 코드를 죽이지 않는다), 상한 초과 시 429 `verification_rate_limited`(Retry-After) -- 수신자별 5통/10분, 클라이언트
IP 별 20통/10분, 전체 300통/10분, 동시 발송 8건(모두 api 프로세스 메모리라 레플리카 수만큼 늘어난다; 거절된 요청은
어느 상한도 쓰지 않는다). 릴레이 자체의 `RELAY_RATE_LIMIT` 은 그와 별개로 걸린다.

**테스트베드 검증(사내 릴레이 없음)**: `tests/fake_knox_relay.py` 가 같은 계약을 흉내 낸다 --
`python3 tests/fake_knox_relay.py --host 0.0.0.0 --port 8025 --token <T> --outbox <DIR>` 로 띄우고 포탈에서
**발송 방식을 `knox_relay` 로 바꾸고** IP·포트·키를 그 값으로 넣으면, 보낸 메일이 `<DIR>/NNNN.html` 로 떨어진다
(가입 흐름 끝까지 실검증 가능 -- 테스트 메일 버튼은 발송 방식과 무관하지만 가입·재설정 인증 메일은 knox_relay 일
때만 릴레이로 간다). 검증이 끝나면 발송 방식을 다시 비우거나 `stub` 으로 되돌린다(가짜 릴레이가 꺼지면 knox_relay
는 502 가 된다).

**인증번호 무차별 대입 상한**: 코드당 5회 틀리면 그 코드가 죽고, (아이디, 용도)별로 첫 실패부터 24시간 동안
누적 10회 틀리면 그 창이 끝날 때까지 인증번호 발급·확인이 모두 429 `verification_locked`(Retry-After)다 --
재발급으로 시도 횟수를 초기화하는 공격(4자리를 하루 ~30% 확률로 맞힘)을 막는다. 로그인은 영향이 없고 잠금은
창이 끝나면 저절로 풀린다(포탈에 해제 버튼은 없다 -- 급하면 운영자가 DB 테이블 `verification_failures` 의 그
(username, purpose) 행을 지운다; 성공한 확인도 그 행을 지운다). 남의 아이디로 10번 틀려 그 사람의 재설정·가입을
하루 막을 수 있다는 것이 이 상한의 대가다(이벤트 `verification_locked` 로 보인다).
또 **같은 접속 IP** 의 틀린 인증번호는 24시간에 20회까지다(여러 아이디에 나눠 던지는 추측 차단) -- 넘으면 그 IP 의
가입·재설정 확인이 429 `verification_client_locked`(이벤트 `verification_client_locked` 에 IP). api 프로세스
메모리라 재시작하면 풀리고, 공용 프록시 뒤의 사용자들은 함께 막힐 수 있다.

## 12. 작업(요청) 삭제 — 정리 상태·지연 대응 (2026-10-08)

관리자가 포탈 **작업 목록**에서 끝난 작업(종단 요청)을 골라 지운다(세션으로 로그인한 관리자만 — 공유 토큰은 403
`admin_session_required`; 진행 중인 작업은 선택 불가, 서버도 다시 거부한다). 배치 항목의 작업은 하나씩은 못 지우고
**배치 단위**로만 지운다(아래 「배치 단위 삭제」). 삭제는 두 단계다:

1. **DB — 즉시**(api, `POST /api/admin/requests:delete`): 요청·결과·plan·잡·상태 이력·진단 이벤트·사용량 요약을 한
   트랜잭션으로 지우고, 같은 트랜잭션에서 감사 1행(`audit_log` 'request'/'delete' — 누가·언제·무엇을, 컨펌·취소 actor·
   root 여부·실행 신원 스냅숏)과 **정리 아웃박스** `request_purges` 1행(잡 id·ref·삭제 시점 artifact base)을 남긴다.
   커밋 순간 목록·상세·아티팩트·로그·사용량·지표에서 사라진다. api 는 파일·k8s 를 만지지 않는다. 요청마다 자기
   트랜잭션이라 한 항목의 DB 오류(교착·연결 끊김)는 그 항목만 `request_delete_failed` 로 제외되고(변경 없음 — 다시
   시도) 나머지는 계속된다 — api 로그 `request delete failed for <id>`.
2. **파일·파드 — 비동기**(controller `request-purge` 루프, 기본 15초): 아웃박스 행마다
   `k8s`(기록된 ref + 라벨 `dms.io/job-id` Pod·vcjob + launcher 를 지우고 **Terminating 포함 0 개**가 될 때까지 기다림)
   → `files`(컨트롤러가 `<base>/<job_id>` 를 `<base>/.dms-trash/<job_id>` 로 rename — 지우지는 않는다)
   → `purging`(단명 **purge 파드**가 trash 를 비움) → 끝(행 삭제 + 이벤트 `request_purged`).
   스토리지의 실제 데이터(복사·삭제된 파일)는 어느 단계도 건드리지 않는다.

**정리 상태 확인** — 포탈 작업 목록 툴바의 「결과 파일·파드 정리 중 N건 · 지연 M건」과 그 아래 「정리 지연: 사유」 줄, 또는(조회라 공유 토큰도 된다):

```bash
curl -sf "$API/api/admin/request-purges" "${AUTH[@]}" | python3 -m json.tool
# {"pending": 대기 행 수, "stalled": last_error 가 있는 행 수, "oldest_requested_at": ...,
#  "items": [{"request_id", "stage": k8s|files|purging, "attempts", "last_error", "requested_at",
#             "requested_by", "next_attempt_at"}]}   # 오래된 순 최대 50건
kubectl -n dms get pods -l dms.io/purge=1 -o wide          # purge 파드(언제나 최대 1개, dms-artifact-purge-<hash>)
kubectl -n dms logs deploy/dms-controller -c controller | grep request-purge   # 예기치 못한 예외(stderr)
```

정상이면 삭제 후 1~3분 안에 `pending` 이 0 이 된다. 실패는 포기하지 않고 백오프(15초 × 2^n, 상한 900초)로 계속
재시도하며 `last_error` 로 보인다. 이벤트(전부 `request_id` NULL, component `request-purge` — id 는 payload)는
`purge_failed`(사유가 바뀔 때만)·`purge_ref_rejected`·`artifact_left_at_old_base`·`artifact_reappeared`·`request_purged`.

| `last_error` | 뜻 | 조치 |
|---|---|---|
| `purge_waiting_pods` | 삭제 10분이 지나도 그 잡의 Pod·vcjob 이 남아 있다(대개 Terminating) | 아래 「Terminating 파드」. 파일 단계로 넘어가지 않는 것이 정상(안전 > 진행) |
| `purge_k8s_failed` | k8s 나열·삭제 실패(API 서버·RBAC·Volcano CRD 부재) | `kubectl -n dms auth can-i delete pods --as=system:serviceaccount:dms:dms-controller`, vcjob CRD 확인 |
| `purge_base_unavailable` | base 를 열 수 없다(마운트 빠짐) 또는 삭제 시점 base 가 비어 있었다 | 컨트롤러 파드의 `/cephfs` 마운트·artifact base 화면 3홉 확인 |
| `purge_base_unsafe` | base 가 root 소유가 아니거나 g+w/o+w, 또는 `.dms-trash` 가 심링크·0700 아님·다른 FS | §2b 전제(base root:root 755)로 되돌린다. `.dms-trash` 는 root 0700 디렉터리여야 한다 |
| `artifact_dir_unexpected` | `<base>/<job_id>` 가 root 소유 디렉터리가 아니다(심링크·파일·남의 소유) — §2b 규칙 5 위반 신호 | 그 경로를 운영자가 직접 확인(누가 만들었나). DMS 는 건드리지 않는다 |
| `purge_detach_failed` | rename 실패(일시 FS 오류) | 자동 재시도. 계속되면 컨트롤러 로그 |
| `purge_no_node` | purge 파드를 올릴 노드가 없다 — 신선한 에이전트 보고가 **지금 base 를 exists·writable** 로 확인한 노드 중 배치 제외·cordon·스케줄 불가가 아닌 노드 | 노드 화면(에이전트 보고)·노드 배치 제외 목록 확인. 제외를 풀면 다음 틱에 진행 |
| `purge_pod_failed` | purge 파드 수준의 실패 — 이미지 pull 실패, Pending 10분 초과, 파드 생성 실패, 또는 끝났는데 실린 이름을 **하나도** 못 지웠다(그 요청의 항목 탓이라는 근거가 없다). 그 파드에 실린 요청 전부가 이 표시를 받고, 백오프 뒤 **다시 한 파드에 함께** 실린다 | `kubectl -n dms describe pod -l dms.io/purge=1`(지워지기 전 — 다음 틱에 지운다), 이미지(잡 이미지 `DMS_JOB_IMAGE`/포탈 job-image) pull 가능 여부, purge 파드를 올릴 노드의 `<base>` 마운트 |
| `purge_entry_failed` | 같은 purge 파드의 다른 이름은 지워졌는데 **이 요청의** trash 항목만 남았다(스크립트는 실패해도 다음 이름으로 가므로 시도했는데 못 지운 것), 또는 데드라인·축출로 끊길 때 지우던 항목이다. 끊긴 지점 뒤의 이름(시도조차 못 함)은 `purge_pod_failed` 로 묶음에 남는다. 이 요청은 다음부터 **혼자** 실린다 — 묶어 실을 다른 요청이 없을 때(due 가 된 지 5분이 넘으면 먼저) | 노드에서 `<base>/.dms-trash/<job_id>` 를 본다: EIO 같은 FS 손상, 그 아래 다른 장치 마운트(`--one-file-system` 이 넘지 않는다), 1시간 데드라인을 넘는 거대 트리(재시도마다 줄어 결국 끝난다). 지울 수 없는 원인이면 운영자가 고친 뒤 다음 재시도가 마무리한다 |
| `purge_pod_stuck` | purge 파드가 생성 후 1시간 10분(데드라인 3600초 + 유예 600초)이 지나도 끝나지 않는다 — 노드가 죽어 Running 으로 남았거나 Terminating 에 갇혔거나, rm 이 멈춘 마운트에서 D 상태라 kubelet 이 데드라인을 집행하지 못했다. 그 뒤의 모든 결과 파일 정리가 줄을 서 있다(파드는 언제나 1개 — 두 번째를 띄우지 않는다, DMS 는 이 파드를 지우지 않는다) | `kubectl -n dms get pods -l dms.io/purge=1 -o wide` 로 노드를 보고 `kubectl get node <node>`. 노드가 NotReady 로 확정되고 복구되지 않을 때만 `kubectl -n dms delete pod <name> --force --grace-period=0`(아래 「Terminating 파드」 — 살아 있는 노드의 rm 을 떼면 무엇이 남는지 모른다). 노드가 살아 있으면 그 노드의 `<base>` 마운트(멈춘 CephFS)부터. 파드가 사라지면 다음 틱에 표시가 거둬지고 새 파드가 이어서 비운다 |
| `purge_target_still_present` | 지운 요청·잡 id 가 DB 에 **다시** 있다(DB 복원·변조) — 정리는 k8s·파일을 전혀 건드리지 않는다 | DB 를 확인한다. 되살아난 행이 맞으면 그 아웃박스 행을 운영자가 지운다(정리하지 않음). 다시 지우려면 포탈에서 다시 삭제 |
| `purge_row_invalid` | 아웃박스 행 모양이 깨졌다(jobs JSON·job id 형식·단계·base) — 아무것도 하지 않는다 | 행을 운영자가 확인(변조 의심). 고칠 수 없으면 행을 지우고 남은 디렉터리는 운영자 판단 |
| `purge_failed` | 예기치 못한 예외 | 컨트롤러 로그의 `request-purge error on <id>` |

**Terminating 파드 강제 삭제는 노드가 정말 죽었을 때만.** 살아 있는(또는 분할된) 노드의 파드를 `--force
--grace-period=0` 으로 지우면 API 객체만 사라지고 컨테이너는 계속 돌 수 있다 — 그 launcher 가 정리 **뒤에**
`<base>/<job_id>/<phase>` 를 다시 만들면 행 없는 디렉터리가 남는다(정리 중이면 `artifact_reappeared` 로 k8s 단계부터
다시 한다). 노드가 NotReady 로 확정되고 복구되지 않을 때만:
`kubectl -n dms delete pod <name> --force --grace-period=0`.

**`<base>/.dms-trash`**(root 0700) — 컨트롤러가 떼어낸 `<job_id>` 디렉터리가 purge 파드를 기다리는 곳이다.
**손으로 지우지 마라**: 진행 판정이 이 디렉터리(FS)에서 나온다 — 비어 있어야 할 때 비어 있으면 그만이지만, `.dms-trash`
자체를 지우거나 심링크로 바꾸면 `purge_base_unsafe` 로 정리가 멈춘다. 아웃박스 행이 없는데 trash 에 남은 항목(드묾 —
행을 운영자가 지웠거나 옛 base)은 DMS 가 지우지 않는다(「DB 에 없는 디렉터리 = 고아」 추론 금지) — 운영자 판단으로
root 가 직접 지운다. 같은 이유로 base 아래 행 없는 `<job_id>` 도 DMS 는 지우지 않는다.

**옛 base 의 사본은 남는다.** 삭제 시점 base 가 지금과 다르거나(정리 대기 중 base 를 force 로 바꿈) 잡이 다른 base 에
썼으면 그 경로는 열지도 지우지도 않고 `artifact_left_at_old_base` 경고(경로 표시)만 남긴다(사용자 결정). 정리 대기가
있으면 artifact base 화면의 잠금 수에 포함돼 force 없이는 base 가 바뀌지 않는다.

**정리 대기 중 이미지 롤백 금지.** 이 기능 이전 이미지의 컨트롤러는 `request_purges` 를 모른다 — 롤백하면 대기 행의
파드·결과 파일이 그대로 남는다(다시 올리면 재개). 롤백이 필요하면 `pending` 이 0 인지 먼저 본다. drain 중에도 정리는
멈춘다(새 purge 파드를 띄우지 않는다).

**purge 파드의 권한**(`purge_runner.build_purge_pod`): root + capabilities `DAC_OVERRIDE`·`FOWNER` 만(나머지 drop),
base 볼륨 하나(`/dms-artifact-base`, 스토리지 무마운트), SA 토큰 없음, 읽기 전용 루트 fs, 고정 스크립트에 job id 만
positional 인자(32자 hex 가 아니면 아무것도 지우지 않고 exit 2), `rm -rf --one-file-system`(이름 하나가 실패해도 뒤 이름은
끝까지 시도하고 마지막에 exit 1 — 지울 수 없는 항목 하나가 같이 실린 요청을 세우지 않는다. 앞선 파드가 못 지운 요청은
다음부터 혼자 파드에 실린다), activeDeadlineSeconds 3600. 파드·vcjob 은 **전체 job id**(라벨 `dms.io/job-id`, launcher 는
소유 vcjob 의 라벨·uid)로 확인한 것만 지운다 — 이름은 id 앞 12자만 담는다.
네임스페이스 PSA privileged 전제(두 cap 은 baseline 허용). RBAC 변경 없음(컨트롤러 Role 의 pods·jobs create/delete).

**배치 단위 삭제**(2026-10-10). 작업 목록의 「배치」 열(관리자만)이 행마다 배치 이름(상세 링크)을 보이고, 배치 항목
작업의 체크박스는 **배치 전체** 토글이다 — 그 배치의 작업 전부(재실행·재스캔 이력 포함, 화면에 안 보이는 것까지)와 배치
기록(항목 목록·이름·메모·실행 설정)이 한 트랜잭션으로 함께 지워진다(전부 아니면 전무, 같은 `POST
/api/admin/requests:delete` 의 `batches`). 작업마다 위 1·2 단계가 그대로 일어나고(아웃박스 행·감사 행이 작업마다), 감사에
배치 1행(`audit_log` 'batch'/'delete' — 배치 이름·옵션·실행 신원·항목·자식 id)이 더해진다.
- 고를 수 있는 배치: 완료·취소된 배치이고 끝나지 않은 작업이 0 이어야 한다(확인 대기·실행 중이면 배치 상세에서 먼저 취소).
  고를 수 없으면 배치 칸 둘째 줄에 이유가 보인다(「배치 진행 중」·「미완료 작업 있음」(그 배치의 다른 작업이 아직 안
  끝났다)·「삭제 상한 초과」(아래 상한 — 마우스를 올리면 긴 말)). 배치가 취소됐는데
  작업이 남아 있으면(`request_not_deletable` + 그 작업 id) 그 작업의 상세에서 취소한 뒤 다시 지운다.
- 오래된 배치의 작업 찾기: 배치 상세 머리줄의 「전체 작업에서 이 배치의 작업 보기」(또는 「기록 없음」 칸)가 그 배치의 작업만
  거른 목록(`/jobs?batch=<id>`, API `GET /api/user/requests?batch_id=`)을 연다 — 재실행 이력까지 한자리에 보인다.
- 상한: 한 번에 배치 10개(`delete_batch_selection_too_large`), 배치 하나에 작업 1000개·항목 10000개(`batch_delete_too_large`
  — 배치 하나가 한 트랜잭션이라 그동안 api 가 멈춘다; 넘는 배치를 지우는 청크 삭제는 BACKLOG). 확인 창을 연 뒤 재실행 등으로
  작업 수가 바뀌었으면 `batch_changed`(무변화 — 목록을 다시 보고 다시 시도).
- 큰 배치의 파일·파드 정리는 오래 걸린다 — 정리 루프는 틱(15초)마다 20행을 진행하고, 파드가 남은 작업은 지운 파드가 끝났는지
  한 번 더 봐야 해서(작업마다 k8s 스윕 두 번) 작업 1000개면 **30분 안팎**(가짜 k8s 실측 27분), 한 번에 고를 수 있는 최대(배치
  10개 × 1000)면 4~5시간이다. 그동안 다른 삭제의 정리도 같은 줄에 선다. 툴바 「정리 중 N건」이 줄어드는 것으로 본다. 줄을
  서 기다린 시간은 지연이 아니다 — 「지연」(`purge_waiting_pods`)은 그 작업의 첫 k8s 스윕부터 10분이 지나도 파드가 남을
  때만이다.
- 배치 화면의 「배치 삭제」(`DELETE /api/admin/batches/{id}`)는 그대로 **배치 기록만** 지운다(작업은 남는다). 그렇게 남은
  작업은 작업 목록 배치 열에 「기록 없음 xxxxxxxxxxxx」(배치 id 앞 12자 — 누르면 그 묶음만 거른 목록)로 보이고, 같은
  방식(배치 단위)으로 지운다. 배치 행도 작업도 없이 항목만 남은 묶음(옛 경합·롤링 업데이트 잔재 — 화면엔 안 보인다)은
  `POST /api/admin/requests:delete` 에 `{"batches":[{"batch_id":"<id>","expected_request_count":0}]}` 로 지운다.
- 다른 배치의 항목이 이 배치의 작업을 가리키는 비정상 DB 면 `batch_child_shared`(무변화) — 그 항목을 가진 배치를 먼저
  지운다. 두 배치를 한 번에 함께 고르면 서버가 그 배치를 지운 뒤 이 배치를 다시 시도하므로 선택 순서와 무관하게 둘 다
  지워진다.

**env(둘 다 `deploy/k8s/20-config.yaml`, 기본값과 같은 값):**

| env | 기본 | 소비자 | 뜻 |
|---|---|---|---|
| `DMS_REQUEST_DELETE_QUIET_SECONDS` | 60 | api | 요청·잡의 마지막 갱신 후 이 초가 지나야 지울 수 있다(`request_recently_finished`). 음수는 기동 거부, 0 은 e2e 전용 |
| `DMS_REQUEST_PURGE_INTERVAL_SECONDS` | 15 | controller | request-purge 루프 간격 = 실패 백오프의 기준 단위. 1 미만은 기동 거부 |

마이그레이션은 자동이다(`migrate` 가 `request_purges` 테이블과 `idx_data_jobs_request`·`idx_plans_request`·
`idx_batch_items_request` 인덱스를 만든다). 배치 단위 삭제(2026-10-11)는 `idx_requests_batch(batch_id)` 를
`idx_requests_batch_order(batch_id, commit_order)` 로 바꾼다(새 것을 만든 뒤 옛 것을 지운다 — 한 배치만 거른 목록이 큰
테이블에서 전체를 훑지 않게). 새 인덱스를 만드는 동안 `requests` 쓰기가 기다린다(테이블 크기에 비례 — 다른 인덱스
추가와 같은 성질).

## Unresolved values to fill in during live validation

- **`DMS_LDAP_BIND_PW`** (Secret `dms-secrets`, shape in
  `deploy/k8s/20-secret.example.yaml`): the LDAP search-account password
  (2026-08-23: auth bind is the default -- the DN lives in the ConfigMap as
  `DMS_LDAP_BIND_DN`, testbed account `search_dms` provisioned by
  testbed/roles/openldap_server, password `ldap_search_password` in the
  testbed repo's gitignored `secrets.yml` -- group_vars holds only a
  CHANGEME marker since 2026-08-30). With
  `DMS_LDAP_REQUIRE_AUTH_BIND="true"` a missing/placeholder
  password fails LOUD at startup (SettingsError -> CrashLoopBackOff), so
  inject the secret before applying config. A wrong (but present) password
  fails soft at plan time (`IdentityUnavailable`), fixable without
  redeploying anything else.
- **`DMS_ALLOW_PRIVILEGED_REQUESTERS` / `DMS_PRIVILEGED_REQUESTERS`**
  (`deploy/k8s/20-config.yaml`, ConfigMap): **default is `true` /
  `root,admin`** (also the code default in `src/dms/config.py`). Being in the
  list is **eligibility only** (2026-09-30 incident: an admin's sync with
  "실행 신원 = a user" ran as root and rewrote another user's 700 destination
  to the source owner). A single request runs as **uid 0/gid 0 (root)** --
  skipping the LDAP node-identity check -- only when the body says
  `run_as_root: true` AND the authenticated requester is eligible (admin +
  session auth + in the list; otherwise 403 `privileged_not_authorized`). An
  omitted or false `run_as_root` is non-root on the server. The "admins run as
  root by default" rule lives in the **portal**: for an eligible admin the
  'root 권한으로 실행' checkbox starts ticked, EXCEPT when 실행 신원
  (`owner_username`) names another user -- then it starts unticked and the job
  runs as that user's LDAP uid/gid (the incident path); the portal always sends
  the resolved value explicitly for admins. `GET /api/auth/me` returns
  `can_run_as_root` so the portal only offers/defaults root to eligible admins.
  API scripts that want root must send `run_as_root: true` over a session.
  Batch children keep "root if the batch creator is eligible". The stepper
  re-checks every root job against its request before each submission and
  fails it closed (`privilege_not_requested`) if neither `run_as_root` nor a
  batch is behind it (jobs planned before the rule change). The gate keys on
  the authenticated `requester_id`, NOT the client-supplied `owner_username`,
  so a normal user cannot escalate (owner_username != actor -> 403
  `privileged_not_authorized`). Token-authenticated requests are never
  root -- use session-based actors in production, and keep the allowlist
  minimal. To disable entirely: `DMS_ALLOW_PRIVILEGED_REQUESTERS: "false"`
  (or `DMS_PRIVILEGED_REQUESTERS: ""` -- an explicit empty string overrides
  the default to no privileged requesters).
- **`DMS_JOB_IMAGE` / image tags**: manifests hardcode tags across three
  independent lineages -- `pkg-01:5000/dms:<tag>` (api/controller/migrate,
  currently `d23`), `pkg-01:5000/dms-agent:<tag>` (agent DaemonSet, `dev5`),
  `pkg-01:5000/dms-mpifileutils:<tag>` (`DMS_JOB_IMAGE` ConfigMap value,
  `job3`). Keep `build-and-push.sh`'s `TAG` and the manifests' tags in sync
  manually (no templating layer here by design -- the old "legacy/install/
  미러 금지" rule, from the since-removed legacy tree's CLAUDE.md, ruled out
  introducing e.g. Helm/kustomize for this pass). Portal-driven rollout (§9) patches the live
  `dms:` and `dms-agent:` workloads but does NOT rewrite these files, so after
  a rollout you must hand-edit the tag here to keep repo and cluster aligned.
- **`/cephfs` scheduling assumption on `dms-api`/`dms-controller`**: both
  Deployments hostPath-mount `/cephfs` with `type: Directory` (fails pod
  admission on a node without that mount). This is safe today because the
  only schedulable nodes are w1-5, all of which mount `cephfs-dms`. If a
  non-cephfs node joins the schedulable pool, add a nodeSelector/affinity or
  relax to `DirectoryOrCreate`.
- **DaemonSet node heterogeneity**: `cephfs-third` volume is
  `DirectoryOrCreate` so the agent pod still starts on w4-5 (which never had
  `/cephfs-third`) -- it just shadows an empty dir there, and
  `probe_mounts()` correctly reports that storage `Missing` on those nodes.
  Confirm this reads as expected once agents are actually running.
- **Agent os-metrics network figures**: `probe_os_metrics()` reads the HOST
  network namespace's counters, so `network_rx_bytes`/`network_tx_bytes` are
  real node throughput. The DaemonSet bind-mounts `/proc/1/net/dev` (PID 1 =
  host netns) as a hostPath `type: File` at `/host/proc/1/net/dev` and points
  the probe there with `DMS_AGENT_NET_DEV_PATH` -- the same injection
  convention `DMS_AGENT_MOUNTINFO_PATH` already uses. A plain `/host/proc`
  directory mount would NOT fix it: `/proc/net/*` reflects the *reader's*
  netns, so it has to be the PID 1 file.
  **Do not adopt `hostNetwork: true`** as an alternative (design §2.5 rejected
  it): without a matching `dnsPolicy: ClusterFirstWithHostNet` the agent pod
  loses cluster DNS, can no longer resolve `dms-api`, and stops reporting
  **permanently and silently** -- the report loop is fail-soft, so nothing
  surfaces the breakage. It also widens the agent's network exposure for no
  gain over the bind mount.
- **Agent os-metrics counts PHYSICAL interfaces only** (design §2.6): summing
  every non-`lo` interface in the host netns double-counts cross-node pod
  traffic (`cilium_vxlan` *and* `eth0` see the same bytes) and mixes in pod
  veth host ends (`lxc*`). Detection is by kernel registration site, not by
  name -- the kernel registers virtual interfaces under
  `/sys/devices/virtual/net/<name>`, so an interface in `/proc/net/dev` with
  no directory there is physical. Prefix blocklists (`lxc*`/`cilium_*`/
  `cali*`/...) differ per CNI and fail silently. The DaemonSet mounts that
  directory read-only as hostPath `type: Directory` at
  `/host/sys/devices/virtual/net` and points the probe at it with
  `DMS_AGENT_VIRTUAL_NET_PATH`.
  **`DMS_AGENT_VIRTUAL_NET_PATH` defaults to UNSET on purpose -- never set it
  to the in-container `/sys/devices/virtual/net`.** A pod has that path too,
  but what it holds is the *pod netns's* virtual interfaces. Pointing the
  probe there filters the **host's** interface list through a set from a
  **different namespace** -- the two were never comparable, so **any** host
  interface whose name collides with a pod-side one is misjudged virtual and
  dropped. `eth0` is merely where that collision is most likely (CNIs name the
  pod interface `eth0` by convention, and VM/cloud hosts often name the
  physical NIC `eth0` too), but a host on `ens192`/`enp5s0`/`eno1` breaks the
  same way if the name collides. Unset (or an unreadable path) means no
  filtering: the probe keeps today's all-but-`lo` sum rather than losing the
  metric.
