# DMS testbed deployment runbook

> **프로덕션 설치는 이 파일(716줄, 테스트베드 런북)을 읽지 마세요.**
> `deploy/overlays/prod/README.md` **하나**만 보면 됩니다 — 프리플라이트 →
> S0(노드 IaC) → `values.env` 채우기 → `deploy/install.sh --context <ctx> --plan` → `deploy/install.sh --context <ctx>`(S4 뒤 인터넷 노드 push →
> `--stage S5` 로 재개) → S13 체크리스트. **`--context` 는 필수**입니다(`install.sh`·`preflight-cluster.sh`·`verify.sh` — 2026-11-03 ⑦).
> 이 문서는 테스트베드(개별 파일 apply)·시나리오·HTTPS 원리의 상세 참고용입니다. 설치기의 단계 표는 §2h, 단계가 자동으로 하는 일의 손 판
> (조사·복구용)은 §2d(공용 디렉터리)·§2e(레지스트리·이미지 반입)·§2f(DB)·§2g(입구)입니다.

Deploys the clean-slate DMS control plane (`dms api` / `dms controller` /
`dms agent`, execution backend = Volcano) onto the testbeds. All
manifests here are written against the **new** CLI/config (`src/dms/cli.py`,
`src/dms/config.py`). (The old implementation's `legacy/install/` was removed
from the tree 2026-08-30 -- see git history if needed.)

### 테스트베드는 둘이다 (2026-10-21)

같은 호스트(luminous)·같은 dmsnet(10.10.10.0/24)·같은 pkg-01(레지스트리 VM·PostgreSQL·Ceph)을 쓰는 클러스터 두 개다.
**네임스페이스 이름이 둘 다 `dms` 라 kubeconfig 컨텍스트가 유일한 구분이다 — kubectl 은 늘 `--context` 를 명시한다.**
current-context 는 `dms`(ssc)로 둔다. 이 런북의 §0~§12 는 ssc 테스트베드 기준이고(컨텍스트 없는 예시 = `dms`),
k8s 테스트베드는 §13 이 런북이다.

> **이 체크아웃(dms-k8s)에는 ssc 오버레이(`deploy/overlays/testbed`)와 LDAP 코드가 없다**(2026-10-26 LDAP 코드 삭제 —
> k8s 신원 모드 전용, `docs/ARCHITECTURE.md` §3 불변식 12). §0~§12 의 ssc 전용 절차(컨텍스트 `dms` 예시·LDAP·보조 그룹)는
> dms-ssc 체크아웃의 이 파일이 진실이다 — 여기 남은 ssc 문구는 두 테스트베드의 공용 사실(인프라·경로)을 위한 참고다.

