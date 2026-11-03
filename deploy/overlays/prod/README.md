# DMS 프로덕션 설치 (한 화면 체크리스트)

테스트베드 매니페스트(`deploy/k8s/*`)를 base 로 두고 **사이트별 값만** 여기서
덮는다. 아래 6단계로 설치한다 — 스크립트가 실패를 **한 줄 사유**로 알린다(파드 로그를 뒤질 일이 없다).
단, `REGISTRY_MODE=in-cluster`(기본)면 3단계가 한 명령이 아니다: 노드 준비·S4~S6 은 아직 `deploy/README.md` §2e 의 손 명령이고
(설치기 연결은 묶음 ⑦), S7 프로브는 그 뒤에 돈다(2026-11-03 ④).

**dms-k8s 브랜치: k8s 신원 모드 전용(2026-10-26 LDAP 코드 삭제).** base 에 LDAP 키·nslcd 가 없고 에이전트는 `dms agent`
로 뜬다(예전의 LDAP null 패치·`patch-agent.yaml` 은 지웠다). 모든 단건 잡이 root 이고 권한은 대상 클러스터의 RoleBinding 맵핑
(`<id>-clusterrole-admin|edit` → 그 namespace 의 모든 PVC)으로 판정한다. 사용자 연산은 sync·scan·rm.
대상 클러스터(API 서버·읽기 전용 토큰)·호출 노드·StorageClass 매핑은 설치 뒤 포탈 「k8s 연동」에서 등록한다
(`deploy/README.md` 런북). 렌더 계약은 `tests/test_prod_k8s_overlay.py`.

