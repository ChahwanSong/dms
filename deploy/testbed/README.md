# k8s 테스트베드(`dmsk8s`) — DMS 쪽 운영 (dms-k8s 라인)

> **이 문서는 테스트베드 전용이다.** 여기 나오는 값(`dmsk8s`·`10.112.0.50:5000`·`/dms_shared`·`k8s-w1`·`mason` …)은 프로덕션 값이 아니다.
> 프로덕션 설치·운영은 [`deploy/overlays/prod/README.md`](../overlays/prod/README.md)(한 화면 체크리스트)와 [`deploy/README.md`](../README.md)
> (런북 — 모든 절차의 상세 원천)를 따른다. 이 문서는 같은 절차를 이 테스트베드에서 돌릴 때의 **차이·값·되돌림·한계**만 적고, 절차 자체는
> 다시 쓰지 않는다(deploy/README 의 절 번호로 가리킨다). 현재 동작의 진실은 코드와 [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) 다.

- 이 브랜치(dms-k8s)의 테스트베드는 **dmsk8s 하나**다. ssc 테스트베드(컨텍스트 `dms`·LDAP·오버레이 `deploy/overlays/testbed`)는 dms-ssc
  체크아웃의 몫이고 이 브랜치에는 그 오버레이도 LDAP 코드도 없다(2026-10-26 삭제).
- **인프라는 testbed IaC 저장소 `~/dms-dev/testbed`(= `/home/mason/dms-dev/testbed`)가 진실이다** — VM·네트워크·NFS·CephFS·호스트 edge·CA·
  PV 픽스처·재부팅을 여기서 다시 쓰지 않는다. 아래 표의 위치를 본다(경로는 그 저장소 기준).

| 주제 | testbed 저장소 위치 |
|---|---|
| VM·IP·MAC·vCPU/RAM·OS(k8s-cp1 Ubuntu 24.04, k8s-w1·w2 Rocky Linux 9.6)·CIDR·Cilium | `README.md` 「Topology」·「The two clusters」 · `docs/ARCHITECTURE.md` §3·§4·§23 |
| NFS export(`/data/dms_shared`·`/data/k8s_pv`·빌드 소스 ro) | `docs/ARCHITECTURE.md` §5·§19.4·§22 · `README.md` 「Shared vs per-cluster」 |
| CephFS `dmsfs:/k8s` 하위 트리·cephx·부팅 가드 | `docs/ARCHITECTURE.md` §13·§13.1·§13.2 |
| pkg-01 PostgreSQL `dmsdb_k8s`(지금은 되돌림용 — §7) | `docs/ARCHITECTURE.md` §14 |
| 입구 Traefik(`roles/traefik`, `make traefik-dmsk8s`)·호스트 edge(haproxy)·`portal_edge` 스위치·IP 직접 접속(`traefik_portal_ip_access`, IngressRoute `dms/dms-portal-ip`)·MetalLB·ingress-nginx(되돌림 입구)·호스트 VIP·릴레이·TLS CA | `docs/ARCHITECTURE.md` §15.2~§15.5·§19.5·§19.7·§19.8 · `README.md` 「Portal edge」 |
| 빌드 소스 라우팅(`/home/mason/dms-dev/dms-k8s`, cron ff, worktree 금지) | `docs/ARCHITECTURE.md` §16 · `README.md` 「Build-source routing」 |
| PV 스토리지·csi-driver-nfs·StorageClass `tb-*`·RBAC/PVC 픽스처·DMS 리더 SA | `docs/ARCHITECTURE.md` §22 · `README.md` 「PV storage」 |
| 처음부터 재구축·호스트 재부팅·validate | `docs/ARCHITECTURE.md` §17·§18·§21 · `README.md` 「Rebuild from scratch」·「After a host reboot」·「Validation」 |
| 사내 AD SSO 시험 IdP(호스트 docker — Samba AD DC `dms-idp-dc`·Keycloak `dms-idp-kc`·PostgreSQL `dms-idp-db`, `make idp-*`) | `docs/ARCHITECTURE.md` §24 · `README.md` 「테스트베드 AD SSO」 |
| 비밀(SSH 키·CA·kubeconfig·DB 비밀번호·리더 토큰·IdP 비밀 `files/idp/`·IdP 인증서 키 `files/tls/idp/`)의 위치 | `README.md` 「Secrets — where they live」 |

testbed 저장소의 「Then the DMS application」 목록과 `docs/ARCHITECTURE.md` §17 5단계는 옛 deploy/README 절 번호(§13-x 등)를 가리킨다 —
지금 어디에 있는지는 §11.

목차: 1 범위·안전 규칙 · 2 값 — 자리표시자 대응표 · 3 오버레이 testbed-k8s 계약 · 4 설치·재구축 순서 · 5 시드·픽스처 값 ·
6 일상 운영 · 7 되돌림 상태·보존물 · 8 시나리오 · 9 검증 도구 · 10 테스트베드의 한계(프로덕션과 다른 점) · 11 옛 deploy/README 절 대응.
절 번호(§1~§11)는 다른 파일(deploy/README 부록 Z·BACKLOG·오버레이 주석)이 가리키는 앵커다 — 바꾸지 않는다.

## 1. 범위·안전 규칙

1. **kubectl·도우미는 늘 `--context dmsk8s`.** ssc 클러스터와 네임스페이스 이름(`dms`)이 같아 kubeconfig 컨텍스트가 유일한 구분이다.
   개발 호스트의 current-context 는 `dms`(ssc)로 둔다 — **`dmsk8s` 를 current 로 만들지 않는다**(`--context` 를 빠뜨린 명령이 아래 고정 장치에
   걸려 거부되게).
2. 고정 장치(코드로 확인):
   - 오버레이의 `deploy/overlays/testbed-k8s/kube-context` = `dmsk8s`. `guard-images.sh` 는 다른 컨텍스트를 겨누면 exit 4 로 거부한다 —
     `--context`·`KUBE_CONTEXT` 가 둘 다 없으면 current-context(`dms`)를 읽으므로 역시 exit 4(`guard-images.sh` 의 kube-context 대조).
   - `install.sh` 는 `--context` 가 필수이고 렌더의 `kube-context` 와 다르면 exit 2(`install.sh` 머리 주석 `--context`).
   - `preflight-cluster.sh`·`verify.sh`·`shared-storage-probe.py`·`pg-secrets.py` 에도 `--context dmsk8s` 를 준다.
3. **태그 bump·오버레이 변경은 dms-k8s 브랜치에서만** 커밋한다(워크트리 공유 중에는 `git commit -- <경로>` pathspec — CLAUDE.md).
   ssc 오버레이 `deploy/overlays/testbed` 는 이 브랜치에서 지웠다 — **되살리지 않는다**.
4. **LDAP 없음.** k8s 신원 모드 전용이다(`docs/ARCHITECTURE.md` §3 불변식 12). 노드에도 LDAP 클라이언트(sssd·nslcd)가 없다 — testbed
   validate 가 부정 검사로 본다. ssc 의 시크릿·설정을 이 클러스터로 가져오지 않는다 —
   `DMS_IDENTITY_MODE` 가 k8s 가 아니거나 `DMS_LDAP_URI` 가 있으면 api·controller 가 기동을 거부한다(deploy/README §10-2).
5. 사용자가 관리하는 라이브 값은 실증 전에 목록을 확인하고, 실증으로 바꾼 것만 되돌린다 — 사용자 sync 허용 쌍, 스토리지 기본 소유
   (전부 0:0), 메일 발송 방식(평소 stub — §9).
6. 빌드 소스 `/home/mason/dms-dev/dms-k8s` 는 origin/dms-k8s 전체 clone 이다. 그 안에 git worktree 를 만들거나 작업 세션을 열지 않는다 —
   export 에 섞이고, 로컬 커밋·브랜치 전환이 있으면 cron 의 ff 가 멈춰 빌드가 조용히 낡는다(testbed `README.md` 「Build-source routing」).

## 2. 값 — 자리표시자 대응표

deploy/README 머리의 자리표시자가 이 테스트베드에서 무엇인지. **원천 파일이 진실**이다 — 태그처럼 자주 바뀌는 값은 여기 옮겨 적지 않는다.
(파일 이름만 쓴 것은 `deploy/overlays/testbed-k8s/` 아래다.)

| 자리표시자 | 테스트베드 값 | 원천 |
|---|---|---|
| `<ctx>` / `--site` | `dmsk8s` / `testbed-k8s` | `kube-context` |
| `<REGISTRY>` | `10.112.0.50:5000` — in-cluster(`REGISTRY_CLUSTER_IP=10.112.0.50`·`REGISTRY_PORT=5000`, svc CIDR 10.112.0.0/12 하단), 평문 HTTP. 저장소 `/dms_shared/dms/registry` | `values.env` |
| `<SHARED_DIR>` (+ `SHARED_DIR_ID`) | `/dms_shared` — 호스트 NFS `10.10.10.1:/data/dms_shared` 를 3노드가 fstab 으로(`lookupcache=positive`). id 는 `values.env` 의 `SHARED_DIR_ID` | `values.env` + `patch-config.yaml` 의 `DMS_ARTIFACT_BASE_URI`(둘이 같아야 한다 — §3) |
| `<FQDN>` | `dms-k8s.local`(호스트 `/etc/hosts` → VIP-A). DMS 쪽은 `values.env` 의 `PORTAL_DOMAIN` — Ingress `dms/dms-portal-traefik` 의 host(2026-11-16 부터 고정 오버레이도 필수) | testbed `group_vars/dmsk8s.yml` `portal_dns` · `values.env` |
| `<VIP-A>` | `10.10.10.1:443` — 호스트 dmsbr0 주소 = 호스트 edge(배포판 haproxy, Avi VS-443 흉내: L4·`send-proxy-v2`·TCP 헬스체크·3600초, 풀 = **노드 셋** :443 의 Traefik). 외부 입구 `https://dms-k8s.local:8443`(LAN·Tailscale 주소의 :8443 — 같은 edge). `values.env` 에 `PORTAL_VIP` 는 없다(고정 오버레이는 선택 — §4-10) | testbed `portal_edge_vip`, `README.md` 「Portal edge」 |
| IP 직접 접속(테스트베드 전용, 2026-11-17 — DMS 는 설치·변경하지 않는다) | testbed `traefik_portal_ip_access: true` → `roles/traefik` 의 IngressRoute `dms/dms-portal-ip`(ingressClassName `traefik`, websecure 만, Host 가 VIP-A·`host_ext_ips`(= `tbinfo.py portal-ip-hosts dmsk8s`) 가운데 하나와 정확히 같을 때 → `dms-api:http`, `tls: {}`) + 같은 주소의 IP SAN(아래 `<PORTAL_TLS_SECRET>`). hosts 를 둘 수 없는 기기(Tailscale 휴대폰)는 테스트베드 CA(`files/tls/ca.crt` 만 — `ca.key` 는 옮기지 않는다)를 신뢰한 뒤 `https://<Tailscale 주소>:8443/` 로 연다. LAN 은 `https://<LAN 주소>:8443/`, 호스트는 `https://10.10.10.1/` 이다. `http://` 와 그 밖의 이름·주소는 404 다. ns `dms` 가 없으면 역할이 적용을 건너뛰므로 DMS 설치 때 `make traefik-dmsk8s` 를 한 번 더 돈다(§4-5). 끄기: `traefik_portal_ip_access: false` + `make traefik-dmsk8s`(라벨로 지운다 — 늘어난 SAN 은 다음 재발급까지 남지만 무해) | testbed `README.md` 「Portal edge」 Bare-IP access · `docs/ARCHITECTURE.md` §15.5 「IP 접속」·§19.8 |
| 입구 Traefik(DMS 는 설치·변경하지 않는다) | `kube-system/traefik` DaemonSet — rke2-traefik 모양(`docker.io/rancher/hardened-traefik:v3.7.4-build20260608`, 프로덕션과 같은 판). 노드 셋 hostPort 80 → web :8000·443 → websecure :8443(TLS), IngressClass `traefik`(기본 아님), websecure `proxyProtocol.trustedIPs` = `10.10.10.1/32`(호스트 edge = Avi SE 자리), forwardedHeaders 비움. cp1 의 taint 는 toleration 으로 넘는다(프로덕션 RKE2 마스터는 taint 가 없다) | testbed `roles/traefik`·`group_vars/dmsk8s.yml` 의 `traefik_*` |
| (되돌림 입구) | MetalLB VIP `10.20.20.101` → ingress-nginx(testbed `portal_edge: ingress` — §7) | testbed `public_vip` · `patch-ingress.yaml`·`patch-metallb.yaml` |
| `<마스터1..3>` | **`k8s-cp1` 10.10.10.20 한 대**(§10) | testbed 인벤토리 |
| NoSchedule 없는 워커 | `k8s-w1` 10.10.10.21 · `k8s-w2` 10.10.10.22 | 〃 |
| `<빌드 노드>`(= 인터넷 노드) | `k8s-w1` | testbed `dms_build_node` |
| `build_source_path` | `/home/mason/dms-dev/dms-k8s` — origin/dms-k8s 전체 clone(호스트 cron 이 ff), 워커에는 같은 경로로 읽기 전용 NFS | testbed `dms_build_source_path` |
| `<LOCAL_ADMIN>` | `mason`(특권 목록 `root,admin,mason`) | `patch-config.yaml` 의 `DMS_PRIVILEGED_REQUESTERS` |
| `<EMAIL_DOMAIN>` | `example.com`(테스트베드 RoleBinding subject 의 도메인) | `patch-config.yaml` 의 `DMS_ACCOUNT_EMAIL_DOMAIN` |
| 메일 발송 방식 | `stub`(코드 기본 — `config.py` `mailer_backend`; 사내 릴레이에 닿지 않는다). 「k8s 연동」 「개요」·「클러스터」 탭의 stub 경고가 **정상**이다 | 코드 기본 |
| `<DMS_TAG>` | `dN` 계열(2026-10-26 d1 부터). **현재 값은 `kustomization.yaml` 의 `newTag`(dms·dms-agent 두 항목)와 `patch-config.yaml` 의 `DMS_JOB_IMAGE`** 를 본다. 그 전의 `k0`~`k4` 이력은 `kustomization.yaml` 머리 주석·CHANGELOG 「테스트베드 분리」 | 오버레이 |
| `<사내CA>` | 테스트베드 CA `~/dms-dev/testbed/files/tls/ca.crt`(gitignore, 영구 사본 `/data/dms-testbed/backup-20261021/tls/`) | testbed `README.md` 「Secrets」·`docs/ARCHITECTURE.md` §15.4 |
| `<PORTAL_TLS_SECRET>` | `dms-portal-tls` — testbed `make portal-tls-dmsk8s` 가 같은 CA 로 만든다. SAN = `dms-k8s.local`·`10.20.20.101`(`public_vip`)·`PORTAL_TLS_EXTRA_IPS`(testbed Makefile 기본 = 호스트의 Tailscale 주소)·`tbinfo.py portal-ip-hosts dmsk8s`(`traefik_portal_ip_access` 클러스터만 — VIP-A 10.10.10.1 + `host_ext_ips`). 2026-11-17 재발급 뒤 라이브 SAN 은 DNS 하나·IP 넷(10.20.20.101·10.10.10.1·LAN·Tailscale). 요청한 SAN 이 하나라도 빠지면 `make portal-tls-dmsk8s` 가 `FORCE` 없이 스스로 재발급한다(`CHECK=1` 미리보기). IP 로 붙는 연결은 SNI 가 없어서 Traefik 이 접속한 주소(edge 경유면 PROXY v2 목적지)가 IP SAN 인 인증서를 고른다 — 그래서 IP 접속 주소는 모두 SAN 이어야 한다 | `values.env` 의 `PORTAL_TLS_SECRET` · testbed `docs/ARCHITECTURE.md` §15.4 |
| DB | CNPG `dms-pg`(DB·사용자 `dms`, 인스턴스 3개 = cp1·w1·w2) — 2026-11-03 T7 부터. 옛 pkg-01 `dmsdb_k8s` 는 되돌림용(§7) | `values.env` 의 `PG_*` |
| 포탈 부제 | 「K8S 테스트베드」(`PUT /api/admin/portal-settings` — 두 포탈을 구분한다, §6-5) | DB |

