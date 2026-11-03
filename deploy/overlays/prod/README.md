# DMS 프로덕션 설치 (한 화면 체크리스트)

테스트베드 매니페스트(`deploy/k8s/*`)를 base 로 두고 **사이트별 값만** 여기서 덮는다. 설치는 **단계 설치기** `deploy/install.sh` 하나다
(2026-11-03 ⑦, 설계 v6 8-1·8-2): S1 점검 → S2 시크릿 → S2b SELinux 프로브 → S3 계획 → S4 플랫폼·레지스트리 → (인터넷 노드 push) → S5 대조 →
S6 pull 프로브 → S7 공용 스토리지 프로브 → S8 입구 → S9 CNPG → S9b Volcano → S10 DB → S11 DMS → S12 검증 → S13 사후. 실패는 **한 줄 사유**로
멈추고(파드 로그를 뒤질 일이 없다), 같은 명령을 다시 돌리면 끝난 단계는 no-op·검증이다. 단계 표·종료 코드는 `deploy/README.md` §2h.

**dms-k8s 브랜치: k8s 신원 모드 전용(2026-10-26 LDAP 코드 삭제).** base 에 LDAP 키·nslcd 가 없고 에이전트는 `dms agent`
로 뜬다(예전의 LDAP null 패치·`patch-agent.yaml` 은 지웠다). 모든 단건 잡이 root 이고 권한은 대상 클러스터의 RoleBinding 맵핑
(`<id>-clusterrole-admin|edit` → 그 namespace 의 모든 PVC)으로 판정한다. 사용자 연산은 sync·scan·rm.
대상 클러스터(API 서버·읽기 전용 토큰)·호출 노드·StorageClass 매핑은 설치 뒤 포탈 「k8s 연동」에서 등록한다
(`deploy/README.md` 런북). 렌더 계약은 `tests/test_prod_k8s_overlay.py`.