**입구는 Envoy Gateway 다(2026-11-03 ⑥, 설계 v6 #13).** prod 는 MetalLB·ingress-nginx 를 쓰지 않는다 — 이 오버레이가 공용 base 의
Ingress·MetalLB 풀을 지우고(`patch-remove-ingress.yaml`), 포탈은 Avi VS-443(L4, PROXY v2) → 마스터 ×3 :443 의 Envoy(렌더러가 내는
`deploy/.rendered/prod/platform/gateway`)로 들어온다(3c 단계, `deploy/README.md` §2g). **아직 install.sh·preflight 는 옛 입구를 전제한다** —
install.sh 1단계가 MetalLB·IngressClass nginx 가 없으면 멈추고 6단계가 ingress-nginx Service 에 어노테이션을 단다(묶음 ⑦ 이 지운다). 그래서
⑦ 출하 전에는 prod 에 install.sh 를 쓰지 않는다(설계 12 위험 19) — 아래 5단계는 ⑦ 뒤의 모양이다.

```sh
# 1) 선행 점검(읽기 전용). Volcano·PSA·노드 확인 — 스크립트의 MetalLB/ingress-nginx 항목은 ⑥ 부터 해당 없다(⑦ 이 지운다).
PORTAL_VIP=<서비스 VIP> sh deploy/preflight-cluster.sh

# 2) 값 채우기 — 파일 하나. (비밀 아님. 비밀은 4단계.)
cp deploy/overlays/prod/values.env.example deploy/overlays/prod/values.env
$EDITOR deploy/overlays/prod/values.env          # REPLACE_* 를 실값으로

# 3) 네임스페이스 + 레지스트리·이미지 (한 번만. 설치와 분리 — 매 설치마다 빌드하지 않는다)
kubectl apply -f deploy/k8s/00-namespace.yaml   # 설계 S2 — 이미지 없는 유일한 raw apply(멱등). 3)의 시크릿·레지스트리와 4)의 시크릿이 이 ns 에 산다
#    REGISTRY_MODE=in-cluster(기본, 2026-11-03 ④): 노드 준비(S0-4 신뢰 drop-in · S0-5 마스터에 레지스트리 이미지 반입·고정) →
#    sh deploy/render.sh prod --context <ctx> → S4(PriorityClass·http 시크릿·레지스트리 SSA) → S5(인터넷 노드가 deploy/images.lock 과
#    dms · dms-agent · dms-mpifileutils 를 레지스트리 ClusterIP 로 push) → S6(모든 노드 crictl pull) → S7 공용 스토리지 프로브
#    (python3 deploy/shared-storage-probe.py --context <ctx> --shared-dir <SHARED_DIR> --image $(sed -n 's/^REGISTRY=//p' deploy/.rendered/prod/render.env)/dms:<DMS_TAG>
#    — in-cluster 면 values 의 REGISTRY 는 비어 있고 렌더러가 render.env 에 채운다; 레지스트리와 dms 이미지가 S4·S5 뒤에야 생긴다).
#    명령은 deploy/README.md §2e(설치기 자동화는 묶음 ⑦ — install.sh 는 in-cluster 면 push 를 건너뛰고, 레지스트리 Service·Ready·
#    dms 3종 태그가 없으면 apply 전에 멈춘다).
#    REGISTRY_MODE=external: 사이트 레지스트리 — install.sh 1b 가 이 호스트의 podman/docker 에서 REGISTRY 로 push 한다
#    (실패·로컬 이미지 없음은 WARN). DMS 는 평문 HTTP 레지스트리만 다룬다(REGISTRY_TLS_VERIFY=false, 노드 insecure 신뢰).

# 3b) DB — CloudNativePG(2026-11-03 ⑤, 설계 v6 6·8-1 S2/S9/S10). DMS 는 DB 가 Ready 된 뒤에 적용한다(5단계 — migrate 는 backoffLimit 3).
#    명령은 deploy/README.md §2f(설치기 자동화는 묶음 ⑦): S2 시크릿(python3 deploy/pg-secrets.py --context <ctx> ensure-app → init-secrets —
#    값을 출력하지 않는다) → S9 CNPG 오퍼레이터(이미 있으면 다시 설치하지 않는다 — 감지; 첫 설치는 ns cnpg-system 을 먼저 만든 뒤 server
#    dry-run) SSA → Cluster CR server dry-run(9443) → S10 값 대조(python3 deploy/pg-path-probe.py … --mode pv-check — 적용 전: PV 용량·SC 는 SSA 가
#    조용히 바꾼다) → db-volumes(SC + PV ×5) → 사전 검사(… --mode precheck) → db-cluster → 900초 폴링(readyInstances·dms-pg-rw·
#    primary Ready) → 경로 대조(--mode contrast) → db-backup + 첫 덤프. 공용 스토리지 밖 사본(덤프·시크릿 암호화본)의 보관처를 정한다(§2f-5).
#    REGISTRY_MODE=external 이면 S9 앞에 둘을 더 한다(in-cluster 는 3단계 S4·S5 가 했다): PriorityClass dms-addon-critical 적용(없으면 오퍼레이터
#    파드가 admission 에서 거절된다)과 lock **전부**(cloudnative-pg·postgresql·gateway·envoy — §2e-4 의 lock 루프를 REGISTRY 로; 3c 의 입구 이미지도
#    여기서 같이, 2026-11-03 ⑥ 리뷰 2)를 REGISTRY 로 push(install.sh 1b 는 DMS 이미지만) — §2f-3.

# 3c) 입구 — Envoy Gateway(2026-11-03 ⑥, 설계 v6 #13·#14·4-3·8-1 S0-5/S8). 명령은 deploy/README.md §2g(설치기 자동화는 묶음 ⑦):
#    S0-5 마스터 3대 /etc/sysctl.d/90-dms-envoy.conf = net.ipv4.ip_unprivileged_port_start = 443(Envoy 는 비 root 65532 — 빠지면 그 마스터만 443 bind
#    실패, 파드는 Ready·Gateway 는 Programmed 로 조용히 남는다)
#    → 감지(Gateway API·EG 가 이미 있으면 C — CRD·컨트롤러는 건드리지 않고 platform/gateway 만) → A 면 addons/gateway-api(v1.6.1 standard) SSA →
#    addons/envoy-gateway(v1.9.3) SSA → 컨트롤러 rollout → (4단계의 dms-portal-tls 뒤) platform/gateway SSA → Gateway dms-portal Programmed·
#    **마스터마다** `ss -ltnp` 에 :443(Programmed 만으로는 bind 를 증명하지 못한다). 이미지는 lock 의 gateway·envoy — in-cluster 면 S5 가, external 이면
#    3b 의 lock 전부 push 가 REGISTRY 에 올린 것(external 이면 3b 에서 lock 전부를 push 했는지·PriorityClass 를 적용했는지 먼저 확인 — 빠지면 EG·Envoy
#    파드가 `<REGISTRY>/gateway|envoy…` ErrImagePull·admission 거절). Avi VS-443(L4 전용 프로필·PROXY v2·풀 = 마스터
#    :443·TCP 헬스)과 마스터 :443 을 SE 대역에서만 받는 방화벽은 사이트 몫 — PROXY 는 기본 필수(GATEWAY_PROXY_PROTOCOL — 옛 입구에서 옮길 때만
#    컷오버 동안 optional, §2g-4).

# 4) 시크릿·TLS (out-of-band — git 밖, 오버레이가 만들지 않는다). 네임스페이스 dms 는 3)에서 만들었다(없으면 not found)
#    dms-secrets(4키)는 3b) 의 pg-secrets.py init-secrets 가 만든다 — DMS_DATABASE_URL = CNPG URL(설계 6-3: dms-pg-rw·keepalive·
#    tcp_user_timeout=15000·target_session_attrs=read-write·sslmode=require, 비밀번호 = dms-pg-app), 토큰 셋 무작위. 확인:
python3 deploy/pg-secrets.py --context <ctx> check
kubectl -n dms create secret tls dms-portal-tls --cert=tls.crt --key=tls.key
#   (시크릿은 4키 — LDAP 바인드 비밀번호 키는 없다. TLS SAN 에 PORTAL_DOMAIN + PORTAL_VIP(Avi VIP-A) 둘 다. 이 Secret 을 Gateway 리스너가 쓴다 —
#    이름을 바꾸려면 values.env 의 PORTAL_TLS_SECRET, 단 install.sh 의 존재 게이트는 아직 dms-portal-tls 고정(⑦).)

# 5) 설치 — 먼저 dry-run(변경 없음)으로 게이트+렌더+서버검증, 통과하면 실제 설치
sh deploy/install.sh --dry-run
sh deploy/install.sh

# 6) 검증 — PASS/FAIL 한 줄씩
PORTAL_DOMAIN=<도메인> CACERT=<사내CA> sh deploy/verify.sh
```

`install.sh` 가 apply 전에 막아주는 것: 애드온 미설치, `dms-secrets`/`dms-portal-tls`
부재, 시크릿 자리표시자(CHANGE_ME) 잔존, `values.env` 미치환·레지스트리 값 형식(렌더러), in-cluster 레지스트리 부재·주소 불일치·NotReady·
dms 3종 태그 없음(S5 미완 — port-forward 로 매니페스트 확인, 2026-11-03 ④). 전부 **한 줄 사유**로
중단하므로 CrashLoop→로그 트롤링 루프가 없다.

---

## values.env 값

`deploy/overlays/prod/values.env.example` 의 주석에 각 값의 뜻·예시가 있다.
요약: 레지스트리(아래) · `DMS_TAG`/`DMS_AGENT_TAG`/`MFU_TAG`(push 한 태그) · `SHARED_DIR`
(공용 디렉터리 절대 경로 — 아래) · `EMAIL_DOMAIN`(계정 아이디 = k8s 맵핑 ID 를 인증 메일이 보증) · `LOCAL_ADMIN`(특권 목록 —
전체 PV·직접 경로 권한) · `PORTAL_DOMAIN`(포탈 FQDN) · `PORTAL_VIP`(Avi VIP-A — 둘 다 2026-11-03 ⑥ 부터 매니페스트가 아니라 `render.env` 로만 간다:
인증서 SAN·S12 `curl --resolve` 의 기준) · (선택) 입구 `GATEWAY_PROXY_PROTOCOL`·`PORTAL_TLS_SECRET`·`GATEWAY_ENVOY_*_REQUEST`(`deploy/README.md` §2g-1).
옛 values.env 의 `LDAP_*`·`IDENTITY_SUPPLEMENTARY_GROUPS` 는 무시된다(렌더가 한 줄 알린다).

**레지스트리(2026-11-03 ④, 설계 v6 7)**: `REGISTRY_MODE=in-cluster`(기본) 면 DMS 가 control-plane 노드에 Distribution v3.1.2 하나를
띄운다(`deploy/platform/registry`, 저장소 `<SHARED_DIR>/dms/registry`). `REGISTRY_CLUSTER_IP`(Service 고정 ClusterIP — ServiceCIDR 안·
미사용·하단 정적 대역; 렌더러는 형식만 보므로 values.env.example 주석의 명령으로 손으로 확인 — S1 자동 검사는 ⑦)·`REGISTRY_PORT`(5000)로 주소가 정해지고, `REGISTRY` 는 비우면 그 주소로 채워진다(채우면 같아야 한다) — 이
주소가 이미지 newName·`DMS_JOB_IMAGE`·`DMS_BUILD_REGISTRY`·`DMS_BUILD_BUILDER_IMAGE` 와 DB 의 잡 이미지에 박히고 노드 신뢰 drop-in·고정
이미지 이름에도 들어가므로 **한 번 정하면 바꾸지 않는다**. `SHARED_DIR_ID` 는 S0 가 출력한 `.dms-shared-id` 값이다 — 레지스트리 init 이
저장소의 센티널과 대조해 없거나 다르면 기동하지 않는다(마운트 빠진 마스터의 로컬 디스크에 쓰지 않게). `REGISTRY_TLS_VERIFY=false`
(평문 HTTP 만 — 포탈 태그 조회·삭제가 `http://` 고정). `REGISTRY_MODE=external` 이면 `REGISTRY` 에 사이트 레지스트리 `host:port`(경로
접두 불가)를 쓴다(줄이 없으면 external — 옛 values.env). 절차는 `deploy/README.md` §2e.

**`SHARED_DIR`(2026-11-03, 옛 `SHARED_FS` 대체)**: 사이트가 모든 마스터·워커의 **같은 호스트 경로**에 `/etc/fstab` 으로 미리
마운트한 공용 스토리지(종류 무관)의 마운트포인트 또는 그 아래 디렉터리(절대 경로, `^/[A-Za-z0-9._/-]+$`, `/` 아님, 정규화).
DMS 는 마운트·프로비저닝하지 않고 `<SHARED_DIR>/dms/` 아래만 쓴다. 아티팩트 base·allowlist·api/controller hostPath 가 모두
`<SHARED_DIR>/dms/artifacts` 하나다(오버레이 replacements 가 `DMS_ARTIFACT_BASE_URI` 에서 낸다). 모든 hostPath 는 `type: Directory`
라 마운트가 빠진 노드에서는 파드가 뜨지 않는다(빈 로컬 디렉터리에 조용히 쓰지 않는다). 옛 values.env 에 `SHARED_FS=cephfs` 가
남아 있으면 render 가 FAIL 하고 `SHARED_DIR=/cephfs` 로 바꾸라고 알린다(같은 env base). 그 값을 쓰기 전에 확인한다:
(1) 포탈 「아티팩트 경로」가 **DB 설정**이고 값이 `file://<SHARED_DIR>/dms/artifacts` 가 아니면 새 배치에서 그 경로는 api·controller
에 보이지 않는다(옛 allowlist 는 공용 디렉터리 전체였다) — 산출물을 옮기고 base 를 다시 저장(force)한다. (2) 등록 스토리지의
`mount_path`·`managed_root` 가 `<SHARED_DIR>/dms` 를 덮으면(옛 사이트의 `mount_path=/cephfs` 처럼) 그 스토리지 잡에 DMS 상태가
보인다 — SHARED_DIR 는 그 스토리지 밖의 **별도 마운트포인트**로 고른다(같은 파일시스템의 하위 디렉터리로는 못 피한다). 적용 뒤
「아티팩트 경로」 화면이 금지 경로(DMS 상태 디렉터리)와 겹치는 기존 스토리지를 경고로 보인다. (3) 옛 배치(hostPath `/cephfs`)
위에 이 렌더를 적용하면 `guard-images.sh` 가 hostPath 변경으로 **exit 5** 거부한다 — 노드 마운트·S0·프로브·산출물 복사(전환
런북, `deploy/README.md`)를 마친 뒤 `ALLOW_HOSTPATH_CHANGE=1` 로 적용한다. `<SHARED_DIR>/dms` 와 그 상위·하위는
DMS 스토리지로 등록할 수 없다(포탈 422 `storage_overlaps_dms_state`) — 데이터 스토리지와 같은 파일시스템이면 SHARED_DIR 를 그
스토리지의 `mount_path` 밖에 둔다. 트리·센티널(S0)은 한 노드에서 root 로 `sh deploy/shared-dir-init.sh <SHARED_DIR>` — 마운트를
확인(포함 마운트가 `/` 면 거부)한 뒤 `dms/{artifacts,pgdata/dms-pgdata-01..05,pgdata/.retired,backup/pg,registry}` 와
`.dms-shared-id` 등을 만들고, SHARED_DIR·상위는 만들지 않으며, 이미 있으면 검사만 한다(종료 0 PASS · 1 FAIL · 2 사용법 · 4 모름).
동작 확인(교차 노드 가시성·root 소유 보존·숫자 uid/gid·POSIX·fsync, S0 뒤 — in-cluster 면 dms 이미지가 레지스트리에 생기는 3단계 S6 뒤):
`python3 deploy/shared-storage-probe.py --context <ctx> --shared-dir <SHARED_DIR> --image <REGISTRY>/dms:<DMS_TAG>`
(`<REGISTRY>` 는 in-cluster 면 `deploy/.rendered/prod/render.env` 의 `REGISTRY=` — values 에서는 비어 있다.
종료 0 PASS · 3 WARN(G3 만, 설치 계속) · 1 FAIL(센티널 없음 = S0 미실행 포함) · 4 모름 · 2 사용법).

렌더러 `sh deploy/render.sh prod --context <ctx>`(태그가 `live` 면 그 클러스터에서 읽는다; 옛 `sh deploy/overlays/prod/render.sh` 는 같은 일의 호환 진입점)가 이 값을 오버레이 템플릿과 플랫폼 층에
넣어 `deploy/.rendered/prod/`(git 밖 — `app/`·`platform/priorityclass/`·`platform/registry/`·`addons/cnpg/`·`platform/db-*/`·`addons/gateway-api/`·
`addons/envoy-gateway/`·`platform/gateway/`·`render.env`)를 만들고, `install.sh` 가 `app/` 을
`kubectl apply -k` 한다(플랫폼 층은 SSA — `deploy/README.md` §2e). 렌더러는 in-cluster 면 다섯 곳의 레지스트리 주소가 같은지, 오버레이의
아티팩트 base 가 `SHARED_DIR` 에서 나온 값인지 다시 보고 어긋나면 FAIL 한다(실패하면 결과 디렉터리를 바꾸지 않는다). 오버레이 템플릿
자체(kustomization·patch-*)는 손대지 않는다. 값 하나로 안 되는 건 렌더 후 `deploy/.rendered/prod/app/patch-config.yaml` 에서 직접
편집하면 된다(다시 렌더하면 덮인다).

## 선행조건 (오버레이 적용 전에 클러스터에 있어야 함)

| 항목 | 확인 |
|---|---|
| **Kubernetes** 1.3x / **CNI** 임의 | preflight §0 |
| **Volcano** (잡 gang 스케줄러) | `kubectl get crd jobs.batch.volcano.sh` |
| **입구(Envoy Gateway)** | MetalLB·ingress-nginx 는 **필요 없다**(2026-11-03 ⑥ — 오버레이가 Ingress·풀을 지운다). Gateway API v1.6.x CRD + EG v1.9.x(없으면 DMS 가 3c 단계에서 설치 — A, 있으면 재사용 — C), 마스터 sysctl `ip_unprivileged_port_start=443`, ns `envoy-gateway-system` PSA privileged, Avi VS-443(PROXY v2 → 마스터 :443) — `deploy/README.md` §2g. 확인: `kubectl -n dms get gateway dms-portal`(Programmed) + 마스터마다 `ss -ltnp` 에 :443(envoy) — Programmed 는 sysctl 빠진 마스터를 못 잡는다 |
| **PSA** | `dms` 네임스페이스 `enforce: privileged`(00-namespace.yaml) — 사내 정책이 허용해야 잡 파드가 뜸 |
| **PostgreSQL** | CloudNativePG Cluster `dms-pg`(3b단계 — `deploy/README.md` §2f): 마스터마다 1개, 공용 디렉터리의 정적 hostPath PV ×5(S0 가 `dms/pgdata/dms-pgdata-01..05` 를 26:26 0700 으로 만든다), `dms-secrets` 의 URL 은 `dms-pg-rw`(설계 6-3). apiserver → 워커 파드 9443(CNPG 웹훅)이 열려 있어야 한다 |
| **공용 디렉터리** | 모든 마스터·워커의 같은 경로 `SHARED_DIR` 에 마운트 + S0 `sh deploy/shared-dir-init.sh <SHARED_DIR>`(한 노드, root — `dms` 0750·`dms/artifacts` 0755 등과 센티널), 각 스토리지 `managed_root` 사전 mkdir. 확인: `deploy/shared-storage-probe.py`(in-cluster 면 3단계 S6 뒤 — 프로브 파드가 dms 이미지를 그 레지스트리에서 받는다) |
| **클러스터 내 레지스트리**(in-cluster) | 모든 노드 CRI-O 신뢰 `/etc/containers/registries.conf.d/50-dms-registry.conf`(S0-4) · 마스터 전부 레지스트리 이미지 반입 + `/etc/crio/crio.conf.d/99-dms-pinned.conf`(S0-5) · S4 적용 · S5 push · S6 pull — `deploy/README.md` §2e. install.sh 는 Service `dms-registry`(clusterIP == `REGISTRY_CLUSTER_IP`)가 Ready 가 아니거나 dms·dms-agent·dms-mpifileutils 태그가 그 레지스트리에 없으면 멈춘다 |
세부(Avi VS-443 설정·PROXY 컷오버·원 IP·「80 불가 = 정상」·업그레이드)는 `deploy/README.md §2g`(옛 MetalLB·nginx 입구의 §10 은 테스트베드용).
api/controller 의 hostPath 는 `SHARED_DIR` 에서 렌더된다(base 는 자리표시자 — 손으로 패치하지 않는다).
**dms-api·dms-controller 는 마스터에 뜬다**(2026-11-03 ⑤, 설계 #24·#25 — `patch-placement.yaml`: control-plane nodeSelector + toleration,
not-ready·unreachable `tolerationSeconds: 30`). 이 오버레이가 제어면 Deployment 에서 바꾸는 것은 이미지·아티팩트 경로·이 배치 두 필드뿐이다
(`tests/test_release_manifest_contract.py`). 마스터에도 `SHARED_DIR` 마운트·레지스트리 신뢰가 있어야 한다(G1·S0-4). 마스터를 내릴 때의 규칙은
`deploy/README.md` §2f-7(VM 전원 확인 전 강제 삭제·out-of-service taint·Node 삭제 금지).