**옛 판의 틀린 값**(§11 의 옛 절들): 빌드 소스 표 dmsk8s 행·인프라 전제·첫 관리자 절이 적은 빌드 소스 `/home/mason/dms-dev/dms` 는
틀렸다 — 그 체크아웃(main)은 2026-10-22 부터 어느 클러스터의 빌드 소스도 아니다(testbed `group_vars/all.yml` `dms_src_retired_paths`,
validate 가 확인). 인프라 전제 절의 `PORTAL_VIP=10.20.20.101 sh deploy/preflight-cluster.sh` 도 틀렸다 — preflight 는 `PORTAL_VIP` 를 읽지
않는다(읽는 것은 `verify.sh`).

## 3. 오버레이 `testbed-k8s` 계약

| 파일 | 담는 것 |
|---|---|
| `kustomization.yaml` | base(`deploy/k8s`) + `images`(dms·dms-agent 의 `newName`/`newTag`) + `patches`(아래 셋) + `replacements`(아티팩트 경로 — prod 와 같은 블록, 두 곳을 같이 고친다) |
| `patch-config.yaml` | ConfigMap 사이트 값 — `DMS_JOB_IMAGE`·`DMS_BUILD_REGISTRY`·`DMS_BUILD_BUILDER_IMAGE`·`DMS_PRIVILEGED_REQUESTERS`·`DMS_ALLOW_PRIVILEGED_REQUESTERS`·`DMS_USER_ALLOWED_OPERATIONS`(`sync,rm` — scan 은 특권 운영자만)·`DMS_ACCOUNT_EMAIL_DOMAIN`·`DMS_ARTIFACT_BASE_URI` |
| `values.env` | 플랫폼·애드온 층(레지스트리·DB·입구)의 **유일한 원천** + 앱 값(`SHARED_DIR`·`REGISTRY`)의 기록. 입구 층(`platform/ingress`)의 값은 `PORTAL_DOMAIN`·`PORTAL_TLS_SECRET` 뿐이다(`INGRESS_CLASS`·`TRAEFIK_ENTRYPOINT` 는 기본 `traefik`·`websecure`) |
| `kube-context` | `dmsk8s`(§1) |
| `patch-ingress.yaml`·`patch-metallb.yaml` | 되돌림 입구(Ingress `dms-portal`(class nginx) 호스트·MetalLB VIP `10.20.20.101`) — 소크 뒤 삭제(§7). 입구 층의 Ingress `dms-portal-traefik` 과 이름이 달라 둘이 함께 있다 |

- **고정 오버레이**: 값을 커밋한다(비밀이 아니다). prod 는 템플릿 오버레이(`REPLACE_*` 토큰)라 사이트의 values.env(커밋하지 않는다)로
  렌더한다. `sh deploy/render.sh testbed-k8s` 는 앱 오버레이를 그대로 복사하고 플랫폼·애드온 층만 values.env 로 낸다
  → `deploy/.rendered/testbed-k8s`(`render.sh` 「고정 오버레이」). 설치기도 같은 렌더를 쓴다.
- **두 원천의 대조 — 한 커밋에서 같이 고친다.** 앱 값은 오버레이 파일이 원천이고 values.env 는 그 기록이라, `render.sh` 가 다음이 어긋나면
  FAIL(rc 2)한다:
  - `patch-config.yaml` 의 `DMS_ARTIFACT_BASE_URI` = `file://<values.env 의 SHARED_DIR>/dms/artifacts`.
  - 렌더 결과의 레지스트리가 values.env `REGISTRY` 와 같다: `kustomization.yaml` 의 `newName`(dms·dms-agent) · `DMS_JOB_IMAGE` ·
    `DMS_BUILD_REGISTRY` · `DMS_BUILD_BUILDER_IMAGE`(in-cluster 라 빌더도 같은 레지스트리여야 한다). 설계 7-4 의 「같은 주소가 여섯 곳」
    (`render.sh` 머리 주석) 가운데 여섯째인 DB 잡 이미지(`control_state.job_image`)는 렌더 밖이라 릴리스로 맞춘다.
  - 테스트도 같은 것을 본다: `tests/test_shared_dir_contract.py`(기대값 `/dms_shared`), `tests/test_platform_registry_contract.py`,
    `tests/test_testbed_k8s_overlay.py`(상수 `K8S_REGISTRY`·`SHARED_BUILDER`·`K8S_VIP`·`K8S_HOST`, 렌더에 `ldap` 0건·에이전트 `dms agent` 직접 기동).
- **`SHARED_DIR_ID`** = `/dms_shared/dms/.dms-shared-id`(S0 가 쓴 센티널, `registry/.dms-registry-id` 와 같은 값). 레지스트리 init 이 대조한다 —
  공용 디렉터리를 새로 만들어 id 가 바뀌면 values.env 를 고쳐 커밋한다. 어긋나면 레지스트리가 「센티널 불일치」로 뜨지 않고 install.sh S4 가
  rollout 시간 초과로 멈춘다(deploy/README §2e-5 「저장소 이동」).
- **축소 requests**(설계 v6 10 — cp1 여유가 작다): 레지스트리 50m/64Mi, PG 50m/256Mi·`shared_buffers` 64MB. limits 는
  base 그대로. 옛 Envoy 50m/128Mi(`GATEWAY_ENVOY_*`)는 2026-11-16 입구와 함께 없어졌다 — 입구 Traefik 은 testbed IaC 가 깔고 requests 가
  없다(차트 기본 `resources: {}`). PG CPU 는 T7 실측 뒤 100m → 50m 로 낮췄다(⑤ 뒤 워커마다 +200m 가 붙어 2노드 dscan gang 이 5m 모자라 Volcano 에서 대기했다 —
  `values.env` 주석).
- **DB 배치**: `PG_PLACEMENT=all-nodes`(control-plane 이 cp1 하나라 인스턴스를 cp1·w1·w2 에 하나씩 — prod 는 늘 마스터마다 하나),
  `PG_BACKUP_KEEP=24`(호스트 `/data` 여유가 작다 — prod 기본 336).
- **api·controller 는 워커에 뜬다**: prod 의 `patch-placement.yaml`(마스터 배치)이 이 오버레이에는 없다 — cp1(4GiB)에 메모리가 모자라다.
  그래서 메일(dms-api → 릴레이)의 출발 노드와 장애 시 복귀 수치가 prod 와 다르다(§10). k8s 연동 호출(대상 apiserver)은 api·controller 배치와
  무관하다 — 포탈 「호출 노드와 주기」에서 지정한 노드의 dms-agent 가 직접 부른다(`agent/k8s_fetch.py`, 테스트베드 = 주 k8s-w1·보조 k8s-w2 — §5-3).
- **Deployment 는 패치하지 않는다** — securityContext 는 base 가 유일한 진실이다
  (`tests/test_release_manifest_contract.py::test_overlays_do_not_patch_control_plane_deployments`). 예외는 `replacements` 의 아티팩트 경로 두 필드.
- **잡 이미지**: `DMS_JOB_IMAGE` 는 「DB 오버라이드가 없을 때의 기본」이자 드리프트 배지의 기준이다. 라이브 잡 이미지는 포탈 릴리스의
  `job-image`(DB `control_state.job_image`)가 이긴다.
- **공용 디렉터리 모드**: 지금 공용 디렉터리 `/dms_shared/dms` 는 S0 가 만든 **0750** 이다. CephFS 의 `/cephfs/dms`(0770 — `dms-shared-fs.yml`)는
  옛 아티팩트 base 의 부모(§7)일 뿐이다. 770 은 S0 가 FAIL 로 알리는 모양이라(`shared-dir-init.sh` 의 `TREE` — `dms` 는 750·700 만) 새
  SHARED_DIR 에 쓰지 않는다(규칙: deploy/README §2b-3).

## 4. 설치·재구축 순서

**전제**: testbed 저장소에서 dmsk8s 인프라가 섰다 — `README.md` 「Rebuild from scratch」 0~2b(또는 `make all-dmsk8s`) 뒤 `make validate-dmsk8s`
가 FAIL 0. 이 시점의 「portal TLS not issued yet」 INFO·「namespace dms absent」 INFO(edge 백엔드 DOWN 도 INFO, DMS Ingress·readyz 검사는
건너뜀)는 정상이다(DMS 네임스페이스·Ingress 가 아직 없다 — Traefik 자체는 `all-dmsk8s` 의 `make traefik-dmsk8s` 가 이미 깔았다).
dmsk8s 는 `idp_enabled: true` 라 validate-dmsk8s 의 「testbed AD SSO」 절은 IdP 컨테이너 셋이 떠 있어야 FAIL 0 이다 — `make all-dmsk8s` 에는
idp 타겟이 없으므로 새 호스트면 `make idp-up idp-config` 를 먼저 한다(이 시점의 「no deploy/dms-api」·「Secret dms/dms-oidc not created yet」 는
info 라 정상).
**설치 호스트** = 개발 호스트(luminous — kubeconfig 에 `dmsk8s` 컨텍스트), 이 저장소의 dms-k8s 체크아웃. 프로덕션 판 전체 순서는 deploy/README §0-5.