**입구는 Envoy Gateway 다(2026-11-03 ⑥, 설계 v6 #13).** prod 는 MetalLB·ingress-nginx 를 쓰지 않는다 — 이 오버레이가 공용 base 의
Ingress·MetalLB 풀을 지우고(`patch-remove-ingress.yaml`), 포탈은 Avi VS-443(L4, PROXY v2) → 마스터 ×3 :443 의 Envoy 로 들어온다(S8,
`deploy/README.md` §2g). 설치기·preflight 에 옛 MetalLB·IngressClass 게이트는 없다(⑦).

**`--context <ctx>` 는 모든 명령에 필수다** — `install.sh` 는 `--context` 플래그만 받는다(env `KUBE_CONTEXT` 도 받지 않는다). `preflight-cluster.sh`·
`verify.sh`·python 도우미는 `--context` 또는 env `KUBE_CONTEXT`(설치기가 export 한다), `host-net-probe.sh` 는 플래그만 — 어느 것도 없으면 exit 2. current-context 는
어느 스크립트도 읽지 않는다(2026-11-03 ⑦ 리뷰 2 — 문구 정정).

## 0. 사이트 준비(S0 — 노드 IaC, 설치 전에 한 번)

0. **레지스트리 주소 고르기·확인(in-cluster — 4·5 보다 먼저)**: `REGISTRY_CLUSTER_IP` 는 4(전 노드 drop-in)·5(마스터 반입 이름·고정 패턴)에
   박히므로 박기 전에 고른다 — 렌더 없이 env 만으로 S1 의 주소 검사를 돌려 §5 가 PASS 인 주소로:
   `REGISTRY_MODE=in-cluster REGISTRY_CLUSTER_IP=<IP> sh deploy/preflight-cluster.sh --context <ctx>`(ServiceCIDR 안·미사용·하단 정적 대역 —
   예: /12 대역의 `x.x.0.10` 은 kube-dns 기본 주소라 FAIL). **한 번 정하면 바꾸지 않는다**(아래 「레지스트리」, 2026-11-03 ⑦ 리뷰 1).
1. **공용 스토리지**: 모든 마스터·워커의 같은 경로에 `/etc/fstab` 으로 마운트하고 `SHARED_DIR` 를 그 안(마운트포인트 또는 하위)에 둔다(설계 3-2 —
   아래 「`SHARED_DIR`」). `SHARED_DIR` 자체는 사이트가 만든다.
2. **SELinux(필수 전제 — 설계 2-3·#48)**: RHEL 계열(RHEL·Rocky 등 EL) **DMS 노드**(마스터 + NoSchedule taint 없는 워커 = dms-agent 가 뜨는 노드)는
   SELinux 가 **enforcing 이 아니어야** 한다 — permissive 또는 disabled, **재부팅 뒤에도**. 권장: `/etc/selinux/config` 의 `SELINUX=permissive` 로
   고정하고 `setenforce 0`. 완전 비활성이 필요하면 `grubby --update-kernel ALL --args selinux=0` 후 재부팅(Red Hat 은 프로덕션에서 `selinux=0` 을 권하지
   않고, disabled 에서 되돌리면 전체 relabel). `setenforce 0` 만 하면 재부팅 때 되돌아간다 — 설치기 S2b 가 이것도 잡는다. Ubuntu 처럼 SELinux 가
   없는 노드는 그대로 통과. DMS 는 SELinux 라벨 작업(`semanage`·`restorecon`)을 하지 않는다 — enforcing 이면 hostPath·`/proc/1` 접근이 EACCES 로
   막혀 DMS 사유가 엉뚱하게 나온다(S12 가 api·controller 로그의 EACCES 를 보면 S2b 를 다시 돌리라고 안내한다). S2b 프로브는 모든 노드에 이미
   있는 셸 포함 이미지를 쓴다(pull 없음) — 기본은 Cilium agent 이미지를 찾고, Cilium 이 없는 클러스터면 env `SELINUX_PROBE_IMAGE=<ref>`
   (예: 그 CNI 의 agent 이미지)를 `install.sh` 에 준다.
3. **S0 초기화**: 한 노드에서 root 로 `sh deploy/shared-dir-init.sh <SHARED_DIR>` — 마운트를 확인(포함 마운트가 `/` 면 거부)한 뒤 트리·센티널을
   만든다(이미 있으면 검사만). 출력의 `dms/.dms-shared-id = …` 가 values.env 의 `SHARED_DIR_ID`.
4. **레지스트리 신뢰(전 노드)**: `/etc/containers/registries.conf.d/50-dms-registry.conf` 에 `[[registry]] location="<IP>:<PORT>" insecure=true` →
   `systemctl reload crio`. 서명 정책이 reject 기본이면 허용 항목, 런타임 프록시 env 가 있으면 `NO_PROXY` 에 `<IP>`(설계 7-4).
5. **마스터 3대**: 레지스트리 자기 이미지 반입·고정(`/etc/crio/crio.conf.d/99-dms-pinned.conf` — 아래 「이미지 반입」 2)), Envoy sysctl
   `/etc/sysctl.d/90-dms-envoy.conf` = `net.ipv4.ip_unprivileged_port_start = 443`(`deploy/README.md` §2g-2), kubelet 예약(설계 2-2 권고 —
   `systemReserved: cpu=500m,memory=1Gi` · `kubeReserved: cpu=500m,memory=1Gi` · `evictionHard: memory.available<500Mi` → allocatable 약
   7000m/29.5Gi)·`shutdownGracePeriod`(선택 — 값 미정, 클러스터 운영자 K7).
6. **도구**: 설치 호스트 — kubectl(클러스터 minor ±1)·curl·python3(RHEL 9 = 3.9). **마스터 ×3** — skopeo·crictl(5 의 반입: oci-archive →
   containers-storage, airgap RHEL 이면 사내 저장소에서 미리 설치). **인터넷 노드**(외부 인터넷이 되는 클러스터 노드 1대) — skopeo·podman·
   python3, 같은 커밋의 DMS 저장소.
7. **Avi VS-443(설치 전에 만든다)**: 443 전용 L4 프로필 PROXY v2 · 풀 = 마스터 ×3 :443 · TCP 헬스 · 유휴 3600초 · SE 대역 → 마스터 :443 방화벽
   (`deploy/README.md` §2g-4 표). 풀 멤버는 S8(Envoy) 전까지 down 이 정상이다. 아직 못 열었어도 설치는 된다 — S12 는 앱·DB 를 apiserver
   서비스 프록시로 판정하고 VIP 경유 readyz 무응답은 WARN 으로만 알린다(2026-11-03 ⑦ 리뷰 1).

## 1~6. 설치

```sh
# 1) 값 — 파일 하나(비밀 아님). REPLACE_* 를 실값으로(아래 「values.env 값」)
cp deploy/overlays/prod/values.env.example deploy/overlays/prod/values.env
$EDITOR deploy/overlays/prod/values.env

# 2) 계획 — 변경 없음: S1 preflight(K8s 1.34~1.36·SHARED_DIR 형식·REGISTRY_CLUSTER_IP 의 ServiceCIDR·미사용·하단 정적 대역·PSA·admission·
#    애드온 감지 표·노드 셸 체크리스트) + S2 확인 + S3 층별 server dry-run. FAIL·P·X 면 그 사유부터 해소한다(P 는 --adopt <x> 로만 채택).
sh deploy/install.sh --context <ctx> --plan
#    RESULT WARN 이면 위 [WARN] 줄(preflight·감지)을 읽는다 — 정책 엔진·cluster.local 아님·하단 정적 대역 밖 같은 뒤 단계 실패의 예고다.
#    계획이 FAIL·모름이면 끝 줄이 「계획부터 다시: … --plan」이다(실제 설치 명령은 FAIL·모름 없는 계획 뒤에만 보인다 — 같은 플래그·env 로).
#    노드 셸 체크리스트([MANUAL] 줄 — getenforce·crio config 의 pinned_images·signature_policy·findmnt -T <SHARED_DIR>·런타임 NO_PROXY·sysctl)를
#    노드에서 돌려 확인한다.

# 3) 설치 — S1 → S2(ns·dms-pg-app·dms-secrets 를 만들고 값을 출력하지 않는다) → S2b → S3 → S4(PriorityClass·레지스트리) 에서 멈춘다
#    (RESULT: PAUSE, exit 10 — 레지스트리가 비어 있다). TLS 시크릿이 아직 없으면 S2 가 WARN 으로 알리고 **S8 이 멈춘다** — 그 전에 만든다
#    (사내 CA 발급을 기다리는 동안에도 S2b~S7 은 먼저 끝내 둘 수 있다):
#      kubectl --context <ctx> -n dms create secret tls dms-portal-tls --cert=tls.crt --key=tls.key   # SAN: PORTAL_DOMAIN + PORTAL_VIP(VIP-A)
sh deploy/install.sh --context <ctx>

# 4) 인터넷 노드 — install.sh 가 출력한 push 명령(아래 「이미지 반입」 3)) + DMS 3종 최초 빌드·push, 그 뒤 설치 호스트에서 재개 — PAUSE 가 출력한
#    재개 줄을 그대로 쓴다(--upgrade-addon·--adopt·env 를 싣는다). S12 의 VIP 경유 readyz 는 설치 호스트가 사내 CA 를 믿지 않으면 CACERT 가 필요하다:
CACERT=<사내CA> sh deploy/install.sh --context <ctx> --stage S5     # S5 대조 → S6 → … → S12 → S13(설치 호스트가 사내 CA 를 믿으면 CACERT 생략)

# 5) 첫 관리자(계정 0개인 새 사이트 — 포탈 「k8s 연동」·「컨트롤 상태」 등록 전에): S13 체크리스트의 명령 그대로. 이름은 values.env 의 LOCAL_ADMIN
#    (특권 목록 — root 자격·전체 PV 권한). x-admin-token 부트스트랩만 평문 비밀번호를 받는다. 토큰은 0600 파일로만 curl 에(출력·argv 없음),
#    비밀번호는 셸 내장 printf 로 stdin 에. port-forward 라 Avi·DNS 전에도 된다:
#      kubectl --context <ctx> -n dms port-forward svc/dms-api 18080:http        # 다른 터미널
umask 077; H=$(mktemp)
kubectl --context <ctx> -n dms get secret dms-secrets -o jsonpath='{.data.DMS_ADMIN_TOKEN}' | base64 -d | sed 's/^/x-admin-token: /' > "$H"
stty -echo; printf 'password: '; read -r PW; stty echo; echo               # 비밀번호에 " 와 \ 는 쓰지 않는다(JSON)
printf '{"username":"%s","password":"%s"}' '<LOCAL_ADMIN>' "$PW" \
  | curl --noproxy '*' -sS -H @"$H" -H 'content-type: application/json' --data-binary @- http://127.0.0.1:18080/api/admin/accounts
rm -f "$H"; unset PW                                                        # 201 → 포탈 로그인(이후 자동화는 봉인 로그인 — deploy/README.md §10-7)

# 6) 다시 검증만(언제든) — 읽기 전용으로는 VERIFY_NO_PROBES=1
PORTAL_DOMAIN=<FQDN> PORTAL_VIP=<VIP-A> CACERT=<사내CA> sh deploy/install.sh --context <ctx> --only S12
```

- **멈춤과 재개**: 어느 단계든 실패하면 고친 뒤 `--stage <그 단계>` 로 이어 간다 — 멈춤 줄이 보이는 재개 명령은 이번 실행의 `--upgrade-addon`·
  `--adopt`·판정 env(S12 의 `CACERT`·`PORTAL_DOMAIN`·`PORTAL_VIP`·`VERIFY_NO_PROBES` 포함)를 그대로 싣는다(빼면 업그레이드가 조용히 빠진다).
  모름(exit 4 — 프로브가 노드를 재지 못함 등)은 실패와 섞지 않는다. `--plan` 의 재확인 줄은 늘 `--plan` 이다. `sh deploy/install.sh --help` 가
  env·`--upgrade-addon` 이름·종료 코드를 보인다.
- **이미 있는 애드온**: Gateway API·EG·CNPG·Volcano 가 이미 있으면 감지가 C(호환 — 재사용, **다시 설치하지 않는다**)·C!(EOL·범위 밖 — WARN +
  소유자 요청)·X(비호환 — FAIL)로 가른다. `ADDON_INSTALL=never` 면 없는 애드온(A)을 설치하지 않고 FAIL + 「클러스터 운영자에게 요청」.
  **Volcano 는 아직 DMS 가 설치하지 않는다**(층 미동봉 — 없으면 S1 이 멈춘다; ≥ v1.15, K8s 1.36 이면 v1.16 을 클러스터 운영자에게 요청).
- **업그레이드**: `--upgrade-addon cnpg`(minor 하나씩 — 직전 덤프 → SSA → rollout → healthy 대기), `--upgrade-addon envoy-gateway` 등
  (`deploy/README.md` §2f-6·§2g-6), `--upgrade-addon registry`(마스터 pull·고정 목록 먼저 — §2e-5). 이미지 교체를 막는 가드(exit 3)는 이 플래그일
  때만 그 층에서 열린다(전역 `ALLOW_IMAGE_CHANGE=1` 은 S11 의 앱 가드까지 열므로 애드온 업그레이드에 쓰지 않는다).
- **external 레지스트리**(`REGISTRY_MODE=external`, `REGISTRY_CLUSTER_IP` 없음): S4 는 PriorityClass 만, S5 는 사이트 `REGISTRY` 를 직접 대조하고
  push 명령도 그 주소로 보인다(lock 전부 — 애드온 이미지도 그 레지스트리에서 받는다).

## 이미지 반입(인터넷 노드 — 설계 7-5, 상세 `deploy/README.md` §2e-4)

오프라인 번들은 없다. 인터넷 노드(외부 인터넷이 되는 클러스터 노드 — 설치 뒤 포탈 「컨트롤 상태」의 빌드 노드로 등록, S13)가 상류에서 받아
레지스트리 ClusterIP 로 **직접** push 한다. 프록시를 거치면 상류 pull 은 `HTTPS_PROXY` 를 타고, push 는 타지 않게 `NO_PROXY` 에 레지스트리 IP 를 넣는다.

1. **lock 확인**: `python3 deploy/images-lock.py check && python3 deploy/images-lock.py verify`(설치와 같은 커밋 — linux/amd64 매니페스트 digest 인지).
2. **레지스트리 자기 이미지(S0-5, 레지스트리가 뜨기 전 1회)**: `skopeo copy docker://docker.io/library/registry@<lock digest> oci-archive:/tmp/registry-<태그>.tar`
   → 마스터 3대로 옮겨 `skopeo copy oci-archive:… containers-storage:[…]<IP>:<PORT>/registry:<태그>` → `99-dms-pinned.conf` 의
   `pinned_images = ["<IP>:<PORT>/registry:*", <기존 목록>]` → `systemctl reload crio` → `crictl inspecti … | grep pinned` → 파일 삭제.
3. **lock push(S5)**: `install.sh` 가 S4 뒤 출력하는 `sh deploy/push-images.sh --registry <IP>:<PORT> …` 를 `--run` 으로(멱등 — 끊겼으면 다시).
4. **DMS 3종 최초 빌드**: `REGISTRY=<IP>:<PORT> TAG=<values.env 의 DMS_TAG> ./deploy/docker/build-and-push.sh`(S5 PAUSE 가 실제 값으로 보인다 —
   agent 는 같은 TAG 의 dms·dms-mpifileutils 위에서 빌드되므로 DMS_TAG·DMS_AGENT_TAG·MFU_TAG 를 같게. base 매니페스트는 고치지 않는다; 빌드 전
   e2e 게이트는 개발 PC 에서 같은 커밋으로) — 설치 뒤에는 포탈 빌드가 같은 노드를 쓴다. 그다음 설치 호스트에서 PAUSE 가 보인 재개 줄(`--stage S5`).

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
미사용·하단 정적 대역 — S1 preflight 가 본다, 2026-11-03 ⑦)·`REGISTRY_PORT`(5000)로 주소가 정해지고, `REGISTRY` 는 비우면 그 주소로 채워진다(채우면 같아야 한다) — 이
주소가 이미지 newName·`DMS_JOB_IMAGE`·`DMS_BUILD_REGISTRY`·`DMS_BUILD_BUILDER_IMAGE` 와 DB 의 잡 이미지에 박히고 노드 신뢰 drop-in·고정
이미지 이름에도 들어가므로 **한 번 정하면 바꾸지 않는다**. `SHARED_DIR_ID` 는 S0 가 출력한 `.dms-shared-id` 값이다 — 레지스트리 init 이
저장소의 센티널과 대조해 없거나 다르면 기동하지 않는다(마운트 빠진 마스터의 로컬 디스크에 쓰지 않게). `REGISTRY_TLS_VERIFY=false`
(in-cluster 는 평문 HTTP 만 — 포탈 태그 조회·삭제가 `http://` 고정. external TLS 레지스트리면 `true` 로 두면 인터넷 노드 push·설치기 S5 대조가
TLS 를 쓰지만 포탈 태그 조회·삭제·빌드 push 는 그 레지스트리에서 쓰지 못한다 — 2026-11-03 ⑦ 리뷰 2). `REGISTRY_MODE=external` 이면 `REGISTRY` 에 사이트 레지스트리 `host:port`(경로
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
동작 확인(교차 노드 가시성·root 소유 보존·숫자 uid/gid·POSIX·fsync — 설치기 S7 이 한다; dms 이미지가 레지스트리에 생기는 S5 뒤. 손으로는):
`python3 deploy/shared-storage-probe.py --context <ctx> --shared-dir <SHARED_DIR> --image <REGISTRY>/dms:<DMS_TAG>`
(`<REGISTRY>` 는 in-cluster 면 `deploy/.rendered/prod/render.env` 의 `REGISTRY=` — values 에서는 비어 있다.
종료 0 PASS · 3 WARN(G3 만, 설치 계속) · 1 FAIL(센티널 없음 = S0 미실행 포함) · 4 모름 · 2 사용법).

렌더러 `sh deploy/render.sh prod --context <ctx>`(태그가 `live` 면 그 클러스터에서 읽는다; 옛 `sh deploy/overlays/prod/render.sh` 는 같은 일의 호환 진입점)가 이 값을 오버레이 템플릿과 플랫폼 층에
넣어 `deploy/.rendered/prod/`(git 밖 — `app/`·`platform/priorityclass/`·`platform/registry/`·`addons/cnpg/`·`platform/db-*/`·`addons/gateway-api/`·
`addons/envoy-gateway/`·`platform/gateway/`·`render.env`)를 만들고, `install.sh` 가 `app/` 을
`kubectl apply -k` 한다(플랫폼 층은 SSA — `deploy/README.md` §2e). 렌더러는 in-cluster 면 다섯 곳의 레지스트리 주소가 같은지, 오버레이의
아티팩트 base 가 `SHARED_DIR` 에서 나온 값인지 다시 보고 어긋나면 FAIL 한다(실패하면 결과 디렉터리를 바꾸지 않는다). 오버레이 템플릿
자체(kustomization·patch-*)는 손대지 않는다. **`install.sh` 는 매 실행(`--plan`·`--only` 포함) 처음에 다시 렌더한다 — `deploy/.rendered/prod`
를 손으로 고친 값은 설치기로 적용되지 않는다**(편집은 조용히 사라지고 설치는 편집 없는 값으로 PASS 한다, 2026-11-03 ⑦ 리뷰 2). 필요한 값은
values.env 키로 넣는다 — 키가 없으면 docs/BACKLOG.md 「⑦ 설치기 후속」(예: 클러스터 도메인 ≠ cluster.local 의 EG `KUBERNETES_CLUSTER_DOMAIN`)이고,
그 전까지는 설치기 실행 뒤 손으로 적용한다(`deploy/README.md` §2d~§2g 의 손 명령 — 앱 층 값이면 다음 설치기 S11 apply 가 되돌리므로 키를 먼저).

## 선행조건 (설치기가 확인하는 것 — S0 사이트 몫과 설치기가 만드는 것)

| 항목 | 확인 |
|---|---|
| **Kubernetes** 1.34~1.36(설계 #4 — prod minor) / **CNI** 임의(Cilium KPR 없음 + kube-proxy 전제) | S1 preflight §0(밖이면 FAIL) |
| **SELinux**(RHEL 계열 DMS 노드) | enforcing 아님(재부팅 뒤에도) — S0 사이트 몫, S2b 프로브가 노드마다 재고 FAIL·모름이면 멈춘다(위 「0. 사이트 준비」 2) |
| **Volcano** (잡 gang 스케줄러) | ≥ v1.15(K8s 1.36 이면 v1.16) — **사이트가 설치**(DMS 층 미동봉, BACKLOG ⑦). S1 감지가 C 면 재사용, 없으면 S1 이 멈춘다. S9b 가 DMS 모양 vcjob 을 server dry-run(웹훅 8443) |
| **입구(Envoy Gateway)** | MetalLB·ingress-nginx 는 **필요 없다**(2026-11-03 ⑥ — 오버레이가 Ingress·풀을 지운다). Gateway API v1.6.x CRD + EG v1.9.x(없으면 설치기 S8 이 설치 — A, 있으면 재사용 — C), 마스터 sysctl `ip_unprivileged_port_start=443`(S0-5 — S8 이 마스터마다 다시 잰다), ns `envoy-gateway-system` PSA privileged, Avi VS-443(PROXY v2 → 마스터 :443) — `deploy/README.md` §2g. 확인: S8·S12 가 Gateway Programmed + 마스터마다 :443 LISTEN(`deploy/host-net-probe.sh` — Programmed 는 sysctl 빠진 마스터를 못 잡는다) |
| **PSA** | `dms` 네임스페이스 `enforce: privileged`(00-namespace.yaml) — 사내 정책이 허용해야 잡 파드가 뜸 |
| **PostgreSQL** | CloudNativePG Cluster `dms-pg`(설치기 S9·S10 — `deploy/README.md` §2f): 마스터마다 1개, 공용 디렉터리의 정적 hostPath PV ×5(S0 가 `dms/pgdata/dms-pgdata-01..05` 를 26:26 0700 으로 만든다), `dms-secrets` 의 URL 은 `dms-pg-rw`(설계 6-3). apiserver → 워커 파드 9443(CNPG 웹훅)이 열려 있어야 한다 |
| **공용 디렉터리** | 모든 마스터·워커의 같은 경로 `SHARED_DIR` 에 마운트 + S0 `sh deploy/shared-dir-init.sh <SHARED_DIR>`(한 노드, root — `dms` 0750·`dms/artifacts` 0755 등과 센티널), 각 스토리지 `managed_root` 사전 mkdir. 확인: 설치기 S7(`deploy/shared-storage-probe.py` — S5 뒤: 프로브 파드가 dms 이미지를 그 레지스트리에서 받는다) |
| **클러스터 내 레지스트리**(in-cluster) | 모든 노드 CRI-O 신뢰 `/etc/containers/registries.conf.d/50-dms-registry.conf`(S0-4) · 마스터 전부 레지스트리 이미지 반입 + `/etc/crio/crio.conf.d/99-dms-pinned.conf`(S0-5) · S4 적용 · S5 push·대조 · S6 pull — `deploy/README.md` §2e. 설치기는 레지스트리가 Ready·`/v2/` 200 이 아니면 S4 에서, lock 이미지가 lock digest 가 아니거나 dms·dms-agent·dms-mpifileutils 태그가 없으면 S5(·S11 apply 전)에서 멈춘다 |
세부(Avi VS-443 설정·PROXY 컷오버·원 IP·「80 불가 = 정상」·업그레이드)는 `deploy/README.md §2g`(옛 MetalLB·nginx 입구의 §10 은 테스트베드용).
api/controller 의 hostPath 는 `SHARED_DIR` 에서 렌더된다(base 는 자리표시자 — 손으로 패치하지 않는다).
**dms-api·dms-controller 는 마스터에 뜬다**(2026-11-03 ⑤, 설계 #24·#25 — `patch-placement.yaml`: control-plane nodeSelector + toleration,
not-ready·unreachable `tolerationSeconds: 30`). 이 오버레이가 제어면 Deployment 에서 바꾸는 것은 이미지·아티팩트 경로·이 배치 두 필드뿐이다
(`tests/test_release_manifest_contract.py`). 마스터에도 `SHARED_DIR` 마운트·레지스트리 신뢰가 있어야 한다(G1·S0-4). 마스터를 내릴 때의 규칙은
`deploy/README.md` §2f-7(VM 전원 확인 전 강제 삭제·out-of-service taint·Node 삭제 금지).
