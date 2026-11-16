# DMS 배포·운영 런북 (dms-k8s — 프로덕션 기준)

이 문서는 사이트 운영자가 DMS(dms-k8s 라인)를 **프로덕션 클러스터에 설치하고 운영하는** 절차의 원천이다. 사이트 중립으로 썼다 — 값은 아래
자리표시자이고, 사이트 값은 `deploy/overlays/prod/values.env`(설치 때 만든다)에서 온다.

- **처음 설치**: 한 화면 체크리스트 `deploy/overlays/prod/README.md` 를 위에서 아래로 따라가면서, 단계마다 가리키는 이 문서의 절을 연다. 전체 순서·
  호스트·성공 판정은 §0-5 에 있다.
- **설치 뒤 운영**: §0-7 의 지도에서 찾는다(설정 변경 §3-4 · 업그레이드 §4 · 롤백·복구 §5 · 일상 점검·문제 해결 §14).
- **현재 동작의 진실은 코드와 `docs/ARCHITECTURE.md`** 다. 이 문서의 근거 괄호(`파일 심볼`)는 그 사실을 코드에서 확인한 곳이다. 코드로 확인하지 못한
  사실은 「미검증」이라고 적었다.
- **k8s 테스트베드**(dmsk8s)의 값·순서·되돌림은 `deploy/testbed/README.md`, 그 인프라(VM·NFS·호스트 edge)는 testbed IaC 저장소가 맡는다.
  이 문서에서 테스트베드 값은 `> 예(테스트베드): …` 인용 줄이나 `(예: 테스트베드 …)` 괄호에만 둔다. 테스트베드에서 잰 수치는
  「테스트베드 실측」 표시를 붙인 근거다 — NFS·VM 환경이라 프로덕션 값은 다를 수 있다.

**자리표시자**

| 자리표시자 | 뜻 | 원천 |
|---|---|---|
| `<ctx>`(§2d~§2h 의 코드 블록에서는 `C`) | 대상 클러스터의 kubeconfig 컨텍스트 이름 | 운영자 |
| `<REGISTRY>` = `<IP>:<PORT>`(§2e 의 코드 블록에서는 `IP:PORT`) | 클러스터 내 레지스트리 주소(`REGISTRY_CLUSTER_IP:REGISTRY_PORT`). external 이면 사이트 레지스트리 `host:port` | values.env → `deploy/.rendered/prod/render.env` 의 `REGISTRY=` |
| `<SHARED_DIR>` | 공용 디렉터리 절대 경로 | values.env `SHARED_DIR` |
| `<FQDN>` / `<VIP-A>` | 포탈 주소 / Avi VS-443 의 VIP | values.env `PORTAL_DOMAIN` / `PORTAL_VIP` |
| `<LOCAL_ADMIN>` / `<EMAIL_DOMAIN>` | 첫 관리자 이름 / 계정 메일 도메인 | values.env `LOCAL_ADMIN` / `EMAIL_DOMAIN` |
| `<DMS_TAG>` | DMS 이미지 3종(`dms`·`dms-agent`·`dms-mpifileutils`)의 태그 — 첫 설치는 셋을 같게 | values.env `DMS_TAG`·`DMS_AGENT_TAG`·`MFU_TAG` |
| `<PORTAL_TLS_SECRET>` | 포탈 인증서 Secret 이름(기본 `dms-portal-tls`) | values.env `PORTAL_TLS_SECRET` |
| `<빌드 노드>` | 인터넷 노드 = 설치 뒤 포탈 빌드 노드(NoSchedule taint 없는 워커) | 운영자 |
| `<사내CA>` | 포탈 서버 인증서를 발급한 CA 파일 | 사이트 PKI |
| `<마스터>` | control-plane 노드(3대) | 사이트 |

**목차**

| § | 내용 |
|---|---|
| 0 | 프로덕션 배포 한눈에 — 정할 값·요건·네트워크·승인·전체 순서·책임 |
| 1 | DMS 이미지 — 최초(부트스트랩)·비상 빌드 |
| 2 · 2b | 스토리지 마운트 전제 · 제어면 root 전제 |
| 2d · 2e · 2f · 2g | 공용 디렉터리 · 클러스터 내 레지스트리 · DB(CNPG) · 입구(클러스터의 Traefik) |
| 2h | 설치기(install.sh) — 단계 S1~S13 |
| 3 · 3b | 적용 규칙·설정 변경 · 시크릿·인증서·토큰 |
| 4 · 5 | 업그레이드 · 롤백·복구 |
| 6 · 7 | 설치 뒤 설정(사용자에게 열기 전) · 스모크 시험 |
| 8 · 9 | 포탈 빌드 · 포탈 릴리스 |
| 10 · 11 | 계정·권한·인증 · 인증 메일(Knox 릴레이) |
| 12 · 12b | 작업(요청) 삭제 · 결과 파일 자동 만료 |
| 14 | 일상 운영·점검·문제 해결 |
| 부록 Z | 옛 절 번호 대응 |

절 번호는 다른 파일·설치기 출력·다른 저장소가 가리키는 앵커라 바꾸지 않는다. 빈 번호(§2c·§13)와 옮긴 절은 부록 Z 에서 찾는다.

## 0. 프로덕션 배포 한눈에 — 결정·요건·순서·책임

이 장은 클러스터를 만지기 전에 읽는다. 무엇을 정하고, 누구에게 무엇을 받고, 어떤 순서로 설치하는지를 한 곳에 모았다.
한 화면 체크리스트는 `deploy/overlays/prod/README.md` 이고, 그 단계마다 이 문서의 절로 온다.

### 0-1. 설치 전에 정할 값

| 값 | 누가 정하나 | 어디에 박히나 | 나중에 바꿀 수 있나 | 상세 |
|---|---|---|---|---|
| 포탈 FQDN `<FQDN>`·Avi VIP-A `<VIP-A>` (values `PORTAL_DOMAIN`·`PORTAL_VIP`) | 사용자 + DNS·LB 담당 | `PORTAL_DOMAIN` 은 입구 Ingress `dms-portal-traefik` 의 host·tls hosts 다(렌더가 문법을 본다 — 와일드카드·포트·IP 는 FAIL). 둘 다 `render.env` 로도 간다(인증서 SAN·S8 ingress-probe·S12 `curl --resolve` 기준). `PORTAL_VIP` 는 매니페스트에 없다 | 바꿀 수 있다: 인증서 재발급(§3b-2)·DNS → values → `--only S8,S12` | §2g-1·§3b-2 |
| 입구 = 클러스터의 Traefik(IngressClass·TLS 진입점 — values `INGRESS_CLASS`·`TRAEFIK_ENTRYPOINT`, 기본 `traefik`·`websecure`)과 LB 의 PROXY v2 | 클러스터 운영자 + 네트워크·LB 담당 | DMS 는 Traefik 을 설치·변경하지 않고 Ingress 하나만 만든다(ingressClassName·`router.entrypoints`). LB 가 PROXY v2 를 붙이면 Traefik websecure 의 `proxyProtocol.trustedIPs` = LB 출발지(SE)를 클러스터 운영자가 넣는다(RKE2 는 Rancher chartValues·HelmChartConfig). LB 풀 = Traefik 이 도는 노드 전부 :443. LB VS(§0-5 1f)를 만들기 전에 셋이 맞춘다. 옛 `GATEWAY_PROXY_PROTOCOL`·`GATEWAY_LISTEN_PORT`·`GATEWAY_ENVOY_*` 는 더 쓰지 않는다(남아 있으면 렌더가 알림 한 줄 뒤 무시) | 바꿀 수 있다: values → `--only S8`. PROXY v2 를 켜고 끄는 데는 순서가 있다(trustedIPs ↔ LB — §2g-4) | §2g-1·§2g-2·§2g-4 |
| 공용 디렉터리 `<SHARED_DIR>` (+ S0 가 내는 `SHARED_DIR_ID`) | 공용 스토리지 담당 | 아티팩트 base·allowlist·api/controller hostPath·PG PV 5개·덤프·레지스트리 저장소 | 어렵다 — 전환 런북(§2d-4) | §2d |
| 레지스트리 모드·주소(`REGISTRY_MODE`·`REGISTRY_CLUSTER_IP`·`REGISTRY_PORT`, external 이면 `REGISTRY`) | 클러스터 운영자 | 모든 노드의 CRI-O 신뢰 drop-in, 마스터 고정 이미지 이름, 이미지 `newName`, `DMS_JOB_IMAGE`·`DMS_BUILD_REGISTRY`·`DMS_BUILD_BUILDER_IMAGE`, DB `control_state.job_image` | **바꾸지 않는다**(여섯 곳이 함께 바뀌어야 한다) | §2e-1 |
| control-plane 라벨 값(values `CONTROL_PLANE_LABEL_VALUE` — 빈 값 = kubeadm 관례 `""`, RKE2 는 `true`) | 클러스터 운영자 | 레지스트리·api/controller(PG 는 `PG_PLACEMENT=control-plane` 일 때)의 nodeSelector | 바꿀 수 있다: values → 전체 설치기 재실행(파드가 다시 배치된다 — §3-4 (c)) | §0-2·§2f-1 |
| 인터넷(= 빌드) 노드 `<빌드 노드>` | 클러스터 운영자 | 이미지 반입(S0-5·S5), 포탈 빌드(컨트롤 상태 `build_node_name`). 그 노드의 프록시가 **TLS 를 가로채면**(사내 CA 로 재서명) 첫 부트스트랩 빌드(`build-and-push.sh`)는 사내 CA 를 넣지 못한다 — 미리 확인하고 §1 「실패하면」의 우회를 준비한다 | 컨트롤 상태에서 바꿀 수 있다(요건은 §0-2) | §1·§2e-4·§6-3·§8 |
| 첫 관리자 이름 `<LOCAL_ADMIN>` | 사용자 | `DMS_PRIVILEGED_REQUESTERS = root,admin,<LOCAL_ADMIN>`(prod `patch-config.yaml`) | values → `--only S11`(env 목록). 다른 관리자는 계정 화면 부여로(§10-2) | §6-1·§10-2 |
| 계정 메일 도메인 `<EMAIL_DOMAIN>` | 사용자 | `DMS_ACCOUNT_EMAIL_DOMAIN` — 인증 메일 받는 주소 `<아이디>@<EMAIL_DOMAIN>`, Kubeflow `namespaceAdmin` 바인딩 인식 조건(`k8s_rbac` 모듈 docstring) | values → `--only S11` | §6-2·§11 |
| 사내 CA 서버 인증서(SAN = `<FQDN>` + `<VIP-A>`) | 보안·PKI 담당 | Secret `<PORTAL_TLS_SECRET>`(기본 `dms-portal-tls`) — 클러스터의 Traefik 이 Ingress 의 tls 로 읽는다 | 언제든 교체(재기동 없음 — 미검증, §2g-6) | §3b-2 |
| 메일 릴레이 IP·포트·인증 키, 허용 클라이언트 = **마스터 3대 IP** | 메일 담당 | 포탈 메일 설정(DB). 발송 방식 기본값은 사이트 `patch-config.yaml` 에 고정(§6-2) | 포탈에서 언제든 | §6-2·§11 |
| kube 컨텍스트 `<ctx>`(선택: `deploy/overlays/prod/kube-context` 로 고정) | 운영자 | 모든 명령의 `--context` | — | §3-2 |
| Volcano 설치·업그레이드 담당 | 클러스터 운영자 | DMS 는 Volcano 를 설치하지 않는다(층 미동봉) | — | §0-6 |
| DMS 이미지 태그 `<DMS_TAG>`(`DMS_TAG` = `DMS_AGENT_TAG` = `MFU_TAG`) | 운영자 | 첫 설치의 이미지 태그 | 설치 뒤에는 `live` 를 권한다(§4) | §1·§4 |

- **렌더가 형식을 보지 않는 값이 셋이다** — `EMAIL_DOMAIN`·`LOCAL_ADMIN`·`PORTAL_VIP` 는 「비었나·`REPLACE_` 인가」만 본다
  (`render.sh` 의 필수 키 목록 `KEYS` 검사). 오타도 렌더를 통과한다. `LOCAL_ADMIN` 에 오타가 나면 첫 관리자가 특권 목록 밖이 되고(§6-1),
  `EMAIL_DOMAIN` 이 틀리면 인증 메일이 엉뚱한 주소로 간다. 두 번 확인한다. `PORTAL_DOMAIN` 은 2026-11-16 부터 Ingress host 라 렌더가 호스트 이름
  문법을 본다(`render.sh _host_problem` — §2g-1). 문법이 맞는 오타는 그래도 통과한다.
- 레지스트리 주소는 박기 전에 고른다. 설치 호스트에서 렌더 없이 S1 의 주소 검사만 돌린다:
  `REGISTRY_MODE=in-cluster REGISTRY_CLUSTER_IP=<IP> sh deploy/preflight-cluster.sh --context <ctx>` → `REGISTRY_CLUSTER_IP <IP> 가 ServiceCIDR … 의 하단 정적 대역`
  과 `REGISTRY_CLUSTER_IP <IP> 미사용` 두 줄이 PASS 인 주소를 쓴다(§2e-1).

### 0-2. 클러스터·노드·호스트 요건

**클러스터**
- Kubernetes **1.34~1.36**. 밖이면 S1 이 FAIL 한다(`preflight-cluster.sh` 「0. 클러스터 / 컨텍스트」). CNPG 1.30·Volcano v1.16 의 지원 범위다.
- **control-plane 3대**. 0대면 FAIL, 3대가 아니면 WARN 이다. PG 인스턴스(마스터마다 1개, required anti-affinity)·클러스터 내 레지스트리·
  dms-api·dms-controller 가 마스터에 선다(prod 렌더 — `platform/db-cluster`·`platform/registry`·`patch-placement.yaml`).
  이들은 라벨 `node-role.kubernetes.io/control-plane` 의 **값 `""`**(kubeadm 관례)로 고른다 — RKE2 처럼 `true` 를 다는 배포판이면 values.env
  `CONTROL_PLANE_LABEL_VALUE=true`(§2f-1 표; 확인 `kubectl --context <ctx> get nodes -L node-role.kubernetes.io/control-plane`). 아니면 레지스트리·PG·
  api/controller 는 Pending 이다.
- **NoSchedule taint 없는 워커 2대 이상**(권장). 다음은 워커에만 뜬다:
  - CNPG 오퍼레이터 2개(`addons/cnpg/1.30.1/patch-operator.yaml` — control-plane `DoesNotExist`, 두 파드를 다른 노드에 선호).
  - pg_dump CronJob(`platform/db/backup/cronjob.yaml`).
  - dms-agent DaemonSet. 이 DaemonSet 은 NoExecute 만 견딘다(`k8s/50-agent-daemonset.yaml` tolerations). 그래서 NoSchedule taint 가 있는 노드에는
    뜨지 않고, 잡·빌드도 에이전트가 보고한 노드에서만 돈다.
  - 워커가 1대면 그 워커를 정비하는 동안 CNPG 웹훅(failurePolicy Fail)·덤프·잡이 함께 멈춘다.
- 런타임은 **CRI-O** 다(노드 준비가 `registries.conf.d`·`crio.conf.d` drop-in 이다 — containerd 판은 없다).
- CNI 는 무엇이든 되지만 **kube-proxy 가 있어야** 한다. hostNetwork 파드와 인터넷 노드가 ClusterIP 로 레지스트리에 닿는 경로다.
- **Volcano ≥ v1.15**(K8s 1.36 이면 v1.16)를 클러스터 운영자가 먼저 설치한다. 없으면 S1 이 멈춘다(`preflight-cluster.sh` 「1. Volcano」).
- **Traefik**(포탈 입구 — 클러스터 운영자 몫. RKE2 는 `rke2-traefik` 이 기본으로 깔린다): IngressClass `<INGRESS_CLASS>`(기본 `traefik`, controller
  `traefik.io/ingress-controller`), Ingress 공급자(`--providers.kubernetesingress`), TLS 진입점(`--entryPoints.<TRAEFIK_ENTRYPOINT>.address`, 기본
  `websecure`). IngressClass 가 없거나 Traefik 이 아니면 S1 이 FAIL 한다(`preflight-cluster.sh` 「2. 입구」). **DMS 는 Traefik 을 설치·변경하지 않는다**
  — 요건 전체는 §2g-2.
- **DMS 노드** = 마스터 + NoSchedule taint 없는 워커. RHEL 계열이면 SELinux 가 enforcing 이 아니어야 한다 — permissive 또는 disabled, 재부팅 뒤에도.
  S2b 가 노드마다 잰다(prod README 「0. 사이트 준비」 2).
- kubelet 예약은 사이트 값이다. 예(설계 가정, 8 vCPU/32GB 마스터): `systemReserved: cpu=500m,memory=1Gi`, `kubeReserved: cpu=500m,memory=1Gi`,
  `evictionHard: memory.available<500Mi` → allocatable 약 7000m/29.5Gi.

**자원 예산(prod 렌더의 requests / limits — 설계 표가 아니라 매니페스트 값)**

| 어디 | 무엇 | requests | limits |
|---|---|---|---|
| 마스터마다 | PG 인스턴스(`dms-pg`) | 1 CPU / 4Gi | 메모리 4Gi(CPU 없음) |
| 마스터 1대 | dms-api(+ initContainer migrate 50m/128Mi) | 100m / 256Mi | 1 / 1Gi |
| 마스터 1대 | dms-controller(+ initContainer migrate) | 100m / 256Mi | 1 / 1Gi |
| 마스터 1대 | dms-registry(+ init 10m/16Mi) | 100m / 128Mi | 1 / 512Mi |
| 워커 | CNPG 오퍼레이터 ×2 | 100m / 100Mi | 100m / 200Mi |
| 워커(매시간) | pg_dump | 100m / 256Mi | 1 / 1Gi |
| 워커마다 | dms-agent | 25m / 64Mi | 250m / 256Mi |
| 빌드 노드 | 포탈 빌드 파드 | 250m / 128Mi(임시 저장소 10Gi) | 1000m / 1Gi(12Gi) |
| 워커 | 데이터 잡 | 노드당 `procs_per_node` 코어 + launcher 100m | — |

- 셋이 한 마스터에 몰리면 그 마스터의 DMS requests 는 약 1.3 CPU / 4.6Gi 다. 여기에 etcd·apiserver·CNI 몫이 더해진다. 입구 Traefik(클러스터
  것 — RKE2 는 모든 노드의 DaemonSet)의 몫은 이 표에 없다 — 사이트의 Traefik 설정을 본다.
- dms-api requests(100m/256Mi)는 설계 2-1 표의 250m/512Mi 와 다르다. 어느 쪽으로 할지는 BACKLOG 에 올렸다.

**설치 호스트**(클러스터 밖이어도 된다)
- kubectl(클러스터 minor ±1)·python3 ≥ 3.9(표준 라이브러리만)·curl·sha256sum·POSIX sh, 그리고 설치할 커밋의 DMS 체크아웃.
- apiserver(6443)에만 닿으면 된다. 레지스트리 확인은 port-forward, readyz 판정은 apiserver 서비스 프록시로 한다(`install.sh` S4·S5, `verify.sh`).
  VIP 경유 readyz 만 `<VIP-A>:443` 이 필요하고, 닿지 않으면 WARN 이다. S8·S12 의 ingress-probe 는 Traefik 이 도는 노드 :443 에 직접 붙는다 —
  닿지 않으면 UNKNOWN 이라 WARN 뒤 계속한다(§2g-3).
- 프록시 env 가 있으면 `NO_PROXY` 에 apiserver 주소를 넣는다(`preflight-cluster.sh` 가 확인한다).
- **kube 자격의 권한**: 설치 컨텍스트는 **cluster-admin** 이어야 한다. 설치기는 CRD·ClusterRole/ClusterRoleBinding·Mutating/ValidatingWebhookConfiguration·
  ValidatingAdmissionPolicy·PriorityClass·StorageClass·PV·Namespace 를 만들고(§0-4 표), ns `dms` 에 privileged DaemonSet(S2b)을
  띄우고, 시크릿을 만들고 값까지 읽는다(S2·S11 `secrets_gate`, S13 이 찍는 첫 관리자 명령은 관리 토큰을 읽는다). 설치기는 권한을 미리 검사하지 않는다
  (`install.sh`·`preflight-cluster.sh` 에 `auth can-i` 없음) — 모자라면 그 단계의 apply 가 `Forbidden` 으로 멈춘다. 재실행(`--only S11` 포함)도 앱 층에
  클러스터 범위 객체(Namespace·PriorityClass·Queue·ClusterRole)가 있어 같은 자격으로 돈다.

**인터넷(= 빌드) 노드** — 설치 때는 이미지 반입, 설치 뒤에는 포탈 빌드 노드다.
- 클러스터의 **NoSchedule taint 없는 워커**여야 한다. 빌드·프리플라이트 파드에 toleration 이 없고 nodeSelector `kubernetes.io/hostname` 만 쓴다
  (`build_manifests.py` 의 두 Pod 매니페스트). 컨트롤 상태는 에이전트가 보고한 노드만 받는다(`routes_control.put_control_state` — 422 `unknown_build_node`).
- 빌드 파드는 **privileged** 다(`build_manifests.py` — buildah). 사이트 정책이 허용해야 한다.
- skopeo·podman·python3·bash, 그리고 **로컬 체크아웃**(git worktree 금지 — 빌드 파드가 `.git` 밖 gitdir 를 못 따라가 SHA 가 `unknown` 이 된다, §8 1)).
- 프록시: `HTTPS_PROXY` 와 함께 `NO_PROXY=<REGISTRY 의 IP>`. 레지스트리 주소가 NO_PROXY 에 없으면 `push-images.sh --run` 이 아무것도 하지 않고 멈춘다.
- egress 목록은 §0-3.

**마스터 3대**
- skopeo·crictl(레지스트리 자기 이미지 반입 — §2e-4 2)).
- 입구용 포트·sysctl 요건은 없다(2026-11-16 입구 교체 — 옛 Envoy 의 빈 포트 :443/:19000~19003·`90-dms-envoy.conf` 는 은퇴, 이미 둔 사이트의
  정리는 §2g-8 정리 7). 노드 :443 은 클러스터 Traefik 의 hostPort 다(§2g-2).

**모든 DMS 노드 + 공용 스토리지를 마운트하는 모든 호스트**
- `fs.protected_hardlinks=1`·`fs.protected_symlinks=1`(§2b-2).
- 공용 스토리지를 같은 경로에 마운트한다(§2d — 설계 3-2 의 G1~G12).
- 데이터·PV 스토리지는 워커에 등록할 경로 그대로 마운트한다(§2·§6-5).

### 0-3. 네트워크·방화벽·시간·DNS

| 출발 → 도착 | 포트 | 왜 | 근거 |
|---|---|---|---|
| 사용자망 → `<VIP-A>` | 443/tcp | 포탈. 80 VS 는 만들지 않는다(Traefik web 진입점에는 DMS 라우터가 없어 404 다 — 리디렉트 없음) | §2g-4·§2g-5 |
| Avi SE → Traefik 이 도는 노드 전부 | 443/tcp(hostPort) | Traefik websecure. hostPort 는 CNI·CRI 가 거는 DNAT(PREROUTING → FORWARD)라 **노드의 INPUT 방화벽에 걸리지 않는다** — 출발지를 좁히려면 망 방화벽·CNI 정책에서 한다. 원 IP 위조를 막는 것은 방화벽이 아니라 Traefik `proxyProtocol.trustedIPs`(LB 출발지만 — 그 밖의 출발지의 PROXY 헤더는 버린다) | §2g-2·§2g-4 |
| 설치 호스트 → Traefik 이 도는 노드 | 443/tcp(선택) | S8·S12 ingress-probe 의 노드 직접 확인. 막혀 있으면 UNKNOWN(WARN 뒤 계속 — VIP 경유 readyz 가 다시 본다) | §2g-3 |
| 설치 호스트·운영자 → apiserver | 6443/tcp | kubectl·port-forward·서비스 프록시 | §0-2 |
| apiserver → 모든 노드 kubelet | 10250/tcp | logs·exec·port-forward | 설계 4-2 |
| apiserver → 워커 파드 | 9443/tcp | CNPG 웹훅(failurePolicy **Fail**) | prod 렌더 `addons/cnpg` |
| apiserver → Volcano 웹훅 파드 | 8443/tcp | Volcano admission(S9b 가 vcjob dry-run 으로 확인) | 설계 4-2 |
| 모든 노드 ↔ 모든 노드(마스터 포함) | CNI 하부 포트 | 예: Cilium vxlan 8472/udp·health 4240/tcp. 마스터도 워크로드를 돌린다 | 설계 4-2 |
| 파드망 안 | 5432·8000·8080·5000 | api·controller·pg_dump → PG, PG ↔ PG, 오퍼레이터 → 인스턴스, Traefik 파드(ns 는 클러스터 것 — RKE2 `kube-system`) → dms-api 8080, 노드·파드 → 레지스트리 ClusterIP. NetworkPolicy 가 있으면 허용해야 한다 | 설계 4-2·§2g-1 |
| 인터넷 노드 → 상류(프록시 경유) | 443/tcp | 반입: docker.io·quay.io·ghcr.io(`deploy/images.lock` 의 SOURCE). 빌드: 위 + npm·PyPI·Debian 미러·dl.k8s.io·github.com(dms-mpifileutils 빌드) | `images.lock`, `docker/Dockerfile.*`, §8 3) |
| 인터넷 노드 → 레지스트리 ClusterIP | `<REGISTRY>` 포트(기본 5000)/tcp | push(kube-proxy 경로) | §2e-4 |
| 인터넷 노드 → 마스터 ×3 | ssh 등 | 레지스트리 자기 이미지 파일 전달(S0-5 — 처음 한 번) | §2e-4 2) |
| dms-api(마스터) → 메일 릴레이 | 8025/tcp(http) | 인증 메일. 메일은 api 만 보낸다(`api/mailer.py`) | §6-2·§11 |
| 호출 노드(워커의 dms-agent) → 대상 클러스터 apiserver | 등록한 URL 의 포트(보통 6443)/tcp | k8s 연동 인벤토리(`agent/k8s_fetch.py fetch_inventory`) | §6-5 |
| 워커 → 데이터·PV 스토리지, 워커 ↔ 워커 | 사이트 기존 | 잡의 스토리지 접근, MPI(파드망) | §2 |

- **RHEL firewalld**: 기본 public zone 을 켠 채 두면 노드 간 CNI 흐름이 막힌다. 위 표의 노드 간 포트를 연다. 설치기·preflight 는 firewalld 를 보지 않는다(S0 사이트 몫).
  테스트베드 실측(Rocky 9·Cilium vxlan, 2026-11-03): 10250/tcp·8472/udp·4240/tcp·4244/tcp·7946/tcp+udp·30000-32767/tcp 를 열자 파드 DNS·ClusterIP·에이전트 보고가
  돌아왔다. 프로덕션의 포트는 CNI 설정에 따라 다르다.
- **서비스·파드 CIDR** 이 사내 대역(메일 릴레이·PV 백엔드·대상 apiserver·DNS)과 겹치지 않아야 한다. 겹치면 파드가 그 주소에 닿지 못한다(설계 §11 K5).
- **시간 동기(NTP)**: 모든 노드·설치 호스트·인터넷 노드·메일 릴레이. 인증서·etcd·토큰·로그 시각이 걸린다(설계 §11 X1).
  DMS 는 시간 동기를 검사하지 않는다(`preflight-cluster.sh`·`verify.sh` 에 없음) — 사이트 IaC 가 보장한다.
- **DNS**: `<FQDN>` → `<VIP-A>`(사내 DNS). 등록과 공지는 §6-8 의 확인을 마친 뒤에 한다. 그 전에는 `curl --resolve <FQDN>:443:<VIP-A>` 나 운영자 PC 의 hosts 로 쓴다.

### 0-4. 보안 승인 항목과 DMS 가 만드는 클러스터 범위 객체

**승인받을 것**(설계 §11 「S1 승인 항목」)
- ns `dms` PSA privileged, root 컨테이너(api·controller — cap 전부 drop, §2b), hostPath(artifacts·registry·backup)와 hostPath PV(pgdata ×5),
  **공용·데이터·PV 스토리지 모두 DMS 노드에 root 보존(squash 없음)** — 공용은 §2d, 데이터·PV 는 §2 「root 접근」.
- **dms-agent DaemonSet**(NoSchedule 없는 워커마다): **uid 0**(`k8s/50-agent-daemonset.yaml` 은 `runAsUser: 0` 만 준다 — capability 는 런타임 기본 그대로).
  호스트 `/` 를 `/host/root` 에 붙인다. `readOnly` 는 최상위 바인드(호스트 루트 fs)에만 걸리고, 전파된 하위 마운트(데이터·PV 스토리지, `<SHARED_DIR>` —
  PG 데이터·덤프·레지스트리·결과 파일 포함)는 호스트 옵션 그대로 **쓰기 가능**하다(recursiveReadOnly 는 HostToContainer 와 함께 쓸 수 없다 — 그 파일의
  머리 주석). 그래서 에이전트 컨테이너는 노드의 모든 파일(kubelet 자격·다른 파드의 시크릿 볼륨 포함)을 읽을 수 있고, 공유 토큰(admin Bearer — §3b-3)을
  env 로 갖는다. 에이전트 코드는 쓰지 않지만 권한으로는 막혀 있지 않다.
- **데이터 잡 파드**(ns `dms` — 컨트롤러가 잡마다 만드는 프리플라이트 Pod, Volcano vcjob 의 launcher·worker): **uid 0**(`execution_manifests._container`·
  `build_preflight_pod`). worker 는 `SYS_CHROOT` 를 더한다(sshd·MPI — `_worker_security_context`). 그 밖의 capability 는 런타임 기본이다. 잡이 다루는
  스토리지의 `mount_path` **전체**를 쓰기 가능한 hostPath(`type: Directory`)로 붙인다(`execution_volcano.VolcanoExecutionAdapter._volumes`) — 일반 사용자의
  PV 하위 경로 요청도 같다. 그 경로 밖을 못 만지게 하는 것은 코드 봉쇄(planner·러너)다. 왜 root 인가: dms-k8s 는 LDAP 신원이 없어 모든 잡이 root 이고,
  권한은 RoleBinding 맵핑이, 결과 소유는 스토리지 기본 소유자 `--chown` 이 정한다(§10-1). 결과 파일 정리 Pod 는 uid 0 에 `DAC_OVERRIDE`·`FOWNER` 만 쓰고
  아티팩트 base hostPath 를 붙인다(`purge_runner.build_purge_pod`).
- privileged 빌드 파드, 설치기의 일회용 privileged 프로브(S2b SELinux — 끝나면 지운다)·일회용 root 프로브(S7 공용 스토리지). S8·S12 의 입구 확인
  (`ingress-probe.sh`)은 파드를 띄우지 않는다 — 설치 호스트에서 노드 :443 으로 HTTPS GET 만 한다.
- **설치·재실행에 쓰는 kube 자격 = cluster-admin**(누가 그 컨텍스트를 보관하나 — §0-2 「설치 호스트」).
- RHEL 계열 DMS 노드의 SELinux 비 enforcing.
- 클러스터 안 익명 평문 레지스트리(서비스 CIDR 이 사내망에 광고되지 않아 클러스터 안으로 한정), 인터넷 노드의 상류 이미지 반입.
- **포탈 입구는 클러스터 공용 Traefik 이다**(DMS 는 설치·변경하지 않는다 — §2g). 포탈 TLS 개인키(Secret `<PORTAL_TLS_SECRET>`, ns `dms`)를 그 Traefik
  이 읽는다 — 상류 Traefik 차트의 ClusterRole(v40.3.0 소스 확인)과 테스트베드 ClusterRole 은 `secrets` get·list·watch 를 클러스터 전역에 준다.
  사이트 Traefik 의 실제 RBAC 는 클러스터 운영자에게 확인한다. 같은 Traefik 이 다른 팀의 라우터도 서빙한다 — DMS Ingress 는 host 를 늘 두고
  진입점을 websecure 로 좁힌다(§2g-1).
- 평문 통신: 메일 릴레이(http), 클러스터 내 레지스트리(http), **클러스터 안 앱 트래픽** — Traefik → `dms-api:8080`(TLS 는 Traefik 에서 끝난다 — 세션
  쿠키가 실린다, `platform/ingress/ingress.yaml`), 모든 dms-agent → `http://dms-api:8080`(`k8s/50-agent-daemonset.yaml` `DMS_AGENT_API_URL` — 매 요청에 공유 토큰
  Bearer, k8s 연동 호출 노드에는 대상 클러스터 리더 토큰이 응답으로 온다 — `routes_agent.k8s_sync_claim`). DMS 는 파드망 암호화를 켜지 않는다 — 필요하면
  CNI 의 암호화(WireGuard·IPsec)로 사이트가 덮는다. 대상 클러스터의 장기 SA 토큰(§6-5).
- **데이터 변경·삭제의 의미**: sync·rm 은 미리보기(dry-run) 뒤 사람이 확인해야 실행된다(`domain.confirm_gated` — scan 만 확인이 없다). 단건은 요청자가
  컨펌한다(§10-1). 실행은 root 이고 **rm 은 휴지통 없이 지운다(되돌릴 수 없다)**. 일반 사용자는 맵핑된 PVC 의 하위 경로만(PV 루트 rm 은 422
  `pv_root_rm_forbidden`), 특권 운영자는 등록된 스토리지의 직접 경로까지 지운다. **배치**(특권 세션 관리자만 만든다 — `routes_batches._require_batch_privilege`)는
  자동 확인(`auto_confirm`, 기본 꺼짐)을 켤 수 있고, 켜면 자식마다 사람 확인 없이 실행된다(sync·rm — `domain.auto_confirmable`). rm 배치는 「되돌릴 수
  없음」 동의(`acknowledge_irreversible: true` — 없으면 422 `batch_rm_auto_confirm_ack_required`)가 있어야 켜지고 감사 로그에 남는다
  (`routes_batches._check_auto_confirm_enable`·`_auto_confirm_audit`). 작업 삭제(§12)·결과 만료(§12b)는 DMS 의 기록·결과 파일만 지우고 스토리지 데이터는
  건드리지 않는다.

**DMS 가 만드는 클러스터 범위 객체**(prod 렌더 전수 — CRD 는 개수만)

| 층(단계) | 객체 |
|---|---|
| app(S2·S11) | Namespace `dms`(PSA enforce·audit·warn privileged). PriorityClass `dms-build`(10, preemption Never)·`dms-low`(50)·`dms-mid`(100)·`dms-high`(200). Volcano Queue `dms-data`. ClusterRole + ClusterRoleBinding `dms-agent-readonly`(nodes get)·`dms-api-nodes-readonly`(nodes get·list)·`dms-api-queue-readonly`(queues get — `dms-data` 만) |
| platform/priorityclass(S4) | PriorityClass `dms-addon-critical`(1000 — CNPG 오퍼레이터) |
| platform/db-volumes(S10) | StorageClass `dms-pg-shared`(no-provisioner·Retain). PV `dms-pgdata-01..05`(hostPath `<SHARED_DIR>/dms/pgdata/…`). ValidatingAdmissionPolicy + Binding `dms-pg-shared-pvc-namespace`(이 클래스의 PVC 는 ns `dms` 에서만) |
| addons/cnpg(S9 — A 일 때만) | Namespace `cnpg-system`. CRD 11개(`postgresql.cnpg.io`). ClusterRole 7개(`cnpg-manager` + database·publication·subscription 의 editor·viewer) + ClusterRoleBinding `cnpg-manager-rolebinding`. Mutating·ValidatingWebhookConfiguration `cnpg-*` |

- 같은 이름의 PriorityClass `dms-low`·`dms-mid`·`dms-high`·`dms-build` 와 Queue `dms-data` 가 이미 있으면 **덮어쓴다**. preflight 가 「이미 존재 — 덮어씀」으로 알린다.
- 애드온이 이미 있고 호환이면(감지 C) 그 층은 설치하지 않는다(§2h). 위 표의 애드온 행은 DMS 가 설치할 때(A)만 생긴다.
- 입구 층 `platform/ingress`(S8)는 ns `dms` 의 Ingress `dms-portal-traefik` 하나뿐이라 이 표에 없다 — 클러스터 범위 객체를 만들지 않는다
  (Traefik·IngressClass·Traefik CRD 는 클러스터 것이다). 2026-11-16 이전 커밋이 깐 Envoy Gateway·Gateway API 객체의 정리는 §2g-8.
- 대상 클러스터(k8s 연동)에는 사이트가 읽기 전용 리더 계정(ns `dms-reader`·ClusterRole `dms-inventory-reader` 등)을 만든다 — §6-5.

### 0-5. 전체 순서

| # | 단계 | 어느 호스트 | 명령(요지) | 성공 판정 | 실패하면 / 상세 |
|---|---|---|---|---|---|
| 1a | 레지스트리 주소 고르기 | 설치 호스트 | `REGISTRY_MODE=in-cluster REGISTRY_CLUSTER_IP=<IP> sh deploy/preflight-cluster.sh --context <ctx>` | 주소 검사 줄 PASS | 다른 주소. §2e-1 |
| 1b | 공용 스토리지 마운트·S0 | 모든 노드(fstab) → 한 노드(root) | `sudo sh deploy/shared-dir-init.sh <SHARED_DIR>` → 다른 노드 `--check` | `RESULT: PASS`, 출력 `dms/.dms-shared-id = …` → values `SHARED_DIR_ID` | 사유 줄. §2d-2 |
| 1c | SELinux·sysctl | 모든 DMS 노드 | `/etc/selinux/config` permissive + `setenforce 0`, `fs.protected_*=1` | `getenforce` ≠ Enforcing, `sysctl fs.protected_hardlinks fs.protected_symlinks` = 1 | prod README 「0. 사이트 준비」 2, 이 문서 §2b-2 |
| 1d | 레지스트리 신뢰 | 모든 노드 | `/etc/containers/registries.conf.d/50-dms-registry.conf` → `systemctl reload crio` | `cat /etc/containers/registries.conf.d/50-dms-registry.conf` 가 `location="<REGISTRY>"`·`insecure=true`, `systemctl reload crio` exit 0 — 실제 pull 은 S6 pull 프로브가 판정 | §2e-2 |
| 1e | 마스터 준비 | 마스터 ×3(+ 인터넷 노드) | 레지스트리 이미지 반입·`99-dms-pinned.conf` | `crictl inspecti <REGISTRY>/registry:<태그>` 에 pinned | §2e-4 2) |
| 1f | 방화벽·NTP·DNS·Avi VS-443 | 사이트 | §0-3 표, Avi VS-443(443 전용 L4·풀 = Traefik 이 도는 노드 전부 :443·헬스 TCP 443 또는 SNI·Host = `<FQDN>` 인 HTTPS `/readyz`·유휴 ≥ 3600초). PROXY v2 는 사이트 선택 — 켜려면 1g 의 trustedIPs 가 먼저다(§2g-4 순서) | 포트 표대로 열림. readyz 헬스면 풀은 S11(dms-api) 전까지 down 이 정상(TCP 443 헬스는 Traefik 만 본다) | §0-3·§2g-4 |
| 1g | Volcano·Traefik | 클러스터 운영자 | Volcano 상류 설치(≥ v1.15). Traefik 은 이미 있는 것을 확인(RKE2 `rke2-traefik`) — LB 가 PROXY v2 를 붙이면 websecure `proxyProtocol.trustedIPs` = LB 출발지(SE)(Rancher chartValues·HelmChartConfig) | S1 「Volcano CRD 존재」·「IngressClass <INGRESS_CLASS>(controller traefik.io/ingress-controller)」 PASS·`Traefik 파드 Ready N/M` | §0-2·§2g-2 |
| 2 | values | 설치 호스트 | `cp deploy/overlays/prod/values.env.example deploy/overlays/prod/values.env` → `REPLACE_*` 채움 | 다음 단계의 렌더가 통과 | prod README 「values.env 값」 |
| 3 | 계획 | 설치 호스트 | `sh deploy/install.sh --context <ctx> --plan` | 끝 줄 「계획 끝(변경 없음). 실제 설치: …」, `RESULT: PASS`(또는 이해한 WARN 만) | 그 줄의 사유. §2h·§14-4 |
| 4 | 포탈 TLS 시크릿 | 설치 호스트 | ns 가 없으면 `kubectl --context <ctx> apply -f deploy/k8s/00-namespace.yaml` → `kubectl --context <ctx> -n dms create secret tls <PORTAL_TLS_SECRET> --cert=tls.crt --key=tls.key` | `-o jsonpath='{.type}'` = `kubernetes.io/tls` | S8 이 멈춘다. §3b-2 |
| 5 | 설치 1회차 | 설치 호스트 | `sh deploy/install.sh --context <ctx>` | exit 10, `RESULT: PAUSE` + push 명령(레지스트리에 이미지가 이미 있으면 끝까지) | `--stage <그 단계>`. §2h |
| 6 | 반입·첫 빌드 | 인터넷 노드(같은 커밋) | `python3 deploy/images-lock.py check && python3 deploy/images-lock.py verify` → PAUSE 가 찍은 `sh deploy/push-images.sh --registry <REGISTRY> … --run` → `REGISTRY=<REGISTRY> TAG=<DMS_TAG> ./deploy/docker/build-and-push.sh` | 셋 다 exit 0 | §2e-4·§1 |
| 7 | 재개 | 설치 호스트 | PAUSE 가 찍은 줄 그대로(예: `CACERT=<사내CA> sh deploy/install.sh --context <ctx> --stage S5`) | `STAGE: S12 PASS`(VIP 미개통이면 WARN), S13 체크리스트 출력 | §2h·§14-4 |
| 8 | 설치 뒤 설정 | 설치 호스트 + 포탈 | 첫 관리자 → **메일** → 컨트롤 상태 → 스토리지·정책 → k8s 연동 → 사본 | §6-8 확인표 | §6 |
| 9 | 스모크 | 포탈 + 설치 호스트 | §7 | §7 전부 기대대로 | §7·§10-1 |
| 10 | 개통 | Avi + 설치 호스트 → DNS | Avi 풀 멤버(Traefik 이 도는 노드 :443) 전부 up → `CACERT=<사내CA> sh deploy/install.sh --context <ctx> --only S12`(`--resolve` 라 DNS 전에 된다) → §6-8 확인표 → DNS 등록 → 사용자 공지 | `RESULT: PASS`(VIP readyz WARN 없음) | §6-7 |

### 0-6. 책임 경계

| 주체 | 하는 일 |
|---|---|
| 사이트 IaC(노드 담당) | 공용·데이터·PV 스토리지 마운트, SELinux, sysctl(`fs.protected_*`), CRI-O drop-in(신뢰·고정), 레지스트리 자기 이미지 반입, kubelet 예약, 방화벽, NTP |
| 네트워크·LB·DNS 담당 | Avi VS-443(443 전용 L4 프로필·풀 = Traefik 이 도는 노드 전부 :443·헬스·유휴 ≥ 3600초·PROXY v2 는 선택 — 켜면 클러스터 운영자의 trustedIPs 와 짝, §2g-4), 망 방화벽(노드 :443 은 hostPort 라 노드 INPUT 방화벽이 아니다), DNS |
| 보안·PKI 담당 | 승인(§0-4), 포탈 서버 인증서, 공용 스토리지 밖 사본의 보관 책임 |
| 공용 스토리지 담당 | 설계 3-2 의 G1~G12, 유지보수 전 DMS 상태 파드 비우기 합의(§2f-7) |
| 클러스터 운영자 | K8s·CNI·CRI-O, **Volcano**, **Traefik**(포탈 입구 — 설치·정적 설정(PROXY v2 trustedIPs·타임아웃)·판 올리기·보안 패치, §2g-2·§2g-6), 이미 있는 공유 애드온(감지 C — 업그레이드는 그 소유자), 운영자 RBAC(ns `dms` 의 시크릿 읽기·`pods/exec`·`pods/attach`·파드 생성 제한 — 관리 토큰이 api·controller env 에도 있다, §3b-3) |
| 메일 담당 | 릴레이 허용 클라이언트(마스터 IP)·허용 도메인·인증 키 |
| DMS 설치기(`install.sh` S1~S12) | ns·시크릿(TLS 제외)·레지스트리·애드온(A)·입구 Ingress(Traefik 은 확인만)·DB·앱·검증. 멱등 — 같은 명령을 다시 돌려도 된다 |
| 포탈(세션 관리자) | 빌드·릴리스·계정·메일·k8s 연동·정책·스토리지·컨트롤 상태 |
| 운영자 손 | S13 체크리스트, 공용 스토리지 밖 사본, values.env 태그(명시 태그일 때), 사이트 `patch-config.yaml` 튜닝 키(§3-4) |

### 0-7. 설치 뒤 운영 지도

| 하고 싶은 일 | 절 |
|---|---|
| 설정 값 바꾸기(로그인 보호·보존일·메일 방식 등) | §3-4 |
| 시크릿·인증서·토큰 보관·회전 | §3b |
| 새 DMS 커밋·애드온으로 올리기 | §4(빌드 §8, 릴리스 §9) |
| 되돌리기·DB 복구·재해 복구 | §5 |
| 포탈 입구(Traefik·LB·인증서·원 IP) 점검·문제 해결 | §2g-5·§2g-6 |
| 계정·권한·로그인 보호 | §10 |
| 인증 메일 | §11 |
| 작업 삭제·결과 파일 만료 | §12·§12b |
| 일상 점검·정기 작업·노드 정비·문제 해결 | §14 |

## 1. DMS 이미지 — 최초(부트스트랩)·비상 빌드 (`build-and-push.sh`)

평상시 빌드는 **포탈**에서 한다(§8). 이 절의 스크립트는 두 경우에만 쓴다 — (1) 첫 설치: 포탈이 아직 없어 DMS 이미지 3종을 처음 만들 때(설치기 S4 뒤
PAUSE 가 이 명령을 렌더 값으로 찍는다), (2) 비상: 포탈·컨트롤러가 죽어 빌드를 제출할 수 없을 때. 포탈 빌드와 달리 적합성 프리플라이트도 리소스 봉투도 없다.

- **먼저 「이미지 빌드 전 게이트」(§8)** 를 개발 PC 에서 같은 커밋으로 돌린다 — 비상 빌드라고 건너뛰지 않는다.
- **어느 호스트**: 인터넷 노드(§0-2 — podman·인터넷·레지스트리 ClusterIP 도달). 설치 호스트와 **같은 커밋**의 로컬 체크아웃 루트에서(스크립트가 저장소 루트를
  빌드 컨텍스트로 잡으므로 체크아웃 안 어디서 불러도 된다).
- **명령**(PAUSE 가 찍은 줄 그대로 — 값은 `deploy/.rendered/prod/render.env` 의 `REGISTRY`, 태그는 values.env 의 `DMS_TAG`):

  ```bash
  REGISTRY=<REGISTRY> TAG=<DMS_TAG> ./deploy/docker/build-and-push.sh
  ```

  `dms-mpifileutils` → `dms` → `dms-agent` 순서로 빌드해 `<REGISTRY>/<이름>:<DMS_TAG>` 로 push 한다(`podman push --tls-verify=false` — 클러스터 내 레지스트리는
  평문 HTTP). agent 는 같은 `TAG` 의 dms·dms-mpifileutils 위에서 빌드되므로 **세 태그(`DMS_TAG`·`DMS_AGENT_TAG`·`MFU_TAG`)를 같게** 둔다 — 다르면 PAUSE 가
  그 사실을 알리고 같게 맞춘 뒤 다시 렌더하라고 한다. `REGISTRY` 는 기본값이 없다 — 비우면 아무 명령도 돌기 전에 멈춘다(다른 사이트의 레지스트리로 push 하는
  사고를 막는 fail-closed, `deploy/docker/build-and-push.sh` 머리 주석). 선택 env: `IMAGES`(일부만)·`NO_CACHE=1`·`PUSH=0`(빌드만).
- **성공 판정**: 스크립트가 exit 0·끝 줄 `>> done: …` 이고, 설치 호스트에서 재개한 `install.sh --context <ctx> --stage S5` 의 `STAGE: S5` 가 PASS 다(S5 는 lock 대조와
  함께 앱 렌더의 DMS 이미지 세 태그가 레지스트리에 있는지 본다 — 없으면 이 명령을 다시 보이며 멈춘다, §2e-4 3)).
- **실패하면**: `podman not found` = 인터넷 노드가 아니다. push 의 `connection refused`·`i/o timeout` = 인터넷 노드 → ClusterIP 경로·`NO_PROXY`(§2e-4 3)
  의 증상 표). 빌드 중 상류(npm·PyPI·Debian·dl.k8s.io·github.com)에 닿지 않음 = 인터넷 노드의 `HTTPS_PROXY`(podman 이 프록시 env 를 RUN 단계에
  넘긴다). **빌드 안의 https 가 `x509: certificate signed by unknown authority` 류로 실패 = TLS 를 가로채는 사내 프록시**다 — `build-and-push.sh` 는
  사내 CA 를 빌드 컨테이너에 넣지 않는다(RUN 단계는 이미지 자신의 신뢰 저장소만 본다. 포탈 빌드의 사내 CA 주입 §8 3b-1) 은 여기 적용되지 않는다 —
  BACKLOG 「`build-and-push.sh` 사내 CA 주입」). 우회(**미검증**): 가로채기 없는 망의 빌드 호스트에서 같은 커밋·같은 TAG 로
  `PUSH=0 REGISTRY=<REGISTRY> TAG=<DMS_TAG> ./deploy/docker/build-and-push.sh` → `podman save -o <이름>.tar <REGISTRY>/<이름>:<DMS_TAG>`(세 이미지)
  → 인터넷 노드로 옮겨 `skopeo copy --dest-tls-verify=false docker-archive:<이름>.tar docker://<REGISTRY>/<이름>:<DMS_TAG>`. 첫 설치 전에 이 프록시를
  쓰는 사이트인지 확인해 둔다(§0-1).
- **base 매니페스트(`deploy/k8s/*`)의 `image:`·`DMS_JOB_IMAGE` 는 사이트 중립 자리표시자다 — 고치지 않는다.** base 를 실값으로 바꾸면 오버레이의
  `images:` 치환이 조용히 빠지고 저장소를 받은 다른 사이트가 그 값을 배포한다(CLAUDE.md 「base 매니페스트는 사이트 중립」). 태그는 values.env 가 넣는다.
- 설치 뒤 비상 빌드로 만든 태그는 포탈 릴리스(§9)로 굴린다. 레지스트리 밖 사본도 더한다(§2e-5 「백업」).

> 예(테스트베드): 레지스트리 `10.112.0.50:5000`, 인터넷 노드 k8s-w1 — 값 대응은 `deploy/testbed/README.md` §2.

## 2. 스토리지 마운트 전제 — 데이터 스토리지·PV 백엔드

DMS 는 데이터 스토리지를 마운트하지 않는다. 사이트가 노드에 마운트해 두고 포탈에 등록한다(§6-4). DMS 가 보는 것은 노드의 POSIX 경로뿐이다.

- **에이전트 DaemonSet 은 스토리지마다 hostPath 를 나열하지 않는다**(2026-09-16). `50-agent-daemonset.yaml` 은 호스트 `/` 를 `/host/root` 에 `readOnly`
  (최상위 바인드 = 호스트 루트 fs; 전파된 스토리지 하위 마운트는 호스트 옵션 rw 그대로)·`mountPropagation: HostToContainer` 로 한 번만 붙이고, 프로브가
  `mount_path` 를 그 접두로 번역한다(`DMS_AGENT_HOST_ROOT`). 그래서 새 스토리지는 **노드에 마운트 + 포탈 등록**만으로 1~2 보고 주기 안에 Ready 가 된다 —
  매니페스트를 고치지 않는다(계약 테스트가 막는다).
- **전제: 호스트 `/` 가 shared 마운트**다(systemd 기본 — `findmnt -no PROPAGATION /` 가 `shared`). 아니면 그 노드는 `propagation_stale` 을 보고한다 —
  `mount --make-rshared /` 뒤 그 노드의 에이전트 파드를 다시 띄운다.
- **어느 노드에**: 잡이 도는 워커(NoSchedule taint 없는 워커 — dms-agent 가 뜨는 노드)에, **포탈에 등록할 경로 그대로** 마운트한다. 노드마다 마운트한
  스토리지가 달라도 된다 — 두 스토리지를 함께 마운트한 노드가 있으면 dsync, 없으면 nsync 로 돈다(§7-3).
- **root 접근**: 모든 잡이 uid 0 으로 돈다(§10-1, `execution_manifests._container`). 데이터·PV 스토리지는 DMS 워커(NoSchedule 없는 워커 전부)에서
  root 가 root 로 보여야 한다 — NFS export 는 그 워커 IP 에 `no_root_squash`, OneFS 등은 export 의 root 사용자 매핑을 root 로. squash 면 미리보기가 읽지
  못한 경로로 실패하고(`preview_scan_incomplete`), 실행은 쓰기·`--chown`(PV 목적지 결과 소유 — §10-1, `execution_manifests._auto_chown`)·남의 파일 rm 이
  EACCES/EPERM 으로 실패한다. 스토리지 담당의 승인 항목이다(§0-4).
- **NFS 계열(OneFS 포함)은 `lookupcache=positive`** 로 마운트한다. 기본 `all` 이면 다중 노드 dsync 에서 한 노드가 막 만든 디렉터리를 다른 노드가 음성 캐시
  때문에 못 보고 ENOENT 로 실패한다(테스트베드 실측 2026-10-26). remount 로는 바뀌지 않는다(umount → mount). CephFS·GPFS 는 해당 없다.
- **`managed_root`** = `mount_path` 자신 또는 그 아래(`repositories.storages._validate`) — DMS 가 다루는 루트다. 등록 전에 디렉터리를
  만들어 둔다. 사용자 데이터 root 는 공용 디렉터리 `<SHARED_DIR>/dms` 와 겹칠 수 없다 — 그 자신·상위·하위는 422 `storage_overlaps_dms_state`(§2d-1).
  데이터 스토리지와 공용 스토리지가 같은 파일시스템이면 `SHARED_DIR` 를 그 스토리지의 `mount_path` 밖(별도 마운트포인트)에 둔다.
- **`backend_type`** 은 8종 라벨이다 — `cephfs`·`gpfs`·`wekafs`·`lustre`·`purestorage`·`netapp`·`onefs`·`nfs`(`repositories.storages._BACKENDS`). 경로
  해석에는 쓰이지 않는다. 단 `gpfs`·`onefs`·`nfs` 는 k8s 연동의 PV 경로 프리셋 키다(`pv_paths.PRESETS` — §6-5).
- **PV 백엔드**(k8s 연동 — §6-5): 대상 클러스터의 PV 가 실제로 놓인 스토리지를 DMS 워커에 같은 방식으로 마운트한다. PV 경로가 워커에서 그대로 보여야 한다.

확인(워커에서): `findmnt -T <mount_path>` 가 기대한 장치·옵션, `ls -d <managed_root>`. 등록 뒤에는 포탈 스토리지 → 노드 화면에서 그 노드의 스토리지가 Ready
인지 본다(에이전트 보고 30초 주기 — `DMS_AGENT_INTERVAL_SECONDS`).

> 예(테스트베드): 데이터 root·e2e 픽스처를 만드는 플레이북(`deploy/testbed/dms-shared-fs.yml`)과 CephFS 레이아웃은 `deploy/testbed/README.md` §4.

## 2b. 제어면 root 전제 (2026-09-09)

`dms-api`·`dms-controller` 컨테이너는 **uid 0** 으로 돈다(`40-api.yaml`·`41-controller.yaml` 컨테이너 securityContext: capabilities 전부 drop, 이미지 fs
읽기 전용; `migrate` initContainer·`30-migrate-job` 은 이미지 USER 65532 그대로). 아티팩트 base(`<SHARED_DIR>/dms/artifacts`)가 `root:root` 라 65532 로는
3홉 쓰기 왕복 검증이 항상 실패하기 때문이다. 파일시스템 권한은 2차 방어가 아니다 — 인가는 DB 와 코드 봉쇄뿐이다(`docs/ARCHITECTURE.md` §7
「root 제어면」). 이 결정에 따라오는 **배포 전제** 네 가지(2b-1~2b-4):

### 2b-1. 아티팩트 base 권한

**base 와 `<base>/<job_id>` 는 root:root · 요청자 쓰기 불가(비-world-writable · 비-group-writable · POSIX ACL 쓰기 항목/default ACL 없음), 단 base 는
other 실행(x) 필수**(`chmod 755` 또는 `711`; **700/710/750/770 · g+w · ACL 금지** — 2026-09-30 감사 정정 + 2026-10-07). 러너가 `<job_id>` 를 root 로
만들고 `<phase>` 만 실행 신원에게 chown 한다. dms-k8s 는 모든 잡이 root 라 잡 안의 도구가 이 권한에 막히지는 않는다. 그래도 base 가 쓰기 가능하면 공유 FS 를
마운트한 다른 호스트의 사용자가 `<job_id>` 를 미리 만들어 봉쇄 기준을 옮기거나 `<phase>` 의 rank.sh 를 바꿔치기할 수 있어 제어면 판정은 그대로다.
**강제 위치** — 제어면(3홉 API·컨트롤러 홉 + base 저장 PUT/validate 422, `artifact_base.roundtrip_artifact_base`): 소유자가 제어면 euid(root)가 아니면
`artifact_base_not_owned`, `o+w` 면 `artifact_base_world_writable`(sticky 여도), `g+w`(gid 무관)·POSIX ACL 의 named 쓰기 항목·default ACL 이 있으면
`artifact_base_group_writable`, other-x 가 없으면 `artifact_base_not_traversable`. 저장이 막히고 3홉 화면이 빨갛다. 이 판정은 표시·저장용이라 잡 제출을
막지 않는다. 제어면 판정은 base 자체만 보므로 **NFSv4/GPFS 상속 ACE 는 남는 위험**이다 — 그런 스토리지에 base 를 두면 상속 ACE 가 없는지 직접 확인한다
(러너가 root 로 만드는 `<job_id>/<phase>` 가 그것을 물려받는다).
고치는 법: `chown root:root <base>; chmod 755 <base>`(g+w 제거), POSIX ACL 은 **재귀로** `setfacl -R -b -k <base>` — base 만 지우면 그 사이 만들어진
`<job_id>` 디렉터리에 상속된 ACL 이 남는다(`getfacl -R -s <base>` 로 남은 항목이 없는지 확인). 상속 default ACL(POSIX/GPFS/NFSv4)이 `<job_id>`·`<phase>` 를
좁히면 요청자 통과와 제어면의 other 읽기가 함께 깨진다.

### 2b-2. fs.protected_hardlinks·symlinks

**`fs.protected_hardlinks=1` · `fs.protected_symlinks=1`** — 공유 FS 를 마운트하고 사용자가 `link(2)` 를 부를 수 있는 **모든** 호스트(k8s 워커뿐 아니라
로그인·계산 노드 포함). 하드링크 허용 여부는 **링크를 만드는 쪽 커널**이 결정하므로(MDS 는 검사하지 않는다) 워커만 켜 두면 충분조건이 아니다. 에이전트
프로브는 k8s 워커만 본다. 확인: `sysctl fs.protected_hardlinks fs.protected_symlinks`(둘 다 1). 코드는 nlink>1·남의 소유 파일을 404 로 거르지만
(`artifact_files.open_artifact_fd`) 이 sysctl 이 근본 방어다. preflight(S1)의 노드 셸 체크리스트에 같은 줄이 있다.

### 2b-3. 공용 디렉터리 권한

**공용 디렉터리(base 의 부모 `<SHARED_DIR>/dms`)는 root:root 로 잠그되 그룹 쓰기·other 통과는 금지 — `750` 또는 `700`**(2026-10-07 정정, 2026-10-08
리뷰로 711 제거). **요청자는 이 디렉터리를 통과하지 못해야 한다** — other-x(`711`·`755`)를 주면 모든 uid 가 base(755)를 지나 0755 잡 디렉터리와 0644
아티팩트(stdout/stderr 로그·rank.sh·summary.json·dscan 리포트 — 남의 데이터 경로·파일 통계)를 직접 읽어 API 의 소유자 검사
(`artifact_files.open_artifact_fd`)를 우회한다(711/755 는 base **자체**의 선택지다, 2b-1). 제어면·에이전트·러너(launcher)·잡 도구는 root 라 통과하고, 잡
파드는 base 를 **전용 hostPath 볼륨**(`/dms-artifact-base`, `artifact_base.ARTIFACT_MOUNT`)으로 받아 그 부모를 지나가지 않는다. S0 가 만드는 공용
디렉터리는 `750` 이다(§2d-2 — S0 는 이미 있는 `dms` 의 750·700 만 받아들이고 770 은 FAIL 로 알린다).
**스토리지의 `managed_root`(사용자 데이터 root)는 공용 디렉터리 아래에 두지 않는다** — 등록이 422 `storage_overlaps_dms_state` 로 막고(§2d-1), 그 스토리지를
쓰는 잡 컨테이너에 DMS 상태가 보이게 된다.

### 2b-4. 아티팩트 base 허용 접두

**`DMS_ARTIFACT_BASE_ALLOWED_PREFIXES`**(`20-config.yaml` — base 는 자리표시자 `/set-by-overlay.invalid/dms/artifacts`, 오버레이 `replacements` 가
`DMS_ARTIFACT_BASE_URI` 에서 `<SHARED_DIR>/dms/artifacts` 로 낸다, §2d) — 포탈에서 고를 수 있는 base 를 api/controller 가 실제로 붙인 경로(같은 값의
hostPath) 아래로 묶는다. root 라 파일시스템이 더는 경로를 걸러 주지 않는다. 허용 밖 경로는 422 `artifact_base_outside_allowlist`, 이미 저장된 base 가
밖이면 3홉 화면의 API·컨트롤러 홉이 같은 사유로 실패를 낸다.

**영속성**: DMS 의 상태는 두 곳에만 있다 — 공용 디렉터리 `<SHARED_DIR>/dms`(결과 파일 `artifacts`·레지스트리 저장소 `registry`·PG 데이터 `pgdata`·덤프
`backup`)와 그 PG 안의 DB(스토리지·정책·계정·작업 기록). 둘 다 노드 재부팅·파드 재기동과 무관하다. 공용 스토리지를 잃을 때를 위한 밖 사본은 §3b-5·§6-6.
재부팅 직후 첫 잡은 에이전트 첫 보고(30초)·프로브(60초) 전이면 `no_ready_sync_candidate` 로 거부될 수 있다 — 1~2분 뒤 다시 낸다(§14-3).
클러스터·공용 스토리지를 통째로 다시 세우는 순서는 §5-6(테스트베드의 재구축 순서는 `deploy/testbed/README.md` §4).

**적용 방법**: securityContext 는 매니페스트 필드라 포탈 릴리스(이미지 patch)로는 실리지 않는다 — 매니페스트가 바뀐 커밋으로 올릴 때는
`sh deploy/install.sh --context <ctx> --only S11` 을 한 번 거친다(§4-2). 릴리스 화면의 「보안 설정 드리프트」 배지가 api·controller 의 securityContext
어긋남을 보인다(§9-8).

배포 후 확인:

```bash
kubectl --context <ctx> -n dms exec deploy/dms-api -c api -- sh -c 'id; grep -E "^Cap(Eff|Bnd)" /proc/1/status; touch /app/.w'
# uid=0(root) gid=0(root) / CapEff: 0000000000000000 / touch: Read-only file system
kubectl --context <ctx> -n dms get pod -l app.kubernetes.io/name=dms-api \
  -o jsonpath='{.items[0].spec.initContainers[0].securityContext}{"\n"}'   # {} (migrate 는 65532)
```

root 를 「강화」한답시고 runAsNonRoot·65532 를 넣지 않는다 — root:root base 마다 `artifact_base_not_writable` 이 나 즉시 회귀한다
(`tests/test_release_manifest_contract.py` 가 모양을 고정한다).

## 2d. 공용 디렉터리(SHARED_DIR) — 배치·S0·S7·전환 런북 (2026-11-03 ③)

설계 v6(`docs/history/specs/2026-11-03-dms-k8s-prod-design.md`) 3-1·3-2·8-1 「S0·S7 세부」. 이 절은 dms-k8s 공통이다(테스트베드의 지금 배치는
`deploy/testbed/README.md` §3).

DMS 상태(아티팩트·레지스트리 저장소·PG 데이터·덤프)는 사이트가 **모든 마스터·워커의 같은 호스트 경로**에 `/etc/fstab` 으로
미리 마운트한 공용 스토리지(종류 무관) `<SHARED_DIR>` 아래 `dms/` 에 둔다. DMS 는 마운트·프로비저닝하지 않고 FS 종류·마운트 옵션으로
판정하지도 않는다. api·controller 는 `<SHARED_DIR>/dms/artifacts` **하나만** hostPath(`type: Directory`)로 같은 경로에 붙이고, env
base·allowlist 도 같은 값이다 — base 매니페스트는 자리표시자(`/set-by-overlay.invalid/dms/artifacts`)뿐이고 오버레이 `replacements` 가
`DMS_ARTIFACT_BASE_URI` 하나에서 셋을 낸다(`docs/ARCHITECTURE.md` §6 「아티팩트 경로의 원천은 하나다」, `tests/test_shared_dir_contract.py`).
원천 값: prod = `values.env` 의 `SHARED_DIR`(render.sh — `deploy/overlays/prod/README.md`). 값을 커밋하는 고정 오버레이(테스트베드)는
`patch-config.yaml` 의 `DMS_ARTIFACT_BASE_URI` 가 원천이다(`deploy/testbed/README.md` §3).
형식(render.sh·S0·S7 공통): 절대 경로, `/` 아님, 정규화(`//`·`/./`·`/../`·끝 `/` 없음), 문자 `^/[A-Za-z0-9._/-]+$`.

### 2d-1. 트리 (설계 v6 3-1)

| 경로(`<SHARED_DIR>/…`) | 소유:모드 | 쓰는 주체 | 비고 |
|---|---|---|---|
| (`<SHARED_DIR>` 자신) | 사이트 | — | 마운트포인트 또는 그 아래. DMS 가 만들지 않는다 |
| `dms` | root:root 0750(이미 있으면 0700 도) | — | 공용 디렉터리 — 요청자 통과 금지(§2b-3, 711·755 금지). 센티널 `.dms-shared-id`(무작위 32 hex) |
| `dms/artifacts` | root:root 0755(이미 있으면 0711 도) | api·controller·잡(uid 0) | 아티팩트 base = allowlist = api/controller hostPath(§2b-1) |
| `dms/pgdata` | root:root 0700 | PG(CNPG, §2f) | `.retired`(root 0700), `dms-pgdata-01..05`(26:26 0700 + `.dms-pv-id` = PV 이름) |
| `dms/backup` · `dms/backup/pg` | root:root 0750 · 26:26 0700 | pg_dump(§2f-5) | 센티널 `.dms-backup-id` |
| `dms/registry` | 65532:65532 0750 | 클러스터 내 레지스트리(§2e) | 센티널 `.dms-registry-id`(= `.dms-shared-id`) |

- 트리 전체를 S0 가 미리 만든다(2d-2) — 설치기는 만들지 않고 검사만 한다.
- 모든 hostPath 는 `<SHARED_DIR>/dms/…` **하위**를 가리키고 `type: Directory` 다 — 마운트가 빠진 노드에는 그 하위 경로가 없어 파드가
  뜨지 않는다(fail-closed, G2). 마운트포인트 자체를 붙이거나 `DirectoryOrCreate` 로 「완화」하면 그 노드에서 빈
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
python3 deploy/shared-storage-probe.py --context <ctx> --overlay deploy/.rendered/prod/app --shared-dir <SHARED_DIR>
#   └ 렌더된 앱 층에서 dms 이미지를 고른다. 템플릿 deploy/overlays/prod 는 이미지가 REPLACE_ 라 거부되고, 렌더 디렉터리에는 values.env 가
#     복사되지 않아 SHARED_DIR 는 --shared-dir 로 준다(테스트베드 형태는 deploy/testbed/README.md)
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

옛 배치(공용 스토리지의 마운트포인트 전체를 hostPath 로 붙이고 base 가 그 아래에 있던 2026-11-03 전 배치)에서 `<SHARED_DIR>` 로 옮기거나 SHARED_DIR 를 바꿀 때. 이 렌더를 그냥
적용하면 `guard-images.sh` 가 hostPath 변경으로 **exit 5** 거부한다 — 아래를 마친 뒤에만 `ALLOW_HOSTPATH_CHANGE=1` 로 연다.

0. **확인(읽기만)**: 진행 중 잡이 없다(`kubectl --context <ctx> -n dms get vcjob,pods`) — 6 단계(DB base)까지 잡 제출을 멈춘다. 포탈 관리 →
   「아티팩트 경로」의 출처(env/db)와 값을 적어 둔다.
1. **노드 마운트**(사이트): 모든 마스터·워커의 같은 경로에 `/etc/fstab`. NFS 라면 export `no_root_squash`·`sec=sys`, 마운트 `hard`,
   `lookupcache=positive`(G3 권장 — remount 로는 안 바뀐다).
2. **S0**: 한 노드에서 `sh deploy/shared-dir-init.sh <SHARED_DIR>` → 다른 노드에서 `--check` 가 PASS(같은 센티널 값).
3. **S7**: `shared-storage-probe.py` 가 0(또는 3 WARN)이어야 진행한다. 1·4 면 멈추고 원인을 고친다.
4. **옛 base 복사(소유 보존)**: 한 노드에서 root 로 `rsync -aH --numeric-ids <옛 base>/ <SHARED_DIR>/dms/artifacts/`(rsync 가 없으면
   `cp -a <옛 base>/. <SHARED_DIR>/dms/artifacts/`). 대조: 두 쪽에서 `{ find . -type d -printf '%p d %U:%G %m\n'; find . ! -type d -printf '%p %y %U:%G %m %s\n'; } | sort | md5sum`
   이 같고(디렉터리 크기는 뺀다 — CephFS 는 디렉터리 크기를 하위 바이트 합계로 보고해 늘 다르다, 테스트베드 실측 2026-11-03), 새
   base 가 `root:root 755`. `-A`·`-X` 는 쓰지 않는다(NFS 4.2 가 xattr 을 지원하지 않을 수 있고, base 에는 원래 ACL 이 없어야 한다 — §2b-1).
4b. **(`REGISTRY_MODE=in-cluster` 면) 레지스트리 저장소**: §2e-5 「저장소 이동」 2~4 를 앱 적용(5) **전에** 한다(빌드·push 가 없는 창 —
   S0 는 2 에서 했다). 빠뜨리면 레지스트리는 옛 경로에 남고, `SHARED_DIR_ID` 만 새 값으로 바꾼 렌더는 「센티널 불일치」로 레지스트리를 못
   띄운다 — install.sh S4 가 레지스트리 rollout 시간 초과(init 「센티널 불일치」)로 멈춘다(2026-11-03 ⑦).
5. **적용**: `sh deploy/overlays/guard-images.sh <overlay> --context <ctx>` → 기대 exit 5(라이브 hostPath → 새 경로를 찍는다; 오버레이
   태그가 라이브와도 다르면 3 이 먼저다 — 그때는 `ALLOW_IMAGE_CHANGE=1` 도). `ALLOW_HOSTPATH_CHANGE=1` 로 다시 통과시킨 뒤 평소 적용
   절차(prod = `ALLOW_HOSTPATH_CHANGE=1 sh deploy/install.sh --context <ctx>` — S4·S11 의 가드가 함께 열리고, S11 이 설정 rev 변화를 보고 api·controller 를
   다시 띄운다; 손으로 적용했으면 `rollout restart` — ConfigMap 의 base·allowlist 는 기동 때만 읽힌다, §3-5) → `rollout status`. 확인: 두 Deployment 의
   `.spec.template.spec.volumes[*].hostPath` 와 `dms-config` 의 `DMS_ARTIFACT_BASE_URI`·`DMS_ARTIFACT_BASE_ALLOWED_PREFIXES` 가 새 경로.
   컨트롤러 롤아웃 뒤 30초쯤 기다린 다음 본다.
6. **DB base**: 0 단계 출처가 `db` 였다면 세션 관리자로 포탈 「아티팩트 경로」를 `file://<SHARED_DIR>/dms/artifacts` 로 다시 저장한다
   (잠겨 있으면 force). 적용 **뒤**여야 한다 — 3홉 검증은 새 마운트에서만 통과한다. `env` 면 할 일이 없다. 3홉(API·컨트롤러·노드)이 모두
   정상이고, 「DMS 상태 디렉터리」가 `<SHARED_DIR>/dms` 이며 겹치는 스토리지 경고가 없어야 한다.
7. **확인**: 옛 잡 로그가 열린다(복사본), 다중 노드에서 미리보기·실행·scan·사용량·정리, `<SHARED_DIR>` 를 `mount_path` 로 등록하면 422
   `storage_overlaps_dms_state`.
8. **보존·되돌림**: 옛 base 는 지우지 않는다(되돌림 창 — 기한은 BACKLOG). 되돌림은 같은 절차를 거꾸로 — SHARED_DIR(와 base)를 원래
   값으로 되돌려 `ALLOW_HOSTPATH_CHANGE=1` 로 적용하고, 출처가 db 였으면 다시 저장한다.

**옛 `SHARED_FS` 사이트**(values.env 에 `SHARED_FS` 가 남은 사이트 — render.sh 가 FAIL 하고 `SHARED_DIR` 를 안내한다)의 이행 주의 세 가지는
`deploy/overlays/prod/README.md` 「SHARED_DIR」 한 곳에 있다. 확인한 뒤 위 런북을 따른다.

## 2e. 클러스터 내 레지스트리(플랫폼 층) — 노드 준비·S4·S5·S6·운영 (2026-11-03 ④)

설계 v6 7(7-1~7-6)·8-1(S0-4·S0-5·S-1·S4~S6)·8-2 「층 배치」. 이 절은 dms-k8s 공통이다(테스트베드 값은 `deploy/testbed/README.md` §3).
설치기(§2h)의 S4·S5·S6 가 아래를 자동으로 한다(S4 뒤 정지·`--stage S5` 재개·lock 대조·pull 프로브 — 2026-11-03 ⑦). 아래 명령은 같은 일의
손 판(조사·복구용)이다.

CNCF Distribution v3.1.2 하나(오퍼레이터 없음, #46)를 control-plane 노드에 띄운다. **평문 HTTP·익명·`host:port` 만**(DMS 가 강제 — 포탈
태그 조회·삭제는 `http://` 고정, 빌드 push 는 `--tls-verify=false`, 경로 접두 불가). 저장소는 공용 디렉터리 `<SHARED_DIR>/dms/registry`
(S0 가 65532:65532 0750 + 센티널 `.dms-registry-id` 로 만든다 — §2d).

### 2e-1. 구성·값

| 무엇 | 어디 | 비고 |
|---|---|---|
| PriorityClass `dms-addon-critical`(1000) | `deploy/platform/priorityclass` | 잡(≤ 200)보다 위 — CNPG 오퍼레이터가 쓴다(옛 EG 컨트롤러·Envoy 는 2026-11-16 입구 교체로 없앴다). 레지스트리는 쓰지 않는다(마스터 전용) |
| ConfigMap `dms-registry-config` | `deploy/platform/registry/config.yml` + `expected-registry-id` | `/etc/distribution/config.yml`(이미지 기본·debug :5001 을 덮음): rootdirectory `/var/lib/registry`, delete 켬, uploadpurging 168h/24h, readonly 끔, **cache 없음**, `:5000`, health.storagedriver |
| Deployment `dms-registry` | `deploy/platform/registry/deployment.yaml` | replicas 1, control-plane nodeSelector + toleration, not-ready·unreachable 30초, RollingUpdate maxSurge 1·maxUnavailable 0, hostPath `<SHARED_DIR>/dms/registry`(`Directory`), 이미지 `<IP>:<PORT>/registry:3.1.2`(**태그 ref 예외**, init·본 컨테이너 모두 IfNotPresent — 자기 이미지를 자기에게서 pull 하지 않는다), 라벨 `dms.io/addon-version`(= 태그), readiness `GET /v2/_catalog?n=1`(5s/10s/3), liveness TCP, init `sentinel-check`(**검사만**), uid/gid 65532·drop ALL·읽기 전용 rootfs |
| Service `dms-registry` | `deploy/platform/registry/service.yaml` | `clusterIP: <REGISTRY_CLUSTER_IP>` 고정, 포트 `<REGISTRY_PORT>`(→ 컨테이너 5000) |
| Secret `dms-registry-http` | **매니페스트 밖**(2e-3) | 키 `http-secret` → env `REGISTRY_HTTP_SECRET`(secretKeyRef). 없으면 파드가 뜨지 않는다 |
| `deploy/images.lock` | 커밋 | linux/amd64 **매니페스트** digest(index 금지). 검사 `python3 deploy/images-lock.py check`·`verify`, 줄 만들기 `resolve <DEST> <SOURCE>` |

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

**경로 접두는 쓸 수 없다**: `REGISTRY` 는 `host:port` 만이다 — `host:port/<접두>` 는 코드(포탈 태그 조회·삭제·빌드 push)가 지원하지 않는다. 클러스터마다
레지스트리를 따로 두는 이유다.

### 2e-2. 노드 준비(사이트 IaC, 1회) — S0-4 신뢰 · S0-5 레지스트리 이미지 반입·고정

`C`=kube-context, `IP:PORT`=`<REGISTRY_CLUSTER_IP>:<REGISTRY_PORT>`. CRI-O 기준(containerd 경로 없음).

**주소부터 고르고 확인한다**(2026-11-03 ⑦ 리뷰 1): 이 주소는 아래 S0-4(전 노드 drop-in)·S0-5(마스터 반입 이름·고정 패턴)에 박히므로, 박기 전에
렌더 없이 env 만으로 S1 의 주소 검사(preflight 출력의 「5. 공용 디렉터리 · 레지스트리 값」 — ServiceCIDR 안·미사용·하단 정적 대역)를 돌려 PASS 인 주소를 고른다. 예: /12 대역의 `x.x.0.10` 은
kube-dns 기본 주소라 FAIL 이다. 한 번 정하면 바꾸지 않는다.

```bash
REGISTRY_MODE=in-cluster REGISTRY_CLUSTER_IP=<IP> sh deploy/preflight-cluster.sh --context C    # preflight 「5.」 의 주소 두 줄이 PASS 인 주소로
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

# S0-5(마스터의 레지스트리 자기 이미지 반입·고정)는 2e-4 2) 하나에 있다 — 레지스트리를 띄우기 전(S4 전)에 한다.
```

- skopeo 는 RHEL AppStream 패키지다(사이트 미러) — 클러스터 노드 중에는 인터넷 노드와 마스터에만 둔다(워커에는 필요 없다).
- 새 워커는 S0-4 만 하면 된다. 새 마스터는 레지스트리가 살아 있으면 `sudo crictl pull IP:PORT/registry:3.1.2` 뒤 고정하고, 아니면 S0-5 를
  반복한다. 레지스트리 자기 이미지는 이 레지스트리에 기대지 않는다(7-4 의존 규칙 — 시스템 이미지도 마찬가지).

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
| 파드 Pending + 이벤트 `FailedScheduling` | control-plane 노드에 자리가 없거나 taint 키가 다르다(`describe` 의 사유). 사유가 `didn't match Pod's node affinity/selector` 면 control-plane 라벨의 **값**이 `""` 가 아니다(RKE2 = `true` — values.env `CONTROL_PLANE_LABEL_VALUE`, §0-2·§2f-1; 확인 `kubectl --context C get nodes -L node-role.kubernetes.io/control-plane`). FailedMount 면 위 첫 줄 |
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
sudo crictl images --digests | grep 'IP:PORT/registry'               # 3.1.2 와 DIGEST 앞자리 = lock digest 앞자리
# 고정 — 같은 옵션은 이름순 마지막 drop-in 이 이기고 배열은 합치지 않는다: 기존 목록을 읽어 같은 배열에 함께 쓴다.
sudo crio config 2>/dev/null | sed -n '/^[[:space:]]*pinned_images/,/]/p'     # 기존 목록(주석 `# pinned_images` 면 비어 있다)
sudo tee /etc/crio/crio.conf.d/99-dms-pinned.conf >/dev/null <<'EOF'
[crio.image]
pinned_images = ["IP:PORT/registry:*"]
EOF
#   ↑ 기존 목록이 있으면 같은 배열에 넣는다. 패키지 10-crio.conf 는 업그레이드 때 덮어써지므로 건드리지 않는다.
sudo systemctl reload crio
sudo crio config 2>/dev/null | sed -n '/^[[:space:]]*pinned_images/,/]/p'     # 실효값에 IP:PORT/registry:* 가 있어야 한다
sudo crictl inspecti IP:PORT/registry:3.1.2 | grep -m1 '"pinned"'             # "pinned": true (CRI Image.pinned — preflight 노드 체크리스트와 같은 줄)
sudo rm -f /tmp/registry-3.1.2.tar                                            # 마스터·인터넷 노드 모두 지운다
```

- 반입 store 지정: `crio config` 의 `storage_driver = ""` 는 자동 선택이다(실효 overlay — reload 로그 `storage_driver = "overlay"`, 테스트베드
  실측) — 위 `overlay@…` 그대로 쓴다. 다른 storage_driver·root 를 쓰는 사이트는 `sudo crio config` 의 값으로 바꾼다.
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
- 대상: lock 전부 — buildah(포탈 빌더), CNPG 2(오퍼레이터·PG), 레지스트리 자기 이미지(업그레이드·새 마스터·S6 용). 입구 Traefik 은 클러스터
  것이라 lock 에 없다(2026-11-16 — 옛 EG·envoy 줄은 지웠다). Volcano 3 줄(A 경로 설치용)은 Volcano 층과 함께 덧붙인다(아직 없다 — BACKLOG ⑦).
- `--run` 은 프록시 env 가 있는데 `NO_PROXY` 가 레지스트리를 덮지 않으면 **아무것도 하지 않고** FAIL 한다(stderr 에 export 줄).
- **external 레지스트리 사이트**(`REGISTRY_MODE=external`, `REGISTRY_CLUSTER_IP` 없음): S4 는 PriorityClass 만 — lock 전부를 사이트 `REGISTRY` 로
  push 한다(`--registry <REGISTRY>`, TLS 레지스트리면 `--dest-tls-verify true` — values.env `REGISTRY_TLS_VERIFY=true` 면 설치기가 그대로 싣는다;
  포탈 태그 조회·삭제는 http 라 TLS 레지스트리에서 쓰지 못한다). 설치기 S5 가 그 `REGISTRY` 를 직접 대조하고(설치 호스트가 닿아야
  한다 — 닿지 않으면 모름 exit 4) 다르면 이 명령을 같은 주소로 보인다. lock 대조가 PASS 면 lock 줄은 다시 보이지 않고 DMS 이미지 안내만 보인다
  (2026-11-03 ⑦ 리뷰 2). 출력은 **인터넷 노드의 DMS 저장소(설치 호스트와 같은 커밋) 루트에서** 붙여 넣는다(`lock: deploy/images.lock` — 상대 경로).
- **DMS 3종**(dms·dms-agent·dms-mpifileutils)은 상류 이미지가 아니라 lock 밖이다. 처음에는 같은 노드에서 최초 부트스트랩으로
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

- lock 에 이 사이트가 레지스트리로 받지 않는 애드온 줄(예: 클러스터 운영자가 따로 설치한 Volcano)이 있으면 그 줄만 「태그 없음」 FAIL 이다 — 설치기는
  `--skip` 을 붙여 보이고, 손 대조도 `--skip <이름>` 으로 뺀다. 대조는 프록시를 쓰지 않는다.

### 2e-5. 운영 (설계 7-6)

- **업그레이드**: `deploy/platform/registry/deployment.yaml` 의 두 이미지 태그·라벨 `dms.io/addon-version` 과 `deploy/images.lock` 의
  `registry:` 줄(`images-lock.py resolve`)을 함께 바꿔 커밋(셋이 같아야 한다 — 테스트가 고정) → 인터넷 노드가 새 이미지를 push(S5) → **마스터 전부** `sudo crictl pull IP:PORT/registry:<새 태그>`(고정 패턴
  `registry:*` 가 덮는다) → `sh deploy/render.sh <site> --context C` → `guard-images.sh` 가 exit 3(이미지 변경 — 안내에 이 순서가 나온다) →
  `ALLOW_IMAGE_CHANGE=1` 로 다시 → SSA apply → `rollout status`. 설치기로는 마스터 pull 뒤 `sh deploy/install.sh --context C --site <site> --only S4
  --upgrade-addon registry`(감지 D< → upgrade — 가드를 이 층에서만 연다; 전역 `ALLOW_IMAGE_CHANGE=1` 은 S11 의 앱 가드까지 열므로 쓰지 않는다).
  maxSurge 1 이라 새 파드가 Ready 가 된 뒤 옛 파드가 내려간다. **빌드·push 가 없을 때** 한다(겹치는 동안 Service 가 요청을 나눠 S7 ② WARN 사이트에서는
  push 가 `blob unknown` 일 수 있다). 옛 파드는 preStop 5초 뒤 SIGTERM 을 받고 진행 중 요청을 `http.draintimeout`(20s) 동안 마친다(테스트베드 실측:
  교체 중 매니페스트 GET 172회·이미지 복사 62회 실패 0).
- **설정 변경**(2026-11-03 ④ 리뷰 2): Distribution 은 `config.yml` 을, init 은 `expected-registry-id` 를 **기동 때만** 읽는다. 렌더러가 둘의
  sha256 을 파드 템플릿 annotation `dms.io/config-sha256` 에 넣으므로 `config.yml`·`SHARED_DIR_ID` 만 바뀐 렌더도 SSA 로 적용하면 롤링
  교체된다(가드는 이미지·hostPath 만 보므로 이 변경은 막지 않는다). 업그레이드와 같이 빌드·push 가 없을 때 한다. 손으로 고친 ConfigMap
  (아래 GC 길 B)은 렌더값이 그대로라 체크섬도 그대로다 — 되돌린 뒤 `rollout restart` 가 필요하다.
- **GC**(연 몇 번, 수동 — 빌드가 없고 레지스트리가 Ready 일 때만):
  1. readonly 켜기: `kubectl --context C -n dms set env deploy/dms-registry REGISTRY_STORAGE_MAINTENANCE_READONLY='{"enabled":true}'`
     (env 의 JSON 맵 파싱은 테스트베드에서 확인(POST 405) — 길 A. 다른 판에서 안 되면 길 B: `kubectl --context C -n dms edit cm dms-registry-config` 의 `readonly.enabled: true` +
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
  GC 창 동안 포탈 삭제는 409(`registry_delete_disabled` — 문구가 GC 읽기 전용 창도 말한다, 테스트베드 확인), 빌드 push 는 실패, pull 은 된다.
  `set env` 로 바꾼 필드는 kubectl 소유가 되므로 끈 뒤 다음 SSA apply 가 충돌하면 `--force-conflicts` 로 dms-install 에 되돌린다.
- **포탈 이미지 관리**(「빌드 > 이미지 관리」, §8 2b): 삭제는 digest 단위(매니페스트)다 — 안 쓰는 태그라도 사용 중 태그와 digest 를
  공유하면 409 `registry_digest_in_use`(같이 지우면 사용 중 태그가 사라진다 — 테스트베드 실증, 01fd922). 사용 중 판정은 동봉 매니페스트에 더해
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
  앱이 이미 이 레지스트리를 쓰고 있으면 걷지 않는다 — 앱·빌더·잡이 이미지를 받을 다른 레지스트리가 먼저 있어야 한다. 되돌림은 포탈 릴리스로만 한다(§5-1 —
  `kubectl rollout undo` 금지).
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

## 2f. DB(CNPG) — 오퍼레이터(S9)·DB 층(S10)·백업·재활용·업그레이드·운영 규칙 (2026-11-03 ⑤)

설계 v6 6(6-1~6-6)·8-1(S-1·S2·S9·S10 「S10 세부」·「순서의 이유」)·8-2(CNPG 행·A 경로·층 배치). 이 절은 dms-k8s 공통이다
(테스트베드 값은 `deploy/testbed/README.md` §3). 설치기(§2h)의 S2·S9·S10 이 아래를 감지 상태 기계·자동 순서·판정으로 한다(2026-11-03 ⑦) — 아래는 손 판이다.
앱은 접속 문자열(`DMS_DATABASE_URL`)만 본다 — `connect_timeout` 은 코드 기본 5초(설계 6-3).

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
| `PG_CPU_REQUEST`·`PG_MEMORY`·`PG_SHARED_BUFFERS` | `1`·`4Gi`·`PG_MEMORY`/4 | 메모리는 requests == limits. `PG_SHARED_BUFFERS` 를 비우면 `PG_MEMORY` 의 1/4(둘 다 없으면 base 의 1GB = 4Gi/4) — shared_buffers 는 메모리 limit 안에서 채워지므로 `PG_MEMORY` 만 줄이고 1GB 를 남기면 부하 때 cgroup OOM 으로 재기동을 반복한다. 실효 메모리의 절반 이상이면 렌더 FAIL. (예: 테스트베드 50m/256Mi/64MB — `deploy/testbed/README.md` §3) |
| `PG_PLACEMENT` | `control-plane` | 마스터마다 1개(#27). `all-nodes` 는 **control-plane 1대 시험 클러스터 전용**(nodeSelector 를 빼고 toleration·required anti-affinity 는 둔다) — prod 에 쓰지 않는다 |
| `CONTROL_PLANE_LABEL_VALUE` | (빈 값) | control-plane 노드 라벨 `node-role.kubernetes.io/control-plane` 의 **값**. base 의 nodeSelector 는 kubeadm 관례인 `""` 를 고른다 — **RKE2 는 `true`** 라 그대로면 레지스트리·PG·api/controller 가 Pending 이다. 값을 주면 앱 `patch-placement`·platform/registry·platform/db-cluster(`PG_PLACEMENT=control-plane` 일 때)의 그 값을 바꾼다. 확인: `kubectl --context <ctx> get nodes -L node-role.kubernetes.io/control-plane` |
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

- `<PW>` = `dms-pg-app` 의 password. keepalive·`tcp_user_timeout` 이 없으면 primary 가 있던 노드가 죽었을 때 그 연결이 약 16분 매달린다(테스트베드 실측 —
  keepalive 없는 클라이언트 15분 52초, 이 값이면 16초에 감지, 2f-7). `connect_timeout` 은 넣지 않는다(코드가 없을 때만 5초를 붙인다).
- 손으로 만들 때도 값은 stdin 으로: `printf 'username=dms\npassword=%s\n' "$(openssl rand -hex 24)" | kubectl --context C -n dms create secret
  generic dms-pg-app --type=kubernetes.io/basic-auth --from-env-file=/dev/stdin`(`printf` 는 셸 내장이라 argv 에 실리지 않는다).
- 시크릿은 api·controller 가 기동 때만 읽는다 — URL 을 바꾼 뒤 `kubectl --context C -n dms rollout restart deploy/dms-api deploy/dms-controller`.
- `set-url` 은 되돌림 URL 을 지킨다: 옛 값이 이미 이 클러스터의 `dms-pg-rw` 면(이관 뒤 재실행 — 비밀번호 교체 등)
  `dms-secrets-prev` 를 건드리지 않고, prev 에 지금과 다른 외부 URL 이 있으면 멈춘다(어느 쪽이 되돌림인지 확인한 뒤 `--overwrite-prev`).

**이관 순서**(외부 PG → 이미 떠 있는 `dms-pg`, 덤프·복원 — 테스트베드 실측 앱 정지 창 약 3.5분):

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
#    어느 모드든 PV 안의 옛 PGDATA 잔재 `pgdata_*`(CNPG 가 옮겨 둔 것 — 2f-5 재활용 런북)는 WARN
python3 deploy/pg-path-probe.py --context C --rendered $RD/platform/db-probe --mode precheck       # 0 PASS·3 WARN·1 FAIL·4 모름
# 3) 시크릿(2f-2)이 있는지 → Cluster
kubectl --context C -n dms get secret dms-pg-app >/dev/null
kubectl --context C apply --server-side --field-manager=dms-install --dry-run=server -k $RD/platform/db-cluster
kubectl --context C apply --server-side --field-manager=dms-install -k $RD/platform/db-cluster
```

4) **900초 폴링** — `kubectl wait --for=condition=Ready` 는 쓰지 않는다(인스턴스가 **모두** healthy 일 때만 서는 조건이라(테스트베드 실측 —
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
`kubectl --context C -n dms describe cluster dms-pg`·인스턴스 파드 이벤트). Available 여분 PV < 1 → WARN. 테스트베드 실측(NFS, 2026-11-03 — 프로덕션은 스토리지에 따라 다르다): 첫
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
| initdb Job 이 반복 실패 — 로그 `Error while checking for an existing data directory … stat …/pgdata: permission denied` | PV 디렉터리가 26:26 0700 이 아니다(S0 `--check`·사전 검사 — 테스트베드 음성 대조에서 본 문구). hostPath 는 fsGroup 을 적용하지 않는다 |
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
- **복구**(**같은 이름 `dms-pg`** 로 새로 만들고 `pg_restore` — 다운그레이드·제자리 복원에 기대지 않는다). 이름을 바꾸지 않는 것은 pg_dump CronJob
  (`PGHOST=dms-pg-rw`)·경로 프로브·`pg-secrets.py`·설치기·S12 가 모두 `dms-pg` 를 보기 때문이다 — 새 이름이면 매시간 덤프가 버린 옛 DB 를 「완료」로
  받는다. 같은 이름이어도 옛 데이터와 섞이지 않는다: Cluster 를 지우면 PVC 가 함께 지워지고(ownerReference) Released PV 는 claimRef 의 옛 UID 때문에
  새 PVC 에 붙지 않는다(옛 PGDATA 는 그 PV 디렉터리에 남는다 — 재활용 런북이 `.retired` 로 옮긴다).
  0. 잡을 세운다(api 가 살아 있을 때 — 포탈): 컨트롤 상태 「유지보수」(신규 제출 503)와 「drain」(stepper 정지)을 켜고, 도는 잡을 적어 둔다:
     `kubectl --context C -n dms get vcjob -L dms.io/job-id,dms.io/phase`(+ `get pods -l dms.io/job-id`). 끝나기를 기다리거나 요청 상세에서 취소한다 —
     api·controller 를 0 으로 줄여도 이미 제출된 vcjob(root 로 sync·rm 을 하는 파드)은 그대로 돌고 끝난다. DB 를 되돌리면 그 잡의 결과 기록이 사라진다.
  1. 쓰기를 멈춘다: `kubectl --context C -n dms get deploy dms-api dms-controller` 로 replicas 를 적고 `kubectl --context C -n dms scale deploy
     dms-api dms-controller --replicas=0` — 앱이 빈 새 DB 에 먼저 붙으면 migrate 가 스키마를 만들어 `pg_restore` 와 겹친다.
  2. 복원할 덤프를 고른다(`<SHARED_DIR>/dms/backup/pg/dms-<UTC>.dump` — 옛 DB 가 읽히고 손상 전이면 `create job --from=cronjob/dms-pg-dump` 로
     하나 더 뜨고, 잘못된 마이그레이션·삭제가 원인이면 그 전 시각의 것). 공용 스토리지를 잃었으면 밖 사본에서 되살린다(S0 → 2f-4 의 `db-volumes` 부터).
  3. `kubectl --context C -n dms delete cluster dms-pg` → PVC 가 지워지고(테스트베드 실측 약 10초) PV 3개가 Released. `kubectl --context C -n dms get pvc -l
     cnpg.io/cluster=dms-pg` 가 비어 있는지 본다 — 그래도 남은 PVC 가 있으면 지운다: 같은 이름의 새 Cluster 가 옛 PGDATA 째 다시 붙잡을 수
     있다(추정).
  4. 2f-4 3)의 `db-cluster` SSA(같은 렌더, `dms-pg-app` 그대로 — URL·비밀번호가 바뀌지 않는다) → 2f-4 4) 900초 폴링. 여분 PV 2개로 두 인스턴스가
     뜨고 셋째는 Pending 이다 — Released PV 에 아래 재활용 런북을 돌리면 뜬다(복원과 병행해도 된다).
  5. `P=$(kubectl --context C -n dms get cluster dms-pg -o jsonpath='{.status.currentPrimary}')` →
     `kubectl --context C -n dms exec -i "$P" -c postgres -- pg_restore --no-owner --role=dms --exit-on-error --single-transaction -d dms < <덤프>`
     (덤프는 stdin 으로 — 오류 하나면 통째로 되돌려 반쯤 복원된 DB 로 넘어가지 않는다. 실패하면 사유를 고치고 4 부터 다시).
  6. `kubectl --context C -n dms scale deploy dms-api dms-controller --replicas=<적은 값>`(migrate 는 no-op) → `python3 deploy/pg-secrets.py
     --context C check`(PASS — URL 은 그대로 `dms-pg-rw`) → 2f-4 6) 처럼 `create job --from=cronjob/dms-pg-dump` 로 새 DB 의 첫 덤프를 확인한다.
     6 의 scale 원복은 다음 7 의 순서(api 먼저)로 한다.
  7. 복원 뒤 정리:
     (a) 복원 DB 는 **덤프 시점의 컨트롤 상태**다(유지보수·drain 도 DB 값 — `control_state`). dms-api 만 먼저 올려 유지보수·drain 을 다시 켠 뒤
     dms-controller 를 올린다.
     (b) 0 에서 적은 vcjob 가운데 복원 DB 에 없는 잡 id(`SELECT job_id FROM data_jobs WHERE job_id = '<id>'` 가 빈 것)의 vcjob·파드는 DMS 가 지우지 않는다
     (`pod_gc.PodGarbageCollector._targets` 는 DB 에 있는 종단 잡의 `phase_refs` 만 본다. Volcano TTL 은 Completed 만 치운다 — `pod_gc` 모듈 docstring) →
     `kubectl --context C -n dms delete vcjob <이름>`(그 잡 id 의 `pod/dms-preflight-…` 도).
     (c) 덤프 시각 뒤에 컨펌·실행된 sync·rm 은 복원 DB 에서 그 전 상태(컨펌 대기 등)로 돌아와 있다 — 미리보기 시한 안이면 다시 컨펌할 수 있고
     (`routes_jobs.confirm_job` 은 만료·지문만 본다) 그러면 한 번 더 돈다. 해당 작업을 취소하고 사용자에게 알린 뒤 유지보수·drain 을 끈다.
  복원 조건: **원래의
  `DMS_SESSION_SECRET`**(없으면 메일 키·k8s 토큰을 다시 넣는다 — `mail_config.py`), DB 와 아티팩트의 시점이 어긋나면 404·고아 디렉터리가
  생긴다(`artifact_trash.py`). **첫 이관(외부 PG → CNPG)은 CNPG 「initdb import」**(설계 6-4): Cluster 를 `bootstrap.initdb.import`(type
  `microservice`, `databases: [<원본 DB>]`, `externalClusters` 에 원본 접속 — 비밀번호는 `secretKeyRef`)로 **만들 때** 원본을 읽어 들인다.
  bootstrap 은 Cluster 를 만들 때 한 번뿐이라 이미 initdb 로 뜬 Cluster 에는 쓸 수 없다 — 이관할 사이트는 S10 의 `db-cluster` 대신 import
  bootstrap 으로 만들거나(설치기에는 아직 없다 — BACKLOG 「⑦ 설치기 후속」; 이름은 그대로 `dms-pg`: 위 복구와 같은 이유), 이미 떠 있으면
  덤프·복원(2f-2 「이관 순서」)으로 같은 결과를 낸다.
- **대조**(복원·이관 뒤 — 원본과 대상에서 같은 SQL, 결과를 diff): 세션 표시 설정을 양쪽 같게 고정하고(`SET TimeZone='UTC'; SET DateStyle='ISO, MDY';
  SET IntervalStyle='postgres'; SET extra_float_digits=1; SET bytea_output='hex'` — 행 텍스트 표현이 같아야 해시가 같다) public 테이블마다
  `SELECT count(*), md5(coalesce(string_agg(h, '' ORDER BY h COLLATE "C"), '')) FROM (SELECT md5(t::text) AS h FROM public.<표> t) s`, 시퀀스는
  `SELECT sequencename, last_value FROM pg_sequences WHERE schemaname = 'public' ORDER BY 1`. **행 텍스트를 통째로 `string_agg` 하지 않는다** — 그
  백엔드가 테이블 크기만큼 메모리를 쓴다(테스트베드 실측: agent_reports 54MB → 대상 primary 백엔드 183MB, `PG_MEMORY` 256Mi cgroup OOM → 장애조치).
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
     데이터가 조용히 남는다(테스트베드 실측 — 설계의 「join 실패」 추정 정정). 그래서 이 단계를 건너뛰어도 드러나지 않는다: 경로 프로브가
     어느 모드든 `pgdata_*` 잔재를 WARN 하면 내용을 확인한 뒤 같은 FS 의 `pgdata/.retired/` 로 옮긴다. `.retired/` 의 옛 PGDATA 는 덤프 밖의
     유일한 사본일 수 있으니 보존 기한을 정해 지운다(용량 3-3).
  3. claimRef 를 지워 Available 로: `kubectl --context C patch pv dms-pgdata-0N --type=json -p '[{"op":"remove","path":"/spec/claimRef"}]'`
  - **Cluster 삭제**는 CNPG 가 PVC 를 함께 지운다(PVC 에 Cluster ownerReference — 테스트베드 실측: 약 10초 안에, 일회용 인스턴스 2·PV 3 에서
    PV 2개 Released + 1개 Available) → PV 3개가 Released 가 되고 여분은 2개뿐이라
    다시 만든 Cluster(같은 이름 `dms-pg` — 위 복구)의 셋째 인스턴스는 런북을 돌린 뒤에 뜬다. 옛 PGDATA 는 지우지 않고 `.retired` 로 옮겨 보관한다.

### 2f-6. 업그레이드 (설계 6-5 — 연 2회 이상)

| 경우 | 절차 |
|---|---|
| CNPG minor | ① `deploy/addons/cnpg/<새 v>/`(상류 원본 + SOURCE(cosign 검증)·sha256 + 패치 층 — 라벨·자리표시자 태그) · `deploy/versions.env` · `deploy/images.lock`(`images-lock.py resolve cloudnative-pg:<v> ghcr.io/cloudnative-pg/cloudnative-pg:<v>`)을 **한 커밋**에서, 옛 디렉터리는 지운다(테스트가 하나뿐인지 본다) ② 인터넷 노드가 새 이미지를 push(S5) ③ 시험 클러스터 리허설 ④ **minor 는 하나씩**(건너뛰기 금지)·지원 범위(K8s minor) 확인 → 직전 덤프(`create job --from=cronjob/dms-pg-dump`) → 렌더 → `guard-images.sh $RD/addons/cnpg` 가 exit 3 → `ALLOW_IMAGE_CHANGE=1` 로 SSA → 오퍼레이터 롤아웃 → PG 인스턴스 롤링·switchover 완료 대기(그동안 수 초 500). 설치기로는 `sh deploy/install.sh --context <ctx> --only S9 --upgrade-addon cnpg` — 직전 덤프 → SSA → 롤아웃 → healthy 대기를 한다(§4-3) |
| PG minor | `versions.env` 의 `PG_IMAGE` + lock 줄(날짜 붙은 새 태그) → push → 렌더 → `db-cluster`·`db-backup`·`addons/cnpg`(POSTGRES_IMAGE_NAME) SSA → CNPG 가 standby 부터 롤링하고 primary 는 switchover(`primaryUpdateMethod: switchover` — 새 이미지를 이미 받은 인스턴스가 승격된다). **imageName 과 `postgresql.parameters`(예: `PG_SHARED_BUFFERS`)를 한 번에 바꾸지 않는다** — switchover 모드에서는 웹훅이 거절한다(상류 rolling_update 문서): 이미지 먼저, 롤링이 끝난 뒤 설정 |
| PG major | 별도 설계(지금 없음) |
| K8s minor | 신·구 K8s 를 모두 지원하는 버전으로 애드온을 먼저 올린다(1.30.x = K8s 1.34~1.36). CRI-O minor 도 함께 |
| 남이 설치한 CNPG(C) | 업그레이드는 소유자 몫 — DMS 는 EOL·지원 범위를 경고만(감지 C! — 설치·verify 때마다) |
| 롤백 | 다운그레이드에 기대지 않는다 — 같은 이름 `dms-pg` 로 다시 만들고 `pg_restore`(2f-5 복구) |

`kubectl cnpg` 플러그인(switchover·destroy 등)은 인터넷 노드가 릴리스의 `kubectl-cnpg_<v>_linux_x86_64.tar.gz` 를 `cnpg-<v>-checksums.txt` 와
대조해 받아 설치 호스트로 옮긴다(수단은 K8). switchover = `kubectl cnpg promote dms-pg <인스턴스>`(플러그인 권장 — 플러그인이 하는 일은
Cluster `status.targetPrimary` 패치와 phase `Switchover in progress` 다: 플러그인이 없을 때 `kubectl --context C -n dms patch cluster dms-pg
--subresource=status --type=merge -p '{"status":{"targetPrimary":"dms-pg-2"}}'` — 이때 phase 는 바뀌지 않으니 `status.currentPrimary` 가
`targetPrimary` 와 같아질 때까지를 진행 중으로 본다). 테스트베드 실측: 둘 다 약 9초, 옛 primary 의 RESTARTS +1 은 정상.

### 2f-7. 마스터 운영 규칙(6-6)·장애조치 시간(6-3)

- **VM 전원이 꺼진 것을 확인하기 전에는** 파드 강제 삭제(`--force --grace-period=0`), `node.kubernetes.io/out-of-service` taint, Node 객체 삭제를
  하지 않는다 — 공용 경로 hostPath PV 는 k8s 가 노드 간 RWO 를 강제하지 않아, 옛 노드의 PG 가 살아 있으면 **같은 PGDATA 를 두 서버가 연다**(DB 손상,
  위험 #4).
- 마스터 교체: 옛 VM 을 끈 다음에 Node 를 지운다. drain 은 한 대씩, 다음 대로 넘어가기 전에 `kubectl --context C -n dms get cluster dms-pg` 가
  healthy(readyInstances 3)인지 본다. 그 마스터가 primary 면 먼저 switchover.
- 공용 스토리지 유지보수(3-2 G2 보충): 그 노드의 DMS 상태 파드(api·controller·registry·CNPG 인스턴스·pg_dump)를 먼저 비우고(primary 면 switchover
  뒤 cordon + 파드 삭제) → 재마운트 → uncordon.

| 장애조치 상황(테스트베드(NFS 공용 스토리지·VM 3대) 실측 2026-11-03 — 프로덕션은 스토리지·네트워크에 따라 다르다; 「추정」 표시만 추정) | 쓰기 불가 시간 |
|---|---|
| switchover·promote·drain(primary 노드) | 쓰기 공백 약 8~9초, API 5xx 4~7초(promote 5회 5~6초). drain 12초, uncordon 뒤 3/3 약 11~12초. `smartShutdownTimeout` 0 대 180 차이 없음 — drain 은 switchover 를 거쳐 smart 대기가 쓰이지 않는다(switchover 없는 종료는 시험 안 함) |
| PG 파드 재시작·노드 이동 | 수 초~십수 초(primary 면 그동안 API 500 — 추정). 인스턴스가 다른 노드에서 같은 PV 로 다시 뜨기까지 22초(cordon + 파드 삭제, pg_basebackup 없음) |
| primary 노드 사망(전원 차단) | 전원 차단부터 rw 전환까지 **77~79초**(unreachable +4초 → 장애조치 시작 +7초 → 새 primary +25초 → rw +30초 — 노드 판정을 기다린 뒤), API 78초 불가(처음 약 30초는 매달림). 노드가 돌아오면 옛 primary 는 pg_rewind 로 재합류(630MB — 약 2분에 3/3) |
| keepalive·`tcp_user_timeout` 없는 클라이언트(위 사망 때) | **15분 52초** 매달린 뒤 ETIMEDOUT — 2f-2 URL 의 값이 있으면 16초에 감지 |
| apiserver 불통(약 71초) | 영향 없음 — PG 쓰기 공백 0, API 5xx 0, PG 파드 재시작 없음(lease 를 쓰는 컨트롤러들은 재시작 — 정상) |
| primary 노드의 공용 스토리지 정지 | **정지 시간 + 8~22초**(해제 뒤 새 primary +8초·API 복귀 +14초·3/3 +23초; 93초 정지에 API 76초 불가) — **정지 동안 자동 장애조치가 끝나지 않는다**(CNPG 는 옛 primary 가 멈추기를 기다린다). 데이터 손실 없음(no rewind) |
| replica 노드 재마운트(3-2 G2 보충 절차) | 영향 없음 — 3/3 42초, API 오류 0 |
| dms-api 가 있던 마스터 사망 | 약 1.5~2.5분(추정) — 새 마스터에 dms 이미지가 캐시돼 있다는 전제(prod 오버레이의 not-ready·unreachable 30초 + S11 사전 pull). 레지스트리도 같은 마스터였으면 수 분 더(7-6) |

- **primary 노드의 스토리지만 멈췄을 때**: Cluster 가 primary NotReady·「initiating a failover」인데 새 primary 가 서지 않으면 이 경우다 —
  스토리지가 돌아오면 저절로 끝난다(쌓인 커밋 flush → 승격). 길어지면 사람이 조치한다: 그 노드를 **전원 차단으로 확정**한 뒤에만 노드 사망 경로로
  넘긴다(위 6-6 — 꺼진 것을 확인하기 전에는 강제 삭제·out-of-service taint 금지). 이 「정지 중 전원 차단」 경로는 아직 시험하지 않았다(BACKLOG ⑤).
  지연·쓰기 확인은 durable 쓰기로 잰다(`select pg_logical_emit_message(true, 'probe', now()::text)` — `txid_current()`·`select now()` 는 WAL 을
  디스크에 쓰지 않아 정지 중에도 성공한다).

dms-controller 는 DB 연결을 잃으면 exit 1 로 끝나 재시작한다(정상 — 테스트베드 실측: 노드 사망 때 DB 복귀 약 16초 뒤에 붙었다). 짧은 사이에 거듭
잃으면 kubelet 재시작 백오프가 쌓여 복귀가 DB 보다 늦을 수 있다.
keepalive·`tcp_user_timeout`(2f-2 URL)이 없으면 DB 연결이 약 16분 매달린다(위 표). prod 오버레이는 dms-api·dms-controller 를 마스터에
둔다(`deploy/overlays/prod/patch-placement.yaml` — nodeSelector control-plane + toleration + not-ready·unreachable `tolerationSeconds: 30`, #24·#25).

## 2g. 입구(Traefik) — 클러스터의 Traefik·LB VS-443·PROXY v2·S8·검증·운영 (2026-11-16)

사용자 결정(2026-11-16): Envoy Gateway 입구를 폐기했다(병행 모드 없음). **prod 의 포탈 입구는 클러스터에 이미 깔린 Traefik 하나다** — RKE2 는
`rke2-traefik`(kube-system DaemonSet, 모든 노드에 hostPort 80 → 진입점 `web`(:8000)·443 → `websecure`(:8443)). **DMS 는 Traefik 을 설치·변경하지
않고 Ingress 하나만 만든다** — Traefik 의 설치·정적 설정(PROXY v2 신뢰 출발지·타임아웃)·업그레이드는 클러스터 운영자 몫이다. MetalLB·ingress-nginx
도 쓰지 않는다(prod 오버레이가 공용 base 의 Ingress `dms-portal`·MetalLB 풀을 `$patch: delete` 로 지운다 — `deploy/overlays/prod/patch-remove-ingress.yaml`;
DMS 의 Ingress 는 이름이 달라 겹치지 않는다). 설치기(§2h)의 S1(preflight 「2. 입구」)·S8·S12 가 아래를 한다 — 아래 손 명령은 같은 일의 조사·복구용
판이다. 테스트베드의 입구(호스트 edge = LB 흉내 → testbed IaC 가 깐 Traefik)는 `deploy/testbed/README.md` §2·§4-8(되돌림 입구 §7-3).

```
사내 PC ─https://<FQDN>─▶ LB VS-443(VIP-A, L4 TCP — PROXY v2 는 사이트 선택)
   ─▶ Traefik 이 도는 노드 전부 :443(hostPort → 진입점 websecure — TLS 종단, 인증서는 SNI 로 고른다)
   ─▶ Ingress dms/dms-portal-traefik(Host = <FQDN>, 진입점 websecure 만) ─▶ svc dms-api:8080
        LB 가 PROXY v2 를 붙이면 websecure 의 proxyProtocol.trustedIPs = LB 출발지(사이트의 Traefik 설정 — 2g-2)
        앱은 XFF 마지막 항목(= Traefik 이 덧붙인 원 IP)을 기록한다(2g-5)
```

| 하위 절 | 내용 |
|---|---|
| 2g-1 | 구성·값 — DMS 가 만드는 것(Ingress 하나)·values 키·렌더 출력 |
| 2g-2 | 클러스터의 Traefik — 사이트(클러스터 운영자) 선결·Traefik 이 보장하는 것과 못 하는 것·앱이 대신 하는 것 |
| 2g-3 | 설치(S8) — 감지 → TLS 게이트 → SSA → ingress-probe |
| 2g-4 | LB VS-443·PROXY v2 |
| 2g-5 | 검증(S12)·원 IP |
| 2g-6 | 운영·문제 해결 |
| 2g-8 | Envoy Gateway 입구에서 옮기기 — 전환·Envoy 정리 런북(옛 판을 설치한 사이트만, 1회) |

2g-7 은 비워 둔 번호다(옛 k8s 테스트베드 입구 절). 옛 Envoy 판 하위 절(2g-1~2g-6)이 어디로 갔는지는 부록 Z.

### 2g-1. 구성·값 — DMS 가 만드는 것(Ingress 하나)

| 무엇 | 어디(base → 렌더) | 비고 |
|---|---|---|
| Ingress `dms-portal-traefik`(ns dms) — **DMS 가 만드는 유일한 입구 객체** | `deploy/platform/ingress`(사이트 중립 — host 는 자리표시자 `portal.set-by-overlay.invalid`) → `deploy/.rendered/<site>/platform/ingress`(base 를 가리키는 kustomization + JSON 패치 다섯 칸) | S8 이 SSA(`--field-manager=dms-install`). 워크로드가 없어 `guard-images.sh` 대상이 아니다. 계약 `tests/test_ingress_contract.py` |
| Traefik(DaemonSet·IngressClass·CRD·Service·정적 설정) | 클러스터(RKE2 `rke2-traefik`) | **DMS 는 만들지도 바꾸지도 않는다** — 버전 고정·`images.lock` 줄이 없다. S1·S8 은 읽기만 한다(2g-2) |
| 쓰지 않는 것 | — | Middleware·ServersTransport·IngressRoute(kubernetescrd 에 기대지 않는다 — Middleware·ServersTransport 에는 ingressClass 필터가 걸리지 않아 공용 Traefik 에서 DMS 가 만든 정책이 다른 팀 라우터에 섞일 수 있다), host 없는 규칙(catch-all) |

Ingress 모양(`deploy/platform/ingress/ingress.yaml` — 렌더가 바꾸는 칸은 values 키로 적었다):

| 칸 | 값 | 이유 |
|---|---|---|
| `metadata` | name `dms-portal-traefik`, 라벨 `app.kubernetes.io/name: dms-portal-traefik`·`app.kubernetes.io/part-of: dms`·`app.kubernetes.io/managed-by: dms-install` | 감지 항목 `ingress` 가 이 이름·라벨로 A·D=·P 를 가른다. base 앱 `46-ingress` 의 `dms-portal`(class nginx — 테스트베드 되돌림 경로, prod 는 지운다)과 이름이 달라 한 클러스터에 함께 있어도 서로 덮지 않는다 |
| 어노테이션 `traefik.ingress.kubernetes.io/router.entrypoints` | `TRAEFIK_ENTRYPOINT`(기본 `websecure`) | 없으면 Traefik 라우터가 **모든** 진입점에 붙어 :80(web) 평문으로도 포탈이 나간다 — Secure 쿠키 로그인이 소리 없이 깨진다 |
| 어노테이션 `traefik.ingress.kubernetes.io/router.tls` | `"true"` | 진입점에 `http.tls` 가 없어도 이 라우터의 TLS 를 켠다(`spec.tls` 만으로는 라우터 TLS 가 켜지지 않는다) |
| `spec.ingressClassName` | `INGRESS_CLASS`(기본 `traefik`) | 공용 Traefik 은 자기 클래스의 Ingress 만 집는다. 그 IngressClass 의 controller 는 `traefik.io/ingress-controller` 여야 한다(S1·S8) |
| `spec.tls` | hosts `[PORTAL_DOMAIN]`, secretName `PORTAL_TLS_SECRET` | Traefik 은 class 가 맞는 Ingress 와 **같은 ns** 의 tls 시크릿만 읽고, ClientHello 의 SNI 와 인증서 CN/SAN 을 대조해 고른다 |
| `spec.rules` | host `PORTAL_DOMAIN`, `/`(Prefix) → svc `dms-api` 포트 이름 `http`(8080) | host 를 늘 둔다 — host 없는 규칙은 공용 Traefik 에서 다른 팀의 미매칭 호스트를 DMS 가 가로챈다 |

| values.env 키 | 기본 | 뜻 |
|---|---|---|
| `PORTAL_DOMAIN` | (**필수** — 템플릿·고정 오버레이 모두) | 포탈 FQDN = Ingress host·tls hosts. 소문자 호스트 이름만 — 와일드카드(`*`)·`:`(포트·스킴·IPv6)·경로·대문자·IP 는 렌더 FAIL(사유를 따로 말한다). 인증서 SAN·S8 ingress-probe·S12 `curl --resolve` 의 기준이기도 하다 |
| `PORTAL_TLS_SECRET` | `dms-portal-tls` | Ingress 인증서 Secret(ns dms, `kubernetes.io/tls`, SAN = `<FQDN>` + `<VIP-A>` — 보안 S3). S2 가 없으면 알리고, S8 이 SSA 직전에 없거나 type 이 다르면 멈춘다(2g-3) |
| `INGRESS_CLASS` | `traefik` | `spec.ingressClassName`(DNS-1123 label, 63자 이하). 확인: `kubectl --context C get ingressclass` |
| `TRAEFIK_ENTRYPOINT` | `websecure` | `router.entrypoints` — TLS 진입점 이름(소문자·숫자·`-`, 영숫자로 시작, 63자 이하). 확인: Traefik 워크로드 args 의 `--entryPoints.<이름>.address`(S8 이 본다) |
| `PORTAL_VIP` | (템플릿 필수, 고정 오버레이 선택) | 매니페스트에 들어가지 않는다 — `render.env` 로만 가서 인증서 SAN 과 S12 `curl --resolve <FQDN>:443:<VIP-A>` 의 기준이 된다 |
| 옛 `GATEWAY_PROXY_PROTOCOL`·`GATEWAY_LISTEN_PORT`·`GATEWAY_ENVOY_CPU_REQUEST`·`GATEWAY_ENVOY_MEMORY_REQUEST` | — | 2026-11-16 에 없앴다. values.env 에 남아 있어도 렌더는 실패하지 않고 stderr 에 `note: values.env 의 … 는 더 쓰지 않는다(2026-11-16 — 입구는 클러스터의 Traefik) — 무시함(줄을 지워도 된다; …)` 한 줄을 낸 뒤 무시한다 |

입구 키는 셸 env 를 읽지 않는다(렌더가 unset — values.env 만이 원천; S8·S12 의 env `PORTAL_DOMAIN`·`PORTAL_VIP` 는 설치기의 확인 대상만 바꾼다).
렌더러 출력: `INGRESS: <렌더>/platform/ingress` 한 줄(S8 층), `render.env` 의 `INGRESS_LAYERS="platform/ingress"`·`INGRESS_CLASS`·`TRAEFIK_ENTRYPOINT`·
`PORTAL_TLS_SECRET`·`PORTAL_DOMAIN`·`PORTAL_VIP`. 오버레이에 `kube-context` 파일이 있으면 `platform/ingress` 에도 복사한다.

> 예(테스트베드): `PORTAL_DOMAIN=dms-k8s.local`·`PORTAL_TLS_SECRET=dms-portal-tls`, `INGRESS_CLASS`·`TRAEFIK_ENTRYPOINT` 는 기본값, `PORTAL_VIP` 없음(고정 오버레이) — `deploy/overlays/testbed-k8s/values.env`.

### 2g-2. 클러스터의 Traefik — 사이트 선결·보장하는 것·앱이 대신 하는 것

**사이트(클러스터 운영자) 선결** — DMS 는 바꾸지 않고 확인만 한다(S1 preflight 「2. 입구」 = IngressClass·Traefik 파드 Ready 수, S8 감지 항목
`traefik` = 아래 args — `deploy/install-detect.py` `detect_traefik`):

| 항목 | 요건 | DMS 가 보는 곳 | 어긋나면 |
|---|---|---|---|
| Traefik | 클러스터에서 돈다(RKE2 는 `rke2-traefik` — RKE2 v1.36 부터 새 클러스터 기본). IngressClass `<INGRESS_CLASS>` 의 `spec.controller` = `traefik.io/ingress-controller` | IngressClass + 아무 ns 의 DaemonSet·Deployment 가운데 컨테이너 이미지 이름(저장소 경로의 마지막 조각)에 `traefik` 이 든 워크로드의 Ready 파드 | IngressClass 없음·controller 다름 → X(S1 FAIL·S8 멈춤). 워크로드를 못 찾음·Ready 0 → U(S8 ingress-probe 가 최종 판정) |
| Ingress 공급자 | `--providers.kubernetesingress` 켬. `providers.kubernetesingress.ingressClass` 로 좁혔으면 `INGRESS_CLASS` 와 같은 이름 | 워크로드 args(플래그는 대소문자 무시) | X |
| TLS 진입점 | `--entryPoints.<TRAEFIK_ENTRYPOINT>.address`(RKE2 `websecure` = `:8443`, hostPort 443). `http.tls` 는 있으면 좋다 | args | 진입점 없음 → X. `http.tls` 없음 → note(DMS Ingress 의 `router.tls` 가 켠다) |
| PROXY v2 신뢰 출발지 | **LB 가 PROXY v2 를 붙이면** websecure 의 `proxyProtocol.trustedIPs` = LB 출발지(SE 주소)만 — 넓은 대역을 넣지 않는다. LB 가 붙이지 않으면 비워 둔다 | args `--entryPoints.<EP>.proxyProtocol.trustedIPs` | 없으면 WARN(C!) 「LB 가 PROXY v2 를 붙이면 trustedIPs 에 LB 출발지(SE)가 있어야 한다(없으면 PROXY 바이트가 TLS 앞에 붙어 연결이 깨진다)」. `proxyProtocol.insecure=true` 도 WARN(아무 출발지의 헤더를 믿는다 — 원 IP 위조 가능) |
| 전달 헤더 | `forwardedHeaders` 는 **비워 둔다**(trustedIPs·insecure 없음 — 기본) | args | `insecure=true`·trustedIPs 는 note(앱이 기록하는 원 IP 의 근거가 바뀐다 — 아래 「보장」) |
| 정적 설정의 자리 | args(차트·RKE2 는 args 로 준다) | — | `--configFile`·env 로 주면 판정하지 않고 WARN(C!) — 위 항목을 소유자에게 확인 |

RKE2 에서 trustedIPs 넣기(사이트 몫 — 차트 values 의 키 `ports.websecure.proxyProtocol.trustedIPs`. 차트는 값이 비어 있지 않을 때만 플래그를 만든다):

- **Rancher 가 프로비저닝한 클러스터**: Rancher 클러스터 편집 → 클러스터 YAML 의 `spec.rkeConfig.chartValues` 에서 `rke2-traefik` 아래에 같은 values
  트리를 넣는다. Rancher 가 그 값을 HelmChartConfig 로 바꿔 쓰므로 같은 이름의 HelmChartConfig 를 따로 두면 덮일 수 있다(미검증 — Rancher 공식
  문서가 아니다. Rancher 관리 클러스터는 chartValues 로만 고친다).
- **독립 RKE2**: HelmChartConfig 를 서버 노드의 `/var/lib/rancher/rke2/server/manifests/rke2-traefik-config.yaml` 에 두거나 apply 한다(RKE2 공식 문서의 모양):

```yaml
apiVersion: helm.cattle.io/v1
kind: HelmChartConfig
metadata:
  name: rke2-traefik
  namespace: kube-system
spec:
  valuesContent: |-
    ports:
      websecure:
        proxyProtocol:
          trustedIPs:
            - "<SE 주소 1>/32"
            - "<SE 주소 2>/32"
    # forwardedHeaders 는 두지 않는다(비움 — Traefik 이 클라이언트가 보낸 X-Forwarded-*·X-Real-Ip 를 지운다)
```

```yaml
# Rancher chartValues 판(클러스터 YAML spec.rkeConfig.chartValues) — 같은 트리
rke2-traefik:
  ports:
    websecure:
      proxyProtocol:
        trustedIPs: ["<SE 주소 1>/32", "<SE 주소 2>/32"]
```

값을 바꾸면 Traefik DaemonSet 템플릿이 바뀌어 노드마다 롤링 재기동한다(2g-6 「롤링 공백」). 적용 확인:

```bash
kubectl --context C get ds,deploy -A -o wide | grep -i traefik          # Traefik 워크로드(예: RKE2 kube-system/rke2-traefik DaemonSet)
kubectl --context C -n <Traefik ns> get ds <Traefik DaemonSet> -o 'jsonpath={range .spec.template.spec.containers[0].args[*]}{@}{"\n"}{end}' \
  | grep -i -E 'kubernetesingress|websecure|proxyprotocol|forwardedheaders|readtimeout'
sh deploy/install.sh --context C --site <site> --plan    # 감지 traefik 줄의 note(proxyProtocol.trustedIPs=… · readTimeout …)·WARN(trustedIPs 없음)
```

> 예(OA): Avi SE `12.201.53.242/32`·`12.201.53.243/32` 를 Rancher chartValues 의 `rke2-traefik` 에 넣는다(forwardedHeaders 는 비움).
>
> 예(테스트베드): testbed IaC 가 rke2-traefik 모양의 `kube-system/traefik` DaemonSet 을 깔고 trustedIPs = `10.10.10.1/32`(호스트 edge 의 출발지), forwardedHeaders 비움 — `deploy/testbed/README.md` §2.

**Traefik 이 보장하는 것**(v3.7.4 소스·바이너리 확인 + 테스트베드 실측, 2026-11-16 — 이 설계의 근거):

| 동작 | 보장 | 테스트베드 실측 |
|---|---|---|
| PROXY v2 | trustedIPs 출발지의 연결은 PROXY 헤더가 있으면 그 원 IP 를 쓴다. trustedIPs **밖** 출발지의 PROXY 헤더는 읽고 버린다(실제 TCP 피어를 쓴다 — 원 IP 위조 불가) | 신뢰 출발지 + PROXY 원 IP `192.0.2.7` → 앱 client_ip `192.0.2.7`, 비신뢰 출발지 + PROXY `6.6.6.6` → 실제 피어 |
| LB 경유 이중 PROXY | 클라이언트가 LB 를 거치며 자기 PROXY 헤더를 심으면 LB 의 진짜 헤더가 먼저 가고, 심은 바이트는 TLS 자리에 놓여 핸드셰이크가 깨진다(막힘) | `SSL WRONG_VERSION_NUMBER` |
| 전달 헤더 | `forwardedHeaders.trustedIPs` 가 비면 클라이언트가 보낸 `X-Forwarded-*`·`X-Real-Ip`(밑줄 변형 포함)를 지우고 `X-Real-Ip` = 원 IP, XFF 끝에 원 IP 를 덧붙인다 — 앱(`auth.client_ip` = XFF 마지막 항목)은 앱 코드를 바꾸지 않고 원 IP 를 본다 | 위조 `X-Forwarded-For: 7.7.7.7`·`X-Real-Ip: 6.6.6.6` 반영 0 |
| 타임아웃 | 진입점 `readTimeout` 60s(기본)는 **요청 본문 읽기**에만 — 60초 넘게 걸리는 느린 업로드만 끊긴다. `writeTimeout` 0·상류 `responseHeaderTimeout` 0 이라 긴 다운로드·스트리밍은 끊지 않는다 | 1.18MB 를 15.7KB/s 로 65.7초 — 200·전량 |
| 라우팅 | Host 매처는 Host 의 포트를 뗀다(`<FQDN>:8443` 도 맞는다). 모르는 SNI·IP 직접 접속(SNI 없음)은 기본 인증서(`CN = TRAEFIK DEFAULT CERT`, 자가서명) + `404 page not found`. `router.entrypoints` 로 :80 평문에는 붙지 않는다 | 모르는 SNI → 404 + 기본 인증서, `http://<FQDN>`(노드 :80) → 404, Host 에 포트 → 200 |

**못 하는 것**:
- **PROXY 「필수」를 강제하지 못한다** — 신뢰 출발지에서 헤더 없이 온 연결도 받는다(그 연결의 원 IP 는 출발지 = LB 주소로 남는다). 옛 Envoy 의
  `required`·컷오버 단계에 해당하는 것이 없다. 그래서 trustedIPs 에는 LB 출발지만 넣는다. 거꾸로 LB 의 헬스 모니터(헤더 없음)도 받는다(2g-4).
- 요청 본문 상한이 기본으로 없다. 상한을 주는 Buffering 미들웨어는 응답 버퍼를 끌 수 없어(포탈 전체 경로에 걸면 큰 결과 다운로드·내보내기 스트림이
  Traefik 에 다 쌓인 뒤에야 나간다) 쓰지 않는다 → 앱이 낸다(아래).
- 상류 유휴 연결은 Traefik 기본(`idleConnTimeout` 90s)대로다 — DMS 는 ServersTransport 를 만들지 않는다 → 앱 keep-alive 를 그보다 길게 둔다(아래).

**앱이 대신 하는 것**(옛 Envoy BackendTrafficPolicy 자리 — `src/dms/api/body_limit.py`·`src/dms/config.py`·`src/dms/cli.py`). 둘 다 `dms-config`
튜닝 키다 — base `20-config.yaml` 에 일부러 두지 않았고(코드 기본이 계약), 바꾸려면 사이트 `patch-config.yaml` 에 더한다(§3-4 (b) → `--only S11`,
dms-api 재시작):

| 키 | 기본(하한 — 밑이면 기동 거부) | 하는 일 |
|---|---|---|
| `DMS_MAX_REQUEST_BODY_BYTES` | `20971520`(20MiB — 옛 입구 값, 하한 1MiB) | `Content-Length` 가 상한을 넘으면 앱(인증·라우팅)보다 먼저 413, 길이가 없거나(chunked) 작게 선언돼도 받은 바이트를 세어 넘는 순간 413 — 응답 `{"detail": "request_too_large"}`(포탈 문구 「요청 본문이 서버 상한(기본 20 MiB)을 넘습니다 — 배치는 여러 개로 나눠 제출하세요」). 결과 파일 다운로드 **응답** 상한인 413 `artifact_too_large` 와 다른 코드다. **입구 경유뿐 아니라 dms-api 에 닿는 모든 HTTP 요청에 걸린다 — 클러스터 안 에이전트 요청 포함**(Envoy 때는 입구 경유만). 예외는 스스로 더 큰 상한을 재는 k8s 동기화 결과 POST(`/api/agent/k8s-sync/runs/{run_id}` — `DMS_K8S_SYNC_MAX_BYTES` + 1MiB, 기본 33MiB, 넘으면 413 `k8s_sync_too_large`) 하나다 |
| `DMS_API_KEEP_ALIVE_SECONDS` | `120`(하한 1) | uvicorn `timeout_keep_alive`. **Traefik 상류 `idleConnTimeout`(기본 90s)보다 길어야 한다** — 짧으면 백엔드가 Traefik 풀의 유휴 연결을 먼저 닫고, 막 닫힌 연결에 실린 본문 있는 POST 를 Traefik(Go Transport)이 재시도하지 않아 간헐 502 가 난다(본문 없는 GET 은 자동 재시도). 사이트가 `idleConnTimeout` 을 120s 이상으로 올리면 이 값을 그보다 크게 |

테스트베드 실측: ≈21MiB POST → 413 `request_too_large`, ≈19MiB → 상한 통과(앱이 본문을 읽고 422). 본문 있는 POST 40회를 5.5초 간격(옛 uvicorn
keep-alive 5초 바로 뒤)으로 → 502 0.

### 2g-3. 설치(S8) — 감지 → TLS 게이트 → SSA → ingress-probe

`install.sh` S8 「입구(Traefik)」(`stage_S8`)는 넷을 차례로 한다:

**1. 감지**(`install-detect.py detect` — 처음 부른 단계에서 한 번. 전체 실행이면 S1 이 이미 돌렸다):

| 항목 | 코드 → 동작 | 뜻 · S8 |
|---|---|---|
| `traefik` | `C` → reuse | IngressClass·워크로드·args(2g-2)가 맞고 Ready ≥ 1 — 건드리지 않는다(`[INFO] Traefik: C reuse — 건드리지 않는다(DMS 는 Ingress 만 만든다)`) |
| `traefik` | `C!` → reuse + WARN | 위와 같은데 WARN 이 있다(trustedIPs 없음·`proxyProtocol.insecure`·args 밖 정적 설정·Ingress 를 처리하는 Traefik 이 둘 이상) — 설치는 계속하고 소유자에게 알린다 |
| `traefik` | `X` → stop | IngressClass 없음·controller 다름·`providers.kubernetesingress` 없음(또는 다른 ingressClass)·진입점 없음 — **멈춘다**: `Traefik 판정 X — <사유> — DMS 는 Traefik 을 설치·변경하지 않는다(클러스터 운영자에게 — RKE2 는 rke2-traefik, deploy/README §2g)`. traefik 에는 A(설치)가 없다 |
| `traefik` | `U` → verify | 워크로드를 못 찾음·Ready 0 — WARN. 최종 판정은 4 의 ingress-probe 다(Ingress 의 server dry-run 은 Traefik 을 부르지 않는다) |
| `ingress` | `A` → install | Ingress `dms/dms-portal-traefik` 없음 — 만든다 |
| `ingress` | `D=` → reapply | DMS 라벨 — SSA 멱등 재적용(사이트 값 변경 반영 — class·host 가 렌더와 달라도 SSA 가 맞춘다) |
| `ingress` | `P` → stop | 같은 이름에 DMS 라벨이 없다(이름 충돌) — `--adopt ingress` 로만 채택 |

`--upgrade-addon traefik` 은 판정만 한다(남의 것). 옛 항목 이름 `gateway-api`·`envoy-gateway`·`gateway` 는 사용법 오류다(exit 2 — 바뀐 이름을 알려 준다).

**2. TLS 게이트**: `PORTAL_TLS_SECRET`(ns dms)의 type 이 `kubernetes.io/tls` 여야 한다(없으면 Traefik 은 기본 인증서로 답한다). 없거나 type 이 다르면 SSA
전에 멈추고 만드는 명령(`kubectl --context C -n dms create secret tls <PORTAL_TLS_SECRET> --cert=<tls.crt> --key=<tls.key>` — SAN: PORTAL_DOMAIN + PORTAL_VIP)을
보인다. S2 가 미리 WARN 으로 알린다.

**3. SSA** `platform/ingress` — 감지 동작이 install·reapply·adopt 일 때만(adopt 면 `--force-conflicts`).

**4. ingress-probe** — `deploy/ingress-probe.sh` 가 Traefik 이 도는 노드마다 설치 호스트에서 :443 으로 붙어 인증서와 readyz 를 본다. dms-api
Endpoints 가 비었으면(첫 설치 — 앱은 S11) `--tls-only` 를 붙인다(인증서만 — readyz 는 S12 가 본다).

```
sh deploy/ingress-probe.sh --context <ctx> --domain <FQDN> [--nodes ip,…] [--port 443] [--timeout 30] [--cacert <사내CA>] [--tls-only]
```

| 옵션·env | 뜻 |
|---|---|
| 대상 노드(기본) | Traefik 파드(Running, 이미지 이름에 `traefik` — 감지와 같은 기준)의 hostIP. 못 읽거나 없으면 노드 InternalIP 전부. `--nodes ip,…` 를 주면 그 IP 만(파드·노드를 읽지 않는다) |
| `--port` | 기본 443(Traefik hostPort = LB 풀 멤버 포트) |
| `--timeout` | FAIL 인 노드를 다시 재는 총 시간(초, 기본 30 — Traefik 이 막 적용된 Ingress·시크릿을 집는 데 몇 초 걸린다). 0 = 한 번. 연결 실패(UNKNOWN)는 다시 재지 않는다. 다시 재는 간격은 env `PROBE_INTERVAL`(기본 2초) |
| `--cacert` | 주면 curl 이 체인·호스트 이름까지 검증한다. 없으면 curl 은 `-k` 로 응답만 받고, 인증서 이름은 openssl(`s_client -servername <FQDN>`)이 CN/SAN 과 대조한다 |
| `--tls-only` | 인증서만 판정한다(readyz 코드는 근거로만 적는다) |

| 노드 결과 | 판정 |
|---|---|
| readyz 200 + 인증서 OK(openssl 이 CN/SAN 에서 FQDN 을 찾음 — 와일드카드 한 단계 포함, 또는 `--cacert` 검증 통과) | PASS |
| 인증서 주체가 `TRAEFIK DEFAULT CERT` | FAIL — Traefik 에 SNI `<FQDN>` 의 인증서가 없다(tls 시크릿·ns·ingressClassName) |
| 다른 인증서(CN/SAN 에 FQDN 이 없다) | FAIL — `PORTAL_TLS_SECRET` 의 인증서·SNI |
| readyz 404 | FAIL — Traefik 이 Ingress 를 안 집었다(ingressClassName·router.entrypoints·ns) 또는 백엔드 dms-api 가 없다 |
| readyz 그 밖 코드(5xx 등) | FAIL — 라우터는 있는데 백엔드가 그 코드로 답한다 |
| `--cacert` 를 줬는데 TLS 검증 실패(curl rc 51·58·60·77·83) | FAIL — 그 CA 가 이 인증서를 서명하지 않았거나 중간 인증서가 빠졌다 |
| 연결 실패(000) | UNKNOWN — 설치 호스트 → 노드 :443 이 망에서 막혔거나 그 노드에 Traefik 이 없다 |
| 응답은 받았는데 인증서를 확인하지 못함(openssl 없음 / s_client 응답 없음 — 근거에 둘을 가른다, `--cacert` 없음) | UNKNOWN |

출력은 노드마다 `NODE <이름|ip> <PASS|FAIL|UNKNOWN> <ip>:<포트> <근거>`, 마지막 줄 `RESULT: …`. 종료는 0 PASS · 1 FAIL(노드 하나라도) · 4 UNKNOWN
(FAIL 없음 — 모름 ≠ 실패) · 2 사용법·환경이다. kubectl 은 `--context` 를 붙인 get 만 부르고 파드를 띄우지 않는다. 사내 프록시를 타지 않는다
(`--noproxy '*'`). 설치 호스트는 PROXY 헤더 없이 붙지만 Traefik 은 받는다(비신뢰 출발지 = 헤더 무시·없어도 받음) — 그래서 노드 직접 확인이 된다.
S8 은 rc 0 → `[OK] Traefik 경유 PASS — …`, 1 → 멈춤(`Traefik 경유 FAIL(위 NODE 줄) — …`), **4 → WARN 후 계속**(`Traefik 경유 모름(…). 설치는 계속한다 —
S12 의 VIP 경유 readyz 가 다시 본다`), 그 밖 → 환경 오류로 본다.

> 예(테스트베드): `NODE k8s-w1 PASS 10.10.10.21:443 readyz 200 · 인증서 OK(dms-k8s.local)` — 노드 셋 PASS(`deploy/testbed/README.md` §4-8).

손 판(설치기와 같은 순서):

```bash
sh deploy/render.sh <site> --context C
RD=deploy/.rendered/<site>
kubectl --context C get ingressclass <INGRESS_CLASS> -o 'jsonpath={.spec.controller}{"\n"}'   # traefik.io/ingress-controller
kubectl --context C get ds,deploy -A -o wide | grep -i traefik                                # Traefik 워크로드·Ready(args 는 2g-2)
kubectl --context C -n dms get secret <PORTAL_TLS_SECRET> -o 'jsonpath={.type}{"\n"}'          # kubernetes.io/tls
kubectl --context C apply --server-side --field-manager=dms-install --dry-run=server -k $RD/platform/ingress
kubectl --context C apply --server-side --field-manager=dms-install -k $RD/platform/ingress
kubectl --context C -n dms get ingress dms-portal-traefik                                     # CLASS <INGRESS_CLASS> · HOSTS <FQDN>
sh deploy/ingress-probe.sh --context C --domain <FQDN> --cacert <사내CA>                       # 노드마다 PASS
```

### 2g-4. LB VS-443·PROXY v2

LB(사이트 LB 담당 몫 — DMS 는 값만 준다. S13 체크리스트의 「LB VS-443」 줄과 같다):

| 항목 | 값 |
|---|---|
| VS | VIP-A **:443 하나**, L4 TCP(TLS 는 Traefik 이 종단 — LB 는 바이트만 넘긴다). 80 VS 는 만들지 않는다(만들어도 Traefik web 진입점은 DMS 에 404 다 — HTTP → HTTPS 리디렉트는 없다. 사용자 안내는 `https://<FQDN>` 하나) |
| 애플리케이션 프로필 | **443 전용** L4 프로필. PROXY protocol v2 는 사이트 선택 — 켜면 Traefik 의 trustedIPs(2g-2)가 짝이다(아래 순서). 6443(apiserver) VS 의 프로필과 공유하지 않는다(공용 프로필에 PROXY 를 켜면 6443 에도 붙어 apiserver 가 끊긴다) |
| 풀 | **Traefik 이 도는 노드 전부 :443**(노드 IP — hostPort). RKE2 `rke2-traefik` 은 DaemonSet 이라 모든 노드다(RKE2 마스터는 taint 가 없어 마스터도 든다). 목록은 S1 preflight 의 `[INFO] Traefik 파드 Ready N/M (노드: …) — LB VS-443 풀 = 이 노드들의 :443` 줄이나 `kubectl --context C get pods -A -o wide \| grep -i traefik` |
| 헬스 모니터 | TCP 443, 또는 HTTPS `GET /readyz`(200)에 **SNI·Host = `<FQDN>`**. SNI 없는 HTTPS 모니터는 기본 인증서 + 404 라 DOWN 이다. PROXY v2 를 켠 VS 에서도 HTTPS 모니터가 된다 — Traefik 은 신뢰 출발지의 헤더 없는 연결도 받는다(옛 Envoy `required` 와 다르다). `/readyz` 는 dms-api·DB 까지 보므로 앱·DB 장애 때 모든 멤버가 함께 DOWN 이 된다(노드를 가르는 것은 Traefik 생존뿐 — 노드 판별만 원하면 TCP 443) |
| 유휴 타임아웃 | 3600초 이상 — 긴 다운로드·내보내기 스트림을 LB 가 먼저 끊지 않게(Traefik 쪽은 응답 시간 상한이 없다 — writeTimeout 0) |
| 방화벽 | 노드 :443 은 hostPort — CNI(portmap)·CRI 가 거는 DNAT(PREROUTING → FORWARD)라 **노드의 INPUT 방화벽(firewalld 등)에 걸리지 않고** `ss` 에도 보이지 않는다. 출발지를 좁히려면 망 방화벽이나 CNI 정책에서 한다. 원 IP 위조를 막는 것은 방화벽이 아니라 trustedIPs 다(비신뢰 출발지의 PROXY 헤더는 버린다 — 2g-2) |

**PROXY v2 를 켜고 끄는 순서**: 켤 때는 Traefik trustedIPs(2g-2 — 롤링이 끝날 때까지) → LB 프로필의 PROXY v2. 끌 때는 반대로 LB 먼저. trustedIPs 가
있는데 LB 가 아직 헤더를 붙이지 않으면 연결은 깨지지 않는다(원 IP 자리에 SE 주소 — 또는 클라이언트가 직접 심은 PROXY 헤더의 값). trustedIPs 없이 LB 가
헤더를 붙이면 Traefik 이 PROXY 바이트를 TLS 로 읽어 **모든 연결이 깨진다**. 앞의 창(trustedIPs 있음·LB PROXY 꺼짐)에서는 L4 LB 가 바이트를 그대로
넘기므로 클라이언트가 심은 PROXY 헤더를 Traefik 이 신뢰 출발지(SE)의 것으로 믿어 `client_ip`(기록·경보 전용 — 2g-5)를 위조할 수 있다. 그래서 이 창은
짧게 두고(trustedIPs 롤링이 끝나면 같은 작업 창에서 LB PROXY v2 를 켠다), LB 의 PROXY 를 끈 채 둘 거면 trustedIPs 도 같은 작업에서 뺀다. SE 를
늘리거나 바꾸면 trustedIPs 도 같은 작업에서 고친다(빠진 SE 를 거친 연결은 원 IP 가 SE 주소로 찍힌다 — 2g-6).

**PROXY v2 를 못 붙이는 LB**: DMS 쪽에 바꿀 값이 없다(옛 `GATEWAY_PROXY_PROTOCOL=off` 는 없앴다). Traefik trustedIPs 는 비워 둔다 — S8 감지의
traefik WARN 「… proxyProtocol.trustedIPs 가 없다 …」(C!)는 실행할 때마다 보이지만 이런 사이트에서는 그대로 둔다. 앱이 기록하는
`client_ip` 는 LB(SE) 주소다 — 원 IP 는 LB 의 VS 로그에서 시각으로 맞춘다. 모든 사용자가 SE 주소 몇 개로 묶여 여러 사람의 실패가 한 「출발지」 경보로
쌓이니 사이트 `patch-config.yaml` 에 `DMS_LOGIN_ALARM_SOURCE_FAILURES: "0"` 을 더한다(§3-4 (b) → `--only S11`. 전체 실패 경보는 그대로이고, 로그인 상한·
거절은 아이디 기준이라 그대로다).

> 예(OA): Avi L4 VS VIP:443, PROXY v2 켬, 풀 = rke2-traefik 이 도는 노드 :443, SE `12.201.53.242`·`12.201.53.243` = rke2-traefik websecure 의 trustedIPs.
>
> 예(테스트베드): 호스트 edge(haproxy — LB 흉내, `send-proxy-v2`)가 VIP-A `10.10.10.1:443` → 노드 셋(cp1·w1·w2) :443, trustedIPs `10.10.10.1/32` — `deploy/testbed/README.md` §2·§4-8.

### 2g-5. 검증(S12)·원 IP

S12(`verify.sh --rendered`)의 5절 「입구」·6절 「포탈」:

| 검사 | 판정 |
|---|---|
| Ingress `dms/dms-portal-traefik` | 있고 class = 렌더 → PASS · class 가 다름 → WARN(`install.sh --only S8` 로 다시 적용) · 없음 → FAIL · 읽기 실패 → WARN(모름) |
| ingress-probe(노드마다 :443 — 2g-3. `--timeout` = env `INGRESS_PROBE_TIMEOUT`, 기본 5초) | rc 0 → PASS · rc 1 → FAIL · rc 4(UNKNOWN) → WARN(6절 VIP 경유가 다시 본다) · 그 밖 → WARN. 파드를 띄우지 않는 HTTPS GET 이라 `VERIFY_NO_PROBES=1`(`--no-probes`)에서도 돈다 |
| readyz — apiserver 서비스 프록시(`get --raw …/services/http:dms-api:http/proxy/readyz`) | `"ok"` → PASS(앱·DB 판정 — VIP·LB·DNS·CA 와 무관), 아니면 FAIL |
| readyz — VIP 경유(`curl --resolve <FQDN>:443:<VIP-A>` — `PORTAL_VIP` 가 없으면 DNS 대로, env `CACERT` 가 있으면 `--cacert`) | 200 → PASS · 응답 없음(000 — 연결·TLS 실패) → WARN(LB VS-443 미개통이면 정상 — 개통 뒤 `--only S12`. 개통했으면 PORTAL_VIP·CACERT·LB 풀 헬스·PROXY ↔ trustedIPs) · 그 밖 코드 → FAIL(404 = Traefik 이 Ingress 를 안 집었다·다른 Traefik, 5xx = dms-api) |
| `http://<FQDN>/`(80 — VIP 가 있으면 `--resolve <FQDN>:80:<VIP-A>`) | 404 → INFO(정상 — Traefik web 진입점에 DMS 라우터가 없다) · 응답 없음 → INFO(80 VS 없음) · **200 → WARN**(포탈이 평문으로도 나간다 — `router.entrypoints`·`TRAEFIK_ENTRYPOINT` 확인) · 그 밖 → INFO(LB·Traefik 의 80 설정) |

`PORTAL_DOMAIN` 이 없으면(render.env·env 둘 다) Traefik·VIP 경유 검사를 건너뛴다(INFO). 옛 「80 불가 = 정상」(80 이 열려 있으면 FAIL)은 없앴다 —
클러스터의 Traefik 은 web(:80) 진입점을 열어 둘 수 있고, DMS 라우터가 거기에 없으면 된다.

손 판:

```bash
kubectl --context C -n dms get ingress dms-portal-traefik
sh deploy/ingress-probe.sh --context C --domain <FQDN> --cacert <사내CA>
curl -sS --resolve <FQDN>:443:<VIP-A> --cacert <사내CA> https://<FQDN>/readyz                       # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST --resolve <FQDN>:443:<VIP-A> --cacert <사내CA> \
  -H 'Content-Type: application/json' --data-binary @<21MiB 파일> https://<FQDN>/api/auth/login    # 413(앱 request_too_large) — 19MiB 는 413 이 아니다(앱이 본문을 읽고 답한다)
curl -sS -o /dev/null --limit-rate 20k -w '%{http_code} %{time_total}\n' --resolve <FQDN>:443:<VIP-A> --cacert <사내CA> \
  https://<FQDN>/assets/<index-*.js>                                                              # 200 — 60초를 넘겨도 끊기지 않는다
curl -sS -m 5 -o /dev/null -w '%{http_code}\n' http://<FQDN>/                                    # 000(80 VS 없음) 또는 404 — 200 이면 평문 노출
```

**원 IP** — 앱 보안 이벤트 payload 의 두 칸(`src/dms/api/auth.py` `request_origin` — 관리 → 보안 이벤트, 또는 `GET /api/admin/security/events`):
- `client_ip` = XFF 마지막 항목 = Traefik 이 덧붙인 원 IP(LB 의 PROXY v2 를 trustedIPs 로 믿으면 **사용자 PC**, 아니면 LB(SE) 주소). XFF 가 없으면 소켓 피어.
- `peer` = 소켓 피어 = **Traefik 파드 IP**(옛 Envoy 때 마스터 호스트 주소가 있던 자리 — 이름만 같다. 기록 전용 칸이라 노드 IP 와 대조하지 않는다).
- 확인: 보안 이벤트는 임계를 넘을 때만 남는다. 포탈 화면에서 시험 아이디로 틀린 비밀번호를 출발지 경보 임계(base `DMS_LOGIN_ALARM_SOURCE_FAILURES` 20)만큼
  창 안에 넣으면 `login_source_alarm` 이 남는다 — 그 `client_ip` 가 내 PC 주소면 PROXY v2 가 끝까지 살아 있다(테스트베드 실측: 같은 원 IP 로 두 시험
  계정 실패 20건 → `login_source_alarm`, client_ip = 원 IP). SE 주소로 찍히면 2g-6 「client_ip 가 SE 주소」. 위조 `X-Forwarded-For`·`X-Real-Ip` 를 실은
  요청도 client_ip 는 바뀌지 않는다(Traefik 이 지운다 — 2g-2). 이 경보는 정상 동작이다 — 시험 전에 운영자들에게 알린다. 원 IP 는 기록·경보 전용이고
  어떤 거절의 키도 아니다(§10-7).
- 실 브라우저로 이 경로를 한 번 돈다(테스트베드 실측 2026-11-16: 관리자 30화면 × 1440·390 폭 — 콘솔 오류·실패 GET 0).

### 2g-6. 운영·문제 해결

- **Traefik 은 클러스터 공용이다** — 판 올리기·설정은 클러스터 운영자 몫이다(RKE2 는 RKE2 업그레이드 때 `rke2-traefik` 차트가 함께 오른다). DMS 는
  감지 표에 판(이미지 태그 — `traefik` 행의 「설치됨」 칸)과 args 근거를 보일 뿐 고정·판정하지 않는다. 클러스터 업그레이드·Traefik 설정 변경 뒤에는
  `sh deploy/install.sh --context <ctx> --site <site> --only S8,S12` 로 다시 본다(S8 은 Ingress D= 재적용 no-op + ingress-probe).
- **보안 권고**: Traefik v3.7.4(RKE2 hardened 이미지 `v3.7.4-build20260608`)는 그 뒤 공개된 보안 권고 여럿(HTTP 요청 스머글링·경로 라우팅 우회 등 —
  영향 범위 ≤ v3.7.12·≤ v3.7.17)에 든다. 수정판은 **v3.7.18 이상**이다(2026-11-16 조사). RKE2 패치 릴리스로 올리는 것은 사이트(클러스터 운영자) 몫이다.
- **롤링 공백**: rke2-traefik DaemonSet 은 `maxUnavailable 1`·`maxSurge 0`(같은 hostPort)이라 노드마다 옛 파드를 내린 뒤 새 파드를 띄운다. 그 사이
  그 노드 :443 은 비고, 그 노드에 맺혀 있던 긴 연결(큰 다운로드)은 끊긴다. 새 연결은 LB 헬스가 그 멤버를 DOWN 으로 거를 때까지 실패할 수 있다(헬스
  간격·재시도에 달렸다). trustedIPs 같은 정적 설정 변경·판 올리기 모두 이 롤링이다 — 사용자가 적은 시간에 한다(테스트베드 실측: 템플릿 변경 롤링
  동안 2초 간격 readyz 90/90 200 — LB 흉내의 TCP 헬스·재분배).
- **인증서 교체**: 같은 이름의 Secret 을 바꾸면 Traefik 이 다시 읽는다(재기동 없음 — 미검증: 테스트베드에서 교체를 재 보지 않았다). 바꾼 뒤
  `ingress-probe.sh --cacert` 로 노드마다 새 인증서를 확인한다(§3b-2).
- **사이트 값 바꾸기**: `PORTAL_DOMAIN`·`PORTAL_TLS_SECRET`·`INGRESS_CLASS`·`TRAEFIK_ENTRYPOINT` → values.env → `--only S8`(SSA 가 Ingress 를 고친다) →
  `--only S12`. FQDN 을 바꾸면 인증서 재발급(§3b-2)·DNS 가 먼저다. 앱 쪽 값(`DMS_MAX_REQUEST_BODY_BYTES`·`DMS_API_KEEP_ALIVE_SECONDS`)은 2g-2 —
  사이트가 Traefik `idleConnTimeout` 을 올렸으면 keep-alive 를 그보다 크게 둔다.
- **제거**: `kubectl --context C delete -k deploy/.rendered/<site>/platform/ingress`(Ingress 하나 — Traefik 은 그대로 남는다).

**문제 해결**:

| 증상 | 뜻 · 할 일 |
|---|---|
| 브라우저 인증서 경고, 인증서 주체 `CN = TRAEFIK DEFAULT CERT` | Traefik 이 SNI `<FQDN>` 의 인증서를 못 찾았다. ① 사용자가 친 이름 ≠ `PORTAL_DOMAIN`(IP·다른 이름이면 기본 인증서가 정상) ② Secret `<PORTAL_TLS_SECRET>` 이 ns dms 에 없거나 type 이 `kubernetes.io/tls` 가 아니다 ③ 그 Traefik 이 Ingress 의 class 를 집지 않는다(class 가 맞는 Ingress 의 tls 만 읽는다) ④ 인증서 SAN 에 FQDN 이 없다. ingress-probe 의 NODE 근거로 가른다 |
| `404 page not found`(평문 한 줄 — Traefik 의 404) | DMS 라우터가 없거나 맞지 않는다: Host ≠ `PORTAL_DOMAIN`, Ingress 없음, `ingressClassName`·`router.entrypoints`(진입점 이름이 다르면 S8 감지가 X 로 먼저 멈춘다), 백엔드 dms-api 없음. `kubectl --context C -n dms get ingress dms-portal-traefik -o yaml`, Traefik 로그(`kubectl --context C -n <Traefik ns> logs ds/<Traefik DaemonSet> --since=10m`) |
| VIP 경유는 TLS 가 깨지는데(curl `(35) … wrong version number`, 브라우저 `ERR_SSL_PROTOCOL_ERROR`) 노드 :443 직접(ingress-probe)은 PASS | LB 가 PROXY v2 를 붙이는데 Traefik websecure 에 proxyProtocol 이 없다(trustedIPs 가 비어 차트가 플래그를 만들지 않았다) → PROXY 바이트가 TLS 앞에 붙는다. trustedIPs 를 넣거나(2g-2) LB 의 PROXY 를 끈다(2g-4 순서). S8 감지의 traefik WARN 「proxyProtocol.trustedIPs 가 없다」가 같은 신호다 |
| 보안 이벤트의 `client_ip` 가 SE(LB) 주소 | Traefik 이 PROXY 헤더를 원 IP 로 쓰지 않았다: LB 가 PROXY 를 붙이지 않는다(프로필), 또는 trustedIPs 에 실제 출발지가 없다(SE 증설·교체·다른 인터페이스 주소 — 비신뢰 출발지의 헤더는 읽고 버린다), 또는 LB 와 노드 사이 SNAT 이 출발지를 바꾼다. trustedIPs = **노드가 보는** 실제 출발지다 |
| 413 `request_too_large`(포탈 「요청 본문이 서버 상한(기본 20 MiB)을 넘습니다 — …」) | 본문 > `DMS_MAX_REQUEST_BODY_BYTES`. 사용자 요청이면 배치를 나눠 제출한다. 에이전트 요청(api 로그의 413)이면 그 요청 크기를 보고 사이트 `patch-config.yaml` 로 올린다(§3-4 (b)) |
| 본문 있는 POST 가 가끔 502 | 앱 keep-alive ≤ Traefik 상류 `idleConnTimeout`(사이트가 올렸거나 `DMS_API_KEEP_ALIVE_SECONDS` 를 낮췄다), 또는 2026-11-16 이전 커밋으로 빌드한 앱 이미지(uvicorn 기본 5초) |
| 큰 업로드만 60초 근처에서 끊긴다 | Traefik `readTimeout`(기본 60s — 요청 본문 읽기). 사이트가 차트 values `ports.websecure.transport.respondingTimeouts.readTimeout` 을 올린다(2g-2 와 같은 방법) |
| LB 풀 전 멤버 DOWN, 노드 :443 직접은 PASS | HTTPS 헬스 모니터에 SNI·Host 가 없다(기본 인증서 + 404) — SNI·Host = `<FQDN>` 또는 TCP 443(2g-4). `/readyz` 모니터면 앱·DB 장애에도 전 멤버가 DOWN 이다 |
| ingress-probe UNKNOWN(연결 실패) | 설치 호스트 → 노드 :443 이 망에서 막혔거나(hostPort 는 노드 INPUT 방화벽에 걸리지 않는다 — 그 사이의 방화벽이다) 그 노드에 Traefik 이 없다. S8 은 WARN 후 계속하고 S12 의 VIP 경유가 다시 본다 |
| `http://<FQDN>/` 가 200(S12 WARN) | Ingress 의 `router.entrypoints` 가 빠졌거나 평문 진입점 이름이다 — 렌더 결과를 SSA 했는지(`--only S8`)와 `TRAEFIK_ENTRYPOINT` 값을 본다 |
| S8 `Traefik 판정 X — …` | 감지 사유 그대로(IngressClass 없음·controller 다름·`providers.kubernetesingress` 없음·진입점 없음) — 클러스터 운영자에게, 또는 values `INGRESS_CLASS`·`TRAEFIK_ENTRYPOINT` 를 그 Traefik 의 이름으로 |
| platform/ingress SSA 충돌(`Apply failed with N conflict`) | 누가 그 Ingress 를 손이나 다른 도구로 고쳤다 — §2h 「SSA 충돌」 |

### 2g-8. Envoy Gateway 입구에서 옮기기 — 전환·Envoy 정리 런북(옛 판을 설치한 사이트만, 1회)

2026-11-16 이전 커밋으로 Envoy Gateway 입구(`addons/gateway-api`·`addons/envoy-gateway`·`platform/gateway`)를 설치한 사이트만 한다 — 새 사이트는
건너뛴다. 병행 모드는 없다. **새 설치기는 옛 Envoy 객체를 감지하지도 자동으로 지우지도 않는다** — 정리는 아래 손 명령이다. 옛 Envoy 와 Traefik 은
같은 svc dms-api 를 서빙하므로 LB 풀을 옮기는 순간 말고는 끊기지 않는다.

**전환 순서**:

1. **지금 상태**: LB 풀 멤버·포트(옛 Envoy = 마스터 :443 또는 옛 `GATEWAY_LISTEN_PORT`)·PROXY v2 켬 여부, Traefik websecure 의 trustedIPs(2g-2 의
   확인 명령). LB 풀이 이미 Traefik 노드 :443 을 가리키는데 PROXY v2 가 켜져 있고 trustedIPs 가 없으면 지금 VIP 가 깨져 있다(2g-6).
2. **trustedIPs**(LB 가 PROXY v2 를 붙이면 — 사이트, 2g-2). Traefik 이 노드마다 롤링한다.
3. **새 커밋으로 올린다** — values.env 의 `GATEWAY_*` 줄을 지우고(남아도 note 뒤 무시) `PORTAL_DOMAIN` 을 확인한다(이제 Ingress host — 형식을 검사한다).
   옛 `GATEWAY_PROXY_PROTOCOL=off` 사이트는 출발지 경보 0 렌더가 없어졌다 — LB 가 계속 PROXY 를 못 붙이면 2g-4 의 튜닝 키를 더한다. 그다음 §4-2 순서로
   올린다(설치기 전체 실행 — S8 이 Ingress 를 만들고 노드마다 확인, S11 은 새 매니페스트·설정. 태그가 `live` 라 이미지는 그대로다 — 새 앱 이미지는
   §4-2 5 빌드·6 릴리스가 넣는다). **LB 를 옮기기 전에 앱이 새 판이어야 한다** — 옛 앱(uvicorn
   keep-alive 5초·본문 상한 없음)은 Traefik 뒤에서 본문 있는 POST 가 간헐 502 이고 20MiB 상한도 없다. 새 앱은 옛 Envoy 뒤에서도 무해하다.
4. **노드 직접 확인** — S8·S12 의 ingress-probe 가 노드마다 PASS(LB 를 옮기기 전이라 사용자 영향이 없다).
5. **LB 풀 → Traefik 이 도는 노드 전부 :443**, 헬스 모니터를 2g-4 대로 바꾼다. 옛 멤버는 같은 작업 창에서 뺀다.
6. **검증** — `--only S12`(VIP 경유 readyz 200)와 원 IP(2g-5 — client_ip = 사용자 PC, peer = Traefik 파드 IP).
7. **Envoy 정리** — 아래 정리 런북(옛 Envoy 가 트래픽을 받지 않는 것을 확인한 뒤).

테스트베드 실측(2026-11-16, 사용자 경로 2초 간격 readyz): LB 흉내(edge) 풀을 마스터 → 모든 노드로 25/25 200, Traefik 이 마스터를 인수하는 롤링
90/90 200, Envoy 정리 60/60 200 — 실패 0.

되돌림: 전환 7(정리) 전이면 LB 풀을 옛 멤버로 되돌리면 옛 Envoy 가 그대로 받는다(옛 객체가 남아 있고, 새 앱은 Envoy 뒤에서도 무해하다). 정리 뒤에는
옛 커밋의 설치기로 Envoy 층을 다시 깔아야 한다(§5-4).

> 예(OA): ① Avi VS(VIP:443) 풀 멤버·포트·PROXY v2(켜짐)·rke2-traefik 의 trustedIPs 확인 ② Rancher chartValues `rke2-traefik` 에 trustedIPs
> `["12.201.53.242/32","12.201.53.243/32"]`(forwardedHeaders 비움) ③ dms-k8s pull(설치 호스트·빌드 노드 같은 커밋) → values.env 의 `GATEWAY_*` 정리 → `install.sh` → 포탈 빌드·릴리스(새 앱 — §4-2 5·6, Avi 풀을 옮기기 전) ④ Avi 풀 = Traefik 이
> 도는 노드 :443, 헬스 TCP 443 또는 SNI·Host 를 FQDN 으로 한 HTTPS /readyz ⑤ 로그인 실패 보안 이벤트의 client_ip 가 사용자 PC 인지 ⑥ 옛 Envoy(DMS 가
> 깔았다면) 정리 — Gateway API CRD 는 rke2-traefik-crd(Helm) 소유라 남긴다.

**Envoy 정리 런북**(테스트베드에서 실행한 순서 — **DMS 가 깐 것만** 지운다. 먼저 소유를 본다):

```bash
kubectl --context C -n envoy-gateway-system get deploy envoy-gateway -L app.kubernetes.io/managed-by,dms.io/addon-version   # dms-install 이면 DMS 가 깐 EG(정리 2~4 대상)
kubectl --context C get crd -l app.kubernetes.io/managed-by=dms-install -o name | grep gateway.networking.k8s.io       # DMS 가 깐 Gateway API CRD(정리 5 대상) — 없으면 5 를 건너뛴다
```

1. **DMS 입구 객체** — EG 가 살아 있을 때 먼저 지운다(EG 가 GatewayClass `dms` 의 finalizer 를 걷고 Envoy DaemonSet `envoy-gateway-system/envoy-dms-dms-portal-*`
   을 거둔다):
   `kubectl --context C -n dms delete httproute/dms-portal gateway/dms-portal clienttrafficpolicy/dms-portal backendtrafficpolicy/dms-portal envoyproxy/dms-portal --ignore-not-found --wait=true`,
   `kubectl --context C delete gatewayclass dms --ignore-not-found --wait=true`. 그다음 `kubectl --context C -n envoy-gateway-system get ds` 에서 Envoy DaemonSet 이
   사라질 때까지 본다. EG 가 남이 깐 것(옛 C 경로)이었으면 여기서 끝이다 — 2~5 는 소유자 몫이다. 거꾸로 지워 GatewayClass 가 Terminating 에 남았으면
   (걷을 EG 가 없다) Gateway 가 지워진 것을 확인한 뒤 `kubectl --context C patch gatewayclass dms --type=merge -p '{"metadata":{"finalizers":null}}'`.
2. **EG mutating webhook** — 컨트롤러보다 먼저(남으면 파드 생성이 그 웹훅에 걸릴 수 있다):
   `kubectl --context C delete mutatingwebhookconfiguration envoy-gateway-topology-injector.envoy-gateway-system --ignore-not-found`.
3. **EG 네임스페이스·cluster RBAC**: `kubectl --context C delete ns envoy-gateway-system --wait=true --timeout=180s`,
   `kubectl --context C delete clusterrolebinding eg-gateway-helm-certgen:envoy-gateway-system eg-gateway-helm-envoy-gateway-rolebinding --ignore-not-found`,
   `kubectl --context C delete clusterrole eg-gateway-helm-certgen:envoy-gateway-system eg-gateway-helm-envoy-gateway-role --ignore-not-found`.
4. **EG CRD**(`*.gateway.envoyproxy.io` — 테스트베드 8개. 클러스터에 다른 EG 가 없을 때만):
   `for c in $(kubectl --context C get crd -o name | grep 'gateway.envoyproxy.io'); do kubectl --context C delete "$c"; done`.
5. **Gateway API CRD 는 라벨 `app.kubernetes.io/managed-by=dms-install` 인 것만**(테스트베드 10개 — DMS 가 v1.6.1 을 깔았다) + 같은 라벨의 VAP·binding
   `safe-upgrades.gateway.networking.k8s.io`:
   `for c in $(kubectl --context C get crd -l app.kubernetes.io/managed-by=dms-install -o name | grep 'gateway.networking.k8s.io'); do kubectl --context C delete "$c"; done`.
   `kubectl --context C get validatingadmissionpolicy,validatingadmissionpolicybinding safe-upgrades.gateway.networking.k8s.io -L app.kubernetes.io/managed-by` 가
   `dms-install` 이면 `kubectl --context C delete validatingadmissionpolicybinding,validatingadmissionpolicy safe-upgrades.gateway.networking.k8s.io`.
   **RKE2 의 rke2-traefik-crd(Helm 소유)가 깐 Gateway API(예: v1.5.1)는 지우지 않는다** — 라벨로 가른다. DMS 가 그 CRD 를 재사용했던(옛 C) 사이트는
   5 에 할 일이 없다.
6. **PriorityClass `dms-addon-critical` 은 남긴다** — CNPG 오퍼레이터가 쓴다(`addons/cnpg` patch-operator. 클래스는 `platform/priorityclass`, S4.
   레지스트리는 쓰지 않는다 — §2e-3).
7. **노드 설정 은퇴**(사이트가 두었으면): 마스터의 `/etc/sysctl.d/90-dms-envoy.conf`(`ip_unprivileged_port_start = 443`)를 지우고 런타임 값을 되돌린다.
   마스터 :443(옛 리스너 포트)을 SE 대역에만 여는 INPUT 규칙과 :19002·:19003 차단 규칙은 Envoy 용이었다 — Traefik hostPort 트래픽은 FORWARD 경로라 그
   규칙이 막지도 지키지도 못한다(위조 방지는 trustedIPs). 지워도 된다.

```bash
# 마스터마다(root):
rm -f /etc/sysctl.d/90-dms-envoy.conf
grep -rs ip_unprivileged_port_start /etc/sysctl.conf /etc/sysctl.d /run/sysctl.d /usr/local/lib/sysctl.d /usr/lib/sysctl.d   # 다른 파일이 정하면 그 값이 산다 — 그대로 둔다
sysctl -w net.ipv4.ip_unprivileged_port_start=1024    # 다른 파일이 없을 때만. 파일을 지워도 런타임 값은 재부팅까지 443 으로 남는다(1024 = 커널 기본)
```

남은 것 확인: `kubectl --context C get crd -o name | grep -E 'envoyproxy|gateway.networking'`(DMS 가 깐 것 0 — 남의 Gateway API 는 남는다),
`kubectl --context C get ns envoy-gateway-system`(NotFound), `kubectl --context C get clusterrole,clusterrolebinding,mutatingwebhookconfiguration -o name | grep -i -E 'envoy|eg-gateway'`(0).

## 2h. 설치기(install.sh) — 단계 S1~S13·감지·멱등 (2026-11-03 ⑦)

설계 v6 8-1(단계)·8-2(감지 상태 기계)·9 ⑦. §2d~§2g 의 손 명령을 단계로 묶었다 — 손 명령은 같은 일의 조사·복구용 판으로 남긴다. prod 의 순서는
§0-5·`deploy/overlays/prod/README.md`, 판정 근거는 각 도우미의 머리 주석이다. 단계마다 무엇이 실패하면 어디를 보는지는 §14-4 색인.

```bash
sh deploy/install.sh --context C [--site prod] --plan            # S1 + S2(확인만) + S3 — 클러스터를 바꾸지 않는다
sh deploy/install.sh --context C [--site prod]                   # S1 → … → S4 뒤 PAUSE(exit 10: push 명령 출력) — 이미 다 있으면 끝까지
sh deploy/install.sh --context C [--site prod] --stage S5        # 인터넷 노드 push 뒤 재개(S5 → S13)
sh deploy/install.sh --context C [--site prod] --only S7,S12     # 그 단계만(설치 순서대로) — 끝난 단계는 no-op·검증
#   --upgrade-addon <x>(D< 올리기·D= 재적용) · --adopt <x>(P 채택 — SSA --force-conflicts) · env ADDON_INSTALL=never(A 면 FAIL + 운영자에게 요청)
#   env ALLOW_IMAGE_CHANGE=1·ALLOW_HOSTPATH_CHANGE=1(가드 3·5) · VERIFY_NO_PROBES=1(S12 순수 읽기) · PORTAL_DOMAIN·CACERT(S8 ingress-probe·S12) · PORTAL_VIP(S12)
#   env INGRESS_PROBE_TIMEOUT(S12 ingress-probe 의 --timeout, 기본 5초) · PROBE_INTERVAL(ingress-probe 재측정 간격, 기본 2초)
#   env SELINUX_PROBE_IMAGE=<ref>(S2b — Cilium 이 없는 클러스터: 모든 노드에 이미 있는 셸 포함 이미지)
sh deploy/install.sh --help                                      # 머리 주석(env·--upgrade-addon 이름·종료 코드) — exit 0
```

| 단계 | 하는 일(도우미) | 멈춤 |
|---|---|---|
| S1 | `preflight-cluster.sh --render-env`(K8s 1.34~1.36·control-plane·Volcano 라벨 `app=volcano-scheduler`·「2. 입구」 IngressClass `<INGRESS_CLASS>` 의 controller = `traefik.io/ingress-controller`(없거나 다르면 FAIL)·Traefik 파드 Ready 수와 노드(INFO — LB 풀 후보)·PSA·admission·SHARED_DIR 형식·REGISTRY_CLUSTER_IP 의 ServiceCIDR·IPAddress·하단 정적 대역·노드 셸 체크리스트) + `install-detect.py detect` | FAIL·P·X(감지 — traefik X 면 사유를 줄에 싣는다), Volcano 를 설치해야 하는데 층 없음 |
| S2 | `00-namespace.yaml` → `pg-secrets.py ensure-app`·(없으면) `init-secrets` → 4키·자리표시자·`pg-secrets.py check` → `PORTAL_TLS_SECRET` 확인(없으면 WARN — 쓰는 곳인 S8 이 멈춘다) | 키·URL 어긋남(읽기 실패는 모름) |
| S2b | `selinux-probe.py`(privileged 임시 DS, DMS 노드만 판정) | FAIL·모름 |
| S3 | `install-detect.py plan`(층별 SSA server dry-run — 첫 설치의 「ns 가 아직 없음」·「CRD 가 아직 없음」은 SKIP) | FAIL |
| S4 | PriorityClass SSA → (in-cluster, 적용할 때만) guard(`--upgrade-addon registry` 면 이 층에서 이미지 교체 허용) → `dms-registry-http`(없을 때만·stdin) → platform/registry SSA → rollout → port-forward `/v2/` 200 | FailedMount(마스터 이름과 함께)·ImagePullBackOff(S0-5 반입 없음)·FailedScheduling·센티널·clusterIP 충돌 |
| S5 | `images-lock.py compare-registry`(port-forward) + DMS 3종 태그 HEAD → lock 이 다르면 `push-images.sh --print`(external TLS 면 `--dest-tls-verify true`), DMS 이미지만 없으면 그 빌드·복사 안내만 | S4 에서 이어 왔으면 PAUSE, 아니면 FAIL |
| S6 | `pull-probe.py`(Always, 120초, DMS 노드만) | FAIL·모름 |
| S7 | `shared-storage-probe.py`(①~⑥, ② 는 WARN) | 1·4(3 은 계속) |
| S8 | 「입구(Traefik)」(§2g-3): 감지 `traefik`(stop 이면 멈춤 — DMS 는 Traefik 을 설치·변경하지 않는다) → TLS 시크릿(`tls_gate`) → platform/ingress SSA(감지 `ingress` 가 install·reapply·adopt 일 때) → `ingress-probe.sh`(Traefik 이 도는 노드마다 :443 — SNI·Host = PORTAL_DOMAIN 의 인증서·readyz. dms-api Endpoints 가 비었으면 `--tls-only`) | traefik X(`Traefik 판정 X — …`), TLS 시크릿 없음·type 다름, Ingress P(→ `--adopt ingress`), ingress-probe FAIL(rc 1). UNKNOWN(rc 4 — 노드 :443 에 못 닿음)은 WARN 뒤 계속 |
| S9 | (ns 먼저 — DMS 라벨) CNPG SSA → rollout(업그레이드면 가드를 이 층에서 열고, Cluster 가 있으면 직전 덤프 → healthy 대기) → Cluster CR server dry-run(웹훅 9443, 막 뜬 웹훅은 재시도, `--adopt db` 면 `--force-conflicts`) | 「failed calling webhook」 |
| S9b | (층이 있으면) Volcano SSA → `install-detect.py vcjob`(DMS 모양 vcjob dry-run) | FAIL |
| S10 | `pg-path-probe.py pv-check` → db-volumes → `precheck` → db-cluster → 900초 폴링 → `contrast` → db-backup → 첫 덤프(Complete·Failed) | 각 판정 |
| S11 | 시크릿 게이트 → 앱 이미지 확인 → `guard-images.sh` → migrate 지움 → `apply -k`(client-side) → migrate·rollout → 설정 rev 바뀌면 restart → 마스터 사전 pull(WARN) | 가드 3·4·5, migrate |
| S12 | `verify.sh --rendered`(§2g-5 의 검사 — 5절 Ingress `dms-portal-traefik`·ingress-probe(env `INGRESS_PROBE_TIMEOUT`, 기본 5초 — UNKNOWN 은 WARN), 6절 readyz·http 80(404 = 정상 INFO, 200 = 평문 노출 WARN) — + CNPG·여분 PV·경로 대조·CronJob·레지스트리 태그·애드온 요약·EACCES 힌트). readyz 판정은 apiserver 서비스 프록시(`get --raw …/services/http:dms-api:http/proxy/readyz`), VIP 경유(`--resolve`)는 응답 없음이면 WARN(Avi VS-443 미개통 — 첫 설치) | FAIL(이때도 S13 체크리스트를 보인다) |
| S13 | 사후 체크리스트 출력(순서대로 — §6): 첫 관리자 부트스트랩(port-forward + `x-admin-token` 파일, 토큰·비밀번호를 출력·argv 에 싣지 않는다) → **메일(Knox 릴레이 — 사용자 게이트, §6-2)** → 컨트롤 상태 → k8s 연동 → 사본 → 스모크 → LB VS-443(풀 = Traefik 이 도는 노드 :443·헬스·PROXY v2 ↔ trustedIPs — §2g-4)·`--only S12` → 확인표 → DNS·공지 | — |

- **S2b(SELinux) — 테스트베드 실측**(2026-11-03 T-SE, Rocky 9.6 워커): `setenforce 1` 이면 그 노드만 FAIL 「enforcing」, config 만 enforcing 이면 FAIL
  「재부팅하면 enforcing」. enforcing 동안 dms-agent(`container_t`)가 `/proc/1/mountinfo`(`init_t`)·CephFS 루트(`unlabeled_t`)를 못 읽어 그 노드의
  스토리지가 전부 Missing(`not_a_mountpoint`)으로 보고됐다 — prod README 「0. 사이트 준비」 2(SELinux 필수 전제, 설계 #48)의 근거. NFS 경로는 AVC 0.
- **RHEL firewalld — 설치기·preflight 는 보지 않는다**(S0 사이트 몫): 기본 public zone 을 켠 채 두면 노드 간 CNI 흐름이 막혀 파드 DNS·ClusterIP·에이전트
  보고가 실패한다. 열 포트는 §0-3.
- **`--context` 는 필수**다(KUBE_CONTEXT·current-context 를 쓰지 않는다). 렌더의 `kube-context`(오버레이에 그 파일이 있을 때 — §3-2)와 다르면 exit 2. 모든 kubectl
  은 `kc`·`kcn` 함수(`command kubectl --context "$CTX"`)로만 부른다 — 계약 테스트 `test_check_scripts_context.py`.
- **층의 소유**: addons·platform 은 SSA(`--field-manager=dms-install`), 앱은 client-side apply. 감지가 **C**(남이 설치한 호환 애드온)면 그 층에는 apply 도
  dry-run 도 부르지 않는다 — 소비 층(platform/ingress — traefik, Cluster CR dry-run — cnpg, vcjob — volcano)만 본다. Traefik 은 DMS 가 설치하는 층이
  아예 없다(감지 traefik 에 A 가 없다 — §2g-3).
- **멱등**: 같은 렌더로 다시 돌리면 SSA 는 no-op, 시크릿·Job·ns 는 만들지 않고(있으면 그대로), 첫 덤프는 CronJob 의 마지막 성공이 있으면 건너뛰고,
  rollout restart 는 `dms-config`·`dms-secrets` 의 resourceVersion 이 바뀌었는데 이번 apply 가 파드를 바꾸지 않은 경우에만 한다(워크로드 어노테이션
  `dms.io/install-config-rev` — 값이 아니라 rev 만). `tests/test_install_stages.py` 가 심 kubectl 로 순서·PAUSE·재개·멱등·`--plan` 무변경을 고정한다.
- 종료: 0 완료(WARN 포함) · 1 FAIL · 2 사용법·환경(빈 `--stage`·`--only` 포함) · 4 모름 · 10 PAUSE. 마지막 줄 `RESULT: …`, 단계마다 `STAGE: <S> <판정>`.
  preflight(S1)·verify(S12)의 `[WARN]` 줄은 rc 0 이어도 그 단계와 RESULT 를 WARN 으로 만든다(2026-11-03 ⑦ 리뷰 2). 도우미 출력은 4칸 들여 쓴다 — 0열의 `==`·`STAGE:`·`RESULT:` 는 설치기 자신의 줄뿐이라
  `grep '^RESULT:'` 는 설치 결과 하나만 잡는다. **control-plane 이 1대인 시험 클러스터에서는 `--plan` 도 `STAGE: S1 WARN`·
  `RESULT: WARN`(rc 0)이 정상이다** — preflight 「control-plane 노드 1대 — prod 는 3대」 WARN 이다(다른 WARN 이 없는지만 본다).
- **감지 줄은 항목 이름을 싣는다**: `감지 WARN — traefik(S8 U verify)`·`감지 FAIL — priorityclass(S4 X stop), …`. 감지는
  처음 부른 단계에서 한 번 돌지만 WARN 은 **그 항목의 단계**가 센다 — S1 이면 S1(표 전체가 S1 의 일), `--stage S5` 처럼 뒤에 돌 단계면 그 단계가
  시작할 때(`[INFO] … S8 의 판정으로 센다` → S8 의 `[WARN]`), 이번 실행에 없는 단계(`--only S9` 의 S8 항목)면 STAGE 가 아니라 RESULT 에만(전에는
  `--only S9` 가 옛 envoy-gateway U 로 `STAGE: S9 WARN` 이었다). 계획(S3)도 기대는 애드온의 판정이 stop(A never·P·X·?)이면 그 소비 층·앱 층을 날것의
  `no matches for kind` FAIL 이 아니라 `skipped: cnpg 판정 FAIL(P stop) — Cluster… 는 확인하지 않는다` 로 보인다(vcjob 층과 같다).
- **SSA 충돌**(층 적용 중 `Apply failed with N conflict`): 설치기는 충돌한 field manager 이름을 보인다. `--adopt <x>` 는 감지 **P** 에만 듣는다 —
  DMS 소유(D=·D<)·설치(A) 층의 충돌은 관리자가 손 수정(`kubectl-edit`·`kubectl-patch` 등)이면 그 수정을 되돌리거나 DMS 값으로 덮어쓰고
  (`kubectl --context C apply --server-side --field-manager=dms-install --force-conflicts -k deploy/.rendered/<site>/<층>`) 같은 단계부터 다시, 다른
  도구·컨트롤러(Helm·오퍼레이터)면 덮어쓰지 말고 소유자와 정리한다. 설치기는 스스로 `--force-conflicts` 를 쓰지 않는다(채택 P 만).
- **재개 명령**(PAUSE·모름·FAIL 줄)은 이번 실행의 `--upgrade-addon`·`--adopt` 와 판정을 바꾸는 env(`ADDON_INSTALL`·`ALLOW_*`·`SELINUX_PROBE_IMAGE`·
  `VALUES_ENV`·S12 의 `CACERT`·`PORTAL_DOMAIN`·`PORTAL_VIP`·`VERIFY_NO_PROBES`)를 그대로 싣는다 — 출력된 줄을 그대로 붙여 넣는다(2026-11-03 ⑦ 리뷰 1·2:
  플래그가 빠진 재개는 업그레이드를 조용히 건너뛰었고, CACERT 가 빠진 S12 재실행은 VIP readyz 가 TLS 실패로 WARN 이 됐다). **`--plan` 의 「확인한
  뒤」·「계획 끝」 줄은 늘 `--plan` 이다** — 실제 설치 명령(`… --site S [--adopt …]`)은 FAIL·모름이 없는 계획 뒤에만 계획과 같은 플래그·env 로 보인다
  (전에는 계획 중의 모름·FAIL 이 클러스터를 바꾸는 `--stage S1`·`--stage S2` 를 권했다). env `DMS_NS`(옛 install.sh)는 읽지 않는다 — ns dms 고정,
  도우미에도 `--namespace dms`.
- **가드 거부(이미지 변경, exit 3)**: 애드온·플랫폼 층(registry·cnpg·volcano)이면 안내는 `--only <S> --upgrade-addon <x>`(그 층에서만 연다 —
  감지 D< 의 upgrade 와 lock digest 만 바뀐 D= 재적용 둘 다)이고, 앱이면 values.env 태그를 라이브에 맞추거나 `live`(의도한 앱 이미지 변경만
  `ALLOW_IMAGE_CHANGE=1`). 전역 `ALLOW_IMAGE_CHANGE=1` 은 재개 줄에 실려 S11 의 앱 가드(포탈 릴리스 되돌림 방지)까지 열므로 애드온에 쓰지 않는다.
- **렌더는 매 실행 처음에 다시 한다**(`--plan`·`--only` 포함 — `deploy/.rendered/<site>` 를 통째로 바꾼다). 그 아래를 손으로 고친 값은 설치기로
  적용되지 않는다 — 사이트 값은 values.env 키로만(키가 없으면 BACKLOG 「⑦ 설치기 후속」에 올리고, 그 전에는 설치기 실행 뒤 §2d~§2g 의 손 명령으로).
- **앱 업그레이드**: 계획(S3)은 끝난 `dms-migrate` Job 의 `field is immutable` 을 실패로 보지 않는다(S11 이 지우고 다시 만드는 Job — 앱 층 PASS 에
  사유를 적는다).
- **아직 없는 것**(BACKLOG ⑦): Volcano 층(`addons/volcano` — A 사이트는 S1 에서 멈춘다), 새 사이트 DB 의 import bootstrap, `kubectl-cnpg` 반입.

## 3. 적용 규칙·설정 변경 — 설치기 하나·컨텍스트·가드·설정을 바꾸는 네 길

### 3-1. 적용 경로는 설치기 하나다

- base `deploy/k8s` 는 **사이트 중립 자리표시자**다. 이미지는 `set-by-overlay.invalid/<이미지>:set-by-overlay`, 아티팩트 경로는
  `/set-by-overlay.invalid/dms/artifacts` 다(계약 테스트 `tests/test_release_manifest_contract.py`·`tests/test_shared_dir_contract.py`).
  `kubectl apply -f deploy/k8s` 는 pull·마운트에서 실패하도록 돼 있다. 다른 사이트의 값을 조용히 배포하던 사고를 막는 의도된 실패다.
  예외는 이미지가 없는 `deploy/k8s/00-namespace.yaml` 하나다(TLS 시크릿을 먼저 만들 때 — §3b-2).
- prod 의 적용은 `sh deploy/install.sh --context <ctx>` 하나다. 매 실행(`--plan`·`--only` 포함) 처음에
  `deploy/render.sh prod` 가 `deploy/overlays/prod`(템플릿)와 `values.env` 로 `deploy/.rendered/prod/`(git 밖)를 **통째로 다시** 만든다.
  그다음 가드를 거쳐 플랫폼·애드온 층은 SSA(`--field-manager=dms-install`), 앱 층은 S11 의 `kubectl apply -k`(client-side)로 간다
  (`install.sh` 머리 주석 「렌더」·「층의 소유」, `stage_S11`).
- 그래서 `deploy/.rendered/prod` 아래를 손으로 고친 값은 다음 실행이 지운다. 템플릿 `deploy/overlays/prod` 를 직접
  `kubectl apply -k` 하면 `REPLACE_REGISTRY/dms` 같은 이미지가 들어간다 — 하지 않는다.
- 조사·복구 때 손으로 적용해야 하면 렌더 디렉터리만, 가드를 거친 뒤에 적용한다. 앱 층은 migrate Job 을 먼저 지운다(Job spec 은 바꿀 수 없다):

  ```sh
  sh deploy/render.sh prod --context <ctx>                                     # 또는 install.sh --plan 이 남긴 렌더
  sh deploy/overlays/guard-images.sh deploy/.rendered/prod/app --context <ctx>  # exit 0 이어야 한다(3-3)
  kubectl --context <ctx> -n dms delete job dms-migrate --ignore-not-found
  kubectl --context <ctx> apply -k deploy/.rendered/prod/app
  ```

  보통은 같은 일을 하는 `sh deploy/install.sh --context <ctx> --only S11` 을 쓴다(설정 rev 를 보고 재시작까지 한다 — 3-4).

### 3-2. 컨텍스트

- **모든 명령에 `--context <ctx>` 를 준다.** 여러 클러스터가 같은 네임스페이스 이름 `dms` 를 쓰면 컨텍스트가 유일한 구분이다.
  current-context 를 바꿔 두고 기대지 않는다.
- 스크립트마다 받는 방식이 다르다:

  | 스크립트 | 컨텍스트 | 없으면 |
  |---|---|---|
  | `install.sh` | `--context` 플래그만(env `KUBE_CONTEXT` 도 읽지 않는다) | exit 2 |
  | `preflight-cluster.sh`·`verify.sh`·`pull-probe.py`·`selinux-probe.py`·`shared-storage-probe.py`·`install-detect.py` | `--context` 또는 env `KUBE_CONTEXT`(설치기가 export 한다) | exit 2 |
  | `pg-secrets.py`·`pg-path-probe.py` | `--context` 플래그만(argparse `required` — env 를 읽지 않는다) | 사용법 오류 exit 2 |
  | `ingress-probe.sh` | `--context` 플래그만(`--domain` 과 함께 필수) | exit 2 |
  | `render.sh` | `--context` 또는 `KUBE_CONTEXT` — 태그가 `live` 일 때만 클러스터를 읽는다 | `live` 면 FAIL |
  | `overlays/guard-images.sh` | `--context` > `KUBE_CONTEXT` > **current-context** | current-context 로 라이브를 읽는다 |

  `guard-images.sh` 를 손으로 부를 때는 반드시 `--context` 를 준다(`guard-images.sh` 의 컨텍스트 블록 — `kube-context` 파일이 없는 오버레이는
  `--context`·`KUBE_CONTEXT` 가 없으면 `kubectl` 을 컨텍스트 없이 부른다).
- **선택 강화 — 컨텍스트 고정**: 사이트 체크아웃에 `deploy/overlays/prod/kube-context` 파일(주석이 아닌 첫 줄 = 컨텍스트 이름)을 두면 렌더가
  각 층으로 복사하고(`render.sh`), `install.sh` 는 `--context` 가 다르면 exit 2, 가드는 exit 4 로 거부한다. 저장소에 커밋되지 않는
  사이트 로컬 파일이다.

### 3-3. 가드(`deploy/overlays/guard-images.sh`)

S4(레지스트리)·S9(CNPG)·S9b(Volcano 층이 있을 때)·S11(앱) 적용 직전에 설치기가 부른다(`install.sh guard`). S8 의 `platform/ingress` 는 워크로드가
없어 가드 대상이 아니다(2026-11-16 — 옛 EG 컨트롤러 가드는 없앴다). "렌더한 이미지·hostPath 가 라이브와 다르면" 거부한다 — 포탈 릴리스 뒤 옛
태그로 다시 적용해 라이브를 되돌리는 사고를 막는다.

| 종료 | 뜻 | 여는 법 |
|---|---|---|
| 0 | 통과(첫 설치 — 라이브 워크로드 없음 — 포함) | — |
| 2 | 사용법·렌더 실패·없는 컨텍스트·워크로드 없는 층 | 인자·렌더 확인 |
| 3 | 이미지 변경 거부 | 앱: values 태그를 라이브와 같게(또는 `live`). 의도한 앱 이미지 변경만 `ALLOW_IMAGE_CHANGE=1`. 애드온·플랫폼: `--upgrade-addon <x>`(그 층만 연다) |
| 4 | 컨텍스트 불일치(`kube-context` 고정, 또는 고정 없는 오버레이에서 `KUBE_CONTEXT` ≠ current-context) | 맞는 `--context` |
| 5 | hostPath 변경 거부(공용 디렉터리 이동) | §2d-4 전환 런북을 마친 뒤 `ALLOW_HOSTPATH_CHANGE=1` |

둘 다 바뀌면 3 이 먼저다. **애드온 업그레이드에 전역 `ALLOW_IMAGE_CHANGE=1` 을 쓰지 않는다** — 재개 줄에 실려 S11 의 앱 가드까지 열린다
(`install.sh` 머리 주석 `--upgrade-addon`).

### 3-4. 설정을 바꾸는 네 길

| 길 | 무엇 | 반영 | 영구성 |
|---|---|---|---|
| (a) 포탈(DB) | 메일 설정, 스토리지·정책·노드 배치 제외, k8s 연동, 컨트롤 상태, 아티팩트 경로, 포탈 부제, 계정의 root 자격 부여, 잡 이미지(릴리스) | 즉시(재시작 없음) | 영구(DB). 메일·아티팩트 경로·잡 이미지는 **DB 값이 env 를 이긴다**(`mail_config.resolve_mail_config`, `artifact_base.resolve_artifact_base`, `job_image.resolve_job_image`) |
| (b) `dms-config` 튜닝 키 | base `deploy/k8s/20-config.yaml` 의 키(로그인 보호·경보·보존일·루프 간격 등), 또는 base 에 없고 코드 기본값만 있는 키(예: `DMS_MAILER_BACKEND`·`DMS_MAX_REQUEST_BODY_BYTES`·`DMS_API_KEEP_ALIVE_SECONDS` — 더하면 새 키가 생긴다) | 아래 「영구」 절차 | 사이트 `patch-config.yaml` 에 두면 영구 |
| (c) `values.env` 키 | 태그·도메인·레지스트리·DB·입구 값 | 아래 표의 단계 | 영구 |
| (d) 시크릿 | `dms-secrets`·TLS 등 | §3b | — |

**(b) 튜닝 키 — 영구로 바꾸기.** prod 에는 이 키들의 values 키가 없다(values 키로 올리는 일은 BACKLOG). 지금 영구 경로는 사이트 체크아웃의
템플릿에 키를 더하는 것이다:

1. 설치 호스트의 체크아웃에서 `deploy/overlays/prod/patch-config.yaml` 의 `data:` 아래에 키를 더한다. 값은 따옴표 문자열이다. 예:

   ```yaml
     DMS_MAILER_BACKEND: "knox_relay"
   ```

   로그인 보호 장치 값(`DMS_LOGIN_*`)은 §10-7 의 뜻을 읽고 바꾼다 — 예컨대 `DMS_LOGIN_GLOBAL_PACE_LIMIT` 은 기본 0(꺼짐)이고 스프레이 공격 중에만
   임시로 켜는 스위치라 영구 키로 두지 않는다.

2. `sh deploy/install.sh --context <ctx> --plan` → `RESULT` 가 PASS(또는 이해한 WARN).
3. `sh deploy/install.sh --context <ctx> --only S11`. S11 은 `dms-config`·`dms-secrets` 의 resourceVersion 이 바뀌었는데 파드 템플릿이 그대로면
   dms-api·dms-controller·dms-agent 를 `rollout restart` 한다(`install.sh stage_S11` 의 `config_rev`) — 성공이면 「설정이 바뀌어 다시 띄웠다」 줄.
4. 확인: `kubectl --context <ctx> -n dms get cm dms-config -o jsonpath='{.data.DMS_MAILER_BACKEND}'`.
5. 업그레이드 때 이 수정을 잃지 않게 한다. 추적 파일의 로컬 수정이라 `git pull` 이 충돌할 수 있다. 사이트 브랜치의 로컬 커밋으로 두거나
   `git diff deploy/overlays/prod/patch-config.yaml` 을 보관해 두고, 새 커밋으로 올린 뒤 `--plan` 전에 그 키가 있는지 본다
   (렌더 결과 `deploy/.rendered/prod/app/patch-config.yaml` 에도 보인다).

- 값이 틀리면 api·controller 가 **기동을 거부**한다(`config.Settings.from_env` — 정수 하한, `DMS_ALLOW_PRIVILEGED_REQUESTERS=false`, `DMS_IDENTITY_MODE` 가 k8s
  아님, `DMS_LDAP_URI` 있음 등). 확인: `kubectl --context <ctx> -n dms logs deploy/dms-api -c api --tail=20`.
- **임시 변경**(공격 대응처럼 지금 당장): `kubectl --context <ctx> -n dms edit cm dms-config` →
  `kubectl --context <ctx> -n dms rollout restart deploy/dms-api deploy/dms-controller`. **다음 설치기 실행(S11)이 렌더 값으로 되돌린다.**
  계속 둘 값이면 위 영구 절차도 한다. §10-7·§12b 의 「ConfigMap 수정」은 이 두 길을 말한다.

**(b) 입구와 맞물린 튜닝 키**(2026-11-16 입구 교체 — 옛 Envoy BackendTrafficPolicy·`off` 모드 렌더가 하던 일. 앞의 둘은 base `20-config.yaml` 에 일부러
없다 — 코드 기본이 계약이다, `src/dms/config.py`·`src/dms/api/body_limit.py`·`src/dms/cli.py`. 왜 앱이 하는지는 §2g-2 「앱이 대신 하는 것」):

| 키 | 기본(하한 — 밑이면 api·controller 기동 거부) | 언제 바꾸나 |
|---|---|---|
| `DMS_MAX_REQUEST_BODY_BYTES` | `20971520`(20MiB, 하한 `1048576`) | 요청 본문 상한. 넘으면 413 `request_too_large`(인증·라우팅보다 먼저). **입구 경유뿐 아니라 dms-api 에 닿는 모든 HTTP 요청에 걸린다 — 클러스터 안 에이전트 요청 포함**(예외는 자기 상한을 재는 k8s 동기화 결과 POST 하나 — `DMS_K8S_SYNC_MAX_BYTES` + 1MiB, 넘으면 413 `k8s_sync_too_large`). 정당한 요청(에이전트·큰 배치)이 413 이면 올린다. 배치 사용자는 먼저 나눠 제출한다 |
| `DMS_API_KEEP_ALIVE_SECONDS` | `120`(하한 1) | uvicorn keep-alive. 입구 Traefik 상류 `idleConnTimeout`(기본 90s)보다 길어야 한다 — 짧으면 본문 있는 POST 가 간헐 502. 사이트가 `idleConnTimeout` 을 120s 이상으로 올렸으면 그보다 크게. 낮추지 않는다 |
| `DMS_LOGIN_ALARM_SOURCE_FAILURES` | base `"20"` | LB 가 PROXY v2 를 못 붙이는 사이트(모든 사용자가 SE 주소로 보인다 — §2g-4)는 `"0"`. 옛 `GATEWAY_PROXY_PROTOCOL=off` 의 렌더 자리는 없앴다 — 이 키를 직접 더한다(§10-7) |

셋 다 위 영구 절차(사이트 `patch-config.yaml` → `--only S11` — S11 이 `dms-config` 변화를 보고 dms-api·dms-controller·dms-agent 를 다시 띄운다)로 바꾼다.
확인: `kubectl --context <ctx> -n dms get cm dms-config -o jsonpath='{.data.DMS_MAX_REQUEST_BODY_BYTES}'`(키가 없으면 빈 값 = 코드 기본).

**(c) values.env 키 — 들어가는 층과 단계**

| 키 | 층(단계) | 주의 |
|---|---|---|
| `DMS_TAG`·`DMS_AGENT_TAG`·`MFU_TAG` | app(S11) | 설치 뒤에는 `live`(렌더가 라이브 태그를 읽는다 — `render.sh _live_tag`). 명시 태그가 라이브와 다르면 가드 exit 3(§4) |
| `EMAIL_DOMAIN`·`LOCAL_ADMIN` | app `dms-config`(S11 — 재시작 자동) | 형식 검사 없음(§0-1) |
| `PORTAL_DOMAIN`·`PORTAL_TLS_SECRET`·`INGRESS_CLASS`·`TRAEFIK_ENTRYPOINT` | platform/ingress(S8 — Ingress `dms-portal-traefik`) + `render.env`(S12) | §2g-1. `--only S8,S12`. FQDN 을 바꾸면 인증서 재발급(§3b-2)·DNS 가 먼저다 |
| `PORTAL_VIP` | `render.env` 만(S12) | 인증서 SAN 과 맞춘다(§3b-2) |
| 옛 `GATEWAY_PROXY_PROTOCOL`·`GATEWAY_LISTEN_PORT`·`GATEWAY_ENVOY_CPU_REQUEST`·`GATEWAY_ENVOY_MEMORY_REQUEST` | — | **더 쓰지 않는다**(2026-11-16). 남아 있으면 렌더가 stderr 에 `note: … 더 쓰지 않는다 … 무시함` 한 줄을 낸 뒤 무시한다 — 줄을 지운다. PROXY·타임아웃은 클러스터 Traefik 의 정적 설정(§2g-2), 출발지 경보 0 은 위 (b) 튜닝 키 |
| `REGISTRY_CPU_REQUEST`·`REGISTRY_MEMORY_REQUEST` | platform/registry(S4) | 빌드·push 가 없을 때(§2e-5) |
| `PG_CPU_REQUEST`·`PG_MEMORY`·`PG_SHARED_BUFFERS`·`PG_PLACEMENT` | platform/db-cluster(S10) | CNPG 가 롤링한다. 이미지와 한 번에 바꾸지 않는다(§2f-6). `PG_PLACEMENT=all-nodes` 는 control-plane 1대 시험 클러스터 전용 |
| `PG_BACKUP_KEEP` | platform/db-backup(S10) | 개수로 센다 |
| `CONTROL_PLANE_LABEL_VALUE` | app(S11)·platform/registry(S4)·platform/db-cluster(S10) | 세 층을 다시 적용한다(`sh deploy/install.sh --context <ctx> --site <site>`) — nodeSelector 가 바뀌어 파드가 다시 배치된다 |
| `PG_STORAGE_SIZE` | — | PV 를 만든 뒤에는 바꾸지 않는다(S10 `pv-check` 가 FAIL) |
| `REGISTRY_MODE`·`REGISTRY_CLUSTER_IP`·`REGISTRY_PORT`·`REGISTRY` | — | 바꾸지 않는다(§2e-1) |
| `SHARED_DIR`·`SHARED_DIR_ID` | 여러 층 | §2d-4 전환 런북 |

적용은 `--plan` 뒤 전체 재실행 `sh deploy/install.sh --context <ctx>`(끝난 단계는 no-op·검증 — 멱등) 또는 그 단계만 `--only <S>` 다.
DMS 소유 플랫폼 층은 감지가 D=(같은 버전)여도 SSA 로 다시 적용해 사이트 값 변경을 반영한다. 남이 설치한 애드온(C)과 DMS 가 설치한 서드파티
애드온의 D= 는 다시 적용하지 않는다(`install-detect.py` 머리 주석의 코드·동작 표 — D= 행).

### 3-5. ConfigMap·Secret 만 바뀐 손 적용은 파드를 다시 띄우지 않는다

`dms-config`·`dms-secrets` 는 `envFrom`·`secretKeyRef` 라 기동 때만 읽힌다. 이미지는 포탈 릴리스가 이미 바꿔 둬서 `apply -k` 가 파드 템플릿을
바꾸지 않는 경우가 많다. 손으로 적용했으면 다시 띄운다:

```sh
kubectl --context <ctx> -n dms rollout restart deploy/dms-api deploy/dms-controller   # 에이전트 키(DMS_AGENT_*·공유 토큰)를 바꿨으면 ds/dms-agent 도
kubectl --context <ctx> -n dms rollout status deploy/dms-api deploy/dms-controller
```

설치기(S11)는 이 일을 스스로 한다(3-4).

### 3-6. `deploy/k8s/20-secret.example.yaml` 은 적용하지 않는다

자리표시자만 담은 예시다. 라이브 `dms-secrets` 위에 적용하면 진짜 값을 덮는다 — api·controller 는 자리표시자를 보고 기동을 거부하거나
(`config._is_placeholder` — `CHANGE_ME`·`REPLACE_WITH_`) DB 인증에 실패한다. 시크릿은 설치기 S2 가 만든다(§3b-1).

## 3b. 시크릿·인증서·토큰

값은 화면·argv·로그에 내지 않는다. 확인은 키 이름이나 판정만 본다. 값이 꼭 파일로 나가야 하면 바로 암호화한다(3b-5).

### 3b-1. 목록·만드는 곳·확인

| Secret(ns `dms`) | 키 | 만드는 곳 | 읽는 곳 |
|---|---|---|---|
| `dms-secrets` | `DMS_DATABASE_URL`·`DMS_SHARED_TOKEN`·`DMS_ADMIN_TOKEN`·`DMS_SESSION_SECRET`(4키) | S2 `deploy/pg-secrets.py init-secrets` — **없을 때만** 만든다(값 무작위, 출력 없음) | api·controller·migrate(`envFrom`), dms-agent(`DMS_SHARED_TOKEN` 만 — `secretKeyRef`) |
| `dms-pg-app` | `username`(`dms`)·`password`(basic-auth) | S2 `pg-secrets.py ensure-app` — **이미 있으면 덮지 않는다** | CNPG initdb, pg_dump CronJob |
| `dms-registry-http` | `http-secret` | S4(없을 때만, stdin) | 클러스터 내 레지스트리 |
| `<PORTAL_TLS_SECRET>`(기본 `dms-portal-tls`, `kubernetes.io/tls`) | `tls.crt`·`tls.key` | **사이트**(3b-2) | 클러스터의 Traefik(Ingress `dms-portal-traefik` 의 `spec.tls` — class 가 맞는 Ingress 와 같은 ns 의 시크릿만 읽고 SNI 로 고른다, §2g-1) |

이 표 밖에도 설치가 만드는 비밀이 있다(값을 다루지 않는다 — 각 오퍼레이터가 만들고 돌린다): CNPG 오퍼레이터의 PG 서버·복제 인증서와 그 CA(개인키 포함,
ns `dms` — 이름은 CNPG 판에 따른다, 미검증), Volcano ssh 플러그인이 잡마다 만드는 SSH 키(ns `dms` — 잡 매니페스트의 `plugins.ssh`, vcjob 과 함께
지워진다). 전체 목록: `kubectl --context <ctx> -n dms get secret`. ns `dms` 의 시크릿 읽기 권한을 좁히는 이유가 여기에도 있다(§3b-3). 포탈 TLS
개인키는 클러스터 공용 Traefik 의 서비스 계정도 읽는다(§0-4 승인 항목).

DB 안에도 비밀이 둘 있다 — 포탈에 넣은 메일 릴레이 인증 키와 k8s 연동 클러스터 토큰이다. 둘 다 `DMS_SESSION_SECRET` 에서 유도한 키로 봉인해
저장한다(`mail_config` 의 `seal_at_rest`·`open_at_rest`, `routes_k8s`·`routes_agent` 의 클러스터 토큰). 화면에서 다시 볼 수 없다.

확인(설치 호스트):

```sh
kubectl --context <ctx> -n dms get secret dms-secrets -o go-template='{{range $k,$v := .data}}{{$k}}{{"\n"}}{{end}}'   # 정확히 4줄
python3 deploy/pg-secrets.py --context <ctx> check     # URL 이 dms-pg-rw·DB·사용자·dms-pg-app 비밀번호·필수 파라미터와 맞는가(값은 내지 않는다)
kubectl --context <ctx> -n dms get secret <PORTAL_TLS_SECRET> -o jsonpath='{.type}'; echo                             # kubernetes.io/tls
```

- 설치기 S2·S11 이 같은 게이트를 돈다(`install.sh secrets_gate` — 4키, `CHANGE_ME`·`REPLACE_WITH_` 자리표시자 없음, `pg-secrets.py check`).
- api·controller 는 자리표시자나 빈 값을 보면 기동을 거부한다(`config._is_placeholder`).
- URL 모양과 손으로 만드는 법은 §2f-2.

### 3b-2. 포탈 TLS 인증서

- SAN = `DNS:<FQDN>` + `IP:<VIP-A>`(렌더·S8 안내와 같다). **Traefik 은 ClientHello 의 SNI 로 인증서를 고른다** — `https://<FQDN>` 만 이 인증서를
  받는다. IP 로 접속하면(SNI 없음) Traefik 기본 인증서(`CN = TRAEFIK DEFAULT CERT`, 자가서명) + `404 page not found` 다 — IP SAN 이 있어도 IP 접속은
  포탈을 열지 않는다(Host 매처도 FQDN 만 — §2g-2).
- 개인키는 공용 스토리지에 두지 않는다. 권한 0600.

```sh
# 1) 키와 CSR(보안 호스트에서) — 사내 CA 에 서명을 받는다
umask 077
openssl req -new -newkey rsa:2048 -nodes -keyout tls.key -out tls.csr \
  -subj "/CN=<FQDN>" -addext "subjectAltName=DNS:<FQDN>,IP:<VIP-A>"
# 2) 사내 CA 가 발급한 인증서(중간 CA 가 있으면 서버 인증서 뒤에 이어 붙인 체인)를 tls.crt 로
openssl x509 -in tls.crt -noout -subject -enddate -ext subjectAltName      # SAN 두 항목·만료일 확인
# 3) ns 가 아직 없으면(첫 설치 — S2 가 만든다) 먼저: kubectl --context <ctx> apply -f deploy/k8s/00-namespace.yaml
kubectl --context <ctx> -n dms create secret tls <PORTAL_TLS_SECRET> --cert=tls.crt --key=tls.key
```

- 없으면 S2 가 WARN 으로 알리고 **S8 이 멈춘다**(`install.sh tls_gate`). 사내 CA 발급을 기다리는 동안 S2b~S7 을 먼저 끝내 둘 수 있다.
- **교체**: 같은 이름으로 바꾸면 Traefik 이 다시 읽는다 — 재기동 없음(**미검증** — 테스트베드에서 교체를 재 보지 않았다, §2g-6).

  ```sh
  kubectl --context <ctx> -n dms create secret tls <PORTAL_TLS_SECRET> --cert=new.crt --key=new.key --dry-run=client -o yaml \
    | kubectl --context <ctx> apply -f -
  openssl s_client -connect <VIP-A>:443 -servername <FQDN> -CAfile <사내CA> </dev/null 2>/dev/null | openssl x509 -noout -enddate -subject
  sh deploy/ingress-probe.sh --context <ctx> --domain <FQDN> --cacert <사내CA>      # 노드마다 새 인증서 — NODE … PASS
  ```

  `-servername <FQDN>` 을 빼면 기본 인증서가 보인다(위). LB 를 거치지 않고 노드 :443 에 바로 붙어도 같은 인증서가 나온다 — 설치 호스트는 Traefik
  의 신뢰 출발지가 아니라 PROXY 헤더 없이 붙어도 받는다(ingress-probe 가 이 길로 노드마다 본다, §2g-3).
- **만료 감시가 없다.** 설치기·`verify.sh` 는 인증서 만료일을 보지 않는다. §14-2 의 달력에 넣고, 사이트 감시에 등록한다(BACKLOG).

### 3b-3. 토큰의 뜻과 보관

| 키 | 무엇을 여나 | 누가 가지나 | 보관 |
|---|---|---|---|
| `DMS_SHARED_TOKEN` | `Authorization: Bearer` — role admin, `auth=token`(`api.auth.current_identity`). 관리 조회·스토리지·정책 등은 되고, 계정·빌드·릴리스·컨트롤 상태·작업 삭제·메일 설정·k8s 연동 변경·잡 제출은 안 된다(§10-4). 에이전트 API(`/api/agent/report`·`/api/agent/k8s-sync/claim`·`/api/agent/k8s-sync/runs/{id}`)도 연다 — `x-dms-actor: node:<노드>` 로 아무 노드나 사칭할 수 있어(`routes_agent._node_from` 은 actor 이름만 대조한다), k8s 연동 호출 노드로 사칭하면 claim 응답에서 **대상 클러스터 리더 토큰을 평문으로** 받고 인벤토리 결과(RoleBinding·PVC)를 올려 **사용자 PV 권한 맵핑을 바꿀 수 있다** — 곧 노출되면 모든 사용자 PV 에 대한 root 잡 권한과 같다 | **모든 dms-agent 파드**(env) — 노드 root 는 읽을 수 있다 | 노드 root 와 같은 신뢰 수준으로 보되, 범위는 한 노드가 아니라 **모든 사용자 PV 권한**이다. 스크립트에 쓰면 그 스크립트도 그 수준이다 |
| `DMS_ADMIN_TOKEN` | `POST /api/admin/accounts` 의 `x-admin-token` 헤더 하나. **admin 계정을 아무 이름으로나 만든다** — 특권 목록 이름이면 특권 운영자가 된다. 첫 관리자 뒤에도 늘 동작한다(`routes_auth.create_admin_account` — 토큰 경로에는 특권 이름 가드가 없다) | 시크릿 `dms-secrets` + 그것을 `envFrom` 으로 받는 dms-api·dms-controller 컨테이너(initContainer `migrate` 포함)의 env(`k8s/40-api.yaml`·`41-controller.yaml`). dms-agent 는 공유 토큰만 받는다 | **ns `dms` 의 시크릿 읽기 권한 = DMS 최고 권한**이다. 노출면은 `secrets` get·list·watch 만이 아니다 — `pods/exec`·`pods/attach`(api·controller 파드 안에서 `env`), 파드·워크로드 생성(시크릿을 마운트·envFrom 하는 파드)도 같은 값을 읽는다. 이 권한들을 클러스터 관리자로 좁힌다(§0-6). 값을 다른 곳에 복사하지 않는다 |
| `DMS_SESSION_SECRET` | 세션 쿠키 서명(`api/app.py create_app` 의 `SessionMiddleware`), 비밀번호 봉인 키 유도(`PasswordTransport`), DB 에 저장한 메일 키·k8s 토큰의 봉인 | 시크릿 + api·controller env(관리 토큰과 같은 노출면) | 바꾸면 3b-4 의 일이 생긴다. 옛 덤프를 복원하려면 그 시점의 값이 필요하다(§5-3) |
| `DMS_DATABASE_URL` | CNPG `dms-pg-rw` 접속(비밀번호 = `dms-pg-app`) | api·controller | §2f-2 |

- 공유 토큰 경로의 `x-dms-actor` 헤더는 `node:<노드 이름>`(에이전트)만 받는다. 다른 값은 400 `invalid_actor` 다(`api.auth.current_identity`) — 스크립트는 이 헤더를 싣지 않는다.
- 관리 토큰을 끄는 스위치는 없다(필수 키 — 비거나 자리표시자면 기동 거부). 첫 관리자를 만든 뒤 3b-4 로 새 무작위 값으로 바꿔 두면, 그 사이 셸
  기록·임시 파일에 남았을 수 있는 옛 값이 죽는다. 회전 도구와 부트스트랩을 닫는 옵션은 BACKLOG 「`DMS_ADMIN_TOKEN` 회전·끄기」에 올렸다.
- 토큰으로 만든 계정은 감사 로그에 actor `token:admin-token` 으로 남는다(`routes_auth.create_admin_account`).

### 3b-4. 회전

시크릿을 바꾼 뒤 `sh deploy/install.sh --context <ctx> --only S11` 을 돌린다. S11 이 `dms-secrets` 의 resourceVersion 변화를 보고 api·controller·agent
를 다시 띄운다(`install.sh stage_S11` 의 `config_rev`). 값은 stdin 으로만 넣는다. 아래 명령 모양은 이 문서를 쓰며 실행해 보지 않았다(**미검증**):

```sh
# 예: 공유 토큰. 키 이름만 바꿔 DMS_ADMIN_TOKEN 도 같다. DMS_SESSION_SECRET 은 "$(openssl rand -base64 32)"
printf '{"stringData":{"DMS_SHARED_TOKEN":"%s"}}' "$(openssl rand -hex 24)" \
  | kubectl --context <ctx> -n dms patch secret dms-secrets --type=merge --patch-file=/dev/stdin
sh deploy/install.sh --context <ctx> --only S11      # 「설정이 바뀌어 다시 띄웠다: … ds/dms-agent」
```

| 바꾼 것 | 따라오는 일 | 할 일 |
|---|---|---|
| `DMS_SHARED_TOKEN` | 옛 토큰을 쓰는 스크립트는 401 `invalid_token`. 재시작 전까지 에이전트 보고가 잠깐 실패한다 | 스크립트의 토큰을 바꾼다. 노드 화면에서 보고가 다시 신선한지 본다 |
| `DMS_ADMIN_TOKEN` | 없음 | — |
| `DMS_SESSION_SECRET` | 모든 세션 쿠키의 서명이 틀어져 **전원 다시 로그인**. 포탈은 봉인 키를 다시 받는다(kid 불일치 422 → 재수신). DB 의 메일 키·k8s 토큰이 열리지 않는다 — 메일 설정에 「읽을 수 없습니다」, knox_relay 발송은 `relay_misconfigured`, 클러스터 동기화 실패 | 포탈 메일 설정에서 키를 다시 넣는다(§11). 「k8s 연동」에서 클러스터마다 토큰을 다시 넣는다(§6-5). 옛 값은 암호화한 사본으로 남겨 둔다(옛 덤프 복원용) |
| DB 비밀번호 | `dms-pg-app`·DB 롤·`DMS_DATABASE_URL` 셋이 함께 바뀌어야 한다 | 회전 절차는 아직 없다(**미검증** — BACKLOG). `pg-secrets.py` 는 이미 있는 `dms-pg-app` 을 덮지 않는다 |
| 포탈 TLS | 없음(재기동 없음 — 미검증, §2g-6) | 3b-2 |
| 대상 클러스터 리더 토큰 | 다음 동기화부터 401 | 「k8s 연동」에서 새 토큰(§6-5) |

### 3b-5. 공용 스토리지 밖 사본

공용 스토리지를 통째로 잃으면 PG 복제본 3개·덤프·레지스트리·결과 파일이 함께 사라진다. 그때를 위한 사본이다(보관처·주기·담당은 사이트가 정한다 —
설계 §11 U6·S5).

| 무엇 | 어떻게 | 상세 |
|---|---|---|
| `dms-secrets`·`dms-pg-app` | `kubectl --context <ctx> -n dms get secret dms-secrets dms-pg-app -o yaml \| gpg --symmetric --cipher-algo AES256 -o dms-secrets-$(date -u +%Y%m%d).yaml.gpg` | §2f-5 |
| pg_dump | 백업 노드에서 root 로 `rsync -a --numeric-ids <SHARED_DIR>/dms/backup/pg/ <밖 경로>/`(덤프에는 비밀번호 해시와 봉인된 키가 있다 — 암호화한 곳에) | §2f-5 |
| 현장에서 빌드한 DMS 이미지 태그 | `skopeo sync --src docker --dest dir --src-tls-verify=false <REGISTRY>/dms <밖 경로>`(dms-agent·dms-mpifileutils 도) | §2e-5 |
| TLS 키·인증서 | 사이트 PKI 보관 | 3b-2 |
| `values.env`, 사이트 `patch-config.yaml` 로컬 수정, `kube-context` | 비밀은 아니지만 재설치에 필요하다 | §3-4 |
| 지금 도는 DMS 태그(릴리스할 때마다 적어 둔다) | `kubectl --context <ctx> -n dms get deploy/dms-api ds/dms-agent -o jsonpath='{range .items[*]}{.spec.template.spec.containers[0].image}{"\n"}{end}'`, `kubectl --context <ctx> -n dms get cm dms-config -o jsonpath='{.data.DMS_JOB_IMAGE}'`, 포탈 릴리스 화면의 job-image 태그(DB 값 — env 를 이긴다) | §5-6 — values 태그가 `live` 면 빈 새 클러스터에서는 읽을 곳이 없다 |

## 4. 업그레이드 — 앱(새 DMS 커밋)·잡 이미지·애드온

### 4-1. 무엇을 무엇으로 올리나

| 대상 | 수단 | 상세 |
|---|---|---|
| 앱 이미지 3종(`dms`·`dms-agent`·`dms-mpifileutils`) | 포탈 빌드 → 포탈 릴리스(세션 관리자). 릴리스 순서는 서버가 강제한다(agent → api → controller — `repositories.releases.ROLLOUT_ORDER`) | §8·§9 |
| 잡 이미지(`dms-mpifileutils` — 다음 잡 파드가 쓸 이미지) | 릴리스 화면의 `job-image` 행(`routes_releases.JOB_IMAGE_COMPONENT`). DB `control_state.job_image` 에 저장되고 다음 잡부터 바로 쓰인다(재시작 없음) | §9-5 |
| 매니페스트·RBAC·securityContext·새 설정 키(새 커밋의 base) | `sh deploy/install.sh --context <ctx> --only S11`. 포탈 릴리스는 이미지만 바꾼다 — 그래서 릴리스 화면의 「보안 설정 드리프트」 배지는 S11 로만 사라진다 | §9-8·§3 |
| DMS 소유 플랫폼 층 값(레지스트리·입구 Ingress·DB 설정) | 그 단계 재실행(§3-4 (c)) | §2e~§2g |
| 애드온 버전(CNPG·레지스트리 이미지) | 새 커밋의 `deploy/versions.env`·`deploy/images.lock` → 인터넷 노드 push → `--only <S> --upgrade-addon <x>` | 4-3 |
| Volcano | 클러스터 운영자(DMS 미동봉) | §0-6 |
| Traefik(입구) | 클러스터 운영자(RKE2 는 RKE2 업그레이드 때 `rke2-traefik` 차트가 함께 오른다 — DMS 는 판을 고정·판정하지 않는다). 올린 뒤 `--only S8,S12` | §2g-6 |
| PG 메이저 | 별도 설계가 없다 | §2f-6 |
| K8s minor | 신·구 K8s 를 모두 지원하는 판으로 애드온을 먼저 올린다 | §2f-6 |

dms-api·dms-controller 에는 initContainer `migrate` 가 있다(`deploy/k8s/40-api.yaml`·`41-controller.yaml`). 새 `dms` 이미지를 릴리스하면 그 파드가 뜰 때
스키마 마이그레이션이 먼저 돈다(PG 어드바이저리 락으로 직렬화).

### 4-2. 새 DMS 커밋으로 올리는 순서

먼저 4-5 점검표를 끝낸다. 새 커밋 범위의 `docs/CHANGELOG.md` 항목을 읽는다 — 「잡 이미지 재빌드 필요」·마이그레이션·롤백 주의가 거기 적힌다.

1. **체크아웃을 같은 커밋으로**(설치 호스트·빌드 노드 둘 다): `git -C <체크아웃> fetch` → `git -C <체크아웃> checkout <커밋>`(또는 pull).
   사이트 `patch-config.yaml` 로컬 수정이 살아 있는지 본다(§3-4 (b)).
   성공: 두 호스트의 `git -C <체크아웃> rev-parse HEAD` 가 같다. 빌드 노드 체크아웃은 git worktree 가 아니어야 한다(§8 1)).
2. **values 태그**: `DMS_TAG`·`DMS_AGENT_TAG`·`MFU_TAG` 를 `live` 로 둔다(권장). 렌더가 지금 클러스터에서 도는 태그를 읽어 채운다(`render.sh _live_tag`).
3. **계획**: `sh deploy/install.sh --context <ctx> --plan` → `RESULT: PASS`(또는 이해한 WARN). 끝난 `dms-migrate` Job 의 `field is immutable` 은 앱 층
   PASS 로 본다(`install-detect.py` 머리 주석 「앱 업그레이드 함정」). 애드온 감지 줄에 `D<`(DMS 가 설치한 낮은 판) WARN 이 보이면 4-3 이다.
4. **매니페스트·RBAC·설정·플랫폼 층 먼저**: `sh deploy/install.sh --context <ctx>`(전체 — 끝난 단계는 no-op·검증이다). S11 은 앱 층(`app/`)만
   적용하므로, 새 커밋이 DMS 소유 플랫폼 층(`deploy/platform/` — 입구 Ingress, DB 파라미터·덤프 CronJob, 레지스트리 설정, PriorityClass)을
   바꿨으면 S4·S8·S10 이 감지 D=(같은 판) → SSA 재적용으로 넣는다(`install-detect.py` 머리 표의 D= 행 — 플랫폼은 reapply). 이미지는 `live` 라
   그대로이고, 새 base·RBAC·설정 키가 S11 로 들어간다. 새 이미지가 쓸 RBAC 가 이미지보다 먼저 들어가야 한다(§9-6). 성공: 끝 줄 `RESULT: PASS`.
   - lock digest 가 바뀐 커밋이면 S4 뒤 PAUSE(exit 10)로 push 명령이 나온다 → 인터넷 노드에서 push → 찍힌 `--stage S5` 재개 줄.
   - **2026-11-16 이전 커밋(Envoy Gateway 입구)에서 올리는 사이트**는 §2g-8 「전환 순서」를 따른다 — values.env 의 `GATEWAY_*` 는 note 뒤 무시되고,
     `PORTAL_DOMAIN` 이 Ingress host 로 문법 검사를 받고, S8 이 Ingress 를 새로 만든다. 옛 Envoy 객체는 설치기가 감지하지도 지우지도 않는다. LB 풀을
     Traefik 노드로 옮기는 것은 아래 6(새 `dms` 이미지 릴리스 — 본문 상한·keep-alive 120 이 든 앱) 뒤에 한다. 옛 앱은 Traefik 뒤에서 본문 있는 POST 가
     간헐 502 다(§2g-8 3).
   - 서드파티 애드온 층(`deploy/addons/` — CNPG)은 같은 판이면 다시 적용하지 않는다(D= 서드파티 = none). 판이 바뀌었으면 감지가
     D<(WARN 「--upgrade-addon …」)로 알리고 그대로 둔다 → 4-3. 같은 판의 DMS 패치 층만 바뀌었으면 그 단계를 `--upgrade-addon <x>` 로(D= + 플래그 →
     reapply).
   - 새 커밋이 앱 층만 바꿨으면(`git diff --stat <옛 커밋>..<새 커밋> -- deploy/platform deploy/addons deploy/versions.env deploy/images.lock` 가 빔)
     `--only S11` 로 줄여도 된다.
5. **빌드**: 개발 PC 에서 같은 커밋으로 「이미지 빌드 전 게이트」(§8)를 통과시킨 뒤, 포탈 운영 → 빌드에서 이미지와 태그를 골라 제출한다(태그는 제안 칩을
   그대로 — 이 사이트의 다음 번호). 화면의 기본 선택은 `dms` 하나다(`BuildForm.tsx` `DEFAULT_IMAGES`).
   - **6 에서 dms-agent 를 새 태그로 올리려면 세 이미지(`dms-mpifileutils`·`dms`·`dms-agent`)를 모두 체크한다.** dms-agent 는 이 빌드 태그의
     `dms`·`dms-mpifileutils` 를 FROM 한다(`build_manifests._SCRIPT` 의 `MFU_IMAGE=$DMS_BUILD_REGISTRY/dms-mpifileutils:$DMS_BUILD_TAG`). 새 태그에는
     mpifileutils 가 아직 없으므로 빼면 agent 빌드가 pull 에서 실패한다(화면도 같은 경고를 낸다). mpifileutils 는 소스 컴파일이라 오래 걸린다(§8 5)).
     세 이미지 모두 DMS 소스(`src/`)를 담는다 — 에이전트 코드(`dms-agent`)나 잡 러너 코드(`dms-mpifileutils` = 잡 이미지)가 바뀐 커밋이면 셋 다 빌드한다.
   - 에이전트·잡 러너를 올리지 않을 커밋이면 `dms` 만 빌드하고 6 에서 dms-api·dms-controller 만 새 태그로 낸다(dms-agent·job-image 행은 고르지 않는다 —
     레지스트리에 없는 태그는 422 `unknown_tag`, §9-3).
   성공: 빌드 이력의 그 빌드가 Succeeded. 실패 사유는 §8 3b).
6. **릴리스**: 운영 → 릴리스에서 dms-agent·dms-api·dms-controller(+ 필요하면 job-image)를 새 태그로 한 번에 제출한다 — **5 에서 빌드한 이미지만**
   (`routes_releases` 가 레지스트리에 그 태그가 있는지 본다).
   성공: 행마다 `Applied`. 컨트롤러 자기 갱신 동안 화면이 약 30초 멈추는 것은 정상이다(§9-2). 실패하면 남은 행은 `rollout_aborted` 로 닫힌다(§9-7 — §5-1).
7. **명시 태그를 쓰는 사이트만**: values.env 의 태그를 릴리스한 태그로 바꾼다. 안 바꾸면 다음 S11 의 가드가 exit 3 으로 멈춘다(§3-3).
8. **검증**: `VERIFY_NO_PROBES=1 CACERT=<사내CA> sh deploy/install.sh --context <ctx> --only S12` → `RESULT: PASS`(설치 호스트가 사내 CA 를 믿으면
   `CACERT` 생략 — 없으면 VIP 경유 readyz 가 TLS 실패로 WARN 이 된다). 포탈 릴리스 화면에 드리프트 배지가 없다.

포탈을 쓰지 않는 길도 있다: 같은 태그를 `build-and-push.sh` 로 만들고(§1) values.env 에 명시한 뒤
`ALLOW_IMAGE_CHANGE=1 sh deploy/install.sh --context <ctx> --only S11`. 매니페스트와 이미지가 한 번에 바뀐다. 이 길에서는 잡 이미지(DB 값)는 바뀌지 않는다
— 릴리스 화면에서 job-image 를 따로 올린다(`job_image.resolve_job_image` — DB 값이 `DMS_JOB_IMAGE` 를 이긴다). 진행 중 릴리스 행(Pending·Applying)이
있으면 끝난 뒤에 한다 — 컨트롤러가 그 행의 이미지로 다시 patch 할 수 있다(§5-1 「포탈이나 컨트롤러가 깨져」 2).

### 4-3. 애드온 업그레이드

공통: 새 판은 DMS 커밋으로 온다(`deploy/addons/<이름>/<판>/`·`deploy/versions.env`·`deploy/images.lock` — 한 커밋). 그 커밋으로 체크아웃을 맞춘 뒤 인터넷 노드에서
`sh deploy/push-images.sh --registry <REGISTRY> --run`(S5 와 같은 명령 — 멱등)으로 새 이미지를 넣는다. 시험 클러스터가 있으면 먼저 거기서 한다. 점검 창에서:

| 애드온 | 명령 | 먼저 할 것 | 상세 |
|---|---|---|---|
| 클러스터 내 레지스트리 | `sh deploy/install.sh --context <ctx> --only S4 --upgrade-addon registry` | **마스터 전부** `sudo crictl pull <REGISTRY>/registry:<새 태그>`(고정 패턴 `registry:*` 가 덮는다). 빌드·push 가 없는 창 | §2e-5 |
| CNPG(minor) | `sh deploy/install.sh --context <ctx> --only S9 --upgrade-addon cnpg` | **minor 는 하나씩**. 설치기가 직전 덤프 → SSA → 롤아웃 → healthy 대기를 한다 | §2f-6 |
| PG minor | `versions.env` 의 `PG_IMAGE` + lock 줄 → push → db-cluster·db-backup·cnpg 층 적용 | 이미지와 `postgresql.parameters` 를 한 번에 바꾸지 않는다. 설치기 플래그 조합(`--upgrade-addon cnpg,db` 등)은 **미검증** — §2f-6 의 손 판을 따른다 | §2f-6 |

- 가드는 `--upgrade-addon` 으로 고른 층에서만 열린다. **전역 `ALLOW_IMAGE_CHANGE=1` 을 쓰지 않는다**(§3-3).
- 설치기는 다운그레이드하지 않는다 — 감지 D>(DMS 소유·높은 판)는 keep 이다(`install-detect.py` 머리 주석). 되돌림은 §5.
- 남이 설치한 애드온(감지 C·C!)의 업그레이드는 그 소유자 몫이다. DMS 는 EOL·지원 범위를 WARN 으로만 알린다.
- **Traefik 은 DMS 애드온이 아니다** — 버전 핀·lock 줄·`--upgrade-addon` 대상이 없다(`--upgrade-addon traefik` 은 판정만 한다). 판 올리기·보안 패치
  (v3.7.4 는 그 뒤 공개된 보안 권고에 들고 수정판은 v3.7.18 이상 — §2g-6)는 클러스터 운영자 몫이고, Traefik DaemonSet 이 노드마다 롤링한다(§2g-6
  「롤링 공백」). 올린 뒤 `sh deploy/install.sh --context <ctx> --site <site> --only S8,S12` 로 다시 본다. 옛 Gateway API·Envoy Gateway 업그레이드
  행은 2026-11-16 입구 교체로 없앴다.

### 4-4. 레지스트리 주소 변경·DB 이관 뒤

- 잡 이미지는 DB(`control_state.job_image`)에 레지스트리 주소째 들어 있고 env 를 이긴다. 레지스트리 주소를 바꿨거나 DB 를 옮겨 왔으면
  릴리스 화면에서 job-image 를 지금 레지스트리의 태그로 **다시 릴리스**한다(S13 체크리스트의 같은 줄).
- 레지스트리 주소 변경 자체는 지원하는 일상 절차가 아니다 — 여섯 곳과 노드 신뢰 drop-in·고정 이미지 이름이 함께 바뀐다(§2e-1). 절차는 **미검증**이다.

### 4-5. 업그레이드 전 점검표

- [ ] 최신 덤프가 있다: `J=dms-pg-dump-pre-$(date -u +%Y%m%d%H%M); kubectl --context <ctx> -n dms create job --from=cronjob/dms-pg-dump "$J"` →
      §2f-4 6) 의 Complete·Failed 루프(`job/dms-pg-dump-first` 자리에 `"$J"`, 600초 — `wait --for=condition=complete` 는 Failed 를 못 알아채 쓰지 않는다)가
      PASS → `kubectl --context <ctx> -n dms logs "job/$J"` 의 덤프 파일 이름을 적어 둔다(`<SHARED_DIR>/dms/backup/pg/`).
- [ ] 공용 스토리지 밖 사본이 최신이다(§3b-5).
- [ ] 진행 중인 잡·빌드가 없다(포탈 전체 작업·빌드 이력, `kubectl --context <ctx> -n dms get vcjob,pods`). 새 제출을 막으려면 컨트롤 상태의 「유지보수」
      (신규 제출 503 `maintenance_mode` — `routes_requests.reject_when_maintenance`), 잡 진행까지 세우려면 「drain」(stepper 정지 — `stepper.JobStepper.run_once`).
- [ ] 작업 삭제 정리 대기가 0 이다(작업 목록 툴바 「정리 중 0건」 — 롤백 가능성 대비, §12 「정리 대기 중 이미지 롤백 금지」).
- [ ] 레지스트리가 정상이다(`kubectl --context <ctx> -n dms get deploy dms-registry` Ready — 릴리스는 레지스트리가 정상일 때만, §2e-5).
- [ ] 점검 창을 사용자에게 알렸다.

## 5. 롤백·복구

| 무엇을 되돌리나 | 길 | 절 |
|---|---|---|
| 앱 이미지·잡 이미지 | 포탈 릴리스로 옛 태그(포탈·컨트롤러가 깨졌으면 values 명시 태그 + `--only S11` — 진행 중 릴리스 행 먼저) | 5-1 |
| 매니페스트·설정 | 옛 커밋 → 설치기 전체 실행(앱 층만 바뀌었으면 `--only S11`) | 5-2 |
| DB 데이터 | 같은 이름 `dms-pg` 재생성 + `pg_restore` | 5-3 |
| 입구·인증서 | Ingress 재적용(`--only S8`), PROXY v2 ↔ trustedIPs 순서, 옛 인증서, (옛 판에서 옮긴 사이트) LB 풀 되돌림 | 5-4 |
| 공용 디렉터리·레지스트리 | 전환 런북 역순 | 5-5 |
| 클러스터·공용 스토리지 상실 | 재해 복구 | 5-6 |

### 5-1. 앱 이미지 되돌리기

- 운영 → 릴리스에서 컴포넌트마다 옛 태그를 골라 다시 릴리스한다. 롤백 버튼은 없다 — 실패한 배치는 남은 행을 `rollout_aborted` 로 닫아 반쯤 섞인
  조합을 남기지 않으므로, 이력에서 옛 태그를 골라 다시 내면 된다(§9-7). 잡 이미지도 job-image 행에서 옛 태그로 낸다(다음 잡부터).
- **`kubectl rollout undo` 를 쓰지 않는다.** 포탈 릴리스 기록·드리프트 판정·values `live` 가 모르는 상태가 되고, 옛 ReplicaSet 은 옛 레지스트리 주소나
  옛 securityContext 를 담고 있을 수 있다(레지스트리를 옮긴 사이트에서는 받을 곳이 없는 이미지로 돌아간다).
- 명시 태그 사이트는 values.env 의 태그도 되돌린다(아니면 다음 S11 가드 exit 3).
- **스키마는 되돌아가지 않는다.** 새 이미지의 initContainer `migrate` 가 이미 스키마를 바꿨다. 옛 이미지가 새 스키마에서 도는지는 변경마다 다르다 —
  일반 보장은 없다(**미검증**). 그 커밋의 CHANGELOG 항목을 먼저 본다(예: §12 「정리 대기 중 이미지 롤백 금지」, §12b — api 와 controller 둘 다 새 이미지여야
  한다). 옛 이미지가 새 스키마에서 못 돌면 업그레이드 직전 덤프로 DB 를 되돌린다(5-3 — 그 뒤의 쓰기는 잃는다).
- **포탈이나 컨트롤러가 깨져 릴리스를 낼 수 없을 때**(포탈을 쓰지 않는 되돌림 — §4-2 끝의 길과 같다). 릴리스를 실제로 patch 하는 것은 dms-controller 의
  RolloutWatcher 다(`controller.build_loops` 의 rollout-watcher) — 나쁜 릴리스가 dms-api(포탈)나 dms-controller 를 망가뜨렸으면 옛 태그 릴리스를 내지도,
  처리하지도 못한다.
  1. **진행 중 릴리스 행을 먼저 본다.** 포탈이 열리면 릴리스 이력, 아니면(psql 명령 모양은 **미검증** — §2f-5 의 `pg_restore` 와 같은 로컬 접속):
     `P=$(kubectl --context <ctx> -n dms get cluster dms-pg -o jsonpath='{.status.currentPrimary}')` →
     `kubectl --context <ctx> -n dms exec "$P" -c postgres -- psql -d dms -c "SELECT id, component, tag, state, applied_at FROM releases WHERE state IN ('Pending','Applying') ORDER BY seq"`.
  2. 행이 남아 있으면 **살아난 컨트롤러의 첫 틱**이 그 행을 처리한다(`rollout_watcher.RolloutWatcher.run_once`). Applying 행은 `applied_at` 부터
     `DMS_ROLLOUT_TIMEOUT_SECONDS`(기본 600초, Deployment 는 3배 = 1800초 — DaemonSet 은 진행이 보이면 다시 센다)가 지났으면 `rollout_timeout` 으로 Failed 가
     되고 뒤 Pending 행도 함께 닫힌다. **안 지났으면 spec 이미지가 목표와 다르다고 보고 그 행의 이미지로 다시 patch 한다**(「크래시 복구」) — 손으로 되돌린
     것을 덮는다. 컨트롤러가 죽어 있는 동안에는 시한이 지나도 닫을 주체가 없으니, 손 되돌림은 그 시각이 지난 뒤에 한다. Pending 이 머리인 배치(시작 전)는
     살아난 컨트롤러가 그대로 적용한다 — 원하는 태그가 아니면 그 배치가 끝난(Applied·Failed) 뒤 다시 되돌린다.
  3. values.env 의 `DMS_TAG`·`DMS_AGENT_TAG`·`MFU_TAG` 를 되돌릴 옛 **명시** 태그로 바꾼다(`live` 면 지금의 나쁜 태그를 다시 읽는다 — `render.sh _live_tag`).
  4. `ALLOW_IMAGE_CHANGE=1 sh deploy/install.sh --context <ctx> --only S11` → 끝 줄 `RESULT: PASS`. in-cluster 레지스트리면 S11 이 그 태그가 레지스트리에
     있는지 먼저 본다(`install.sh check_app_images`). 가드는 이 실행에서만 열린다(§3-3).
  5. 포탈이 돌아오면 릴리스 화면의 현재 이미지·드리프트를 확인하고, 잡 이미지(DB 값)는 job-image 행으로 따로 되돌린다. values 태그는 다시 `live` 로 둬도 된다.

### 5-2. 매니페스트·설정 되돌리기

- 체크아웃을 옛 커밋으로(사이트 `patch-config.yaml` 로컬 수정 포함) → `sh deploy/install.sh --context <ctx> --plan` → 전체 실행
  `sh deploy/install.sh --context <ctx>`(4-2 4 와 같은 이유 — 플랫폼 층도 옛 값으로 다시 적용된다. 되돌릴 커밋 사이에 앱 층만 바뀌었으면 `--only S11`).
  태그가 `live` 면 이미지는 그대로이고 매니페스트·설정만 돌아간다. 설정 rev 가 바뀌면 S11 이 다시 띄운다. 애드온 판은 내려가지 않는다(D> keep — §4-3).
  2026-11-16 이전 커밋(Envoy Gateway 입구)으로 되돌리는 것은 입구까지 바뀌는 일이다 — 5-4 「Envoy 입구로 되돌리기」.
- 손으로 고친 ConfigMap(임시 변경 — §3-4)은 S11 한 번이면 렌더 값으로 돌아간다.
- 플랫폼 층 값(values.env)은 값을 되돌리고 그 단계를 다시 돌린다(§3-4 (c)).

### 5-3. DB 복구

- 층: 동기 복제(노드·파드 장애 1건) · pg_dump 매시간(`17 * * * *`, 기본 336개 = 14일 — 잘못된 마이그레이션·삭제·재구축) · 공용 스토리지 밖 사본(공용 스토리지
  손실). **PITR 은 없다** — 마지막 덤프 뒤의 쓰기는 잃는다(최대 약 1시간 + 복구 시간).
- 절차는 §2f-5 「복구」 0~7 이다. 요약:
  0. 잡을 세운다: 컨트롤 상태 「유지보수」·「drain」을 켜고 도는 vcjob 을 적어 둔다(`kubectl --context <ctx> -n dms get vcjob -L dms.io/job-id,dms.io/phase`).
     끝나기를 기다리거나 요청 상세에서 취소한다 — api·controller 를 0 으로 줄여도 제출된 vcjob 은 그대로 돈다.
  1. 쓰기 멈춤: replicas 를 적고 `kubectl --context <ctx> -n dms scale deploy dms-api dms-controller --replicas=0`.
  2. 복원할 덤프를 고른다(`<SHARED_DIR>/dms/backup/pg/dms-<UTC>.dump`).
  3. `kubectl --context <ctx> -n dms delete cluster dms-pg` → PVC 가 지워지고 PV 3개가 Released.
  4. 같은 렌더로 `platform/db-cluster` SSA → 900초 폴링(같은 이름 `dms-pg`, `dms-pg-app` 그대로 — URL 이 바뀌지 않는다).
  5. primary 에 `pg_restore --no-owner --role=dms --exit-on-error --single-transaction -d dms`(덤프는 stdin — 오류면 통째로 되돌린다).
  6. scale 원복(7 의 순서 — api 먼저) → `python3 deploy/pg-secrets.py --context <ctx> check` → 덤프 한 번 떠서 확인.
  7. 복원 뒤 정리: 복원 DB 는 덤프 시점의 컨트롤 상태다 — dms-api 를 먼저 올려 유지보수·drain 을 다시 켠 뒤 dms-controller 를 올린다. 0 에서 적은 vcjob 중
     복원 DB 에 없는 잡 id 의 것은 DMS 가 지우지 않는다(`pod_gc` 는 DB 의 잡만 본다) — `kubectl --context <ctx> -n dms delete vcjob <이름>`. 덤프 뒤에
     컨펌·실행된 sync·rm 은 컨펌 대기 등으로 돌아와 있어 다시 컨펌하면 한 번 더 돈다 — 취소하고 사용자에게 알린 뒤 유지보수·drain 을 끈다.
- 조건: **원래의 `DMS_SESSION_SECRET`**(아니면 메일 키·k8s 토큰을 다시 넣는다), DB 와 결과 파일의 시점이 어긋나면 404·고아 디렉터리가 생긴다.
- CNPG·PG 를 **다운그레이드하지 않는다** — 같은 이름으로 다시 만들고 복원한다(§2f-6 「롤백」 행).
- 외부 PG 에서 옮겨 온 직후 되돌리기: `python3 deploy/pg-secrets.py --context <ctx> restore-url` →
  `kubectl --context <ctx> -n dms rollout restart deploy/dms-api deploy/dms-controller`(§2f-2 — 이관 뒤의 쓰기는 원본에 없다).
- 복구 뒤 Released PV 는 §2f-5 「Released 재활용 런북」으로 되돌린다.

### 5-4. 입구 되돌리기

- **Ingress**: 손으로 고쳤거나 지운 `dms-portal-traefik` 은 `sh deploy/install.sh --context <ctx> --site <site> --only S8` 이 렌더 값으로 다시 맞춘다
  (SSA 멱등 — 손 수정이 다른 field manager 로 남아 충돌하면 §2h 「SSA 충돌」). 사이트 값(`PORTAL_DOMAIN`·`PORTAL_TLS_SECRET`·`INGRESS_CLASS`·
  `TRAEFIK_ENTRYPOINT`)은 values.env 를 되돌리고 `--only S8,S12`.
- **PROXY v2·trustedIPs**: Traefik 설정은 사이트(클러스터 운영자) 몫이다. 끌 때는 LB 의 PROXY v2 를 먼저, 그다음 trustedIPs — 켤 때는 반대(§2g-4).
  순서를 뒤집어 trustedIPs 없이 LB 가 헤더를 붙이면 VIP 의 모든 연결이 TLS 에서 깨진다. LB 가 PROXY 를 끄고 둘 거면 출발지 경보 0 튜닝 키(§3-4 (b)).
- **인증서**: 옛 인증서로 같은 이름의 Secret 을 다시 만든다(§3b-2 「교체」).
- **Envoy 입구로 되돌리기**(2026-11-16 이전 Envoy Gateway 판에서 옮겨 온 사이트만): Envoy 정리(§2g-8 정리 런북) **전**이면 LB 풀을 옛 멤버(마스터
  :443 또는 옛 `GATEWAY_LISTEN_PORT`)로 되돌리면 옛 Envoy 가 그대로 받는다 — 새 앱(본문 상한·keep-alive 120)은 Envoy 뒤에서도 무해하다(§2g-8).
  정리 **뒤**에는 옛 커밋의 설치기로 Envoy 층(Gateway API·Envoy Gateway·`platform/gateway`)과 마스터 sysctl 을 다시 깔아야 한다 — 이 경로는
  **미검증**이고 그동안 포탈이 닿지 않는다. 설치기는 다운그레이드하지 않는다(D> keep).
- Traefik 판 되돌림·Avi·DNS 는 사이트 몫이다.

### 5-5. 공용 디렉터리·레지스트리

- 공용 디렉터리를 옮긴 뒤 되돌리기: §2d-4 의 8(같은 절차를 거꾸로 — 옛 경로는 되돌림 창 동안 지우지 않는다).
- 레지스트리: 앱이 아직 쓰지 않을 때만 걷는다(§2e-5 「걷어내기」). 저장소를 옮겼으면 옛 경로를 되돌림 창 동안 둔다(§2e-5 「저장소 이동」).

### 5-6. 재해 복구(전체 재설치)

이 절차는 리허설하지 않았다. 각 단계는 검증된 절(§2f-5 복구, §2e-4 반입, 설치기 S2 의 「있으면 만들지 않는다」)을 엮은 것이고, 엮은 순서 전체는
**미검증**이다(BACKLOG). 시험 클러스터에서 한 번 돌려 본 뒤 사이트 런북으로 삼는다.

준비물: 공용 스토리지 밖 사본(3b-5) — 시크릿 사본(`dms-secrets`·`dms-pg-app`), 마지막 덤프, 현장 빌드 DMS 태그의 사본, TLS, values.env, 사이트
`patch-config.yaml` 수정, **마지막으로 돌던 DMS 태그 기록**(3b-5 — api·agent·잡 이미지). 그리고 덤프 시점과 같은(또는 더 새) DMS 커밋.

| 경우 | 다른 점 |
|---|---|
| A. 클러스터만 잃었다(공용 스토리지 그대로) | 덤프·레지스트리 저장소·결과 파일이 남아 있다. 같은 `SHARED_DIR` 를 쓴다 |
| B. 공용 스토리지도 잃었다 | 모두 밖 사본에서 되살린다. 결과 파일은 돌아오지 않는다(옛 작업의 로그·리포트는 404) |

1. **노드 준비**(§0-5 1a~1g). B 면 새 공용 디렉터리에 S0(`shared-dir-init.sh`) → 새 `SHARED_DIR_ID` 를 values 에. A 면 `--check` 로 센티널을 확인한다.
   A 의 `pgdata/dms-pgdata-0N` 에는 옛 PGDATA 가 남아 있다 — S10 전에 §2f-5 「Released 재활용 런북」 2 처럼 다섯 개 모두 `.retired` 로 옮기고 빈 26:26
   0700 디렉터리 + `.dms-pv-id` 를 다시 만든 뒤 `sudo sh deploy/shared-dir-init.sh --check <SHARED_DIR>` PASS(**필수** — 옮기지 않으면 새 클러스터의 PV 가
   Available 인데 옛 `pgdata/` 가 보여 S10 사전 검사가 FAIL 로 멈춘다 — `pg-path-probe.py` precheck). 옛 PGDATA 는 덤프 밖의 유일한 사본일 수 있으니
   지우지 않는다.
2. **ns 와 시크릿 복원**(S2 전): `kubectl --context <ctx> apply -f deploy/k8s/00-namespace.yaml` → 시크릿 사본을 메모리 파일시스템(0600)에 풀고 `metadata` 의
   `resourceVersion`·`uid`·`creationTimestamp`·`managedFields` 를 지운 뒤 `kubectl --context <ctx> create -f <파일>` → 파일을 지운다.
   S2 는 이미 있는 `dms-pg-app`·`dms-secrets` 를 바꾸지 않는다(`install.sh stage_S2` — `ensure-app` 은 덮지 않고 `init-secrets` 는 없을 때만).
   원래 `DMS_SESSION_SECRET` 이 돌아와야 덤프 안의 봉인된 메일 키·k8s 토큰이 열린다. 사본이 없으면 S2 가 새로 만들게 두고 메일 키·k8s 토큰을 나중에 다시 넣는다.
3. **DB 까지만 설치**:
   - 먼저 values 태그: values.env 의 `DMS_TAG`·`DMS_AGENT_TAG`·`MFU_TAG` 가 `live` 면 3b-5 에 적어 둔 명시 태그로 바꾼다. 빈 새 클러스터에는 라이브
     워크로드가 없어 `live` 를 읽지 못하고, 설치기는 매 실행 처음에 렌더하므로 `--plan` 부터 「태그가 'live' 인데 클러스터에서 라이브 이미지를 읽지
     못했습니다」(exit 2)로 멈춘다(`render.sh _live_tag`). 4 의 「같은 태그 이름」도 이 기록이다. 복구가 끝나면(7 뒤) 다시 `live` 로 둬도 된다.
   - TLS(§3b-2) → `sh deploy/install.sh --context <ctx> --plan` →
     `sh deploy/install.sh --context <ctx> --only S1,S2,S2b,S3,S4,S5,S6,S7,S8,S9,S9b,S10`. **전체 실행이나 PAUSE 가 찍은 `--stage S5` 줄을 쓰지 않는다** —
     S11 까지 가서 migrate 가 빈 DB 에 스키마를 만들면 6 의 복원과 겹친다. A 는 레지스트리 저장소가 그대로라 S5 대조가 통과하고 S10 까지 간다.
     B 는 S5 에서 멈춘다(레지스트리가 비었다) — 4 를 한 뒤 `--only S5,S6,S7,S8,S9,S9b,S10` 으로 잇는다.
4. **이미지**(B): 인터넷 노드에서 `push-images.sh --run`(lock). DMS 태그는 사본에서 `skopeo sync --src dir --dest docker --dest-tls-verify=false <사본 경로> <REGISTRY>/…`
   (**미검증**)로 넣거나, 같은 커밋·**같은 태그 이름**으로 `build-and-push.sh` 를 다시 돌린다. DB 의 릴리스 기록·잡 이미지가 그 태그 이름을 가리킨다.
5. **빈 DB 확인**: S10 이 끝나면 빈 `dms-pg` 가 떠 있다. S10 은 빈 DB 의 첫 덤프를 하나 만든다 — 복원할 덤프 파일 이름을 미리 적어 두고 헷갈리지 않는다.
6. **복원**(S11 전 — migrate 가 빈 DB 에 스키마를 만들기 전에): `P=$(kubectl --context <ctx> -n dms get cluster dms-pg -o jsonpath='{.status.currentPrimary}')` →
   `kubectl --context <ctx> -n dms exec -i "$P" -c postgres -- pg_restore --no-owner --role=dms --exit-on-error --single-transaction -d dms < <덤프>`.
7. **앱**: `sh deploy/install.sh --context <ctx> --only S11,S12`. migrate 는 복원한 스키마를 이 커밋으로 올린다. S13 체크리스트를 다시 본다.
8. **뒤처리**: 레지스트리 주소가 바뀌었으면 job-image 재릴리스(§4-4). 세션 시크릿이 새것이면 메일 키(§11)·k8s 토큰(§6-5)을 다시 넣는다. 컨트롤 상태의 빌드 노드
   이름이 새 클러스터의 노드와 다르면 고친다(§6-3). §7 스모크 → §6-8 확인표 → 개통.

## 6. 설치 뒤 설정 — 사용자에게 열기 전

S12 가 끝나면 S13 이 사후 체크리스트를 찍는다(`install.sh stage_S13`). 이 장은 그 항목을 순서대로 푼다. **순서가 중요하다**: 6-1(첫 관리자) → 6-2(메일 —
사용자 게이트) → 6-3 → 6-4 → 6-5 → 6-6 → 6-7(개통 — 그 안에서 S12 → 6-8 확인표 → DNS → 공지). 6-8 의 확인표를 모두 채우기 전에는 DNS 등록·사용자 공지를 하지 않는다.

**이 단계에서 포탈에 들어가는 법**(공식 DNS 전):
- Avi VS-443 이 열려 있으면 운영자 PC 의 hosts 에 `<VIP-A> <FQDN>` 를 넣고 `https://<FQDN>` 으로 연다(권장 — 실제 경로 그대로).
- 아니면 설치 호스트에서 `kubectl --context <ctx> -n dms port-forward svc/dms-api 18080:http` 후 `http://localhost:18080`. 세션 쿠키가 `Secure` 이고
  (`DMS_SESSION_COOKIE_SECURE: "true"` — prod 렌더) 포탈이 WebCrypto 로 비밀번호를 봉인하므로, 브라우저가 localhost 를 보안 컨텍스트로 다뤄야 로그인된다
  (**미검증** — 브라우저마다 다르다). 6-1 의 curl 은 이 문제와 무관하다.

### 6-1. 첫 관리자(`<LOCAL_ADMIN>`)

계정이 0개인 새 사이트에서 포탈 항목을 시작하려면 먼저 있어야 한다. `x-admin-token` 부트스트랩만 평문 비밀번호를 받는다
(`routes_auth.create_admin_account`). 토큰은 0600 파일로만 curl 에 넘기고(출력·argv 없음), 비밀번호는 셸 내장 `printf` 로 stdin 에 싣는다.
port-forward 라 Avi·DNS 전에도 된다. 명령은 S13 이 찍는 것과 같다.

한 터미널(설치 호스트):

```sh
kubectl --context <ctx> -n dms port-forward svc/dms-api 18080:http
```

다른 터미널(POSIX sh — 비밀번호에 `"` 와 `\` 는 쓰지 않는다):

```sh
umask 077; H=$(mktemp)
kubectl --context <ctx> -n dms get secret dms-secrets -o jsonpath='{.data.DMS_ADMIN_TOKEN}' | base64 -d | sed 's/^/x-admin-token: /' > "$H"
stty -echo; printf 'password: '; read -r PW; stty echo; echo
printf '{"username":"%s","password":"%s"}' '<LOCAL_ADMIN>' "$PW" | curl --noproxy '*' -sS -H @"$H" -H 'content-type: application/json' --data-binary @- http://127.0.0.1:18080/api/admin/accounts
rm -f "$H"; unset PW
```

- 성공: HTTP 201, 본문 `{"username":"<LOCAL_ADMIN>","role":"admin"}`. 이미 있으면 409 `account_exists`, 토큰이 틀리면 403 `admin_token_required`.
- 이름은 values.env 의 `LOCAL_ADMIN` 과 **글자까지 같아야** 한다. 그래야 특권 목록(`root,admin,<LOCAL_ADMIN>`)에 들어 맵핑 면제(직접 경로·전체 PV·scan·배치)를
  얻는다. 다르면 일반 관리자다(§10-1). 확인: 포탈 로그인 뒤 관리 → 계정의 「전체 PV·직접 경로 권한」 칸, 또는 `/api/auth/me` 의 `can_run_as_root: true`.
- **사용자에게 열기 전에** 한다. 셀프 가입은 특권 이름을 막지 않는다 — 누가 먼저 그 이름으로 가입하면(메일이 stub 이면 누구나) 이 명령이 409 가 된다.
  그 계정은 user 역할이라 특권은 없지만 특권 목록 이름이라 보호된다 — 특권 세션 관리자만 지울 수 있다(§10-2 「보호」, 목록의 다른 이름으로 관리자를
  먼저 만들어 그 세션으로 지운 뒤 다시 만든다 — 이 경로는 **미검증**).
- 그 뒤 자동화가 로그인 세션이 필요하면 봉인 로그인을 쓴다(§10-7). 관리 토큰의 뜻·보관은 §3b-3.

### 6-2. 인증 메일(Knox 릴레이) — 사용자 게이트

**왜 게이트인가.** prod 렌더에는 `DMS_MAILER_BACKEND` 가 없어 코드 기본 `stub` 으로 돈다(`config.Settings.mailer_backend`). stub 은 인증번호를 메일로 보내지
않고 **응답에 돌려준다**(`routes_auth.request_verification_code` — `stub_code`). k8s 신원 모드에서는 DMS 계정 아이디 = RoleBinding 의 ID 라, 이 상태로 열면
누구나 남의 아이디로 가입·비밀번호 재설정을 해 **그 사람의 PV 권한을 얻는다**(§10-1). 이 단계를 마치기 전에는 DNS 를 등록·공지하지 않고 k8s 연동(6-5)을
등록하지 않는다. 「k8s 연동」 「개요」·「클러스터」 탭도 stub 이면 경고한다(「클러스터」 탭은 등록 버튼이 있는 화면이다).

**DNS 를 미루는 것만으로는 닫히지 않는다.** Avi VS-443 은 설치 전에 만든다(§0-5 1f) — 그래서 S8(Ingress)·S11(앱)이 끝나면 FQDN 과 VIP 를 아는
사람은 hosts·`curl --resolve <FQDN>:443:<VIP-A>` 로 포탈을 연다(Traefik 은 Host·SNI 로만 DMS 라우터를 고른다 — `https://<VIP-A>` 처럼 IP 로 직접
붙으면 기본 인증서 + 404 라 열리지 않지만, 이름만 맞추면 된다 — §2g-2·§3b-2). 노드 :443 에 직접 닿는 사람도 같다(Traefik 은 신뢰 출발지 밖의
연결도 PROXY 헤더 없이 받는다). 이 창에 stub 인증번호로 남의 아이디로 가입한 계정은 user 로 남고, 나중에 k8s 연동(6-5)이 등록되는 순간 그 아이디의
PV 권한을 얻는다(맵핑은 계정 이름 기준 — `repositories.k8s.K8sRepository.access_for`).
- 권장: 이 단계(메일 게이트)가 끝날 때까지 **Avi VS-443 을 운영자 대역으로만** 연다(VS 의 출발지 제한). 노드 :443 은 Traefik hostPort 라 노드의
  INPUT 방화벽으로는 막히지 않는다(§2g-4) — 사용자망에서 노드로 바로 닿는 길이 있으면 망 방화벽에서 같은 기간 막는다.
- 그러지 못했으면 6-5 등록 **전에** 관리 → 계정에서 운영자가 만들지 않은 user 계정을 확인해 지운다(6-8 확인표에 줄이 있다).

1. **릴레이 쪽 준비**(메일 담당 — 사내 Knox 릴레이 `knox_mail_dms_certi`, §11):
   - `RELAY_ALLOWED_CLIENTS` = 릴레이가 보는 dms-api 의 출발 주소. prod 는 dms-api 가 마스터에만 뜨므로(`deploy/overlays/prod/patch-placement.yaml`) **마스터 3대의
     노드 IP** 다. 메일은 api 만 보낸다(`api/mailer.py`). 파드 송신이 노드 IP 로 SNAT 되는지는 CNI 설정이다 — 아니면 파드 대역을 넣는다(사이트 확인).
   - `RELAY_ALLOWED_DOMAINS` 에 `<EMAIL_DOMAIN>`, 인증 키 `RELAY_TOKEN`.
2. **연결 확인**(마스터 한 대에서): `curl -s http://<릴레이 IP>:8025/healthz` → `{"ok": true}`.
3. **포탈**(세션 관리자 — 공유 토큰은 403 `mail_settings_session_required`): 관리 → 메일 설정 → 발송 방식 `knox_relay`, 릴레이 IP·포트(8025)·프로토콜(http),
   인증 키 → 저장 → 「연결 확인」(GET /healthz) → 「테스트 메일」(POST /send — 키·허용 IP·도메인까지 본다)을 자기 주소로.
   성공: 테스트 메일이 도착한다. 실패하면 화면에 사유와 조치가 나온다(`mailer.MailerError` — §11 표).
4. **발송 방식 기본값을 고정한다**: 사이트 `deploy/overlays/prod/patch-config.yaml` 에 `DMS_MAILER_BACKEND: "knox_relay"` 를 더하고
   `sh deploy/install.sh --context <ctx> --only S11`(§3-4 (b)). 포탈 칸을 비워도 stub 으로 돌아가지 않게 하는 것이다.
   확인: `kubectl --context <ctx> -n dms get cm dms-config -o jsonpath='{.data.DMS_MAILER_BACKEND}'` = `knox_relay`.
5. **끝 확인**: 시험 아이디로 가입 흐름을 끝까지 돈다 — 인증번호가 **메일로** 오고 화면에는 나오지 않는다. 이벤트 `verification_email_sent` 가 남는다(§11 「운영 신호」).

- `DMS_SESSION_SECRET` 을 바꾸면 포탈에 저장한 키가 열리지 않는다 — 다시 넣는다(§3b-4).
- 운영 신호·상한·구제는 §11.

### 6-3. 컨트롤 상태 — 빌드 노드

운영 → 컨트롤 상태(세션 관리자 — `routes_control.put_control_state` 는 `require_session_admin`). **PUT 은 전체 치환**이다 — 화면은 알아서 하지만 API 로 바꿀 때는
GET 한 값에서 바꿀 칸만 고쳐 전 필드를 보낸다(빠진 칸은 NULL).

| 칸 | 값 | 확인 |
|---|---|---|
| `build_node_name` | 인터넷 노드. **에이전트가 보고한 노드만** 받는다(아니면 422 `unknown_build_node`) — 곧 NoSchedule taint 없는 워커다. 빌드 파드는 toleration 없이 이 노드의 hostname 으로만 뜬다(`build_manifests.py`) | 스토리지 → 노드 화면에 그 노드 |
| `build_source_path` | 그 노드의 **로컬 체크아웃** 절대 경로(git worktree 금지 — SHA `unknown`, §8 1)). 설치한 커밋과 같게 둔다 | 빌드 프리플라이트(`build_source_unavailable`) |
| 빌드 프록시(`build_http_proxy`·`build_https_proxy`·`build_no_proxy`) | 사내 프록시. 인증 프록시(`user:pass@`)는 저장이 거절된다. 레지스트리·localhost·127.0.0.1 은 서버가 no_proxy 에 보탠다 | 프리플라이트(`build_node_no_egress`·`build_proxy_unreachable`) |
| `build_proxy_ca_path` | TLS 를 가로채는 사내 프록시면 빌드 노드 위 사내 CA PEM 의 절대 경로 | 프리플라이트(`build_proxy_ca_missing`·`build_proxy_tls_failed`) |
| `build_host_network` | 프록시가 그 노드에서만 닿는 주소일 때. loopback 프록시는 자동으로 켜진다 | §8 3b-1) |
| 유지보수·drain | 평소 꺼짐. 유지보수 = 신규 제출 503 `maintenance_mode`, drain = 잡 진행 정지(§4-5) | 대시보드 |

- 설치 뒤 첫 빌드는 업그레이드 때 해도 되지만, 열기 전에 한 번 제출해 프리플라이트(소스·egress·레지스트리 `DMS_BUILD_REGISTRY` 도달·디스크)가 통과하는지 보기를
  권한다(§8 3b)). 빌드는 레지스트리에 태그를 하나 만든다.

### 6-4. 스토리지·정책

전제: 데이터 스토리지가 워커에 **등록할 경로 그대로** 마운트돼 있고(§2), 각 `managed_root` 디렉터리가 있다. 에이전트가 1~2 주기(`DMS_AGENT_INTERVAL_SECONDS`
— prod 30초) 보고한 뒤에야 스토리지가 그 노드에서 Ready 로 보인다(`placement.eligible_nodes`).

**스토리지**(스토리지 → 스토리지 — 포탈을 먼저 쓴다)

| 칸 | 값 |
|---|---|
| 이름 | 사용자에게 보이는 이름 |
| `mount_path` | 노드의 마운트 경로 |
| `managed_root` | `mount_path` 자신 또는 그 아래(`repositories.storages` 검증) — DMS 가 다루는 루트 |
| `backend_type` | `cephfs`·`gpfs`·`wekafs`·`lustre`·`purestorage`·`netapp`·`onefs`·`nfs`(`repositories.storages._BACKENDS`). 경로 해석에는 쓰이지 않는 라벨이다 — 단 `gpfs`·`onefs`·`nfs` 는 PV 경로 프리셋 키다(`pv_paths.PRESETS`, 6-5) |
| 종류 | `filesystem`(직접 경로) · `pv`(k8s PV 전용 — StorageClass 매핑 대상) · `both`(`repositories.k8s.STORAGE_KINDS`) |
| 기본 소유 uid:gid | `pv`·`both` 에서만. PV 목적지 sync 결과가 이 소유로 맞춰진다(기본 0:0, 0..4294967294) — 6-5 |
| 사용 범위 | 전체 / 관리자 전용(`user_enabled=false`) / 비활성(`enabled=false`) |

- `<SHARED_DIR>/dms` 와 그 상위·하위는 등록할 수 없다(422 `storage_overlaps_dms_state` — §2d-1).
- 진행 중인 작업이 쓰는 스토리지는 삭제가 409 `storage_in_use` 다(대신 사용 범위를 「비활성」으로). `mount_path`·`managed_root`·`backend_type` 변경도
  409 `storage_in_use_path_change` 로 막힌다(미리보기와 실행 경로가 갈라지지 않게 — 작업이 끝난 뒤 다시, `routes_storages.update_storage`). 사용 범위·
  종류·기본 소유는 진행 중에도 바꿀 수 있다(비상 차단 = 비활성화. 기본 소유를 바꾸면 아직 실행되지 않은 그 PV 목적지 sync 는 `storage_owner_changed` — 6-5).
- API 로 할 때는 공유 토큰 Bearer 만 싣는다. **`x-dms-actor` 헤더는 넣지 않는다**(400 `invalid_actor` — §3b-3):

  ```sh
  umask 077; AUTH=$(mktemp)            # 토큰은 헤더 파일로만(argv·화면에 싣지 않는다 — 6-1 과 같은 방식)
  kubectl --context <ctx> -n dms get secret dms-secrets -o jsonpath='{.data.DMS_SHARED_TOKEN}' | base64 -d \
    | sed 's/^/Authorization: Bearer /' > "$AUTH"
  API=http://127.0.0.1:18080          # port-forward(6-1). 또는 https://<FQDN> + --cacert <사내CA>
  curl --noproxy '*' -sf -X POST "$API/api/admin/storages" -H @"$AUTH" -H 'content-type: application/json' -d '{
    "storage_name": "<이름>", "mount_path": "<마운트 경로>", "managed_root": "<마운트 경로>/<하위>",
    "backend_type": "nfs", "kind": "pv", "default_uid": 0, "default_gid": 0}'
  curl --noproxy '*' -sf "$API/api/admin/storages" -H @"$AUTH" | python3 -m json.tool
  # 끝나면: rm -f "$AUTH"
  ```

**정책**(관리 → 정책) — 도구 4개 `scan`·`dsync`·`nsync`·`rm`(`repositories.control.POLICY_TOOLS` — 「sync」가 아니라 dsync·nsync)

| 칸 | 뜻 |
|---|---|
| `max_nodes` | 잡 하나가 쓰는 노드 수 상한 |
| `procs_per_node` | 노드당 프로세스 = 워커가 **요청하는 CPU 코어 수**(1..1024). 노드 CPU 상한 = floor((allocatable − launcher 100m − `DMS_NODE_CPU_RESERVE_MILLICORES`(기본 500m)) / 1000) 보다 크면 그 상한으로 줄어들고, 한 코어도 못 담는 노드는 후보에서 빠진다(다 빠지면 `node_cpu_insufficient`). 의도대로 돌리려면 상한 이하로 둔다(예: 테스트베드 워커 allocatable 1800m → 상한 1) |
| `queue` | `dms-data`(DMS 가 만드는 Volcano Queue). 저장할 때 앞뒤 공백을 지우고, k8s 이름 규칙(DNS-1123 subdomain, 253자 이하) 밖이면 422 `invalid_policy` |
| `default_priority`·`max_priority` | `low`·`mid`·`high`. 우선순위를 생략한 sync 는 **dsync 정책의 `default_priority`** 를 쓴다(제출 때는 도구가 정해지지 않았다 — `domain.resolve_priority`). nsync 행의 `default_priority` 는 읽히지 않고 nsync 에는 `max_priority`(상한)만 걸린다(포탈은 그 칸을 잠그고 「dsync 따름」으로 보인다) |
| `execution_timeout_seconds`·`preview_timeout_seconds` | 실행·미리보기 시한(초, 1 이상 2147483647 이하 — policies 의 int4). scan 은 미리보기가 없어 scan 행의 `preview_timeout_seconds` 는 쓰이지 않는다(사전 점검·실행 전 재점검은 정책과 무관한 고정 10분 — `stepper.PREFLIGHT_DEADLINE_SECONDS`) |
| `enabled` | 끄면 그 도구의 잡이 계획되지 않는다 |

정책 4개는 **migrate 가 이미 시드한다**(`migrations` 의 정책 시드 — 행이 없을 때만 넣고 고친 값은 덮지 않는다): `scan`·`rm` max_nodes 4,
`dsync`·`nsync` 8, 넷 다 `procs_per_node` 8 · 큐 `dms-data` · `mid`/`high` · 미리보기 12시간(43200) · 실행 24시간(86400) · 켜짐. 보통 고칠 것은
**`procs_per_node` 하나**다(위 노드 CPU 상한 이하로). `execution_timeout_seconds` 를 줄이면 큰 sync 가 중간에 죽고 rm 은 부분 삭제로 남는다.

PUT 은 **전체 치환**이다(`repositories.control.upsert_policy` — 행을 지우고 다시 넣는다). 보내지 않은 칸은 기본값이 되고, 특히
`preview_timeout_seconds` 를 빼면 None(미리보기 시한 없음)이 된다(`routes_policies.PolicyBody`). 그래서 포탈 관리 → 정책에서 고치거나, API 로는
GET 으로 받은 행에서 바꿀 칸만 고쳐 그대로 PUT 한다:

```sh
TOOL=dsync                                          # scan · dsync · nsync · rm 중 하나씩
curl --noproxy '*' -sf "$API/api/admin/policies/$TOOL" -H @"$AUTH" > policy.json
python3 -c 'import json,sys; p=json.load(open(sys.argv[1])); p["procs_per_node"]=int(sys.argv[2]); json.dump(p,open(sys.argv[1],"w"))' policy.json <노드 CPU 상한 이하>
curl --noproxy '*' -sf -X PUT "$API/api/admin/policies/$TOOL" -H @"$AUTH" -H 'content-type: application/json' -d @policy.json
# 성공: 응답 JSON 의 procs_per_node 가 바뀌고 preview_timeout_seconds·execution_timeout_seconds 는 그대로다
```

> 예(테스트베드): 워커 2 vCPU 라 `procs_per_node` 1 — 값과 이유는 `deploy/testbed/README.md` §5-2.

- **사용자 sync 허용 스토리지 쌍**은 dms-k8s 에서 실효가 없다. 일반 사용자는 PV 주소만 낼 수 있고 양쪽이 PV 면 쌍 검사가 면제되며
  (`k8s_access.pair_gate_exempt`), 관리자 역할은 쌍 검사를 받지 않는다(`routes_requests.submit`). 비워 둔다.
- denylist 는 이름 규칙만 평가한다(그룹 규칙 추가는 422 `denylist_group_unsupported`). 판정은 **계획 시점**이다(`identity.resolve_job_identity` ←
  planner): 제출은 접수된 뒤 `identity_denied` 로 거부되고, 등재 전에 냈지만 아직 계획되지 않은 요청에도 걸린다. **이미 계획돼 확인 대기·실행 중인
  작업은 막지 않는다** — 멈추려면 그 요청 상세에서 취소한다. 대상은 앞뒤 공백을 지우고 소문자로 저장한다(빈 값은 422 `denylist_subject_empty`).
  이미 등재된 대상을 다른 사유로 다시 추가하면 사유만 바뀐다(감사 `denylist/update`).

### 6-5. k8s 연동 — 대상 클러스터·호출 노드·스토리지(PV)·StorageClass·계정

일반 사용자의 권한은 대상 클러스터의 RoleBinding 이 정한다(§10-1). 등록·변경은 **세션 관리자**만 된다 — 공유 토큰은 조회와 「지금 동기화」만(`routes_k8s` 모듈
docstring).

**전제**
- 6-2(메일)가 끝났다. 가입 인증 메일이 「DMS 계정 아이디 = 그 메일함 주인」을 보증하는 유일한 장치다.
- **PV 를 담은 스토리지가 DMS 워커에 마운트돼 있다**(PV 경로 그대로 보이게). NFS 기반(OneFS 포함)은 `lookupcache=positive` 로 마운트한다 — 기본 `all` 이면
  다중 노드 dsync 에서 다른 노드가 막 만든 디렉터리를 음성 캐시 때문에 못 봐 ENOENT 로 실패한다(테스트베드 실측 2026-10-26). remount 로는 안 바뀐다
  (umount → mount). CephFS·GPFS 는 해당 없다.
- PV 스토리지를 노드마다 따로 마운트해도 된다 — 두 스토리지를 함께 마운트한 노드가 없으면 PV 로 보내는 sync 는 nsync 로 돈다. 함께 마운트한 노드가 있으면 dsync 가 먼저다.
- **호출 노드의 에이전트가 대상 API 서버에 닿는다**. 대상 방화벽에는 「개요」 탭의 「주·보조 호출 노드 IP」만 연다. 확인(그 노드의 dms-agent 파드에서):

  ```sh
  POD=$(kubectl --context <ctx> -n dms get pods -l app.kubernetes.io/name=dms-agent --field-selector spec.nodeName=<호출 노드> -o name)
  kubectl --context <ctx> -n dms exec "$POD" -- getent hosts <대상 API 호스트>          # 이름이 풀린다(IP 로 등록하면 생략)
  ```

**절차**(포탈 스토리지 → k8s 연동 — 탭: 개요(합계·호출 노드와 주기) · 클러스터(등록·카드·지금 동기화) · StorageClass · PV 탐색 · 사용자 권한 · 동기화 이력)

1. **리더 계정**(대상 클러스터): 「클러스터」 탭 「클러스터 등록」 창의 「대상 클러스터에 만들 읽기 전용 계정」 YAML 을 대상에 적용한다 — ns `dms-reader`, SA `dms-inventory`,
   ClusterRole `dms-inventory-reader`(rolebindings·persistentvolumeclaims·persistentvolumes·storageclasses **list** + namespace `kube-system` get), 바인딩,
   장기 토큰 Secret `dms-inventory-token`(`frontend/src/features/k8s/readerManifest.ts`). 쓰기 권한은 없다.

   ```sh
   kubectl --context <대상 ctx> apply -f <창의 YAML 을 저장한 파일>
   kubectl --context <대상 ctx> auth can-i list rolebindings -A --as=system:serviceaccount:dms-reader:dms-inventory   # yes
   umask 077
   kubectl --context <대상 ctx> -n dms-reader get secret dms-inventory-token -o jsonpath='{.data.token}' | base64 -d > reader.token
   kubectl --context <대상 ctx> -n dms-reader get secret dms-inventory-token -o jsonpath='{.data.ca\.crt}' | base64 -d > reader-ca.crt
   ```

   토큰 파일은 0600, 화면·로그에 내지 않는다. 포탈이 봉인해 보내고 서버는 암호화해 저장한다. 장기 SA 토큰은 승인 항목이다(§0-4).
2. **PV 스토리지**(6-4 — 종류 `pv` 또는 `both`, `backend_type` 은 PV 백엔드에 맞게). **기본 소유 uid:gid** 는 그 PV 를 실제로 쓰는 파드의 uid/fsGroup 에
   맞춘다. 그 스토리지의 PV 를 목적지로 하는 sync 결과가 이 소유로 `--chown` 된다(일반 사용자는 늘 이 값, 특권 운영자만 「PV 루트 소유자」를 고른다 —
   `execution_manifests._auto_chown`). 바꾸면 이미 제출됐지만 아직 실행되지 않은 그 PV 목적지 sync 는 `storage_owner_changed` 로 멈춘다(다시 제출).
3. **호출 노드·주기**(「개요」 탭 「호출 노드와 주기」): 주·보조 = 대상 API 에 닿는 DMS 워커(에이전트가 보고한 노드). 주기 기본 300초(`DMS_K8S_SYNC_INTERVAL_SECONDS`, 하한 60) — 포탈 값이 이긴다.
4. **클러스터 등록**(「클러스터」 탭 → 「클러스터 등록」): 이름(소문자 DNS 라벨), API 서버 `https://<호스트>[:<포트>]`(경로 없음), TLS = 「지정한 CA」(`reader-ca.crt`) 권장 — 서버 이름(SNI)이 인증서와
   다르면 지정한다. 검증 끄기는 쓰지 않는다. 토큰 = `reader.token` → 그 클러스터 카드의 「지금 동기화」. 첫 run 은 폴링 주기(15초) 안에 시작한다.
   같은 클러스터를 다른 주소로 또 등록하면 「클러스터」 탭의 카드가 「같은 클러스터로 보입니다」로 경고한다(지문 = `kube-system` namespace uid).
   API 서버 주소를 바꾸면 같은 저장에서 토큰을 다시 넣어야 한다(422 `k8s_token_required`).
5. **StorageClass 매핑**(StorageClass 탭): 동기화로 SC 가 보인 **뒤에만** 된다(전에는 404 `storage_class_not_found`). SC 마다 스토리지를 고른다. 경로 템플릿을
   비우면 `backend_type` 프리셋이다 — `nfs` = `{subdir}`, `gpfs` = `{volume}/{volume}-data`, `onefs` = `data/csi/{volume}`(`pv_paths.PRESETS`). 화면의
   「예시 경로」가 워커의 실제 디렉터리인지 본다: 워커에서 `ls -d <예시 경로>`.
6. **계정**: 인식하는 바인딩은 둘이다(`k8s_rbac` 모듈 docstring).
   - 이름이 `<id>-clusterrole-admin` 또는 `<id>-clusterrole-edit` 이고 roleRef 가 ClusterRole `kubeflow-admin`·`kubeflow-edit` → DMS 아이디 = `<id>` 의 `-` 를 `.` 로
     (예: `hong-gildong-clusterrole-edit` → `hong.gildong`).
   - 이름이 `namespaceAdmin`, roleRef `kubeflow-admin`, User subject 하나의 이메일 도메인이 `<EMAIL_DOMAIN>` → 그 로컬파트.
   그 아이디로 사용자가 가입하거나 관리자가 만든다. 「사용자 권한」 탭의 「계정 없음」·「subject 불일치」로 빠진 사람을 찾는다.
7. **확인**: 「PV 탐색」의 PVC 수가 대상의 `kubectl --context <대상 ctx> get pvc -A --no-headers | wc -l` 와 같다. 「사용자 권한」에 인식된 바인딩이 보인다.
   시험 사용자로 로그인 → 작업 → 단일 작업에서 자기 namespace 의 PV 만 보인다(§7).

**장애 표시**
- 토큰 거부 → 「클러스터」 탭 카드 「실패 · API 서버가 토큰을 거부했습니다(401)」. 서버 이름 불일치 → 「API 서버 인증서를 검증하지 못했습니다」 + 원문. 둘 다 노드 탓이 아니라
  보조 노드로 넘기지 않는다.
- 주 노드 에이전트가 멈추면 요청 약 90초 뒤 보조 노드가 가져간다(테스트베드 실측 93초).
- 권한 회수(RoleBinding 삭제)는 다음 동기화에 반영된다. 컨펌 대기 중이던 작업은 컨펌 때 409 `k8s_access_denied` 로 끝난다.
- 클러스터를 지우면(「클러스터」 탭 카드의 「삭제」) 그 클러스터의 성공·실패는 「개요」 탭 호출 노드 카드의 「마지막 성공」·「마지막 실패」에서 바로 빠진다(「마지막 호출」에는 남는다). 동기화 이력은
  보존일(`DMS_K8S_SYNC_RUN_RETENTION_DAYS`, 기본 30)이 지나면 지운다 — 등록된 클러스터와 달리 최근 50건을 남기지 않는다(`repositories.k8s` `prune_runs`).
  같은 이름으로 다시 등록하면 그 이름의 옛 실패가 호출 노드 카드에 다시 보이다가, 그 노드가 새 등록에 성공하면 「해결됨」이 된다.
- 클러스터를 지워도 **StorageClass 매핑은 남는다**(매핑은 SC 이름 하나에 걸린다 — 지우는 곳은 스토리지 삭제와 매핑 해제뿐이다). 그 SC 이름이 남은
  어느 클러스터에도(SC·PVC 모두) 보이지 않으면 StorageClass 탭에 클러스터 칸이 「어느 클러스터에도 보이지 않음 — 매핑 해제 가능」인 행이 생기고
  「손볼 것」에 센다(대상에서 SC 를 지운 경우도 같다 — `routes_k8s.list_storage_classes`). 다시 등록하지 않을 클러스터라면 그 행의 「편집」에서
  「매핑 해제」를 골라 저장한다. 남겨 두면 그 스토리지를 `filesystem` 으로 바꿀 때 422 `storage_has_sc_mappings` 가 난다. 그 이름의 SC 가 다시
  보이면(같은 클러스터를 다시 등록하는 등) 남은 매핑이 그대로 다시 걸린다.
- `DMS_SESSION_SECRET` 을 바꾸면 저장한 토큰이 열리지 않는다 — 클러스터마다 다시 넣는다(§3b-4).

### 6-6. 공용 스토리지 밖 사본

S13 의 두 줄이다. 처음 한 번 만들고 주기를 정한다(§14-2).
- 덤프·`dms-secrets`·`dms-pg-app`(암호화) — §3b-5·§2f-5.
- 현장에서 빌드한 DMS 태그(설치 때 `build-and-push.sh` 로 만든 첫 태그 포함) — `skopeo sync --dest dir`(§2e-5 「백업」).

### 6-7. DNS·Avi 개통

순서대로 한다 — DNS 등록은 6-8 확인표를 다 채운 **뒤**다.

1. Avi 풀 멤버(Traefik 이 도는 노드 전부 :443 — S1 의 `Traefik 파드 Ready N/M (노드: …)` 줄) 헬스가 모두 up(Avi 화면). readyz 헬스 모니터면
   S11(dms-api) 전까지 down 이 정상이었다(TCP 443 모니터는 Traefik 만 보므로 설치 전부터 up 일 수 있다 — §2g-4).
2. 설치 호스트에서 VIP 경유 검증 — **DNS 가 필요 없다**(S12 는 `curl --resolve <FQDN>:443:<VIP-A>` 로 VIP 를 직접 본다 — `verify.sh` 「6. 포탈」):
   `CACERT=<사내CA> sh deploy/install.sh --context <ctx> --only S12` → `RESULT: PASS`(VIP readyz WARN 없음). 설치 호스트가 사내 CA 를 믿으면 `CACERT` 를 뺀다.
3. 6-8 확인표를 채운다.
4. 사내 DNS 에 `<FQDN>` → `<VIP-A>`. 메일 게이트 동안 Avi VS-443 을 운영자 대역으로 좁혀 두었으면(6-2) 사용자망으로 넓힌다.
5. 사용자에게 공지한다. 주소는 `https://<FQDN>` 하나다(80 VS 는 없고 HTTP → HTTPS 리디렉트도 없다 — §2g-4).

### 6-8. 사용자에게 열기 전 확인표

- [ ] `<LOCAL_ADMIN>` 로 포탈에 로그인되고 「전체 PV·직접 경로 권한」이 있다(6-1).
- [ ] 메일 발송 방식이 `knox_relay` 이고 시험 가입의 인증번호가 메일로만 왔다. `dms-config` 의 `DMS_MAILER_BACKEND` = `knox_relay`(6-2).
- [ ] 컨트롤 상태의 빌드 노드·소스 경로가 맞고, 빌드 프리플라이트가 통과했다(6-3).
- [ ] 스토리지가 노드 화면에서 Ready, 정책 4개가 켜져 있다(6-4).
- [ ] k8s 연동 동기화가 성공이고 PVC 수·바인딩이 대상과 맞다(6-5).
- [ ] §7 스모크를 모두 통과했다.
- [ ] 공용 스토리지 밖 사본이 있다(6-6).
- [ ] 계정 목록에 운영자가 만들지 않은 user 계정이 없다(메일 게이트 전 stub 창의 가입 — 6-2). 6-5 등록 전에 이미 봤어도 다시 본다.
- [ ] `CACERT=<사내CA> sh deploy/install.sh --context <ctx> --only S12` 가 PASS(6-7 2 — DNS 등록 전).
- [ ] (LB 가 PROXY v2 를 붙이는 사이트) 원 IP 가 살아 있다 — 보안 이벤트의 `client_ip` 가 운영자 PC 주소이고 SE 주소가 아니다(확인법 §2g-5).
- [ ] 관리 토큰 보관·회전 방침을 정했다(§3b-3), 인증서 만료일을 달력에 넣었다(§3b-2·§14-2).

## 7. 스모크 시험 — 설치 뒤 권한·경로 확인

사용자에게 열기 전에(§6-8) 한 번, 업그레이드 뒤에도 한 번 돈다. 목적은 「일반 사용자는 자기 PV 안에서만, 특권 운영자는 직접 경로까지」가 실제로 그렇게
도는지와 잡이 끝까지 가는지 보는 것이다. 어긋나면 §10-1 의 기대 사유와 대조한다.

### 7-1. 준비

- **시험 대상**: 대상 클러스터에 시험 namespace 하나, 그 안에 지워도 되는 PVC 둘(소스·목적지), 시험 사용자의 바인딩
  `<시험id>-clusterrole-edit`(roleRef ClusterRole `kubeflow-edit`)을 만든다. 실제 사용자 namespace 를 쓰면 데이터를 건드린다 — 쓰지 않는다.
  「k8s 연동」 「클러스터」 탭에서 그 클러스터 카드의 「지금 동기화」 → 「사용자 권한」 탭에 그 바인딩이 인식됐는지 본다(§6-5).
- **계정**: 시험 사용자(그 맵핑 ID — `<시험id>` 의 `-` 를 `.` 로), 특권 운영자(`<LOCAL_ADMIN>`).
- **도구**: 포탈(브라우저)로 한다. 잡 제출은 세션 로그인만 된다 — 공유 토큰 Bearer 제출은 403 `privileged_not_authorized` 다(`routes_requests.submit`).
  스크립트로 하려면 봉인 로그인(§10-7)으로 세션을 얻는다.
- 실패 원인은 요청 상세의 「실행 로그」에서 `DMS_EXEC_REASON=<코드>` 줄을 본다(§14-4).

### 7-2. 일반 사용자

| # | 할 일 | 기대 |
|---|---|---|
| 1 | 로그인 화면 → 가입. 아이디 = 맵핑 ID | 인증번호가 **메일로** 온다(화면에 나오면 stub — §6-2 로 돌아간다). 가입 완료 안내 메일 |
| 2 | 작업 → 단일 작업 | 연산은 sync·rm 만 보인다(scan 없음). PV 고르기에 시험 namespace 의 PVC 만 보인다 |
| 3 | sync: 소스 PVC(하위 경로) → 목적지 PVC(하위 경로) 제출 | 요청이 계획되고 미리보기가 돈다 → 잡 상태 `ConfirmPending` |
| 4 | 미리보기 내용을 보고 **본인이** 「작업 컨펌」 | `Executing` → `Succeeded` |
| 5 | 결과 소유 확인 — 목적지 스토리지를 마운트한 워커에서 `stat -c '%u:%g %n' <목적지 PV 경로>/<하위 경로>` | 목적지 스토리지의 「기본 소유 uid:gid」(§6-4) |
| 6 | rm: 목적지 PVC 의 하위 경로 → 컨펌 | 성공, 그 경로가 지워진다 |
| 7 | rm: 하위 경로를 비워 PV 전체 | 422 `pv_root_rm_forbidden` |
| 8 | 다른 namespace 의 PVC 를 API 로 지정(화면에는 안 보인다) | 403 `k8s_access_denied` |

### 7-3. 특권 운영자(`<LOCAL_ADMIN>`, 세션)

| # | 할 일 | 기대 |
|---|---|---|
| 1 | scan: 직접 경로(스토리지 + `managed_root` 아래 **실재하는** 상대 경로) | 성공, 리포트에 파일 수·바이트. 없는 경로면 사전 점검이 `target_not_readable` 로 거부한다 |
| 2 | sync: 직접 경로 → 직접 경로(같은 스토리지 또는 두 스토리지) | 두 스토리지를 함께 마운트한 노드가 있으면 dsync, 없으면 nsync(`placement.select_tool_and_candidates`). 요청 상세에서 도구를 본다 |
| 3 | 실행 중인 작업을 요청 상세에서 취소 | 취소됨. 그 잡의 vcjob·파드가 사라진다(7-4) |
| 4 | 결과 소유: 직접 경로 목적지가 `filesystem` 종류 스토리지면 | 소스 소유가 보존된다. `pv` 종류면 늘, `both` 면 PV 루트 아래일 때 그 스토리지의 기본 소유자(§10-1) |

### 7-4. 확인 명령

```sh
kubectl --context <ctx> -n dms get vcjob,pods -o wide          # 잡 파드 이름에는 job id 앞 12자가 들어 있다
kubectl --context <ctx> -n dms get vcjob,pods -l dms.io/job-id=<job_id>   # 워커·프리플라이트 파드(launcher 는 소유 vcjob 으로 찾는다)
VERIFY_NO_PROBES=1 CACERT=<사내CA> sh deploy/install.sh --context <ctx> --only S12   # RESULT: PASS(설치 호스트가 사내 CA 를 믿으면 CACERT 생략)
```

- 포탈 운영 → 아티팩트 경로: 3홉(API·컨트롤러·노드)이 모두 정상, 「DMS 상태 디렉터리」가 `<SHARED_DIR>/dms`, 겹치는 스토리지 경고 없음.
- 스토리지 → 노드: 모든 워커의 에이전트 보고가 신선하다.
- 요청 상세의 결과 화면·실행 로그가 열린다(결과 파일이 공용 디렉터리에 써졌다는 뜻).

끝나면 정리한다: 시험 작업은 관리자가 전체 작업에서 지운다(§12 — 세션 관리자). 시험 PVC·바인딩·namespace 를 대상 클러스터에서 지우고 「클러스터」 탭 카드의 「지금 동기화」.
시험 계정은 관리 → 계정에서 비활성화하거나 지운다.

### 7-5. 기대 거부 사유

권한 경로별 사유는 §10-1 표다. 제출 화면에서 자주 보는 것: 일반 사용자의 scan 403 `scan_unrestricted_only`, 직접 경로 403 `raw_path_not_allowed`,
맵핑 없는 namespace 403 `k8s_access_denied`, 한 번도 동기화되지 않은 클러스터 409 `k8s_mapping_unknown`, 노드 CPU 상한보다 작은 노드뿐 `node_cpu_insufficient`(§6-4).

## 8. 포탈에서 이미지 빌드 (슬라이스 11)

설치 뒤 DMS 이미지 3종(`dms-mpifileutils`·`dms`·`dms-agent`)은 포탈 「빌드」에서 만든다 — 빌드 노드 위 bare Pod 가 빌드해 `DMS_BUILD_REGISTRY`
(= values.env 의 레지스트리, 렌더가 넣는다)로 push 한다. 만든 태그를 클러스터에 올리는 것은 §9 포탈 릴리스다. `20-config.yaml` 의 `DMS_BUILD_*` 키가
이 기능의 설정이고, 빌드 노드 자체는 ConfigMap 이 아니라 DB(컨트롤 상태)에 있다(아래 0)). 포탈이 없을 때의 부트스트랩·비상 빌드는 §1.

### 이미지 빌드 전 게이트 — 로컬 풀스택 e2e(개발 PC, 수기)

이미지를 만들기 **전에**(포탈 빌드든 §1 의 부트스트랩·비상 빌드든) 빌드할 커밋으로 개발 PC 에서 아래를 돌리고 초록을 확인한다. 빨강이면 빌드를 제출하지
않는다.

```bash
cd frontend && npm run test:e2e
# = npm run build && tsc -p tsconfig.e2e.json && playwright test  (빌드까지 한 방)
```

- 시나리오 32건(17개 파일, 2026-11-06 기준 — `npx playwright test --list` 로 센다), 빌드를 포함해 몇 분이다. 하네스가 백엔드를 띄우고 끝나면 스스로
  죽인다(tmp DB 포함 정리).
- **이 게이트는 수기다 — CI 는 없다.** 이 저장소에는 이 스위트를 자동으로 돌려주는 것이 없다. 사람이 빌드 전에 돌리는 것이 유일한 강제력이다.
- **무엇을 잡는가**(프론트 단위 테스트 `npx vitest run` 이 구조적으로 못 보는 넷): 기하(넓은 표가 레이아웃을 밀어내는 회귀 — jsdom 에는 레이아웃이 없다),
  실 HTTP 왕복(세션 쿠키 로그인, `dist` 서빙 + SPA fallback — vite dev 서버가 가리는 경로), 폴링(목록 수렴·상세의 종단 뒤 중지), 풀스택 부팅
  (`migrate` → `api` → `controller` → `agent` 리포트 → 스토리지 `Ready` — 못 세우면 skip 이 아니라 실패).
- 클러스터도 PostgreSQL 도 쓰지 않는다(tmp sqlite + `execution_backend=stub`). 그래서 **§7 의 실 클러스터 스모크를 대체하지 않는다** — 그 앞단의 싼 그물이다.
- **전제**: 개발 PC 에 Google Chrome(러너는 시스템 크롬 — `frontend/playwright.config.ts` 의 `channel:"chrome"`; 없으면 `npx playwright install chromium`
  뒤 `DMS_E2E_BROWSER_CHANNEL= npm run test:e2e`), `frontend/node_modules`(`npm ci`)와 저장소 루트 `.venv` 의 `dms` 편집 설치(하네스가
  `<repo>/.venv/bin/dms` 로 백엔드를 띄운다), **포트 8093 이 비어 있을 것**(누가 듣고 있으면 즉시 실패한다 — 낡은 서버에 붙어 초록이 나는 거짓을
  막는다. 포트를 바꾸지 말고 이전 실행의 잔재를 정리한다).

### 빌드 절차

**0) 빌드 노드와 소스 경로를 먼저 지정한다.** 포탈 「컨트롤 상태」 화면(`PUT
/api/admin/control-state`, `build_node_name`·`build_source_path`)에서 지정하며,
`control_state` 테이블에 저장된다 — ConfigMap이 아니다, 운영자가 포탈에서 언제든
바꾸는 값이라 재적용마다 되돌아가면 안 되기 때문이다. 지정 전에 「빌드」 화면에서
제출하면 API가 `422 build_node_not_set`/`build_source_not_set`으로 거절한다.
**빌드 노드의 조건**: (1) **NoSchedule taint 없는 워커** — 빌드·프리플라이트 파드에 toleration 이 없고 nodeSelector `kubernetes.io/hostname` 만 쓴다
(`build_manifests.py`). 에이전트가 보고한 노드만 저장된다(422 `unknown_build_node`). (2) **privileged 파드 허용** — 빌드 파드는 privileged(buildah)다.
(3) 빌드 소스는 그 노드의 **로컬 체크아웃**(git worktree 금지 — 아래 1)). (4) 인터넷 egress(직접 또는 프록시 — 아래 3)·3b-1)). 설치 때의 인터넷 노드를
그대로 쓰는 것이 보통이다(§0-2).

**1) 빌드는 빌드 노드의 로컬 소스에서 뜬다(슬라이스 33).** 빌드 파드가
`build_source_path`(`<빌드 노드의 DMS 체크아웃 절대 경로>`)를 hostPath 로 **읽기 전용**
마운트하고, 시작 시점에 tar 스냅샷을 떠 `/src` 에서 빌드한다
(`src/dms/build_manifests.py`). git 연동이 없다 — **커밋·push 하지 않은 작업
트리도 그대로 빌드된다.** 커밋 SHA 는 마운트의 `.git` 에서 읽어 기록하고, 작업
트리에 미커밋 변경이 있으면 `-dirty` 접미를 붙인다(워크트리 경로처럼 SHA 를 읽을
수 없으면 `unknown`). 빌드 노드의 로컬 체크아웃을 지정하고, 설치·업그레이드한 커밋과 같게 둔다(§4-2 1).

**git worktree 로 만들지 않는다** — 빌드 파드는 이 경로 하나만 마운트해서 `.git` 파일이 가리키는 바깥 gitdir 를 못 따라가 SHA 가 `unknown` 이 된다.
빌드 코드에는 브랜치 개념이 없다 — 컨트롤 상태는 DB 하나 = 클러스터 하나라, 클러스터마다 자기 체크아웃을 가리킨다. 컨트롤 상태 PUT 은 전체 치환이라
(빠진 필드는 NULL) GET 한 값에서 바꿀 필드만 고쳐 전 필드를 보낸다. (예: 테스트베드의 빌드 노드·소스 경로는 `deploy/testbed/README.md` §2.)

**2) 태그는 지정하거나 파생된다(드리프트 방지 내장, 슬라이스 34).** 「빌드」 폼의
(선택) 태그 입력에 관례 태그(예: `d75`)를 지정하면 그 태그로 push 된다. **빌드는
빌드하는 이미지의 동봉 매니페스트(`deploy/k8s`) 태그를 이 빌드 태그로 자동
스탬프**하므로(`build_manifests._SCRIPT`), 그 태그로 배포하면 **live == 동봉
매니페스트가 되어 드리프트 배지가 안 뜬다** — 예전처럼 손으로 `deploy/k8s` 를 먼저
bump 해 빌드하지 않아도 된다. 그 태그를 실제로 굴리는 것은 포탈 릴리스(§9)다 — values.env 태그가 `live` 면 손댈 것이 없고, 명시 태그면 릴리스 뒤 그 태그로 바꾼다(§4-2 7).
(테스트베드는 오버레이 `newTag` 를 커밋한다 — `deploy/testbed/README.md` §6.) 태그를 비우면 `b<build_id 앞 8자>`
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
기반 자동 GC 는 두지 않는다(클러스터 내 레지스트리의 GC 는 §2e-5). 레지스트리 삭제가 `405` 면 — external 레지스트리라면 그 레지스트리의
`storage.delete.enabled`(env `REGISTRY_STORAGE_DELETE_ENABLED=true`)가 꺼진 것이다(클러스터 내 레지스트리는 켜져 있다 — §2e-1). 빌드 이력 행은 「빌드 이력」 화면에서 다중 선택 삭제(종단 빌드만) — 그 빌드의 빌드·프로브 파드를 먼저 지우고, apiserver 에 닿지 않아 못 지우면
502 `build_pod_cleanup_failed`(잠시 뒤 다시). pod-gc(기본 600초)가 이미 치운 빌드는 클러스터에 묻지 않고 지운다.

**3) 빌드 노드는 인터넷 egress가 필요하다.** 빌드가 hermetic하지 않다 — Buildah
빌드(privileged 컨테이너) 안에서 npm install(포탈 프론트엔드), `dl.k8s.io`(kubectl 등
설치), PyPI(파이썬 의존성), Debian bookworm 미러(apt 패키지), docker.io(베이스
이미지)에 접근한다. **빌드할 때만 운영자가 그 워커에 인터넷을 열면 된다** — 상시
개방이 아니어도 된다.

**필요한 egress 는 전부 빌드 파드 경로다.** 빌더 이미지 `<REGISTRY>/buildah:stable` 은 `deploy/images.lock` 의 줄이라 설치기 S5 가 레지스트리에
push 해 두므로(§2e-4) 노드(kubelet/CRI-O)는 인터넷 없이 빌더를 받는다. 프리플라이트 프로브는 **파드 네트워크**로 검사하는데 이미지 pull 은
**노드 네트워크**로 일어나므로, 빌더가 레지스트리에 없으면 프로브는 통과하고 빌드 파드가 `ImagePullBackOff` 로 앉는다 — S5 대조(§2e-4 4))로 확인한다.

**3b) 착수 전에 적합성 프리플라이트가 돈다(슬라이스 21, 슬라이스 33에서 소스 검사
추가).** 제출하면 빌드 노드 위에 단발 프로브 파드(`dms-build-pf-<build_id[:12]>`,
job image 라 인터넷 없이도 뜬다)가 네 가지를 검사하고 실패하면 **수 초~수십 초
안에** 고유 사유 코드로 끝낸다 — 2시간 generic 타임아웃을 기다리지 않는다:
- 소스 경로에 `deploy/docker/Dockerfile.dms` 존재 → `build_source_unavailable`
  (경로 오타·마운트 소실을 빌드 전에 잡는다)
- egress(`quay.io`·`registry-1.docker.io` TCP 443) → `build_node_no_egress`
  (로그에 실패 호스트 전부)
- `DMS_BUILD_REGISTRY` 도달 → `build_registry_unreachable`
- 노드 fs 여유(`avail ≥ 0.15·total + 12GiB`) → `build_node_disk_low`(실측 바이트 기록)

검사 대상 egress 는 `quay.io`·`registry-1.docker.io` 둘이다(소스는 로컬이라 git 호스트는 보지 않는다).

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
프록시 쪽 IP allowlist 로 푼다). 근거·실증: `docs/history/changelog-2026-09-to-10-21.md` 「빌드 프록시 + 신규 사이트 태그 누출 차단」.

**프록시가 빌드 노드의 localhost 에만 있는 경우**(예: `ssh -R` 리버스 터널 — 호스트 loopback 에만 바인드): 파드 안의 `127.0.0.1` 은 파드 자신이라
닿지 못한다. 그래서 프록시 호스트가 `localhost`/`127.0.0.1`/`::1` 이면 서버가 **호스트 네트워크 모드**를 자동으로 켠다(빌드·프리플라이트 파드
`hostNetwork: true` + `dnsPolicy: ClusterFirstWithHostNet` + `buildah bud --network=host` — buildah 의 `RUN` 단계도 호스트 netns 에 있어야 하므로 셋이 한
스위치다). loopback 이 아니지만 그 노드에서만 닿는 주소면 컨트롤 상태의 「빌드 파드 호스트 네트워크」 스위치로 켠다. 빌드 파드는 원래 privileged 라
노출 표면은 프로브 파드(비특권)가 호스트 loopback 서비스에 닿게 되는 점만 는다. 대안은 터널을 노드 IP 에 바인드하는 것이다(다른 노드에도 열린다).

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
- 인증 프록시(Basic)는 여전히 미지원. 근거·실증: `docs/history/changelog-2026-09-to-10-21.md` 「사내 프록시 CA(TLS 가로채기 프록시)」.

**no_proxy 힌트**: 컨트롤 상태의 프록시 제외 입력 아래에 이 사이트의 「권장 값」(레지스트리 host / host:port, localhost, 127.0.0.1, .svc,
.cluster.local, 워커 노드 InternalIP)이 뜨고 버튼 하나로 채워진다. 노드 IP 는 API 가 k8s Node 를 읽어 오므로 ClusterRole `dms-api-nodes-readonly`
(`10-rbac.yaml`)가 있어야 한다(없으면 노드 IP 만 빼고 그 사실을 표시). 중복은 무해하다.

**3b-2) 신규 사이트의 매니페스트 기준값(2026-09-08).** 이미지에 동봉된
`deploy/k8s` 는 "그 이미지를 만든 소스 트리"의 값이라, 포탈 밖에서 부트스트랩한
이미지(§1)는 base 자리표시자(`set-by-overlay.invalid`)를 담고 있다. 동봉 이미지의
레지스트리가 사이트의 `DMS_BUILD_REGISTRY` 와 다르면 서버가 "모름"으로 접어 드리프트
배지·레지스트리 "사용 중"·빌드 화면 어디에도 남의 태그가 새지 않고, 포탈 빌드의
스탬프는 레지스트리까지 치환하므로 첫 포탈 빌드부터 동봉값이 그 사이트 것이 된다.
빌드 화면의 제안 태그는 라이브·레지스트리·빌드 이력의 dNN 중 최대+1, 하나도 없으면
**d1** 이다.

**3c) 빌드는 데이터 잡과 같은 워커에서 동시에 돈다.** 빌드 노드를 잡 풀에서 빼지 않는다. 빌드 파드는 봉투(cpu 250m/1000m, mem 128Mi/1Gi,
eph 10Gi/12Gi)와 PriorityClass `dms-build`(10 < `dms-low` 50)를 달고 돈다 — cpu limit(1000m)이 실질 보호막이고, 노드가 압박받으면 **빌드가 먼저 죽는다**.
잡 워커는 노드당 프로세스 수만큼 CPU 를 **요청**하므로(launcher 100m — §6-4) CPU 예약이 겹친다: 워커가 코어를 예약 중이면 빌드 파드(250m)가 그 노드에서
스케줄을 기다리고, 빌드가 돌면 그 노드의 워커가 Volcano 대기에 걸릴 수 있다(둘 다 기다릴 뿐 실패하지 않는다). 노드 CPU 상한은 allocatable 에서
launcher 100m 와 예비(`DMS_NODE_CPU_RESERVE_MILLICORES`, 기본 500m — DaemonSet·제어면·빌드 몫)를 빼고 코어 단위로 내린 값이다(예: 테스트베드 워커
allocatable 1800m 이면 워커 1000m + launcher 100m 를 예약해도 700m 가 남는다). 노드의 DMS 외 requests 와 빌드 250m 가 예비보다 크면 예비를 올린다.

**4) (폐기)** 만든 태그를 매니페스트에 손으로 넣던 절차는 없어졌다 — 배포는 §9 포탈 릴리스로 한다.

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
둘뿐이고, 레지스트리는 빌드와 같은 `DMS_BUILD_REGISTRY`를 쓴다. 릴리스는 **세션 관리자**만 한다(공유 토큰 403 `admin_session_required`), 레지스트리가
정상일 때만 한다(§2e-5). 새 커밋으로 올리는 전체 순서는 §4-2, 되돌리기는 §5-1.

### 9-1. 흐름: 한 배치로 제출하고, 순서는 서버가 강제한다

「릴리스」 화면은 세 워크로드
(`dms-agent`/`dms-api`/`dms-controller`)와 잡 이미지(`job-image` — 5)) 행마다 select를 주고, 올리고 싶은 것만 태그를
골라 **한 번에** 제출한다(`POST /api/admin/releases`). 제출 순서가 무엇이든 서버가
`ROLLOUT_ORDER = ("dms-agent", "dms-api", "dms-controller")`로 정렬해
`releases.seq`에 **DB로 지속**시키고(`src/dms/repositories/releases.py`), 컨트롤러의
RolloutWatcher가 그 seq 순서대로 하나씩 patch → 수렴 확인 → 다음으로 넘어간다. 순서가
행에 박혀 있으므로 배치 중간에 컨트롤러가 죽어도 새 파드가 seq만 보고 이어간다. 동시
롤아웃은 하나로 제한된다 — 진행 중인 배치가 있으면 `409 rollout_in_progress`.

### 9-2. `dms-controller`를 갱신하면 컨트롤러가 자기 자신을 재시작시킨다

그래서
`dms-controller`가 배치의 **마지막**이다. 컨트롤러가 자기 Deployment를 patch하는 순간
옛 파드는 종료되고, 「릴리스」 화면의 갱신이 리스 재획득(최대 ~30초 + 파드 기동)만큼
멈춘다 — **장애가 아니다.** 컨트롤러는 patch 전에 행을 `Applying`으로 먼저 기록하므로
(record-then-patch), 새 파드가 그 `Applying` 행을 이어받아 수렴을 확인하고 `Applied`로
닫는다. 화면이 잠시 얼어 있어도 기다릴 것 — 이것이 이 기능의 정상 동작이다.
간격을 늘리면(`DMS_ROLLOUT_INTERVAL_SECONDS`) per-loop 리스가 함께 길어져 이 정지
구간이 몇 배로 늘어난다.

### 9-3. 같은 태그 재롤아웃은 거절된다(`same_tag`)

현재 워크로드에 걸린 것과 같은 태그를
고르면 `422 same_tag`로 막는다 — 모든 매니페스트가 `imagePullPolicy: IfNotPresent`라
같은 태그를 다시 밀어봐야 파드 스펙이 그대로여서 아무 일도 일어나지 않기 때문이다(§8 2) 와
같은 함정). 레지스트리에 없는 태그는 `422 unknown_tag`, 모르는 컴포넌트는
`422 unknown_component`.

### 9-4. 롤아웃 뒤 설치기가 되돌리지 않게 — values 태그

릴리스는 살아 있는 클러스터 오브젝트만 바꾼다. 다음 설치기 실행(S11 의 `apply -k`)이 옛 태그를
다시 적용하려 하면 `deploy/overlays/guard-images.sh`(install.sh 가 apply 직전에 부른다 — §3-3)가 "렌더 이미지 != 라이브" 로 exit 3 거부한다 —
조용히 되돌아가지는 않는다(의도한 이미지 변경만 `ALLOW_IMAGE_CHANGE=1`). 그래서 prod 는 values.env 의 `DMS_TAG`/`DMS_AGENT_TAG`/`MFU_TAG` 를:

- **`live`**(설치 뒤 권장, 2026-10-06): `render.sh` 가 지금 클러스터에서 도는 태그를 읽어 채운다(DMS_TAG←deploy/dms-api, DMS_AGENT_TAG←ds/dms-agent,
  MFU_TAG←ConfigMap dms-config 의 DMS_JOB_IMAGE — `render.sh _live_tag`). 포탈 릴리스는 values.env 를 읽지 않으므로 평소엔 이 파일을 고칠 일이 없고,
  재적용(설정 변경 등) 때도 렌더 이미지 = 라이브라 가드를 통과한다. 첫 설치는 라이브 워크로드가 없어 명시 태그를 써야 한다(render.sh 가 거부한다).
  MFU `live` 는 ConfigMap(apply 가 쓰는 부트스트랩 기본값)을 읽는다 — 런타임 잡 이미지는 DB 값(5))이라, MFU `live` 는 "ConfigMap 을 재적용으로
  되돌리지 않는다"는 뜻이지 런타임 잡 이미지와 늘 같다는 뜻은 아니다.
- **명시 태그**: 릴리스한 태그로 바꾼다(§4-2 7).

(테스트베드처럼 값을 커밋하는 고정 오버레이는 `newTag`·`DMS_JOB_IMAGE` 를 커밋한다 — `deploy/testbed/README.md` §6.) 포탈은 저장소 파일을 고치지
않는다(컨트롤러 파드 안에 저장소가 없다). 어긋남은 릴리스 화면의 드리프트 배지가 보인다.

### 9-5. 태그 계보 셋은 서로 독립이고, 잡 이미지는 릴리스의 `job-image` 행이다

`dms:`(api/controller/migrate 가 공유), `dms-agent:`,
`dms-mpifileutils:` 는 각각 다른 레지스트리 리포이고 버전이 같이 갈 이유가 없다 — 화면은 컴포넌트별로 자기 리포의 태그 목록만 보인다
(`COMPONENTS[*].repository`). 잡 이미지(`dms-mpifileutils` — 다음 잡 파드가 쓸 이미지)는 워크로드가 아니라 DB 오버라이드다
(`routes_releases.JOB_IMAGE_COMPONENT`, DB `control_state.job_image`): 넷째 행으로 함께 보이고 적용은 즉시(재시작 없음, 다음 잡부터)다. DB 값이
ConfigMap 의 `DMS_JOB_IMAGE` 를 이긴다(`job_image.resolve_job_image`). 레지스트리 주소를 바꿨거나 DB 를 옮겨 왔으면 이 행을 다시 릴리스한다(§4-4).

### 9-6. RBAC은 세 워크로드로 좁혀져 있다

컨트롤러 Role의 apps `get`/`patch`는
`resourceNames: ["dms-api", "dms-controller", "dms-agent"]`로 한정된다
(`10-rbac.yaml`) — 컨트롤러가 네임스페이스의 임의 워크로드를 건드릴 수 없다.
api Role은 같은 세 이름에 **읽기 전용 `get`만** 받는다(「릴리스」 화면이 현재
이미지를 보여주기 위한 것) — patch는 컨트롤러에만 있다. `list`와 `*/status`
규칙은 **두지 않는다**: 코드는 `read_namespaced_deployment`/`_daemon_set`으로
메인 리소스만 읽고(상태는 그 안에 담겨 온다) 어디서도 apps를 list하지 않는다.
특히 `list`는 `resourceNames`를 따르지 않아, 두면 Role이 네임스페이스의 모든
워크로드로 조용히 넓어져 위의 "세 워크로드로 좁혀져 있다"가 사실이 아니게 된다.
RBAC 이 바뀐 커밋이면 새 태그를 릴리스하기 **전에** `sh deploy/install.sh --context <ctx> --only S11` 로 `10-rbac.yaml` 을 먼저 들인다(§4-2 4). 확인:

```bash
kubectl --context <ctx> auth can-i patch deployments.apps/dms-controller \
  --as=system:serviceaccount:dms:dms-controller -n dms      # yes
kubectl --context <ctx> auth can-i patch deployments.apps \
  --as=system:serviceaccount:dms:dms-api -n dms             # no
kubectl --context <ctx> auth can-i list deployments.apps \
  --as=system:serviceaccount:dms:dms-controller -n dms      # no (의도적)
```

### 9-7. 없는 태그를 강제로 넣으면 시끄럽게 실패한다

레지스트리가 다운이면 태그 검증이
fail-open이라(설계 §7) 존재하지 않는 태그가 통과할 수 있다. 그 경우 파드가
`ImagePullBackOff`에 빠지고, Deployment는 `ProgressDeadlineExceeded`로, DaemonSet은
`DMS_ROLLOUT_TIMEOUT_SECONDS` 벽시계로 `Failed`가 된다(DaemonSet에는
`progressDeadlineSeconds`가 없어 이 값이 유일한 실패 수단이다). 실패하면 배치의 남은
컴포넌트는 `rollout_aborted`로 닫히고 반쯤 섞인 버전 조합이 생기지 않는다 — 복구는
이력에서 옛 태그를 골라 다시 롤아웃하면 된다(별도 롤백 버튼이 없는 이유). `kubectl rollout undo` 는 쓰지 않는다(§5-1).

### 9-8. 보안 설정 드리프트 배지(2026-10-26)

「릴리스」 화면의 dms-api·dms-controller 「현재 이미지」 칸은 라이브 컨테이너의
실효 securityContext(runAsUser·runAsGroup·runAsNonRoot·allowPrivilegeEscalation·readOnlyRootFilesystem·privileged·
capabilities add/drop — 컨테이너 값이 없으면 파드 수준)를 이미지에 동봉된 `40-api.yaml`·`41-controller.yaml` 과 비교해, 다르면
「보안 설정 드리프트」와 필드별 「라이브 X · 매니페스트 Y」를 보인다(응답 `GET /api/admin/releases/targets` 의
`security_drift` — null 모름 / [] 같음 / 목록). 포탈 롤아웃은 이미지만 바꾸므로 배지는 롤아웃으로 사라지지 않는다 —
`sh deploy/install.sh --context <ctx> --only S11` 로 매니페스트를 적용해야 맞춰진다(§2b·§4-2). 동봉 매니페스트가 없거나 그 블록을
못 읽으면 표시하지 않는다(거짓 경보보다 모름). dms-agent·잡 이미지는 비교하지 않는다.

화면: 「릴리스」(`/admin/releases`) — 워크로드 3행 + 잡 이미지 행 + 태그 select, 한 배치 제출, 진행
중에는 폴링, 아래에 롤아웃 이력.

## 10. 계정·권한·인증

dms-k8s 는 **k8s 신원 모드 전용**이다. 동작의 근거는 `docs/ARCHITECTURE.md` §3 불변식 12·§7 과 `k8s_access`·`identity` 모듈 docstring 이다.
10-7(웹 인증 하드닝)은 로그인 보호 장치의 값과 봉인 로그인이다.

### 10-1. 권한 모델(k8s 신원 모드)

- **모든 단건 잡이 root(uid 0/gid 0)다.** 서버가 요청에 `run_as_root: true` 를 싣고(`routes_requests.submit`), root 는 **세션으로 로그인한** 요청에만
  허용된다(`identity.root_allowed` — 제출·계획·컨펌 세 지점이 같은 술어). 그래서 공유 토큰으로는 잡을 낼 수 없다.
- **무엇을 만질 수 있나**는 대상 클러스터의 RoleBinding 맵핑이 정한다(`k8s_access`, `repositories.k8s.access_for`). 인식된 바인딩이 있는 (클러스터, namespace) 의
  모든 PVC 를 읽고 쓴다(§6-5 6).
- **맵핑 면제**(특권 운영자) = admin 역할 + 특권 목록(10-2) + 세션(`api.auth.can_run_as_root`). 면제자만 직접 경로·전체 PV·scan·배치를 쓴다.
- 사용자 연산: prod `DMS_USER_ALLOWED_OPERATIONS="sync,rm"`(`deploy/overlays/prod/patch-config.yaml` — 코드 기본은 `sync` 하나). scan 은 면제자만이다
  (`k8s_access.scan_allowed`).

| 누구 | 할 수 있는 것 |
|---|---|
| 일반 사용자(user 역할, 세션) | 맵핑된 namespace 의 PV 주소로 sync·rm. 컨펌은 본인 |
| 목록 밖 관리자(admin 역할, 특권 목록 밖) | 관리 화면(스토리지·정책·계정 일부 등). 잡은 일반 사용자와 같다 — PV 주소만, scan 없음 |
| 특권 운영자(admin + 목록·부여 + 세션) | 맵핑 면제 — 직접 경로·전체 PV·PV ↔ 직접 경로 혼합·scan·배치·사용자 지정 |
| 공유 토큰(Bearer) | 관리 조회와 일부 설정 — 잡 제출·계정·배포 경로는 안 된다(10-4) |

| 경로 | 결과 |
|---|---|
| 일반 사용자 + PV 주소(`pv`·`source_pv`·`destination_pv`)로 **sync·rm** — 맵핑이 있는 namespace | **동작**(root 실행, sync 허용 쌍 면제, 컨펌은 본인) |
| 면제자가 아닌 사람의 scan | 403 `scan_unrestricted_only` |
| 면제자가 아닌 사람의 직접 경로(스토리지 + 경로) | 403 `raw_path_not_allowed` |
| 맵핑이 없는 namespace / 한 번도 동기화되지 않은 클러스터 | 403 `k8s_access_denied` / 409 `k8s_mapping_unknown` |
| PV 루트 rm(하위 경로 `''`·`'.'`·`'./'`) | 422 `pv_root_rm_forbidden` |
| PV 목적지 sync 의 결과 소유 | 목적지 스토리지의 기본 소유자(스토리지 화면 「기본 소유 uid:gid」, 기본 0:0)로 `--chown`(`execution_manifests._auto_chown`). 일반 사용자는 늘 이것이다. 특권 운영자만 「결과 소유: PV 루트 소유자」(`pv_owner: "pv_root"` — 비면제 403 `pv_owner_root_not_allowed`)나 chown 지정. 제출 뒤 기본값이 바뀌면 계획 Rejected / 컨펌 409 `storage_owner_changed`(다시 제출), 저장값이 무효면 409 `invalid_storage_owner` |
| 직접 경로 목적지의 결과 소유(배치 포함) | 목적지 스토리지가 `pv` 종류면 늘, `both` 면 지금 인벤토리의 PV 루트 아래일 때 그 스토리지의 기본 소유자로(`k8s_access.direct_dest_owner`). 그 밖(`filesystem`·PV 밖)은 소스 소유 보존 |
| 대기·컨펌 대기 중 권한 회수·PVC 재생성 | 계획 Rejected / 컨펌 409(`k8s_access_denied`·`pv_moved`·`pv_not_found`) |
| `run_as_root: false` 명시 | 422 `run_as_root_required` |
| 공유 토큰(Bearer) 제출 | 403 `privileged_not_authorized` — 자동화는 봉인 세션 로그인(10-7) |
| 그룹 denylist 규칙 추가 | 422 `denylist_group_unsupported`(이름 규칙만 평가) |
| `run_as_root` 없는 옛 요청 행·목록 밖 생성자의 배치 자식 | 계획 Rejected `privileged_not_authorized`(비 root 로 낮추지 않는다) |
| 사용자 스캔 경로 등록·통계 | 면제자 전용(비면제 422 `scan_path_unrestricted_only`) |
| 로그인·가입·재설정·`/api/auth/me`·사용량 분석·아티팩트 | 동작 |

- 포탈은 `/api/auth/me` 의 `user_operations`·`unrestricted` 로 제출 화면을 가른다. 표시일 뿐이고 서버가 제출·계획·컨펌에서 다시 본다.
- 권한의 원천이 「DMS 계정 아이디 = RoleBinding 의 ID」라, 가입 인증 메일이 그 결속을 보증한다 — 메일이 stub 이면 이 표 전체가 무의미하다(§6-2).

### 10-2. 특권 목록·맵핑 면제 자격

- **자격 이름 집합 = env `DMS_PRIVILEGED_REQUESTERS` ∪ 계정 화면에서 부여한 계정**(`privileged.privileged_names`). 코드 기본은 `root,admin`, prod 렌더는
  `root,admin,<LOCAL_ADMIN>`(`deploy/overlays/prod/patch-config.yaml`).
- 목록에 있다는 것만으로 면제가 되지 않는다 — **admin 역할 + 세션**이 함께여야 한다(`api.auth.can_run_as_root`). 반대로 admin 역할만으로도 안 된다.
- **재배포 없이 넓히기**: 관리 → 계정의 「전체 PV·직접 경로 권한」 칸(`PUT /api/admin/accounts/{username}/root` — `routes_accounts.set_root_grant`).
  - 특권 세션 관리자만 한다(아니면 403 `root_grant_privileged_only`, 세션 아니면 403 `accounts_session_required`). 자기 자신은 안 된다.
  - 활성 admin 계정에만 준다(아니면 409 `root_grant_requires_admin`). env 목록 이름은 배포 설정으로만 바뀐다(409 `root_grant_config_managed`).
  - 감사 로그 `account/root_grant`·`root_revoke`. user 로 강등하면 부여가 지워지고, 지운 계정을 같은 이름으로 다시 만들어도 이어받지 않는다.
- **env 목록 바꾸기**: 첫 관리자 이름은 values `LOCAL_ADMIN`(§3-4 (c)). 이름을 더 박으려면 사이트 `patch-config.yaml` 의 `DMS_PRIVILEGED_REQUESTERS` 를 고친다
  (§3-4 (b)). 보통은 계정 화면 부여가 낫다.
- **보호**: env 목록 ∪ 부여 플래그가 선 계정(`privileged.protected_names` — 비활성 부여 계정 포함)은 특권 관리자만 관리한다 — 만들기·역할·비활성화·삭제·관리자
  비밀번호 재설정·인증 잠금 해제(`routes_accounts.guard_privileged_account`). 메일 셀프 재설정도 막힌다(403 `privileged_account_protected`).
- **기동 거부**(설정을 잘못 섞었을 때 조용히 다른 권한 모델로 돌지 않게 — `config.Settings.from_env`): `DMS_ALLOW_PRIVILEGED_REQUESTERS=false`(모든 잡이 root 라
  아무 잡도 못 돈다), `DMS_IDENTITY_MODE` 가 비어 있지 않고 `k8s` 도 아님, `DMS_LDAP_URI` 가 있음. 확인:
  `kubectl --context <ctx> -n dms logs deploy/dms-api -c api --tail=20`.
- `DMS_PRIVILEGED_REQUESTERS: ""` 이면 계정 화면에서 부여받은 활성 admin 계정만 면제자다(자격 = env 목록 ∪ 부여 — `privileged.privileged_names`).
  부여도 없으면 맵핑만 남고 배치 자식은 모두 거부된다 — 그 상태에서는 부여할 특권 세션 관리자도 없으니 env 를 비우기 전에 부여부터 한다.
- 배치 자식은 「배치 생성자가 자격이 있으면 root」다. 아니면 `privileged_not_authorized` 로 거부한다(낮추지 않는다). 판정은 인증된 요청자 기준이다 —
  클라이언트가 보낸 `owner_username` 이 아니다.

### 10-3. 첫 관리자·관리 토큰

- 첫 관리자 만드는 법: §6-1. 이름은 `<LOCAL_ADMIN>`.
- 그 뒤 관리자는 포탈 관리 → 계정의 「계정 생성」으로 만든다(세션 관리자, 역할 user·admin. 특권 목록 이름은 특권 관리자만 — `routes_auth.create_admin_account`
  세션 경로).
- **관리 토큰(`DMS_ADMIN_TOKEN`)은 상시 자격이다** — 첫 관리자 뒤에도 admin 계정을 아무 이름으로나 만든다. 시크릿만이 아니라 api·controller 파드
  env 에도 있으므로 ns `dms` 의 `pods/exec`·파드 생성 권한도 같은 무게다. 보관·회전은 §3b-3·§3b-4.

### 10-4. 공유 토큰으로 되는 것·안 되는 것

공유 토큰(`DMS_SHARED_TOKEN`, Bearer)은 모든 노드 에이전트가 가진 admin 자격이다. 계정·배포 경로는 세션을 요구해 막는다(CLAUDE.md 「공유 토큰은 계정·배포 경로를 못 만진다」).

| 되는 것(`require_admin`) | 안 되는 것 |
|---|---|
| 관리 조회 전반(`GET /api/admin/...` — 계정·메일 설정·k8s 연동·릴리스 대상·작업 정리 상태 등) | 계정 변경 — 403 `accounts_session_required`(생성·역할·비활성화·삭제·root 부여·관리자 비밀번호 재설정·인증 잠금 해제) |
| 스토리지 등록·수정·삭제, 정책, 허용 쌍 | 빌드·릴리스·컨트롤 상태·아티팩트 경로 변경·레지스트리 이미지 삭제·작업(요청) 삭제·배치 기록 삭제 — 403 `admin_session_required`(`auth.require_session_admin`) |
| 노드 배치 제외(`PUT /api/admin/nodes/{name}/exclusion`), 포탈 부제 | 메일 설정 저장·연결 확인·테스트 메일 — 403 `mail_settings_session_required` |
| k8s 연동 「지금 동기화」·보조 연결 확인 | k8s 연동 변경(호출 노드·클러스터·토큰·StorageClass 매핑) — `require_session_admin` |
| 배치 CSV 해석·템플릿 | 잡 제출 — 403 `privileged_not_authorized`. 배치 생성 — 특권 세션만 |
| 에이전트 API(`require_user` + `routes_agent._node_from`) — `x-dms-actor: node:<아무 노드>` 로 노드 사칭: 보고(노드·마운트 상태), k8s 동기화 claim(대상 클러스터 리더 토큰 수령), 결과 업로드(RoleBinding·PVC 인벤토리 → 사용자 PV 권한 맵핑 변경) | — |

- 공유 토큰 경로의 `x-dms-actor` 는 `node:<이름>`(에이전트)만 받는다 — 스크립트가 다른 값을 실으면 400 `invalid_actor`.
- 토큰이 틀리면 401 `invalid_token` 이다.

### 10-5. 계정 관리

- **관리자 화면**(관리 → 계정 — 세션 관리자): 계정 생성, 역할(user·admin), 비활성화, 삭제, 비밀번호 재설정(인증번호 없이 — 인증번호 전체 잠금 중에도 된다,
  `routes_auth.admin_password_reset`), root 자격 부여(10-2). 특권 계정은 특권 관리자만(10-2 「보호」).
- **인증 잠금 해제**(관리 → 보안 이벤트 — 세션 관리자): 아이디별 「잠금 해제」(DB 의 인증 실패 기록 삭제)와 「전체 잠금 해제」(api 메모리). 둘 다 감사 로그와
  이벤트에 누가 했는지 남는다. 상한 값은 §11.
- **사용자 셀프서비스**: 가입·비밀번호 재설정은 메일 인증번호(6자리·5분)로 한다. 받는 주소는 `<아이디>@<EMAIL_DOMAIN>` 파생이다.
- **비밀번호**: 저장은 scrypt(`repositories.accounts._hash_password`), 전송은 포탈이 봉인한다(평문은 422 `password_encryption_required` — `x-admin-token` 부트스트랩만 예외).
- **세션 끊기**: 비밀번호가 바뀌면(셀프·관리자 재설정) 그 계정의 세션 세대가 회전해 기존 로그인이 모두 끊긴다(401 `session_revoked` — `api.auth.current_identity`,
  DB `accounts.session_epoch`). 비활성화한 계정은 401 `account_disabled` 다(로그인도 — 비밀번호가 맞을 때만, 실패로 세지 않는다; 셀프 비밀번호
  재설정은 발급·변경 모두 403 `account_disabled`).

### 10-6. 세션·로그아웃·여러 포탈

- 세션은 서명된 쿠키 `dms_session` 이다(Starlette `SessionMiddleware`, 서명 키 `DMS_SESSION_SECRET` — `api/app.py create_app`). prod 렌더는
  `DMS_SESSION_COOKIE_SECURE: "true"` 라 https 로만 실린다.
- **수명 14일**: DMS 는 `max_age` 를 주지 않아 Starlette 기본 1,209,600초다. 쿠키는 세션 내용이 바뀔 때만 다시 발급되므로 로그인한 때부터 14일이다.
  유휴 만료는 없다. (이미지의 Starlette 판은 고정돼 있지 않다 — `pyproject.toml` 의 `fastapi>=0.115`. 위 동작은 개발 환경의 Starlette 1.3.1 로 확인했다.)
- **로그아웃은 그 브라우저의 쿠키만 지운다**(`/api/auth/logout` — `session.clear()`). 서버에 세션 표가 없어서, 복사된 쿠키 값은 14일 동안 유효하다.
  서버 쪽에서 끊는 길은 셋이다: 그 계정의 비밀번호 변경(세션 세대 회전), 계정 비활성화, `DMS_SESSION_SECRET` 회전(전원 — §3b-4). 이 한계는 BACKLOG 에 적었다.
- **한 브라우저에서 여러 포탈**: 쿠키 이름이 같고 쿠키는 포트가 아니라 호스트 이름으로 갈린다. 같은 호스트의 다른 포트로 두 포탈을 열면 서로 로그아웃시킨다 —
  포탈마다 다른 FQDN 을 쓴다.
- **포탈 부제**: 관리 → 포탈 설정의 부제(`PUT /api/admin/portal-settings` — `routes_portal`)가 사이드바·로그인 화면·브라우저 탭 제목에 「메인 - 부제」로 보인다.
  사이트(운영·시험)를 구분하는 데 쓴다.

### 10-7. 웹 인증 하드닝 — 비밀번호 봉인·로그인 보호 장치(2026-09-07, 2026-11-03 보호 장치 키 추가)

`20-config.yaml` 의 아래 키들이 기본 자세다. 값을 바꾸는 법은 §3-4 (b)(영구 = 사이트 `patch-config.yaml` → `--only S11`, 임시 = ConfigMap 수정 +
`rollout restart` — 다음 설치기 실행이 되돌린다).

- `DMS_PASSWORD_ENCRYPTION_REQUIRED: "true"` — 포탈은 비밀번호를 WebCrypto 로 봉인해(`password_enc`, ECDH P-256 + HKDF + AES-256-GCM) 보내고 서버는
  평문 `password` 를 422 `password_encryption_required` 로 거절한다. 서버 키는 `DMS_SESSION_SECRET` 에서 유도되므로 시크릿 회전 = 키 회전(포탈이 자동
  재수신 — §3b-4). **첫 관리자 부트스트랩**(`x-admin-token` curl — §6-1)만 평문을 받는다.
- **자동화가 로그인 세션이 필요하면 봉인해서 로그인한다**(공유 토큰으로는 잡을 낼 수 없다 — §10-4). 봉인은 저장소의 `dms.api.password_transport.seal_with_info`
  하나로 된다: 공개키 `GET /api/auth/transport-key`(무인증) → `seal_with_info(<그 응답>, <비밀번호>, purpose="login", username=<아이디>)` → 본문
  `{"username": <아이디>, "password_enc": <봉인>}` 를 `POST /api/auth/login` → 응답의 `Set-Cookie: dms_session=…` 를 이후 요청에 싣는다(`routes_auth.login`·
  `_password_from`). `dms` 패키지(`cryptography` 포함)가 설치된 호스트에서 — 아래 예는 이 문서를 쓰며 실행해 보지 않았다(**미검증**; 저장소 안
  도우미 스크립트는 BACKLOG):

  ```python
  import getpass, json, ssl, urllib.request
  from dms.api.password_transport import seal_with_info
  API, USER = "https://<FQDN>", "<아이디>"
  ctx = ssl.create_default_context(cafile="<사내CA>")
  info = json.load(urllib.request.urlopen(API + "/api/auth/transport-key", context=ctx))
  body = {"username": USER, "password_enc": seal_with_info(info, getpass.getpass(), purpose="login", username=USER)}
  req = urllib.request.Request(API + "/api/auth/login", data=json.dumps(body).encode(), headers={"content-type": "application/json"})
  cookie = urllib.request.urlopen(req, context=ctx).headers["set-cookie"].split(";", 1)[0]   # 화면·로그에 내지 않는다
  ```

- `DMS_LOGIN_RATE_LIMIT_ATTEMPTS: "10"` / `..._WINDOW_SECONDS: "60"`: **아이디별** 실패 10회/분을 넘으면 429 `login_rate_limited` + Retry-After 다. 창이
  지나면 저절로 풀린다(잠금 아님). 원 IP 로는 막지 않는다(2026-11-03 — SNAT 뒤 전원이 함께 잠겼다). 관찰만 하려면 이 ATTEMPTS 와 아래
  `DMS_LOGIN_ID_ATTEMPT_LIMIT` 를 **둘 다** 0 으로 둔다(이것만 0 이면 남의 틀린 비밀번호도 총 시도로 세어 남의 아이디가 429 에 묶인다).
- `DMS_LOGIN_ID_ATTEMPT_LIMIT: "60"`(/60초): 아이디별 총 시도(성공 포함)다. 맞는 비밀번호를 반복해 CPU 를 태우는 경로를 막는다. e2e 한 벌의 admin
  로그인은 약 31회라 영향이 없다(60 에 다가가면 e2e `env.ts` 에 이 키를 준다). 봉인 로그인을 반복하는 자동화도 이 상한 안에서 세션을 재사용한다.
- `DMS_LOGIN_SCRYPT_SLOTS: "2"` / `..._MAX_WAIT_SECONDS: "5"`: 비밀번호 검증 동시 실행 수다. 못 얻으면 최대 5초 기다린 뒤 503 `login_busy`(과부하 신호,
  차단 아님).
- `DMS_LOGIN_GLOBAL_PACE_LIMIT: "0"`(꺼짐) / `..._WINDOW_SECONDS: "60"` / `..._MAX_WAIT_SECONDS: "15"`: 대기형 전체 스위치다.
  - 스프레이 공격 중 출발지 차단(Avi·방화벽) 전까지 켠다. 예: LIMIT "60" → 전 아이디 실패를 분당 60회로 늦춘다.
  - 공격 대응처럼 지금 당장이면 임시 경로(ConfigMap 수정 → `kubectl --context <ctx> -n dms rollout restart deploy/dms-api` — 재시작은 메모리 카운터도
    비운다), 계속 둘 값이면 영구 경로(§3-4 (b)).
  - 켠 채 기동하면 보안 이벤트에 `login_pacing_enabled` 가 남는다.
- `DMS_LOGIN_ALARM_WINDOW_SECONDS: "600"` / `_FAILURES: "30"` / `_ATTEMPTS: "300"` / `_SOURCE_FAILURES: "20"`: 경보 임계다(이벤트만).
  - 출발지 경보는 창당 50건까지만 남는다.
  - 원 IP 가 한 주소로 모이는 배치(LB 가 PROXY v2 를 못 붙여 모든 사용자가 SE 주소로 보이는 사이트 — §2g-4)는 SOURCE_FAILURES 를 0 으로 둔다.
    2026-11-16 부터 렌더 자리(옛 `GATEWAY_PROXY_PROTOCOL=off` → 0, `REPLACE_LOGIN_ALARM_SOURCE_FAILURES`)는 없다 — prod 는 base 값 20 그대로이고,
    0 이 필요한 사이트는 `patch-config.yaml` 에 `DMS_LOGIN_ALARM_SOURCE_FAILURES: "0"` 을 더한다(§3-4 (b) → `--only S11`). 고정 오버레이도 같다.
- 확인은 포탈 **관리 → 보안 이벤트**에서 한다(보호 장치 현재 값·인증번호 잠금 해제·최근 이벤트·출발지 IP).
- 검증(설치 뒤 한 번 — §7 과 함께): 평문 로그인
  `curl -sS --cacert <사내CA> -H 'content-type: application/json' -d '{"username":"u","password":"p"}' https://<FQDN>/api/auth/login` = HTTP 422 **이고 본문
  `detail` 이 `password_encryption_required`**(상태 코드만 보지 않는다 — content-type 을 빼면 본문 검증에서 다른 422 가 나와 설정이 꺼져 있어도
  「통과」한다); 봉인 로그인 = 200 + Set-Cookie; 틀린 비밀번호 봉인 10회 후 11번째(정답) = 429; 같은 출발지에서 다른 아이디로 10회 실패한
  뒤에도 내 아이디 로그인 = 200(IP 축 없음); 위조 `X-Real-IP`·`X-Forwarded-For` 는 보안 이벤트의 출발지에 반영되지 않는다(Traefik 이 지운다 — forwardedHeaders 를 비워 둔 경우, §2g-2·§2g-5); 포탈 화면 로그인
  정상 + 개발자도구 네트워크 탭 본문에 `password` 없음.

## 11. 인증 메일 — 사내 Knox 메일 릴레이 (2026-10-01)

회원가입·비밀번호 재설정 인증번호 메일은 **메신저 서버의 릴레이**가 보낸다(웹서버는 Knox Mail API 에 직접
닿지 않는다): `/usr/lib/zabbix/alertscripts/knox_mail_dms_certi.py`(systemd `knox-mail-dms-certi`, root, TCP
8025)와 같은 디렉터리의 `knox_mail_dms_certi.env`. DMS 쪽 구현은 `src/dms/api/mailer.py` — 메일은 dms-api 만 보낸다. **처음 켜는 절차(사용자에게
열기 전의 게이트)는 §6-2** 다. 이 절은 설정 칸·운영 신호·상한·구제다.

설정은 **포탈 관리 → 메일 설정**에서 한다(재시작 불필요, DB 가 env 를 이긴다 -- 칸을 비우면 아래 env·기본값):

| 포탈 칸 | env 기본값 | 비고 |
|---|---|---|
| 발송 방식 | `DMS_MAILER_BACKEND` (`stub`) | `stub` = 메일 없이 화면에 코드 표시(개발용 -- **운영 금지**: 아이디만 알면 누구나 가입·재설정), `knox_relay` = 실메일. 운영은 사이트 `patch-config.yaml` 로 env 기본값을 `knox_relay` 로 고정한다(§6-2 4·§3-4 (b)). 포탈의 stub 전환은 확인 체크 뒤에만 저장된다 |
| 릴레이 서버 IP·포트·프로토콜 | `DMS_MAIL_RELAY_URL` (`http://<메신저 서버 IP>:8025`) | 포트 기본 8025, http |
| 인증 키 | `DMS_MAIL_RELAY_TOKEN` (Secret 권장) | `knox_mail_dms_certi.env` 의 `RELAY_TOKEN` 과 같은 값. 포탈 입력은 봉인 전송·암호화 저장, 다시 볼 수 없음. **키는 주소에 묶인다**: env 키는 주소가 `DMS_MAIL_RELAY_URL` 그대로일 때만 쓰이고(포탈에서 IP·포트·프로토콜 중 하나라도 정하면 포탈에 키를 넣어야 한다), 포탈 키가 있을 때 주소를 바꾸면 같은 저장에서 키를 다시 입력해야 한다 |
| 타임아웃(초) | `DMS_MAIL_RELAY_TIMEOUT_SECONDS` (20) | 릴레이의 KNOX_CONNECT_TIMEOUT_SECONDS(3)+KNOX_TIMEOUT_SECONDS(10) 보다 길게 |
| 서비스명 | `DMS_MAIL_SERVICE_NAME` (`Supercom 포털`) | 메일 제목·본문 표시 |
| (읽기 전용) 받는 도메인 | `DMS_ACCOUNT_EMAIL_DOMAIN` | 받는 주소 = `<아이디>@<도메인>` |

릴레이 쪽 준비(메신저 서버):
- `RELAY_ALLOWED_CLIENTS` 에는 **릴레이가 보는 DMS 의 접속 IP** 를 넣는다 -- 파드 송신은 보통 노드 IP 로 SNAT 되므로 dms-api 파드가 뜰 수 있는
  노드 IP 들이고, prod 오버레이는 dms-api 를 마스터에만 두므로(`deploy/overlays/prod/patch-placement.yaml`) **마스터 3대의 노드 IP** 다(SNAT 하지 않는
  CNI 면 파드 대역 — 사이트 확인; 비우면 토큰만 검사).
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

**사내 릴레이 없이 시험하기(시험 클러스터)**: `tests/fake_knox_relay.py` 가 같은 계약을 흉내 낸다(테스트베드 절차는 `deploy/testbed/README.md` §9) --
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
curl --noproxy '*' -sf "$API/api/admin/request-purges" -H @"$AUTH" | python3 -m json.tool     # API·AUTH 는 §6-4(헤더 파일)
# {"pending": 대기 행 수, "stalled": last_error 가 있는 행 수, "oldest_requested_at": ...,
#  "items": [{"request_id", "stage": k8s|files|purging, "attempts", "last_error", "requested_at",
#             "requested_by", "next_attempt_at"}]}   # 오래된 순 최대 50건
kubectl --context <ctx> -n dms get pods -l dms.io/purge=1 -o wide          # purge 파드(언제나 최대 1개, dms-artifact-purge-<hash>)
kubectl --context <ctx> -n dms logs deploy/dms-controller -c controller | grep request-purge   # 예기치 못한 예외(stderr)
```

정상이면 삭제 후 1~3분 안에 `pending` 이 0 이 된다. 실패는 포기하지 않고 백오프(15초 × 2^n, 상한 900초)로 계속
재시도하며 `last_error` 로 보인다. 이벤트(전부 `request_id` NULL, component `request-purge` — id 는 payload)는
`purge_failed`(사유가 바뀔 때만)·`purge_ref_rejected`·`artifact_left_at_old_base`·`artifact_reappeared`·`request_purged`.

| `last_error` | 뜻 | 조치 |
|---|---|---|
| `purge_waiting_pods` | 삭제 10분이 지나도 그 잡의 Pod·vcjob 이 남아 있다(대개 Terminating) | 아래 「Terminating 파드」. 파일 단계로 넘어가지 않는 것이 정상(안전 > 진행) |
| `purge_k8s_failed` | k8s 나열·삭제 실패(API 서버·RBAC·Volcano CRD 부재) | `kubectl --context <ctx> -n dms auth can-i delete pods --as=system:serviceaccount:dms:dms-controller`, vcjob CRD 확인 |
| `purge_base_unavailable` | base 를 열 수 없다(마운트 빠짐) 또는 삭제 시점 base 가 비어 있었다 | 컨트롤러 파드의 `<SHARED_DIR>/dms/artifacts` hostPath(노드의 공용 디렉터리 마운트, §2d)·artifact base 화면 3홉 확인 |
| `purge_base_unsafe` | base 가 root 소유가 아니거나 g+w/o+w, 또는 `.dms-trash` 가 심링크·0700 아님·다른 FS | §2b 전제(base root:root 755)로 되돌린다. `.dms-trash` 는 root 0700 디렉터리여야 한다 |
| `artifact_dir_unexpected` | `<base>/<job_id>` 가 root 소유 디렉터리가 아니다(심링크·파일·남의 소유) — §2b-1 위반 신호 | 그 경로를 운영자가 직접 확인(누가 만들었나). DMS 는 건드리지 않는다 |
| `purge_detach_failed` | rename 실패(일시 FS 오류) | 자동 재시도. 계속되면 컨트롤러 로그 |
| `purge_no_node` | purge 파드를 올릴 노드가 없다 — 신선한 에이전트 보고가 **지금 base 를 exists·writable** 로 확인한 노드 중 배치 제외·cordon·스케줄 불가가 아닌 노드 | 노드 화면(에이전트 보고)·노드 배치 제외 목록 확인. 제외를 풀면 다음 틱에 진행 |
| `purge_pod_failed` | purge 파드 수준의 실패 — 이미지 pull 실패, Pending 10분 초과, 파드 생성 실패, 또는 끝났는데 실린 이름을 **하나도** 못 지웠다(그 요청의 항목 탓이라는 근거가 없다). 그 파드에 실린 요청 전부가 이 표시를 받고, 백오프 뒤 **다시 한 파드에 함께** 실린다 | `kubectl --context <ctx> -n dms describe pod -l dms.io/purge=1`(지워지기 전 — 다음 틱에 지운다), 이미지(잡 이미지 `DMS_JOB_IMAGE`/포탈 job-image) pull 가능 여부, purge 파드를 올릴 노드의 `<base>` 마운트 |
| `purge_entry_failed` | 같은 purge 파드의 다른 이름은 지워졌는데 **이 요청의** trash 항목만 남았다(스크립트는 실패해도 다음 이름으로 가므로 시도했는데 못 지운 것), 또는 데드라인·축출로 끊길 때 지우던 항목이다. 끊긴 지점 뒤의 이름(시도조차 못 함)은 `purge_pod_failed` 로 묶음에 남는다. 이 요청은 다음부터 **혼자** 실린다 — 묶어 실을 다른 요청이 없을 때(due 가 된 지 5분이 넘으면 먼저) | 노드에서 `<base>/.dms-trash/<job_id>` 를 본다: EIO 같은 FS 손상, 그 아래 다른 장치 마운트(`--one-file-system` 이 넘지 않는다), 1시간 데드라인을 넘는 거대 트리(재시도마다 줄어 결국 끝난다). 지울 수 없는 원인이면 운영자가 고친 뒤 다음 재시도가 마무리한다 |
| `purge_pod_stuck` | purge 파드가 생성 후 1시간 10분(데드라인 3600초 + 유예 600초)이 지나도 끝나지 않는다 — 노드가 죽어 Running 으로 남았거나 Terminating 에 갇혔거나, rm 이 멈춘 마운트에서 D 상태라 kubelet 이 데드라인을 집행하지 못했다. 그 뒤의 모든 결과 파일 정리가 줄을 서 있다(파드는 언제나 1개 — 두 번째를 띄우지 않는다, DMS 는 이 파드를 지우지 않는다) | `kubectl --context <ctx> -n dms get pods -l dms.io/purge=1 -o wide` 로 노드를 보고 `kubectl --context <ctx> get node <node>`. 노드가 NotReady 로 확정되고 복구되지 않을 때만 `kubectl --context <ctx> -n dms delete pod <name> --force --grace-period=0`(아래 「Terminating 파드」 — 살아 있는 노드의 rm 을 떼면 무엇이 남는지 모른다). 노드가 살아 있으면 그 노드의 `<base>` 마운트(멈춘 공용·데이터 스토리지 마운트)부터. 파드가 사라지면 다음 틱에 표시가 거둬지고 새 파드가 이어서 비운다 |
| `purge_target_still_present` | 지운 요청·잡 id 가 DB 에 **다시** 있다(DB 복원·변조) — 정리는 k8s·파일을 전혀 건드리지 않는다 | DB 를 확인한다. 되살아난 행이 맞으면 그 아웃박스 행을 운영자가 지운다(정리하지 않음). 다시 지우려면 포탈에서 다시 삭제 |
| `purge_row_invalid` | 아웃박스 행 모양이 깨졌다(jobs JSON·job id 형식·단계·base) — 아무것도 하지 않는다 | 행을 운영자가 확인(변조 의심). 고칠 수 없으면 행을 지우고 남은 디렉터리는 운영자 판단 |
| `purge_failed` | 예기치 못한 예외 | 컨트롤러 로그의 `request-purge error on <id>` |

**Terminating 파드 강제 삭제는 노드가 정말 죽었을 때만.** 살아 있는(또는 분할된) 노드의 파드를 `--force
--grace-period=0` 으로 지우면 API 객체만 사라지고 컨테이너는 계속 돌 수 있다 — 그 launcher 가 정리 **뒤에**
`<base>/<job_id>/<phase>` 를 다시 만들면 행 없는 디렉터리가 남는다(정리 중이면 `artifact_reappeared` 로 k8s 단계부터
다시 한다). 노드가 NotReady 로 확정되고 복구되지 않을 때만:
`kubectl --context <ctx> -n dms delete pod <name> --force --grace-period=0`.

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
- 값을 바꾸는 법은 §3-4 (b)(영구 = 사이트 `patch-config.yaml` → `--only S11` 이 api·controller 를 다시 띄운다). 만료는 되돌릴 수 없다 — 보존일을 크게 줄이기 전에 영향 범위를
  확인한다(`SELECT COUNT(*) FROM data_jobs WHERE artifacts_expired_at IS NULL AND updated_at < '<날짜>'`).
- 마이그레이션은 자동이다(`request_purges.kind`·`data_jobs.artifacts_expired_at`·부분 인덱스 `idx_data_jobs_expiry`). api 와
  controller 가 둘 다 새 이미지여야 한다(옛 api 는 만료 표시를 모르고 404 를 낸다). 첫 배포 뒤 오래된 이력이 많으면 며칠에 걸쳐
  따라잡고, 그동안 요청 삭제 정리는 만료 행 뒤에 최대 50행만큼만 선다.

## 14. 일상 운영·점검·문제 해결

### 14-1. 헬스·점검

**프로브와 엔드포인트**(`deploy/k8s/40-api.yaml`, `api/app.py create_app`)
- `/healthz` — liveness(30초 주기). 이벤트 루프에서 바로 답한다 — DB 와 무관하다.
- `/readyz` — readiness(10초 주기). DB 에 `SELECT 1` 을 보낸다. 실패하면 503 으로 Service 에서 빠지고, **30회 연속** 실패(약 5분)하면 프로세스가 스스로
  끝나 재시작한다(`DMS_READYZ_EXIT_FAILURES`, 0 = 끔). 성공 본문에 재연결 횟수(`reconnects`·`last_reconnect_at`)가 있다.

**설치 호스트에서**

```sh
kubectl --context <ctx> get --raw '/api/v1/namespaces/dms/services/http:dms-api:http/proxy/readyz'; echo   # {"status":"ok","reconnects":…}
curl -sS --resolve <FQDN>:443:<VIP-A> --cacert <사내CA> https://<FQDN>/readyz; echo                       # VIP·Avi·Traefik 경로까지
VERIFY_NO_PROBES=1 CACERT=<사내CA> sh deploy/install.sh --context <ctx> --only S12   # 전체 재점검(읽기만) — RESULT: PASS(사내 CA 를 믿는 호스트면 CACERT 생략)
kubectl --context <ctx> -n dms get deploy,ds,cronjob,pods -o wide
kubectl --context <ctx> -n dms get cluster dms-pg                       # READY 3/3, STATUS healthy
kubectl --context <ctx> -n dms get ingress dms-portal-traefik           # CLASS <INGRESS_CLASS> · HOSTS <FQDN>
sh deploy/ingress-probe.sh --context <ctx> --domain <FQDN> --cacert <사내CA>   # Traefik 이 도는 노드마다 NODE … PASS, RESULT: PASS
kubectl --context <ctx> -n dms get cronjob dms-pg-dump                  # LAST SCHEDULE 이 1시간 안
kubectl --context <ctx> get pv | grep dms-pgdata                        # Released 가 없다(있으면 §2f-5 재활용 런북)
```

`VERIFY_NO_PROBES=1` 을 빼면 S12 가 프로브 파드(PG 경로 대조)를 띄워 더 깊게 본다. 입구 확인(ingress-probe)은 파드를 띄우지 않아 둘 다에서 돈다.

**포탈의 운영 신호**

| 화면 | 보는 것 |
|---|---|
| 운영 → 대시보드 | 큐·노드 부하, 유지보수·drain 표시 |
| 관리 → 보안 이벤트 | 로그인 보호 장치의 현재 값, 인증번호 잠금, 최근 보안 이벤트·출발지 IP(§10-7) |
| 작업 → 전체 작업 툴바 | 「결과 파일·파드 정리 중 N건 · 지연 M건」과 지연 사유(§12) |
| 운영 → 아티팩트 경로 | 3홉(API·컨트롤러·노드), DMS 상태 디렉터리와 겹치는 스토리지 경고, 결과 파일 보존·만료·정리 지연(§12b) |
| 운영 → 릴리스 | 이미지 드리프트 배지, 「보안 설정 드리프트」(§9-8 — S11 로만 사라진다) |
| 스토리지 → 노드 | 에이전트 보고 신선도·스토리지 상태·배치 제외 |
| 스토리지 → k8s 연동 | 「개요」 탭: 클러스터 상태별 개수, 호출 노드 카드(마지막 호출·성공·실패). 「클러스터」 탭: 클러스터별 마지막 동기화 결과. 메일 stub 경고는 두 탭 모두(§6-5) |

**모니터링 연계**
- DMS 에는 Prometheus 지표 엔드포인트가 없다. 입구 Traefik 의 지표(차트 기본 `metrics.prometheus`)는 클러스터 운영자의 것이다.
- 지표 API(`require_admin` — 공유 토큰 Bearer·세션 관리자 모두 조회 가능): `GET /api/admin/metrics/jobs`·`/nodes`·`/infra`·`/queue`(`api/routes_metrics.py`). `metrics/jobs` 는 2026-10-28 부터
  `files_total`·`bytes_total` 대신 `sync_items_total`·`sync_bytes_total`·`rm_items_total` 을 낸다 — 옛 키를 읽는 외부 스크립트는 고친다.
  2026-11-11 부터 창 없는 비종단 상태별 수 `live_by_state` 를 더 내고, `throughput` 의 일 버킷(`bucket: "day"`, 창 48h 초과)은
  UTC 가 아니라 KST 날짜로 묶는다(시간 버킷은 그대로 UTC).
  `metrics/nodes` 의 `points` 에는 2026-11-11 부터 인접 보고 간격이 `DMS_AGENT_REPORT_STALE_SECONDS`(기본 300초)를 넘는 곳마다 공백 표지 점이 하나
  낀다 — 값이 전부 null 이고(`disks: []`) `at` 은 공백의 가운데다. 공백 직후 점의 `net_rx_bps`·`net_tx_bps` 도 null 이다(`metrics_series.build_node_points`).
  점 수를 보고 횟수로 세거나 값이 전부 null 인 점을 정상 보고로 읽는 외부 스크립트는 고친다.
- 입구의 요청별 로그는 클러스터 Traefik 의 접근 로그다 — 켜고 모으는 것은 클러스터 운영자 몫이다(차트 values `logs.access.enabled`, 기본 꺼짐).
  DMS 는 Traefik 설정을 바꾸지 않는다. 로그인 출발지(원 IP)는 앱 보안 이벤트의 `client_ip`·`peer` 로 본다(§2g-5).
- 이벤트 보존 30일(`DMS_EVENT_RETENTION_DAYS`), 에이전트 보고 보존 30일(`DMS_AGENT_REPORT_RETENTION_DAYS`).

### 14-2. 정기 작업 달력

| 주기 | 일 | 어떻게 | 절 |
|---|---|---|---|
| 매일 | 덤프가 돌았나 | `kubectl --context <ctx> -n dms get cronjob dms-pg-dump`(LAST SCHEDULE), 실패 Job 없음 | §2f-5 |
| 매일 | 보안 이벤트 검토 | 관리 → 보안 이벤트 — 경보(`login_*_alarm`)·잠금 | §10-7 |
| 매주(사이트가 정함) | 공용 스토리지 밖 사본 | 덤프 rsync, 시크릿 gpg(바뀌었을 때) | §3b-5 |
| 매주 | 정리·만료 지연 0 | 작업 목록 툴바, 아티팩트 경로 화면 | §12·§12b |
| 매월 | 포탈 인증서 만료일 | `kubectl --context <ctx> -n dms get secret <PORTAL_TLS_SECRET> -o jsonpath='{.data.tls\.crt}' \| base64 -d \| openssl x509 -noout -enddate` | §3b-2 |
| 매월 | Released PV 0 | `kubectl --context <ctx> get pv` | §2f-5 |
| 분기(또는 용량을 보고) | 레지스트리 GC, 공용 디렉터리 용량 | `du -sh <SHARED_DIR>/dms/{registry,backup,artifacts}`(공용 스토리지를 마운트한 노드, root) | §2e-5 |
| 현장 빌드마다 | 새 DMS 태그의 레지스트리 밖 사본 | `skopeo sync --dest dir` | §2e-5 |
| 연 2회 이상 | CNPG·PG minor, K8s minor 맞춤 | 점검 창 | §2f-6·§4-3 |
| 클러스터(RKE2) 업그레이드·Traefik 설정 변경 뒤 | 입구 재확인(Traefik 판·보안 권고는 클러스터 운영자) | `sh deploy/install.sh --context <ctx> --site <site> --only S8,S12` | §2g-6 |
| 새 DMS 커밋 | 앱 업그레이드 | §4-2 | §4 |
| 사이트 정책 | 시크릿·토큰 회전 | §3b-4 | §3b |

### 14-3. 노드·마스터 유지보수

**워커**
- DMS 에서 빼기: 포탈 스토리지 → 노드의 「배치 제외」(`PUT /api/admin/nodes/{name}/exclusion`) 또는 `kubectl --context <ctx> cordon <노드>`. 에이전트는
  cordon·NoSchedule 에도 남아 보고를 계속하고, NoExecute taint 도 견딘다(`deploy/k8s/50-agent-daemonset.yaml` tolerations) — 그래서 DMS 가 그 노드를 빼고 계획한다.
- 도는 잡은 끝나기를 기다리거나 취소한다. 빌드 노드면 컨트롤 상태에서 다른 노드로 바꾼다(§6-3).
- 워커 전용 애드온(CNPG 오퍼레이터·pg_dump)은 다른 워커로 옮겨진다 — 워커가 둘 이상이어야 한다(§0-2).
- Traefik 이 도는 노드(RKE2 는 모든 노드)는 LB 풀 멤버이기도 하다. Traefik 파드는 DaemonSet 이라 drain 해도 남고 노드와 함께 내려간다 — 재부팅
  전에 LB 풀에서 그 멤버를 빼 두면(비활성) 헬스가 거를 때까지의 새 연결 실패가 없다(§2g-6 「롤링 공백」과 같은 이유). 노드를 더하거나 빼면 LB 풀도
  같이 고친다(기준은 S1 의 `Traefik 파드 Ready N/M (노드: …)` 줄 — §2g-4).
- 재부팅 직후 첫 잡은 에이전트 첫 보고(30초)·프로브 전이면 `no_ready_sync_candidate` 로 거부될 수 있다 — 1~2분 뒤 다시 낸다.

**마스터**(§2f-7 규칙 — 어기면 PG 데이터가 깨질 수 있다)
- 한 대씩. 다음 대로 넘어가기 전에 `kubectl --context <ctx> -n dms get cluster dms-pg` 가 healthy(readyInstances 3)인지 본다. 그 마스터가 PG primary 면 먼저
  switchover(§2f-6).
- **VM 전원이 꺼진 것을 확인하기 전에는** 파드 강제 삭제(`--force --grace-period=0`)·`node.kubernetes.io/out-of-service` taint·Node 삭제를 하지 않는다 — 같은
  PGDATA 를 두 서버가 연다.
- 그 마스터에 Traefik 이 돌면(RKE2 마스터는 taint 가 없어 돈다) LB 풀 멤버이기도 하다 — 위 워커와 같이 재부팅 전에 LB 에서 빼 둔다. 레지스트리·
  api·controller 는 남은 마스터로 옮겨진다(레지스트리 약 10~20초 공백, 설계 2-2).
- **마스터 교체**: 옛 VM 을 끈 뒤 Node 를 지운다. 새 마스터에 S0 을 다시 한다 — 공용 스토리지 마운트, SELinux, `fs.protected_*`, 레지스트리 신뢰 drop-in(§2e-2),
  레지스트리 자기 이미지 반입·고정(§2e-4 2)). 입구용 노드 준비(옛 sysctl·빈 포트)는 없다. 클러스터에 붙인 뒤 그 노드에 Traefik 파드가 떴는지 보고 LB 풀
  멤버(옛 노드 :443 → 새 노드 :443)를 고치고 `sh deploy/install.sh --context <ctx> --only S1,S6,S12`(S1 = Traefik 파드 Ready·노드 목록, S6 = pull 프로브,
  S12 = 검증 — ingress-probe 가 새 노드 :443 도 본다)로 확인한다.

**공용 스토리지 유지보수**: 그 노드의 DMS 상태 파드(api·controller·registry·CNPG 인스턴스·pg_dump)를 먼저 비운다 — primary 면 switchover 뒤 cordon + 파드 삭제 →
재마운트 → uncordon(§2f-7, 설계 3-2 「G2 보충」).

### 14-4. 문제 해결 색인

| 증상 | 원인·확인 | 갈 곳 |
|---|---|---|
| 설치기 한 줄 사유(`[FAIL] …`) | 단계별 표 — S4 §2e-3, S5·S6 §2e-4, S8 §2g-3·§2g-6, S9 §2f-3, S10 §2f-4. 전체 흐름·종료 코드 §2h, `sh deploy/install.sh --help` | 각 절 |
| `RESULT: UNKNOWN`(exit 4) | 프로브가 판정하지 못했다(파드 미기동·읽기 실패). 실패가 아니다 — 출력이 말하는 대로 노드에서 확인한 뒤 같은 단계부터 | §2h |
| api·controller CrashLoop, 로그에 `is missing or a placeholder` | `dms-secrets` 키가 없거나 자리표시자 | §3b-1 |
| api·controller CrashLoop, 로그에 `does not use LDAP`·`must not be false`·`must be >=` | 설정 키가 기동 거부 규칙에 걸렸다(`config.Settings.from_env`) | §3-4·§10-2 |
| api·controller 로그에 EACCES, 스토리지가 `not_a_mountpoint` 로 Missing(RHEL) | SELinux enforcing | `sh deploy/install.sh --context <ctx> --only S2b`, prod README 「0. 사이트 준비」 2 |
| ImagePullBackOff | 노드 레지스트리 신뢰(S0-4)·마스터 레지스트리 이미지 고정(S0-5)·태그 없음(S5) | §2e-2·§2e-4, `--only S6` |
| FailedMount `<SHARED_DIR>/dms/…` | 그 노드에 공용 디렉터리 마운트가 없다, 또는 S0 전 | §2d |
| 포탈이 안 열린다(연결 실패·TLS 오류·404·502/503) | ① 앱부터: `kubectl --context <ctx> get --raw '/api/v1/namespaces/dms/services/http:dms-api:http/proxy/readyz'` — 실패면 앱·DB(아래 `/readyz` 행). ② 입구: `kubectl --context <ctx> -n dms get ingress dms-portal-traefik`(CLASS·HOSTS), `sh deploy/ingress-probe.sh --context <ctx> --domain <FQDN> --cacert <사내CA>`(노드마다 — NODE 근거: `TRAEFIK DEFAULT CERT` = tls 시크릿·SNI·class, 404 = Traefik 이 Ingress 를 안 집었다, 5xx = dms-api), `kubectl --context <ctx> get pods -A -o wide \| grep -i traefik`(노드마다 Ready). ③ LB: Avi 풀 멤버(Traefik 이 도는 노드 :443) — 모두 DOWN 이면 헬스 모니터(SNI·Host 없는 HTTPS 모니터는 기본 인증서 + 404 라 DOWN)·망 방화벽, 일부만이면 그 노드의 Traefik. ④ 노드 직접(ingress-probe)은 PASS 인데 VIP 경유만 TLS 가 깨진다(`wrong version number`) = LB 가 PROXY v2 를 붙이는데 Traefik websecure 에 trustedIPs 가 없다. ⑤ 인증서: `openssl s_client -connect <VIP-A>:443 -servername <FQDN> -CAfile <사내CA> </dev/null \| openssl x509 -noout -enddate`(만료 감시 없음 — §3b-2). IP 로 접속하면 기본 인증서 + 404 가 정상이다. ⑥ 본문 있는 POST 만 가끔 502 = 앱 keep-alive ≤ Traefik `idleConnTimeout`, 413 = 본문 상한(아래 행). 증상별 표는 §2g-6 「문제 해결」 | §2g-3·§2g-6·§3b-2 |
| S1 `IngressClass … 없음`·S8 `Traefik 판정 X — …` | 클러스터에 Traefik(IngressClass controller `traefik.io/ingress-controller`·`providers.kubernetesingress`·TLS 진입점)이 없거나 이름이 다르다 — **DMS 는 Traefik 을 설치하지 않는다**. 클러스터 운영자에게(RKE2 는 `rke2-traefik`), 또는 values `INGRESS_CLASS`·`TRAEFIK_ENTRYPOINT` 를 그 Traefik 의 이름으로 | §2g-2·§2g-3 |
| 413 `request_too_large`(포탈 「요청 본문이 서버 상한(기본 20 MiB)을 넘습니다 — …」) | 요청 본문 > `DMS_MAX_REQUEST_BODY_BYTES`(기본 20MiB) — 클러스터 안 에이전트 요청에도 걸린다. 사용자 요청이면 배치를 나눠 제출, 에이전트 요청(api 로그의 413)이면 키를 올린다 | §2g-2·§3-4 (b) |
| 잡이 오래 Pending(미리보기·실행 vcjob 이 시작하지 않음) | 포탈 운영 → 대시보드 「큐 현황」: 큐 `dms-data` 배지(Closed = Volcano 쪽에서 닫혔다 — 클러스터 운영자)와 「지금 대기 중」의 PodGroup 사유(`queue_reader`). 클러스터에서: `kubectl --context <ctx> get queue dms-data -o jsonpath='{.status.state}'`(Open 이어야 한다), `kubectl --context <ctx> -n dms get vcjob,podgroup \| grep <job id 앞 12자>` → `kubectl --context <ctx> -n dms describe vcjob <이름>`(이벤트의 Unschedulable 사유). 흔한 원인: 노드 CPU 상한(`procs_per_node`), 다른 잡이 코어를 차지함, 후보 노드 cordon·배치 제외, Volcano 스케줄러 장애. **DMS 의 시한(미리보기·실행·점검 600초)은 파드가 노드에 붙은 뒤부터 센다**(`execution_manifests._apply_task_deadlines`, `stepper.PREFLIGHT_DEADLINE_SECONDS`) — 노드 CPU 상한이 제출 크기보다 작다고 알려진 경우만 `node_cpu_insufficient` 로 끝내고(`stepper._raise_if_cpu_short`), 그 밖의 이유로 끝내 배치되지 않는 잡은 스스로 끝나지 않는다 — 원인을 풀거나 요청 상세에서 취소한다 | §6-4·§14-3 |
| `node_cpu_insufficient` | 정책의 `procs_per_node` 를 노드 CPU 상한 이하로 | §6-4 |
| `no_ready_sync_candidate` | 에이전트 보고가 오래됐거나 그 스토리지 마운트가 없다. 재부팅 직후면 1~2분 뒤 | 14-3 |
| 제출 403·409·422 | 권한 경로별 사유 | §10-1 |
| 제출 503 `maintenance_mode` | 컨트롤 상태 유지보수가 켜져 있다 | §6-3 |
| 잡이 전혀 진행하지 않는다 | 컨트롤 상태 drain 이 켜져 있다(stepper 정지) | §6-3 |
| 빌드 실패 `build_registry_unreachable`·`build_node_no_egress`·`build_source_unavailable`·`build_node_disk_low`·`build_proxy_*` | 빌드 프리플라이트 | §8 3b) |
| 컨트롤 상태 저장 422 `unknown_build_node` | 빌드 노드는 에이전트가 보고한 워커여야 한다 | §6-3 |
| 인증 메일 실패(`verification_email_failed`·`relay_*`) | 릴레이 주소·키·허용 IP·도메인 | §11 |
| 메일 설정 「읽을 수 없습니다」·`relay_misconfigured` | `DMS_SESSION_SECRET` 이 바뀌었다 — 키 다시 입력 | §3b-4 |
| k8s 연동 「클러스터」 탭 카드 「실패 · … 토큰을 거부했습니다(401)」·인증서 검증 실패 | 리더 토큰·CA·서버 이름 | §6-5 |
| 로그인 429 `login_rate_limited`·503 `login_busy` | 로그인 보호 장치 | §10-7 |
| 401 `session_revoked` | 그 계정의 비밀번호가 바뀌었다 — 다시 로그인 | §10-5 |
| 작업 삭제 뒤 정리가 멈춤 | 정리 상태의 `last_error` | §12 |
| `/readyz` 503, DB 연결 오류 | `kubectl --context <ctx> -n dms get cluster dms-pg`, `python3 deploy/pg-secrets.py --context <ctx> check` | §2f·§2f-7 |
| 릴리스 화면 드리프트·보안 설정 드리프트 | 매니페스트가 라이브와 다르다 | §9-8, `--only S11` |
| 잡이 실패 — 원인 | 요청 상세 「실행 로그」(launcher 로그)의 `DMS_EXEC_REASON=<코드>` 줄이 사유, 그 아래 `DMS_JR_TOOL_OUTPUT` 의 `  \| ` 줄이 도구 출력 발췌(stdout 끝 80·stderr 끝 20줄, 합계 10 KiB). 전체는 stdout.log·stderr.log(결과 파일 만료 전), 결과 화면 「실패 항목」은 표본(최대 10개) | §12b |
| rm 이나 sync 가 계획 단계에서 Conflict `path_conflict` | 같은 스토리지에서 경로가 같거나 조상·자손인 작업이 먼저 있다(rm 경로 겹침 잠금). 이미 계획된 잡은 실행 직전 **보류**(이벤트 `path_overlap_hold` → `path_overlap_released`) — 실패가 아니다 | 겹친 작업이 끝난 뒤 다시 제출 |
| sync 실패 `sync_type_change_refused` | 목적지의 비어 있지 않은 디렉터리를 파일·링크로 바꾸는 sync — 「삭제」를 켜고 다시 | — |
| `rm_items_failed`·`sync_items_failed` | 일부 항목을 못 지웠다(전에는 Succeeded 로 보이던 경우) | 결과 화면 「실패 항목」 |
| `destination_full`·`destination_read_only`·`sync_stopped_max_errors` | 목적지 공간·쿼터·읽기 전용·실패 65,000건 — 원인을 고치고 다시 실행하면 이어서 맞춘다 | — |
| `source_path_redirected`·`destination_path_redirected` | 관리 디렉터리 아래 경로의 어느 단계가 심링크(직접 경로 sync 도 봉쇄) — 실제 경로로 다시 | — |
| 422 `direct_bufsize_misaligned` | direct(O_DIRECT)를 켜면 bufsize 는 4096·2^k ≤ 4 MiB | — |

### 14-5. 에이전트 OS 지표 메모

에이전트의 노드 지표(`agent/probes.py probe_os_metrics`)는 매니페스트가 주는 두 경로에 기댄다(`deploy/k8s/50-agent-daemonset.yaml`):
- **네트워크 수치는 호스트 netns 의 것**이다. 호스트의 `/proc/1/net/dev` 를 hostPath(`type: File`)로 `/host/proc/1/net/dev` 에 붙이고
  `DMS_AGENT_NET_DEV_PATH` 로 가리킨다. `/host/proc` 디렉터리를 붙이는 것으로는 안 된다 — `/proc/net/*` 는 읽는 쪽의 netns 를 보여 준다.
  **`hostNetwork: true` 로 바꾸지 않는다** — `dnsPolicy: ClusterFirstWithHostNet` 없이 켜면 에이전트가 클러스터 DNS 를 잃어 `dms-api` 를 못 찾고, 보고 루프가
  fail-soft 라 **조용히 영영** 멈춘다.
- **물리 인터페이스만 센다.** 커널은 가상 인터페이스를 `/sys/devices/virtual/net/<이름>` 아래에 등록한다 — 호스트의 그 디렉터리를 읽기 전용 hostPath 로
  `/host/sys/devices/virtual/net` 에 붙이고 `DMS_AGENT_VIRTUAL_NET_PATH` 로 가리킨다. 이 값을 컨테이너 안의 `/sys/devices/virtual/net` 으로 **바꾸지 않는다** —
  그것은 파드 netns 의 가상 인터페이스라, 이름이 겹치는 호스트 인터페이스(예: `eth0`)가 가상으로 오판돼 빠진다. 값이 없거나 읽을 수 없으면 거르지 않고
  `lo` 만 뺀 합을 낸다.

## 부록 Z. 옛 절 번호 대응 (2026-11-06 재편)

2026-11-06 에 이 문서를 프로덕션 기준 런북으로 다시 짰다. 다른 파일·설치기 출력·다른 저장소가 가리키는 번호는 될 수 있으면 그대로 두었다.
주제가 같은 옛 번호는 프로덕션판으로 다시 썼고(§0·§3·§3b·§4·§5·§6·§7·§10), 주제가 다른 옛 번호는 **비워 두었다**(§2c·§2d-5·§2e-6·§2f-8·§2g-7·
§12c·§12d·§13) — 옛 참조가 엉뚱한 내용에 조용히 떨어지지 않게. 옛 본문은 `git show 5bec2e69:deploy/README.md` 로 본다.

| 옛 번호·제목 | 지금 |
|---|---|
| 머리말 「테스트베드는 둘이다」·두 테스트베드 비교 표·「Testbed facts」 표 | `deploy/testbed/README.md` §1·§2(ssc 테스트베드는 dms-ssc 체크아웃의 몫) |
| 「이미지 빌드 전 게이트: 로컬 풀스택 e2e」(옛 맨 앞) | §8 의 「이미지 빌드 전 게이트」 |
| §0 Registry setup(ssc `registry-setup.sh`) | 지웠다(ssc 전용). k8s 테스트베드의 노드 신뢰는 testbed IaC 의 crio 롤. 새 §0 = 프로덕션 배포 한눈에 |
| §1 Build and push images | §1(인터넷 노드 기준으로 일반화). 옛 「레지스트리 경로 접두 불가」는 §2e-1 |
| §2 Mount CephFS on the worker nodes | §2(스토리지 마운트 전제). 테스트베드 CephFS 플레이북·레이아웃은 `deploy/testbed/README.md` §4 |
| §2c 보조 그룹(LDAP gidNumber) | 지웠다(LDAP 코드 삭제 — CHANGELOG 「LDAP 코드 삭제·정리」) |
| §2d-5 / §2e-6 / §2f-8 / §2g-7(k8s 테스트베드의 배치·전환·CNPG 실증·입구) | `deploy/testbed/README.md` §3·§7. 일회성 기록은 CHANGELOG(2026-11-03 ③~⑥·테스트베드 정식 구축) |
| §3 Apply manifests, in order | §3(적용 규칙 — 적용은 설치기 하나, base raw apply 금지) |
| §3b Create the secret | §3b(시크릿·인증서·토큰 — 시크릿은 설치기 S2 가 만든다) |
| §4 Migrate / §5 Bring up(테스트베드 오버레이) | §4(업그레이드 — S11 이 migrate → rollout 을 한다). §5 는 이제 롤백·복구 |
| §6 Seed storages and policies | §6-4(일반). 테스트베드 시드 값은 `deploy/testbed/README.md` §5 |
| §7 Scenarios | §7(스모크 시험). 테스트베드판 시나리오는 `deploy/testbed/README.md` §8 |
| §8-4(만든 태그를 손으로 매니페스트에) | §8 4)(폐기 — 배포는 §9 포탈 릴리스) |
| §9-4(「롤아웃 뒤 매니페스트 `image:` 를 손으로 맞춘다」) | §9-4(다시 씀 — values 태그 `live`) |
| §9-5(「잡 이미지는 롤아웃 대상 아님」) | §9-5(다시 씀 — 잡 이미지는 릴리스의 job-image 행) |
| §9 「실증 체크리스트(설계 §11)」 | 지웠다 — 슬라이스 13 설계 `docs/history/specs/2026-08-06-dms-portal-rollout-slice13-design.md` §11 |
| 「프로덕션 배포 — kustomize 오버레이」(번호 없음) | §0·§3 |
| §10 포탈 HTTPS 노출 — nginx ingress 1~6 | 지웠다(ssc 테스트베드의 옛 입구 — 옛 본문은 위 `git show`). k8s 테스트베드의 되돌림 경로(Ingress·MetalLB)는 `deploy/testbed/README.md` §7. 새 §10 = 계정·권한·인증. 프로덕션 입구는 §2g |
| §10-7 웹 인증 하드닝 | §10-7(같은 내용) |
| §12c(2026-10-26 결정 묶음) / §12d(2026-10-28 dms-ssc 이식) | CHANGELOG 「BACKLOG 결정 묶음 구현」 / 「dms-ssc 이식」. 운영자가 알 사실은 §10-1(직접 경로 결과 소유)·§14-1(지표 API 키)·§14-4(실패 원인 보기·rm 경로 겹침 잠금·sync 실패 사유) |
| §13 머리(k8s 테스트베드 소개)·권한 표 | 권한 표 → §10-1, 기동 거부 규칙 → §10-2, 나머지 → `deploy/testbed/README.md` §1 |
| §13-1~§13-8, §13-6b(k8s 테스트베드 인프라·레지스트리 시드·DB·시크릿·오버레이 적용·입구·첫 관리자·빌드·릴리스·태그 bump) | `deploy/testbed/README.md` §1~§6(번호 대응표는 그 문서 §11) |
| §13-9 k8s 연동 등록 | **§6-5**(일반 런북). 테스트베드 등록 값은 `deploy/testbed/README.md` §5 |
| 「Unresolved values to fill in during live validation」 | 특권 요청자 → §10-2, 에이전트 OS 지표 → §14-5, 나머지(LDAP·옛 태그·옛 hostPath 메모)는 지웠다 |

**2026-11-16 입구 교체**(사용자 결정 — Envoy Gateway 입구 폐기, 입구 = 클러스터에 이미 깔린 Traefik): §2g 를 다시 썼다. 번호 §2g 는 그대로이고(제목만
「입구(Traefik)」 — 설치기 출력이 가리키는 앵커는 `deploy/README §2g`), 하위 절은 주제가 같은 번호를 다시 썼다(2g-1·2g-3~2g-6). 주제가 없어진 내용은
「폐기」로 적었다. §2g-7 은 여전히 비워 두고, 새 절은 §2g-8 이다. 옛 본문은 `git show b9b3f411:deploy/README.md`(교체 직전 판)로 본다.

| 옛 번호·제목(Envoy Gateway 판, 2026-11-03 ⑥ ~ 11-14) | 지금 |
|---|---|
| §2g 「입구(Envoy Gateway) — 마스터 sysctl(S0-5)·S8 설치·Avi VS-443·PROXY 컷오버·검증」 머리·구성 그림 | §2g 「입구(Traefik) — 클러스터의 Traefik·LB VS-443·PROXY v2·S8·검증·운영」 머리·구성 그림(같은 번호 — 다시 씀) |
| §2g-1 구성·값(Gateway API CRD·Envoy Gateway·`platform/gateway`·버전·digest, values `GATEWAY_PROXY_PROTOCOL`·`GATEWAY_LISTEN_PORT`·`GATEWAY_ENVOY_*`) | §2g-1(다시 씀 — Ingress 하나·`PORTAL_DOMAIN`·`PORTAL_TLS_SECRET`·`INGRESS_CLASS`·`TRAEFIK_ENTRYPOINT`). Gateway API·EG 층·버전 핀·lock 줄·`GATEWAY_*` 키는 폐기 — Envoy 입구 폐기 2026-11-16(키가 남아 있으면 렌더가 note 뒤 무시한다) |
| §2g-2 마스터 sysctl(S0-5 — `90-dms-envoy.conf`·빈 포트 :443/:19000~19003·hot restart·마스터 방화벽·클러스터 도메인 `cluster.local`) | 폐기 — Envoy 입구 폐기 2026-11-16. 이미 둔 sysctl·마스터 방화벽 규칙의 은퇴는 §2g-8 정리 7. 같은 번호는 이제 「클러스터의 Traefik — 사이트 선결·보장하는 것·앱이 대신 하는 것」 |
| S0-5 의 마스터 sysctl(옛 §0-2·§0-5 1e 가 §2g-2 를 가리키던 줄) | 폐기 — Envoy 입구 폐기 2026-11-16(은퇴는 §2g-8 정리 7). S0-5 의 레지스트리 이미지 반입·고정은 그대로 §2e-2·§2e-4 2) |
| §2g-3 설치(S8, A 경로 — Gateway API → EG → 대기 → `platform/gateway`, C 경로 확인 항목, 증상 표) | §2g-3(다시 씀 — 감지 `traefik`·`ingress` → TLS 게이트 → SSA `platform/ingress` → `deploy/ingress-probe.sh`). 증상 표 → §2g-6 「문제 해결」. EG 의 A·C 경로·certgen·Programmed·마스터 LISTEN 확인은 폐기 |
| §2g-4 Avi VS-443·PROXY 컷오버(optional → required)·`off`·`GATEWAY_LISTEN_PORT`·마스터 hostPort 충돌 | LB 표 → §2g-4(풀 = Traefik 이 도는 노드 전부 :443, PROXY v2 를 켜고 끄는 순서, PROXY 를 못 붙이는 LB). 컷오버(optional → required)·`off` 모드·리스너 포트·마스터 hostPort 충돌 확인은 폐기 — Envoy 입구 폐기 2026-11-16(Traefik 에는 PROXY 「필수」가 없다 — §2g-2) |
| §2g-5 검증(S12)·원 IP(Envoy 접근 로그·UC 수·15초+ 검사·「80 불가 = 정상」) | §2g-5(다시 씀 — Ingress·ingress-probe·VIP 경유 readyz·80 판정·앱 보안 이벤트 `client_ip`/`peer`). Envoy 접근 로그·UC 수는 폐기. 「80 불가 = 정상」 FAIL 은 80 판정(404 = 정상 INFO, 200 = 평문 노출 WARN)으로 바뀌었다 |
| §2g-6 업그레이드·운영(EG 업그레이드·EG 설정·인증서·롤링 공백·마스터 메모리 예산·제거 순서·GatewayClass finalizer) | 운영 → §2g-6(Traefik 은 사이트 몫·롤링 공백·보안 권고·인증서·문제 해결). EG 업그레이드·마스터 메모리 예산은 폐기. 옛 제거 순서·finalizer → §2g-8 「Envoy 정리 런북」 |
| `deploy/host-net-probe.sh`(S8 의 마스터 sysctl·LISTEN 프로브) | 폐기 — 노드 경유 확인은 `deploy/ingress-probe.sh`(§2g-3) |

- 옛 번호를 가리키는 다른 저장소(testbed IaC 의 README·docs/ARCHITECTURE·group_vars)는 위 표들로 찾아온다. 그쪽 참조를 고치는 일은 BACKLOG 에 올렸다.
- `docs/CHANGELOG.md`·`docs/history/` 의 옛 번호는 당시 기록이라 고치지 않는다 — 위 표들로 읽는다.