| # | 어디서 | 무엇 | 성공 판정 | 실패하면 |
|---|---|---|---|---|
| 1 | 노드(root) | `/dms_shared` S0 | `RESULT: PASS`, 3노드 같은 `.dms-shared-id` | deploy/README §2d-2 |
| 2 | k8s-w1 → k8s-cp1 | (cp1 을 새로 만들었을 때만) 레지스트리 자기 이미지 반입 S0-5 | cp1 `crictl images` 에 `10.112.0.50:5000/registry` | deploy/README §2e-4 2) |
| 3 | 설치 호스트 | 데이터 root·e2e 픽스처 플레이북 | PLAY RECAP `failed=0` | testbed `make cephfs-dmsk8s` |
| 4 | 설치 호스트 | `install.sh --plan` | `RESULT: WARN`(S1 WARN 1건 — 아래) | deploy/README §2h·§14-4 |
| 5 | 설치 호스트 | 시크릿(S2)·포탈 TLS·IP 접속 라우트 | S2 `[OK] dms-secrets: 4키…`, `dms-portal-tls` 있음, IngressRoute `dms-portal-ip` 있음 | deploy/README §2f-2·§3b · §4-5 |
| 6 | 설치 호스트 | `install.sh` 본 실행 | rc 0 `RESULT: PASS/WARN`, 또는 rc 10 PAUSE | 출력의 사유 줄 · deploy/README §2h |
| 7 | k8s-w1 → 설치 호스트 | (PAUSE 때만) push·부트스트랩 빌드·태그 bump → `--stage S5` | S5 PASS 뒤 끝까지 | deploy/README §2e-4·§1 |
| 8 | 호스트·설치 호스트 | 입구 확인 | `host-edge.sh status` 의 백엔드 셋 UP, `ingress-probe.sh` 노드 셋 PASS | testbed `README.md` 「After a host reboot」 표 · §4-8 |
| 9 | 포탈 | 첫 관리자·컨트롤 상태·시드·k8s 연동 | 로그인·Ready·동기화 성공 | deploy/README §6 |
| 9b | 호스트·설치 호스트 | (선택) SSO 복원 — testbed `make idp-up idp-config idp-check idp-dms-secret` → `rollout restart deploy/dms-api` | `sso-info` 가 `"enabled":true` | §6-7 |
| 10 | 설치 호스트·호스트 | 최종 검증 | validate FAIL 0, S12 PASS | deploy/README §14 |

9b 는 클러스터(ns `dms`)만 새로 세웠을 때 빠뜨리기 쉽다: IdP 는 호스트 docker 라 VM·클러스터를 다시 세워도 남지만 Secret `dms/dms-oidc` 는
클러스터와 함께 없어진다(그때는 `make idp-check idp-dms-secret` + dms-api 재시작이면 된다). Secret 이 없을 때 idp check 는 info 만 내므로
validate 가 잡지 못하고, 포탈의 SSO 버튼만 조용히 사라진다 — 확인은 `curl --cacert <CA> https://dms-k8s.local/api/auth/sso-info`(§6-7).

### 4-1. `/dms_shared` S0

스크립트를 노드로 복사해(예: `/tmp`, 노드 SSH 는 testbed 저장소의 키 `files/id_ed25519`) root 로 돌린다:

```bash
sudo sh /tmp/shared-dir-init.sh /dms_shared           # 공용 디렉터리가 새것일 때 한 노드(k8s-w1)에서만 — 출력의 「dms/.dms-shared-id = <id>」를 적는다
sudo sh /tmp/shared-dir-init.sh --check /dms_shared   # 나머지 노드(재사용이면 3노드 모두 이것만) — RESULT: PASS, 같은 id
```

- 새 id 면 `values.env` 의 `SHARED_DIR_ID` 를 고쳐 dms-k8s 에 커밋한다(§3).
- **k8s-w1·w2 에는 `/cephfs`(CephFS)도 마운트돼 있다** — 인자를 `/cephfs` 로 주면 CephFS 에 트리를 만든다. 늘 `/dms_shared`.
- `/dms_shared` 마운트 자체(NFS export·fstab)는 testbed `make storage`(`docs/ARCHITECTURE.md` §19.4) 몫이다.

### 4-2. k8s-cp1 의 레지스트리 자기 이미지(S0-5)

레지스트리 이미지는 자기에게서 받을 수 없어 cp1 의 containers-storage 에 손으로 반입·고정한 것이다. cp1 을 새로 만들었으면(처음부터
재구축이면 늘) **install.sh S4 전에** deploy/README §2e-4 2) 를 한다(인터넷 노드 = k8s-w1 에서 lock digest 로 oci-archive → cp1 반입).
cp1 은 Ubuntu 24.04 라 skopeo 패키지의 부수 파일이 다르다 — §10 「skopeo 메모」. 고정 drop-in(`99-dms-pinned.conf`)은 testbed crio 롤이 이미 둔다. 빠뜨리면 레지스트리 파드가
`ImagePullBackOff` 이고 다른 무엇도 이미지를 받지 못한다. cp1 이 그대로면 건너뛴다(데이터 `/dms_shared/dms/registry` 는 그대로 쓴다).

### 4-3. 데이터 root·e2e 픽스처

`deploy/testbed/dms-shared-fs.yml`. **`-e dms_cluster=dmsk8s` 필수** — 플레이북 기본값이 `dms`(ssc)라
빠뜨리면 ssc 클러스터의 워커를 겨눈다(기본값 정리는 BACKLOG):

```bash
ANSIBLE_CONFIG=~/dms-dev/testbed/ansible.cfg ansible-playbook \
  -e ansible_ssh_private_key_file=$HOME/dms-dev/testbed/files/id_ed25519 \
  -e dms_cluster=dmsk8s deploy/testbed/dms-shared-fs.yml --list-hosts      # 대상만 확인(접속 안 함) — dmsk8s_workers[0] 등
# 확인했으면 같은 명령에서 --list-hosts 를 빼고 실행
```

만드는 것: `/cephfs/managed`(데이터 root)·`/cephfs/managed/e2e`(root 소유 픽스처 — §5-4), `/cephfs-third/managed`·`/cephfs-secondary/managed`.
같이 만드는 `/cephfs/dms`·`/cephfs/dms/artifacts` 는 dmsk8s 에서 **옛 base**다(§7) — 지금 base 는 S0 의 `/dms_shared/dms/artifacts`.
「Base CephFS must be mounted here」 태스크가 실패하면 CephFS 마운트(testbed `make cephfs-dmsk8s`)부터.

### 4-4. 계획(변경 없음)

```bash
sh deploy/install.sh --context dmsk8s --site testbed-k8s --plan
```

`RESULT: WARN`(rc 0)이 정상이다 — S1 의 `[WARN] control-plane 노드 1대 — prod 는 3대 … 테스트베드 1대는 정상`(`preflight-cluster.sh`) 하나뿐이어야
한다. 감지 표에서 volcano 는 **C**(testbed IaC 가 설치한 v1.15.0 — 설치기가 건드리지 않는다), traefik 도 **C**(testbed IaC 가 설치한
rke2-traefik 모양 — DMS 는 Traefik 을 설치·변경하지 않고 Ingress 하나만 만든다; 2026-11-16 실측 Ready 3, notes 에 `proxyProtocol.trustedIPs=10.10.10.1/32`·
readTimeout 기본 60s), 이미 설치된 클러스터면 DMS 층들(입구 층 `ingress` 포함)은 `D=`(2026-11-03 T9 실측 — CHANGELOG 「⑦ 설치기」). S1 의
preflight 「2. 입구」 는 IngressClass `traefik` PASS 와 「Traefik 파드 Ready 3/3」 INFO 다. 다른 WARN·FAIL 은 deploy/README §2h 의 사유 표.

### 4-5. 시크릿·포탈 TLS

시크릿은 손으로 만들지 않는다: S2 가 `dms-pg-app`(`pg-secrets.py ensure-app`)과, 없을 때만 `dms-secrets`
(`pg-secrets.py init-secrets` — 4키, CNPG `dms-pg-rw` URL)를 만든다(`install.sh` `stage_S2`). 옛 판의 손 시크릿(pkg-01 URL)은 T7 이전 것이다.
포탈 인증서는 S8 이 쓰고 없으면 S8 이 멈추므로, 네임스페이스가 생긴 뒤 S8 전에 만든다:

```bash
sh deploy/install.sh --context dmsk8s --site testbed-k8s --only S2   # ns dms + 시크릿 — [OK] dms-secrets: 4키·자리표시자 없음
cd ~/dms-dev/testbed && make portal-tls-dmsk8s                       # dms-portal-tls(같은 테스트베드 CA — INIT_CA=1 은 새 CA 라 쓰지 않는다)
kubectl --context dmsk8s -n dms get secret dms-portal-tls -o jsonpath='{.type}{"\n"}'   # kubernetes.io/tls
cd ~/dms-dev/testbed && make traefik-dmsk8s   # 테스트베드 전용 IP 접속 IngressRoute dms/dms-portal-ip — IaC 단계에서는 ns dms 가 없어 건너뛰었다
                                              #   (결과 줄 「NOT applied -- run make traefik-dmsk8s again after the DMS install」). DaemonSet 층은 unchanged, 파드 교체 없음
kubectl --context dmsk8s -n dms get ingressroutes.traefik.io dms-portal-ip
```

이 `make traefik-dmsk8s` 가 없으면 10단계 `make validate-dmsk8s` 가 `portal IP access: IngressRoute(s) '' (want dms/dms-portal-ip; make
traefik-dmsk8s)` 로 FAIL 이다. 라우트는 ns 만 있으면 걸리지만, bare IP readyz 200 은 S8(Ingress — Traefik 이 그 tls 로 인증서를 싣는다)과
S11(dms-api) 뒤에야 나온다(§2 「IP 직접 접속」).

(`--only S2` 를 먼저 도는 이 순서는 이 문서가 권하는 조합이다 — 각 단계는 `install.sh` `stage_S2`·`tls_gate` 로 확인했지만 이 조합 그대로
돌려 본 기록은 없다(**미검증**). TLS 없이 본 실행을 했다면 S8 이 「TLS 시크릿 … 가 없다」로 멈추고 재개 명령(`--stage S8`)을 보인다 —
TLS 를 만든 뒤 그 명령으로 이어 간다.)
`dms-secrets` 가 이미 있는데 URL 이 CNPG 가 아니면(예: 옛 pkg-01 URL) S2 의 `pg-secrets.py check` 가 멈춘다 — deploy/README §2f-2.

### 4-6. 본 실행

```bash
sh deploy/install.sh --context dmsk8s --site testbed-k8s
```

- 레지스트리 데이터(`/dms_shared/dms/registry`)를 재사용하면 S4~S13 이 이어서 끝난다(`RESULT: WARN` — S1 WARN 1건).
- 공용 디렉터리가 새것(빈 레지스트리)이면 S4 뒤 **PAUSE(rc 10)** 하며 push 명령과 재개 명령을 보인다 → 4-7.

### 4-7. (PAUSE 때만) 인터넷 노드 k8s-w1 에서 push·부트스트랩 빌드

```bash
sudo dnf install -y podman                                           # 부트스트랩 빌드에만 필요 — testbed IaC 는 skopeo 만 깐다
sh deploy/push-images.sh --registry 10.112.0.50:5000 --run           # lock 4줄(registry·buildah·CNPG·PG — EG·envoy 줄은 2026-11-16 삭제) — RESULT: PASS
REGISTRY=10.112.0.50:5000 TAG=<새 dN> ./deploy/docker/build-and-push.sh   # DMS 3종(dms-mpifileutils·dms·dms-agent), 세 태그 같게
```

- 어느 체크아웃에서: k8s-w1 에는 빌드 소스 `/home/mason/dms-dev/dms-k8s`(origin/dms-k8s 의 **읽기 전용** NFS)가 같은 경로에 있다. 읽기 전용
  마운트에서 `build-and-push.sh` 를 돌린 기록은 없다(**미검증** — podman 빌드는 컨텍스트를 읽기만 한다). 막히면 쓰기 가능한 사본(rsync)에서.
- pkg-01 은 podman 이 있어도 ClusterIP `10.112.0.50` 에 닿지 않아 대신할 수 없다.
- 빌드 전 게이트(deploy/README §8 「이미지 빌드 전 게이트」)는 개발 PC 에서 같은 커밋으로 돈다.
- **새 빌드는 늘 새 `dN`** — 옛 태그 이름으로 다시 push 하면 태그가 거짓말을 한다.
- 그 `dN` 으로 오버레이를 bump 해 dms-k8s 에 커밋한다(`kustomization.yaml` 의 `newTag` 둘 + `patch-config.yaml` 의 `DMS_JOB_IMAGE` — §6-1).
- 설치 호스트에서 PAUSE 출력의 재개 명령 그대로: `sh deploy/install.sh --context dmsk8s --site testbed-k8s --stage S5`(lock 대조 + DMS 3종 태그 확인).

### 4-8. 입구 확인

