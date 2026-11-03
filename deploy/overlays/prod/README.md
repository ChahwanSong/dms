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

```sh
# 1) 선행 점검(읽기 전용). Volcano/MetalLB/ingress-nginx·PSA·노드 라벨 확인.
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

# 4) 시크릿·TLS (out-of-band — git 밖, 오버레이가 만들지 않는다). 네임스페이스 dms 는 3)에서 만들었다(없으면 not found)
kubectl -n dms create secret generic dms-secrets \
  --from-literal=DMS_DATABASE_URL='postgresql://USER:PW@HOST:5432/DB' \
  --from-literal=DMS_SHARED_TOKEN="$(openssl rand -hex 32)" \
  --from-literal=DMS_ADMIN_TOKEN="$(openssl rand -hex 32)" \
  --from-literal=DMS_SESSION_SECRET="$(openssl rand -hex 32)"
kubectl -n dms create secret tls dms-portal-tls --cert=tls.crt --key=tls.key
#   (시크릿은 4키 — LDAP 바인드 비밀번호 키는 없다. TLS SAN 에 PORTAL_DOMAIN + PORTAL_VIP 둘 다.)

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
전체 PV·직접 경로 권한) · `PORTAL_DOMAIN` · `PORTAL_VIP`. 옛 values.env 의 `LDAP_*`·`IDENTITY_SUPPLEMENTARY_GROUPS` 는
무시된다(렌더가 한 줄 알린다).

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
넣어 `deploy/.rendered/prod/`(git 밖 — `app/`·`platform/priorityclass/`·`platform/registry/`·`render.env`)를 만들고, `install.sh` 가 `app/` 을
`kubectl apply -k` 한다(플랫폼 층은 SSA — `deploy/README.md` §2e). 렌더러는 in-cluster 면 다섯 곳의 레지스트리 주소가 같은지, 오버레이의
아티팩트 base 가 `SHARED_DIR` 에서 나온 값인지 다시 보고 어긋나면 FAIL 한다(실패하면 결과 디렉터리를 바꾸지 않는다). 오버레이 템플릿
자체(kustomization·patch-*)는 손대지 않는다. 값 하나로 안 되는 건 렌더 후 `deploy/.rendered/prod/app/patch-config.yaml` 에서 직접
편집하면 된다(다시 렌더하면 덮인다).

## 선행조건 (오버레이 적용 전에 클러스터에 있어야 함)

| 항목 | 확인 |
|---|---|
| **Kubernetes** 1.3x / **CNI** 임의 | preflight §0 |
| **Volcano** (잡 gang 스케줄러) | `kubectl get crd jobs.batch.volcano.sh` |
| **MetalLB(L2)** | `kubectl -n metallb-system get pods` — 풀·광고는 오버레이가 적용 |
| **ingress-nginx** | IngressClass `nginx` + 컨트롤러에 `--default-ssl-certificate=dms/dms-portal-tls` |
| **PSA** | `dms` 네임스페이스 `enforce: privileged`(00-namespace.yaml) — 사내 정책이 허용해야 잡 파드가 뜸 |
| **PostgreSQL** | 도달 가능 + DB/계정 생성 |
| **공용 디렉터리** | 모든 마스터·워커의 같은 경로 `SHARED_DIR` 에 마운트 + S0 `sh deploy/shared-dir-init.sh <SHARED_DIR>`(한 노드, root — `dms` 0750·`dms/artifacts` 0755 등과 센티널), 각 스토리지 `managed_root` 사전 mkdir. 확인: `deploy/shared-storage-probe.py`(in-cluster 면 3단계 S6 뒤 — 프로브 파드가 dms 이미지를 그 레지스트리에서 받는다) |
| **클러스터 내 레지스트리**(in-cluster) | 모든 노드 CRI-O 신뢰 `/etc/containers/registries.conf.d/50-dms-registry.conf`(S0-4) · 마스터 전부 레지스트리 이미지 반입 + `/etc/crio/crio.conf.d/99-dms-pinned.conf`(S0-5) · S4 적용 · S5 push · S6 pull — `deploy/README.md` §2e. install.sh 는 Service `dms-registry`(clusterIP == `REGISTRY_CLUSTER_IP`)가 Ready 가 아니거나 dms·dms-agent·dms-mpifileutils 태그가 그 레지스트리에 없으면 멈춘다 |
| **공인 존 노드 라벨** | `kubectl label node -l <셀렉터> network-zone=public --overwrite` |

세부(공인 존 제약·페일오버·인증서 재발급·HTTPS 원리)는 `deploy/README.md §10`.
api/controller 의 hostPath 는 `SHARED_DIR` 에서 렌더된다(base 는 자리표시자 — 손으로 패치하지 않는다).