| | **ssc 테스트베드** | **k8s 테스트베드** (§13) |
|---|---|---|
| 검증하는 브랜치 | dms-ssc(후보) — 빌드 소스 전환 전에는 main | dms-k8s(2026-10-22 분리 뒤 직접 개발) |
| 노드 | dms-cp1 .10 + dms-w1~w3 .11–.13 | k8s-cp1 .20(Ubuntu 24.04, 4GiB — 2026-11-03 ⑥ T8 에 2.5 → 4: EG·GW API CRD 로 kube-apiserver RSS ≈1GB, §2g-6) + k8s-w1·k8s-w2 .21·.22(**Rocky Linux 9.6, SELinux permissive**, firewalld 꺼짐, 3GiB — 2026-11-03 T-SE, 설계 #49) |
| kube 컨텍스트 | `dms` (current) | `dmsk8s` (current 로 만들지 않는다) |
| 오버레이 | `deploy/overlays/testbed`(dms-ssc 에만) | `deploy/overlays/testbed-k8s` |
| 포탈 | https://dms.local (VIP 10.20.20.100) | https://dms-k8s.local (VIP 10.20.20.101 → ingress-nginx). Envoy Gateway 가 **병행**(cp1 :443, PROXY 필수 — 2026-11-03 ⑥ T8, §2g-7) |
| 레지스트리 | `pkg-01:5000` (docker `registry`) | `10.112.0.50:5000` — 클러스터 내(④, 2026-11-03~ 앱·빌더 `buildah:stable` 모두, §2e-6). 그 전 `pkg-01:5001`(docker `registry-k8s`, 빌더만 :5000 공용) — 되돌림용으로 둔다 |
| 태그 관례 | `dN` | `dN` — d1 부터(2026-10-26; 그 전 k0~k4 는 옛 kN, 첫 설치 시드는 `d0`) |
| PostgreSQL | `dmsdb` / `dmsapp` | CNPG Cluster `dms-pg`(DB `dms`/사용자 `dms` — ⑤, 2026-11-03 T7~, §2f-8). 그 전 pkg-01 `dmsdb_k8s` / `dmsapp_k8s` — 되돌림용으로 둔다 |
| LDAP | 사용(pkg-01 slapd, 아래 표) | **없음**(사용자 결정) — k8s 신원 모드(모든 잡 root + 권한 = RoleBinding 맵핑, §13) |
| `dms-secrets` | 5키(+ `DMS_LDAP_BIND_PW`) | **4키**(바인드 비밀번호 키 없음) |
| CephFS | dmsfs:/ → `/cephfs`, `/third` → `/cephfs-third`(w1·w2), `/secondary` → `/cephfs-secondary`(w3) | dmsfs:/k8s → `/cephfs`, `/k8s/third` → `/cephfs-third`(k8s-w1), `/k8s/secondary` → `/cephfs-secondary`(k8s-w2) |
| 아티팩트 base | `file:///cephfs/dms/artifacts`(CephFS, 아래 표) | `file:///dms_shared/dms/artifacts` — 공용 디렉터리 `SHARED_DIR=/dms_shared`(호스트 NFS `10.10.10.1:/data/dms_shared`, 3노드, §2d; 2026-11-03 ③ d18 T3 전환. 옛 base `/cephfs/dms/artifacts` 는 보존) |
| 빌드 노드 / `build_source_path` | dms-w1 / `/home/mason/dms-dev/dms-ssc`(전환 뒤; 전 `/home/mason/dms-dev/dms`) | k8s-w1 / `/home/mason/dms-dev/dms-k8s`(origin/dms-k8s 체크아웃, cron 이 ff) |

경로 문자열(`/cephfs`, 스토리지 mount_path)은 두 클러스터가 같다 — k8s 쪽은 CephFS 의 `/k8s` 하위 트리를
같은 이름에 마운트하므로 스토리지 시드를 바꿀 필요가 없다. **artifact base 는 다르다**(2026-11-03 ③): k8s 테스트베드의 DMS 상태는
3노드 모두가 마운트한 공용 디렉터리 `/dms_shared` 아래(`file:///dms_shared/dms/artifacts`, §2d)이고, 데이터 root `/cephfs/managed`·
픽스처는 그대로 CephFS 다. 인프라(VM·CephFS 마운트·레지스트리·DB·호스트 라우트·TLS CA)는
testbed 저장소(`~/dms-dev/testbed`) 몫이다.

Testbed facts baked into these assets — ssc 테스트베드 (see repo `CLAUDE.md` for the source of
truth):

| thing | value |
|---|---|
| build/registry node | `pkg-01` (10.10.10.30) |
| registry | `pkg-01:5000`, insecure (k8s 테스트베드는 클러스터 내 `10.112.0.50:5000` — 2026-11-03~, 그 전 `pkg-01:5001`) |
| PostgreSQL | `postgresql://dmsapp:<PW>@10.10.10.30:5432/dmsdb` — PW 는 testbed `group_vars/all.yml` 의 `postgresql_app_password`(테스트베드 전용 데모값, 인라인). testbed `secrets.yml`(gitignored)엔 k8s 테스트베드의 `postgresql_app_password_k8s` 만 있다 |
| LDAP | `ldap://10.10.10.30:389`, base `dc=dms,dc=local` (ssc 전용 — k8s 테스트베드는 LDAP 없음) |
| CephFS `cephfs-dms` | `/cephfs`, mounted on w1-3 |
| CephFS `cephfs-third` | `/cephfs-third`, mounted on w1-2 |
| CephFS `cephfs-secondary` | `/cephfs-secondary`, mounted on w3 |
| artifact base | `file:///cephfs/dms/artifacts` (k8s 테스트베드는 `file:///dms_shared/dms/artifacts` — §2d) |
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

레지스트리 자체는 **testbed 저장소가 만든다**(2026-10-21 — `registry_server` 롤, `cd ~/dms-dev/testbed && make registry`).
`deploy/docker/registry-setup.sh registry` 는 더 이상 레지스트리를 만들지 않고 거부(exit 2)한다 — 예전 판은 podman·이름 볼륨·
삭제 꺼짐으로 라이브와 **다른** 레지스트리를 만들었다(포탈 「이미지 관리」 삭제가 405). 라이브 모양:

| 컨테이너 | 포트 | 데이터 | 쓰는 클러스터 |
|---|---|---|---|
| `registry` | 5000 | `/opt/dms-registry` | ssc 테스트베드(`dms`) + 공용 `buildah:stable`·애드온 미러 |
| `registry-k8s` | 5001 | `/opt/dms-registry-k8s` | k8s 테스트베드(`dmsk8s`) |

둘 다 docker `registry:2`, `--restart=always`, `REGISTRY_STORAGE_DELETE_ENABLED=true`. 노드 CRI-O 신뢰
(`/etc/containers/registries.conf.d/99-pkg-01-insecure.conf`)도 testbed 의 crio 롤이 깔고, 손으로 고칠 때만 이 저장소의
헬퍼를 쓴다 — 클러스터는 **반드시 명시**한다(기본 컨텍스트 없음):

```bash
./deploy/docker/registry-setup.sh nodes --context dms      # ssc 노드: pkg-01:5000
./deploy/docker/registry-setup.sh nodes --context dmsk8s   # k8s 노드: pkg-01:5000 + pkg-01:5001
#   SSH_USER=ubuntu SSH_OPTS="-i ~/dms-dev/testbed/files/id_ed25519" 로 접속 옵션, NODES/REGISTRIES 로 대상 고정
```

Verify: 노드에서 `sudo crictl pull pkg-01:5000/buildah:stable`(k8s 노드는 `pkg-01:5001/dms:d0` 도)이
"http: server gave HTTP response to HTTPS client" 없이 받아져야 한다. 레지스트리 경로 접두(`pkg-01:5000/k8s`)는 코드가
지원하지 않는다 — 클러스터마다 포트(프로세스)를 나눈 이유다(docs/BACKLOG.md).

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

위 `pkg-01:5000` 은 **테스트베드 예**다 — 사이트에서는 인터넷(빌드) 노드에서 `REGISTRY=<레지스트리 host:port> TAG=<태그>` 로 돌린다
(설치기 S5 PAUSE 가 렌더의 실제 값으로 이 줄을 보인다). **태그는 values.env 의 `DMS_TAG`·`DMS_AGENT_TAG`·`MFU_TAG` 와 같게** 둔다 — agent 는
같은 `TAG` 의 dms·dms-mpifileutils 위에서 빌드되므로 첫 부트스트랩은 세 값을 같게 두는 것이 쉽다. **base 매니페스트(`deploy/k8s/*`)의
`image:`·`DMS_JOB_IMAGE` 는 사이트 중립 자리표시자다 — 고치지 않는다**(2026-11-03 ⑦ 리뷰 1: 예전 이 절은 base 를 고치라고 했다. base 를 실값으로
바꾸면 오버레이의 `images: name: set-by-overlay.invalid/dms` 치환이 조용히 빠지고, 저장소를 받은 다른 사이트가 그 값을 배포한다 — CLAUDE.md
「base 매니페스트는 사이트 중립」). 태그는 오버레이(prod 는 `values.env`, 테스트베드는 `newTag`)가 넣는다.

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
                     # cephfs-dms/cephfs-third/cephfs-secondary on w1-3/w1-2/w3
                     # (k8s 테스트베드는 testbed 의 dmsk8s 타깃: /k8s 하위 트리를 같은 경로에)
```

Then create the shared-FS layout the manifests/seed step below assume exist.
On the testbed this is **codified** (idempotent Ansible, uses the testbed's
inventory; the testbed's own `make ceph` only mounts CephFS). 클러스터는
`-e dms_cluster=<그룹 접두>` 로 고른다(기본 `dms`):

```bash
# ssc 테스트베드(dms_workers[0] 등)
ANSIBLE_CONFIG=~/dms-dev/testbed/ansible.cfg ansible-playbook \
  -e ansible_ssh_private_key_file=$HOME/dms-dev/testbed/files/id_ed25519 \
  deploy/testbed/dms-shared-fs.yml
# k8s 테스트베드(dmsk8s_workers[0] 등 — k8s-w1 이 /k8s 하위 트리 안에 만든다; LDAP 픽스처 없음)
ANSIBLE_CONFIG=~/dms-dev/testbed/ansible.cfg ansible-playbook \
  -e ansible_ssh_private_key_file=$HOME/dms-dev/testbed/files/id_ed25519 \
  -e dms_cluster=dmsk8s deploy/testbed/dms-shared-fs.yml
# 대상만 보려면 끝에 --list-hosts(접속하지 않는다)
```

which produces (see the playbook header for the rationale, §2b for the rules):

```bash
# by hand, on any node with /cephfs mounted (root):
mkdir -p /cephfs/dms/artifacts /cephfs/managed
chown root:root /cephfs/dms /cephfs/dms/artifacts /cephfs/managed
chmod 770 /cephfs/dms            # DMS common dir -- requesters must NOT traverse it (§2b-3)
                                 #   (testbed only; elsewhere 750 or 700 -- never 711/755, §2b-3)
chmod 755 /cephfs/dms/artifacts  # artifact base -- 3-hop rejects o+w / non-root owner (§2b-1)
chmod 755 /cephfs/managed        # data root (managed_root of cephfs-dms) -- OUTSIDE the common dir
mkdir -p /cephfs-third/managed /cephfs-secondary/managed   # only with `make cephfs-nsync`
```

The playbook also lays down the LDAP e2e fixtures under `/cephfs/managed/ldap-e2e`
(alice/bob/cocoa.song private/shared trees, growth files) that the portal
verification scripts use. k8s 테스트베드(LDAP 없음)에서는 LDAP uid/gid 소유 트리를 만들지 않고 root 소유분
(growth·victim-protected·dest)만 `/cephfs/managed/e2e` 에 둔다.

(`managed_root` for each registered storage below lives under its
`mount_path` -- `StoragesRepository._validate` in
`src/dms/repositories/storages.py` requires `managed_root == mount_path` or
a subdirectory of it. `dms-controller`/`dms-api` need `/cephfs/dms/artifacts`
readable -- see `DMS_ARTIFACT_BASE_URI`.)

**k8s 테스트베드(2026-11-03 ③)**: 이 플레이북의 `/cephfs/dms`·`/cephfs/dms/artifacts` 는 dmsk8s 에서 **옛 base**(T3 보존 창,
§2d-5)다 — 지금 base 는 공용 디렉터리 `/dms_shared/dms/artifacts` 이고, 그 트리는 플레이북이 아니라 S0
(`sh deploy/shared-dir-init.sh /dms_shared`, §2d-2)가 만든다. 데이터 root `/cephfs/managed`·`/cephfs/managed/e2e` 픽스처는 여전히
이 플레이북 몫이다. `<SHARED_DIR>/dms` 자신·상위·하위는 스토리지로 등록할 수 없다(422 `storage_overlaps_dms_state`) — `cephfs-dms`
(`/cephfs`)는 `/dms_shared` 와 겹치지 않는다.

## 2b. 제어면 root 전제 (2026-09-09)

`dms-api`·`dms-controller` 컨테이너는 **uid 0** 으로 돈다(`40-api.yaml`·
`41-controller.yaml` 컨테이너 securityContext: capabilities 전부 drop, 이미지 fs
읽기 전용; `migrate` initContainer·`30-migrate-job` 은 이미지 USER 65532 그대로).
운영 아티팩트 base(`/cephfs/dms/artifacts`)가 `root:root` 라 65532 로는 3홉 쓰기
왕복 검증이 항상 실패했기 때문이다. 이 결정에 따라오는 **배포 전제** 네 가지:

1. **base 와 `<base>/<job_id>` 는 root:root · 요청자 쓰기 불가(비-world-writable ·
   비-group-writable · POSIX ACL 쓰기 항목/default ACL 없음), 단 base 는 other 실행(x)
   필수** (`chmod 755` 또는 `711`; **700/710/750/770 · g+w · ACL 금지** — 2026-09-30 감사
   정정(예전 "권장 700" 은 틀렸다) + 2026-10-07). 러너가 `<job_id>` 를 root 로 만들고 `<phase>` 만 실행 신원에게
   chown 한다. dms-k8s 는 모든 잡이 root 라(2026-10-26 LDAP 코드 삭제 — 보조 그룹·비 root 실행 경로 없음) 잡 안의
   도구가 이 권한에 막히지는 않는다. 그래도 base 가 쓰기 가능하면 공유 FS 를 마운트한 다른 호스트의 사용자가
   `<job_id>` 를 미리 만들어 봉쇄 기준을 옮기거나 `<phase>` 의 rank.sh 를 바꿔치기할 수 있어 제어면 판정은 그대로다.
   **강제 위치**:
   - **제어면**(3홉 API·컨트롤러 홉 + base 저장 PUT/validate 422,
     `artifact_base.roundtrip_artifact_base`): 소유자가 제어면 euid(root)가 아니면
     `artifact_base_not_owned`, `o+w` 면 `artifact_base_world_writable`(sticky 여도),
     `g+w`(gid 무관)·POSIX ACL 의 named 쓰기 항목·default ACL 이 있으면
     `artifact_base_group_writable`, other-x 가 없으면 `artifact_base_not_traversable`.
     **예전 이 문서가 허용하던 750/710(및 770) base 도 이제 거부된다** — 저장이 막히고 3홉
     화면이 빨갛다. 이 판정 자체는 표시·저장용이라 잡 제출을 막지 않는다.
   - (2026-10-07~25 의 잡 단위 관문 — 보조 그룹이 실린 비 root 잡의 컨트롤러 정적 관문 `artifact_base_unsafe_at_step`·
     preflight 의 other-x 비트·`test -w`(`artifact_base_group_writable`) — 는 비 root 실행 경로와 함께 지웠다.) 제어면 판정은
     base 자체만 보므로 **NFSv4/GPFS 상속 ACE 는 남는 위험**이다(그런 스토리지에 base 를 두면 상속 ACE 가 없는지 직접
     확인할 것 — 러너가 root 로 만드는 `<job_id>/<phase>` 가 그것을 물려받는다).
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
   통과는 금지 — `750` 또는 `700`**(2026-09-09 d129 에 "770 까지" 로
   시작, 2026-10-07 정정, 2026-10-08 리뷰로 711 제거). **요청자는 이 디렉터리를
   통과하지 못해야 한다** — other-x(`711`·`755`)를 주면 모든 uid 가 base(755)를 지나 0755 잡
   디렉터리와 0644 아티팩트(stdout/stderr 로그·rank.sh·summary.json·dscan 리포트 — 남의 데이터
   경로·파일 통계)를 직접 읽어 API 의 소유자 검사(`artifact_files.open_artifact_fd`)를 우회한다
   (711/755 는 base **자체**의 선택지다, 위 1). 제어면·에이전트·러너(launcher)·잡 도구는 root 라 통과하고, 잡 파드는
   base 를 **전용 hostPath 볼륨**(`/dms-artifact-base`, `artifact_base.ARTIFACT_MOUNT`)으로 받아 그 부모를 지나가지
   않는다. 테스트베드 `/cephfs/dms` 는 770 그대로다(dms-k8s 에는 잡이
   그룹 권한으로 도는 경로가 없다 — 750 으로 조일지는 사용자 결정 사항). k8s 테스트베드의 지금 공용 디렉터리
   `/dms_shared/dms` 는 S0 가 만든 `750` 이다(2026-11-03 ③, §2d — S0 는 이미 있는 `dms` 의 750·700 만 받아들이고 770 은 FAIL 로
   알린다).
   단 **스토리지의 `managed_root`(사용자 데이터 root)는 공용 디렉터리 아래에
   두지 마라** -- 비 root 잡이 있던 시절 preflight 가 요청자 uid 로 `test -r/-w` 해 `source_not_readable`
   류로 거부됐다(dms-k8s 의 root 잡은 막히지 않지만 레이아웃은 그대로 둔다; 테스트베드는 §6 시드대로 `cephfs-dms` 의 managed_root 가
   `/cephfs/managed` 여야 한다; `/cephfs/dms` 로 드리프트해 있던 것을 d129 실증에서
   되돌렸다). 실증: `/cephfs/dms` 770 + 데이터 `/cephfs/managed` 에서 alice sync Succeeded.
4. **`DMS_ARTIFACT_BASE_ALLOWED_PREFIXES`**(`20-config.yaml` — base 는 자리표시자 `/set-by-overlay.invalid/dms/artifacts`,
   오버레이 `replacements` 가 `DMS_ARTIFACT_BASE_URI` 에서 `<SHARED_DIR>/dms/artifacts` 로 낸다, 2026-11-03 ③ §2d) — 포탈에서
   고를 수 있는 base 를 api/controller 가 실제로 붙인 경로(같은 값의 hostPath) 아래로
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
deploy/overlays/<site>` 를 한 번 반드시 거친다(이미지 드리프트 배지는 이미지만 비교한다 — 2026-10-26 부터 릴리스 화면의
「보안 설정 드리프트」 배지가 api·controller 의 securityContext 어긋남을 따로 보인다, §9-8).

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

## 2c. 보조 그룹(LDAP gidNumber) — dms-k8s 에는 없다 (2026-10-07 → 2026-10-26 삭제)

LDAP 보조 그룹(계획 시점 스냅숏·큐 대기 재확인·`DMS_IDENTITY_SUPPLEMENTARY_GROUPS` 스위치·preflight/워커 그룹 물질화)은
2026-10-26 LDAP 코드 삭제(사용자 결정 (d))로 이 라인에서 지웠다 — 운영 절차·스위치 범위표·스토리지별 주의는 dms-ssc 의
이 절이 진실이다. dms-k8s 에 남은 것: 보조 gid 목록이 실린 잡 행(옛 스냅숏·변조)은 제출 전에 `identity_missing_at_step`
으로 끊기고(빌더도 거부 — 조용히 버리고 돌리지 않는다), 옛 사유 코드(`identity_groups_not_applied`·`identity_changed_at_step`·
`ldap_unavailable` 등)는 옛 행 표시용으로 포탈 문구에만 남는다. 옛 values.env 의 `IDENTITY_SUPPLEMENTARY_GROUPS`·`LDAP_*` 는
`render.sh` 가 무시한다(알림 한 줄).

## 2d. 공용 디렉터리(SHARED_DIR) — 배치·S0·S7·전환 런북 (2026-11-03 ③)

설계 v6(`docs/history/specs/2026-11-03-dms-k8s-prod-design.md`) 3-1·3-2·8-1 「S0·S7 세부」·10 「T3 세부」. 이 절은 dms-k8s
공통(prod·testbed-k8s)이고 테스트베드 판은 2d-5 다.

DMS 상태(아티팩트 — 이후 묶음 ④⑤ 의 레지스트리·PG·백업도)는 사이트가 **모든 마스터·워커의 같은 호스트 경로**에 `/etc/fstab` 으로
미리 마운트한 공용 스토리지(종류 무관) `<SHARED_DIR>` 아래 `dms/` 에 둔다. DMS 는 마운트·프로비저닝하지 않고 FS 종류·마운트 옵션으로
판정하지도 않는다. api·controller 는 `<SHARED_DIR>/dms/artifacts` **하나만** hostPath(`type: Directory`)로 같은 경로에 붙이고, env
base·allowlist 도 같은 값이다 — base 매니페스트는 자리표시자(`/set-by-overlay.invalid/dms/artifacts`)뿐이고 오버레이 `replacements` 가
`DMS_ARTIFACT_BASE_URI` 하나에서 셋을 낸다(`docs/ARCHITECTURE.md` §6 「아티팩트 경로의 원천은 하나다」, `tests/test_shared_dir_contract.py`).
원천 값: prod = `values.env` 의 `SHARED_DIR`(render.sh — `deploy/overlays/prod/README.md`), testbed-k8s = `patch-config.yaml` 의
`DMS_ARTIFACT_BASE_URI`(커밋된 `values.env` 의 `SHARED_DIR=/dms_shared` 는 같은 값의 기록 — 둘이 어긋나면 테스트가 빨간불).
형식(render.sh·S0·S7 공통): 절대 경로, `/` 아님, 정규화(`//`·`/./`·`/../`·끝 `/` 없음), 문자 `^/[A-Za-z0-9._/-]+$`.

### 2d-1. 트리 (설계 v6 3-1)

| 경로(`<SHARED_DIR>/…`) | 소유:모드 | 쓰는 주체 | 비고 |
|---|---|---|---|
| (`<SHARED_DIR>` 자신) | 사이트 | — | 마운트포인트 또는 그 아래. DMS 가 만들지 않는다 |
| `dms` | root:root 0750(이미 있으면 0700 도) | — | 공용 디렉터리 — 요청자 통과 금지(§2b-3, 711·755 금지). 센티널 `.dms-shared-id`(무작위 32 hex) |
| `dms/artifacts` | root:root 0755(이미 있으면 0711 도) | api·controller·잡(uid 0) | 아티팩트 base = allowlist = api/controller hostPath(§2b-1) |
| `dms/pgdata` | root:root 0700 | (⑤ DB) | `.retired`(root 0700), `dms-pgdata-01..05`(26:26 0700 + `.dms-pv-id` = PV 이름) |
| `dms/backup` · `dms/backup/pg` | root:root 0750 · 26:26 0700 | (⑤ pg_dump) | |
| `dms/registry` | 65532:65532 0750 | (④ 레지스트리) | 센티널 `.dms-registry-id`(= `.dms-shared-id`) |

- pgdata·backup·registry 는 ④⑤ 전에는 빈 디렉터리다(S0 가 미리 만든다).
- 모든 hostPath 는 `<SHARED_DIR>/dms/…` **하위**를 가리키고 `type: Directory` 다 — 마운트가 빠진 노드에는 그 하위 경로가 없어 파드가
  뜨지 않는다(fail-closed, G2). 옛 base 처럼 마운트포인트 자체(`/cephfs`)를 붙이거나 `DirectoryOrCreate` 로 「완화」하면 그 노드에서 빈
  로컬 디렉터리로 통과해 조용히 로컬 디스크에 쓴다 — 하지 마라(deploy 아래 `DirectoryOrCreate` 0건을 테스트가 고정).
- `<SHARED_DIR>/dms` 자신·상위(SHARED_DIR 포함)·하위는 DMS 스토리지(`mount_path`·`managed_root`)로 등록할 수 없다(422
  `storage_overlaps_dms_state` — 생성·경로 변경 때). 이미 겹친 행은 포탈 「아티팩트 경로」 화면의 「DMS 상태 디렉터리」 아래 경고로 보인다.
  데이터 스토리지와 같은 파일시스템이면 SHARED_DIR 를 그 스토리지의 `mount_path` 밖(별도 마운트포인트)에 둔다.

### 2d-2. S0 초기화 — `deploy/shared-dir-init.sh`

```bash
sh deploy/shared-dir-init.sh [--check] <SHARED_DIR>     # 한 노드에서 root 로(공용이라 한 번이면 모든 노드에 보인다)
```

- **가드**(하나라도 어긋나면 아무것도 만들지 않는다): 형식 · root · `[ -d <SHARED_DIR> ]` · `findmnt -n -o TARGET -T <SHARED_DIR>` 가
  `/` 가 아님(아니면 「마운트 없음」). SOURCE·FSTYPE·OPTIONS 는 보고만 한다.
- SHARED_DIR·상위는 만들지 않는다(`mkdir -p`·`install -d` 없음). 트리는 위에서 아래로 한 단계씩 만들고 상위에서 물려받은 setgid 를
  지운다. 센티널은 noclobber 로 쓴다(두 노드에서 동시에 돌려도 하나만 쓴다).
- **이미 있으면 검사만 하고 고치지 않는다** — 공용 경로라 chmod·chown 이 전 노드에 번진다. 어긋나면 FAIL 로 무엇이 다른지 알리고
  운영자가 확인해 손으로 고친다. 심링크는 따라가지 않고 FAIL, 레지스트리 센티널만 있고 `.dms-shared-id` 가 없으면 추측하지 않고 FAIL.
- `--check`: 만들지 않고 검사만(없는 것도 FAIL) — 다른 노드에서 같은 결과·같은 센티널 값인지 볼 때.
- 종료: 0 PASS · 1 FAIL(가드 거부·모양 어긋남) · 2 사용법·환경(인자·형식·root 아님) · 4 모름(findmnt 없음·무응답). 마지막 줄 `RESULT:`.
- 실행한 노드 하나의 마운트만 본다 — 모든 노드는 S7 이 본다. root 특권을 주지 않는 export(root_squash)면 chown 이 거부돼 FAIL 이다.

### 2d-3. S7 동작 프로브 — `deploy/shared-storage-probe.py`

```bash
python3 deploy/shared-storage-probe.py --context <ctx> --shared-dir <SHARED_DIR> --image <REGISTRY>/dms:<DMS_TAG>
python3 deploy/shared-storage-probe.py --context <ctx> --overlay deploy/overlays/<site>   # 렌더에서 dms 이미지, values.env 에서 SHARED_DIR
#   선택: --dry-run(계획·파드 매니페스트만) --nodes a,b --json --g3-rounds 20 --retry-seconds 60 --start-timeout 180
```

- `--context`(또는 `KUBE_CONTEXT`) 필수 — current-context 에 기대지 않는다. SHARED_DIR 는 `--shared-dir` > env `SHARED_DIR` >
  `--overlay` 디렉터리의 `values.env` 순이다(render.sh 는 반대로 셸의 `SHARED_DIR` 를 무시한다 — values.env 만 원천).
- **파드 모양**: 설계의 「임시 DS」는 **노드별 일회용 Pod** 로 구현했다 — nodeName 고정·toleration Exists(control-plane 포함)·dms
  이미지·root, cap 은 drop ALL + CHOWN·FOWNER·DAC_READ_SEARCH, 이미지 fs 읽기 전용, SA 토큰 없음, hostPath `<SHARED_DIR>/dms`
  (`Directory` — 없으면 파드가 뜨지 않아 「마운트 없음」)와 호스트 `/proc/1/mountinfo`(읽기 전용), `activeDeadlineSeconds` 로 스스로도
  끝난다. 도우미 코드는 `kubectl exec -i` 의 stdin 으로 보낸다. 쓰는 곳은 `<SHARED_DIR>/dms/.probe-<nonce>` 하나이고 끝나면(실패·중단
  에도) 지우고 파드도 지운다 — 정리 실패는 INFO(판정 밖)로 남기고 지울 경로·명령을 stderr 에 낸다.
- **검사**: ① G1 같은 스토리지(포함 마운트 TARGET `/` → 「마운트 없음」, 교차 노드 nonce, `.dms-shared-id` 가 노드마다 다름·일부만·빈
  값·**전부 없음(S0 미실행)** → FAIL) ② G3 노드 간 가시성(부정 캐시·rename 교체, 1초 안, 20회 — **실패는 WARN**) ③ G4 root 소유
  보존(남 소유 0700 통과 포함) ④ G5 숫자 uid/gid(26·65532) ⑤ fsync 왕복 ⑥ G9 POSIX(O_EXCL·nlink·O_NOFOLLOW·rename 원자·디렉터리
  fsync). FS 종류·마운트 옵션만으로 FAIL 하지 않고, 교차 노드 확인(② 제외)은 `--retry-seconds` 까지 재시도한 뒤 판정한다.
  FAIL·WARN 사유에 NFS 힌트(`no_root_squash`·`sec=sys`/idmap·`lookupcache=positive` + `nocto` 없음)가 붙는다.
- 종료: 0 PASS · 3 WARN(G3 만 — 계속해도 된다) · 1 FAIL(센티널 없음 포함) · 4 모름(파드 미기동·이미지 pull 실패·exec 실패 — FAIL
  은 없음) · 2 사용법(클러스터를 건드리기 전). 마지막 줄 `RESULT:`. 마스터에 레지스트리 신뢰가 없으면 그 노드는 이미지 pull 실패로
  「모름」이다 — 미리 pull 해 둔다.

### 2d-4. 전환 런북 — 공용 디렉터리 이행·변경

옛 배치(hostPath `/cephfs`, base `/cephfs/dms/artifacts`)에서 `<SHARED_DIR>` 로 옮기거나 SHARED_DIR 를 바꿀 때. 이 렌더를 그냥
적용하면 `guard-images.sh` 가 hostPath 변경으로 **exit 5** 거부한다 — 아래를 마친 뒤에만 `ALLOW_HOSTPATH_CHANGE=1` 로 연다.

0. **확인(읽기만)**: 진행 중 잡이 없다(`kubectl --context <ctx> -n dms get vcjob,pods`) — 6 단계(DB base)까지 잡 제출을 멈춘다. 포탈 관리 →
   「아티팩트 경로」의 출처(env/db)와 값을 적어 둔다.
1. **노드 마운트**(사이트): 모든 마스터·워커의 같은 경로에 `/etc/fstab`. NFS 라면 export `no_root_squash`·`sec=sys`, 마운트 `hard`,
   `lookupcache=positive`(G3 권장 — remount 로는 안 바뀐다).
2. **S0**: 한 노드에서 `sh deploy/shared-dir-init.sh <SHARED_DIR>` → 다른 노드에서 `--check` 가 PASS(같은 센티널 값).
3. **S7**: `shared-storage-probe.py` 가 0(또는 3 WARN)이어야 진행한다. 1·4 면 멈추고 원인을 고친다.
4. **옛 base 복사(소유 보존)**: 한 노드에서 root 로 `rsync -aH --numeric-ids <옛 base>/ <SHARED_DIR>/dms/artifacts/`(rsync 가 없으면
   `cp -a <옛 base>/. <SHARED_DIR>/dms/artifacts/`). 대조: 두 쪽에서 `{ find . -type d -printf '%p d %U:%G %m\n'; find . ! -type d -printf '%p %y %U:%G %m %s\n'; } | sort | md5sum`
   이 같고(디렉터리 크기는 뺀다 — CephFS 는 디렉터리 크기를 하위 바이트 합계로 보고해 늘 다르다, 2026-11-03 T3 실측), 새
   base 가 `root:root 755`. `-A`·`-X` 는 쓰지 않는다(NFS 4.2 가 xattr 을 지원하지 않을 수 있고, base 에는 원래 ACL 이 없어야 한다 — §2b-1).
4b. **(`REGISTRY_MODE=in-cluster` 면) 레지스트리 저장소**: §2e-5 「저장소 이동」 2~4 를 앱 적용(5) **전에** 한다(빌드·push 가 없는 창 —
   S0 는 2 에서 했다). 빠뜨리면 레지스트리는 옛 경로에 남고, `SHARED_DIR_ID` 만 새 값으로 바꾼 렌더는 「센티널 불일치」로 레지스트리를 못
   띄운다 — install.sh S4 가 레지스트리 rollout 시간 초과(init 「센티널 불일치」)로 멈춘다(2026-11-03 ⑦).
5. **적용**: `sh deploy/overlays/guard-images.sh <overlay> --context <ctx>` → 기대 exit 5(라이브 hostPath → 새 경로를 찍는다; 오버레이
   태그가 라이브와도 다르면 3 이 먼저다 — 그때는 `ALLOW_IMAGE_CHANGE=1` 도). `ALLOW_HOSTPATH_CHANGE=1` 로 다시 통과시킨 뒤 평소 적용
   절차(testbed-k8s = §13-6, prod = `ALLOW_HOSTPATH_CHANGE=1 sh deploy/install.sh --context <ctx>` — S4·S11 의 가드가 함께 열린다). api·controller 를
   `rollout restart`(ConfigMap 의 base·allowlist 는 기동 때만 읽힌다 — §13-8) → `rollout status`. 확인: 두 Deployment 의
   `.spec.template.spec.volumes[*].hostPath` 와 `dms-config` 의 `DMS_ARTIFACT_BASE_URI`·`DMS_ARTIFACT_BASE_ALLOWED_PREFIXES` 가 새 경로.
   컨트롤러 롤아웃 뒤 30초쯤 기다린 다음 본다.
6. **DB base**: 0 단계 출처가 `db` 였다면 세션 관리자로 포탈 「아티팩트 경로」를 `file://<SHARED_DIR>/dms/artifacts` 로 다시 저장한다
   (잠겨 있으면 force). 적용 **뒤**여야 한다 — 3홉 검증은 새 마운트에서만 통과한다. `env` 면 할 일이 없다. 3홉(API·컨트롤러·노드)이 모두
   정상이고, 「DMS 상태 디렉터리」가 `<SHARED_DIR>/dms` 이며 겹치는 스토리지 경고가 없어야 한다.
7. **확인**: 옛 잡 로그가 열린다(복사본), 다중 노드에서 미리보기·실행·scan·사용량·정리, `<SHARED_DIR>` 를 `mount_path` 로 등록하면 422
   `storage_overlaps_dms_state`.
8. **보존·되돌림**: 옛 base 는 지우지 않는다(되돌림 창 — 기한은 BACKLOG). 되돌림은 같은 절차를 거꾸로 — SHARED_DIR(와 base)를 원래
   값으로 되돌려 `ALLOW_HOSTPATH_CHANGE=1` 로 적용하고, 출처가 db 였으면 다시 저장한다.

**옛 `SHARED_FS` 사이트(prod values.env)의 이행 주의** — `SHARED_FS` 가 남은 values.env 는 render.sh 가 FAIL 하고 `SHARED_DIR=/<옛 값>`
을 안내한다(같은 env base). 그 값을 쓰기 전에 확인한다(render.sh FAIL 문구와 같은 세 가지):
(1) 「아티팩트 경로」가 **DB 설정**이고 값이 `file://<SHARED_DIR>/dms/artifacts`(또는 그 아래)가 아니면 새 배치에서 그 경로는 api·controller 에
보이지 않는다(옛 allowlist·hostPath 는 공용 디렉터리 최상위 전체였다 — 잡 파드는 DB base 를 그대로 붙여 잡은 돌고 컨트롤러만 summary 를
못 읽는다) — 산출물을 그 경로로 옮기고 base 를 다시 저장(force)한다. (2) 등록 스토리지의 `mount_path`·`managed_root` 가 `<SHARED_DIR>/dms`
를 덮으면(옛 사이트의 `mount_path=/cephfs` 처럼) 그 스토리지를 쓰는 잡 컨테이너에 DMS 상태가 보인다 — SHARED_DIR 는 그 스토리지 밖의
**별도 마운트포인트**로 고른다(같은 파일시스템의 하위 디렉터리로는 못 피한다; 등록 가드는 경로를 바꿀 때만 걸리므로 적용 뒤 「아티팩트
경로」 화면의 경고로 드러난다). (3) 옛 배치 위에 이 렌더를 적용하면 guard exit 5 — 위 런북을 마친 뒤 `ALLOW_HOSTPATH_CHANGE=1`.

### 2d-5. k8s 테스트베드(`dmsk8s`) — 지금 배치 (T3, d18)

- **공용 스토리지**: 호스트(10.10.10.1)의 `/data/dms_shared`(root:root 0755)를 NFS 로 export — 클라이언트는 dmsk8s 3노드 10.10.10.20·.21·.22
  만(`rw,sync,no_subtree_check,no_root_squash`; 등록 스토리지용 `/data/k8s_pv`·VM 서브넷 전체용 `/data/mgmt_storage` 와 분리). 3노드
  (k8s-cp1·w1·w2) 모두 fstab 으로 `/dms_shared` 에: `10.10.10.1:/data/dms_shared /dms_shared nfs4 _netdev,nofail,vers=4.2,rw,hard,timeo=600,retrans=2,lookupcache=positive 0 0`.
  testbed IaC 반영 전이다(라이브 적용만 — BACKLOG).
- **배치**: `SHARED_DIR=/dms_shared`(오버레이 `values.env` + `patch-config.yaml` 의 base `file:///dms_shared/dms/artifacts`) → api/controller
  hostPath·allowlist `/dms_shared/dms/artifacts`, 상태 디렉터리 `/dms_shared/dms`(0750, S0).
- **옛 base** `/cephfs/dms/artifacts`(CephFS — 라이브 hostPath 가 `/cephfs` 이던 d17 까지)는 되돌림 창 동안 지우지 않는다. 데이터 root
  `/cephfs/managed`·`/cephfs/managed/e2e` 픽스처·스토리지 `cephfs-*` 는 그대로 CephFS 다(§2 플레이북). `cephfs-dms`(`/cephfs`)는
  `/dms_shared` 와 겹치지 않아 등록 가드·경고에 걸리지 않는다.
- **전환 명령**(2d-4 의 테스트베드 판, 한 번). S0 는 스크립트를 노드에 복사해(예: `/tmp`) root 로 돌린다. **w1·w2 에는 `/cephfs`(CephFS)가
  마운트돼 있다** — 거기서 `--check` 없이 `/cephfs` 로 돌리면 CephFS 에 트리를 만든다. 가드 음성 대조는 cp1(`/cephfs` 마운트 없음)에서만.

```bash
# 0. 확인 — 3노드 `findmnt /dms_shared` = nfs4 … lookupcache=positive, 진행 중 잡 없음, 「아티팩트 경로」 출처·값 기록
kubectl --context dmsk8s -n dms get vcjob,pods
# 1. S0 — w1 에서 한 번 → cp1·w2 검사 → cp1 가드 음성 대조
sudo sh /tmp/shared-dir-init.sh /dms_shared            # k8s-w1: RESULT: PASS (MADE …)
sudo sh /tmp/shared-dir-init.sh --check /dms_shared    # k8s-cp1·k8s-w2: PASS, 같은 .dms-shared-id
sudo sh /tmp/shared-dir-init.sh /cephfs; ls -ld /cephfs /cephfs/dms   # k8s-cp1 만: rc=1 「마운트 없음」, 아무것도 생기지 않음
# 2. S7 — 설치 호스트에서(이 체크아웃)
python3 deploy/shared-storage-probe.py --context dmsk8s --overlay deploy/overlays/testbed-k8s --dry-run
python3 deploy/shared-storage-probe.py --context dmsk8s --overlay deploy/overlays/testbed-k8s; echo rc=$?                      # 0(cp1 포함 ①~⑥)
python3 deploy/shared-storage-probe.py --context dmsk8s --overlay deploy/overlays/testbed-k8s --shared-dir /cephfs; echo rc=$?  # 1: cp1 「마운트 없음」, w1·w2 「센티널 없음」
#    (k8s-w1 에서 `sudo ls -la /cephfs/dms | grep -c '\.probe-'` = 0 — 프로브가 CephFS 에도 흔적을 남기지 않는다)
# 3. 옛 base 복사 — k8s-w1 에서 root 로, 소유 보존 + 대조
sudo rsync -aH --numeric-ids /cephfs/dms/artifacts/ /dms_shared/dms/artifacts/
sudo sh -c 'for b in /cephfs/dms/artifacts /dms_shared/dms/artifacts; do cd $b && { find . -type d -printf "%p d %U:%G %m\n"; find . ! -type d -printf "%p %y %U:%G %m %s\n"; } | sort | md5sum; done'   # 같음(디렉터리 크기 제외 — CephFS 디렉터리 크기 = rbytes)
stat -c '%U:%G %a' /dms_shared/dms/artifacts                                                                                 # root:root 755
# 4. 적용 — §13-6 순서 그대로, 가드만 플래그로 연다(오버레이 태그가 라이브와 다르면 ALLOW_IMAGE_CHANGE=1 도)
kubectl --context dmsk8s -n dms delete job dms-migrate --ignore-not-found
sh deploy/overlays/guard-images.sh deploy/overlays/testbed-k8s --context dmsk8s; echo rc=$?   # 5: hostPath /cephfs → /dms_shared/dms/artifacts
ALLOW_HOSTPATH_CHANGE=1 sh deploy/overlays/guard-images.sh deploy/overlays/testbed-k8s --context dmsk8s
kubectl --context dmsk8s apply -k deploy/overlays/testbed-k8s
kubectl --context dmsk8s -n dms wait --for=condition=complete job/dms-migrate --timeout=180s
kubectl --context dmsk8s -n dms rollout restart deploy/dms-api deploy/dms-controller
kubectl --context dmsk8s -n dms rollout status deploy/dms-api --timeout=300s
kubectl --context dmsk8s -n dms rollout status deploy/dms-controller --timeout=300s
kubectl --context dmsk8s -n dms get deploy dms-api dms-controller \
  -o jsonpath='{range .items[*]}{.metadata.name} {.spec.template.spec.volumes[*].hostPath}{"\n"}{end}'
kubectl --context dmsk8s -n dms get cm dms-config \
  -o jsonpath='{.data.DMS_ARTIFACT_BASE_URI} {.data.DMS_ARTIFACT_BASE_ALLOWED_PREFIXES}{"\n"}'
# 5~7. DB base 재저장(출처가 db 였을 때) → 3홉·「DMS 상태 디렉터리」 /dms_shared/dms·겹침 경고 없음 → 2d-4 의 7 확인
```

- 선택 음성 대조(설계 T3 — 별도 export·임시 마운트가 필요해 위 명령에는 없다, 2026-11-03 실측):
  - root_squash export → S7 **FAIL**(exit 1) — 3노드 모두 ③ G4 「root 소유 변경(squash) … export 에 no_root_squash」.
  - 같은 export 를 `nosharecache,lookupcache=all`(+`nocto`·60초 속성 캐시까지) 로 붙인 별도 경로 → **PASS**. 설계는 ② WARN 을
    기대했지만(추정) 이 커널(6.8)은 디렉터리가 바뀌면 부정 dentry 를 다시 확인해, 노드 셸 직접 측정에서도 다른 노드가 만든 새 이름이
    즉시(0.001초) 보였다 — ② 는 동작 판정이라 PASS 가 맞다(WARN 경로는 단위 테스트가 고정). 이 대조에서 찾은 프로브 결함(새 이름과
    rename 교체를 한 루프에서 봐 교체 쪽 LOOKUP 이 부정 dentry 를 무효로 만듦)은 고쳤다 — 이제 차례로 본다.
  - `SHARED_DIR=/cephfs` → **FAIL**(cp1 「마운트 없음」, w1·w2 「센티널 없음」), CephFS 위 `.probe-*` 잔여 0. S0 를 cp1 의 `/cephfs` 로
    돌리면 rc=1 이고 아무것도 만들지 않는다.
- **되돌림**: `values.env` 의 `SHARED_DIR=/cephfs` 와 `patch-config.yaml` 의 base `file:///cephfs/dms/artifacts`(→ hostPath `/cephfs/dms/artifacts`)로 함께 고치고,
  `tests/test_shared_dir_contract.py::test_testbed_values_env_carries_a_valid_shared_dir_matching_the_overlay_base` 의 기대값(`/dms_shared`)도
  고친 뒤 `ALLOW_HOSTPATH_CHANGE=1` 로 적용, 출처가 db 였으면 다시 저장한다. 그 배치에서는 `cephfs-dms`(`/cephfs`)가 상태 디렉터리
  `/cephfs/dms` 를 덮어 「아티팩트 경로」 화면에 겹침 경고가 뜬다(③ 이전 배치의 원래 모양 — 가드는 경로를 바꿀 때만 걸린다).

## 2e. 클러스터 내 레지스트리(플랫폼 층) — 노드 준비·S4·S5·S6·운영 (2026-11-03 ④)

설계 v6 7(7-1~7-6)·8-1(S0-4·S0-5·S-1·S4~S6)·8-2 「층 배치」·10 T4. 이 절은 dms-k8s 공통(prod·testbed-k8s)이고 테스트베드 판은 2e-6 이다.
설치기(§2h)의 S4·S5·S6 가 아래를 자동으로 한다(S4 뒤 정지·`--stage S5` 재개·lock 대조·pull 프로브 — 2026-11-03 ⑦). 아래 명령은 같은 일의
손 판(조사·복구용)이다.

CNCF Distribution v3.1.2 하나(오퍼레이터 없음, #46)를 control-plane 노드에 띄운다. **평문 HTTP·익명·`host:port` 만**(DMS 가 강제 — 포탈
태그 조회·삭제는 `http://` 고정, 빌드 push 는 `--tls-verify=false`, 경로 접두 불가). 저장소는 공용 디렉터리 `<SHARED_DIR>/dms/registry`
(S0 가 65532:65532 0750 + 센티널 `.dms-registry-id` 로 만든다 — §2d). DMS 코드(`registry.py`·`routes_registry.py`)는 바뀌지 않았다.

### 2e-1. 구성·값

| 무엇 | 어디 | 비고 |
|---|---|---|
| PriorityClass `dms-addon-critical`(1000) | `deploy/platform/priorityclass` | 잡(≤ 200)보다 위 — ⑤ CNPG·⑥ EG 컨트롤러가 쓴다. 레지스트리는 쓰지 않는다(마스터 전용) |
| ConfigMap `dms-registry-config` | `deploy/platform/registry/config.yml` + `expected-registry-id` | `/etc/distribution/config.yml`(이미지 기본·debug :5001 을 덮음): rootdirectory `/var/lib/registry`, delete 켬, uploadpurging 168h/24h, readonly 끔, **cache 없음**, `:5000`, health.storagedriver |
| Deployment `dms-registry` | `deploy/platform/registry/deployment.yaml` | replicas 1, control-plane nodeSelector + toleration, not-ready·unreachable 30초, RollingUpdate maxSurge 1·maxUnavailable 0, hostPath `<SHARED_DIR>/dms/registry`(`Directory`), 이미지 `<IP>:<PORT>/registry:3.1.2`(**태그 ref 예외**, init·본 컨테이너 모두 IfNotPresent — 자기 이미지를 자기에게서 pull 하지 않는다), 라벨 `dms.io/addon-version`(= 태그), readiness `GET /v2/_catalog?n=1`(5s/10s/3), liveness TCP, init `sentinel-check`(**검사만**), uid/gid 65532·drop ALL·읽기 전용 rootfs |
| Service `dms-registry` | `deploy/platform/registry/service.yaml` | `clusterIP: <REGISTRY_CLUSTER_IP>` 고정, 포트 `<REGISTRY_PORT>`(→ 컨테이너 5000) |
| Secret `dms-registry-http` | **매니페스트 밖**(2e-3) | 키 `http-secret` → env `REGISTRY_HTTP_SECRET`(secretKeyRef). 없으면 파드가 뜨지 않는다 |
| `deploy/images.lock` | 커밋 | linux/amd64 **매니페스트** digest(index 금지). 검사 `python3 deploy/images-lock.py check|verify`, 줄 만들기 `resolve <DEST> <SOURCE>` |

base 는 사이트 중립 자리표시자(`set-by-overlay.invalid`)뿐이라 `kubectl apply -k deploy/platform/registry` 는 실패한다(의도). 사이트 값은
렌더러가 낸다 — **같은 렌더러가 앱 오버레이와 플랫폼 층을 함께 렌더한다**:

```bash
sh deploy/render.sh <site> [--context <ctx>]      # prod·testbed-k8s. 옛 `sh deploy/overlays/prod/render.sh` 는 `render.sh prod` 의 호환 진입점
# → deploy/.rendered/<site>/app/                    (앱 — kubectl apply -k, client-side)
#   deploy/.rendered/<site>/platform/priorityclass/ (SSA)
#   deploy/.rendered/<site>/platform/registry/      (SSA — REGISTRY_CLUSTER_IP 가 있을 때만)
#   deploy/.rendered/<site>/render.env              (해석값: REGISTRY 등)
```

| values.env 키 | 뜻 |
|---|---|
| `REGISTRY_MODE` | `in-cluster`(앱이 이 레지스트리를 쓴다 — prod 권장) / `external`(사이트 레지스트리 `REGISTRY`). 없으면 external(옛 values.env) |
| `REGISTRY_CLUSTER_IP` | Service 고정 ClusterIP. 렌더러는 IPv4 형식만 본다 — ServiceCIDR 안·미사용·하단 정적 대역은 S1(`preflight-cluster.sh --render-env`)이 본다(2026-11-03 ⑦) — 손으로는: `kubectl --context C get servicecidr`(대역), `kubectl --context C get ipaddresses.networking.k8s.io <IP>` 가 NotFound(미사용), 하단 정적 대역 = CIDR 의 처음 min(max(16, 주소 수/16), 256)개(/12 면 `x.x.0.0`~`x.x.0.255` — 동적 할당이 마지막에 쓰는 대역이라 걷어내기·재적용 사이에 다른 Service 가 가져갈 위험이 가장 작다). 있으면 레지스트리 층을 렌더한다(external 이어도 — 병행 기간) |
| `REGISTRY_PORT` | Service 포트(기본 5000) |
| `REGISTRY` | 앱 레지스트리 `host:port`. in-cluster 면 비우거나 `<IP>:<PORT>` 와 같게 — **같은 주소가 여섯 곳**(REGISTRY·newName·`DMS_JOB_IMAGE`·`DMS_BUILD_REGISTRY`·`DMS_BUILD_BUILDER_IMAGE` + DB `control_state.job_image`)의 원천. 렌더러가 다섯 곳이 같은지 다시 본다 |
| `REGISTRY_TLS_VERIFY` | in-cluster 면 `false` 만. external TLS 레지스트리면 `true` — 인터넷 노드 push(`--dest-tls-verify true`)와 설치기 S5 대조(https)만 쓴다. 포탈 태그 조회·삭제·빌드 push 는 `http://` 고정이라 TLS 레지스트리에서는 쓰지 못한다(설계 7-2, 2026-11-03 ⑦ 리뷰 2). 빈 값 = `false` |
| `SHARED_DIR_ID` | S0 가 쓴 `<SHARED_DIR>/dms/.dms-shared-id` 값 = 센티널 기대값(레지스트리 층을 렌더할 때 필수) |
| `REGISTRY_CPU_REQUEST`·`REGISTRY_MEMORY_REQUEST` | (선택) requests — 기본 100m/128Mi(limits 1/512Mi) |

**주소는 한 번 정하면 바꾸지 않는다**(7-4): 노드 신뢰 drop-in·고정 이미지 이름·DB 의 잡 이미지에 박힌다. 바꾸면 S0-4·S0-5 를 다시 하고
앱 이미지·잡 이미지를 새 주소로 다시 릴리스한다. clusterIP 는 만든 뒤 못 바꾸는 필드라 다른 값의 apply 는 apiserver 가 거절한다.

### 2e-2. 노드 준비(사이트 IaC, 1회) — S0-4 신뢰 · S0-5 레지스트리 이미지 반입·고정

`C`=kube-context, `IP:PORT`=`<REGISTRY_CLUSTER_IP>:<REGISTRY_PORT>`. CRI-O 기준(containerd 경로 없음).

**주소부터 고르고 확인한다**(2026-11-03 ⑦ 리뷰 1): 이 주소는 아래 S0-4(전 노드 drop-in)·S0-5(마스터 반입 이름·고정 패턴)에 박히므로, 박기 전에
렌더 없이 env 만으로 S1 의 주소 검사(§5 — ServiceCIDR 안·미사용·하단 정적 대역)를 돌려 PASS 인 주소를 고른다. 예: /12 대역의 `x.x.0.10` 은
kube-dns 기본 주소라 FAIL 이다. 한 번 정하면 바꾸지 않는다.

```bash
REGISTRY_MODE=in-cluster REGISTRY_CLUSTER_IP=<IP> sh deploy/preflight-cluster.sh --context C    # §5 의 두 줄이 PASS 인 주소로
```

```bash
# S0-4 — 모든 노드(마스터·워커). v2 형식, .conf 만 읽고 같은 location 은 덮어쓴다. crio.conf 의 insecure_registries 는 폐기 예정이라 쓰지 않는다.
sudo tee /etc/containers/registries.conf.d/50-dms-registry.conf >/dev/null <<'EOF'
[[registry]]
location = "IP:PORT"
insecure = true
EOF
sudo systemctl reload crio                     # SIGHUP — 컨테이너 재시작 없음(자동 반영은 기본 꺼짐)
#   서명 정책: 패키지 CRI-O 는 /etc/crio/policy.json — reject 기본이면 IP:PORT 를 허용한다. 런타임 프록시 env 가 있으면 NO_PROXY 에 IP 를.

# S0-5 — 인터넷 노드(빌드 노드)에서 한 번: lock 의 amd64 digest 로 받아 파일 하나로(docker save 는 쓰지 않는다)
DG=$(awk '$1=="registry:3.1.2"{print $3}' deploy/images.lock)
skopeo copy "docker://docker.io/library/registry@$DG" oci-archive:/tmp/registry-3.1.2.tar
# → 마스터 전부로 옮긴다(scp 등). 각 마스터에서(store 지정은 `sudo crio config` 의 root·runroot·storage_driver 와 같게 — 기본값 예):
sudo skopeo copy oci-archive:/tmp/registry-3.1.2.tar \
  "containers-storage:[overlay@/var/lib/containers/storage+/run/containers/storage]IP:PORT/registry:3.1.2"
sudo crictl images | grep 'IP:PORT/registry'   # 3.1.2 가 보여야 한다
# 고정(kubelet 이 GC 하지 않게): 기존 pinned_images 를 읽어 합친다 — 같은 옵션은 이름순 마지막 drop-in 이 이기고 배열은 합치지 않는다.
sudo crio config 2>/dev/null | sed -n '/^[[:space:]]*pinned_images/,/]/p'     # 기존 목록(주석 `# pinned_images` 면 비어 있다)
sudo tee /etc/crio/crio.conf.d/99-dms-pinned.conf >/dev/null <<'EOF'
[crio.image]
pinned_images = ["IP:PORT/registry:*"]
EOF
#   ↑ 기존 목록이 있으면 같은 배열에 함께 쓴다. 패키지 10-crio.conf 는 업그레이드 때 덮어써지므로 건드리지 않는다.
sudo systemctl reload crio
sudo crio config 2>/dev/null | sed -n '/^[[:space:]]*pinned_images/,/]/p'     # 실효값에 IP:PORT/registry:* 가 있어야 한다
sudo rm -f /tmp/registry-3.1.2.tar             # 반입 뒤 지운다(인터넷 노드도)
```

- skopeo 는 RHEL AppStream 패키지다(사이트 미러) — 클러스터 노드 중에는 인터넷 노드와 마스터에만 둔다(워커에는 필요 없다).
  Ubuntu 24.04 노드의 `skopeo`(1.13.3)는
  `golang-github-containers-common`(`/etc/containers/policy.json`, **`/etc/containers/registries.conf.d/shortnames.conf`** — CRI-O 가 읽는
  drop-in 이고 짧은 이름 별칭 수십 개, `/usr/share/containers/{containers.conf,seccomp.json}`)·`golang-github-containers-image`
  (`/etc/containers/registries.conf`, 전부 주석)를 함께 깔고, containers-common 의 **필수** 의존 `container-network-stack` 으로
  containernetworking-plugins(`/usr/lib/cni`)도 깐다 — `--no-install-recommends` 로도 빠지지 않는다(2026-11-03 cp1·w1 `apt-get install -s`
  확인). CRI-O 의 `plugin_dirs` 는 기본값 `/opt/cni/bin` 뿐이라(`crio config` 에서 주석 처리된 기본값) `/usr/lib/cni` 는 쓰이지 않아 무해하다.
  추천 패키지 netavark·aardvark-dns 는 `--no-install-recommends` 로 빠진다(storage.conf 는 어느 패키지에도 없다).
  `apt-get install -y --no-install-recommends skopeo` 로 깔고, 영향은 설치 전후 `sudo find /etc/containers /etc/crio /etc/cni -type f -exec
  sha256sum {} +`·`ls /opt/cni/bin` 을 대조하고 `sudo crio config | grep -A3 plugin_dirs` 가 `/opt/cni/bin` 뿐인지 본다(`crio config`
  diff 는 registries.conf.d 의 내용을 보이지 않는다). 패키지 CRI-O 의 서명 정책은 `/etc/crio/policy.json` 이라 새 `/etc/containers/policy.json`
  은 CRI-O 에 쓰이지 않는다(`crio config` 의 `signature_policy` 로 확인).
- 새 워커는 S0-4 만 하면 된다. 새 마스터는 레지스트리가 살아 있으면 `sudo crictl pull IP:PORT/registry:3.1.2` 뒤 고정하고, 아니면 S0-5 를
  반복한다. 레지스트리 자기 이미지는 이 레지스트리에 기대지 않는다(7-4 의존 규칙 — 시스템 이미지도 마찬가지).
- 반입 store 지정: `crio config` 의 `storage_driver = ""` 는 자동 선택이다(실효 overlay — reload 로그 `storage_driver = "overlay"`, 2026-11-03
  T4 cp1) — 위 `overlay@…` 그대로 쓴다.

### 2e-3. 적용(S4) — PriorityClass·http 시크릿·레지스트리

네임스페이스 `dms` 가 먼저 있어야 한다(설계 8-1 S2 — 새 클러스터면 아래 첫 줄; 설치기는 S2 가 먼저 적용한다). 없으면 시크릿 생성과
레지스트리 apply 가 `namespaces "dms" not found` 로 실패한다.

```bash
kubectl --context C apply -f deploy/k8s/00-namespace.yaml        # S2: PSA privileged 라벨 — 이미지 없는 유일한 raw apply(멱등)
sh deploy/render.sh <site> --context C   # 태그가 'live' 면 C 에서 읽는다(빠뜨리면 current-context — 엉뚱한 클러스터의 태그)
R=deploy/.rendered/<site>/platform
kubectl --context C apply --server-side --field-manager=dms-install --dry-run=server -k $R/priorityclass
kubectl --context C apply --server-side --field-manager=dms-install -k $R/priorityclass
# http 시크릿 — 없을 때만 만들고 값을 출력하지 않는다(argv 에도 싣지 않는다: stdin 으로)
kubectl --context C -n dms get secret dms-registry-http >/dev/null 2>&1 || \
  head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' | \
  kubectl --context C -n dms create secret generic dms-registry-http --from-file=http-secret=/dev/stdin >/dev/null
sh deploy/overlays/guard-images.sh $R/registry --context C        # 재적용이면 이미지(3)·저장소 hostPath(5) 변경을 막는다
kubectl --context C apply --server-side --field-manager=dms-install --dry-run=server -k $R/registry
kubectl --context C apply --server-side --field-manager=dms-install -k $R/registry
kubectl --context C -n dms rollout status deploy/dms-registry --timeout=180s
kubectl --context C -n dms port-forward svc/dms-registry 15000:<PORT> >/dev/null 2>&1 &
PF=$!
# port-forward 는 apiserver 에 붙어 파드를 찾은 뒤에야 listen 한다 — 바로 curl 하면 000(연결 거부)이 나 실패로 잘못 보인다. 10초까지 기다린다.
for i in 1 2 3 4 5 6 7 8 9 10; do
  code=$(curl -s -o /dev/null -w '%{http_code}' 'http://127.0.0.1:15000/v2/_catalog?n=1') && [ "$code" != 000 ] && break
  sleep 1
done
echo "catalog $code"                                                     # 200(빈 레지스트리도)
kill $PF
```

먼저 `kubectl --context C -n dms describe pod -l app.kubernetes.io/name=dms-registry` 의 이벤트, 그다음 init 로그
(`kubectl --context C -n dms logs deploy/dms-registry -c sentinel-check`)를 본다 — 가장 흔한 실패(경로 없음)는 init 이 돌기 전에 난다.

| 증상 | 뜻 · 할 일 |
|---|---|
| Pod `Init:0/1`(ContainerCreating) + 이벤트 `FailedMount … hostPath type check failed: <SHARED_DIR>/dms/registry is not a directory` | 그 마스터에 공용 스토리지 마운트가 없거나 S0 미실행(설계 S4 「마스터 X 에 공용 디렉터리 없음」). `findmnt -T <SHARED_DIR>`·S0 `--check`. 경로를 손으로 만들지 않는다(G2 — 마운트 없는 노드의 로컬 디스크에 쓰게 된다). init 로그는 비어 있다 |
| init `sentinel-check` 실패 「센티널 없음」 | 경로는 있으나 `.dms-registry-id` 가 없다 — 마운트가 빠진 마스터에 같은 이름의 **로컬** 디렉터리가 있거나, 센티널이 지워졌거나, S0 를 거치지 않고 만든 디렉터리. `findmnt -T <SHARED_DIR>`·S0 `--check` 를 본다. init 은 만들지 않는다 |
| 「센티널 불일치」 | `SHARED_DIR_ID`(values.env → ConfigMap `expected-registry-id`)와 저장소의 `.dms-registry-id` 가 다르다. 먼저 아무 노드에서 `sudo cat <SHARED_DIR>/dms/.dms-shared-id <SHARED_DIR>/dms/registry/.dms-registry-id` 로 두 파일을 본다. **두 값이 같고 values 만 다르면**(가장 흔함 — 옮겨 적다 틀렸거나 S0 를 다시 한 뒤 갱신 누락): values.env 의 `SHARED_DIR_ID` 를 고치고 `sh deploy/render.sh <site> --context C` → platform/registry SSA 적용(설정 체크섬이 바뀌어 새 파드로 교체된다) → `rollout status`. **두 파일이 서로 다르면**: 다른 스토리지·옛 설치의 흔적이다 — S0 `--check` 결과와 함께 손으로 확인하기 전에는 고치지 않는다(init 도 고치지 않는다) |
| 「저장소 디렉터리를 읽을 수 없음」 | `<SHARED_DIR>/dms/registry` 소유·모드가 65532:65532 0750 이 아니다(S0 `--check`) |
| 「센티널 기대값 없음」 | 렌더하지 않은 base 를 적용했다 — `.rendered/<site>/platform/registry` 를 적용 |
| Pod `CreateContainerConfigError` | Secret `dms-registry-http` 가 없다(위 명령) |
| Service apply `provided IP is already allocated` | `REGISTRY_CLUSTER_IP` 를 다른 Service 가 쓴다(`kubectl --context C get ipaddresses.networking.k8s.io <IP>` 의 PARENTREF) — 아직 아무 데도 박히기 전이면 미사용 주소로 바꾼다(2e-1 의 확인 명령) |
| 파드 Pending + 이벤트 `FailedScheduling` | control-plane 노드에 자리가 없거나 taint 키가 다르다(`describe` 의 사유). FailedMount 면 위 첫 줄 |
| `ErrImagePull`·`ImagePullBackOff` `…IP:PORT/registry:<태그>` | 그 마스터에 S0-5 반입·고정이 없다(레지스트리는 자기 이미지를 자기에게서 받을 수 없다 — 태그 ref·IfNotPresent). 그 노드에서 `sudo crictl images --digests \| grep registry` · 2e-4 2) 를 그 마스터에 한다. 설치기 S4 는 실패 사유에 파드의 노드 이름을 함께 낸다 |

### 2e-4. 이미지 반입 — 인터넷 노드(S0-5·S5)·설치 호스트 대조(S5)·pull 확인(S6) (2026-11-03 ⑦)

설계 v6 7-5·8-1(S-1·S0-5·S4~S6). 여러 이미지를 묶은 오프라인 번들은 없다 — **인터넷 노드 1대**(외부 인터넷이 되는 클러스터 노드, 첫
설치에는 K8 로 받은 노드, 설치 뒤에는 컨트롤 상태의 빌드 노드 `build_node_name` — S13)가 상류에서 받아 레지스트리 ClusterIP 로 직접
push 한다. 파일로 옮기는 것은 레지스트리 자기 이미지 하나(oci-archive)뿐이고 반입 뒤 지운다. 설치기(§2h)는 S4 뒤에 멈춰 3) 의 명령을
보이고(`RESULT: PAUSE`, exit 10), push 뒤 `install.sh --context C --stage S5` 가 4) 의 대조와 S6 를 한다.

| 누가 | 무엇 | 도구 |
|---|---|---|
| 인터넷 노드 | S0-5 레지스트리 자기 이미지 → oci-archive 파일(레지스트리가 뜨기 전, 1회) · S5 lock push · DMS 3종 최초 빌드 | skopeo(RHEL AppStream), podman(DMS 최초 빌드), python3(lock 검사 — RHEL 9 기본), DMS 저장소 체크아웃(**설치와 같은 커밋** — lock 이 그 커밋의 것) |
| 마스터 ×3 | S0-5 반입·고정(파일을 받아 containers-storage 로) | skopeo, crictl |
| 설치 호스트 | S4 적용 → 멈춤 → S5 대조(`install.sh --stage S5`) → S6 | kubectl(`--context C`), python3, curl |

프록시: 상류 pull 은 인터넷 노드의 env `HTTPS_PROXY`(·`HTTP_PROXY`)를 타고, **레지스트리 push 는 타면 안 된다**(프록시는 ClusterIP 에
닿지 않는다 — 포탈 빌드와 같은 규칙, §8). 프록시를 거치는 노드면 `NO_PROXY` 에 레지스트리 IP 를 넣는다. skopeo(Go)는 `NO_PROXY`
가 비어 있지 않으면 그것만 읽고(`no_proxy` 무시), 정확한 IP·`IP:PORT`·CIDR·도메인 접미·`*` 를 받는다. 컨트롤 상태 화면의 프록시
값은 빌드·프리플라이트 **파드**에만 들어간다 — 호스트에서 도는 이 반입에는 셸 env 로 준다.

#### 1) 인터넷 노드 — lock 확인(S-1 · 릴리스 담당이 이미 했어도 같은 커밋인지)

```bash
cd <DMS 저장소>                                     # 설치 호스트와 같은 커밋
python3 deploy/images-lock.py check                 # 형식(오프라인)
python3 deploy/images-lock.py verify                # 상류에서 digest 가 linux/amd64 단일 매니페스트인지(index digest 면 FAIL)
#   UNKNOWN(exit 4) = 상류에 닿지 못함 — 프록시 env·사내 CA 를 본다(실패가 아니다; 고친 뒤 다시).
```

#### 2) S0-5 — 레지스트리 자기 이미지(레지스트리가 뜨기 전, 1회 · 새 마스터)

레지스트리 Deployment 는 자기 이미지를 **태그 ref**(`IP:PORT/registry:<태그>`, 설계 7-5 의 문서화된 예외)로, `IfNotPresent` 로 쓴다 —
자기에게서 pull 할 수 없으므로 마스터마다 미리 넣고 kubelet GC 에서 고정한다. 태그·digest 는 lock 의 `registry:` 줄 하나가 원천이다.

```bash
# 인터넷 노드: lock 의 amd64 매니페스트 digest 로 받아 파일 하나로(docker save·index digest 는 쓰지 않는다)
RT=$(awk '$1 ~ /^registry:/ {print $1}' deploy/images.lock)          # 예: registry:3.1.2
DG=$(awk -v d="$RT" '$1 == d {print $3}' deploy/images.lock)
SRC=$(awk -v d="$RT" '$1 == d {sub(/:[^:\/]*$/, "", $2); print $2}' deploy/images.lock)   # docker.io/library/registry
skopeo copy "docker://$SRC@$DG" "oci-archive:/tmp/${RT%%:*}-${RT#*:}.tar"
sha256sum "/tmp/${RT%%:*}-${RT#*:}.tar"                                # 옮긴 뒤 마스터에서 같은 값인지
# → 마스터 3대로 옮긴다(scp 등 — K8). 각 마스터에서(store 는 `sudo crio config` 의 root·runroot·storage_driver 와 같게 — 기본값 예):
sudo skopeo copy oci-archive:/tmp/registry-3.1.2.tar \
  "containers-storage:[overlay@/var/lib/containers/storage+/run/containers/storage]IP:PORT/registry:3.1.2"
sudo crictl images --digests | grep 'IP:PORT/registry'               # 3.1.2 와 DIGEST 앞자리 = lock digest 앞자리(T4: 1290056e…)
# 고정 — 같은 옵션은 이름순 마지막 drop-in 이 이기고 배열은 합치지 않는다: 기존 목록을 읽어 같은 배열에 함께 쓴다.
sudo crio config 2>/dev/null | sed -n '/^[[:space:]]*pinned_images/,/]/p'     # 기존 목록(주석 `# pinned_images` 면 비어 있다)
sudo tee /etc/crio/crio.conf.d/99-dms-pinned.conf >/dev/null <<'EOF'
[crio.image]
pinned_images = ["IP:PORT/registry:*"]
EOF
#   ↑ 기존 목록이 있으면 같은 배열에 넣는다. 패키지 10-crio.conf 는 업그레이드 때 덮어써지므로 건드리지 않는다.
sudo systemctl reload crio
sudo crio config 2>/dev/null | sed -n '/^[[:space:]]*pinned_images/,/]/p'     # 실효값에 IP:PORT/registry:* 가 있어야 한다
sudo crictl inspecti IP:PORT/registry:3.1.2 | grep -m1 '"pinned"'             # "pinned": true (CRI Image.pinned — preflight 노드 체크리스트와 같은 줄;
                                                                              #  T4 가 어떤 명령으로 봤는지는 기록이 없다 — T9 에서 실측 확인)
sudo rm -f /tmp/registry-3.1.2.tar                                            # 마스터·인터넷 노드 모두 지운다
```

- 새 마스터: 레지스트리가 살아 있으면 `sudo crictl pull IP:PORT/registry:<태그>` 뒤 고정(패턴 `registry:*` 가 덮는다), 아니면 이 2) 를
  다시 한다(레지스트리 자기 이미지는 이 레지스트리에 기대지 않는다 — 7-4 의존 규칙).
- 레지스트리 업그레이드: lock 의 `registry:` 줄·`deploy/platform/registry` 의 두 이미지 태그·라벨을 한 커밋에서 바꾼 뒤 3) 의
  `--only registry` push → 마스터 전부 `crictl pull` → §2e-5 업그레이드 순서.

#### 3) S5 — 인터넷 노드에서 lock push (`deploy/push-images.sh`)

`install.sh` 는 S4(레지스트리 Ready·`/v2/` 200) 뒤에 멈추고 아래 명령을 출력한다(이 사이트가 받지 않는 줄 — 남이 설치한 CNPG(C)의 오퍼레이터,
external 사이트의 레지스트리 자기 이미지 — 은 `--skip` 이 붙어 나온다). 인터넷 노드에서:

```bash
cd <DMS 저장소>                                           # 설치 호스트와 같은 커밋
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}IP"              # 프록시를 거치는 노드만(no_proxy 만 쓰는 셸이면 그것도)
sh deploy/push-images.sh --registry IP:PORT              # --print(기본): 실행할 skopeo 명령만 본다(네트워크 없음)
sh deploy/push-images.sh --registry IP:PORT --run        # push — 멱등(이미 lock digest 인 태그는 SKIP, push 뒤 digest 재확인)
#   한 줄만: --only <이름|DEST>(여러 번), 빼기: --skip <이름|DEST>. 끊겼으면 같은 명령을 다시 돌린다(올라간 줄은 건너뛴다).
#   마지막 줄 RESULT: PASS 면 설치 호스트에서 install.sh --context C --stage S5 로 재개한다.
```

- 줄마다 `skopeo copy --preserve-digests --dest-tls-verify=false docker://<SOURCE 저장소>@<DIGEST> docker://IP:PORT/<DEST>` 다 — 소스를 lock
  의 amd64 매니페스트 digest 로 집어 그 매니페스트 하나만 복사하고(`--all` 금지), `--preserve-digests` 로 레지스트리 digest == lock digest
  를 지킨다(렌더가 `<REG>/<DEST>@<DIGEST>` 로 받는다). DEST 는 늘 태그를 붙인다(GC `--delete-untagged` 가 태그 없는 digest 를 지운다).