사용자 경로는 호스트 edge(haproxy — Avi VS-443 흉내, `send-proxy-v2`) → **노드 셋(cp1·w1·w2) :443 의 Traefik**(rke2-traefik 모양 — §2)
→ DMS Ingress `dms/dms-portal-traefik`(host `dms-k8s.local`, 진입점 websecure 만) → dms-api 다. 2026-11-16 Envoy Gateway 입구를 폐기하고(병행
없음) 앱 d27 과 함께 이 경로로 옮겼다(전환 순서·측정은 testbed `docs/ARCHITECTURE.md` §19.7, DMS 쪽 기록은 CHANGELOG). Traefik 은 DMS 가 깔지
않는다 — testbed IaC 의 `make traefik-dmsk8s` 가 두고, 설치기 S8 「입구(Traefik)」 은 감지(traefik C — 건드리지 않음) → TLS 게이트 →
`platform/ingress` SSA → `deploy/ingress-probe.sh`(Traefik 파드가 도는 노드마다 `--resolve dms-k8s.local:443:<노드>` readyz·인증서)다. Traefik 이
뜨면 edge 백엔드는 TCP 체크로 UP 이지만 Ingress 가 없으면 `TRAEFIK DEFAULT CERT` + 404 다(DMS 라우트는 Host = 포탈 이름뿐). S8 뒤:

```bash
bash ~/dms-dev/testbed/scripts/host-edge.sh status
#   OK   dmsk8s backend k8s-cp1 10.10.10.20:443 UP (TCP check, PROXY v2)      — k8s-w1 10.10.10.21·k8s-w2 10.10.10.22 도 같은 줄
curl -sS --cacert ~/dms-dev/testbed/files/tls/ca.crt https://dms-k8s.local/readyz
sh deploy/ingress-probe.sh --context dmsk8s --domain dms-k8s.local --cacert ~/dms-dev/testbed/files/tls/ca.crt
#   NODE k8s-cp1 PASS 10.10.10.20:443 readyz 200 · 인증서 OK(dms-k8s.local)   … w1·w2 도 PASS, 마지막 줄 RESULT: PASS
```

- 설치 호스트가 노드 :443 에 PROXY 없이 직접 붙어도 Traefik 은 받는다 — 호스트(10.10.10.1)는 신뢰 출발지라 헤더가 없어도 받고, 다른 출발지의
  PROXY 헤더는 읽고 버린다(원 IP 위조 불가). 그래서 노드 방화벽이 필요 없고, cp1 의 옛 nft :443 펜스와 Envoy port floor
  (`/etc/sysctl.d/90-dms-envoy.conf`)는 은퇴했다(testbed `roles/common` 이 걷고 validate 가 부재를 단언 — `make baseline ANSIBLE='ansible-playbook
  --limit k8s-cp1 --tags cp443'`).
- hostPort 는 CRI-O 의 DNAT(PREROUTING → FORWARD)라 노드 `ss` 에 :443 이 보이지 않는다 — 리스너가 아니라 위 프로브로 본다.
- 노드 :80 은 Traefik web 진입점이 404 로 답한다(DMS 라우터는 websecure 에만). edge 에는 80 이 없어 S12 6절은 「http://dms-k8s.local/ 응답 없음 —
  80 VS 없음」 INFO 다(`verify.sh` 6절 판정: 응답 없음·404 = INFO, 200 = WARN — 평문 노출, router.entrypoints 어노테이션 확인).
- ingress-probe 의 판정: 200 + 인증서 = PASS, 404 = FAIL(Traefik 이 Ingress 를 안 집었다 — ingressClassName·router.entrypoints·ns),
  `TRAEFIK DEFAULT CERT` = FAIL(tls 시크릿·SNI), 연결 실패(000) = UNKNOWN(S8 은 WARN 후 계속 — S12 가 VIP 로 다시 본다). FAIL 노드는 `--timeout`(기본
  30초, verify 안에서는 `INGRESS_PROBE_TIMEOUT` 기본 5초) 동안 다시 잰다. dms-api 가 아직 없으면 S8 은 `--tls-only`(인증서만)로 부른다.
- Envoy 가 입구에서 하던 두 가지는 앱으로 옮겼다(d27): 요청 본문 상한 `DMS_MAX_REQUEST_BODY_BYTES`(기본 20MiB — 넘으면 413 `request_too_large`)와
  uvicorn keep-alive `DMS_API_KEEP_ALIVE_SECONDS`(기본 120초 — Traefik 기본 idleConnTimeout 90초보다 길게). testbed-k8s 도 코드 기본 그대로다
  (`patch-config.yaml`·base `20-config.yaml` 에 없다). 상한은 입구 경유뿐 아니라 클러스터 안 에이전트 요청에도 걸린다(k8s 동기화 결과 POST 만 자기
  상한). 2026-11-16 실측: ≈21MiB POST → 413, ≈19MiB → 통과, 본문 있는 POST 40회를 5.5초 간격으로 → 502 0.

그 전에 앱에 닿아야 하면 되돌림 경로로: `curl --cacert <CA> --resolve dms-k8s.local:443:10.20.20.101 https://dms-k8s.local/readyz`
(MetalLB → ingress-nginx — testbed validate 가 늘 본다).

### 4-9. 설치 뒤 설정

deploy/README §6 그대로, 값만 이 테스트베드의 것:
- 6-1 첫 관리자: S13 이 보이는 명령(`'<LOCAL_ADMIN>'` = `'mason'`). `mason` 은 특권 목록이라 root 자격·전체 PV 권한이 있다.
- 6-2 메일: 테스트베드는 **stub 그대로**다(사내 릴레이에 닿지 않는다). 그래서 이 테스트베드에서는 가입 인증 메일이 신원 증명이 아니다 —
  프로덕션에서는 허용되지 않는 상태다(deploy/README §6-2). 릴레이 경로를 시험할 때만 §9 의 가짜 릴레이.
- 6-3 컨트롤 상태: `build_node_name=k8s-w1`, `build_source_path=/home/mason/dms-dev/dms-k8s`. 빌드 프록시 칸은 비운다(노드는 libvirt NAT 로
  바로 나간다 — testbed `README.md` 「Topology」). PUT 은 전체 치환이라 GET 한 값에서 바꿀 칸만 고친다.
- 포탈 부제 「K8S 테스트베드」(관리 → 포탈 설정, `PUT /api/admin/portal-settings`).
- 6-4 스토리지·정책, 6-5 k8s 연동: §5 의 값.
- 6-9 SSO(선택): 이 문서 §6-7(IdP 는 testbed IaC 가 호스트 docker 로 — §4 표 9b).

### 4-10. 최종 검증

```bash
cd ~/dms-dev/testbed && make validate-dmsk8s        # FAIL 0 — 포탈 TLS(CA 검증)·edge 백엔드·Traefik·DMS Ingress·PV 픽스처·리더 auth can-i·
                                                    #   IP 접속·모르는 이름 404·testbed AD SSO 절 포함(아래)
cd <dms-k8s 체크아웃>
CACERT=$HOME/dms-dev/testbed/files/tls/ca.crt sh deploy/install.sh --context dmsk8s --site testbed-k8s --only S12
```

- `PORTAL_DOMAIN`(`dms-k8s.local`)은 2026-11-16 부터 testbed-k8s 의 `values.env` 에 있어 render.env 로 간다(Ingress host — 고정 오버레이도 필수).
  `PORTAL_VIP` 는 없다(고정 오버레이는 선택) — 없으면 6절 readyz 는 `--resolve` 없이 이름으로 붙고, 호스트 `/etc/hosts` 가 그 이름을 VIP-A
  (10.10.10.1)로 보내므로 같은 edge 를 지난다. 주소를 못 박으려면 `PORTAL_VIP=10.10.10.1` 을 env 로 더한다(`verify.sh` 머리 주석).
- validate-dmsk8s 가 DMS 설치 뒤에 더 보는 것(testbed `scripts/validate.sh`): ① IP 접속 — IngressRoute `dms/dms-portal-ip`, 주소마다
  `dms-portal-tls` 의 IP SAN, bare IP readyz 200(SNI 없음·CA 검증); ② 모르는 이름은 404 + `TRAEFIK DEFAULT CERT`(catch-all·TLSStore default 없음 —
  스위치와 무관하게 늘); ③ 「testbed AD SSO」 절(`scripts/idp.sh check` — IdP 컨테이너·LDAPS·시험 사용자 로그인·dms-api 파드에서 discovery·Secret
  `dms-oidc` 값 대조; `READ_ONLY=1` 이면 로그인을 생략한다). PASS 수는 판마다 바뀐다(예: testbed `docs/ARCHITECTURE.md` §19.8 실측 PASS=219).
- `CACERT` 를 빼면 6절 readyz 가 TLS 검증 실패(000)로 WARN 이 된다(입구 문제를 「Avi 미개통」으로 읽게 된다). ingress-probe 는 CA 없이도
  인증서 이름을 openssl 로 본다.
- 2026-11-16 실측(d27, 새 설치기): S12 PASS=17 WARN=0 FAIL=0 — 5절 Ingress `dms-portal-traefik`·ingress-probe 노드 셋 PASS, 6절 readyz 200
  (`via https://dms-k8s.local`), http 80 「응답 없음 — 80 VS 없음」 INFO, 7절 감지 traefik C·ingress D=.

## 5. 시드·픽스처 값

### 5-1. 스토리지 (deploy/README §6-4)

| 이름 | mount_path | managed_root | backend | 종류 | 기본 소유 | 마운트된 노드 |
|---|---|---|---|---|---|---|
| `cephfs-dms` | `/cephfs` | `/cephfs/managed` | cephfs | 파일시스템 | — | k8s-w1·k8s-w2 |
| `cephfs-third` | `/cephfs-third` | `/cephfs-third/managed` | cephfs | 파일시스템 | — | k8s-w1 |
| `cephfs-secondary` | `/cephfs-secondary` | `/cephfs-secondary/managed` | cephfs | 파일시스템 | — | k8s-w2 |
| `k8s-pv-nfs` | `/k8s_pv/nfs` | = mount_path | nfs | PV | 0:0 | k8s-w1·k8s-w2 |
| `k8s-pv-gpfs` | `/k8s_pv/gpfs` | = mount_path | gpfs | 둘 다(both) | 0:0 | k8s-w1·k8s-w2 |
| `k8s-pv-onefs` | `/k8s_pv/onefs` | = mount_path | onefs | PV | 0:0 | k8s-w1·k8s-w2 |

- CephFS 는 `dmsfs:/k8s` 하위 트리를 같은 경로 이름에 붙인 것이라 ssc 와 경로 문자열이 같아도 데이터는 섞이지 않는다(testbed
  `docs/ARCHITECTURE.md` §13). cp1 에는 `/cephfs` 가 없다.
- `/k8s_pv/*` 는 호스트 NFS 를 `lookupcache=positive` 로 붙인다(testbed `k8s_pv_mount_options` — 다중 노드 dsync 의 음성 dentry ENOENT, k3 실증).
  backend 값(nfs·gpfs·onefs)은 PV 경로 프리셋 키다(`pv_paths.PRESETS`) — 이 테스트베드의 셋은 실제로는 모두 NFS 다.
- 데이터 root 는 `/dms_shared` 와 겹치지 않아 등록 가드(422 `storage_overlaps_dms_state`)에 걸리지 않는다.
- 스토리지 기본 소유는 전부 0:0 이다(사용자 관리 — §1 의 5).

### 5-2. 정책·허용 쌍

- 도구 넷 `scan`·`dsync`·`nsync`·`rm`(`repositories.control.POLICY_TOOLS`), 큐 `dms-data`. `max_nodes` 는 워커 수(2) 이하면 충분하다.
- **`procs_per_node` ≤ 1**: 워커가 2 vCPU 라 allocatable 1800m(테스트베드 실측)에서 launcher 100m·예비 500m
  (`node_cpu.DEFAULT_NODE_CPU_RESERVE_MILLICORES`)를 빼면 노드 CPU 상한이 1이다(`node_cpu.node_cpu_cap`). 더 큰 값은 「요청 N → 노드 CPU 상한
  1」로 줄어 보인다(§6-3).
- 사용자 sync 허용 쌍: k8s 신원 모드에서 일반 사용자의 PV 주소 sync 는 쌍 면제이고 직접 경로는 면제자(쌍 제한 없음)만 쓰므로 이
  테스트베드에서 쌍 목록은 실효가 거의 없다(deploy/README §10-1). 목록은 사용자가 관리한다 — 실증 전에 확인하고 바꾼 것만 되돌린다.
- 그룹 denylist 규칙은 넣지 않는다(422 `denylist_group_unsupported` — 이름 규칙만).

### 5-3. k8s 연동 (deploy/README §6-5 의 테스트베드 값)

대상 클러스터는 둘이다. 자기 자신 **`dmsk8s`** 와, 두 번째 **`dmsk8s2`** = 호스트 docker 의 k3s 컨테이너 `dms-k3s2`(`rancher/k3s:v1.31.4-k3s1`,
API `https://10.10.10.1:6444` — **testbed IaC 밖**, 2026-10-26 d3 멀티 클러스터 실증용 — CHANGELOG 「d3 라이브 실증」). dmsk8s2 의 픽스처는 ns
`lab-x`·`lab-y`, StorageClass `c2-nfs` → 매핑 `k8s-pv-nfs`, 템플릿 `c2/{namespace}/{pvc}`(데이터는 호스트 `/data/k8s_pv/nfs/c2/<ns>/<pvc>` — 워커의
`/k8s_pv/nfs/c2/…`)다. 재생성 절차는 어디에도 기록돼 있지 않다(§7).

| 항목 | 값 |
|---|---|
| 읽기 전용 리더 | testbed `bash scripts/k8s-pv-reader-creds.sh <이 저장소·export 밖 디렉터리>` → `token`·`ca.crt`(0600, 토큰은 출력하지 않는다) — SA `dms-reader/dms-inventory` |
| 호출 노드·주기(「개요」 탭) | 주 `k8s-w1`, 보조 `k8s-w2`, 5분 |
| 클러스터 등록(「클러스터」 탭) | 이름 `dmsk8s`, API 서버 `https://k8s-cp1:6443`(CoreDNS 가 `k8s-cp1` → 10.10.10.20 — testbed `coredns-hosts-dmsk8s`), TLS 「지정한 CA」(위 `ca.crt`), 토큰 → 카드의 「지금 동기화」 |
| StorageClass 매핑 | `tb-nfs → k8s-pv-nfs`, `tb-gpfs → k8s-pv-gpfs`, `tb-onefs → k8s-pv-onefs`(템플릿 비움 = 백엔드 프리셋). 「예시 경로」가 워커의 실제 디렉터리인지 `ssh k8s-w1 ls -d <예시 경로>` |
| 계정 | 픽스처 RoleBinding(ns `team-a`·`team-b`·`team-c`, ClusterRole `kubeflow-admin/edit/view`)의 ID 와 같은 아이디 — 예: `junwoo-song-clusterrole-admin` → `junwoo.song`. stub 메일이라 인증번호가 화면에 나온다(테스트베드만) |
| 기대값(2026-11-19 DB 실측 — 클러스터 카드가 보이는 개수) | **dmsk8s**: PV 이름(PVC) 10 · Bound 9, RoleBinding 37 · 인식 8, 권한 사용자 5. **dmsk8s2**: PVC 2 · Bound 2, RoleBinding 13 · 인식 3, 권한 사용자 3. dmsk8s 의 PVC 는 픽스처 7(Bound 6)에 2026-11-03 T7 의 CNPG PVC 3개(ns `dms` 의 `dms-pg-1`~`3`)를 더한 것이다 — 에이전트는 PVC 를 클러스터 전체에서 읽고 카운트는 namespace 를 거르지 않는다(`repositories/k8s.py` `cluster_counts`). 인식 8 은 2026-10-26 d3 의 namespaceAdmin 인식(5 → 8) 뒤 값이다. 픽스처(testbed `make pv-fixtures-dmsk8s`)나 DB 인스턴스 수를 바꾸면 달라진다 |

### 5-4. e2e 픽스처 (`dms-shared-fs.yml`, root 소유)

`/cephfs/managed/e2e/` 아래 `growth/`(사용량 분석 — `fresh-1.bin`·`fresh-3.bin`·고정 과거 mtime 의 `old-a.bin`·`old-b.bin`), `victim-protected/`
(`keep.txt`·`data/k1.txt`), `dest/`(0777 — sync 목적지). LDAP uid/gid 소유 픽스처(alice·bob·cocoa.song — `ldap-e2e`)는 ssc 전용이라 여기는 없다.

## 6. 일상 운영

### 6-1. 빌드 → 릴리스 → 태그 bump (deploy/README §8·§9)

1. 빌드할 커밋을 **origin/dms-k8s 에 push** 한다 — 빌드 소스 `/home/mason/dms-dev/dms-k8s` 는 호스트 cron 이 ff 로만 맞춘다(origin/main 은 어느
   포탈 빌드에도 닿지 않는다). 확인: `git -C /home/mason/dms-dev/dms-k8s log -1 --oneline`. 빌드 파드는 그 경로의 `.git` 에서 SHA 를 읽는다
   (미커밋 변경이 있으면 `-dirty`).
2. 빌드 전 게이트(deploy/README §8 「이미지 빌드 전 게이트」) — 개발 PC 에서 같은 커밋으로.
3. 포탈 「빌드」: 이미지 `[dms-mpifileutils, dms, dms-agent]`, 태그 = 제안 칩 `dN`. 제안은 라이브 태그·레지스트리의 dms 태그·빌드 이력 가운데
   최댓값 + 1 이고 **릴리스 이력은 보지 않는다**(`BuildForm.nextTagSuggestion`) — 시험 빌드를 레지스트리·빌드 기록에서 지웠으면 이미 릴리스한
   번호가 다시 제안될 수 있으니 그때는 더 큰 번호를 직접 넣는다.
4. 포탈 「릴리스」: agent → api → controller, 그리고 잡 이미지(`job-image`). 에이전트 보고 필드에 컨트롤러가 기대는 변경이면 agent 를 먼저
   내고 관리 → 노드(`/api/admin/nodes`)에서 새 필드를 확인한 뒤 api·controller 를 낸다. controller 롤아웃 뒤 **약 30초** 기다린 다음 실증한다
   (종료 유예 중인 옛 컨트롤러가 루프를 계속 돈다 — BACKLOG 「컨트롤러 롤아웃 겹침 창」).
5. 오버레이 bump 커밋(**dms-k8s 에서만**, pathspec): `kustomization.yaml` 의 `newTag` 두 항목(dms 계보 = api·controller·migrate, dms-agent 계보)
   + `patch-config.yaml` 의 `DMS_JOB_IMAGE`. 관례상 `kustomization.yaml` 머리 주석의 태그 이력에 한 줄을 더한다.

   ```bash
   git commit -m "chore(testbed-k8s): 오버레이 태그 <옛 dN> → <새 dN>" -- \
     deploy/overlays/testbed-k8s/kustomization.yaml deploy/overlays/testbed-k8s/patch-config.yaml
   ```

   bump 를 빠뜨리면 다음 S11·가드가 「렌더 이미지 ≠ 라이브」로 exit 3 거부한다(옛 태그로 조용히 되돌리지는 않는다).
   포탈 릴리스는 라이브 ConfigMap 의 `DMS_JOB_IMAGE` 를 바꾸지 않는다(런타임 잡 이미지는 DB `job-image` 행이다). 그래서 bump 커밋 뒤 첫 S11 은
   `dms-config` 를 바꾸고, 파드 템플릿이 그대로인 dms-api·dms-controller·dms-agent 셋을 모두 rollout restart 한다(템플릿 지문 + 워크로드
   어노테이션 `dms.io/install-config-rev` — §6-2). 에이전트 롤링이 돌므로 조용한 시각에 돌린다.

### 6-2. 오버레이·설정 변경 적용

- **권장**: `sh deploy/install.sh --context dmsk8s --site testbed-k8s --only S11` — (매 실행 렌더) → 시크릿 게이트 → 앱 이미지 확인 →
  guard-images(3·4·5) → migrate Job 삭제 → `apply -k`(client-side) → 재시작 판정과 설정 표지(대기 전에 — `dms-config`·`dms-secrets` 의
  resourceVersion 이 바뀌었는데 파드 템플릿 지문이 그대로인 워크로드를 고른다) → migrate 완료·롤아웃 대기(ds/dms-agent 는 노드 수 비례) →
  고른 워크로드 rollout restart → 마스터(k8s-cp1) `dms` 이미지 사전 pull(실패는 WARN)(`install.sh` 머리 S11 줄·`stage_S11`).
- **손 판**(`kustomization.yaml` 머리 주석과 같다):

  ```bash
  kubectl --context dmsk8s -n dms delete job dms-migrate --ignore-not-found
  sh deploy/overlays/guard-images.sh deploy/overlays/testbed-k8s --context dmsk8s   # 0 통과 · 3 이미지 · 4 컨텍스트 · 5 hostPath
  kubectl --context dmsk8s apply -k deploy/overlays/testbed-k8s
  kubectl --context dmsk8s -n dms wait --for=condition=complete job/dms-migrate --timeout=180s
  kubectl --context dmsk8s -n dms rollout status deployment/dms-api
  kubectl --context dmsk8s -n dms rollout status deployment/dms-controller
  kubectl --context dmsk8s -n dms rollout status daemonset/dms-agent
  ```

- **base(`deploy/k8s`)를 raw 로 apply 하지 않는다**(`00-namespace.yaml` 만 예외 — deploy/README §3-1): `20-config.yaml` 을 raw 로 올리면
  오버레이의 사이트 값(`DMS_USER_ALLOWED_OPERATIONS`·`DMS_JOB_IMAGE` 등)이 base 값으로 돌아가고, 워크로드는 자리표시자 이미지라 pull 에서 실패한다.
- **ConfigMap 만 바뀐 손 apply 는 파드를 다시 띄우지 않는다** — `dms-config` 는 api·controller 의 `envFrom` 이라 기동 때만 읽힌다
  (deploy/README §3-5). 손 apply 뒤 `kubectl --context dmsk8s -n dms rollout restart deploy/dms-api deploy/dms-controller`. 에이전트는
  `dms-config` 를 `envFrom` 으로 받지 않는다(`50-agent-daemonset.yaml` 머리 주석 — env 를 명시한다).

### 6-3. 잡이 Volcano 에서 기다리는 것은 정상

워커는 `procs_per_node` 만큼 CPU 를 실제로 요청한다(launcher 100m). 워커가 2 vCPU 라 노드마다 1코어 워커 하나 정도만 들어가서, 동시 잡·배치
자식은 Volcano 에서 기다린다(대시보드 「Volcano 대기」, PodGroup 「Insufficient cpu」). 결함이 아니다 — 여러 잡을 내는 실증은 순서대로 돈다.

### 6-4. 자동화 로그인

- Bearer 공유 토큰은 조회·스토리지·정책까지다. 잡 제출은 403 `privileged_not_authorized`(세션 인증만 root 잡 — `routes_requests.submit`),
  컨펌은 403 `confirm_session_required`(`routes_jobs.confirm_job`).
- 공유 토큰 호출에 `x-dms-actor` 헤더를 붙이지 않는다 — 토큰 경로는 `node:<이름>` 만 받아 400 `invalid_actor`(`dms.api.auth`).
- 스크립트가 세션이 필요하면 **봉인 로그인**: `GET /api/auth/transport-key` → `dms.api.password_transport.seal_with_info(info, pw,
  purpose="login", username=u)` → `POST /api/auth/login` 에 `{"username": u, "password_enc": <봉인>}` → 세션 쿠키 `dms_session`. 평문
  `password` 는 422 `password_encryption_required`. 일반 절차·로그인 상한은 deploy/README §10-7. 저장소 안의 도우미 스크립트는 아직 없다(BACKLOG).

### 6-5. 한 호스트의 두 포탈

ssc(`dms.local`)와 이 테스트베드(`dms-k8s.local`)의 세션 쿠키 이름이 같다(`dms_session` — `dms.api.app`). 쿠키는 포트가 아니라 호스트 단위라,
같은 IP 의 다른 포트로 둘을 열면 서로 로그아웃시킨다. hosts 를 둘 수 있는 기기는 늘 호스트 이름으로 연다. hosts 가 없는 기기(Tailscale 휴대폰)는
dmsk8s 를 IP 로 연다(`https://<그 주소>:8443/` — §2 「IP 직접 접속」). 그 IP 의 :443 이 ssc 포탈이므로, 그런 기기에서 두 포탈을 다 IP 로 쓰면 서로
로그아웃시킨다 — 하나는 이름으로 연다. 포탈 부제(「K8S 테스트베드」)로 화면을 구분한다.

### 6-6. 재부팅 뒤

testbed `make post-boot-check WAIT=900`(`README.md` 「After a host reboot」 — 증상 표 포함). DMS 상태는 `/dms_shared`(호스트 NFS)와 CephFS 에
있어 VM 재부팅에도 남는다. 재부팅 직후 첫 잡은 에이전트 첫 보고(30초)·프로브(60초) 전이면 `no_ready_sync_candidate` 로 거부될 수 있다 —
1~2분 뒤 다시 낸다.

### 6-7. 사내 AD SSO 시험 환경 (2026-11-18)

프로덕션 모양(사내 AD → Keycloak(OIDC IdP, LDAP 연동) → DMS 「Sign in with … SSO」 — deploy/README §6-9)을 테스트베드에 그대로 세웠다. IdP 는 클러스터가
아니라 **호스트 docker**(워커 자원을 쓰지 않는다)이고 testbed IaC 가 관리한다(`make idp-up`·`idp-config`·`idp-check`·`idp-dms-secret`·`idp-down`,
`scripts/idp.sh`, 설계·실측은 testbed `docs/ARCHITECTURE.md` §24).