- 대상: lock 전부 — buildah(포탈 빌더), CNPG 2(오퍼레이터·PG), EG 2(gateway — 컨트롤러·certgen·shutdown-manager 가 같은 이미지, envoy),
  레지스트리 자기 이미지(업그레이드·새 마스터·S6 용). Volcano 3 줄(A 경로 설치용)은 Volcano 층과 함께 덧붙인다(아직 없다 — BACKLOG ⑦).
- `--run` 은 프록시 env 가 있는데 `NO_PROXY` 가 레지스트리를 덮지 않으면 **아무것도 하지 않고** FAIL 한다(stderr 에 export 줄).
- **external 레지스트리 사이트**(`REGISTRY_MODE=external`, `REGISTRY_CLUSTER_IP` 없음): S4 는 PriorityClass 만 — lock 전부를 사이트 `REGISTRY` 로
  push 한다(`--registry <REGISTRY>`, TLS 레지스트리면 `--dest-tls-verify true` — values.env `REGISTRY_TLS_VERIFY=true` 면 설치기가 그대로 싣는다;
  포탈 태그 조회·삭제는 http 라 TLS 레지스트리에서 쓰지 못한다). 설치기 S5 가 그 `REGISTRY` 를 직접 대조하고(설치 호스트가 닿아야
  한다 — 닿지 않으면 모름 exit 4) 다르면 이 명령을 같은 주소로 보인다. lock 대조가 PASS 면 lock 줄은 다시 보이지 않고 DMS 이미지 안내만 보인다
  (2026-11-03 ⑦ 리뷰 2). 출력은 **인터넷 노드의 DMS 저장소(설치 호스트와 같은 커밋) 루트에서** 붙여 넣는다(`lock: deploy/images.lock` — 상대 경로).
- **DMS 3종**(dms·dms-agent·dms-mpifileutils, B3 포함 dN)은 상류 이미지가 아니라 lock 밖이다. 처음에는 같은 노드에서 최초 부트스트랩으로
  빌드해 push 한다(`REGISTRY=IP:PORT TAG=<values.env 의 DMS_TAG — 세 태그 같게> ./deploy/docker/build-and-push.sh`, §1 — 빌드 전 e2e 게이트는 인터넷 노드가 아니라 개발 PC
  에서 같은 커밋으로). 기존 레지스트리가 있으면 `skopeo copy --preserve-digests --src-tls-verify=false --dest-tls-verify=false
  docker://<옛 레지스트리>/dms:dN docker://IP:PORT/dms:dN` 로 옮겨도 된다. 설치 뒤에는 포탈 빌드(§8)가 같은 노드를 빌드 노드로 쓴다.
  설치기 S5 는 앱 렌더의 세 이미지 태그가 레지스트리에 있는지도 본다(없으면 위 안내와 함께 멈춘다 — S11 도 apply 전에 다시 본다).

| 증상(`--run`) | 뜻 · 할 일 |
|---|---|
| `FAIL NO_PROXY — 아무것도 push 하지 않았다` | 프록시 env 가 있는데 레지스트리 IP 가 `NO_PROXY` 밖 — 출력된 `export NO_PROXY=…` 뒤 다시 |
| `skopeo copy 실패` + `pinging container registry IP:PORT … connection refused / i/o timeout` | 인터넷 노드 → ClusterIP 경로(kube-proxy 가 그 노드에서 도는가, 노드 방화벽), S4 의 레지스트리 Ready |
| `… http: server gave HTTP response to HTTPS client` | `--dest-tls-verify true` 로 돌렸다 — 클러스터 내 레지스트리는 평문 HTTP(기본 false) |
| `… reading manifest sha256:… : manifest unknown`(소스) | lock digest 가 상류에 없다(상류가 지움·저장소 이동) — 1) 의 `verify`, 새 줄은 `images-lock.py resolve` |
| `… toomanyrequests` | docker.io 익명 pull 한도 — 시간을 두고 다시(멱등) |
| `… x509: certificate signed by unknown authority`(소스) | 사내 TLS 가로채기 프록시 — 사내 CA 를 인터넷 노드 신뢰 저장소에(`/etc/pki/ca-trust/source/anchors` + `update-ca-trust`) |
| `push 뒤 레지스트리 digest … 가 lock 과 다르다` | 같은 태그에 다른 push 가 겹쳤다(포탈 빌드·다른 운영자) — 겹치는 작업을 멈추고 다시 |

#### 4) S5 대조 — 설치 호스트(`install.sh --context C --stage S5`)

설치기가 port-forward 를 열어 `images-lock.py compare-registry` 로 각 DEST 태그의 매니페스트 본문 sha256 을 lock 과 견준다 — **태그가 없거나
다른 digest 면 FAIL**(설계 8-1 S5). 손으로 같은 일을 하려면:

```bash
kubectl --context C -n dms port-forward svc/dms-registry :PORT >/tmp/pf.log 2>&1 &
PF=$!
# listen 을 기다린다(apiserver 에 붙어 파드를 찾은 뒤에야 찍힌다) — 무작위 로컬 포트
for i in 1 2 3 4 5 6 7 8 9 10; do
  LPORT=$(sed -n 's/^Forwarding from 127\.0\.0\.1:\([0-9][0-9]*\) .*/\1/p' /tmp/pf.log | head -n 1); [ -n "$LPORT" ] && break; sleep 1
done
python3 deploy/images-lock.py compare-registry --registry-url "http://127.0.0.1:$LPORT"     # [--skip <이름>]... [--only <이름>]...
kill $PF
```

| 결과 | 뜻 · 할 일 |
|---|---|
| `OK <DEST>: sha256:…` | 태그 == lock digest. `registry`·`buildah` 줄에는 `[태그 ref …]` — 소비자가 digest 가 아니라 태그로 받는다(레지스트리 자기 이미지 = 설계 7-5 예외: S6 pull 프로브·새 마스터·업그레이드, buildah = 포탈 빌더) |
| `FAIL … 태그 … 가 없다(404) — lock 의 이미지가 레지스트리에 없다` | 3) 을 하지 않았거나 그 줄이 빠졌다 — 출력된 `push-images.sh --only <DEST> --run` |
| `FAIL … 가 없다(404). lock digest 는 레지스트리에 있지만 …` / `… 다른 digest … lock digest 는 레지스트리에 있지만 이 태그가 가리키지 않는다` | 태그 없는 lock digest — GC 가 지운다. 태그를 붙여 다시 push(같은 명령) |
| `FAIL … 다른 digest … lock 과 다른 이미지가 이 태그로 push 됐다` | 누군가 같은 태그에 다른 이미지를 push 했다(태그 ref 줄이면 소비자가 그 이미지를 받는다) — 다시 push 로 lock 에 맞춘다 |
| `UNKNOWN 레지스트리에 닿지 못했다` (exit 4) | port-forward 가 닫혔거나 주소가 틀렸다 — S4 의 레지스트리 Ready·`/v2/` 부터 |
| `UNKNOWN … HTTP 401 — 익명 레지스트리가 아니다` | 다른 서비스에 붙었다(클러스터 내 레지스트리는 익명) — Service·포트 확인 |

대조는 레지스트리 안의 사본만 본다 — 마스터에 고정된 레지스트리 자기 이미지는 2) 의 `crictl images --digests` 가, 노드 pull 은 S6 가 본다.

#### 5) S6 — pull 확인(설치기의 임시 DS — `deploy/pull-probe.py`; 손으로는 모든 노드에서)

```bash
python3 deploy/pull-probe.py --context C --render-env deploy/.rendered/<site>/render.env      # 노드마다 실제 pull(Always), 120초, DMS 노드만 판정
sudo crictl pull IP:PORT/registry:3.1.2 && sudo crictl pull IP:PORT/buildah:stable           # 손으로(노드에서)
#   실패면: 신뢰 drop-in(S0-4)·서명 정책(/etc/crio/policy.json)·런타임 프록시 NO_PROXY·ClusterIP 경로(kube-proxy) 순서로 본다.
```

#### 테스트베드(dmsk8s) 실측 (2026-11-03, d19 레지스트리 10.112.0.50:5000)

`compare-registry`(port-forward) — lock 6줄(registry·buildah·cloudnative-pg·postgresql·gateway·envoy) 전부 OK, RESULT PASS. lock 사본에 Volcano
v1.16.0 3줄(`images-lock.py resolve` 로 만든 docker.io/volcanosh 의 amd64 digest)을 더하면 그 3줄만 「태그 없음 — 레지스트리에 없다」 FAIL
(테스트베드 Volcano 는 IaC 설치 — 레지스트리에 vc-* 저장소 없음), `--skip` 으로 빼면 PASS. 닿지 않는 `HTTP_PROXY` env 를 걸어도 PASS(대조는
프록시를 쓰지 않는다). `install.sh --context dmsk8s --site testbed-k8s --only S5` — 같은 대조 + dms·dms-agent·dms-mpifileutils d19 태그 확인 PASS,
끝난 뒤 port-forward 프로세스 0(2026-11-03 ⑦).

### 2e-5. 운영 (설계 7-6)

- **업그레이드**: `deploy/platform/registry/deployment.yaml` 의 두 이미지 태그·라벨 `dms.io/addon-version` 과 `deploy/images.lock` 의
  `registry:` 줄(`images-lock.py resolve`)을 함께 바꿔 커밋(셋이 같아야 한다 — 테스트가 고정) → 인터넷 노드가 새 이미지를 push(S5) → **마스터 전부** `sudo crictl pull IP:PORT/registry:<새 태그>`(고정 패턴
  `registry:*` 가 덮는다) → `sh deploy/render.sh <site> --context C` → `guard-images.sh` 가 exit 3(이미지 변경 — 안내에 이 순서가 나온다) →
  `ALLOW_IMAGE_CHANGE=1` 로 다시 → SSA apply → `rollout status`. 설치기로는 마스터 pull 뒤 `sh deploy/install.sh --context C --site <site> --only S4
  --upgrade-addon registry`(감지 D< → upgrade — 가드를 이 층에서만 연다; 전역 `ALLOW_IMAGE_CHANGE=1` 은 S11 의 앱 가드까지 열므로 설치기에서는
  쓰지 않는다, 2026-11-03 ⑦ 리뷰 1). maxSurge 1 이라 새 파드가 Ready 가 된 뒤 옛 파드가 내려간다. **빌드·push
  가 없을 때** 한다(겹치는 동안 Service 가 요청을 나눠 S7 ② WARN 사이트에서는 push 가 `blob unknown` 일 수 있다). 옛 파드는 preStop 5초
  뒤 SIGTERM 을 받고 진행 중 요청을 `http.draintimeout`(20s) 동안 마친다(b3c5f4e — T4 실측: 교체 중 매니페스트 GET 172회·이미지 복사 62회
  실패 0). drain 이 없는 옛 템플릿(b3c5f4e 이전)에서 처음 올릴 때는 옛 파드가 drain 없이 끝난다(SIGTERM 에 곧바로 exit 2 — 진행 중 pull 이
  끊긴다) — 빌드·push 가 없는 조용한 창에.
- **설정 변경**(2026-11-03 ④ 리뷰 2): Distribution 은 `config.yml` 을, init 은 `expected-registry-id` 를 **기동 때만** 읽는다. 렌더러가 둘의
  sha256 을 파드 템플릿 annotation `dms.io/config-sha256` 에 넣으므로 `config.yml`·`SHARED_DIR_ID` 만 바뀐 렌더도 SSA 로 적용하면 롤링
  교체된다(가드는 이미지·hostPath 만 보므로 이 변경은 막지 않는다). 업그레이드와 같이 빌드·push 가 없을 때 한다. 손으로 고친 ConfigMap
  (아래 GC 길 B)은 렌더값이 그대로라 체크섬도 그대로다 — 되돌린 뒤 `rollout restart` 가 필요하다.
- **GC**(연 몇 번, 수동 — 빌드가 없고 레지스트리가 Ready 일 때만):
  1. readonly 켜기: `kubectl --context C -n dms set env deploy/dms-registry REGISTRY_STORAGE_MAINTENANCE_READONLY='{"enabled":true}'`
     (env 의 JSON 맵 파싱은 T4 확인(POST 405) — 길 A. 다른 판에서 안 되면 길 B: `kubectl --context C -n dms edit cm dms-registry-config` 의 `readonly.enabled: true` +
     `kubectl --context C -n dms rollout restart deploy/dms-registry`. 그러면 `data.config.yml` 이 kubectl-edit 소유가 되므로 끈 뒤의 SSA
     재적용은 `--force-conflicts` 가 필요하다).
  2. `rollout status` 가 끝나고 옛 ReplicaSet 파드(Terminating 포함)가 0개인지 본 뒤 `terminationGracePeriodSeconds`(30초)만큼 더 기다린다.
  3. `curl -s -o /dev/null -w '%{http_code}' -X POST http://IP:PORT/v2/dms/blobs/uploads/` 가 **405**(읽기 전용의 증거).
  4. `kubectl --context C -n dms exec deploy/dms-registry -c registry -- registry garbage-collect --dry-run --delete-untagged /etc/distribution/config.yml`
     로 확인한 뒤 `--dry-run` 없이. **태그 없는 digest 이미지는 지워진다.**
  5. readonly 끄기 → 롤아웃 뒤 `POST …/blobs/uploads/` 가 202 → 시험 push·삭제. 길 A(env): `set env … REGISTRY_STORAGE_MAINTENANCE_READONLY-`.
     길 B(ConfigMap): `kubectl --context C apply --server-side --field-manager=dms-install --force-conflicts -k $R/registry`(ConfigMap 원복) →
     **`kubectl --context C -n dms rollout restart deploy/dms-registry`** → `rollout status` — 렌더값이 그대로라 체크섬 annotation 이 바뀌지 않아
     SSA 만으로는 파드가 다시 뜨지 않고 계속 읽기 전용이다.
  GC 창 동안 포탈 삭제는 409(`registry_delete_disabled` — 문구가 GC 읽기 전용 창도 말한다, T4 확인), 빌드 push 는 실패, pull 은 된다.
  `set env` 로 바꾼 필드는 kubectl 소유가 되므로 끈 뒤 다음 SSA apply 가 충돌하면 `--force-conflicts` 로 dms-install 에 되돌린다.
- **포탈 이미지 관리**(「빌드 > 이미지 관리」, §8 2b): 삭제는 digest 단위(매니페스트)다 — 안 쓰는 태그라도 사용 중 태그와 digest 를
  공유하면 409 `registry_digest_in_use`(같이 지우면 사용 중 태그가 사라진다 — T4 실증, 01fd922). 사용 중 판정은 동봉 매니페스트에 더해
  DB 잡 이미지 실효값(`control_state.job_image` — 레지스트리 전환 뒤 다시 릴리스한 잡 이미지)도 본다.
- **백업**: 애드온은 인터넷 노드가 lock 의 digest 로 다시 받을 수 있다. **현장에서 빌드한 DMS 태그는 레지스트리에만 있다** —
  `skopeo sync --src docker --dest dir --src-tls-verify=false IP:PORT/dms <공용 스토리지 밖 경로>`(dms-agent·dms-mpifileutils 도)로 사본을 더한다.
- **장애**(설계 7-6 표): 레지스트리가 있는 마스터의 공용 스토리지가 멈추면 readiness(catalog)가 NotReady 로 드러낸다(`/v2/` 는 계속 200) —
  그 마스터를 cordon 하고 파드를 지우는 수동 조치 전까지 이어진다. 이미 도는 파드는 영향이 없다(`IfNotPresent`). **릴리스는 레지스트리가
  정상일 때만** 한다.
- **걷어내기**(앱이 아직 이 레지스트리를 쓰지 않을 때만 — 실증 중단·다시 적용 전): `kubectl --context C delete -k $R/registry`(ConfigMap·
  Service·Deployment — clusterIP 가 풀린다) → `kubectl --context C -n dms delete secret dms-registry-http` → PriorityClass 는 참조하는 파드가
  0개일 때만(`kubectl --context C get pods -A -o jsonpath='{range .items[*]}{.spec.priorityClassName}{"\n"}{end}' | grep -cx dms-addon-critical`
  이 0) `kubectl --context C delete priorityclass dms-addon-critical`. 저장소 `<SHARED_DIR>/dms/registry` 는 지우지 않는다(센티널·push 한
  이미지 — 다시 적용하면 그대로 쓴다). 노드의 신뢰 drop-in·고정 설정·반입 이미지는 남겨도 무해하다(그 주소에 아무것도 없으면 쓰이지 않는다).
- **저장소 이동**(SHARED_DIR 변경 — §2d-4 의 in-cluster 하위 단계, 2026-11-03 ④ 리뷰 2). 렌더된 레지스트리 층의 hostPath 가 바뀌어
  `guard-images.sh` 가 exit 5 로 멈춘다. **빌드·push 가 없는 창에서, 앱 재적용보다 먼저** 한다(앱의 새 파드는 이 레지스트리에서 pull 한다):
  1. 새 경로에 S0(`sh deploy/shared-dir-init.sh <새 SHARED_DIR>` — 새 무작위 `.dms-shared-id` 와 같은 값의 `registry/.dms-registry-id`) → 다른
     노드에서 `--check`.
  2. 한 노드에서 root 로 `rsync -aH --numeric-ids --exclude=/.dms-registry-id <옛 SHARED_DIR>/dms/registry/ <새 SHARED_DIR>/dms/registry/`.
     **센티널은 복사에서 뺀다** — 통째로 복사하면 새 센티널이 옛 값으로 덮여 레지스트리가 「센티널 불일치」로 기동을 거부하고 S0
     `--check` 도 FAIL 한다. 대조는 §2d-4 4 의 find·md5sum 을 두 쪽에서 `! -path ./.dms-registry-id` 를 더해(65532 소유 그대로).
  3. values.env 의 `SHARED_DIR` 와 `SHARED_DIR_ID`(= 새 `<SHARED_DIR>/dms/.dms-shared-id`)를 고치고 `sh deploy/render.sh <site> --context C`.
  4. `sh deploy/overlays/guard-images.sh $R/registry --context C` → 기대 exit 5 → `ALLOW_HOSTPATH_CHANGE=1` 로 다시 → SSA 적용 →
     `rollout status`(새 파드가 새 경로·새 기대값으로 Ready 가 된 뒤 옛 파드가 내려간다). 확인: `logs deploy/dms-registry -c sentinel-check`
     가 「센티널 확인 (<새 id>)」, `/v2/<repo>/tags/list` 가 옮기기 전과 같다.
  5. 그다음 앱을 적용한다(§2d-4 5). 옛 `<옛 SHARED_DIR>/dms/registry` 는 되돌림 창 동안 지우지 않는다.

### 2e-6. k8s 테스트베드(`dmsk8s`) — T4·앱 전환 (2026-11-03 완료, d19)

- 값: `deploy/overlays/testbed-k8s/values.env` — `REGISTRY_MODE=in-cluster`·`REGISTRY=10.112.0.50:5000`(2026-11-03 T4 16 전환 — 그 전
  external·`pkg-01:5001`), `REGISTRY_CLUSTER_IP=10.112.0.50`(svc 10.112.0.0/12 하단, 미사용 실측)·`REGISTRY_PORT=5000`·`SHARED_DIR_ID`
  (= `/dms_shared/dms/.dms-shared-id`)·requests 50m/64Mi(설계 10 — T4 실측 RSS 43~50MB, 대량 pull 때 최고 약 66MiB 로 request 를 살짝 넘는다;
  limit 512Mi). `sh deploy/render.sh testbed-k8s` 가 `deploy/.rendered/testbed-k8s/platform/{priorityclass,registry}` 를 내고, 렌더된 `app/` 은
  커밋된 오버레이와 같다(테스트가 고정). 앱 적용은 지금처럼 `kubectl --context dmsk8s apply -k deploy/overlays/testbed-k8s`(§13-6).
- control-plane 은 cp1 하나라 S0-5 는 cp1 에만, S0-4 는 3노드, 인터넷 노드 역할은 빌드 노드 k8s-w1. 결과는 CHANGELOG ④ 「T4 실증」.
  앱이 이 레지스트리를 쓰므로 2e-5 「걷어내기」(`C`=dmsk8s, `R=deploy/.rendered/testbed-k8s/platform`)는 아래 되돌림 뒤에만 한다.
- **앱 전환**(T4 16 — 완료, 6287cd8; 첫 포탈 빌드 d19 = 35a10a3): `values.env`(`REGISTRY_MODE=in-cluster`, `REGISTRY=10.112.0.50:5000`)·
  오버레이 `newName`·`patch-config.yaml` 의 `DMS_JOB_IMAGE`·`DMS_BUILD_REGISTRY`·`DMS_BUILD_BUILDER_IMAGE` 를 한 커밋에서 바꾸고(렌더러가 다섯
  곳 대조 — 빌더도 같은 레지스트리여야 한다), `tests/test_testbed_k8s_overlay.py` 의 레지스트리 상수도 고친다 → guard exit 3 →
  `ALLOW_IMAGE_CHANGE=1` → apply → api·controller `rollout restart`(ConfigMap) → 포탈 릴리스로 **잡 이미지(`control_state.job_image`)를 새
  주소로 다시 릴리스**. 이후 포탈 빌드는 새 레지스트리로 push 하고 동봉 매니페스트가 새 주소로 스탬프돼 드리프트가 「일치」다(d19).
  되돌림 = 그 커밋을 되돌려 같은 순서로 — `pkg-01:5001` 은 되돌림용으로 남겨 뒀지만 d18 까지만 있다(d19 부터는 새 레지스트리에만 —
  태그를 d18 로 맞추거나 `skopeo copy` 로 옮긴 뒤).

## 2f. DB(CNPG) — 오퍼레이터(S9)·DB 층(S10)·백업·재활용·업그레이드·운영 규칙 (2026-11-03 ⑤)

설계 v6 6(6-1~6-6)·8-1(S-1·S2·S9·S10 「S10 세부」·「순서의 이유」)·8-2(CNPG 행·A 경로·층 배치)·10 T6/T6'/T7. 이 절은 dms-k8s 공통이고
테스트베드 판은 2f-8 이다. 설치기(§2h)의 S2·S9·S10 이 아래를 감지 상태 기계·자동 순서·판정으로 한다(2026-11-03 ⑦) — 아래는 손 판이다.
**DMS 애플리케이션 코드는 바뀌지 않았다**(접속 문자열만 바뀐다 — `connect_timeout` 은 코드 기본 5초 그대로, 6-3).