| 요소 | 테스트베드 값 |
|---|---|
| AD | Samba AD DC 컨테이너(`dms-idp-dc`) — realm `AD.DMS-K8S.LOCAL`(DNS 도메인 `ad.dms-k8s.local`), DC `dc1.ad.dms-k8s.local`(LDAPS 인증서 SAN). docker 내부망 `dms-idp` 에만, 호스트·LAN 에 포트 없음 |
| IdP | Keycloak 컨테이너(`dms-idp-kc`) + PostgreSQL 17.10 `dms-idp-db`(Keycloak DB — 내부망 `dms-idp` 에만, publish 없음), issuer `https://idp.dms-k8s.local:8444/realms/dms-testbed`(`10.10.10.1:8444` 에만 열림), 인증서 = 테스트베드 CA |
| AD 연동 | vendor AD · LDAPS(인증서 검증) · 아이디 = sAMAccountName · `mail` → email · **READ_ONLY**(사용자가 email 을 못 바꾼다) · Trust Email 끔(`email_verified: false`) |
| client | `dms-k8s` — confidential, Standard flow 만, PKCE S256 필수, redirect `https://dms-k8s.local/api/auth/oidc/callback` |
| 이름 해석 | `idp.dms-k8s.local → 10.10.10.1` — 호스트 `/etc/hosts`(`make host-hosts`)와 dmsk8s 파드(CoreDNS hosts 블록, `make coredns-hosts-dmsk8s`) |
| DMS 쪽 | Secret `dms/dms-oidc`(`make idp-dms-secret` — 비밀은 testbed `files/idp/` 의 파일에서, 저장소 밖), 버튼 글자 `Sign in with Testbed AD SSO` |
| AD 시험 계정 | `sso.alice`(새 계정 → user 로 자동 생성) · `mason`(기존 특권 계정 → admin 그대로) · `admin`(차단) · `sso.other`(@other.example — 도메인 불일치) · `sso.disabled`(DMS 에서 비활성화해 `account_disabled` 시험). 비밀번호는 testbed `files/idp/users/<이름>.pw`(0600, git 제외) |

- **호스트 브라우저에서만** 된다: IdP(`10.10.10.1:8444`)와 redirect(`https://dms-k8s.local` = 10.10.10.1:443)가 호스트 경로라, LAN·Tailscale 기기의
  브라우저는 IdP 에 닿지 않는다(그 기기는 비밀번호 로그인을 쓴다).
- 켜고 끄기: Secret 이 있으면 켜짐 — 끄려면 `kubectl --context dmsk8s -n dms delete secret dms-oidc` 뒤 `rollout restart deploy/dms-api`. Secret 을
  바꾼 뒤에도 늘 `rollout restart deploy/dms-api`(S11 의 설정 재시작은 이 Secret 을 보지 않는다).
- email_verified 시험: `bash scripts/idp.sh trust-email on|off`(testbed) — 켜면 id_token 이 `email_verified: true`. DMS 쪽 `DMS_OIDC_REQUIRE_EMAIL_VERIFIED=true`
  를 Secret 에 더하면 확인되지 않은 메일을 거절한다(`sso_email_unverified`). 기본은 둘 다 끔(IaC 기본값 — `make idp-config` 가 Trust Email 을 끔으로 되돌린다).
- **클러스터를 다시 세우면 Secret `dms/dms-oidc` 가 없어진다**(IdP 컨테이너는 호스트에 남는다): testbed `make idp-check idp-dms-secret` →
  `kubectl --context dmsk8s -n dms rollout restart deploy/dms-api` → `curl --cacert <CA> https://dms-k8s.local/api/auth/sso-info` 가 `"enabled":true`.
  Secret 이 없을 때 idp check 는 info 만 내므로 validate 가 잡지 못하고 SSO 버튼만 조용히 사라진다(§4 표 9b).
- 호스트 재부팅 뒤 Keycloak 이 안 떠 있으면(docker 가 `dmsbr0` 보다 먼저 떠 `10.10.10.1:8444` 를 못 붙인 경우) testbed `make idp-up`.
- `make idp-down` 은 컨테이너만 지운다(볼륨·네트워크·`files/idp/` 유지 — `idp-up` 이 같은 AD·Keycloak 을 되살린다). `make idp-down PURGE=1` 은
  볼륨까지 지워 다음 `idp-up` 이 새 AD·새 Keycloak 이 되고 client secret 도 새로 나온다 — 그때는 `make idp-config idp-dms-secret` +
  `rollout restart deploy/dms-api` 를 한다.
- 로그아웃은 DMS 세션만 끝낸다 — IdP 세션이 남아 버튼을 다시 누르면 비밀번호 없이 들어온다(프로덕션과 같다, deploy/README §6-9).

## 7. 되돌림 상태·보존물

지우거나 덮기 전에 보는 목록이다. 「삭제 시점」이 사용자 결정인 것은 BACKLOG 의 그 항목이 진실이다.

| 무엇 | 왜 남아 있나 | 되돌리는 법 | 삭제 시점 |
|---|---|---|---|
| 옛 아티팩트 base `/cephfs/dms/artifacts`(CephFS — d17 까지 라이브 hostPath `/cephfs`) | T3(2026-11-03, d18) 전환은 **복사**였다(CHANGELOG ③). 옛 잡 산출물의 원본 | 아래 7-1 — 지금은 사실상 되돌림 경로가 아니다 | 사용자 결정(BACKLOG 「옛 base `/cephfs/dms/artifacts` 의 보존 기한·삭제 시점」) |
| pkg-01 외부 PG `dmsdb_k8s`/`dmsapp_k8s` + 시크릿 `dms-secrets-prev`(T7 이관 전 URL) | T7(2026-11-03) 이관은 원본을 건드리지 않았다(CHANGELOG ⑤) | 7-2 | 사용자 결정(BACKLOG 「pkg-01 외부 PG(`dmsdb_k8s`) 정리 시점」) |
| 되돌림 입구: MetalLB VIP `10.20.20.101` → ingress-nginx(오버레이 `patch-ingress.yaml`·`patch-metallb.yaml` + testbed IaC) | 2026-11-04 입구 영구 컷오버(호스트 edge — 그때 뒤는 Envoy)의 소크 동안. 2026-11-16 edge 뒤를 Traefik 으로 바꾼 뒤에도 그대로 둔다 | 7-3 | 소크 뒤(별도 결정 — BACKLOG ⑥ 「소크 뒤 Ingress·MetalLB 삭제」) |
| k8s-cp1 containers-storage 의 레지스트리 자기 이미지(손 반입·고정) | 레지스트리가 자기 이미지를 자기에게서 받을 수 없다 | cp1 재생성 때 §4-2 | 지우지 않는다 |
| 두 번째 대상 클러스터 `dmsk8s2`(호스트 docker 컨테이너 `dms-k3s2` — §5-3) | IaC 밖 실험 클러스터(2026-10-26 d3 멀티 클러스터 실증). 재구축 절차가 어디에도 기록돼 있지 않다(**미검증**) | 없다 — 호스트를 새로 세우면 다시 생기지 않는다. 그 클러스터 카드가 실패하면 포탈 「k8s 연동」 「클러스터」 탭에서 끄거나 지운다 | 사용자 결정 |

### 7-1. 옛 base 로 되돌리기 — 지금은 권하지 않는다

③ 직후의 되돌림은 `values.env` 의 `SHARED_DIR=/cephfs` 와 `patch-config.yaml` 의 base `file:///cephfs/dms/artifacts`(그리고
`tests/test_shared_dir_contract.py` 의 기대값 `/dms_shared`)를 함께 고쳐 `ALLOW_HOSTPATH_CHANGE=1` 로 적용하는 것이었다. 그러나 ④⑤ 뒤로는
**레지스트리 저장소(`<SHARED_DIR>/dms/registry`)·PG PV(`<SHARED_DIR>/dms/pgdata`)·덤프(`<SHARED_DIR>/dms/backup/pg`)도 같은 `SHARED_DIR` 에서
나온다**(`render.sh` 의 레지스트리 hostPath·`PGDATA_DIR`·백업 경로). SHARED_DIR 만 바꾸면 레지스트리·DB 층의 렌더 경로도 함께 바뀌므로,
되돌리려면 레지스트리 저장소 이동(deploy/README §2e-5)과 DB 이관을 함께 해야 한다 — 시험한 적 없다(**미검증**). 그 배치에서는 `cephfs-dms`(`/cephfs`)가
상태 디렉터리 `/cephfs/dms` 를 덮어 「아티팩트 경로」 화면에 겹침 경고도 뜬다. 옛 산출물이 필요하면 원본을 읽기만 한다(복사본은 새 base 에 있다).

### 7-2. DB 를 외부 PG 로 되돌리기·CNPG 걷어내기

`RD=deploy/.rendered/testbed-k8s`(deploy/README §2f-3·§2f-4 와 같은 뜻). T7 뒤로는 `dms-pg` 가 앱 DB 라 **먼저 앱을 되돌린다**:

1. 포탈 컨트롤 상태의 drain 을 켠다(잡 제출 정지 — 이관 때와 같은 정지 창, deploy/README §2f-2 「이관 순서」).
2. `python3 deploy/pg-secrets.py --context dmsk8s restore-url`(`dms-secrets-prev` 의 옛 URL 을 다시 넣는다) →
   `kubectl --context dmsk8s -n dms rollout restart deploy/dms-api deploy/dms-controller` → drain 을 끈다. 그 뒤 `pg-secrets.py check` 는 FAIL 이
   맞다(CNPG URL 이 아니다). **이관 뒤의 쓰기는 외부 PG 에 없다** — 살리려면 `dms-pg` 덤프를 원본에 먼저 복원한다(deploy/README §2f-5).
3. CNPG 를 걷을 때만: `kubectl --context dmsk8s delete -k $RD/platform/db-backup` → `kubectl --context dmsk8s -n dms delete cluster dms-pg`
   (PVC 가 함께 지워지면 PV 3개가 Released — deploy/README §2f-5 재활용 런북) → (다시 시작하지 않을 때만) `delete -k $RD/platform/db-volumes`
   (PV·SC — 디렉터리는 남는다) → `delete -k $RD/addons/cnpg`(CRD 를 지우면 그 종류의 객체가 모두 지워진다 — Cluster 가 0개인지 먼저 확인).
   `dms-pg-app` 은 Cluster 를 다시 만들 때 그대로 쓴다.

### 7-3. 입구를 ingress 로 되돌리기

testbed `group_vars/dmsk8s.yml` 의 `portal_edge: ingress` + `make host-net host-hosts host-edge`(DNAT 먼저 → 이름 → frontend 제거 — 세 스크립트가
틀린 순서를 거부한다). edge 로 다시 갈 때는 `portal_edge: traefik` + `make host-edge host-net host-hosts`(노드에 Traefik 이 떠 있어야 한다 — 가드
`host-edge.sh ready`). 옛 값 `gateway` 는 은퇴했다(testbed `tbinfo.py` 가 이유와 함께 거부) — **Envoy 입구로의 되돌림은 없다**(DMS 쪽 입구 층은
aa26bbce 가 지웠고, 라이브의 DMS Envoy·`envoy-gateway-system`·EG CRD·DMS 가 깐 Gateway API CRD 는 2026-11-16 정리했다. PriorityClass
`dms-addon-critical` 은 CNPG 오퍼레이터가 써서 남는다). 상세·검증: testbed `docs/ARCHITECTURE.md` §15.3·§15.5·§19.5·§19.7, `README.md`
「Portal edge」. 확인: `curl --cacert <CA> --resolve dms-k8s.local:443:10.20.20.101 https://dms-k8s.local/` 200.

**소크 뒤 삭제할 때 DMS 쪽 할 일**: 오버레이에서 `patch-ingress.yaml`·`patch-metallb.yaml` 을 빼고 base 의 Ingress·MetalLB 풀을 지우는 패치(prod 의
`deploy/overlays/prod/patch-remove-ingress.yaml` 모양)를 싣고, `tests/test_testbed_k8s_overlay.py` 의 `K8S_VIP`·`K8S_HOST` 단언을 같이 바꾼다. 그때
testbed IaC 가 dmsk8s 의 ingress-nginx·MetalLB 를 걷고 되돌림 경로는 없어진다.

### 7-4. 레지스트리·이미지 — 되돌릴 곳이 없다