PG 는 CloudNativePG(#26) **1.30.1**(1.30.x 최신, 2026-09-23 — cosign 번들로 검증한 상류 원본 그대로, `deploy/addons/cnpg/1.30.1/SOURCE`)
위의 Cluster `dms-pg` 하나다: 인스턴스 3(마스터마다 1개 — required anti-affinity + control-plane toleration), 동기 `any 1 preferred`(#28),
PG 17 digest 고정(#27), 볼륨은 공용 디렉터리의 **정적 hostPath PV 풀**(`<SHARED_DIR>/dms/pgdata/dms-pgdata-01..05`, 인스턴스 3 + 여분 2 — #40).
로컬 디스크 PG 는 어느 렌더 오버레이에도 없다(#31 — 테스트가 고정).

### 2f-1. 구성·값

| 무엇 | 어디(base → 렌더) | 비고 |
|---|---|---|
| CNPG 오퍼레이터 | `deploy/addons/cnpg/<CNPG_VERSION>/`(상류 원본 + `SOURCE`·`*.sha256` + DMS 패치 층) → `.rendered/<site>/addons/cnpg` | 6-1 airgap 함정: 이미지와 env `OPERATOR_IMAGE_NAME`(= PG 인스턴스 파드의 bootstrap init 이미지 — 마스터가 받는다) **둘 다** `<REG>/cloudnative-pg:1.30.1@sha256:<amd64>`, `Always` → `IfNotPresent`, env `POSTGRES_IMAGE_NAME` = PG 이미지(imageName 을 생략한 Cluster 도 ghcr.io 로 가지 않게). replicas 2·워커(required nodeAffinity control-plane `DoesNotExist`, 두 파드 다른 노드 선호)·`dms-addon-critical`. 라벨 `app.kubernetes.io/managed-by=dms-install`·`dms.io/addon-version=1.30.1`(모든 객체, selector 는 상류 그대로). **`WATCH_NAMESPACE` 없음** = 모든 네임스페이스(dms 포함 — 8-2 「watch 범위에 dms 가 없으면 X」; 클러스터에 CNPG 는 하나만 둔다). 웹훅·RBAC·CRD·ns `cnpg-system` 은 상류 그대로(웹훅 `failurePolicy: Fail` — apiserver → 워커 파드 **9443**, 설계 #7) |
| SC + PV ×5 | `deploy/platform/db/volumes` → `platform/db-volumes` | SC `dms-pg-shared`(no-provisioner·Immediate·Retain·확장 불가). PV `dms-pgdata-0N`: hostPath `<SHARED_DIR>/dms/pgdata/dms-pgdata-0N` **`type: Directory`**, Retain, RWO, **nodeAffinity·claimRef 없음**, capacity = `PG_STORAGE_SIZE`. 디렉터리는 S0 가 26:26 0700 + `.dms-pv-id`(= PV 이름)로 만든다(hostPath 는 fsGroup 을 적용하지 않는다). ValidatingAdmissionPolicy `dms-pg-shared-pvc-namespace`(+ Deny 바인딩): 이 클래스의 PVC 는 ns `dms` 에서만(claimRef 없는 여분 PV 를 다른 네임스페이스 PVC 가 붙이지 못하게 — 2026-11-03 09b0652) |
| Cluster `dms-pg` | `deploy/platform/db/cluster` → `platform/db-cluster` | instances 3, imageName `<REG>/postgresql:17.11-202611020810-minimal-trixie@sha256:<amd64>`(IfNotPresent), `smartShutdownTimeout: 0`, `enablePDB`, `primaryUpdateMethod: switchover`, synchronous `any/1/preferred`, `shared_buffers` 1GB, bootstrap initdb `dms`/`dms` + Secret `dms-pg-app`, storage SC `dms-pg-shared` + size(= PV capacity) + `resizeInUseVolumes: false`, 1 CPU/4Gi(메모리 limit 4Gi, CPU limit 없음) |
| pg_dump CronJob `dms-pg-dump` | `deploy/platform/db/backup` → `platform/db-backup` | 매시간 `17 * * * *`, `-Fc`, `dms-pg-rw` 에서, 워커, uid 26, hostPath `<SHARED_DIR>/dms/backup/pg`(Directory), 센티널 `.dms-backup-id` **검사만**, 보존 개수 `PG_BACKUP_KEEP`(기본 336 = 14일), 접속 정보는 `dms-pg-app` secretKeyRef 뿐 |
| 경로 프로브 | `deploy/platform/db/probe` → `platform/db-probe` + `deploy/pg-path-probe.py` | 일회용 DaemonSet(모든 노드에 뜨되 **판정은 DMS 노드** — 마스터 + NoSchedule taint 없는 워커(설계 2-3), 그 밖은 정보만; `--nodes a,b` 로 바꾼다. uid 26, 읽기 전용) — S10 사전 검사·경로 대조. 상주하지 않는다. `--mode pv-check` 는 프로브 없이 이미 있는 SC·PV 를 render.env 와 대조한다(적용 전) |
| 버전·digest | `deploy/versions.env`(`CNPG_VERSION`·`CNPG_OPERATOR_IMAGE`·`PG_IMAGE` = lock DEST) + `deploy/images.lock` | 렌더러가 lock 에서 그 DEST 의 linux/amd64 매니페스트 digest 를 찾는다(없거나 둘이면 FAIL). 둘 다 커밋 |
| 시크릿 | **매니페스트 밖**(2f-2) | `dms-pg-app`(basic-auth, username `dms`)·`dms-secrets` 의 `DMS_DATABASE_URL` |

애드온 이미지의 레지스트리 = 클러스터 내 레지스트리(`REGISTRY_CLUSTER_IP` 가 있으면 `<IP>:<PORT>` — S5 가 lock 이미지를 그리로 push 한다, 앱이
external 이어도), 없으면 `REGISTRY`(`render.env` 의 `ADDON_REGISTRY`). `sh deploy/render.sh <site> --context C` 가 `addons/cnpg`·`platform/db-{volumes,
cluster,backup,probe}` 를 함께 낸다(`ADDON:`·`PLATFORM:`(S10 순서)·`PROBE:` 줄, `render.env` 의 `CNPG_OPERATOR_REF`·`PG_IMAGE_REF`·`DB_LAYERS`).

| values.env 키(전부 선택) | 기본 | 뜻 |
|---|---|---|
| `PG_STORAGE_SIZE` | `20Gi` | Cluster `storage.size` **와** PV capacity(한 값이 두 곳 — 크면 맞는 PV 가 없어 새 PVC 가 Pending). 명목값(hostPath 는 강제하지 않는다). PV 를 만든 뒤에는 바꾸지 않는다 — PV 용량은 가변이라 SSA 가 조용히 바꾸지만 CNPG 는 `storage.size` 축소를 거절해 둘이 어긋난다(재생성 PVC Pending). 그래서 2f-4 0단계 `pv-check` 가 라이브 PV 와 다르면 FAIL 한다 |
| `PG_CPU_REQUEST`·`PG_MEMORY`·`PG_SHARED_BUFFERS` | `1`·`4Gi`·`PG_MEMORY`/4 | 메모리는 requests == limits. `PG_SHARED_BUFFERS` 를 비우면 `PG_MEMORY` 의 1/4(둘 다 없으면 base 의 1GB = 4Gi/4) — shared_buffers 는 메모리 limit 안에서 채워지므로 `PG_MEMORY` 만 줄이고 1GB 를 남기면 부하 때 cgroup OOM 으로 재기동을 반복한다. 실효 메모리의 절반 이상이면 렌더 FAIL. 테스트베드 50m/256Mi/64MB(설계 10 축소, CPU 는 T7 실측 뒤 더 낮춤 — 2f-8) |
| `PG_PLACEMENT` | `control-plane` | 마스터마다 1개(#27). `all-nodes` 는 **테스트베드만**(control-plane 이 하나 — nodeSelector 를 빼고 toleration·required anti-affinity 는 둔다) |
| `PG_BACKUP_KEEP` | `336` | pg_dump 보존 개수(매시간 → 14일). **개수**로 센다 — 시각 기준이면 덤프가 며칠 실패하는 동안 좋은 덤프가 전부 지워질 수 있다 |

### 2f-2. 시크릿(S2) — `dms-pg-app`·`DMS_DATABASE_URL`(값을 출력하지 않는다)

`deploy/pg-secrets.py` 가 값을 화면·argv 에 싣지 않고(kubectl 에 stdin 으로만) 만든다. 이미 있는 `dms-pg-app` 은 덮어쓰지 않는다(Cluster 가
그 비밀번호로 만들어졌을 수 있다 — 한쪽만 바꾸면 인증이 깨진다).

```bash
python3 deploy/pg-secrets.py --context C ensure-app      # dms-pg-app(basic-auth, username dms, 무작위 48 hex — URL 인코딩 불필요)
python3 deploy/pg-secrets.py --context C init-secrets    # 새 사이트: dms-secrets 4키(URL = 아래 + 토큰 셋 무작위). 이미 있으면 바꾸지 않는다
python3 deploy/pg-secrets.py --context C set-url         # 기존 사이트(외부 PG → CNPG 이관): DMS_DATABASE_URL 만 바꾸고 옛 값은 dms-secrets-prev 에
python3 deploy/pg-secrets.py --context C restore-url     # 되돌림(URL 원복)
python3 deploy/pg-secrets.py --context C check           # 6-3 install 게이트 — host·포트·DB·사용자·비밀번호·필수 파라미터(어긋난 항목 이름만)
```

URL 모양(설계 6-3 — CNPG `-app` 시크릿의 `uri` 에는 파라미터를 붙일 수 없어 직접 조립한다):

```
postgresql://dms:<PW>@dms-pg-rw.dms.svc:5432/dms?sslmode=require&target_session_attrs=read-write
  &keepalives=1&keepalives_idle=10&keepalives_interval=5&keepalives_count=3&tcp_user_timeout=15000
```

- `<PW>` = `dms-pg-app` 의 password. keepalive·`tcp_user_timeout` 이 없으면 primary 가 있던 노드가 죽었을 때 API 가 최대 약 15분 매달린다(추정 —
  T6' 실증). `connect_timeout` 은 넣지 않는다(코드가 없을 때만 5초를 붙인다).
- 손으로 만들 때도 값은 stdin 으로: `printf 'username=dms\npassword=%s\n' "$(openssl rand -hex 24)" | kubectl --context C -n dms create secret
  generic dms-pg-app --type=kubernetes.io/basic-auth --from-env-file=/dev/stdin`(`printf` 는 셸 내장이라 argv 에 실리지 않는다).
- 시크릿은 api·controller 가 기동 때만 읽는다 — URL 을 바꾼 뒤 `kubectl --context C -n dms rollout restart deploy/dms-api deploy/dms-controller`.
- `set-url` 은 되돌림 URL 을 지킨다(2026-11-03 a22cb32 — T7 검토): 옛 값이 이미 이 클러스터의 `dms-pg-rw` 면(이관 뒤 재실행 — 비밀번호 교체 등)
  `dms-secrets-prev` 를 건드리지 않고, prev 에 지금과 다른 외부 URL 이 있으면 멈춘다(어느 쪽이 되돌림인지 확인한 뒤 `--overwrite-prev`).

**이관 순서**(외부 PG → 이미 떠 있는 `dms-pg`, 덤프·복원 — 2026-11-03 T7 실측, 앱 정지 창 약 3.5분):

1. 동결: DMS drain 켬(포탈 컨트롤 상태 — `PUT /api/admin/control-state`, 세션 관리자) → 진행 중 잡·빌드 0 확인 →
   `kubectl --context C -n dms patch cronjob dms-pg-dump --type=merge -p '{"spec":{"suspend":true}}'` → `get deploy dms-api dms-controller` 로 replicas 를
   적고 `scale deploy dms-api dms-controller --replicas=0` → **두 Deployment 의 파드가 모두 사라질 때까지** 기다린다(`get pod -l
   'app.kubernetes.io/name in (dms-api,dms-controller)'` 가 빔 — 종료 중인 파드가 원본에 쓰면 그 쓰기는 덤프에 없다).
2. 원본 덤프(`pg_dump -Fc --no-owner --no-acl`, 원본 세션은 `PGOPTIONS='-c default_transaction_read_only=on'` — 원본에 쓸 수 없게) → 대상 `dms` 의
   public 테이블이 0개인지 확인 → `dms` 사용자로 `pg_restore --no-owner --no-acl --exit-on-error --single-transaction -d dms`(실패하면 통째로
   되돌아 반쯤 복원된 DB 가 남지 않는다 — 재시도에 정리가 필요 없다) → `ANALYZE` → 원본·대상 대조(2f-5 「대조」 — 행 수·행 해시·시퀀스).
3. `python3 deploy/pg-secrets.py --context C set-url` → `check`(PASS).
4. `scale deploy dms-api dms-controller --replicas=<적은 값>` — 이미 0 이라 `rollout restart` 로는 뜨지 않는다. initContainer migrate 는 no-op.
5. CronJob `suspend: false` → `create job --from=cronjob/dms-pg-dump` 로 새 DB 의 덤프 확인 → DMS drain 끔.

되돌림은 `restore-url` → `rollout restart`(원본은 손대지 않는다 — 이관 뒤의 쓰기는 원본에 없으니 살리려면 `dms-pg` 덤프를 원본에 복원).

### 2f-3. CNPG 오퍼레이터(S9, A 경로) — 감지 → SSA → 롤아웃 → Cluster CR server dry-run

먼저 감지한다(설치기는 `install-detect.py` — 설계 8-2; 손으로는 아래): CNPG 가 이미 있으면(**C**) 다시 설치하지 않는다(둘이면 P — 이중 오퍼레이터).

```bash
kubectl --context C get crd clusters.postgresql.cnpg.io                                       # 없으면 A(설치), 있으면 아래로 판정
kubectl --context C get deploy -A -l app.kubernetes.io/name=cloudnative-pg \
  -o 'custom-columns=NS:.metadata.namespace,NAME:.metadata.name,BY:.metadata.labels.app\.kubernetes\.io/managed-by,V:.metadata.labels.dms\.io/addon-version,IMG:.spec.template.spec.containers[0].image'
#   BY=dms-install + V 같음 → D=(no-op) · V 낮음 → D<(업그레이드는 2f-6) · 남이 설치 → C(호환: 1.25 이상·지원 minor, env/ConfigMap 의 WATCH_NAMESPACE 가 dms 를 포함) · 둘 이상 → P(중단)
```

A 경로(S5 가 lock 의 `cloudnative-pg:1.30.1`·`postgresql:17.11-…` 를 레지스트리에 push 한 뒤 — §2e-4. **`REGISTRY_MODE=external`** 이고
`REGISTRY_CLUSTER_IP` 가 없으면 애드온 이미지도 사이트 레지스트리(`render.env` 의 `ADDON_REGISTRY` = `REGISTRY`)에서 받는다 — §2e-4 의 lock push
루프를 `IP:PORT` 대신 그 `REGISTRY` 로 돌리거나 그 레지스트리에 미리 미러한다 — 설치기 S5 가 그 `REGISTRY` 로 대조하고 push 명령을 보인다):

```bash
sh deploy/render.sh <site> --context C
RD=deploy/.rendered/<site>                                           # 사이트 렌더 디렉터리(§2e 의 R = …/platform 과 다르다 — 2026-11-03 ⑤ 리뷰 2)
# PriorityClass dms-addon-critical(오퍼레이터 파드가 쓴다) — in-cluster 면 S4(§2e-3)가 이미 적용했다. external 사이트는 §2e 를 따르지 않으므로
# 여기서 적용한다 — 없으면 Priority admission 이 파드 생성을 거절한다(파드 0개 → 아래 rollout status 시간 초과; 2026-11-03 ⑤ 리뷰 2):
kubectl --context C get priorityclass dms-addon-critical || kubectl --context C apply --server-side --field-manager=dms-install -k $RD/platform/priorityclass
sh deploy/overlays/guard-images.sh $RD/addons/cnpg --context C      # 재적용이면 오퍼레이터 이미지 변경(= CNPG 업그레이드)을 exit 3 으로 막는다
# 첫 설치(cnpg-system 없음)면 네임스페이스만 먼저 만든다 — server dry-run 은 같은 묶음의 Namespace 를 저장하지 않아 그 안의 객체마다
# `namespaces "cnpg-system" not found`(NamespaceLifecycle) 로 실패한다(상류 kubernetes#83562, 2026-11-03 ⑤ 리뷰). 라벨은 이어지는 SSA 가 붙인다.
kubectl --context C get ns cnpg-system || kubectl --context C create namespace cnpg-system
kubectl --context C apply --server-side --field-manager=dms-install --dry-run=server -k $RD/addons/cnpg
kubectl --context C apply --server-side --field-manager=dms-install -k $RD/addons/cnpg      # CRD 가 커서 client-side 는 annotation 한도를 넘는다
kubectl --context C -n cnpg-system rollout status deploy/cnpg-controller-manager --timeout=300s
kubectl --context C -n cnpg-system get pods -o wide                 # 2개, 워커 2대에 하나씩, IMAGE 가 레지스트리 주소(ghcr.io 아님)
kubectl --context C -n cnpg-system get events --field-selector reason=Pulling -o custom-columns=MSG:.message   # 받는 이미지가 레지스트리 주소뿐
# 9443 경로 실증(S9 판정): DMS 가 만들 Cluster CR 을 server dry-run — 웹훅(mutating·validating)이 실제로 불린다. 아무것도 만들지 않는다.
kubectl --context C apply --server-side --field-manager=dms-install --dry-run=server -k $RD/platform/db-cluster
```

| 증상 | 뜻 · 할 일 |
|---|---|
| dry-run `failed calling webhook "mcluster.cnpg.io" … context deadline exceeded / connection refused` | apiserver → 워커 파드 9443 이 막혔다(설계 #7 — 방화벽·NetworkPolicy·CNI). 오퍼레이터 파드가 Ready 인지 먼저 본다 |
| `rollout status` 시간 초과·cnpg-system 에 파드 0개 + `kubectl --context C -n cnpg-system describe rs` 이벤트 `FailedCreate … no PriorityClass with name dms-addon-critical was found` | PriorityClass 가 없다(external 사이트 — S4 를 거치지 않았다). 위 `get priorityclass … \|\| apply … $RD/platform/priorityclass` 를 돌리면 ReplicaSet 이 곧 다시 만든다 |
| 오퍼레이터 Pending + `FailedScheduling` | 워커 자리 부족 — 잡이 워커를 채웠으면 선점(`dms-addon-critical`)이 일어난다. NoSchedule taint 없는 워커가 있는지(required nodeAffinity: control-plane 아님) |
| `ErrImagePull … ghcr.io` | 렌더하지 않은 base 를 적용했다(자리표시자) 또는 lock 이미지 push(S5) 누락 — 렌더 결과(`$RD/addons/cnpg`)만 적용 |
| `no matches for kind "Cluster"` | CRD 가 아직 없다(오퍼레이터 층을 먼저) |
| addons dry-run `namespaces "cnpg-system" not found` | 첫 설치에서 네임스페이스를 먼저 만들지 않았다(위 `create namespace` — server dry-run 은 묶음 안의 Namespace 를 만들지 않는다) |

### 2f-4. DB 층(S10) — SC + PV ×5 → 사전 검사 → Cluster → 900초 폴링 → 경로 대조 → CronJob → 첫 덤프

```bash
RD=deploy/.rendered/<site>
# 0) 이미 있는 SC·PV 가 렌더와 다르면 멈춘다(조용히 다시 만들지 않는다) — **적용 전에**: apiserver 가 막는 것은 PV 의 hostPath·volumeMode 와
#    SC 의 provisioner·reclaimPolicy·volumeBindingMode 뿐이고, PV 의 capacity·storageClassName 은 가변이라 SSA 가 Bound PV 까지 조용히 바꾼다
#    (k8s 1.34 ValidatePersistentVolumeUpdate — 2026-11-03 ⑤ 리뷰). 경로가 <SHARED_DIR>/dms/pgdata/ 밖이어도 FAIL. 없는 것은 INFO(적용이 만든다).
python3 deploy/pg-path-probe.py --context C --rendered $RD/platform/db-probe --mode pv-check      # 0 PASS·1 FAIL·2 사용법 — 읽기만(프로브를 띄우지 않는다)
#    손 대조(같은 것): 아래 CAP·SC·PATH 가 render.env 의 PG_STORAGE_SIZE·dms-pg-shared·<SHARED_DIR>/dms/pgdata/<이름> 과 같아야 한다
grep -E '^(SHARED_DIR|PG_STORAGE_SIZE)=' $RD/render.env
kubectl --context C get pv dms-pgdata-01 dms-pgdata-02 dms-pgdata-03 dms-pgdata-04 dms-pgdata-05 --ignore-not-found \
  -o 'custom-columns=NAME:.metadata.name,PHASE:.status.phase,CAP:.spec.capacity.storage,SC:.spec.storageClassName,PATH:.spec.hostPath.path,TYPE:.spec.hostPath.type'
kubectl --context C get sc dms-pg-shared --ignore-not-found -o 'custom-columns=PROV:.provisioner,RECLAIM:.reclaimPolicy,BIND:.volumeBindingMode'
# 1) SC + PV ×5
kubectl --context C apply --server-side --field-manager=dms-install --dry-run=server -k $RD/platform/db-volumes
kubectl --context C apply --server-side --field-manager=dms-install -k $RD/platform/db-volumes
# 2) 사전 검사 — DMS 노드(마스터 + NoSchedule taint 없는 워커 — 설계 2-3; 그 밖의 노드는 정보만, `--nodes a,b` 로 바꾼다)마다 PV 디렉터리
#    26:26 0700·.dms-pv-id == PV 이름·Available PV 에 옛 PGDATA 없음(약 1~2분). 첫 줄들에 판정 대상·판정 밖 노드 목록이 나온다.
#    어느 모드든 PV 안의 옛 PGDATA 잔재 `pgdata_*`(CNPG 가 옮겨 둔 것 — 2f-5 재활용 런북)는 WARN(2026-11-03 T6)
python3 deploy/pg-path-probe.py --context C --rendered $RD/platform/db-probe --mode precheck       # 0 PASS·3 WARN·1 FAIL·4 모름
# 3) 시크릿(2f-2)이 있는지 → Cluster
kubectl --context C -n dms get secret dms-pg-app >/dev/null
kubectl --context C apply --server-side --field-manager=dms-install --dry-run=server -k $RD/platform/db-cluster
kubectl --context C apply --server-side --field-manager=dms-install -k $RD/platform/db-cluster
```

4) **900초 폴링** — `kubectl wait --for=condition=Ready` 는 쓰지 않는다(인스턴스가 **모두** healthy 일 때만 서는 조건이라(2026-11-03 T6 실측 —
switchover 중에도 `ClusterIsNotReady`) 하나가 늦으면 끝없이 기다린다). 세 조건이 모두 서면 「DB 사용 가능」이다:

```bash
for i in $(seq 1 90); do
  ready=$(kubectl --context C -n dms get cluster dms-pg -o jsonpath='{.status.readyInstances}')
  prim=$(kubectl --context C -n dms get cluster dms-pg -o jsonpath='{.status.currentPrimary}')
  eps=$(kubectl --context C -n dms get endpoints dms-pg-rw -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null)
  pr=$( [ -n "$prim" ] && kubectl --context C -n dms get pod "$prim" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')
  echo "$(date +%T) readyInstances=${ready:-0} primary=${prim:--} rw=${eps:--} primaryReady=${pr:--}"
  [ "${ready:-0}" -ge 1 ] && [ -n "$eps" ] && [ "$pr" = True ] && break
  sleep 10
done
kubectl --context C -n dms get cluster dms-pg; kubectl --context C -n dms get pvc,pod -l cnpg.io/cluster=dms-pg -o wide
```

판정(S10): readyInstances 3 → PASS, 1~2 → **WARN**(모름 ≠ 실패 — 나머지는 join 중일 수 있다), 0 이거나 900초 초과 → **FAIL**(Cluster `status.phase`·
`kubectl --context C -n dms describe cluster dms-pg`·인스턴스 파드 이벤트). Available 여분 PV < 1 → WARN. 테스트베드 실측(NFS, 2026-11-03 T6): 첫
primary 약 135초(initdb 67초 + template SQL 43초 — fsync 비용), 3/3 Ready 6분 31초.

```bash
# 5) 경로 대조 — Bound PV 마다 모든 DMS 노드(곧 인스턴스가 아닌 노드도)가 같은 <PV 경로>/pgdata/PG_VERSION 을 보는지. 못 보면 FAIL
#    「PG 가 공용 경로가 아닌 곳에 씀」(마운트 빠진 노드의 로컬 디스크 — #31 의 조용한 위반). 파드 안에서 open 으로 60초까지 재시도(S7 교차 노드 원칙).
#    Cluster status.initializingPVC 에 있는 PV(initdb·join — pg_basebackup 중)는 대조하지 않고 UNKNOWN(종료 4) — readyInstances 가 3 이 된 뒤 다시.
python3 deploy/pg-path-probe.py --context C --rendered $RD/platform/db-probe --mode contrast
# 6) CronJob → 첫 덤프(S0 를 ⑤ 전에 돌린 트리는 backup/pg/.dms-backup-id 가 없다 — S0 를 다시 돌리면 그것만 만든다: sh deploy/shared-dir-init.sh <SHARED_DIR>)
kubectl --context C apply --server-side --field-manager=dms-install -k $RD/platform/db-backup
kubectl --context C -n dms create job --from=cronjob/dms-pg-dump dms-pg-dump-first
# 끝날 때까지(성공·실패 둘 다, 최대 600초). `wait --for=condition=complete` 는 쓰지 않는다 — Job 이 Failed 가 되어도 --timeout 까지 기다린다
# (--for 는 하나만 받는다; 가장 흔한 첫 실패 「센티널 없음」이 10분 뒤 timed out 으로만 보였다 — 2026-11-03 ⑤ 리뷰 2). 1.31+ 는 Complete·Failed
# 앞에 SuccessCriteriaMet·FailureTarget 이 먼저 선다 — 단어로 맞춘다.
for i in $(seq 1 120); do
  st=$(kubectl --context C -n dms get job dms-pg-dump-first -o jsonpath='{range .status.conditions[?(@.status=="True")]}{.type} {end}')
  case " $st" in *" Complete "*) echo PASS; break ;; *" Failed "*) echo FAIL; break ;; esac
  [ "$i" -lt 120 ] || echo "FAIL(600초 — describe job/dms-pg-dump-first)"
  sleep 5
done
kubectl --context C -n dms logs job/dms-pg-dump-first                 # 「dms-pg-dump: dms-<UTC>.dump (N bytes)」·「완료」 — FAIL 이면 마지막 줄이 사유(아래 표)
```

| 증상 | 뜻 · 할 일 |
|---|---|
| 인스턴스 파드 `FailedMount … hostPath type check failed` | 그 노드에 `<SHARED_DIR>/dms/pgdata/dms-pgdata-0N` 이 없다(마운트 없음·S0 미실행). 경로를 손으로 만들지 않는다(G2) |
| initdb Job 이 반복 실패 — 로그 `Error while checking for an existing data directory … stat …/pgdata: permission denied` | PV 디렉터리가 26:26 0700 이 아니다(S0 `--check`·사전 검사 — 2026-11-03 T6 의 0:0 음성 대조 문구). hostPath 는 fsGroup 을 적용하지 않는다 |
| 새 PVC 가 Pending — 이벤트 `ProvisioningFailed: no volume plugin matched name: kubernetes.io/no-provisioner` | 맞는 PV 가 없다(정적 풀이라 프로비저너가 없다는 문구로 나온다): Available 여분이 없다(Released 는 2f-5 런북) 또는 `storage.size` > PV capacity |
| 다른 네임스페이스의 `dms-pg-shared` PVC 생성이 `Forbidden`(「네임스페이스 dms 에서만」) | 정상 — VAP `dms-pg-shared-pvc-namespace`(2f-1). 클래스 없이 `volumeName` 으로 고른 PVC 는 클래스가 달라 `VolumeMismatch` 로 Pending |
| 덤프 「센티널 없음」 | 그 워커의 `<SHARED_DIR>/dms/backup/pg` 에 `.dms-backup-id` 가 없다 — 마운트 빠진 노드의 로컬 디렉터리이거나 S0 를 ⑤ 전에 돌렸다(S0 재실행) |
| 덤프 `password authentication failed` | `dms-pg-app` 과 Cluster 의 dms 역할 비밀번호가 다르다(시크릿을 Cluster 뒤에 다시 만들었다) |

### 2f-5. 백업·복구·Released PV 재활용 (설계 6-4·6-2)

- 층: 동기 복제(노드·파드 장애 1건) · pg_dump 매시간(잘못된 마이그레이션·삭제·재구축) · **공용 스토리지 밖 사본**(공용 스토리지 손실 — 복제본 3개와
  덤프가 같은 스토리지에 있다). PITR 은 없다(#29).
- **공용 스토리지 밖 사본**(보관처는 U6·S5 — 사이트 결정): 덤프(`<SHARED_DIR>/dms/backup/pg/dms-*.dump`)와 시크릿 `dms-secrets`·`dms-pg-app` 을
  **암호화해서** 둔다(덤프에는 비밀번호 해시와 봉인된 키가 있다). 시크릿은 값을 화면에 내지 않고 파일로 바로 암호화한다:
  `kubectl --context C -n dms get secret dms-secrets dms-pg-app -o yaml | gpg --symmetric --cipher-algo AES256 -o dms-secrets-$(date -u +%Y%m%d).yaml.gpg`
  (덤프 복사는 백업 노드에서 root 로 `rsync -a --numeric-ids <SHARED_DIR>/dms/backup/pg/ <밖 경로>/`). 현장에서 빌드한 DMS 이미지 태그도 레지스트리
  밖 사본에 더한다(§2e-5 「백업」).
- **복구**(**같은 이름 `dms-pg`** 로 새로 만들고 `pg_restore` — 다운그레이드·제자리 복원에 기대지 않는다). 이름을 바꾸지 않는 이유(2026-11-03 ⑤
  리뷰 2): pg_dump CronJob 의 `PGHOST=dms-pg-rw`·경로 프로브·`pg-secrets.py`(URL·`check`)·⑦ 설치기·S12 가 모두 `dms-pg` 를 본다 — 새 이름으로
  복구하면 옛 Cluster 를 남겼을 때 매시간 덤프가 **버린 옛 DB** 를 「완료」로 받고(경보 없음 — 보존 개수가 차면 좋은 덤프가 밀려난다) 새 라이브 DB
  의 덤프는 하나도 생기지 않는다. 같은 이름이어도 옛 데이터와 섞이지 않는다 — Cluster 를 지우면 PVC 가 함께 지워지고(PVC 에 Cluster ownerReference —
  2026-11-03 T6 실측, 약 10초) Released PV 는
  claimRef 의 옛 UID 때문에 새 PVC 에 붙지 않는다(옛 PGDATA 는 그 PV 디렉터리에 남는다 — 재활용 런북이 `.retired` 로 옮겨 보관한다).
  1. 쓰기를 멈춘다: `kubectl --context C -n dms get deploy dms-api dms-controller` 로 replicas 를 적고 `kubectl --context C -n dms scale deploy
     dms-api dms-controller --replicas=0` — 앱이 빈 새 DB 에 먼저 붙으면 migrate 가 스키마를 만들어 `pg_restore` 와 겹친다.
  2. 복원할 덤프를 고른다(`<SHARED_DIR>/dms/backup/pg/dms-<UTC>.dump` — 옛 DB 가 읽히고 손상 전이면 `create job --from=cronjob/dms-pg-dump` 로
     하나 더 뜨고, 잘못된 마이그레이션·삭제가 원인이면 그 전 시각의 것). 공용 스토리지를 잃었으면 밖 사본에서 되살린다(S0 → 2f-4 의 `db-volumes` 부터).
  3. `kubectl --context C -n dms delete cluster dms-pg` → PVC 가 지워지고(T6 실측 약 10초) PV 3개가 Released. `kubectl --context C -n dms get pvc -l
     cnpg.io/cluster=dms-pg` 가 비어 있는지 본다 — 그래도 남은 PVC 가 있으면 지운다: 같은 이름의 새 Cluster 가 옛 PGDATA 째 다시 붙잡을 수
     있다(추정).
  4. 2f-4 3)의 `db-cluster` SSA(같은 렌더, `dms-pg-app` 그대로 — URL·비밀번호가 바뀌지 않는다) → 2f-4 4) 900초 폴링. 여분 PV 2개로 두 인스턴스가
     뜨고 셋째는 Pending 이다 — Released PV 에 아래 재활용 런북을 돌리면 뜬다(복원과 병행해도 된다).
  5. `P=$(kubectl --context C -n dms get cluster dms-pg -o jsonpath='{.status.currentPrimary}')` →
     `kubectl --context C -n dms exec -i "$P" -c postgres -- pg_restore --no-owner --role=dms -d dms < <덤프>`(덤프는 stdin 으로).
  6. `kubectl --context C -n dms scale deploy dms-api dms-controller --replicas=<적은 값>`(migrate 는 no-op) → `python3 deploy/pg-secrets.py
     --context C check`(PASS — URL 은 그대로 `dms-pg-rw`) → 2f-4 6) 처럼 `create job --from=cronjob/dms-pg-dump` 로 새 DB 의 첫 덤프를 확인한다.
  복원 조건: **원래의
  `DMS_SESSION_SECRET`**(없으면 메일 키·k8s 토큰을 다시 넣는다 — `mail_config.py`), DB 와 아티팩트의 시점이 어긋나면 404·고아 디렉터리가
  생긴다(`artifact_trash.py`). **첫 이관(외부 PG → CNPG)은 CNPG 「initdb import」**(설계 6-4): Cluster 를 `bootstrap.initdb.import`(type
  `microservice`, `databases: [<원본 DB>]`, `externalClusters` 에 원본 접속 — 비밀번호는 `secretKeyRef`)로 **만들 때** 원본을 읽어 들인다.
  bootstrap 은 Cluster 를 만들 때 한 번뿐이라 이미 initdb 로 뜬 Cluster 에는 쓸 수 없다 — 이관할 사이트는 S10 의 `db-cluster` 대신 import
  bootstrap 으로 만들거나(⑦ 설치기 몫 — 이름은 그대로 `dms-pg`: 위 복구와 같은 이유), 이미 떠 있으면 덤프·복원(2f-2 「이관 순서」)으로 같은 결과를 낸다. 테스트베드
  (2026-11-03): T6 이 일회용 Cluster 로 import 를, T7 이 이미 떠 있던 `dms-pg` 로 덤프·복원을 실증했다 — 둘 다 행 수·md5 일치(2f-8).
- **대조**(복원·이관 뒤 — 원본과 대상에서 같은 SQL, 결과를 diff): 세션 표시 설정을 양쪽 같게 고정하고(`SET TimeZone='UTC'; SET DateStyle='ISO, MDY';
  SET IntervalStyle='postgres'; SET extra_float_digits=1; SET bytea_output='hex'` — 행 텍스트 표현이 같아야 해시가 같다) public 테이블마다
  `SELECT count(*), md5(coalesce(string_agg(h, '' ORDER BY h COLLATE "C"), '')) FROM (SELECT md5(t::text) AS h FROM public.<표> t) s`, 시퀀스는
  `SELECT sequencename, last_value FROM pg_sequences WHERE schemaname = 'public' ORDER BY 1`. **행 텍스트를 통째로 `string_agg` 하지 않는다** — 그
  백엔드가 테이블 크기만큼 메모리를 쓴다(T7 1회차: agent_reports 54MB → 대상 primary 백엔드 183MB, `PG_MEMORY` 256Mi cgroup OOM → 장애조치).
  행 해시(32자)만 모으면 4만 행에 약 1.3MB 다.
- **Released 재활용 런북**(6-2 — 인스턴스를 볼륨째 다시 만든 뒤 옛 PV 가 Released 로 남았을 때. 여분이 2개라 급하지 않다):
  0. **실행하는 노드에서** `[ "$(findmnt -n -o TARGET -T <SHARED_DIR>)" != / ] && [ -f <SHARED_DIR>/dms/.dms-shared-id ]` — 아니면 중단(마운트가
     빠진 노드에서 디렉터리를 만들면 그 노드의 `Directory` 검사가 통과해 PG 가 로컬 디스크에 쓴다).
  1. 그 PV 를 쓰는 claim 이 없는지 — **PV 기준으로** 본다(2026-11-03 ⑤ 리뷰 2):
     `kubectl --context C get pv dms-pgdata-0N -o jsonpath='{.status.phase} {.spec.claimRef.name} {.spec.claimRef.uid}{"\n"}'` 가 `Released` 이고,
     `kubectl --context C get pvc -A -o jsonpath='{range .items[*]}{.metadata.namespace}/{.metadata.name} {.spec.volumeName}{"\n"}{end}' | grep ' dms-pgdata-0N$'`
     가 비어 있다(어떤 PVC 도 이 PV 에 묶여 있지 않다). **같은 이름의 새 PVC·파드가 있는 것은 정상이다** — CNPG 1.30 은 가장 낮은 빈 인스턴스
     번호를 재사용해 PVC 이름이 늘 `dms-pg-1..3` 이다; 그 PVC 의 uid 는 claimRef.uid 와 다르고 volumeName 은 다른 PV 다. Released = 그 claim(UID)의
     PVC 가 지워졌다는 뜻이고, PVC 는 쓰는 파드가 있는 동안 지워지지 않으므로(pvc-protection) 옛 claim 을 마운트한 파드는 없다. 단 옛 인스턴스 파드를
     **강제 삭제**(`--force --grace-period=0`)했던 경우는 그 노드 VM 이 꺼졌는지 먼저 확인한다(2f-7 — 옛 PG 가 같은 PGDATA 를 아직 열고 있을 수 있다).
  2. 같은 FS 안에서 옛 디렉터리를 보관 위치로 옮기고 빈 디렉터리를 새로 만든다 — **상위 디렉터리는 만들지 않는다**(`mkdir -p`·`install -d` 금지):
     ```bash
     sudo mv <SHARED_DIR>/dms/pgdata/dms-pgdata-0N <SHARED_DIR>/dms/pgdata/.retired/dms-pgdata-0N-$(date -u +%Y%m%dT%H%M%SZ)
     sudo mkdir -m 0700 <SHARED_DIR>/dms/pgdata/dms-pgdata-0N && sudo chown 26:26 <SHARED_DIR>/dms/pgdata/dms-pgdata-0N
     printf 'dms-pgdata-0N\n' | sudo tee <SHARED_DIR>/dms/pgdata/dms-pgdata-0N/.dms-pv-id >/dev/null   # root:root 0644(umask 022)
     sudo sh deploy/shared-dir-init.sh --check <SHARED_DIR>                                               # 모양·센티널 PASS
     ```
     옛 PGDATA 가 남은 채 새 인스턴스가 붙어도 **실패하지 않는다** — CNPG 1.30 은 그것을 같은 PV 안 `pgdata_<UTC>` 로 옮기고 새로 시작해 옛
     데이터가 조용히 남는다(2026-11-03 T6 실측 — 설계의 「join 실패」 추정 정정). 그래서 이 단계를 건너뛰어도 드러나지 않는다: 경로 프로브가
     어느 모드든 `pgdata_*` 잔재를 WARN 하면 내용을 확인한 뒤 같은 FS 의 `pgdata/.retired/` 로 옮긴다. `.retired/` 의 옛 PGDATA 는 덤프 밖의
     유일한 사본일 수 있으니 보존 기한을 정해 지운다(용량 3-3).
  3. claimRef 를 지워 Available 로: `kubectl --context C patch pv dms-pgdata-0N --type=json -p '[{"op":"remove","path":"/spec/claimRef"}]'`
  - **Cluster 삭제**는 CNPG 가 PVC 를 함께 지운다(PVC 에 Cluster ownerReference — 2026-11-03 T6 실측: 약 10초 안에, 일회용 인스턴스 2·PV 3 에서
    PV 2개 Released + 1개 Available) → PV 3개가 Released 가 되고 여분은 2개뿐이라
    다시 만든 Cluster(같은 이름 `dms-pg` — 위 복구)의 셋째 인스턴스는 런북을 돌린 뒤에 뜬다. 옛 PGDATA 는 지우지 않고 `.retired` 로 옮겨 보관한다.

### 2f-6. 업그레이드 (설계 6-5 — 연 2회 이상)

| 경우 | 절차 |
|---|---|
| CNPG minor | ① `deploy/addons/cnpg/<새 v>/`(상류 원본 + SOURCE(cosign 검증)·sha256 + 패치 층 — 라벨·자리표시자 태그) · `deploy/versions.env` · `deploy/images.lock`(`images-lock.py resolve cloudnative-pg:<v> ghcr.io/cloudnative-pg/cloudnative-pg:<v>`)을 **한 커밋**에서, 옛 디렉터리는 지운다(테스트가 하나뿐인지 본다) ② 인터넷 노드가 새 이미지를 push(S5) ③ 테스트베드 리허설 ④ **minor 는 하나씩**(건너뛰기 금지)·지원 범위(K8s minor) 확인 → 직전 덤프(`create job --from=cronjob/dms-pg-dump`) → 렌더 → `guard-images.sh $RD/addons/cnpg` 가 exit 3 → `ALLOW_IMAGE_CHANGE=1` 로 SSA → 오퍼레이터 롤아웃 → PG 인스턴스 롤링·switchover 완료 대기(그동안 수 초 500). 설치기 연결(`install.sh --upgrade-addon cnpg`)은 ⑦ |
| PG minor | `versions.env` 의 `PG_IMAGE` + lock 줄(날짜 붙은 새 태그) → push → 렌더 → `db-cluster`·`db-backup`·`addons/cnpg`(POSTGRES_IMAGE_NAME) SSA → CNPG 가 standby 부터 롤링하고 primary 는 switchover(`primaryUpdateMethod: switchover` — 새 이미지를 이미 받은 인스턴스가 승격된다). **imageName 과 `postgresql.parameters`(예: `PG_SHARED_BUFFERS`)를 한 번에 바꾸지 않는다** — switchover 모드에서는 웹훅이 거절한다(상류 rolling_update 문서): 이미지 먼저, 롤링이 끝난 뒤 설정 |
| PG major | 별도 설계(지금 없음) |
| K8s minor | 신·구 K8s 를 모두 지원하는 버전으로 애드온을 먼저 올린다(1.30.x = K8s 1.34~1.36). CRI-O minor 도 함께 |
| 남이 설치한 CNPG(C) | 업그레이드는 소유자 몫 — DMS 는 EOL·지원 범위를 경고만(감지 C! — 설치·verify 때마다) |
| 롤백 | 다운그레이드에 기대지 않는다 — 같은 이름 `dms-pg` 로 다시 만들고 `pg_restore`(2f-5 복구) |

`kubectl cnpg` 플러그인(switchover·destroy 등)은 인터넷 노드가 릴리스의 `kubectl-cnpg_<v>_linux_x86_64.tar.gz` 를 `cnpg-<v>-checksums.txt` 와
대조해 받아 설치 호스트로 옮긴다(수단은 K8). switchover = `kubectl cnpg promote dms-pg <인스턴스>`(플러그인 권장 — 플러그인이 하는 일은
Cluster `status.targetPrimary` 패치와 phase `Switchover in progress` 다: 플러그인이 없을 때 `kubectl --context C -n dms patch cluster dms-pg
--subresource=status --type=merge -p '{"status":{"targetPrimary":"dms-pg-2"}}'` — 이때 phase 는 바뀌지 않으니 `status.currentPrimary` 가
`targetPrimary` 와 같아질 때까지를 진행 중으로 본다). 2026-11-03 T6 실측: 둘 다 약 9초, 옛 primary 의 RESTARTS +1 은 정상.

### 2f-7. 마스터 운영 규칙(6-6)·장애조치 시간(6-3)

- **VM 전원이 꺼진 것을 확인하기 전에는** 파드 강제 삭제(`--force --grace-period=0`), `node.kubernetes.io/out-of-service` taint, Node 객체 삭제를
  하지 않는다 — 공용 경로 hostPath PV 는 k8s 가 노드 간 RWO 를 강제하지 않아, 옛 노드의 PG 가 살아 있으면 **같은 PGDATA 를 두 서버가 연다**(DB 손상,
  위험 #4).
- 마스터 교체: 옛 VM 을 끈 다음에 Node 를 지운다. drain 은 한 대씩, 다음 대로 넘어가기 전에 `kubectl --context C -n dms get cluster dms-pg` 가
  healthy(readyInstances 3)인지 본다. 그 마스터가 primary 면 먼저 switchover.
- 공용 스토리지 유지보수(3-2 G2 보충): 그 노드의 DMS 상태 파드(api·controller·registry·CNPG 인스턴스·pg_dump)를 먼저 비우고(primary 면 switchover
  뒤 cordon + 파드 삭제) → 재마운트 → uncordon.

| 장애조치 상황(PG 검토 4-2 — 「실측」 표시 밖은 추정) | 쓰기 불가 시간 |
|---|---|
| switchover | 약 7초 API 5xx(2026-11-03 T7 실측 — 앱이 CNPG 위에서; CNPG 의 switchover 완료는 약 9초, T6) |
| PG 파드 재시작·노드 이동 | 수 초~십수 초(primary 면 그동안 API 500). 인스턴스가 다른 노드에서 같은 PV 로 다시 뜨기까지 22초(T6 실측 — cordon + 파드 삭제, pg_basebackup 없음) |
| primary 가 있던 마스터 사망 | 약 1~1.5분(노드 판정 약 50초 → 승격 — T6') |
| dms-api 가 있던 마스터 사망 | 약 1.5~2.5분 — 새 마스터에 dms 이미지가 캐시돼 있다는 전제(prod 오버레이의 not-ready·unreachable 30초 + S11 사전 pull). 레지스트리도 같은 마스터였으면 수 분 더(7-6) |

dms-controller 는 DB 연결을 잃으면 exit 1 로 끝나 재시작한다(T7 switchover 실측 — 정상, 곧 다시 붙는다). 짧은 사이에 거듭 잃으면 kubelet
재시작 백오프가 쌓여 복귀가 DB 보다 늦을 수 있다(T6' 에서 본다).
keepalive·`tcp_user_timeout`(2f-2 URL)이 없으면 API 가 최대 약 15분 매달린다(추정, T6' 실증). prod 오버레이는 dms-api·dms-controller 를 마스터에
둔다(`deploy/overlays/prod/patch-placement.yaml` — nodeSelector control-plane + toleration + not-ready·unreachable `tolerationSeconds: 30`, #24·#25;
테스트베드는 cp1 메모리가 모자라 워커 그대로).

### 2f-8. k8s 테스트베드(`dmsk8s`) — T6 (CNPG 실증)·T6'·T7

- 값: `deploy/overlays/testbed-k8s/values.env` — `PG_CPU_REQUEST=50m`·`PG_MEMORY=256Mi`·`PG_SHARED_BUFFERS=64MB`(설계 10 축소 — 64MB 는 PG_MEMORY/4 기본과 같다), `PG_PLACEMENT=all-nodes`
  (control-plane 이 cp1 하나 — 인스턴스를 cp1·w1·w2 에 하나씩; 이 오버레이만), `PG_BACKUP_KEEP=24`(호스트 `/data` 여유가 작다). 오퍼레이터는
  w1·w2, 이미지는 클러스터 내 레지스트리 `10.112.0.50:5000`(빌드 노드 k8s-w1 에서 lock 으로 push). PV 디렉터리 5개는 T3 의 S0 가 이미 만들었다
  (`/dms_shared/dms/pgdata/dms-pgdata-0N` 26:26 0700 + `.dms-pv-id`); `backup/pg/.dms-backup-id` 는 ⑤ 에서 새로 생긴 센티널이라 S0 를 한 번 더 돈다.
- **용량**(2026-11-03 T7 실측): ⑤ 뒤 워커마다 +200m(PG 인스턴스 100m + 오퍼레이터 100m) — dms-controller 가 어느 워커에 있느냐에 따라 2노드
  dscan gang(워커 1000m + launcher 100m)이 5m 모자라 Volcano 에서 대기했다 → `PG_CPU_REQUEST` 100m → 50m(CPU limit 은 없다). 라이브 반영은
  `db-cluster` SSA(requests 변경 = 인스턴스 롤링 + switchover — 5xx 수 초).
- **DMS 앱 DB 는 T7(2026-11-03)부터 `dms-pg`** 다 — 2f-2 「이관 순서」 그대로(앱 정지 창 3분 29초, 테이블 37·행 42,951·시퀀스 7 일치, switchover 5xx
  약 7초; 결과는 CHANGELOG ⑤ 「T7」). pkg-01 의 외부 PG(`dmsdb_k8s`/`dmsapp_k8s`)는 손대지 않고 되돌림용으로 둔다(`dms-secrets-prev` →
  `restore-url`). 장애 시험 **T6'**(전원 차단·drain 0 대 180·apiserver 불통·export 차단·재마운트·fio/pgbench 공용 대 로컬)은 사용자 승인 뒤
  별도 단계다.
- 실행 런북(메인 세션 몫 — 정확한 명령·확인 항목·되돌림; 2026-11-03 실증 끝, 결과는 CHANGELOG ⑤ 「T6 실증」): 설계 10 「T6 세부」의 순서 — push(k8s-w1 skopeo) → 렌더 → PriorityClass(④ 에서
  있음) → `addons/cnpg` → 9443 dry-run → `db-volumes` → 사전 검사 → `db-cluster` → 900초 폴링 → 경로 대조 → `db-backup` + 첫 덤프 → 확인(airgap
  함정 셋·커밋 지연·26:26 음성 대조(별도 일회용 Cluster)·PV 순환·Cluster 삭제·재생성(일회용 — 인스턴스 2·PV 3: 삭제 → PV 2개 Released →
  재생성 때 둘째 인스턴스 Pending → 재활용 런북 → 기동)·노드 이동·switchover·덤프 복원·initdb import(일회용 Cluster — 행 수·md5 대조)).
  S10 0단계 `pv-check` 는 `db-volumes` 적용 전에, 첫 설치의 `addons/cnpg` server dry-run 전에는 `create namespace cnpg-system`(2f-3).
- **걷어내기**(`RD=deploy/.rendered/testbed-k8s` — 2f-3·2f-4 와 같은 뜻, §2e 의 `R`(…/platform)과 다르다). T7 뒤로는 앱 DB 다 — 먼저 앱을
  외부 PG 로 되돌린다(DMS drain → `pg-secrets.py --context dmsk8s restore-url` → api·controller `rollout restart` → drain 끔; 그 뒤 `check` 는
  FAIL 이 맞다 — CNPG URL 이 아니다. 이관 뒤 쓰기를 살리려면 `dms-pg` 덤프를 원본에 먼저 복원). 그다음 `kubectl --context dmsk8s delete -k $RD/platform/db-backup` →
  `kubectl --context dmsk8s -n dms delete cluster dms-pg`(PVC 가 함께 지워지면 PV 3개가 Released — 2f-5 런북으로 되돌린다) →
  (다시 시작하지 않을 때만) `delete -k $RD/platform/db-volumes`(PV·SC — 디렉터리는 남는다) → `delete -k $RD/addons/cnpg`(CRD 를 지우면 그 종류의
  모든 객체가 지워진다 — Cluster 가 0개인지 먼저 확인). `dms-pg-app` 은 Cluster 를 다시 만들 때 그대로 쓴다.

## 2g. 입구(Envoy Gateway) — 마스터 sysctl(S0-5)·S8 설치·Avi VS-443·PROXY 컷오버·검증 (2026-11-03 ⑥)

설계 v6 #5·#13~#15(#14 「EG 기본값 세부」)·4-2·4-3·5 「원 IP 기록」·8-1(S0-5·S8·S12)·8-2(Gateway API·EG 행)·10 T8. **prod 의 포탈 입구는 이것
하나다** — MetalLB·ingress-nginx 를 쓰지 않는다(prod 오버레이가 공용 base 의 Ingress·MetalLB 풀을 `$patch: delete` 로 지운다 —
`deploy/overlays/prod/patch-remove-ingress.yaml`). testbed-k8s 는 Ingress + MetalLB VIP 10.20.20.101 을 사용자 경로로 두고 Gateway 를 병행한다(T8 실증 뒤에도 — 영구 컷오버는 testbed IaC, 2g-7).
설치기(§2h)의 S8·S12 가 아래를 감지 상태 기계(A 면 설치·C 면 platform/gateway 만)·마스터 sysctl·Programmed·마스터마다 :443 으로 한다 — install.sh 의
MetalLB·ingress 게이트는 없앴다(2026-11-03 ⑦, 설계 12 위험 19 해소). 아래는 손 판이다.

```
사내 PC ─https://<FQDN>─▶ Avi VS-443(VIP-A, L4 TCP, PROXY v2) ─▶ 마스터 ×3 :443 = Envoy DaemonSet(hostNetwork, 비 root 65532, TLS 종단)
                                                                  └─▶ svc dms-api:8080(HTTPRoute, 3600s) — 앱은 XFF 마지막 항목을 원 IP 로 기록
```

### 2g-1. 구성·값

| 무엇 | 어디(base → 렌더) | 비고 |
|---|---|---|
| Gateway API CRD | `deploy/addons/gateway-api/<GATEWAY_API_VERSION>/`(상류 `standard-install.yaml` 그대로 + `SOURCE`·sha256 + 라벨 층) → `.rendered/<site>/addons/gateway-api` | **v1.6.1 standard**(EG v1.9.3 의 go.mod 가 v1.6.1 — v1.6.3 과 스키마 같음, `SOURCE`). CRD 10개 + safe-upgrades VAP. 이미지 없음 |
| Envoy Gateway | `deploy/addons/envoy-gateway/<ENVOY_GATEWAY_VERSION>/`(상류 `install.yaml` 그대로 + DMS 패치 층) → `addons/envoy-gateway` | **v1.9.3**(1.9.x 최신, 2026-10-26; v1.8 은 2026-11-08 EOL). 패치 층: Gateway API 실험 채널 CRD·VAP 를 **지운다**(원천은 위 standard), 이미지 = `<REG>/gateway:v1.9.3@sha256:<amd64>`(컨트롤러·certgen Job), EG 설정(ConfigMap `envoy-gateway-config`)의 shutdown-manager·기본 Envoy 이미지 = 레지스트리, 상류 ratelimit 이미지 줄 삭제, 런타임 플래그 `EnvoyProxyPatch` 명시, **watch = ns dms 하나**(2026-11-03 백그라운드 보안 리뷰 — 모든 네임스페이스를 보면 다른 네임스페이스의 Gateway + EnvoyProxy 패치로 PSA privileged 인 이 ns 에 임의의 특권 파드를 띄울 수 있다. 다른 네임스페이스의 class `dms` Gateway 는 무시된다 — 조건 `Unknown`·프록시 없음, T8 실측. DMS 가 설치한 EG 는 DMS 전용이다), 컨트롤러는 워커(required nodeAffinity control-plane `DoesNotExist`)·`dms-addon-critical`, ns `envoy-gateway-system` PSA privileged(Envoy 파드가 여기 hostNetwork 로 뜬다), 라벨 managed-by `dms-install`·`dms.io/addon-version=v1.9.3` |
| 포탈 입구 | `deploy/platform/gateway`(DMS 소유) → `platform/gateway` | GatewayClass `dms`(controllerName EG 기본) · Gateway `dms-portal`(ns dms — HTTPS 443 리스너 하나, **hostname·80 리스너·HSTS 없음**, 인증서 `PORTAL_TLS_SECRET`, `infrastructure.parametersRef` → EnvoyProxy) · EnvoyProxy `dms-portal`(DaemonSet 을 control-plane 노드에, **hostNetwork + `dnsPolicy: ClusterFirstWithHostNet`**(EG 패치) + **`useListenerPortAsContainerPort: true`**, Service ClusterIP, concurrency 2, **`--disable-hot-restart`**(2g-2), 100m/256Mi..1/2Gi, `dms-addon-critical`, envoy preStop `host: 127.0.0.1`(2g-6), Envoy·shutdown-manager 이미지 명시, 접근 로그 JSON — 2g-5) · HTTPRoute `dms-portal`(/ → `dms-api:8080`, `timeouts.request: 3600s`) · ClientTrafficPolicy(PROXY **필수**, `earlyRequestHeaders.remove: [X-Real-IP]`, clientIPDetection **없음**, 밑줄 헤더는 지우고 받음) · BackendTrafficPolicy(`requestBuffer.limit: 20Mi` — 넘으면 413, 상류 유휴 `connectionIdleTimeout: 4s` — 앱 uvicorn keep-alive 5초보다 짧게: 상류가 먼저 닫으면 503 UC) |
| 버전·digest | `deploy/versions.env`(`GATEWAY_API_VERSION`·`ENVOY_GATEWAY_VERSION`·`ENVOY_GATEWAY_IMAGE`·`ENVOY_PROXY_IMAGE`) + `deploy/images.lock`(`gateway:v1.9.3`·`envoy:distroless-v1.39.5`) | shutdown-manager 는 상류에서 컨트롤러와 같은 `envoyproxy/gateway` 이미지라 lock 줄은 둘이다. Envoy 태그 = EG v1.9.3 의 `DefaultEnvoyProxyImage`(index `sha256:9d84a586…` 의 amd64 항목) |

| values.env 키(전부 선택) | 기본 | 뜻 |
|---|---|---|
| `GATEWAY_PROXY_PROTOCOL` | `required` | `optional` 은 **컷오버 중에만**(2g-4) — CTP `proxyProtocol.optional` 한 칸이 이 값에서 나온다. 렌더가 stderr 에 알린다 |
| `PORTAL_TLS_SECRET` | `dms-portal-tls` | Gateway 인증서 Secret(ns dms, kubernetes.io/tls, SAN = 포탈 FQDN + VIP-A — 보안 S3). install.sh S2 가 이 이름으로 존재·`kubernetes.io/tls` 를 본다(⑦) |
| `GATEWAY_ENVOY_CPU_REQUEST`·`GATEWAY_ENVOY_MEMORY_REQUEST` | `100m`·`256Mi` | Envoy requests(limits 1/2Gi 는 base — **넘으면 렌더 FAIL**: EnvoyProxy CRD 는 받아도 EG 가 만드는 DaemonSet 을 apiserver 가 거절해 Gateway 가 뜨지 않는다). 테스트베드 50m/128Mi(설계 10) |

`PORTAL_DOMAIN`·`PORTAL_VIP`(prod 필수 키)는 매니페스트에 들어가지 않는다 — `render.env` 로만 가서 인증서 SAN 과 S12 `curl --resolve` 의 기준이 된다.
렌더러 출력: `GATEWAY: <렌더>/addons/gateway-api` → `…/addons/envoy-gateway` → `…/platform/gateway`(S8 순서), `render.env` 의 `GATEWAY_LAYERS`·
`ENVOY_GATEWAY_REF`·`ENVOY_PROXY_REF`·`GATEWAY_PROXY_PROTOCOL`·`PORTAL_TLS_SECRET`.

### 2g-2. 마스터 sysctl(S0-5, 사이트 IaC — 1회)

Envoy 는 **비 root(65532)** 다(EG 기본값 — DMS 는 바꾸지 않는다, 아래). k8s 는 ambient capability 를 주지 않아
`NET_BIND_SERVICE` 로는 443 bind 가 안 된다(kubernetes#56374) — 마스터 3대의 호스트 netns 에서 비특권 포트 하한을 443 으로 내린다(설계 4-3 (i)).
hostNetwork 파드에는 `net.*` sysctl 을 줄 수 없어 호스트에 둔다. 부작용: 마스터 호스트의 비 root 프로세스도 443~1023 을 bind 할 수 있다(마스터에
일반 사용자가 없다는 전제).

```bash
# 마스터 3대 각각(root):
printf 'net.ipv4.ip_unprivileged_port_start = 443\n' > /etc/sysctl.d/90-dms-envoy.conf
sysctl --system >/dev/null
sysctl -n net.ipv4.ip_unprivileged_port_start          # 443
ss -ltnH '( sport = :443 or sport = :19000 or sport = :19001 or sport = :19002 or sport = :19003 )'   # 비어 있어야 한다(K6)
ss -xlH | grep -c envoy_domain_socket       # 0 이 아니면 호스트 netns 에 다른 Envoy(예: cilium-envoy)가 있다 — --disable-hot-restart 라 무해(아래)
```

- 빠뜨리면 **조용히** 그 마스터만 빠진다(2026-11-03 ⑥ 리뷰 1): Envoy 는 동적 리스너의 bind 실패를 거절(NACK)만 하고 기동을 계속해 파드는 Ready,
  Gateway 는 `Programmed: True`(EG 는 DaemonSet 의 가용 수만 본다)인데 그 마스터 :443 이 LISTEN 이 아니다 — Envoy 로그에 `cannot bind '0.0.0.0:443':
  Permission denied`·`config rejected`, Avi 헬스가 그 마스터를 뺀다. EG 는 같은 설정을 다시 내려 주지 않으므로 **sysctl 을 고친 뒤 그 노드의 Envoy
  파드를 지운다**(`kubectl --context C -n envoy-gateway-system delete pod <그 마스터의 envoy 파드>` — DaemonSet 이 다시 띄운다). 그래서 2g-3 은
  적용 전에 마스터 전부의 sysctl 을, 적용 뒤 마스터마다 :443 을 본다.
- 「root 로 올려 해결」하지 않는다. EG 가 막아 주는 것은 아니다 — EnvoyProxy `container.securityContext`(와 DS patch)는 기본값 위에 strategic merge 로
  덮여 `runAsUser: 0` 도 들어간다(상류 `applySecurityContextOverrides` — 권한 강제는 PSA 등 외부 정책 몫). 쓰지 않는 이유는 설계 4-3 의 선택((i) 권고)·
  비 root 원칙·S1 승인 범위이고, 지키는 것은 이 규약과 계약 테스트(EnvoyProxy 에 securityContext·runAsUser 0건)다. sysctl 을 받을 수 없는 사이트만
  설계 4-3 (ii)(root + NET_BIND_SERVICE)를 따로 검토한다.
- hot restart 는 끈다(EnvoyProxy `extraArgs: [--disable-hot-restart]`, 2026-11-03 ⑥ 리뷰 1): EG 가 `--base-id` 를 넘기지 않아 Envoy 는 base-id 0 의
  추상 유닉스 소켓(`@envoy_domain_socket_*_0`)을 bind 하는데, 추상 소켓은 netns 단위라 hostNetwork 파드는 호스트 것과 겹친다. Cilium 의
  cilium-envoy DaemonSet(hostNetwork, `--base-id 0`)이 이미 잡고 있으면 늦게 뜬 쪽이 `unable to bind domain socket with base_id=0` 으로 죽는다.
- 마스터 호스트에 함께 열리는 포트: :443(리스너)·:19003(readiness)·:19002(shutdown-manager)·127.0.0.1:19000(admin) — 비어 있어야 한다.
  :19001(prometheus stats)은 끈다(`telemetry.metrics.prometheus.disable`, 2026-11-03 커밋 보안 리뷰 — hostNetwork 라 전 인터페이스에 열린다;
  T8 실측: 끄기 전 `0.0.0.0:19001` 에서 다른 노드가 지표 약 1758줄을 읽었다 → 끈 뒤 LISTEN 없음, Gateway Programmed 그대로).
  **마스터 방화벽**: :443 은 Avi SE 대역에서만(V4), :19002·:19003 은 노드 밖에서 막는다(kubelet probe·preStop 은 노드 자신에서 온다 —
  :19002 는 drain 완료를 기다리는 GET `/shutdown/ready` 뿐이고 drain 자체는 localhost admin 이 한다).
- 클러스터 도메인이 `cluster.local` 이 아니면 EG 컨트롤러 env `KUBERNETES_CLUSTER_DOMAIN`(상류 그대로 cluster.local)이 xDS 주소
  `envoy-gateway.envoy-gateway-system.svc.<도메인>.` 을 틀리게 만든다 — `kubectl --context C -n kube-system get cm coredns -o yaml | grep kubernetes`
  로 확인한다(S1 preflight 가 다르면 WARN). 고치는 values 키는 아직 없다(BACKLOG 「⑦ 설치기 후속」) — **`deploy/.rendered` 를 손으로 고쳐도
  설치기는 매 실행 처음에 다시 렌더해 그 편집을 지운다**(2026-11-03 ⑦ 리뷰 2). 그런 사이트는 지금 설치기로 입구를 끝낼 수 없다(S8 이
  Programmed 에서 멈춘다) — values 키를 더하는 것이 BACKLOG 「⑦ 설치기 후속」의 할 일이다.

### 2g-3. 설치(S8, A 경로) — Gateway API → EG → 대기 → platform/gateway

먼저 감지한다(설치기는 `install-detect.py` — 설계 8-2; 손으로는):

```bash
kubectl --context C get crd gateways.gateway.networking.k8s.io \
  -o 'jsonpath={.metadata.annotations.gateway\.networking\.k8s\.io/bundle-version} {.metadata.annotations.gateway\.networking\.k8s\.io/channel}{"\n"}'
#   없음 → A. v1.6.x 이상 → 재사용(standard 든 experimental 이든 — DMS 는 standard 필드만 쓴다). 더 낮으면 X(소유자에게 올려 달라고)
kubectl --context C get crd envoyproxies.gateway.envoyproxy.io; kubectl --context C get deploy -A -l app.kubernetes.io/name=gateway-helm \
  -o 'custom-columns=NS:.metadata.namespace,NAME:.metadata.name,BY:.metadata.labels.app\.kubernetes\.io/managed-by,V:.metadata.labels.dms\.io/addon-version,IMG:.spec.template.spec.containers[0].image'
#   없음 → A · BY=dms-install(V 같음 → D=, 낮음 → D< — 2g-6) · 남이 설치 → C(아래) · 둘 이상 → P(중단)
```

A 경로(S5 가 lock 의 `gateway:v1.9.3`·`envoy:distroless-v1.39.5` 를 레지스트리에 push 한 뒤 — §2e-4 루프가 lock 전부를 돈다. **`REGISTRY_MODE=external`**
이고 `REGISTRY_CLUSTER_IP` 가 없으면 EG 컨트롤러·certgen·Envoy·shutdown-manager 이미지도 사이트 레지스트리(`render.env` 의 `ADDON_REGISTRY` = `REGISTRY`)
에서 받는다 — §2e-4 의 lock push 루프를 `IP:PORT` 대신 그 `REGISTRY` 로 돌리거나 그 레지스트리에 미리 미러한다(§2f-3 과 같다, 2026-11-03 ⑥ 리뷰 2).
설치기 S5 가 그 `REGISTRY` 로 대조하고 push 명령을 보인다):

```bash
sh deploy/render.sh <site> --context C
RD=deploy/.rendered/<site>
# PriorityClass dms-addon-critical — in-cluster 면 S4(§2e-3)가 이미 만들었다. external 사이트는 §2e 를 따르지 않으므로 여기서(§2f-3 과 같은 줄 —
# 없으면 EG 컨트롤러 파드가 admission 에서 거절된다):
kubectl --context C get priorityclass dms-addon-critical || kubectl --context C apply --server-side --field-manager=dms-install -k $RD/platform/priorityclass
# 1) Gateway API CRD(standard)
kubectl --context C apply --server-side --field-manager=dms-install --dry-run=server -k $RD/addons/gateway-api
kubectl --context C apply --server-side --field-manager=dms-install -k $RD/addons/gateway-api
kubectl --context C wait --for=condition=Established crd/gateways.gateway.networking.k8s.io crd/httproutes.gateway.networking.k8s.io --timeout=60s
# 2) Envoy Gateway — 첫 설치면 네임스페이스부터(server dry-run 은 묶음 안의 Namespace 를 저장하지 않는다 — 2f-3 과 같은 이유)
sh deploy/overlays/guard-images.sh $RD/addons/envoy-gateway --context C   # 재적용이면 컨트롤러 이미지 변경(= EG 업그레이드)을 exit 3 으로 막는다
kubectl --context C get ns envoy-gateway-system || kubectl --context C create namespace envoy-gateway-system
kubectl --context C apply --server-side --field-manager=dms-install --dry-run=server -k $RD/addons/envoy-gateway
kubectl --context C apply --server-side --field-manager=dms-install -k $RD/addons/envoy-gateway
kubectl --context C -n envoy-gateway-system wait --for=condition=complete job/eg-gateway-helm-certgen --timeout=120s   # 끝나고 30초 뒤 TTL 로 지워진다(그 뒤면 NotFound)
kubectl --context C -n envoy-gateway-system get secret envoy-gateway envoy   # certgen 이 만든 xDS mTLS 시크릿 — 있으면 된다
kubectl --context C -n envoy-gateway-system rollout status deploy/envoy-gateway --timeout=180s
kubectl --context C -n envoy-gateway-system get pods -o wide        # 컨트롤러 1개 워커, IMAGE 가 레지스트리 주소
# 3) 포탈 입구 — Secret(S2) 먼저: kubectl --context C -n dms get secret <PORTAL_TLS_SECRET>
#    마스터 **전부**의 sysctl(S0-5): ssh <마스터> 'sysctl -n net.ipv4.ip_unprivileged_port_start'   # 마스터마다 443 이하 — 하나라도 크면 멈춘다(2g-2)
kubectl --context C apply --server-side --field-manager=dms-install --dry-run=server -k $RD/platform/gateway
kubectl --context C apply --server-side --field-manager=dms-install -k $RD/platform/gateway
kubectl --context C -n dms wait --for=condition=Programmed gateway/dms-portal --timeout=180s
kubectl --context C -n envoy-gateway-system get ds,pods -o wide -l gateway.envoyproxy.io/owning-gateway-name=dms-portal   # 마스터마다 1개, Ready
ssh <마스터> 'sudo ss -ltnpH "( sport = :443 )"'     # **마스터마다** envoy 가 LISTEN — Programmed·Ready 는 bind 실패를 못 잡는다(2g-2)
```

C 경로(남이 설치한 Gateway API·EG): CRD·컨트롤러는 건드리지 않고(`addons/*` 를 적용하지 않는다) `platform/gateway` 만 적용한다 — Gateway 의
`infrastructure.parametersRef` 가 DMS EnvoyProxy 만 붙인다(GatewayClass 는 DMS 의 `dms`, 다른 GatewayClass 와 함께 있어도 된다). 확인할 것:
그 EG 의 설정(`kubectl --context C -n <ns> get cm envoy-gateway-config -o yaml`)의 `gateway.controllerName` 이 `gateway.envoyproxy.io/gatewayclass-controller`
인지(다르면 GatewayClass `dms` 가 Accepted 가 되지 않는다 — X), `runtimeFlags.disabled` 에 `EnvoyProxyPatch` 가 **없는지**(있으면 hostNetwork 패치가
조용히 무시돼 마스터 :443 이 빈다 — X), `provider.kubernetes.watch` 가 dms 를 포함하는지, 그 컨트롤러 네임스페이스(= Envoy 파드가 뜨는 곳)의 PSA 가
`privileged` 인지, 버전이 v1.9.x 인지(v1.8 은 2026-11-08 EOL — C!).

| 증상 | 뜻 · 할 일 |
|---|---|
| 파드 Ready·Gateway `Programmed: True` 인데 그 마스터 :443 이 LISTEN 아님, Envoy 로그 `cannot bind '0.0.0.0:443': Permission denied`·`config rejected` | 그 마스터에 sysctl(2g-2)이 없다 — 고친 뒤 그 노드의 Envoy 파드를 지운다(EG 가 같은 설정을 다시 내려 주지 않는다) |
| Envoy 파드 CrashLoop(또는 cilium-envoy 가 죽음), 로그 `unable to bind domain socket with base_id=0 … errno=98` | hot restart 추상 소켓 충돌 — EnvoyProxy 의 `extraArgs: [--disable-hot-restart]` 가 빠졌다(렌더 결과만 적용, 2g-2) |
| Envoy 파드 Ready 가 안 되고 로그에 xDS `envoy-gateway.envoy-gateway-system.svc.cluster.local` 해석 실패 | `dnsPolicy: ClusterFirstWithHostNet` 이 안 들어갔다(EnvoyProxyPatch 꺼짐 — C) 또는 호스트 → ClusterIP 경로 불가(S6 와 같은 원인 — kube-proxy·방화벽) |
| Envoy 파드가 `FailedCreate … violates PodSecurity "baseline": host namespaces (hostNetwork=true)` | ns `envoy-gateway-system` 에 PSA privileged 가 없다(C 경로 — 소유자에게) |
| `ErrImagePull … docker.io/envoyproxy/…` | 렌더하지 않은 base·EG 기본값으로 갔다(자리표시자) — 렌더 결과만 적용 |
| `ErrImagePull <레지스트리>/gateway:v1.9.3@sha256:…` 또는 `<레지스트리>/envoy:…@sha256:…` | lock 의 gateway·envoy 가 그 레지스트리에 없다 — S5(§2e-4) 누락(설치기는 S5 대조가 PASS 여야 S8 로 간다 — 손으로 돌았으면 `install.sh --context C --only S5`) |
| EG 컨트롤러 Pending `no PriorityClass with name dms-addon-critical` | S4 의 PriorityClass 가 없다(external 사이트 — 위 `get … \|\| apply … platform/priorityclass`) |
| HTTPRoute `ResolvedRefs: False`(BackendNotFound) | 앱(S11)이 아직 없다 — S11 뒤 저절로 풀린다 |
| 첫 설치 EG 컨트롤러 로그 `failed to get GatewayClass dms` 한 줄 | 무해 — 적용 순서상 Gateway 가 GatewayClass 보다 먼저 보였다. 다음 조정에서 풀린다(T8 실측) |
| 다른 네임스페이스에 만든 class `dms` Gateway 의 조건이 `Unknown` 그대로·Envoy 가 뜨지 않음 | 정상 — DMS 가 설치한 EG 는 ns dms 만 본다(2g-1). 다른 팀의 Gateway 는 그 팀의 EG 로 |

### 2g-4. Avi VS-443·PROXY 컷오버(optional → required)

Avi(설계 #5·#42·V2~V5 — 사이트 LB 담당 몫, DMS 는 값만 준다):

| 항목 | 값 |
|---|---|
| VS | VIP-A **:443 하나**(80 VS 없음 — 「80 불가 = 정상」), L4 TCP(TLS 는 Envoy 가 종단 — Avi 는 바이트만 넘긴다) |
| 애플리케이션 프로필 | **443 전용** L4 프로필, PROXY protocol **v2** 켬. 6443(apiserver) VS 의 프로필과 공유하지 않는다(공용 프로필을 고치면 6443 에도 PROXY 가 붙어 apiserver 가 끊긴다 — 위험 15) |
| 풀 | 마스터 3대 **:443**(노드 IP) |
| 헬스 모니터 | TCP 443(연결만) — HTTPS 모니터는 PROXY 헤더 없이 말해 **required 에서 실패**한다. HTTP 모니터를 쓰려면 모니터에도 PROXY 를 싣는 기능이 필요하다(제품 확인) |
| 유휴 타임아웃(V5) | 3600초 이상(HTTPRoute 3600s 와 맞춘다 — 짧으면 긴 다운로드를 Avi 가 먼저 끊는다) |
| 방화벽(V4) | 마스터 :443 은 **Avi SE 대역에서만** 받는다 — PROXY 헤더를 스스로 실은 위조 연결을 막는 것은 이 규칙이다 |

컷오버(기존 입구에서 옮길 때 — 첫 설치면 처음부터 `required`):

1. `GATEWAY_PROXY_PROTOCOL=optional` 로 렌더 → `platform/gateway` SSA. PROXY 없는 연결(직접 접속·전환 전 LB)도 받는다 — 원 IP 자리에 직접 피어가 남는다.
2. Avi VS-443(PROXY v2) 개통 → DNS 를 VIP-A 로. 접근 로그의 `client_ip` 가 사용자 PC, `peer` 가 SE 대역인지 본다(2g-5).
3. 옛 경로가 비면 `GATEWAY_PROXY_PROTOCOL=required`(기본 — 줄을 지워도 된다) → 렌더 → SSA. 직접 접속(PROXY 없음)이 연결 단계에서 끊기는지 본다.
   optional 로 오래 두지 않는다 — 「PROXY 가 빠진 LB 설정」이 조용히 통과한다(필수면 접속 실패로 바로 드러난다).
- 되돌림: 1단계 값으로 다시 렌더·SSA(Envoy 는 설정만 바뀌고 재기동하지 않는다). Avi 를 못 쓰게 되면(PROXY 옵션 없음) 원 IP 자리에 SE IP 가 남는다 —
  원 IP 별 경보를 끄고 Avi VS 로그를 원 IP 출처로 쓴다(설계 4-3, V5).

### 2g-5. 검증(S12)·원 IP

```bash
kubectl --context C -n dms get gateway dms-portal                     # PROGRAMMED True, ADDRESS = Envoy Service ClusterIP
kubectl --context C -n dms get httproute,clienttrafficpolicy,backendtrafficpolicy dms-portal -o yaml | grep -A3 'type: Accepted'
ssh <마스터> 'sudo ss -ltnp "( sport = :443 )"'                         # **마스터마다** envoy, users:(("envoy",…)) — 비 root(65532)
curl -sS --resolve <FQDN>:443:<VIP-A> --cacert <사내 CA> https://<FQDN>/readyz           # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST --resolve <FQDN>:443:<VIP-A> --cacert <사내 CA> \
  -H 'Content-Type: application/json' --data-binary @<21MiB 파일> https://<FQDN>/api/auth/login    # 413(Envoy) — 19MiB 는 413 이 아니다(앱이 답한다)
curl -sS -o /dev/null --limit-rate 50k -w '%{http_code} %{time_total}\n' --resolve <FQDN>:443:<VIP-A> --cacert <사내 CA> \
  https://<FQDN>/assets/<index-*.js>                                   # 200 이고 15초를 넘겨도 끊기지 않는다(라우트 기본 15초였던 자리 — 3600s)
curl -sS -m 5 http://<FQDN>/ ; echo "exit=$?"                            # 연결 실패가 **정상**(80 리스너·80 VS 없음 — 「80 불가 = 정상」)
# 접근 로그는 라벨로 **마스터 전부**를 읽는다 — `logs ds/<이름>` 은 파드 하나만 고른다(--all-pods=false; Avi 가 요청을 셋에 나눈다, 2026-11-03 ⑥ 리뷰 2)
kubectl --context C -n envoy-gateway-system logs -l gateway.envoyproxy.io/owning-gateway-name=dms-portal -c envoy --prefix --tail=5   # JSON 한 줄/요청 — client_ip·peer·x_forwarded_for
kubectl --context C -n envoy-gateway-system logs -l gateway.envoyproxy.io/owning-gateway-name=dms-portal -c envoy --since=1h | grep -c '"response_flags":"UC"'   # 0(상류 유휴 경쟁 — 2g-1 BTP)
```

- **원 IP**: 접근 로그 `client_ip` = PROXY v2 헤더의 출발지(사용자 PC), `peer` = 실제 TCP 피어(Avi SE), `x_forwarded_for` 의 마지막 항목 = client_ip
  (예외: PROXY 출발지가 루프백이면 Envoy 는 자기 주소를 덧붙인다 — `conn_manager_utility.cc` appendXff. LB 흉내에 127.0.0.1 로 붙는 시험에서만 생긴다).
  앱 이벤트(보안 이벤트 payload `{client_ip, peer}`)의 client_ip 도 같은 값이다(앱은 XFF 마지막 항목 → 소켓 피어 — `src/dms/api/auth.py`; 앱의
  `peer` 는 SE 가 아니라 Envoy 가 뜬 마스터의 호스트 쪽 출발 주소다 — 이름만 같다. Cilium 이면 그 노드의 cilium_host(CiliumInternalIP, 예: dmsk8s cp1
  10.245.0.129)이고 노드 IP 가 아닐 수 있다 — 기록 전용 칸이라 노드 IP 와 대조하지 않는다). 위조
  `X-Forwarded-For: 7.7.7.7`·`X-Real-IP: 6.6.6.6` 을 실은 요청도 client_ip 는 실제 PC 다(XFF 앞에 남을 뿐 마지막이 아니고, X-Real-IP 는 Envoy 가 지운다).
  접근 로그 수집처·보존은 K15·보안 S2. 경로는 쿼리를 뺀다.
- **15초+ 검사**: `--limit-rate` 만으로는 라우트 타임아웃을 건드리지 못할 수 있다 — 응답 전체를 LB·커널 버퍼가 빨리 받아 버리면 Envoy 쪽 스트림은
  15초 안에 끝난다(T8 실측 — haproxy·커널 버퍼가 흡수). 판정은 접근 로그의 그 요청 `duration_ms > 15000` 이고 200 인지로 하거나, 수신 버퍼가 작은
  클라이언트로 한다(T8: 느린 다운로드 20.9초 200).
- **80 불가 = 정상**: HTTP 로 들어오는 리디렉트가 없다(옛 Ingress 의 force-ssl-redirect 자리) — 사용자 안내는 `https://<FQDN>` 하나.
- e2e(실 브라우저)를 이 경로로 한 번 돈다 — EG 의 경로 정규화(`//` 합침·`%2F` 해제 후 리디렉트)가 옛 nginx 와 다르다(앱 경로 조각에 `/` 가 들어가는
  곳은 없다 — 2026-11-03 확인).

### 2g-6. 업그레이드·운영

| 경우 | 절차 |
|---|---|
| EG minor/patch | CNPG 와 같은 점검 창(#30·12 위험 6). ① `deploy/addons/envoy-gateway/<새 v>/`(상류 `install.yaml`·SOURCE·sha256 + 패치 층 — 라벨·자리표시자 태그) · 새 EG 의 go.mod 를 보고 `deploy/addons/gateway-api/<v>/` · `deploy/versions.env` · `deploy/images.lock`(`images-lock.py resolve gateway:<v> docker.io/envoyproxy/gateway:<v>` · 새 EG 의 `DefaultEnvoyProxyImage` 태그로 envoy 줄)을 **한 커밋**에서, 옛 디렉터리는 지운다 ② push(S5) ③ 테스트베드 리허설 ④ `addons/gateway-api` SSA → 남은 `job/eg-gateway-helm-certgen` 삭제(Job spec 은 바꿀 수 없다 — 보통 TTL 30초로 이미 없다) → `guard-images.sh $RD/addons/envoy-gateway` exit 3 → `ALLOW_IMAGE_CHANGE=1` 로 SSA → rollout → `platform/gateway` SSA(Envoy·shutdown-manager 이미지 → DaemonSet 이 마스터마다 차례로 교체 — 아래 「롤링 공백」) |
| EG 설정만 바뀜 | `envoy-gateway-config` 는 기동 때만 읽힌다 — SSA 뒤 `kubectl --context C -n envoy-gateway-system rollout restart deploy/envoy-gateway` |
| 인증서 교체 | 같은 이름의 Secret 을 바꾸면 EG 가 Envoy 에 다시 내려 준다(재기동 없음) |
| EG 컨트롤러 사망 | Envoy 는 마지막 설정으로 계속 돈다(설계 2-1) — 설정 변경만 멈춘다 |
| 남이 설치한 EG(C) | 업그레이드는 소유자 몫 — DMS 는 EOL·지원 범위를 경고만(감지 C! — 설치·verify 때마다) |

가드: `platform/gateway`·`addons/gateway-api` 에는 워크로드가 없어 `guard-images.sh` 대상이 아니다(exit 2) — Envoy 이미지 교체는 `render.env` 의
`ENVOY_PROXY_REF` 변화로 본다. `addons/envoy-gateway` 는 컨트롤러 Deployment 를 `envoy-gateway-system` 에서 읽어 이미지가 바뀌면 exit 3.

- **롤링 공백**: Envoy DaemonSet 은 maxSurge 0(같은 호스트 포트)이라 마스터마다 옛 파드를 내린 뒤 새 파드를 띄운다. envoy 의 preStop 은
  `httpGet.host: 127.0.0.1`(2026-11-03 T8) — 드레인(readiness 503) 동안 :443 은 열려 있고, 옛 파드가 내려간 뒤 새 파드가 잡을 때까지 공백은
  약 1.4초(T8 실측 1.44초). EG 기본 preStop(host 없음)은 hostNetwork 파드에서 kubelet 이 「failed to find networking container」로 실패해 envoy 가
  드레인 없이 SIGTERM — 약 61초 공백이었다(shutdown-manager 가 60초 기다린다). 옛 파드는 옛 preStop 으로 내려가므로 **이 설정을 처음 적용하는
  롤링**(옛 템플릿 → 새 템플릿)은 여전히 약 61초다.
- **마스터 메모리 예산**: CRD 가 늘면 kube-apiserver 의 OpenAPI 몫이 는다 — EG·GW API CRD 스키마가 2.04MB(dmsk8s 클러스터 CRD 스키마의 55%)라
  apiserver RSS 가 ≈1GB 가 됐다(T8 실측, 수백 MB 증가). 설계 2-2 예산의 apiserver requests 2Gi 면 충분하지만 작은 마스터는 압박을 받는다 —
  테스트베드 cp1 2.5GiB 는 MemoryPressure(speaker 축출 반복)로 4GiB 로 올렸다(2g-7). Envoy DS 는 `dms-addon-critical` 이라 압박 때 먼저 축출되지
  않는다(전에는 우선순위가 없어 1순위였다).

### 2g-7. k8s 테스트베드(`dmsk8s`) — T8(병행)과 이후 컷오버

- 값: `deploy/overlays/testbed-k8s/values.env` — `GATEWAY_PROXY_PROTOCOL=required`(최종 값 — T8 의 optional 단계는 이 파일을 고치지 않고 `VALUES_ENV`
  로 따로 렌더), `PORTAL_TLS_SECRET=dms-portal-tls`(Ingress 와 같은 인증서 — SAN dms-k8s.local·10.20.20.101·Tailscale IP), Envoy 50m/128Mi. 이미지는
  `10.112.0.50:5000`(k8s-w1 이 lock 으로 push). cp1 sysctl 은 `/etc/sysctl.d/90-dms-envoy.conf`(T8 에서 1024 → 443 — 노드 변경은 그 키 하나).
- cp1 메모리 4GiB(T8): EG·GW API CRD 뒤 kube-apiserver RSS ≈1GB 로 2.5GiB cp1 이 MemoryPressure(speaker 축출 반복, 2g-6) → DB primary 를 cp1 밖으로
  먼저 옮기고(switchover 7초) virsh 로 4GiB. sysctl drop-in·tfvars `memory_mb` 4096 의 testbed IaC 반영은 작업 사본 패치로 인계 대기(BACKLOG ⑥).
- T8(2026-11-03 완료 — CHANGELOG ⑥ 「T8 실증」): Gateway·Envoy(cp1 hostNetwork :443, PROXY 필수)가 **병행**으로 떠 있다 — 포탈 사용자 경로(MetalLB
  VIP 10.20.20.101 → ingress-nginx, 호스트 릴레이 luminous :8443 → VIP)는 그대로다. Avi VS-443 흉내는 luminous 의 haproxy(`send-proxy-v2` →
  10.10.10.20:443, 예비 포트)이고 시험은 그 포트로만 했다(비 root 443 bind·xDS·413·15초+ 스트림·PROXY optional/필수·X-Real-IP 제거·원 IP).
  테스트베드 한계: 노드 방화벽이 없어 cp1 :19002(shutdown-manager)가 다른 노드에서도 열린다 — prod 는 마스터 방화벽(2g-2).
- 컷오버 시험(T8, 원복함): haproxy 를 **먼저** :8443 에 bind 해 두고, 유닛 재시작 대신 nat PREROUTING·OUTPUT 의 `--dport 8443` DNAT 4줄을
  `iptables -t nat -D` 로 뺐다(ssc 릴레이를 끊지 않는다) — LAN·Tailscale 로그인 200·원 IP = 실제 PC. 되돌림은 같은 4줄을 `-I`(규칙 바이트 동일 확인)
  → haproxy 중지. `HOSTNET_RELAYS` 를 그대로 둔 `-D` 는 유닛 재시작·재부팅이면 되돌아간다 — 시험 전용이다.
- 이후 영구 컷오버(별도 승인 — MetalLB 는 되돌림용으로 남긴다. haproxy 를 testbed IaC 의 관리 서비스(systemd 유닛 + 설정, host-net 유닛 곁)로
  두는 것이 먼저다 — BACKLOG ⑥): ① haproxy 를 **먼저** 띄워 luminous 의 외부 주소 :8443 에 bind 해 두고 — DNAT 가 남아 있어도 bind 는 된다 —
  그다음 `/etc/default/dms-testbed-hostnet` 의 `HOSTNET_RELAYS` 에서 `8443=10.20.20.101:443` 을 빼고(영속), 지금 규칙은 유닛 재시작 대신 위 DNAT
  4줄을 `-D` 로 뺀다(DNAT 가 PREROUTING·OUTPUT 에서 소켓보다 먼저 가로채므로 규칙이 남아 있으면 haproxy 에 닿지 않는다; 순서를 바꾸면 그 사이
  :8443 이 거부된다. 유닛을 재시작해도 되지만 ExecStop 이 태그 규칙을 전부 지웠다 넣으므로 ssc(dms.local) 릴레이도 몇 초 끊긴다 — 2026-11-03 ⑥
  리뷰 2) ② `https://dms-k8s.local:8443` 이 Gateway 경로가 된다(원 IP = 실제 PC — 지금의 DNAT+MASQUERADE 경로는 원 IP 가 호스트 브리지 주소
  10.10.10.1 이다) ③ 소크 뒤 testbed-k8s 오버레이도 prod 처럼 `patch-ingress.yaml`·`patch-metallb.yaml` 을 빼고 `patch-remove-ingress.yaml` 을
  싣는다(테스트 `test_testbed_k8s_overlay.py` 의 VIP·호스트 단언을 같이 바꾼다) — ingress-nginx·MetalLB 자체는 testbed IaC 가 걷는다. 되돌림 =
  `HOSTNET_RELAYS` 원복 + 같은 4줄 `-I`(또는 유닛 재시작 — DNAT 가 먼저 가로채 사용자 경로가 VIP 로 돌아간다) → 그 뒤 haproxy 중지(오버레이를
  바꾸기 전까지는 Ingress 가 그대로 살아 있다). 상태 확인은 `dms-testbed-hostnet status` 에 같은 `HOSTNET_RELAYS` 를 env 로 준다 — 스크립트는 `/etc/default` 를 읽지 않는다(유닛의
  `EnvironmentFile` 만), env 없이 돌리면 정상 컷오버에서도 8443 MISS 다.

## 2h. 설치기(install.sh) — 단계 S1~S13·감지·멱등 (2026-11-03 ⑦)

설계 v6 8-1(단계)·8-2(감지 상태 기계)·9 ⑦. §2d~§2g 의 손 명령을 단계로 묶었다 — 손 명령은 같은 일의 조사·복구용 판으로 남긴다. prod 의 순서는
`deploy/overlays/prod/README.md`, 판정 근거는 각 도우미의 머리 주석이다.

```bash
sh deploy/install.sh --context C [--site prod] --plan            # S1 + S2(확인만) + S3 — 클러스터를 바꾸지 않는다
sh deploy/install.sh --context C [--site prod]                   # S1 → … → S4 뒤 PAUSE(exit 10: push 명령 출력) — 이미 다 있으면 끝까지
sh deploy/install.sh --context C [--site prod] --stage S5        # 인터넷 노드 push 뒤 재개(S5 → S13)
sh deploy/install.sh --context C [--site prod] --only S7,S12     # 그 단계만(설치 순서대로) — 끝난 단계는 no-op·검증
#   --upgrade-addon <x>(D< 올리기·D= 재적용) · --adopt <x>(P 채택 — SSA --force-conflicts) · env ADDON_INSTALL=never(A 면 FAIL + 운영자에게 요청)
#   env ALLOW_IMAGE_CHANGE=1·ALLOW_HOSTPATH_CHANGE=1(가드 3·5) · VERIFY_NO_PROBES=1(S12 순수 읽기) · PORTAL_DOMAIN·PORTAL_VIP·CACERT(S12)
#   env SELINUX_PROBE_IMAGE=<ref>(S2b — Cilium 이 없는 클러스터: 모든 노드에 이미 있는 셸 포함 이미지)
sh deploy/install.sh --help                                      # 머리 주석(env·--upgrade-addon 이름·종료 코드) — exit 0
```

| 단계 | 하는 일(도우미) | 멈춤 |
|---|---|---|
| S1 | `preflight-cluster.sh --render-env`(K8s 1.34~1.36·control-plane·Volcano 라벨 `app=volcano-scheduler`·클러스터 도메인·PSA·admission·SHARED_DIR 형식·REGISTRY_CLUSTER_IP 의 ServiceCIDR·IPAddress·하단 정적 대역·노드 셸 체크리스트) + `install-detect.py detect` | FAIL·P·X(감지), Volcano 를 설치해야 하는데 층 없음 |
| S2 | `00-namespace.yaml` → `pg-secrets.py ensure-app`·(없으면) `init-secrets` → 4키·자리표시자·`pg-secrets.py check` → `PORTAL_TLS_SECRET` 확인(없으면 WARN — 쓰는 곳인 S8 이 멈춘다) | 키·URL 어긋남(읽기 실패는 모름) |
| S2b | `selinux-probe.py`(privileged 임시 DS, DMS 노드만 판정) | FAIL·모름 |
| S3 | `install-detect.py plan`(층별 SSA server dry-run — 첫 설치의 「ns 가 아직 없음」·「CRD 가 아직 없음」은 SKIP) | FAIL |
| S4 | PriorityClass SSA → (in-cluster, 적용할 때만) guard(`--upgrade-addon registry` 면 이 층에서 이미지 교체 허용) → `dms-registry-http`(없을 때만·stdin) → platform/registry SSA → rollout → port-forward `/v2/` 200 | FailedMount(마스터 이름과 함께)·ImagePullBackOff(S0-5 반입 없음)·FailedScheduling·센티널·clusterIP 충돌 |
| S5 | `images-lock.py compare-registry`(port-forward) + DMS 3종 태그 HEAD → lock 이 다르면 `push-images.sh --print`(external TLS 면 `--dest-tls-verify true`), DMS 이미지만 없으면 그 빌드·복사 안내만 | S4 에서 이어 왔으면 PAUSE, 아니면 FAIL |
| S6 | `pull-probe.py`(Always, 120초, DMS 노드만) | FAIL·모름 |
| S7 | `shared-storage-probe.py`(①~⑥, ② 는 WARN) | 1·4(3 은 계속) |
| S8 | gateway-api SSA → CRD Established → (ns 먼저 — DMS 라벨) EG SSA → certgen → rollout(DMS 소유 EG 를 적용하지 않을 때도 Ready·xDS 시크릿 확인) → TLS 시크릿 → `host-net-probe.sh --sysctl`(마스터 ≤ 443) → platform/gateway → Programmed → `host-net-probe.sh --listen`(마스터마다 :443) | 각 대기 실패, TLS 없음, 덜 설치된 EG(→ `--upgrade-addon envoy-gateway`) |
| S9 | (ns 먼저 — DMS 라벨) CNPG SSA → rollout(업그레이드면 가드를 이 층에서 열고, Cluster 가 있으면 직전 덤프 → healthy 대기) → Cluster CR server dry-run(웹훅 9443, 막 뜬 웹훅은 재시도, `--adopt db` 면 `--force-conflicts`) | 「failed calling webhook」 |
| S9b | (층이 있으면) Volcano SSA → `install-detect.py vcjob`(DMS 모양 vcjob dry-run) | FAIL |
| S10 | `pg-path-probe.py pv-check` → db-volumes → `precheck` → db-cluster → 900초 폴링 → `contrast` → db-backup → 첫 덤프(Complete·Failed) | 각 판정 |
| S11 | 시크릿 게이트 → 앱 이미지 확인 → `guard-images.sh` → migrate 지움 → `apply -k`(client-side) → migrate·rollout → 설정 rev 바뀌면 restart → 마스터 사전 pull(WARN) | 가드 3·4·5, migrate |
| S12 | `verify.sh --rendered`(§2g-5 의 검사 + CNPG·여분 PV·경로 대조·CronJob·레지스트리 태그·애드온 요약·80 불가 = 정상·EACCES 힌트). readyz 판정은 apiserver 서비스 프록시(`get --raw …/services/http:dms-api:http/proxy/readyz`), VIP 경유(`--resolve`)는 응답 없음이면 WARN(Avi VS-443 미개통 — 첫 설치) | FAIL(이때도 S13 체크리스트를 보인다) |
| S13 | 사후 체크리스트 출력 — 첫 관리자 부트스트랩(port-forward + `x-admin-token` 파일, 토큰·비밀번호를 출력·argv 에 싣지 않는다)·DNS·Avi 헬스·포탈 등록 | — |

- **S2b(SELinux) — 테스트베드 실측**(2026-11-03 T-SE, Rocky 9.6 워커): `setenforce 1` 이면 그 노드만 FAIL 「enforcing」, config 만 enforcing 이면 FAIL
  「재부팅하면 enforcing」. enforcing 동안 dms-agent(`container_t`)가 `/proc/1/mountinfo`(`init_t`)·CephFS 루트(`unlabeled_t`)를 못 읽어 그 노드의
  스토리지가 전부 Missing(`not_a_mountpoint`)으로 보고됐다 — prod README 「0. 사이트 준비」 2(SELinux 필수 전제, 설계 #48)의 근거. NFS 경로는 AVC 0.
- **RHEL firewalld — 설치기·preflight 는 보지 않는다**(S0 사이트 몫): 기본 public zone 을 켜면 노드 간 흐름이 깨진다(T-SE 실측 — Cilium vxlan 이라
  다른 노드 백엔드의 응답이 8472/udp 로 들어와 INPUT 에서 막힌다: 파드 DNS·호스트 → ClusterIP·NodePort·agent 보고 실패, 노드 안에서 끝나는 외부
  egress 는 산다). 설계 4-2 포트(10250/tcp·8472/udp·4240/tcp·4244/tcp·7946/tcp+udp·30000-32767/tcp)를 열면 모두 복구 — FORWARD 정책은 바꿀 필요 없었다.
- **`--context` 는 필수**다(KUBE_CONTEXT·current-context 를 쓰지 않는다). 렌더의 `kube-context`(testbed-k8s = dmsk8s)와 다르면 exit 2. 모든 kubectl
  은 `kc`·`kcn` 함수(`command kubectl --context "$CTX"`)로만 부른다 — 계약 테스트 `test_check_scripts_context.py`.
- **층의 소유**: addons·platform 은 SSA(`--field-manager=dms-install`), 앱은 client-side apply. 감지가 **C**(남이 설치한 호환 애드온)면 그 층에는 apply 도
  dry-run 도 부르지 않는다 — 소비 층(platform/gateway·Cluster CR dry-run·vcjob)만 본다.
- **멱등**: 같은 렌더로 다시 돌리면 SSA 는 no-op, 시크릿·Job·ns 는 만들지 않고(있으면 그대로), 첫 덤프는 CronJob 의 마지막 성공이 있으면 건너뛰고,
  rollout restart 는 `dms-config`·`dms-secrets` 의 resourceVersion 이 바뀌었는데 이번 apply 가 파드를 바꾸지 않은 경우에만 한다(워크로드 어노테이션
  `dms.io/install-config-rev` — 값이 아니라 rev 만). `tests/test_install_stages.py` 가 심 kubectl 로 순서·PAUSE·재개·멱등·`--plan` 무변경을 고정한다.
- 종료: 0 완료(WARN 포함) · 1 FAIL · 2 사용법·환경(빈 `--stage`·`--only` 포함) · 4 모름 · 10 PAUSE. 마지막 줄 `RESULT: …`, 단계마다 `STAGE: <S> <판정>`.
  preflight(S1)·verify(S12)의 `[WARN]` 줄은 rc 0 이어도 그 단계와 RESULT 를 WARN 으로 만든다(2026-11-03 ⑦ 리뷰 2 — dmsk8s `--plan` 이
  「preflight WARN 1」인데 RESULT PASS 였다). 도우미 출력은 4칸 들여 쓴다 — 0열의 `==`·`STAGE:`·`RESULT:` 는 설치기 자신의 줄뿐이라
  `grep '^RESULT:'` 는 설치 결과 하나만 잡는다.
- **재개 명령**(PAUSE·모름·FAIL 줄)은 이번 실행의 `--upgrade-addon`·`--adopt` 와 판정을 바꾸는 env(`ADDON_INSTALL`·`ALLOW_*`·`SELINUX_PROBE_IMAGE`·
  `VALUES_ENV`·S12 의 `CACERT`·`PORTAL_DOMAIN`·`PORTAL_VIP`·`VERIFY_NO_PROBES`)를 그대로 싣는다 — 출력된 줄을 그대로 붙여 넣는다(2026-11-03 ⑦ 리뷰 1·2:
  플래그가 빠진 재개는 업그레이드를 조용히 건너뛰었고, CACERT 가 빠진 S12 재실행은 VIP readyz 가 TLS 실패로 WARN 이 됐다). **`--plan` 의 「확인한
  뒤」·「계획 끝」 줄은 늘 `--plan` 이다** — 실제 설치 명령(`… --site S [--adopt …]`)은 FAIL·모름이 없는 계획 뒤에만 계획과 같은 플래그·env 로 보인다
  (전에는 계획 중의 모름·FAIL 이 클러스터를 바꾸는 `--stage S1`·`--stage S2` 를 권했다). env `DMS_NS`(옛 install.sh)는 읽지 않는다 — ns dms 고정,
  도우미에도 `--namespace dms`.
- **가드 거부(이미지 변경, exit 3)**: 애드온·플랫폼 층(registry·envoy-gateway·cnpg)이면 안내는 `--only <S> --upgrade-addon <x>`(그 층에서만 연다 —
  감지 D< 의 upgrade 와 lock digest 만 바뀐 D= 재적용 둘 다)이고, 앱이면 values.env 태그를 라이브에 맞추거나 `live`(의도한 앱 이미지 변경만
  `ALLOW_IMAGE_CHANGE=1`). 전역 `ALLOW_IMAGE_CHANGE=1` 은 재개 줄에 실려 S11 의 앱 가드(포탈 릴리스 되돌림 방지)까지 열므로 애드온에 쓰지 않는다.
- **렌더는 매 실행 처음에 다시 한다**(`--plan`·`--only` 포함 — `deploy/.rendered/<site>` 를 통째로 바꾼다). 그 아래를 손으로 고친 값은 설치기로
  적용되지 않는다 — 사이트 값은 values.env 키로만(키가 없으면 BACKLOG 「⑦ 설치기 후속」에 올리고, 그 전에는 설치기 실행 뒤 §2d~§2g 의 손 명령으로).
- **앱 업그레이드**: 계획(S3)은 끝난 `dms-migrate` Job 의 `field is immutable` 을 실패로 보지 않는다(S11 이 지우고 다시 만드는 Job — 앱 층 PASS 에
  사유를 적는다).
- **아직 없는 것**(BACKLOG ⑦): Volcano 층(`addons/volcano` — A 사이트는 S1 에서 멈춘다), 새 사이트 DB 의 import bootstrap, `kubectl-cnpg` 반입.

## 3. Apply manifests, in order

```bash
kubectl --context dms apply -f deploy/k8s/00-namespace.yaml
kubectl --context dms apply -f deploy/k8s/05-volcano-queue-priorityclass.yaml
kubectl --context dms apply -f deploy/k8s/10-rbac.yaml
kubectl --context dms apply -f deploy/k8s/20-config.yaml
```

`20-config.yaml` holds only non-secret config, so it is safe to re-apply on a
running cluster. Never apply `20-secret.example.yaml` — it carries
placeholders, and applying it over a live `dms-secrets` replaces the real
credentials (the api/controller then CrashLoopBackOff on
`password authentication failed for user "dmsapp"`).

**k8s 테스트베드(`dmsk8s`)에는 `00-namespace.yaml` 말고 raw apply 를 하지 않는다** — `20-config.yaml` 을 raw 로
올리면 오버레이의 사이트 값(`DMS_USER_ALLOWED_OPERATIONS`·`DMS_JOB_IMAGE` 등)이 base 값으로 되돌아간다. 설정·RBAC·큐는 전부
`kubectl --context dmsk8s apply -k deploy/overlays/testbed-k8s`(§13)로만 간다.

## 3b. Create the secret (once, out-of-band)

`dms-secrets` is never committed. Create it directly, with the real DB
password and freshly generated tokens:

```bash
# ssc 테스트베드(LDAP 사용 — 5키)
kubectl --context dms -n dms create secret generic dms-secrets \
  --from-literal=DMS_DATABASE_URL='postgresql://dmsapp:<DB_PASSWORD>@10.10.10.30:5432/dmsdb' \
  --from-literal=DMS_SHARED_TOKEN="$(openssl rand -hex 24)" \
  --from-literal=DMS_ADMIN_TOKEN="$(openssl rand -hex 24)" \
  --from-literal=DMS_SESSION_SECRET="$(openssl rand -base64 32)" \
  --from-literal=DMS_LDAP_BIND_PW='<LDAP_SEARCH_PASSWORD>'
```

k8s 테스트베드는 LDAP 이 없어 **4키**다(`DMS_LDAP_BIND_PW` 를 넣지 않는다 — ssc 시크릿을 복사해 오면 안 된다) —
명령은 §13-5.

`DMS_SHARED_TOKEN` grants `role=admin` on every API call (`Bearer <token>`),
so generate it — don't copy one from the repo. Startup rejects any value
containing `CHANGE_ME` or `REPLACE_WITH_` (`src/dms/config.py`
`_is_placeholder`), so a skipped or half-filled secret fails loud instead of
coming up with a publicly known admin token.

Read the value back later with:

```bash
kubectl --context dms -n dms get secret dms-secrets \
  -o jsonpath='{.data.DMS_SHARED_TOKEN}' | base64 -d; echo
```

## 4. Migrate / 5. Bring up api / controller / agent (testbed overlay)

`deploy/k8s` 의 `image:`·`DMS_JOB_IMAGE` 는 **사이트 중립 자리표시자**
(`set-by-overlay.invalid/<img>:set-by-overlay`, 2026-09-14)다 -- 실 레지스트리·태그는 오버레이가 넣는다.
ssc 테스트베드는 `deploy/overlays/testbed`(newTag = 이 테스트베드의 태그, 태그 bump 는 이
파일 커밋), k8s 테스트베드는 `deploy/overlays/testbed-k8s`(§13), 프로덕션은 `deploy/overlays/prod|ssc`(values.env).
**base 를 raw 로 `kubectl apply -f deploy/k8s/…` 하지 마라** -- 자리표시자 이미지라 pull 에서 즉시
실패한다(다른 사이트 값을 조용히 배포하던 사고를 막기 위한 의도된 실패).

**컨텍스트(2026-10-21)**: 두 테스트베드가 같은 네임스페이스(`dms`)를 써서, 컨텍스트를 빠뜨리면 다른 클러스터에 apply 한다.
오버레이 디렉터리의 `kube-context` 파일(testbed = `dms`, testbed-k8s = `dmsk8s`)이 짝을 고정하고, 가드는
`--context`(또는 `KUBE_CONTEXT`, 둘 다 없으면 현재 컨텍스트)가 다르면 **exit 4** 로 거부한다. 가드는 뒤따르는 apply
명령을 통제하지 못하므로 apply·wait·rollout 에도 같은 `--context` 를 쓴다(가드 통과 메시지가 apply 명령을 그대로 찍어 준다).
가드의 종료 코드: 0 통과 · 2 사용법·렌더 실패·없는 컨텍스트 · 3 이미지 변경 거부(`ALLOW_IMAGE_CHANGE=1`) · 4 컨텍스트 불일치 ·
**5 hostPath 변경 거부**(2026-11-03 ③ — 렌더된 Deployment 의 hostPath 집합 != 라이브, 공용 디렉터리 이행은 §2d 전환 런북 뒤
`ALLOW_HOSTPATH_CHANGE=1`; 둘 다 바뀌면 3 이 먼저).

```bash
kubectl --context dms -n dms delete job dms-migrate --ignore-not-found   # Job 은 불변이라 재실행 전 삭제
sh deploy/overlays/guard-images.sh deploy/overlays/testbed --context dms # 렌더 이미지 != 라이브면 거부(포탈 릴리스 되돌림 방지)
kubectl --context dms apply -k deploy/overlays/testbed                   # migrate + api + controller + agent (+ config/rbac/ingress)
kubectl --context dms wait --for=condition=complete job/dms-migrate -n dms --timeout=120s
kubectl --context dms logs job/dms-migrate -n dms   # expect: "migrated"

kubectl --context dms -n dms rollout status deployment/dms-api
kubectl --context dms -n dms rollout status deployment/dms-controller
kubectl --context dms -n dms rollout status daemonset/dms-agent

kubectl --context dms -n dms port-forward svc/dms-api 8080:8080 &
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
export DMS_SHARED_TOKEN=$(kubectl --context dms -n dms get secret dms-secrets \
  -o jsonpath='{.data.DMS_SHARED_TOKEN}' | base64 -d)      # k8s 테스트베드는 --context dmsk8s
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
# 주의(2026-10-16): 워커는 procs_per_node 만큼 CPU 코어를 **실제로 요청**한다(limits 없음, launcher 100m). planner·stepper 가
# 노드당 프로세스를 floor((노드 allocatable − 100m − DMS_NODE_CPU_RESERVE_MILLICORES) / 1000) 코어로 줄이므로(그 노드가 1코어도
# 못 담으면 후보에서 빠지고, 다 빠지면 node_cpu_insufficient 로 거부) 상한을 아는 동안엔 값이 커도 잡이 영구 Pending 이 되지
# 않고, 화면엔 「요청 N → 노드 CPU 상한 M」으로 줄어든 값이 보인다. 상한을 모르는 창(에이전트 롤아웃 중 옛 에이전트 보고가
# 남은 노드·에이전트 재시작 직후 첫 Node 읽기 실패 -- 그 뒤의 읽기 실패는 에이전트가 마지막 값을 이어 싣는다)에 제출된
# 미리보기·실행은 줄지 않고 나가 Volcano 에서 기다리다, 상한이 알려지는 순간 stepper 가 회수하고
# node_cpu_insufficient_at_step 으로 끝낸다(자동으로 줄여 다시 내지 않는다 -- 다시 제출). 의도대로 돌리려면
# procs_per_node ≤ 그 상한으로 둔다 -- 테스트베드(allocatable 1800m, 예비 500)는 상한 1 이다. procs_per_node 상한은
# 1024(워커당 rank 위생 상한, 그 위는 저장 422).
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
dms-k8s 에선 모든 단건 잡이 root 이고 root 허용은 세션 인증 요청뿐이라(`identity.root_allowed`) 이 절의 Bearer
공유 토큰 제출은 403 `privileged_not_authorized` 다 — k8s 테스트베드는 봉인 로그인 세션(§10-7)으로 낸다(§13 표).

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

`cephfs-third` (ssc: w1-2, k8s: k8s-w1) and `cephfs-secondary` (ssc: w3, k8s: k8s-w2) share NO node, so
`select_tool_and_candidates` falls through to `nsync` with
`candidates={"source": [...w1-2], "destination": [...w3]}` -- this is the
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
(`testbed` 저장소 `make storage` — 체크아웃마다 따로 export, 그 부모는 testbed 저장소의 키·CA 가 있어
export 하지 않는다. 새 체크아웃은 testbed `dms_src_checkouts` 에 넣고 `make storage`) — 실 클러스터에서는 빌드
노드의 로컬 체크아웃을 그대로 지정하면 된다.

빌드 코드에는 브랜치 개념이 없다 — **클러스터마다 자기 체크아웃을 가리킨다**(2026-10-21, control_state 는 DB 하나 = 클러스터
하나라 서로 독립):

| 클러스터 | build_node_name | build_source_path | 따라가는 브랜치 | 레지스트리 / 태그 |
|---|---|---|---|---|
| ssc(`dms`) | dms-w1 | `/home/mason/dms-dev/dms-ssc`(전체 clone — 전환 전엔 `/home/mason/dms-dev/dms`) | origin/dms-ssc | pkg-01:5000 / dN |
| k8s(`dmsk8s`) | k8s-w1 | `/home/mason/dms-dev/dms` | origin/dms-k8s(`/home/mason/dms-dev/dms-k8s`) | 10.112.0.50:5000(클러스터 내, 2026-11-03~; 그 전 pkg-01:5001) / dN |

체크아웃은 호스트 cron 이 ff-only 로 맞춘다. **git worktree 로 만들지 않는다** — 빌드 파드는 이 경로 하나만 마운트해서
`.git` 파일이 가리키는 바깥 gitdir 를 못 따라가 SHA 가 `unknown` 이 된다. 컨트롤 상태 PUT 은 전체 치환이라(빠진 필드는
NULL) GET 한 값에서 바꿀 필드만 고쳐 전 필드를 보낸다.

**2) 태그는 지정하거나 파생된다(드리프트 방지 내장, 슬라이스 34).** 「빌드」 폼의
(선택) 태그 입력에 관례 태그(예: `d75`)를 지정하면 그 태그로 push 된다. **빌드는
빌드하는 이미지의 동봉 매니페스트(`deploy/k8s`) 태그를 이 빌드 태그로 자동
스탬프**하므로(`build_manifests._SCRIPT`), 그 태그로 배포하면 **live == 동봉
매니페스트가 되어 드리프트 배지가 안 뜬다** — 예전처럼 손으로 `deploy/k8s` 를 먼저
bump 해 빌드하지 않아도 된다. 단 그 태그를 실제로 굴리려면 **사이트 오버레이의 태그**
(ssc 테스트베드 `deploy/overlays/testbed/kustomization.yaml`, k8s 테스트베드 `deploy/overlays/testbed-k8s/kustomization.yaml`
의 `newTag`, prod/ssc 는 values.env 의 `DMS_TAG`)도 그 태그로 맞춰 `kubectl --context <ctx> apply -k` 해야 새 태그가 배포된다
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
것이다. 빌드 이력 행은 「빌드 이력」 화면에서 다중 선택 삭제(종단 빌드만). 지울 때 그 빌드의 빌드·프로브 파드를 먼저
지운다(2026-10-26 — 예전엔 행만 지워 파드가 pod-gc 시야 밖으로 영영 남았다): apiserver 에 닿지 않아 파드를 못 지우면
502 `build_pod_cleanup_failed` 로 기록을 남긴다(잠시 뒤 다시). pod-gc 가 이미 치운 빌드는 클러스터에 묻지 않고 지운다.
pod-gc 는 빌드도 치운 것에 `builds.pods_gc_at` 을 찍어 다음부터 건너뛴다 — 이 표시가 생긴 첫 배포 뒤 첫 pod-gc 틱(기본
600초)은 표시 없는 옛 종단 빌드를 한 번 훑는다(틱당 200건, 대부분 이미 없는 파드 404). RBAC 변경 없음(api Role 의 pods delete).

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
잡 파드 축출 0건. **2026-10-16 부터 잡 워커가 CPU 를 요청한다**(노드당 프로세스 수만큼 코어,
launcher 100m — 그 전 실증은 잡이 전부 BestEffort 이던 때다). 이제 CPU **예약**이 겹친다: 워커가
노드의 코어를 예약 중이면 빌드 파드(250m 요청)가 그 노드에서 스케줄을 기다릴 수 있고, 반대로 빌드가
돌면 그 노드의 워커가 Volcano 대기에 걸릴 수 있다(둘 다 기다릴 뿐 실패하지 않는다 — 빌드는 build_watcher
타임아웃 안에서). 노드 CPU 상한은 allocatable 에서 launcher 100m 와 예비(`DMS_NODE_CPU_RESERVE_MILLICORES`, 기본
500m — DaemonSet·제어면·빌드 몫)를 빼고 코어 단위로 내린 값이라, 테스트베드(1800m)는 워커 1000m + launcher 100m 를
예약해도 700m 가 남는다(DMS 외 requests 최악 425m + 빌드 250m). 노드의 DMS 외 requests 가 그보다 크면 예비를 올린다.

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

- 테스트베드: `deploy/overlays/<testbed|testbed-k8s>/kustomization.yaml` 의 `newTag`(dms 계보 = api·
  controller·migrate 가 한 항목, dms-agent 계보 = 다른 항목) + `patch-config.yaml` 의
  `DMS_JOB_IMAGE`. **오버레이마다 bump 하는 브랜치가 다르다**(2026-10-21): `testbed-k8s`(k8s 클러스터, dN — 2026-10-26 전엔 kN)는
  dms-k8s 에서만(2026-10-22 분리 전엔 main(→ dms-k8s)), `testbed`(ssc 클러스터, dN)는 ssc 빌드 소스가 dms-ssc 체크아웃으로 넘어간 뒤로는 dms-ssc
  쪽에서만(전환 전에는 main) — 한 커밋에서 둘을 같이 올리지 않는다(체리픽 충돌·남의 태그).
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

**8) 보안 설정 드리프트 배지(2026-10-26).** 「릴리스」 화면의 dms-api·dms-controller 「현재 이미지」 칸은 라이브 컨테이너의
실효 securityContext(runAsUser·runAsGroup·runAsNonRoot·allowPrivilegeEscalation·readOnlyRootFilesystem·privileged·
capabilities add/drop — 컨테이너 값이 없으면 파드 수준)를 이미지에 동봉된 `40-api.yaml`·`41-controller.yaml` 과 비교해, 다르면
「보안 설정 드리프트」와 필드별 「라이브 X · 매니페스트 Y」를 보인다(응답 `GET /api/admin/releases/targets` 의
`security_drift` — null 모름 / [] 같음 / 목록). 포탈 롤아웃은 이미지만 바꾸므로 배지는 롤아웃으로 사라지지 않는다 — 사이트
오버레이를 `kubectl --context <ctx> apply -k deploy/overlays/<site>` 해야 맞춰진다(§2b). 동봉 매니페스트가 없거나 그 블록을
못 읽으면 표시하지 않는다(거짓 경보보다 모름). dms-agent·잡 이미지는 비교하지 않는다.

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
base 로 두고 사이트별 값(레지스트리·태그·공용 디렉터리 `SHARED_DIR`(§2d)·이메일 도메인·포탈
도메인·VIP)만 오버레이에서 덮는다 -- 자리표시자를 채우고 `kubectl apply -k`. **prod 의 포탈 입구는 §2g(Envoy Gateway)** 다(2026-11-03 ⑥ —
prod 오버레이가 base 의 Ingress·MetalLB 풀을 지운다; 아래 §10 의 nginx ingress + MetalLB 는 testbed-k8s 의 지금 입구).
전제조건·값 표·Secret/TLS 생성·적용 순서는 **`deploy/overlays/prod/README.md`**.
이 §(테스트베드 개별 파일 apply)는 그대로 유효하다 -- 오버레이는 base 를 건드리지
않는다.

---

## 10. 포탈 HTTPS 노출 — nginx ingress + TLS + 공인 VIP(L2) (2026-08-19 완증)

> 2026-11-03 ⑥: **prod 는 이 절을 쓰지 않는다** — 입구는 §2g(Avi L4 + PROXY v2 → 마스터 :443 의 Envoy Gateway). 이 절은 테스트베드(testbed-k8s —
> Gateway 와 병행 — 영구 컷오버 전까지, §2g-7)의 지금 입구다.

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
   (SAN: DNS dms.local + IP 10.20.20.100. 리허설 CA 는 2026-10-21 부터 testbed 저장소
   `files/tls/`(gitignore, 0600), 영구 사본 `/data/dms-testbed/backup-20261021/tls/` — 그 전엔 세션 스크래치에만
   있었다. 어느 NFS export 에도 들어가면 안 된다(testbed `portal-tls.sh` 가 거부). `INIT_CA=1` 은 새 CA 라 사용자 PC 가
   다시 신뢰해야 한다 — 복구엔 사본을 쓴다. k8s 테스트베드(dms-k8s.local, 10.20.20.101)도 같은 CA 로 발급한다 — §13-5)
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
   luminous iptables 가 자기 IP 의 80/443/8080 을 VIP 로 DNAT 한다. 2026-10-21 부터 라우트·릴레이(k8s VIP
   10.20.20.101 과 그 :8443 릴레이 포함)는 testbed 저장소의 호스트 네트워크 스크립트와 systemd 유닛이 부팅마다
   다시 깐다 — 옛 손작업 규칙은 세션 스크래치 `setup-relay.sh`·`relay-tailscale.sh`·`relay-8080.sh` 였다.
   k8s 포탈엔 평문 릴레이(:8081)를 두지 않는다(2026-10-22 제거): force-ssl-redirect 가 포트 없는 `https://<host>/`
   = :443 = **ssc** VIP 로 보내 다른 포탈에 떨어졌다. k8s 포탈의 외부 입구는 `https://dms-k8s.local:8443` 하나.)
6. 검증(전부 실증됨): `curl --cacert ca.crt https://dms.local/` = 200(검증
   통과), `http://` = 308, 로그인 Set-Cookie 에 `Secure`, 로그인·admin API·SPA
   딥링크 https 로 200, `ip neigh show 10.20.20.100` = 선출 노드 MAC.
   **페일오버 실측**: 광고 노드의 컨트롤러 파드 강제 종료 → 11 초 다운 후 남은
   레플리카 노드로 ARP 재선출·자동 복구(svc 가 externalTrafficPolicy=Local 이라
   선출은 컨트롤러 파드가 있는 노드 중에서만).

7. **웹 인증 하드닝(2026-09-07, d119)** — 20-config 의 아래 키들이 라이브 자세다(2026-11-03 로그인 보호 장치 키 추가):
   - `DMS_PASSWORD_ENCRYPTION_REQUIRED: "true"` — 포탈은 비밀번호를 WebCrypto 로
     봉인해(`password_enc`, ECDH P-256 + HKDF + AES-256-GCM) 보내고 서버는 평문
     `password` 를 422 로 거절한다. 서버 키는 `DMS_SESSION_SECRET` 에서 유도되므로
     시크릿 회전 = 키 회전(포탈이 자동 재수신). **첫 관리자 부트스트랩**(x-admin-token
     curl)만 평문 허용: `curl -H 'x-admin-token: <ADMIN_TOKEN>' -d '{"username":..,
     "password":..}' https://<포탈>/api/admin/accounts`. 자동화가 로그인 세션이
     필요하면 `python -c 'from dms.api.password_transport import seal_with_info'`
     로 봉인해 보낸다(예: `~/.claude/jobs/.../tmp/login-sealed.py`).
   - `DMS_LOGIN_RATE_LIMIT_ATTEMPTS: "10"` / `..._WINDOW_SECONDS: "60"`: **아이디별** 실패 10회/분을
     넘으면 429 + Retry-After 다. 창이 지나면 저절로 풀린다(잠금 아님). 원 IP 로는 막지 않는다
     (2026-11-03 — SNAT 뒤 전원이 함께 잠겼다). 관찰만 하려면 이 ATTEMPTS 와 아래
     `DMS_LOGIN_ID_ATTEMPT_LIMIT` 를 **둘 다** 0 으로 둔다(이것만 0 이면 남의 틀린 비밀번호도 총 시도로
     세어 남의 아이디가 429 에 묶인다).
   - `DMS_LOGIN_ID_ATTEMPT_LIMIT: "60"`(/60초): 아이디별 총 시도(성공 포함)다. 맞는 비밀번호를 반복해
     CPU 를 태우는 경로를 막는다. e2e 한 벌의 admin 로그인은 약 31회라 영향이 없다(60 에 다가가면 e2e
     `env.ts` 에 이 키를 준다).
   - `DMS_LOGIN_SCRYPT_SLOTS: "2"` / `..._MAX_WAIT_SECONDS: "5"`: 비밀번호 검증 동시 실행 수다. 못 얻으면
     최대 5초 기다린 뒤 503 `login_busy`(과부하 신호, 차단 아님).
   - `DMS_LOGIN_GLOBAL_PACE_LIMIT: "0"`(꺼짐) / `..._WINDOW_SECONDS: "60"` / `..._MAX_WAIT_SECONDS: "15"`:
     대기형 전체 스위치다.
     - 스프레이 공격 중 출발지 차단(Avi·방화벽) 전까지 켠다. 예: LIMIT "60" → 전 아이디 실패를 분당
       60회로 늦춘다.
     - 켜고 끄는 절차는 ConfigMap 수정 → `kubectl --context <ctx> -n dms rollout restart deploy/dms-api`
       다(재시작은 메모리 카운터도 비운다).
     - 켠 채 기동하면 보안 이벤트에 `login_pacing_enabled` 가 남는다.
   - `DMS_LOGIN_ALARM_WINDOW_SECONDS: "600"` / `_FAILURES: "30"` / `_ATTEMPTS: "300"` /
     `_SOURCE_FAILURES: "20"`: 경보 임계다(이벤트만).
     - 출발지 경보는 창당 50건까지만 남는다.
     - 원 IP 가 한 주소로 모이는 배치(PROXY 없음)는 SOURCE_FAILURES 를 0 으로 둔다.
   - 확인은 포탈 **관리 → 보안 이벤트**에서 한다(보호 장치 현재 값·인증번호 잠금 해제·최근 이벤트·
     출발지 IP).
   - 검증(실증 절차): 평문 로그인 `curl -d '{"username":"u","password":"p"}'
     /api/auth/login` = 422 `password_encryption_required`; 봉인 로그인 = 200 +
     Set-Cookie; 틀린 비밀번호 봉인 10회 후 11번째(정답) = 429; 같은 출발지에서 다른
     아이디로 10회 실패한 뒤에도 내 아이디 로그인 = 200(IP 축 없음); 위조 `X-Real-IP` 는
     보안 이벤트의 출발지에 반영되지 않는다; 포탈 화면 로그인 정상 + 개발자도구 네트워크
     탭 본문에 `password` 없음.

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
이전 코드를 죽이지 않는다), 상한 초과 시 429 `verification_rate_limited`(Retry-After) -- 수신자별 5통/10분,
전체 300통/10분, 동시 발송 8건(모두 api 프로세스 메모리라 레플리카 수만큼 늘어난다; 거절된 요청은
어느 상한도 쓰지 않는다). 릴레이 자체의 `RELAY_RATE_LIMIT` 은 그와 별개로 걸린다.
가입·비밀번호 변경이 끝나면 같은 상한을 거쳐 완료 안내 메일(「계정 생성 완료 안내」·「비밀번호 변경 안내」)이
간다. 결과는 이벤트 `account_notice_sent`·`_stub`·`_failed`·`_skipped` 로 남고, 실패해도 가입·변경은 그대로다.

**테스트베드 검증(사내 릴레이 없음)**: `tests/fake_knox_relay.py` 가 같은 계약을 흉내 낸다 --
`python3 tests/fake_knox_relay.py --host 0.0.0.0 --port 8025 --token <T> --outbox <DIR>` 로 띄우고 포탈에서
**발송 방식을 `knox_relay` 로 바꾸고** IP·포트·키를 그 값으로 넣으면, 보낸 메일이 `<DIR>/NNNN.html` 로 떨어진다
(가입 흐름 끝까지 실검증 가능 -- 테스트 메일 버튼은 발송 방식과 무관하지만 가입·재설정 인증 메일은 knox_relay 일
때만 릴레이로 간다). 검증이 끝나면 발송 방식을 다시 비우거나 `stub` 으로 되돌린다(가짜 릴레이가 꺼지면 knox_relay
는 502 가 된다).

**인증번호 무차별 대입 상한**(6자리·5분, 2026-11-03):
- 코드당 5회 틀리면 그 코드가 죽는다.
- (아이디, 용도)별로 첫 실패부터 24시간 동안 누적 10회 틀리면, 그 창이 끝날 때까지 인증번호 발급·확인이 모두
  429 `verification_locked`(Retry-After)다.
- 모든 아이디를 합친 틀린 인증번호가 1시간에 1000회(`DMS_VERIFICATION_GUESS_GLOBAL_LIMIT`/`_WINDOW_SECONDS`)에
  닿으면, 셀프 가입·재설정(발급·확인)이 창이 밀려날 때까지 429 `verification_global_locked`다(이벤트
  `verification_global_locked`).

로그인은 영향이 없다. **구제**:
- 관리 → 계정의 「계정 생성」·「비밀번호 재설정」은 인증번호와 무관하다(세션 관리자만, 특권 계정은 특권 관리자만,
  대상 계정의 기존 로그인은 모두 끊긴다).
- 관리 → 보안 이벤트에서 아이디별 「잠금 해제」(DB `verification_failures` 행 삭제)와 「전체 잠금 해제」(api
  메모리)를 할 수 있다. 둘 다 감사 로그(`security`)와 이벤트에 누가 했는지 남는다.

원 IP 별 상한(옛 `verification_client_locked`)은 없앴다. api 프로세스 메모리 상한은 재시작하면 풀린다.

## 12. 작업(요청) 삭제 — 정리 상태·지연 대응 (2026-10-08)

관리자가 포탈 **작업 목록**에서 끝난 작업(종단 요청)을 골라 지운다(세션으로 로그인한 관리자만 — 공유 토큰은 403
`admin_session_required`; 진행 중인 작업은 선택 불가, 서버도 다시 거부한다). 배치 항목의 작업은 하나씩은 못 지우고
**배치 단위**로만 지운다(아래 「배치 단위 삭제」). 한 건은 요청 상세의 「작업 삭제」로도 지운다(2026-10-26 — 같은 API·같은
확인 창, 배치 자식·진행 중 잡이 있으면 버튼이 잠기고 이유를 보인다; 지우면 작업 목록으로 돌아가 결과를 알린다). 삭제는 두 단계다:

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
| `purge_base_unavailable` | base 를 열 수 없다(마운트 빠짐) 또는 삭제 시점 base 가 비어 있었다 | 컨트롤러 파드의 `<SHARED_DIR>/dms/artifacts` hostPath(노드의 공용 디렉터리 마운트, §2d)·artifact base 화면 3홉 확인 |
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
- 상한: 한 번에 배치 10개(`delete_batch_selection_too_large`), 배치 하나에 작업 2000개(2026-10-26 1000 → 2000)·항목 10000개(`batch_delete_too_large`
  — 배치 하나가 한 트랜잭션이라 그동안 api 가 멈춘다; 넘는 배치를 지우는 청크 삭제는 BACKLOG). 확인 창을 연 뒤 재실행 등으로
  작업 수가 바뀌었으면 `batch_changed`(무변화 — 목록을 다시 보고 다시 시도).
- 큰 배치의 파일·파드 정리는 오래 걸린다 — 정리 루프는 틱(15초)마다 20행을 진행하고, 파드가 남은 작업은 지운 파드가 끝났는지
  한 번 더 봐야 해서(작업마다 k8s 스윕 두 번) 작업 1000개면 **30분 안팎**(가짜 k8s 실측 27분), 2000개면 1시간 안팎, 한 번에 고를
  수 있는 최대(배치 10개 × 2000)면 9~10시간이다. 그동안 다른 삭제의 정리도 같은 줄에 선다. 툴바 「정리 중 N건」이 줄어드는 것으로 본다. 줄을
  서 기다린 시간은 지연이 아니다 — 「지연」(`purge_waiting_pods`)은 그 작업의 첫 k8s 스윕부터 10분이 지나도 파드가 남을
  때만이다.
- 배치 화면의 「배치 삭제」(`DELETE /api/admin/batches/{id}`)는 그대로 **배치 기록만** 지운다(작업은 남는다). 2026-10-26 부터
  세션 관리자만(공유 토큰 403 `admin_session_required`)이고, 확인한 뒤 그 사이 배치가 다시 돌기 시작했으면 409
  `batch_record_changed`(무변화), 지우면 감사 로그에 `batch / delete_record` 1행이 남는다. 그렇게 남은
  작업은 작업 목록 배치 열에 「기록 없음 xxxxxxxxxxxx」(배치 id 앞 12자 — 누르면 그 묶음만 거른 목록)로 보이고, 같은
  방식(배치 단위)으로 지운다. 배치 행도 작업도 없이 항목만 남은 묶음(옛 경합·롤링 업데이트 잔재 — 화면엔 안 보인다)은
  `POST /api/admin/requests:delete` 에 `{"batches":[{"batch_id":"<id>","expected_request_count":0}]}` 로 지운다.
- 다른 배치의 항목이 이 배치의 작업을 가리키는 비정상 DB 면 `batch_child_shared`(무변화) — 그 항목을 가진 배치를 먼저
  지운다. 두 배치를 한 번에 함께 고르면 서버가 그 배치를 지운 뒤 이 배치를 다시 시도하므로 선택 순서와 무관하게 둘 다
  지워진다.

**env(셋 다 `deploy/k8s/20-config.yaml`, 기본값과 같은 값):**

| env | 기본 | 소비자 | 뜻 |
|---|---|---|---|
| `DMS_REQUEST_DELETE_QUIET_SECONDS` | 60 | api | 요청·잡의 마지막 갱신 후 이 초가 지나야 지울 수 있다(`request_recently_finished`). 음수는 기동 거부, 0 은 e2e 전용 |
| `DMS_REQUEST_PURGE_INTERVAL_SECONDS` | 15 | controller | request-purge 루프 간격 = 실패 백오프의 기준 단위. 1 미만은 기동 거부 |
| `DMS_ARTIFACT_RETENTION_DAYS` | 1500 | controller(고르기)·api(표시) | 결과 파일 자동 만료의 보존일(2026-10-26, 0 = 끔, 음수는 기동 거부) — 아래 §12b |

마이그레이션은 자동이다(`migrate` 가 `request_purges` 테이블과 `idx_data_jobs_request`·`idx_plans_request`·
`idx_batch_items_request` 인덱스를 만든다). 배치 단위 삭제(2026-10-11)는 `idx_requests_batch(batch_id)` 를
`idx_requests_batch_order(batch_id, commit_order)` 로 바꾼다(새 것을 만든 뒤 옛 것을 지운다 — 한 배치만 거른 목록이 큰
테이블에서 전체를 훑지 않게). 새 인덱스를 만드는 동안 `requests` 쓰기가 기다린다(테이블 크기에 비례 — 다른 인덱스
추가와 같은 성질).

---

## 12b. 결과 파일 자동 만료(보존 정책, 2026-10-26)

`DMS_ARTIFACT_RETENTION_DAYS`(기본 **1500**, `0` = 끔, 20-config.yaml)일이 지난 종단 작업의 **결과 파일만** 지운다
(사용자 결정 「파일만 만료하고 행은 남김」). 요청·잡·상태 이력·결과 수치는 그대로 남고, 포탈 요청 상세의 출력 자리에
「결과 파일 만료됨(보존 N일, 날짜)」이 뜬다(파일 열람·다운로드 API 는 410 `artifact_expired`).

- controller `artifact-expiry` 루프(5분 — `request-purge` 와 같은 조건에서만 조립)가 대상(잡·요청 둘 다 종단, 종단 시각
  ≈ `data_jobs.updated_at` 이 N일 전보다 오래됨)을 골라 `data_jobs.artifacts_expired_at` 을 찍고 정리 아웃박스에
  `kind='expiry'` 행을 넣는다. 지우는 것은 위 2단계와 **같은 정리 루프**(파드 0 확인 → `.dms-trash` 떼어냄 → purge 파드)다.
- 정리 대기 중인 만료 행은 50개까지만 넣는다(작업 삭제 정리가 그 뒤에 오래 서지 않게) — 오래된 이력이 많아도 하루
  최대 ~1.4만 건씩 따라잡는다.
- 현황: 포탈 **아티팩트 경로** 화면의 「결과 파일 보존: … · 만료됨 N건 · 정리 대기 · 지연(사유)」 한 줄
  (`GET /api/admin/artifact-base` 의 `artifact_retention`). 작업 목록의 삭제 툴바 「정리 중 N건」에는 만료 행을 세지 않는다.
- 지연 사유는 위 표와 같다. 만료 전용 사유 `artifact_expiry_target_invalid` = 만료 표시가 사라졌거나(DB 복원) 잡 기록이
  비종단·없음 — 파일을 건드리지 않고 멈춘다(표시를 확인하고 되돌리면 이어서 끝난다).
- 강제 base 변경 이전에 쓴 결과 파일은 옛 base 에 남는다(`artifact_left_at_old_base` 경고 — 수동 정리).
- 값을 바꾸면 dms-controller·dms-api 재시작(ConfigMap). 만료는 되돌릴 수 없다 — 보존일을 크게 줄이기 전에 영향 범위를
  확인한다(`SELECT COUNT(*) FROM data_jobs WHERE artifacts_expired_at IS NULL AND updated_at < '<날짜>'`).
- 마이그레이션은 자동이다(`request_purges.kind`·`data_jobs.artifacts_expired_at`·부분 인덱스 `idx_data_jobs_expiry`). api 와
  controller 가 둘 다 새 이미지여야 한다(옛 api 는 만료 표시를 모르고 404 를 낸다). 첫 배포 뒤 오래된 이력이 많으면 며칠에 걸쳐
  따라잡고, 그동안 요청 삭제 정리는 만료 행 뒤에 최대 50행만큼만 선다.

---

## 12c. 2026-10-26 결정 묶음 — 운영자가 알아야 할 것

BACKLOG 결정 묶음(CHANGELOG 「BACKLOG 결정 묶음 구현」)에서 운영 표면이 바뀐 것만 모았다. 동작의 지도는 `docs/ARCHITECTURE.md`.

- **rm 경로 겹침 잠금**(ARCHITECTURE §4 「rm 경로 겹침 잠금」): rm 과 같은 스토리지에서 경로가 같거나 조상·자손인 작업(다른 rm,
  sync 의 소스·목적지, scan)은 늦게 온 쪽이 계획 단계에서 Conflict `path_conflict` 로 끝난다(요청 상세 「전이 이력」에 「겹친 요청」
  링크) — 겹친 작업이 끝난 뒤 다시 제출한다. 배치 자식도 같다(항목 실패 `path_conflict`, 「실패분 재실행」으로 다시). 이미 계획된
  잡은 실행 직전에 **보류**된다(실패가 아니다 — 이벤트 `path_overlap_hold`, 풀리면 `path_overlap_released`, 상태는 Executing·
  Preflight 그대로). 보류가 길면 상대 작업(이벤트의 요청 id)을 본다. 서로 다른 이름으로 등록한 포개진 스토리지끼리는 보지 않는다.
- **배치 CSV 는 서버가 해석한다**: 포탈 배치 생성의 「테이블에 반영」·파일 올리기와 배치 상세 「CSV로 전체 교체」는
  `POST /api/admin/batches:parse-csv`(본문 `{operation, csv, storage?, source_storage?, destination_storage?}`, 2 MiB 413
  `batch_csv_too_large`·데이터 행 10000 초과 422 `batch_csv_too_many_rows`)를 부르고, 오류가 있는 줄(물리 줄 번호·사유)이 하나라도
  있으면 반영하지 않는다. 템플릿 `GET /api/admin/batches/csv-template?operation=scan|sync|rm`, 항목 내보내기
  `GET /api/admin/batches/<id>/items.csv`(BOM·수식 방어 `'` — 그대로 다시 올리면 같은 항목). 셋 다 조회라 공유 토큰도 된다(생성은
  그대로 특권 세션). 배치 생성의 스토리지 칸은 등록된 스토리지 드롭다운이다.
- **dscan 총바이트·잘못된 인코딩 이름 수 — 잡 이미지 재빌드 필요**: `Dockerfile.mpifileutils` 의 `MPIFILEUTILS_REF` 가 ae781bd 이후(2026-10-28 부터 7a3189c — dscan 은 그대로)다.
  포탈 빌드에서 `dms-mpifileutils` 를 체크해(§8-5 — 기본 선택이 아니다) 같은 태그로 `dms-agent` 까지 빌드하고 잡 이미지를 올려야
  새 scan 리포트에 `summary.total_bytes`·`summary.invalid_name_entries` 가 생긴다. 옛 이미지·저장된 리포트는 총바이트 = 시간
  히스토그램 합(같은 값), 이름 수 = 「기록 없음」. 이름 수는 카운터일 뿐 broken 사유가 아니다(사용량 분석 상세·합계 각주·CSV 열).
- **직접 경로 목적지의 결과 소유**: 특권 운영자의 직접 경로 sync(배치 포함)도 목적지 스토리지가 PV 종류면 늘, 둘 다(both) 종류면
  지금 인벤토리의 PV 루트 아래일 때 그 스토리지의 기본 소유자로 `--chown` 된다(§13 표). 제출 뒤 기본 소유자·종류·PV 루트가
  바뀌면 컨펌(배치는 자식 실행 직전 — 그 자식만 Rejected)에서 `storage_owner_changed` 로 멈춘다 — 다시 제출(재실행).
- **배치 기록 삭제·배치 단위 삭제 상한·요청 상세 삭제·빌드 삭제·보안 설정 배지**: §12(세션 관리자·409 `batch_record_changed`·
  감사 `batch / delete_record`, 작업 2000개), §8-2b, §9-8.
- **마이그레이션**(자동): `builds.pods_gc_at` + `idx_builds_gc`, `request_purges.kind`, `data_jobs.artifacts_expired_at` +
  `idx_data_jobs_expiry`. RBAC·ConfigMap 키 추가는 `DMS_ARTIFACT_RETENTION_DAYS`(기본값과 같음) 하나.

---

## 12d. 2026-10-28 dms-ssc 이식 — mpifileutils 7a3189c · 경로 봉쇄 통일 · 실패 사유 · 통계 (운영자가 알아야 할 것)

CHANGELOG 「dms-ssc 이식」에서 운영 표면이 바뀐 것만 모았다. 동작의 지도는 `docs/ARCHITECTURE.md`.

- **잡 이미지 재빌드 필요**: `Dockerfile.mpifileutils` 의 `MPIFILEUTILS_REF` = 7a3189c 이고 잡 러너(`dms_job_runner` — summary v2·도구
  결과 사유·경로 봉쇄 재확인)도 바뀌었다. 포탈 빌드에서 `dms-mpifileutils` 를 체크해(§8-5 — 기본 선택이 아니다) 같은 태그로 세 이미지를
  빌드하고, 릴리스는 agent → job-image·controller·api. 배포 순서는 안전과 무관하다 — 새 제어면 + 옛 잡 이미지면 summary 가 3키라 결과
  화면은 중립 라벨·도구 실패는 일반 `execution_failed`, 옛 제어면 + 새 잡 이미지면 새 사유 마커를 몰라 `execution_failed` 로 접는다. 봉쇄는 어느
  조합이든 예전보다 약해지지 않는다(직접 경로 sync 의 봉쇄는 새 제어면의 사전 점검·env 가 있어야 생긴다). 새 사유·결과 화면을 보려면 둘 다
  새 태그.
- **동작이 바뀐 것**(사용자 문의에 답할 것):
  - 목적지의 비어 있지 않은 디렉토리를 소스의 파일·링크로 바꾸는 sync 는 「삭제」가 꺼져 있으면 실패한다(`sync_type_change_refused`) — 옛
    nsync 는 그 안의 목적지 전용 항목을 지워 버렸다(C8, 모든 잡이 root 라 데이터 손실). 바꾸려면 삭제를 켜고 다시 실행.
  - rm 이나 sync 의 삭제가 일부를 못 지우면 이제 Failed(`rm_items_failed`·`sync_items_failed`) — 전엔 Succeeded 였다.
  - nsync 는 목적지 공간·쿼터 부족(ENOSPC·EDQUOT)과 읽기 전용(EROFS)에서 바로 멈추고(`destination_full`·`destination_read_only`), 실패가
    65,000건(포크 기본 — DMS 에서 바꿀 수 없다)에 닿아도 멈춘다(`sync_stopped_max_errors`). 원인을 고치고 다시 실행하면 이어서 맞춘다.
  - nsync 의 `open_noatime` 이 실제로 동작한다(소스 atime 보존 — 전 이미지에선 무동작이었다).
  - **직접 경로 sync 도 경로 봉쇄**를 받는다: 관리 디렉토리 아래 경로의 어느 단계든(소스·목적지 자신 포함) 심링크면 사전 점검·실행 직전·
    도구 직전에 거부(`source_path_redirected`·`destination_path_redirected`). 관리 디렉토리 자신·그 위의 링크(마운트 경로 링크)는 괜찮다 —
    링크를 거쳐 지정하던 경로는 실제 경로로 다시 낸다. 직접 경로 scan 은 봉쇄 없음(종전), rm 은 대상 자신이 링크면 링크만 지운다(종전).
  - direct(O_DIRECT) 를 켜면 bufsize 는 4096·2^k ≤ 4 MiB 여야 한다 — 아니면 제출·배치 생성·실행 설정 변경이 422 `direct_bufsize_misaligned`.
- **실패 원인 보기**: 요청 상세 「실행 로그」(launcher 로그)의 `DMS_EXEC_REASON=<code>` 줄이 사유이고, 그 뒤 `DMS_JR_TOOL_OUTPUT` 머리 아래
  `  | ` 줄이 도구 출력 발췌(stdout 끝 80·stderr 끝 20줄, 합계 10 KiB)다. 전체는 stdout.log·stderr.log(결과 파일 만료 전 — §12b), 결과 화면의
  「실패 항목」은 summary.json 의 표본(최대 10개).
- **지표 API 키 변경**: `GET /api/admin/metrics/jobs` 의 `files_total`·`bytes_total` 이 없어지고 `sync_items_total`·`sync_bytes_total`·
  `rm_items_total`(실패·중단된 실행이 실제로 처리한 수 포함 — 이식 전에 실패한 행은 옛 뜻이라 빼고 센다)이 생겼다. 분해 행에 `rejected`·
  `cancelled`. 옛 키를 읽는 외부 스크립트는 고친다.
- **마이그레이션·설정**: 없음(새 컬럼·ConfigMap 키 없음).

---

## 13. 두 번째 테스트베드 — k8s 클러스터(`dmsk8s`), LDAP 없음 (2026-10-21)

dms-k8s 브랜치 후보(main)를 검증하는 테스트베드다. 오버레이는 `deploy/overlays/testbed-k8s`, 컨텍스트는 `dmsk8s`,
포탈은 https://dms-k8s.local(VIP 10.20.20.101). 맨 위 「테스트베드는 둘이다」 표가 ssc 와의 차이 전부다.

**LDAP 을 쓰지 않는다**(사용자 결정 2026-10-21: 「dms-k8s 브런치에서는 LDAP 을 사용 안할거야. 이점은 확실히해둬.」).
2026-10-26 부터 **코드에 LDAP 이 없다**(사용자 결정 (d) 「LDAP 코드 삭제 및 정리」): 리졸버·노드 신원 프로브·nslcd·보조 그룹이
지워졌고, base 매니페스트에도 LDAP 키·에이전트 LDAP env·nslcd 엔트리포인트가 없다(에이전트는 `dms agent` 로 직접, 시크릿
4키). 그래서 오버레이는 사이트 값만 덮는다(예전의 LDAP null 패치·`patch-agent.yaml` 은 지웠다). `DMS_IDENTITY_MODE` 가 k8s 가
아니거나 `DMS_LDAP_URI` 가 있으면 api·controller 가 **기동을 거부**한다 — ssc 설정·시크릿을 잘못 섞으면 시끄럽게 실패한다.
`tests/test_testbed_k8s_overlay.py` 가 렌더 결과에 `ldap`(대소문자 무시) **0건**을 고정한다.

2026-10-25 부터 이 클러스터는 **k8s 신원 모드**다(2026-10-26 부터 유일한 모드 — base 의 `DMS_IDENTITY_MODE: "k8s"` +
`patch-config.yaml` 의 `DMS_USER_ALLOWED_OPERATIONS: "sync,rm"` — scan 은 특권 운영자만(2026-10-26), dms-k8s RBAC·PV 설계 `docs/history/specs/2026-10-25-dms-k8s-rbac-pv-design.md`).
모든 단건 잡이 root 이고 권한은 대상 클러스터의 RoleBinding 맵핑(`<id>-clusterrole-admin|edit` + ClusterRole
`kubeflow-admin|edit` → 그 namespace 의 모든 PVC 읽기·쓰기, `<id>` 의 `-` → `.` = DMS 계정 아이디)으로 판정한다(코드 경로
`k8s_access`·`identity.root_allowed` — 조용히 다른 신원으로 떨어지는 경로는 없다):

| 경로 | 결과 |
|---|---|
| 일반 사용자(세션) + PV 주소(`pv`/`source_pv`/`destination_pv`)로 scan·sync·rm — 맵핑이 있는 namespace | **동작**(서버가 `run_as_root: true` 를 싣는다 — uid 0/gid 0, sync 허용 쌍 면제, 컨펌은 본인) |
| 일반 사용자·특권 목록 밖 관리자의 직접 경로(storage + 경로) | 403 `raw_path_not_allowed` |
| 맵핑이 없는 namespace / 한 번도 동기화 안 된 클러스터 | 403 `k8s_access_denied` / 409 `k8s_mapping_unknown` |
| PV 루트 rm(하위 경로 `''`·`'.'`·`'./'`) | 422 `pv_root_rm_forbidden` |
| PV 목적지 sync 의 결과 소유(2026-10-26) | 목적지 스토리지의 기본 소유자(스토리지 화면 「기본 소유 uid:gid」, 기본 0:0)로 `--chown` — 일반 사용자는 늘 이것(선택 없음). 특권 운영자만 「결과 소유: PV 루트 소유자」(`pv_owner: "pv_root"` — 비면제 403 `pv_owner_root_not_allowed`)나 chown 지정. 제출 뒤 기본값이 바뀌면 계획 Rejected / 컨펌 409 `storage_owner_changed`(다시 제출), 저장값이 무효면 409 `invalid_storage_owner`. 직접 경로 목적지(배치 포함)는 그 스토리지가 PV 종류면 늘, 둘 다(both)면 PV 루트 아래일 때 같은 기본 소유자로(2026-10-26 결정 (4)), 그 밖(파일시스템·PV 밖)은 소스 소유 보존 |
| 특권 세션 관리자(`DMS_PRIVILEGED_REQUESTERS` = root,admin,mason ∪ 계정 화면 부여) | 맵핑 면제 — 직접 경로·전체 PV·PV ↔ 직접 경로 혼합 |
| 대기·컨펌 대기 중 권한 회수·PVC 재생성 | 계획 Rejected / 컨펌 409(`k8s_access_denied`·`pv_moved`·`pv_not_found`) |
| `run_as_root: false` 명시 | 422 `run_as_root_required` |
| Bearer 공유 토큰 제출 | 403 `privileged_not_authorized`(root 는 세션 필요) — 자동화는 봉인 세션 로그인 |
| 그룹 denylist 규칙 추가 | 422 `denylist_group_unsupported`(평가하지 않는다 — 이름 규칙만, 옛 group 행은 「평가 안 됨」·해제만) |
| `run_as_root` 없는 옛 요청 행·목록 밖 생성자의 배치 자식 | 계획 Rejected `privileged_not_authorized`(비 root 로 낮추지 않는다) |
| 사용자 스캔 경로 등록·통계 | 면제자 전용(비면제 422 `scan_path_unrestricted_only`) |
| 로그인·가입·재설정·`/me`·사용량 분석·아티팩트 | 동작(LDAP 무관) |

보조 그룹·실행 신원의 LDAP uid/gid 같은 LDAP 기능은 dms-ssc 라인의 것이고 **ssc 테스트베드에서**(dms-ssc 체크아웃으로) 검증한다. 대상 클러스터 등록(API 서버·읽기 전용
리더 토큰)·호출 노드·StorageClass 매핑 런북은 아래 **13-9**(k3 실증 2026-10-26).

### 13-1. 인프라 전제 (testbed 저장소 몫 — 여기서는 확인만)

- VM `k8s-cp1` 10.10.10.20 · `k8s-w1` .21 · `k8s-w2` .22(dmsnet), k8s·CRI-O·Cilium 은 ssc 와 같은 버전(pod 10.245.0.0/16,
  svc 10.112.0.0/12), Volcano v1.15.0, MetalLB(자동 풀 10.10.10.220–.229 — ssc 의 .200–.210 과 겹치면 ARP 충돌) +
  ingress-nginx 2 레플리카(`--default-ssl-certificate=dms/dms-portal-tls`, svc 어노테이션 `metallb.io/address-pool=dms-public-pool`),
  `k8s-w1`·`k8s-w2` 둘 다 `network-zone=public`. kubeconfig 에 컨텍스트 `dmsk8s`(current 는 `dms` 유지).
- OS(2026-11-03 T-SE~): cp1 Ubuntu 24.04, w1·w2 **Rocky Linux 9.6**(SELinux permissive, firewalld 꺼짐, CRI-O·kube rpm, ceph-common 19.2.2) — testbed
  IaC 의 EL 분기(넘기기 대기 동안 진짜 testbed 저장소에서 terraform·ansible 을 돌리지 않는다 — BACKLOG). 맨 위 표.
- 노드: **LDAP 클라이언트 없음**(sssd·nslcd 미설치, nsswitch 에 sss/ldap 없음, `getent passwd alice` 실패 — testbed validate 의
  부정 검사), CRI-O 가 `pkg-01:5000`·`pkg-01:5001` 신뢰, `/etc/hosts`·CoreDNS hosts 에 pkg-01(api 파드가 레지스트리를 조회),
  CephFS `dmsfs:/k8s` → `/cephfs`(둘 다), `/k8s/third` → `/cephfs-third`(k8s-w1), `/k8s/secondary` → `/cephfs-secondary`(k8s-w2)
  — cephx `client.dmsk8s` 는 MDS `path=/k8s` + OSD `namespace=k8s`(`/k8s` 레이아웃 `pool_namespace=k8s`)로 두 층에서 갇힌다,
  빌드 소스 `/home/mason/dms-dev/dms` ro NFS(체크아웃별 export·워커 IP 만 — 부모 `/home/mason/dms-dev` 는 export 하지 않는다).
- pkg-01: `registry-k8s` :5001(§0), PostgreSQL `dmsdb_k8s` / 롤 `dmsapp_k8s`(scram — pg_hba 는 DB 별: `dmsapp_k8s` 만
  `dmsdb_k8s` 에, LDAP 사용자 로그인은 이 DB 에 닿지 않는다; PUBLIC CONNECT 회수).
- 호스트: `10.20.20.101/32 dev dmsbr0` onlink 라우트, `/etc/hosts` 의 `10.20.20.101 dms-k8s.local`, (선택) 외부 릴레이
  :8443 → .101:443(평문 릴레이 없음 — §10), 테스트베드 CA(`~/dms-dev/testbed/files/tls/`, gitignore; 영구 사본
  `/data/dms-testbed/backup-20261021/tls/`).

```bash
kubectl --context dmsk8s get nodes -o wide
# 점검 스크립트도 --context 로 대상을 고른다(2026-10-26 — current-context 를 바꾸지 않는다; KUBE_CONTEXT 환경변수도 같다):
PORTAL_VIP=10.20.20.101 sh deploy/preflight-cluster.sh --context dmsk8s
sh deploy/verify.sh --context dmsk8s
```

### 13-2. 레지스트리 시드 `d0`

첫 설치는 포탈이 아직 없어 포탈 빌드를 못 한다 — ssc 레지스트리의 검증된 이미지를 :5001 에 **`d0`** 으로 복사한다(pkg-01 에서,
testbed `make registry` 가 :5001 을 띄운 뒤). `d0` 은 숫자 태그라 포탈의 다음 태그 제안이 곧바로 `d1` 이 된다(2026-10-21 첫 설치는
옛 관례로 `k0` 이었다):

```bash
for pair in dms:d184 dms-agent:d179 dms-mpifileutils:d183; do
  repo=${pair%%:*}
  sudo docker pull localhost:5000/$pair && sudo docker tag localhost:5000/$pair localhost:5001/$repo:d0 \
    && sudo docker push localhost:5001/$repo:d0
done
curl -s http://localhost:5001/v2/_catalog
```

시드 이미지에 동봉된 매니페스트는 :5000 태그로 스탬프돼 있어 k8s 포탈의 드리프트 배지는 첫 포탈 빌드 전까지 「모름」이다(정상, §8-2).

### 13-3. DB · 13-4. 공유 FS

DB·롤은 testbed `postgresql_server` 롤이 만든다(`dmsdb_k8s`/`dmsapp_k8s`, 비밀번호는 testbed `secrets.yml`). 스키마는 migrate 가
만든다. **앱 DB 는 2026-11-03 T7 부터 클러스터 안 CNPG `dms-pg`** 다(§2f-8 — 아래 13-5 의 pkg-01 URL 은 첫 설치 때 값이고 지금은
`dms-secrets-prev` 의 되돌림 URL). 공유 FS 레이아웃은 §2 의 플레이북을 `-e dms_cluster=dmsk8s` 로(k8s-w1 이 `/k8s` 하위 트리 안에 `/cephfs/dms`(770)·
`/cephfs/dms/artifacts`(755)·`/cephfs/managed`·`/cephfs/managed/e2e` 를 만든다 — LDAP 픽스처 없음). **DMS 상태(아티팩트 base)는
2026-11-03 ③(d18 T3)부터 공용 디렉터리 `/dms_shared` 다**(3노드 NFS 마운트, S0 `sh deploy/shared-dir-init.sh /dms_shared` — §2d-5) —
플레이북의 `/cephfs/dms`·`/cephfs/dms/artifacts` 는 옛 base(보존 창)이고, 데이터 root `/cephfs/managed`·픽스처는 그대로다.

### 13-5. 네임스페이스 · 시크릿(4키) · TLS

```bash
kubectl --context dmsk8s apply -f deploy/k8s/00-namespace.yaml        # 이미지 없는 유일한 raw apply
kubectl --context dmsk8s -n dms create secret generic dms-secrets \
  --from-literal=DMS_DATABASE_URL='postgresql://dmsapp_k8s:<DB_PASSWORD>@10.10.10.30:5432/dmsdb_k8s' \
  --from-literal=DMS_SHARED_TOKEN="$(openssl rand -hex 24)" \
  --from-literal=DMS_ADMIN_TOKEN="$(openssl rand -hex 24)" \
  --from-literal=DMS_SESSION_SECRET="$(openssl rand -base64 32)"
#   ↑ 바인드 비밀번호 키 없음(ssc 시크릿을 복사해 오지 않는다). 토큰은 ssc 와 공유하지 않고 새로 만든다.
kubectl --context dmsk8s -n dms get secret dms-secrets -o go-template='{{range $k,$v := .data}}{{$k}}{{"\n"}}{{end}}'   # 정확히 4줄
# TLS: 같은 테스트베드 CA 로 SAN DNS:dms-k8s.local + IP:10.20.20.101(+ 외부 릴레이 IP) 서버 인증서 → secret tls dms-portal-tls
#      (testbed 의 portal-tls 스크립트: `<context> <dns> <vip> [extra-ip]` = dmsk8s dms-k8s.local 10.20.20.101 ...)
```

### 13-6. 오버레이 적용(항상 `--context dmsk8s`)

```bash
kubectl --context dmsk8s -n dms delete job dms-migrate --ignore-not-found
sh deploy/overlays/guard-images.sh deploy/overlays/testbed-k8s --context dmsk8s   # 다른 컨텍스트면 exit 4, 이미지 != 라이브면 3, hostPath != 라이브면 5(§2d)
kubectl --context dmsk8s apply -k deploy/overlays/testbed-k8s
kubectl --context dmsk8s -n dms wait --for=condition=complete job/dms-migrate --timeout=180s
kubectl --context dmsk8s -n dms rollout status deployment/dms-api
kubectl --context dmsk8s -n dms rollout status deployment/dms-controller
kubectl --context dmsk8s -n dms rollout status daemonset/dms-agent
```

LDAP 없음 확인(배포 게이트 — 하나라도 어긋나면 진행하지 않는다):

```bash
kubectl kustomize deploy/overlays/testbed-k8s | grep -ci ldap                          # 0
kubectl --context dmsk8s -n dms get cm dms-config -o yaml | grep -ci ldap               # 0
kubectl --context dmsk8s -n dms exec deploy/dms-api -- env | grep -c LDAP              # 0
kubectl --context dmsk8s -n dms get ds dms-agent -o jsonpath='{.spec.template.spec.containers[0].command}'   # ["dms"]
kubectl --context dmsk8s -n dms exec ds/dms-agent -- sh -c 'test -S /run/nslcd/socket && echo nslcd-up || echo no-nslcd'   # no-nslcd
```

### 13-7. 첫 관리자 · 시드 · 컨트롤 상태

- 첫 관리자: `curl --cacert <CA> -H "x-admin-token: <ADMIN>" -H 'content-type: application/json'
  -d '{"username":"mason","password":"<pw>"}' https://dms-k8s.local/api/admin/accounts` — mason 은 특권 목록에 있어 root 자격이
  있다(로컬 계정 — dms-k8s 에는 LDAP 이 없다). 이후 자동화는 봉인 로그인(§10-7).
- 시드: §6 과 같은 스토리지 이름·경로(`cephfs-dms` `/cephfs` → `/cephfs/managed`, `cephfs-third`, `cephfs-secondary`)·정책·쌍을
  `API=https://dms-k8s.local`, 토큰은 `--context dmsk8s` 시크릿에서 읽어 넣는다. **그룹 denylist 규칙은 넣지 않는다.**
- 컨트롤 상태(세션 관리자 전용, 전체 치환이라 GET 후 전 필드): `build_node_name=k8s-w1`, `build_source_path=/home/mason/dms-dev/dms`.
  포탈 부제(`PUT /api/admin/portal-settings`)를 「K8S 테스트베드」 등으로 두면 두 포탈이 헷갈리지 않는다.
- 쿠키: 두 포탈의 세션 쿠키 이름이 같다(`dms_session`) — 외부에서 같은 IP 의 다른 포트로 둘을 열면 서로 로그아웃시킨다.
  호스트 이름(dms.local / dms-k8s.local)으로 구분해 연다.

### 13-8. 빌드 → 릴리스 → 태그 bump(`dN`)

포탈 「빌드」에서 images `[dms-mpifileutils, dms, dms-agent]`, 태그는 **제안 칩(`d<다음 번호>`)을 그대로** 쓴다 — 제안은 이 사이트의
라이브 태그·이 레지스트리(2026-11-03~ 클러스터 내 `10.112.0.50:5000`, 그 전 :5001)·빌드 이력의 `dN` 최댓값 + 1 이라 git 에 커밋된 오버레이 태그와 무관하다(프로덕션도 같은 코드로
자기 레지스트리·이력의 다음 번호를 제안한다). 2026-10-26 에 옛 `kN`(k0~k4)에서 `dN` 으로 바꿨다 — d1 부터. 릴리스(agent → api →
controller, 잡 이미지) 뒤 `deploy/overlays/testbed-k8s` 의
`newTag`·`DMS_JOB_IMAGE` 를 맞추는 커밋을 **dms-k8s 에서만** 만든다(2026-10-22 브랜치 분리 — ssc 오버레이 `testbed` 는 같이
건드리지 않는다). 포탈 릴리스는 이미지만 패치하므로 에이전트의 `dms agent` 명령 오버라이드는 롤아웃 뒤에도 남는다.

**ConfigMap 만 바뀐 apply 는 파드를 다시 띄우지 않는다**(k3 실증): `dms-config` 는 `envFrom` 이라 값이 기동 때만 읽히고, 이미지는
포탈 릴리스가 이미 바꿔 둬서 `apply -k` 가 파드 템플릿을 바꾸지 않는다. 신원 모드처럼 env 를 바꾼 apply 뒤에는
`kubectl --context dmsk8s -n dms rollout restart deploy/dms-api deploy/dms-controller` 로 다시 띄우고 `/api/auth/me` 의
`identity_mode` 로 확인한다(에이전트는 신원 모드를 읽지 않는다).

### 13-9. k8s 연동 — 대상 클러스터·호출 노드·스토리지·StorageClass 등록 (k3 실증 2026-10-26)

전제(먼저 확인):
- **호출 노드의 에이전트가 대상 API 서버에 닿는다** — 에이전트 파드 안에서 `getent hosts k8s-cp1`·6443 TCP(테스트베드: 클러스터
  DNS 가 `k8s-cp1` → 10.10.10.20 을 푼다). 대상 방화벽에는 개요 화면의 「주·보조 호출 노드 IP」만 열면 된다.
- **PV 경로를 담은 스토리지가 DMS 워커에 마운트돼 있다** — 테스트베드 `/k8s_pv/{nfs,gpfs,onefs}`(testbed `group_vars/all.yml`
  `k8s_pv_mount_options`). **NFS 기반 스토리지(OneFS 포함)는 `lookupcache=positive`** 로 마운트한다: 다중 노드 dsync 에서 rank 0
  이 만든 목적지 디렉터리 아래를 다른 노드의 rank 가 음성 dentry 캐시 때문에 ENOENT 로 실패했다(k3 실증 — 기본 `lookupcache=all`).
  remount 로는 못 바꾼다(umount → mount). CephFS·GPFS 는 캐시가 일관돼 해당 없다.
- **PV 스토리지를 노드마다 따로 마운트해도 된다** — 두 스토리지를 함께 마운트한 노드가 없으면 PV 로 보내는 sync 도 nsync 로
  돈다(2026-10-26, d2 — 잡 이미지의 dms-mpifileutils 2a10a18 이 목적지 안 링크를 따라가지 않는다. 그 전 k1~k4 는
  `pv_sync_needs_shared_node` 로 막았다). 함께 마운트한 노드가 있으면 종전대로 dsync 가 우선이다.
- **가입 인증 메일이 신원 증명이다** — 권한이 「DMS 계정 아이디 == RoleBinding 의 ID」로 묶이므로, 메일러가 stub(코드 에코)이거나
  인증이 꺼져 있으면 아직 계정이 없는 사람의 아이디로 누구나 먼저 가입해 그 사람의 PV 권한을 가져간다. 운영 사이트는 관리 →
  메일 설정에서 Knox 릴레이를 켠다(개요 화면이 stub·꺼짐을 경고한다 — 테스트베드는 stub 라 경고가 뜨는 것이 정상).

절차(포탈 — 전부 **세션 관리자**만, 공유 토큰으로는 조회만):
1. **리더 계정**: 「클러스터 등록」 창의 「대상 클러스터에 만들 읽기 전용 계정」 YAML(ClusterRole: rolebindings·persistentvolumeclaims·
   persistentvolumes·storageclasses list + namespaces/kube-system get, ServiceAccount, 장기 토큰 Secret)을 대상 클러스터에 apply
   하고 Secret 의 `data.token`(base64 그대로 붙여도 된다)과 CA 를 받는다. 테스트베드는 testbed `scripts/k8s-pv-reader-creds.sh` 가
   만든 `dms-reader/dms-inventory` 를 쓴다 — 토큰 파일은 0600, 화면·로그에 출력하지 않는다(포탈이 봉인해 보내고 서버는 암호화해 저장).
2. **스토리지**(스토리지 화면, 「종류」·「기본 소유(uid:gid)」): 테스트베드 값 —

   | 이름 | mount_path = managed_root | backend | 종류 | 기본 소유자 |
   |---|---|---|---|---|
   | `k8s-pv-nfs` | `/k8s_pv/nfs` | nfs | PV | 0:0 |
   | `k8s-pv-gpfs` | `/k8s_pv/gpfs` | gpfs | PV·파일시스템(both) | 0:0 |
   | `k8s-pv-onefs` | `/k8s_pv/onefs` | onefs | PV | 0:0 |
   | `cephfs-dms`(기존) | `/cephfs` · `/cephfs/managed` | cephfs | 파일시스템 | —(쓰이지 않아 칸이 없다) |

   에이전트 보고 한 주기 뒤 상태가 Ready 가 된다. **기본 소유자**(2026-10-26)는 PV·둘 다 종류에서만 보이고, 그 스토리지의 PV 를
   목적지로 하는 sync 결과(목적지와 복사본 전부 — 직접 경로 목적지도 PV 종류면 늘, 둘 다 종류면 PV 루트 아래일 때, 2026-10-26)가
   이 uid:gid 로 맞춰진다 — 일반 사용자는 늘 이 값, 특권 운영자만 「PV 루트 소유자」를
   고를 수 있다. 생략·기배포 행(migrate 가 0 으로 채운다) = 0:0(root:root), 범위는 0..4294967294. 바꾸면 이미 제출됐지만 아직 실행되지
   않은 그 PV 목적지 sync 는 계획·컨펌에서 `storage_owner_changed` 로 멈춘다(다시 제출하면 새 값). PV 를 실제로 쓰는 파드의
   uid/fsGroup 과 맞추려면 여기서 바꾼다.
3. **호출 노드·주기**(k8s 연동 개요): 주 `k8s-w1`, 보조 `k8s-w2`, 5분.
4. **클러스터 등록**: 이름 `dmsk8s`, API 서버 `https://k8s-cp1:6443`, TLS 「지정한 CA」, 토큰 → 「지금 동기화」. 첫 run 은 노드 확인
   주기(15초) 안에 가져가 수십 ms 에 적용된다 — 지문 = kube-system uid(`ca1f5a86-…`). 같은 클러스터를 다른 주소로 또 등록하면 카드가
   「같은 클러스터로 보입니다」로 경고하고 사용자 고르기에 같은 PVC 가 두 번 보인다(권한도 클러스터마다 따로 판정된다).
5. **StorageClass 매핑**(StorageClass 탭): 동기화로 SC 가 보인 **뒤에만** 된다(전에는 404 `storage_class_not_found`). `tb-nfs →
   k8s-pv-nfs`, `tb-gpfs → k8s-pv-gpfs`, `tb-onefs → k8s-pv-onefs`(템플릿 비움 = 백엔드 프리셋 `{subdir}`·`{volume}/{volume}-data`·
   `data/csi/{volume}`). 「예시 경로」가 워커의 실제 디렉터리와 같은지 확인한다(`ssh k8s-w1 ls -d <예시 경로>`).
6. **계정**: RoleBinding 이름에서 뽑은 ID(`junwoo-song-clusterrole-admin` → `junwoo.song`)와 **같은 아이디**로 가입하거나 관리자가
   만든다. 사용자 권한 탭의 「계정 없음」·「subject 불일치」로 빠진 사람을 찾는다.
7. **확인**: PV 탐색(PVC 7 · Bound 6), 사용자 권한(인식 바인딩 5 · 사용자 4), 일반 사용자 로그인 → 단일 작업에서 자기 namespace
   PV 만 보이는지, scan 이 실제 파일 수·바이트를 내는지.

장애 표시(실증): 토큰 거부 → 카드 「실패 · API 서버가 토큰을 거부했습니다(401)」, TLS 서버 이름 불일치 → 「API 서버 인증서를 검증하지
못했습니다」 + 원문(호스트 이름 불일치), 둘 다 노드 탓이 아니라 보조 노드로 넘기지 않는다. 주 노드 에이전트가 멈추면 요청 90초 뒤
보조 노드가 가져간다(SIGSTOP 실증: 93초). 권한 회수(RoleBinding 삭제)는 다음 동기화에 반영되고 컨펌 대기 중이던 작업은 컨펌 때
409 `k8s_access_denied` 로 끝난다.

## Unresolved values to fill in during live validation

- (`DMS_LDAP_BIND_PW` is gone on dms-k8s -- 2026-10-26 LDAP code removal; `dms-secrets` has 4 keys, and a
  `DMS_LDAP_URI` left in the ConfigMap refuses startup. The ssc testbed's LDAP secret lives in dms-ssc's runbook.)
- **`DMS_ALLOW_PRIVILEGED_REQUESTERS` / `DMS_PRIVILEGED_REQUESTERS`**
  (`deploy/k8s/20-config.yaml`, ConfigMap): **default is `true` /
  `root,admin`** (also the code default in `src/dms/config.py`). Being in the
  list is **eligibility only** (2026-09-30 incident: an admin's sync with
  "실행 신원 = a user" ran as root and rewrote another user's 700 destination
  to the source owner). **On dms-k8s every job runs as uid 0/gid 0 (root)** (k8s identity mode, the only mode
  since 2026-10-26): the server stamps `run_as_root: true` on single requests (omitted = root, explicit false = 422
  `run_as_root_required`) and allows it only for session-authenticated requests (`identity.root_allowed`) -- Bearer
  shared-token submissions get 403 `privileged_not_authorized`. What a user may touch is decided by the k8s
  RoleBinding mapping (PV addresses only), not by root eligibility; the list (env ∪ granted accounts) is the
  **exemption** from the mapping (raw paths, every PV) for session admins, and decides batch children ("root if the
  batch creator is eligible" -- otherwise the child is rejected `privileged_not_authorized`, never downgraded).
  Rows with no root basis (an old request without `run_as_root`) are rejected the same way. The stepper re-checks
  every root job against its request before each submission and fails it closed (`privilege_not_requested`).
  The gate keys on the authenticated `requester_id`, NOT the client-supplied `owner_username`
  (owner_username != actor needs the same eligibility -> otherwise 403 `privileged_not_authorized`).
  `DMS_ALLOW_PRIVILEGED_REQUESTERS: "false"` refuses startup on dms-k8s (it would stop every job); an empty
  `DMS_PRIVILEGED_REQUESTERS: ""` leaves only the mapping (no exempt admins, no batch children).
  **Granting eligibility without a redeploy (2026-10-12)**: the admin role
  alone never grants root (the list is the boundary between admins -- an
  unlisted admin must not be able to promote any account and get root). An
  admin who is already eligible (session) can grant/revoke root eligibility
  to another **active admin** account in 관리 → 계정 (「전체 PV·직접 경로 권한」 column,
  `PUT /api/admin/accounts/{u}/root`, audited as `account/root_grant` and
  `root_revoke`). The effective list is env ∪ granted accounts
  (`src/dms/privileged.py`); granted accounts get the same protection as
  listed names (only eligible admins can change/disable/delete them, no
  self password reset). Demoting the account to user drops the grant;
  re-creating a deleted account does not inherit it. Names in the env list
  are shown as 「배포 설정」 and can only be changed via the ConfigMap.
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
- **`dms-api`/`dms-controller` 의 아티팩트 hostPath**(2026-11-03 ③ — 옛 「`/cephfs` scheduling assumption」): 두 Deployment 는
  `<SHARED_DIR>/dms/artifacts` 하나만 `type: Directory` 로 붙인다 — 공용 디렉터리가 마운트되지 않았거나 S0 전인 노드에서는 파드가
  뜨지 않는다. 의도한 fail-closed 다: 공용 스토리지 계약 G1 이 「모든 마스터·워커의 같은 경로」라 정상 노드에는 늘 있고, 확인은
  `deploy/shared-storage-probe.py`(§2d-3). `DirectoryOrCreate` 로 완화하지 마라 — 마운트 없는 노드에 빈 로컬 디렉터리를 만들어 조용히
  로컬 디스크에 쓴다.
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