- **external 로의 되돌림은 없다**: 그 전의 `pkg-01:5001`(d1~d18·k0~k4)은 2026-11-04 컨테이너·데이터째 삭제했다(사용자 결정 — CHANGELOG
  「옛 dmsk8s 레지스트리 pkg-01:5001 삭제」). 그래서 deploy/README §2e-5 「걷어내기」를 이 테스트베드에 쓰지 않는다 — 걷으면 앱·빌더가
  이미지를 받을 곳이 없다. 노드 CRI-O 는 `10.112.0.50:5000` 하나만 신뢰한다(testbed crio 롤의 `50-dms-registry.conf`).
- 옛 태그로 돌아가기 = 포탈 릴리스 이력에서 **이 레지스트리에 있는 태그**를 다시 고른다. d18 은 전환 때 옮긴 같은 digest 사본이고, d18 이하의
  동봉 매니페스트는 `pkg-01:5001` 로 스탬프돼 있어 그 태그로 릴리스하면 드리프트 배지가 「모름」이다. d1~d17·kN 이미지는 더 없다.
- **`kubectl rollout undo`(`--to-revision` 포함)로 되돌리지 않는다**: 포탈 릴리스 기록·드리프트 판정·오버레이 태그가 모르는 상태가 되고, 다음
  S11 가드가 「렌더 이미지 ≠ 라이브」로 exit 3 거부한다(deploy/README §5-1). 되돌림은 포탈 릴리스로만 한다. (2026-11-04 무렵에는 롤아웃 이력 —
  dms-api·dms-controller 의 0-replica ReplicaSet, dms-agent ControllerRevision — 이 옛 `pkg-01:5001/…` 를 가리켜 `ImagePullBackOff` 이기도 했다.
  지금 이력은 revisionHistoryLimit 10 이라 모두 `10.112.0.50:5000` 이다.)

## 8. 시나리오 — 실 클러스터 확인

옛 판의 시나리오(Bearer 제출·`/cephfs/dms` 아래 대상·ssc 노드)는 지금 그대로는 돌지 않는다 — 다시 쓴 판이다. 프로덕션 스모크는 deploy/README §7.

- 공통: 포탈 로그인 세션, 또는 스크립트면 봉인 로그인(§6-4). `B=https://dms-k8s.local`, CA 는 §2 의 테스트베드 CA. 요청 흐름은
  `POST /api/user/requests`(202, `request_id`) → `GET /api/user/requests/<request_id>/jobs` 로 잡 상태를 몇 번 본다(planner 10초·stepper 5초 주기)
  → `ConfirmPending` 이면 그 잡의 `preview_fingerprint` 로 `POST /api/user/jobs/<job_id>:confirm` `{"fingerprint": "<…>"}`(세션만).
- 클러스터 쪽 확인(launcher 파드에는 `dms.io/job-id` 라벨이 없다 — 이름의 `<job_id 앞 12자>` 로 찾는다):
  `kubectl --context dmsk8s -n dms get vcjob,pods -o wide | grep <job_id 앞 12자>`.

### 8-1. 일반 사용자 — PV 주소 sync·rm

계정은 §5-3 의 픽스처 RoleBinding ID 와 같은 아이디(특권 목록 밖). 포탈 「단일 작업」에서 자기 namespace 의 PV 만 보이는지 먼저 본다.

```json
{"operation": "sync",
 "source_pv": {"cluster": "dmsk8s", "namespace": "team-a", "pvc": "<PVC 이름>"}, "source": "<하위 경로>",
 "destination_pv": {"cluster": "dmsk8s", "namespace": "team-a", "pvc": "<PVC 이름>"}, "destination": "<하위 경로>",
 "priority": "mid"}
```

- 미리보기 → 본인 컨펌 → `Succeeded`. 목적지 결과 소유 = 목적지 스토리지 기본 소유자(이 테스트베드 0:0).
- rm: `{"operation": "rm", "pv": {…}, "target": "<하위 경로>"}` → 미리보기 → 본인 컨펌. PV 루트(`''`·`'.'`·`'./'`)는 422 `pv_root_rm_forbidden`.
- 기대 거부: 직접 경로(`storage`·`source_storage` …) 403 `raw_path_not_allowed`, scan 403 `scan_unrestricted_only`, 맵핑 없는 namespace 403
  `k8s_access_denied`, Bearer 제출 403 `privileged_not_authorized`(전체 표는 deploy/README §10-1).

### 8-2. 특권 운영자(`mason`) — 직접 경로 scan·dsync·nsync

`target`·`source`·`destination` 은 그 스토리지 `managed_root` 아래의 상대 경로다(`stepper.JobStepper._abs`). 없는 대상은 preflight 가
`target_not_readable` 로 끊는다.

```json
{"operation": "scan", "storage": "cephfs-dms", "target": "e2e/growth", "priority": "mid"}
{"operation": "sync", "source_storage": "cephfs-dms", "source": "e2e/growth",
 "destination_storage": "cephfs-third", "destination": "<새 하위 경로>", "priority": "mid"}
{"operation": "sync", "source_storage": "cephfs-third", "source": "<위 dsync 의 결과>",
 "destination_storage": "cephfs-secondary", "destination": "<새 하위 경로>", "priority": "mid"}
```

- scan 은 컨펌 단계가 없다.
- 둘째 줄은 **dsync**: `cephfs-dms`(k8s-w1·w2)와 `cephfs-third`(k8s-w1)를 함께 마운트한 k8s-w1 이 있다(`placement.select_tool_and_candidates`).
- 셋째 줄은 **nsync**: `cephfs-third`(k8s-w1)와 `cephfs-secondary`(k8s-w2)는 공유 노드가 없다 → gang vcjob 의 task 가
  `launcher`·`source-worker`·`destination-worker`(`execution_manifests._build_nsync_job`). 확인:
  `kubectl --context dmsk8s -n dms get vcjob <이름> -o jsonpath='{.spec.tasks[*].name}{"\n"}'`.
- 파일시스템 스토리지로의 직접 경로 결과는 소스 소유를 보존한다(deploy/README §10-1).

### 8-3. 취소

`POST /api/user/jobs/<job_id>:cancel`(실행·미리보기 중) → `Cancelled`. 확인: `kubectl --context dmsk8s -n dms get vcjob,pods -o wide | grep <job_id 앞 12자>`
에 남은 것이 없어야 한다(종료 유예 동안은 Terminating 으로 보일 수 있다).

## 9. 검증 도구

| 도구 | 언제 | 메모 |
|---|---|---|
| 로컬 풀스택 e2e(`cd frontend && npm run test:e2e`) | 이미지 빌드 전 | deploy/README §8 「이미지 빌드 전 게이트」 — 수기, CI 없음 |
| `make validate-dmsk8s` · `make post-boot-check`(testbed 저장소) | 인프라 변경·재부팅 뒤 | edge·PV 픽스처·리더 `auth can-i`·빌드 소스 마운트, IP 접속(IngressRoute `dms/dms-portal-ip`·주소마다 IP SAN·bare IP readyz 200 CA 검증), 모르는 이름 404 + `TRAEFIK DEFAULT CERT`(catch-all·TLSStore default 없음 — 늘), 「testbed AD SSO」 절(`scripts/idp.sh check` — `READ_ONLY=1` 이면 로그인 생략)까지(testbed `README.md` 「Validation」 — §4-10) |
| `kubectl --context dmsk8s get nodes -o wide` · `sh deploy/preflight-cluster.sh --context dmsk8s` | 언제든(읽기 전용) | 노드 3대 Ready, control-plane 1대 WARN 은 정상 |
| `install.sh --only S12`(§4-10 의 env 와 함께) | 설치·업그레이드 뒤 | 클러스터를 순수하게 읽기만 하려면 `VERIFY_NO_PROBES=1`(파드를 띄우는 검사 생략 — ingress-probe 는 HTTPS GET 뿐이라 그대로 돈다) |
| `sh deploy/ingress-probe.sh --context dmsk8s --domain dms-k8s.local --cacert <CA>` | 입구를 의심할 때(읽기 전용 — kubectl 은 get 만, 파드를 띄우지 않는다) | 노드마다 `NODE <이름> PASS\|FAIL\|UNKNOWN <근거>`, 마지막 `RESULT:`. 종료 0 PASS · 1 FAIL · 4 UNKNOWN · 2 사용법. 판정은 §4-8 |
| `python3 deploy/shared-storage-probe.py --context dmsk8s --overlay deploy/overlays/testbed-k8s` | 공용 디렉터리를 바꾼 뒤(S7 과 같은 검사) | testbed-k8s 는 렌더 단계 없는 고정 오버레이라 그 디렉터리에서 dms 이미지(newTag)를, 커밋된 `values.env` 에서 `SHARED_DIR` 를 읽는다. prod 형태(렌더 디렉터리 + `--shared-dir`)는 deploy/README §2d-3 |
| 가짜 Knox 릴레이 `tests/fake_knox_relay.py` | 메일 경로를 실검증할 때만 | 아래 |
| LDAP 0건 확인(선택 — 렌더 0건은 `tests/test_testbed_k8s_overlay.py` 가 고정) | 설정을 다른 라인에서 가져왔을 때 | 아래 |

**가짜 Knox 릴레이** — 사내 릴레이에 닿지 않는 테스트베드에서 `mailer.py` 가 기대하는 계약을 흉내 낸다:

```bash
python3 tests/fake_knox_relay.py --host 0.0.0.0 --port 8025 --token <T> --outbox <DIR>   # dms-api 파드가 닿는 곳에서(예: 호스트 10.10.10.1)
```

포탈 관리 → 메일 설정에서 발송 방식 `knox_relay`, IP·포트(8025)·키(`<T>`) → 연결 확인 → 테스트 메일 → `<DIR>/NNNN.html`. 가입·재설정 인증
메일은 발송 방식이 knox_relay 일 때만 릴레이로 간다. **끝나면 원복한다**: 발송 방식을 비우거나 `stub` 으로, 키를 지우고, 가짜 릴레이를 끄고
(꺼진 채 knox_relay 면 502), 시험 계정을 지운다. `--allowed-clients` 로 `RELAY_ALLOWED_CLIENTS` 를 흉내 낼 때 이 테스트베드의 출발지는 dms-api 가
뜬 **워커**의 IP 다(prod 는 마스터 — 이 차이는 §10; 출발 IP 가 노드 IP 로 보이는지는 dmsk8s 에서 **미검증**). 가짜 릴레이 실증 기록은 ssc
테스트베드의 것이다(2026-10-01 d147·d148 — `docs/history/changelog-2026-09-to-10-21.md` 「인증 메일 Knox 릴레이」) — dmsk8s 에서 같은
경로를 돌린 기록은 없다(**미검증**).

**LDAP 0건 확인**(옛 판의 배포 게이트):

```bash
kubectl --context dmsk8s -n dms get cm dms-config -o yaml | grep -ci ldap                     # 0
kubectl --context dmsk8s -n dms exec deploy/dms-api -- env | grep -c LDAP                    # 0
kubectl --context dmsk8s -n dms get ds dms-agent -o jsonpath='{.spec.template.spec.containers[0].command}'   # ["dms"]
```

## 10. 테스트베드의 한계 — 프로덕션과 다른 점

이 테스트베드에서 PASS 한 것이 프로덕션에서 무엇을 보장하지 않는지. 프로덕션 요건은 deploy/README §0.

| 항목 | 테스트베드 | 프로덕션 | 뜻 |
|---|---|---|---|
| control-plane | k8s-cp1 **1대** | 마스터 3대 | S1 WARN 이 정상, PG anti-affinity 를 `all-nodes` 로 피함, 레지스트리가 한 노드 — 레지스트리 HA 가 시험되지 않는다(입구는 2026-11-16 부터 노드 셋 모두 Traefik — edge 풀 3) |
| 제어면 배치 | api·controller 가 워커(§3) | 마스터(`patch-placement.yaml`) | 메일 발송의 출발 IP, 노드 사망 때 복귀 수치가 다르다. k8s 연동 호출의 출발지는 배치와 무관하다 — 둘 다 지정한 호출 노드의 dms-agent(테스트베드 k8s-w1) |
| 에이전트 배치 | k8s-cp1 은 control-plane NoSchedule taint 라 dms-agent 가 k8s-w1·w2 에만 뜬다(toleration 은 빈 key NoExecute 하나) | RKE2 서버 노드는 기본으로 taint 가 없어 마스터에도 뜨고, 잡·빌드·k8s 연동 호출 노드 후보가 된다(deploy/README §0-2) | 마스터에 에이전트가 있을 때의 후보 선택·자원 경합은 여기서 시험되지 않는다 |
| L4 입구 | 호스트 haproxy(Avi VS-443 흉내, PROXY v2, 풀 = 노드 셋 :443 의 Traefik). 출발지 하나(10.10.10.1) | Avi VS-443(VIP-A, PROXY v2, 풀 = Traefik 이 도는 노드 :443 — RKE2 rke2-traefik 은 모든 노드). 출발지 = SE 들(`<SE 주소>`) | 헬스 모니터·유휴 타임아웃·PROXY v2 동작만 흉내 낸다. SE 가 여럿인 출발지를 trustedIPs 에 빠짐없이 넣었는지는 사이트에서 따로 확인한다(빠진 SE 를 지난 연결은 PROXY 바이트가 TLS 앞에 붙어 깨진다) |
| Traefik | testbed IaC 가 깐 rke2-traefik 모양(같은 판, 차트 렌더 args 순서 그대로 — 이름은 `traefik`), trustedIPs 는 IaC 템플릿 값 | RKE2 `rke2-traefik`(Helm) — 정적 설정(websecure `proxyProtocol.trustedIPs`·타임아웃)은 사이트가 Rancher chartValues / HelmChartConfig 로 넣는다 | DMS 감지는 이미지 이름으로 찾아 둘이 같다. chartValues·HelmChartConfig 로 값을 넣는 경로와 그때의 롤링은 여기서 시험되지 않는다 |
| 사내 SSO IdP | 호스트 docker 의 Samba AD DC + Keycloak 26.8(§6-7), 인증서 = 테스트베드 CA, 호스트 브라우저에서만 | 사내 AD + 사이트 Keycloak(사내 CA), 사용자 PC 어디서나 | AD 의 실제 스키마·그룹·정책(계정 잠금·만료), 사이트 Keycloak 판·realm 설정(Trust Email 등)은 시험되지 않는다 — DMS 가 보는 OIDC 계약(issuer·PKCE·client_secret_basic·email 클레임)만 같다 |
| IP 직접 접속 | 테스트베드 전용 IngressRoute `dms/dms-portal-ip` + IP SAN 이라 bare IP 가 200(§2) | DMS 는 Host = FQDN 인 Ingress 하나뿐이다(IngressRoute·catch-all 은 쓰지 않는다 — deploy/README §2g-1). bare IP 는 404 다 | 테스트베드에서 IP 로 열린다고 프로덕션에서도 열리는 것이 아니다 |
| 노드 방화벽 | 없음 — cp1 nft :443 펜스(`dms-testbed-cp443`)는 2026-11-16 은퇴. 워커 firewalld 꺼짐 | 사이트 방화벽(deploy/README §0-3), RHEL firewalld 기본 zone 주의 | 방화벽 규칙의 실수는 여기서 드러나지 않는다. Traefik hostPort 는 CRI-O DNAT(PREROUTING → FORWARD)라 노드 :443 을 SE 대역으로 좁히는 INPUT 규칙은 그 트래픽을 보지 못한다(testbed `docs/ARCHITECTURE.md` §15.2) — 원 IP 위조 방지는 trustedIPs 가 한다 |
| SELinux | 워커 permissive(Rocky 9.6) | permissive 전제(deploy/README §2h S2b — enforcing 실측: 스토리지 전부 Missing) | 같다. enforcing 근거는 이 테스트베드의 T-SE 실측이다 |
| 노드 OS | cp1 Ubuntu 24.04 + 워커 Rocky 9.6 혼합 | 사이트 RHEL 계열 | 패키지 경로가 다르다(아래 skopeo 메모) |
| 노드 크기 | 워커 2 vCPU/3GiB, cp1 2 vCPU/4GiB | 설계 가정 8 vCPU/32GB 예 | requests 축소(§3), 잡이 노드당 1코어(§6-3). cp1 2.5GiB 는 EG·GW API CRD 뒤 apiserver RSS ≈1GB 로 MemoryPressure → 4GiB(T8 — 2026-11-16 그 CRD 들을 걷은 뒤에도 4GiB 그대로) |
| 실측 수치 | NFS 공용 스토리지·VM 3대(fsync 비교는 둘 다 같은 호스트 디스크 — fdatasync 328 대 319 ops/s) | 사이트 스토리지·네트워크 | deploy/README 의 「테스트베드 실측」 수치(장애조치 시간·롤링 공백·fsync)는 프로덕션을 대표하지 않는다 |
| Volcano | testbed IaC 설치(v1.15.0) — 감지 **C**, 설치기가 건드리지 않음 | 클러스터 운영자가 선설치 | `images.lock` 에 Volcano 줄은 없다. 생기면 설치기는 C 일 때 S5 push·대조에서 뺀다(`install.sh` `lock_skips`); 손 `images-lock.py compare-registry` 는 `--skip` 을 직접 준다 |
| 메일 | stub | knox_relay 필수(deploy/README §6-2) | 가입 인증 메일이 신원 증명이 아니다 — 테스트베드에서만 허용 |
| k8s 연동 대상 | 자기 자신(dmsk8s) + 호스트 k3s(dmsk8s2 — 다른 apiserver 를 호스트 주소로, §5-3) | 다른 클러스터일 수 있다 | 호출 노드 → 대상 apiserver 의 방화벽·DNS 경로가 시험되지 않는다(dmsk8s2 도 같은 호스트망의 IP 주소로 부른다) |
| 빌드 소스 | 호스트 체크아웃의 읽기 전용 NFS | 빌드 노드의 로컬 체크아웃 | 「push 해야 빌드된다」(§6-1)는 테스트베드 사정이다 |
| 인터넷 | libvirt NAT 직결(프록시 없음) | 사내 프록시·사내 CA | 빌드 프록시·사내 CA 경로는 따로 시험해야 한다(deploy/README §8) |
| 포탈 | 한 호스트에 둘(§6-5) | 사이트마다 FQDN | 쿠키 충돌은 테스트베드 사정이다 |
| pkg-01 잔재 | `/etc/hosts`·CoreDNS 의 pkg-01 줄(testbed 공용 규칙) | — | 무해 — 이 클러스터는 pkg-01 레지스트리를 조회하지 않는다 |

**Ubuntu 24.04(k8s-cp1)의 skopeo 메모**(S0-5 반입용 — deploy/README §2e-4 2)): `apt-get install -y --no-install-recommends skopeo`(1.13.3)는
`golang-github-containers-common`(`/etc/containers/policy.json`, CRI-O 가 읽는 drop-in `/etc/containers/registries.conf.d/shortnames.conf`,
`/usr/share/containers/{containers.conf,seccomp.json}`)·`golang-github-containers-image`(`/etc/containers/registries.conf`, 전부 주석)와 필수 의존
`container-network-stack`(containernetworking-plugins `/usr/lib/cni`)을 함께 깐다(2026-11-03 cp1·w1 `apt-get install -s` 확인). CRI-O 의
`plugin_dirs` 기본값은 `/opt/cni/bin` 뿐이라 `/usr/lib/cni` 는 쓰이지 않고, 패키지 CRI-O 의 서명 정책은 `/etc/crio/policy.json` 이라 새
`/etc/containers/policy.json` 도 쓰이지 않는다(`crio config` 의 `signature_policy`). 영향 확인: 설치 전후
`sudo find /etc/containers /etc/crio /etc/cni -type f -exec sha256sum {} +`·`ls /opt/cni/bin` 대조, `sudo crio config | grep -A3 plugin_dirs`
(`crio config` diff 는 registries.conf.d 의 내용을 보이지 않는다). 프로덕션(RHEL)은 skopeo 가 AppStream 패키지다(deploy/README §0-2).

## 11. 옛 deploy/README 절 대응

2026-11-06 재편으로 deploy/README 의 테스트베드 절은 이 문서로 옮겼다. 옛 번호(HEAD 5bec2e69 기준)가 지금 어디인지. 프로덕션 절의 옛 번호는
deploy/README 부록 Z.

| 옛 deploy/README | 지금 |
|---|---|
| 머리 「테스트베드는 둘이다」·두 테스트베드 비교 표·「Testbed facts」(ssc) 표 | §1·§2(ssc 값은 dms-ssc 체크아웃) |
| §0 Registry setup(ssc `registry-setup.sh`) | 삭제(ssc 전용). dmsk8s 노드 신뢰 = testbed crio 롤(`50-dms-registry.conf` — §7-4) |
| §2 의 CephFS 마운트·플레이북·손 레이아웃·e2e 픽스처 | §4-3·§5-4(일반 규칙은 deploy/README §2) |
| §2b-3 의 테스트베드 모드(`/cephfs/dms` 770·`/dms_shared/dms` 750) | §3 「공용 디렉터리 모드」·§7 |
| §2b 「재기동·재프로비저닝 영속성」의 테스트베드 복구 순서 | §6-6 + testbed `README.md` 「After a host reboot」·`docs/ARCHITECTURE.md` §17 |
| §2d-5 k8s 테스트베드 — 지금 배치·T3 전환 명령·음성 대조·되돌림 | §2·§3·§4-1·§7-1(전환·음성 대조 기록은 CHANGELOG ③) |
| §2e-6 k8s 테스트베드 — T4·앱 전환 | §2·§3·§4-2·§7-4(CHANGELOG ④) |
| §2f-8 k8s 테스트베드 — T6·T6'·T7·용량·걷어내기 | §3·§7-2(CHANGELOG ⑤) |
| §2g-7 k8s 테스트베드 — 호스트 edge·스위치·T8·컷오버 | §2·§4-8·§7-3·§10 + testbed `README.md` 「Portal edge」·`docs/ARCHITECTURE.md` §15.5·§19.5(CHANGELOG ⑥·「테스트베드 정식 구축」). 2026-11-16 Traefik 교체는 §19.7 |
| §3·§3b 의 테스트베드 명령(raw apply·손 시크릿) | §4-5(시크릿은 설치기 S2) · 규칙은 deploy/README §3·§3b |
| §4·5 오버레이 적용 | §6-2 |
| §6 시드의 테스트베드 값 | §5 |
| §7 시나리오 | §8(다시 씀) |
| §8 빌드 소스 클러스터 표의 dmsk8s 행(틀린 경로) | §2 |
| §9 의 testbed-k8s 오버레이 bump | §6-1 |
| §10(ssc 입구)의 TLS CA·외부 `:8443` | §2(`<사내CA>`·`<VIP-A>`) + testbed `docs/ARCHITECTURE.md` §15.4 |
| §11 「테스트베드 검증(사내 릴레이 없음)」 | §9 |
| §13 머리(LDAP 없음·k8s 신원 모드 권한 표) | §1 · 권한 표는 deploy/README §10-1 |
| §13-1 인프라 전제 | 머리 표·§2·§10(확인 명령은 §9) |
| §13-2 레지스트리 시드 | §4-2·§4-6·§4-7 |
| §13-3 DB · §13-4 공유 FS | §2·§4-3·§7-2 |
| §13-5 네임스페이스·시크릿(4키)·TLS | §4-5 |
| §13-6 오버레이 적용·LDAP 없음 게이트 | §6-2·§9 |
| §13-6b 입구(Gateway) | §4-8 |
| §13-7 첫 관리자·시드·컨트롤 상태·쿠키 | §4-9·§5·§6-5 |
| §13-8 빌드 → 릴리스 → 태그 bump·ConfigMap restart | §6-1·§6-2 |
| §13-9 k8s 연동 | deploy/README §6-5(테스트베드 값은 §5-3) |

**testbed 저장소의 낡은 서술 — 후속**(이 저장소에서는 고칠 수 없다 — BACKLOG 의 testbed 저장소 후속 항목):
- `README.md` 「Then the DMS application」: 옛 DMS README §1·§2·§3/§3b·§4/§5·§6·§8-0·§10-7·§13-2 를 가리킨다 — dmsk8s 의 순서는 이 문서 §4 다.
  그 목록 3 의 dmsk8s DB URL(`postgresql://dmsapp_k8s:…@10.10.10.30:5432/dmsdb_k8s`)은 T7 전 값이다(지금 시크릿은 설치기 S2 가 CNPG URL 로 만든다).
  목록 2 의 「Shared-FS layout(DMS README §2)」는 dmsk8s 에서 데이터 root·픽스처만이다(DMS 상태는 `/dms_shared` S0 — §4-1).
- `docs/ARCHITECTURE.md` §17 5단계: 「아래 절 번호는 dms-ssc 판 기준」이라면서 dms-k8s 판 번호(§2e-4·§2g)를 섞어 쓴다(옛 §13-6b 는 2026-11-16
  판에서 §2g 「입구(Traefik)」 로 바뀌었다). §19.6 의 §2e-6 참조는 이 문서 §7-4 로.
- (해소) `group_vars/dmsk8s.yml` 의 「§2g-7」 주석은 2026-11-16 Traefik 교체 판에서 없어졌다.
- 같은 주제의 일반 절(§1·§2e-4·§2e-5·§10-7)을 가리키는 참조는 번호가 유지돼 그대로 맞다. 옛 §2g-2·§2g-3·§2g-4(Envoy 입구)는 2026-11-16 입구
  교체로 §2g 「입구(Traefik)」 아래 다시 썼다(하위 번호는 주제가 바뀌었다 — 옛 하위 번호의 대응은 deploy/README 부록 Z). testbed 저장소는 이제 하위 번호 없이 §2g 만 가리킨다.
