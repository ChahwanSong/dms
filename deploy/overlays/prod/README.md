# DMS 프로덕션 설치 (한 화면 체크리스트)

사이트 중립 base(`deploy/k8s/*` — 이미지·아티팩트 경로는 자리표시자 `set-by-overlay.invalid`)에 **사이트별 값만** `values.env` 하나로 덮는다.
이 문서는 프로덕션 설치의 **값과 순서**만 적는다 — 단계마다 명령의 이유·성공 판정·실패 표는 `deploy/README.md` 의 그 절에 있다(이 문서에서
파일 이름 없이 쓴 § 도 모두 `deploy/README.md` 의 절이다 — 이 문서 자신의 항목은 「0. 사이트 준비」의 「0 의 N」처럼 쓴다). 자리표시자
(`<ctx>`·`<REGISTRY>`·`<SHARED_DIR>`·`<FQDN>`·`<VIP-A>`·`<LOCAL_ADMIN>`·`<DMS_TAG>`·`<사내CA>`)의 뜻은 `deploy/README.md` 머리 표, 전체 순서표
(어느 호스트·명령·성공 판정·상세 §)는 §0-5, 책임 경계(사이트 IaC·클러스터 운영자·설치기·포탈)는 §0-6. 테스트베드 값은 `deploy/testbed/README.md` 에만 있다.

설치는 **단계 설치기** `deploy/install.sh` 하나다(2026-11-03 ⑦, 설계 v6 8-1·8-2): S1 점검 → S2 시크릿 → S2b SELinux 프로브 → S3 계획 → S4 플랫폼·
레지스트리 → (인터넷 노드 push) → S5 대조 → S6 pull 프로브 → S7 공용 스토리지 프로브 → S8 입구 → S9 CNPG → S9b Volcano → S10 DB → S11 DMS →
S12 검증 → S13 사후. 실패는 **한 줄 사유**로 멈추고(파드 로그를 뒤질 일이 없다), 같은 명령을 다시 돌리면 끝난 단계는 no-op·검증이다. 끝 줄은
`RESULT: …`, 종료 코드는 0 완료(WARN 포함) · 1 FAIL · 2 사용법·환경 · 4 모름 · 10 PAUSE. 단계 표는 `deploy/README.md` §2h, env·플래그는
`sh deploy/install.sh --help`.

**k8s 신원 모드 전용(dms-k8s, 2026-10-26 LDAP 코드 삭제).** base 에 LDAP 키·nslcd 가 없고 에이전트는 `dms agent` 로 뜬다. 모든 단건 잡이
root 이고 권한은 대상 클러스터의 RoleBinding 맵핑(`<id>-clusterrole-admin|edit` → 그 namespace 의 모든 PVC)으로 판정한다. 일반 사용자 연산은
**sync·rm**(scan 은 특권 운영자만 — `patch-config.yaml` 의 `DMS_USER_ALLOWED_OPERATIONS`). 대상 클러스터(API 서버·읽기 전용 토큰)·호출 노드·
StorageClass 매핑은 설치 뒤 포탈 「k8s 연동」에서 등록한다(`deploy/README.md` §6-5, 권한 모델 §10-1). 렌더 계약은 `tests/test_prod_k8s_overlay.py`.

**입구는 Envoy Gateway 다(2026-11-03 ⑥, 설계 v6 #13).** prod 는 MetalLB·ingress-nginx 를 쓰지 않는다 — 이 오버레이가 공용 base 의
Ingress·MetalLB 풀을 지우고(`patch-remove-ingress.yaml`), 포탈은 Avi VS-443(L4, PROXY v2) → 마스터 ×3 :443 의 Envoy 로 들어온다(S8,
`deploy/README.md` §2g).

**`--context <ctx>` 는 모든 명령에 준다.** `install.sh` 는 `--context` 플래그만 받는다(env `KUBE_CONTEXT`·current-context 를 읽지 않는다).
`preflight-cluster.sh`·`verify.sh`·`pull-probe.py`·`shared-storage-probe.py`·`render.sh`(태그가 `live` 일 때)는 `--context` 또는 env
`KUBE_CONTEXT`(설치기가 export 한다), `host-net-probe.sh`·`pg-secrets.py`·`pg-path-probe.py` 는 `--context` 플래그만 — 없으면 exit 2. **예외: `deploy/overlays/guard-images.sh` 는 `--context`·`KUBE_CONTEXT` 가 둘 다 없으면
current-context 를 읽는다**(설치기는 늘 `--context` 로 부른다 — 손으로 부를 때도 준다). 같은 네임스페이스(`dms`)를 쓰는 클러스터가 여럿이면
컨텍스트가 유일한 구분이다. 선택 강화: `deploy/overlays/prod/kube-context`(첫 비주석 줄 = 컨텍스트 이름)를 두면 render 가 각 렌더 층에 복사하고
`install.sh`(exit 2)·guard(exit 4)가 다른 컨텍스트를 거부한다(`deploy/README.md` §3-2).

## 0. 사이트 준비(S0 — 노드 IaC, 설치 전에 한 번)

먼저 `deploy/README.md` §0 을 연다 — 설치 전에 정할 값(§0-1)·클러스터·노드·호스트 요건(§0-2: control-plane 3대 + NoSchedule 없는 워커 ≥ 2)·
방화벽·NTP·DNS(§0-3)·보안 승인 항목과 DMS 가 만드는 클러스터 범위 객체(§0-4). 아래 0~12 는 사이트가 노드에 하는 일이다(설치기는 하지 않는다 —
`--plan` 의 `[MANUAL]` 노드 셸 체크리스트와 설치기 S2b·S6·S7·S8 이 결과를 다시 잰다). **DMS 노드** = 마스터 + NoSchedule taint 없는 워커
(dms-agent·잡·포탈 빌드는 그 워커에만 뜬다 — 에이전트의 toleration 은 NoExecute 뿐이라 마스터에 뜨지 않는다).

0. **레지스트리 주소 고르기·확인(in-cluster — 4·5 보다 먼저, 설치 호스트)**: `REGISTRY_CLUSTER_IP` 는 4(전 노드 drop-in)·5(마스터 반입 이름·고정
   패턴)에 박히므로 박기 전에 고른다 — 렌더 없이 env 만으로 S1 의 주소 검사를 돌려, preflight 출력 「5. 공용 디렉터리 · 레지스트리 값」 절의
   주소 두 줄(`REGISTRY_CLUSTER_IP … 하단 정적 대역`·`… 미사용`)이 PASS 인 주소로:
   `REGISTRY_MODE=in-cluster REGISTRY_CLUSTER_IP=<IP> sh deploy/preflight-cluster.sh --context <ctx>`(ServiceCIDR 안·미사용·하단 정적 대역 —
   예: /12 대역의 `x.x.0.10` 은 kube-dns 기본 주소라 FAIL). **한 번 정하면 바꾸지 않는다**(아래 「레지스트리」, `deploy/README.md` §2e-1).
   external 사이트 레지스트리면 이 항목과 4·5 의 레지스트리 반입은 사이트 몫이다(아래 「external 레지스트리」).
1. **공용 스토리지(전 노드)**: 모든 마스터·워커의 같은 경로에 `/etc/fstab` 으로 마운트하고 `SHARED_DIR` 를 그 안(마운트포인트 또는 하위)에 둔다
   (설계 3-2 — 아래 「`SHARED_DIR`」, `deploy/README.md` §2d). `SHARED_DIR` 자체는 사이트가 만든다.
2. **SELinux(필수 전제 — 설계 2-3·#48, DMS 노드)**: RHEL 계열(RHEL·Rocky 등 EL)은 **enforcing 이 아니어야** 한다 — permissive 또는 disabled,
   **재부팅 뒤에도**. 권장: `/etc/selinux/config` 의 `SELINUX=permissive` 로 고정하고 `setenforce 0`(완전 비활성은 `grubby --update-kernel ALL
   --args selinux=0` 후 재부팅 — Red Hat 은 프로덕션에서 권하지 않고, disabled 에서 되돌리면 전체 relabel). `setenforce 0` 만 하면 재부팅 때
   되돌아간다 — 설치기 S2b 가 이것도 잡는다. Ubuntu 처럼 SELinux 가 없는 노드는 그대로 통과. DMS 는 라벨 작업을 하지 않는다 — enforcing 이면
   hostPath·`/proc/1` 접근이 EACCES 로 막혀 사유가 엉뚱하게 나온다. S2b 프로브는 모든 노드에 이미 있는 셸 포함 이미지를 쓴다(기본은 Cilium agent
   이미지 — 없는 클러스터면 env `SELINUX_PROBE_IMAGE=<ref>` 를 `install.sh` 에 준다; `deploy/README.md` §2h).
3. **S0 초기화(한 노드, root)**: `sh deploy/shared-dir-init.sh <SHARED_DIR>` — 마운트를 확인(포함 마운트가 `/` 면 거부)한 뒤 트리·센티널을
   만든다(이미 있으면 검사만). 성공 = 끝 줄 `RESULT: PASS`, 출력의 `dms/.dms-shared-id = …` 가 values.env 의 `SHARED_DIR_ID`. 다른 노드에서는
   `sh deploy/shared-dir-init.sh --check <SHARED_DIR>` 로 같은 트리가 보이는지(`deploy/README.md` §2d-2).
4. **레지스트리 신뢰(전 노드)**: `/etc/containers/registries.conf.d/50-dms-registry.conf` 에 `[[registry]] location="<IP>:<PORT>" insecure=true` →
   `systemctl reload crio`. 서명 정책이 reject 기본이면 허용 항목, 런타임 프록시 env 가 있으면 `NO_PROXY` 에 `<IP>`(설계 7-4, `deploy/README.md` §2e-2).
5. **마스터 3대**: 레지스트리 자기 이미지 반입·고정(`/etc/crio/crio.conf.d/99-dms-pinned.conf` — 아래 「이미지 반입」 2), `deploy/README.md` §2e-4),
   Envoy sysctl `/etc/sysctl.d/90-dms-envoy.conf` = `net.ipv4.ip_unprivileged_port_start = 443`(`deploy/README.md` §2g-2), `:443`·`:19000~19003` 이
   비어 있을 것, kubelet 예약은 사이트 값(설계 2-2 의 8 vCPU/32GB 가정 예: `systemReserved: cpu=500m,memory=1Gi` · `kubeReserved: cpu=500m,
   memory=1Gi` · `evictionHard: memory.available<500Mi` → allocatable 약 7000m/29.5Gi)·`shutdownGracePeriod`(선택 — 값 미정, 클러스터 운영자).
6. **도구**: 설치 호스트 — kubectl(클러스터 minor ±1)·curl·python3(≥ 3.9, 표준 라이브러리만)·sha256sum·POSIX sh, apiserver(6443)만 닿으면 된다
   (레지스트리 확인은 port-forward). **마스터 ×3** — skopeo·crictl(5 의 반입, airgap RHEL 이면 사내 저장소에서 미리 설치). **인터넷 노드**(외부
   인터넷이 되는 클러스터 노드 1대 = 설치 뒤 포탈 빌드 노드) — skopeo·podman·python3·bash, **NoSchedule taint 없는 워커**(빌드 파드에 toleration 이
   없다), privileged 파드 허용, 같은 커밋의 DMS 저장소 **로컬 체크아웃**(git worktree 금지 — 빌드 SHA 가 `unknown`), 프록시면 `HTTPS_PROXY` +
   `NO_PROXY=<레지스트리 IP>`(`deploy/README.md` §0-2·§8 0)·3)).
7. **Avi VS-443(설치 전에 만든다)**: 443 전용 L4 프로필 PROXY v2(6443 프로필과 공유 금지) · 풀 = 마스터 ×3 :443 · TCP 헬스 · 유휴 3600초 이상 ·
   SE 대역 → 마스터 :443 방화벽(`deploy/README.md` §2g-4 표). 풀 멤버는 S8(Envoy) 전까지 down 이 정상이다. 아직 못 열었어도 설치는 된다 — S12 는
   앱·DB 를 apiserver 서비스 프록시로 판정하고 VIP 경유 readyz 무응답은 WARN 으로만 알린다. **메일 게이트(5b) 전까지는 운영자 대역으로만**
   연다 — 리스너에 hostname 이 없고 인증서 SAN 에 VIP 가 있어 S8 뒤 `https://<VIP-A>` 가 DNS 없이도 열리고, 그 창의 stub 가입 계정은 k8s 연동
   뒤 그 아이디의 PV 권한을 얻는다(`deploy/README.md` §6-2).
   **이 표를 못 맞추는 사이트**(2026-11-14): LB 가 PROXY v2 를 못 붙이면 values.env `GATEWAY_PROXY_PROTOCOL=off`, 마스터의 호스트 443 을 다른 것이
   쓰면(예: RKE2 Traefik 의 hostPort 443 — S1 이 찾아 멈춘다) `GATEWAY_LISTEN_PORT=<포트>`(예 10443)로 하고 풀 멤버·헬스·방화벽 포트를 그
   포트로 맞춘다(`deploy/README.md` §2g-1·§2g-4). 1024 이상이면 5 의 Envoy sysctl(`90-dms-envoy.conf` — §2g-2)이 필요 없다.
8. **데이터·PV 스토리지(워커)**: DMS 가 다룰 스토리지·PV 백엔드를 NoSchedule 없는 워커에 **포탈에 등록할 경로 그대로** 마운트하고(NFS 계열은
   `lookupcache=positive`), 각 `managed_root` 를 미리 만든다. `<SHARED_DIR>/dms` 와 겹치는 경로는 등록되지 않는다(422 `storage_overlaps_dms_state`)
   (`deploy/README.md` §2). export 는 DMS 워커에 root 를 허용한다(root squash 없음 — 모든 잡이 root 다, `deploy/README.md` §2 「root 접근」).
9. **`fs.protected_hardlinks=1`·`fs.protected_symlinks=1`**: 공용·데이터 스토리지를 마운트하고 사용자가 링크를 만들 수 있는 **모든** 호스트(워커만이
   아니다 — `deploy/README.md` §2b-2).
10. **방화벽·시간·DNS**(`deploy/README.md` §0-3): 노드 간 CNI 포트, apiserver → 워커 파드 9443(CNPG 웹훅)·8443(Volcano 웹훅), Avi SE 대역 →
    마스터 :443 만(:19002·:19003 은 노드 밖에서 차단), dms-api(마스터) → 메일 릴레이 :8025, k8s 연동 호출 노드(워커) → 대상 클러스터 API 서버
    (프록시를 타지 않는다), 인터넷 노드 → 상류(프록시). RHEL firewalld 기본 zone 은 그 포트를 열지 않으면 노드 간 흐름을 막는다. 모든 노드 **NTP**
    동기. 포탈 FQDN → VIP-A DNS 는 개통 때 연다(§6-7).
11. **Volcano(클러스터 운영자, 설치 전)**: ≥ v1.15(K8s 1.36 이면 v1.16). DMS 층에 아직 동봉하지 않는다 — 없으면 S1 이 멈춘다(docs/BACKLOG.md
    「⑦ 설치기 후속」). DMS 는 그 위에 Queue `dms-data`·PriorityClass 를 만든다(`deploy/README.md` §0-4).
12. **메일 릴레이 준비(사내 메신저 서버 담당)**: Knox 릴레이 IP·포트(기본 8025)·인증 키를 받는다. 릴레이의 `RELAY_ALLOWED_CLIENTS` 에 **dms-api 가
    뜨는 마스터 3대의 IP**(`patch-placement.yaml` — 인증 메일은 dms-api 만 보내고, 파드 송신은 노드 IP 로 SNAT 되는 CNI 전제),
    `RELAY_ALLOWED_DOMAINS` 에 `EMAIL_DOMAIN`. 포탈 쪽 설정은 설치 뒤 5b)(`deploy/README.md` §11).

## 1~6. 설치

```sh
# 1) [설치 호스트] 값 — 파일 하나(비밀 아님, git 에 커밋하지 않는다). REPLACE_* 를 실값으로(아래 「values.env 값」)
cp deploy/overlays/prod/values.env.example deploy/overlays/prod/values.env
$EDITOR deploy/overlays/prod/values.env
#    (저장소 밖에 두려면 env VALUES_ENV=<경로> — install.sh·render.sh 가 같은 규칙으로 읽는다)

# 2) [설치 호스트] 계획 — 변경 없음: S1 preflight(K8s 1.34~1.36·SHARED_DIR 형식·REGISTRY_CLUSTER_IP·PSA·admission·애드온 감지 표·노드 셸 체크리스트)
#    + S2 확인 + S3 층별 server dry-run. 성공 = 끝 줄 「계획 끝(변경 없음). 실제 설치: …」 + RESULT PASS·WARN(exit 0).
sh deploy/install.sh --context <ctx> --plan
#    FAIL(exit 1)·모름(exit 4)이면 끝 줄이 「계획부터 다시: … --plan」이다 — 위 [FAIL]·[UNKNOWN] 줄의 사유를 고친다(P 는 --adopt <x> 로만 채택,
#    deploy/README.md §2h). RESULT WARN 이면 [WARN] 줄(정책 엔진·cluster.local 아님·하단 정적 대역 밖 — 뒤 단계 실패의 예고)을 읽는다.
#    [MANUAL] 줄(getenforce·crio config 의 pinned_images·signature_policy·findmnt -T <SHARED_DIR>·런타임 NO_PROXY·sysctl)은 노드에서 돌려 확인한다.

# 3) [설치 호스트] TLS 시크릿(사내 CA 발급 — SAN = PORTAL_DOMAIN + PORTAL_VIP) → 설치 1회차. ns 가 아직 없으면 먼저
#    kubectl --context <ctx> apply -f deploy/k8s/00-namespace.yaml (base 에서 raw apply 하는 유일한 파일 — S2 도 같은 것을 적용한다):
#      kubectl --context <ctx> -n dms create secret tls <PORTAL_TLS_SECRET — 기본 dms-portal-tls> --cert=tls.crt --key=tls.key
#    없으면 S2 가 WARN 으로 알리고 **S8 이 멈춘다**(발급을 기다리는 동안 S2b~S7 은 먼저 끝내 둘 수 있다 — deploy/README.md §3b-2).
sh deploy/install.sh --context <ctx>
#    성공(첫 설치) = S1 → S2(ns·dms-pg-app·dms-secrets 를 만들고 값을 출력하지 않는다) → S2b → S3 → S4(PriorityClass·레지스트리) 뒤
#    「---- 인터넷 노드에서 …」 블록 + 재개 줄 + RESULT: PAUSE(exit 10 — 레지스트리가 비어 있다). 레지스트리에 이미 다 있으면 멈추지 않고 끝까지 간다.

# 4) [인터넷 노드 — 같은 커밋의 저장소 루트] PAUSE 가 출력한 명령 그대로(아래 「이미지 반입」 3·4):
#      sh deploy/push-images.sh --registry <REGISTRY> --run                     # 성공 = 끝 줄 RESULT: PASS(멱등 — 끊겼으면 다시)
#      REGISTRY=<REGISTRY> TAG=<DMS_TAG> ./deploy/docker/build-and-push.sh       # 첫 부트스트랩 — 성공 = 「>> done: …」 (deploy/README.md §1)
#    [설치 호스트] 재개 — PAUSE 가 출력한 재개 줄을 그대로 쓴다(--upgrade-addon·--adopt·env 를 싣는다). S12 의 VIP 경유 readyz 는 설치 호스트가
#    사내 CA 를 믿지 않으면 CACERT 가 필요하다(믿으면 생략):
CACERT=<사내CA> sh deploy/install.sh --context <ctx> --stage S5
#    성공 = S5 대조 → S6 → … → S12 → S13 체크리스트 + RESULT PASS·WARN(exit 0). 실패는 한 줄 사유와 그 단계의 표(deploy/README.md §2e-3·§2e-4·
#    §2f-3·§2f-4·§2g-3)로 고친 뒤 `--stage <그 단계>`.

# 5) [설치 호스트] 첫 관리자(계정 0개인 새 사이트 — 포탈 「컨트롤 상태」·「k8s 연동」 등록 전에): S13 체크리스트의 명령 그대로(deploy/README.md §6-1).
#    이름은 values.env 의 LOCAL_ADMIN(특권 목록 — 전체 PV·직접 경로 권한, 다른 이름이면 일반 관리자). x-admin-token 부트스트랩만 평문 비밀번호를
#    받는다. 토큰은 0600 파일로만 curl 에(출력·argv 없음), 비밀번호는 셸 내장 printf 로 stdin 에(" 와 \ 는 쓰지 않는다 — JSON). port-forward 라
#    Avi·DNS 전에도 된다. 한 터미널에서:
#      kubectl --context <ctx> -n dms port-forward svc/dms-api 18080:http
#    다른 터미널(POSIX sh):
umask 077; H=$(mktemp)
kubectl --context <ctx> -n dms get secret dms-secrets -o jsonpath='{.data.DMS_ADMIN_TOKEN}' | base64 -d | sed 's/^/x-admin-token: /' > "$H"
stty -echo; printf 'password: '; read -r PW; stty echo; echo
printf '{"username":"%s","password":"%s"}' '<LOCAL_ADMIN>' "$PW" | curl --noproxy '*' -sS -H @"$H" -H 'content-type: application/json' --data-binary @- http://127.0.0.1:18080/api/admin/accounts
rm -f "$H"; unset PW
#    성공 = 201(본문 {"username": …, "role": "admin"}) → 포탈에 로그인(이후 자동화는 봉인 로그인 — deploy/README.md §10-7). 409 account_exists =
#    이미 있다. 이 토큰은 첫 관리자 뒤에도 admin 계정을 만들 수 있다 — ns dms 의 시크릿 읽기·pods/exec·파드 생성 권한을 좁힌다
#    (api·controller 파드 env 에도 있다 — deploy/README.md §3b-3).

# 5b) [브라우저 — 첫 관리자 세션] 메일(Knox 릴레이) — **사용자에게 열기 전·k8s 연동 등록 전**. 포탈 접속(공식 DNS 전) = 운영자 PC hosts
#    「<VIP-A> <FQDN>」 → https://<FQDN>(deploy/README.md §6 머리 — Avi 가 아직이면 port-forward). 기본 발송 방식 stub 은 인증번호를 화면에
#    돌려줘, 아이디(= RoleBinding 맵핑 ID)만 알면 누구나 가입해 그 PV 권한을 얻는다. 포탈 관리 → 메일 설정 → 발송 방식 knox_relay·릴레이 IP·
#    포트·키 → 「연결 확인」 → 「테스트 메일」. 성공 = 연결 확인 OK·테스트 메일 실수신. 실패 사유·조치는 화면과 deploy/README.md §11
#    (MailerError 표). 고정: 사이트 체크아웃의 patch-config.yaml 에 DMS_MAILER_BACKEND: "knox_relay" → --only S11(deploy/README.md §6-2·§3-4).
#    Avi 를 운영자 대역으로 좁히지 못했으면 k8s 연동 등록 전에 관리 → 계정에서 운영자가 만들지 않은 user 계정을 지운다(deploy/README.md §6-2·§6-8).

# 6) 설치 뒤 설정·개통 = deploy/README.md §6 차례대로: §6-3 컨트롤 상태(빌드 노드 = 인터넷 노드·로컬 체크아웃 경로·프록시) → §6-4 스토리지·
#    정책 → §6-5 k8s 연동 → §6-6 공용 스토리지 밖 사본 → §7 스모크 → §6-7 개통(Avi 3/3 up → 아래 7) S12 — DNS 전에 된다 → §6-8 열기 전
#    확인표 → DNS 등록 → 사용자 공지).

# 7) [설치 호스트] 다시 검증만(언제든 — 읽기 전용으로는 VERIFY_NO_PROBES=1). FQDN·VIP 는 render.env(values.env 의 PORTAL_DOMAIN·PORTAL_VIP)가 기본:
CACERT=<사내CA> sh deploy/install.sh --context <ctx> --only S12
```

- **멈춤과 재개**: 어느 단계든 실패하면 고친 뒤 `--stage <그 단계>` 로 이어 간다 — 멈춤 줄이 보이는 재개 명령은 이번 실행의 `--upgrade-addon`·
  `--adopt`·판정 env(S12 의 `CACERT`·`PORTAL_DOMAIN`·`PORTAL_VIP`·`VERIFY_NO_PROBES` 포함)를 그대로 싣는다(빼면 업그레이드가 조용히 빠진다).
  모름(exit 4 — 프로브가 노드를 재지 못함 등)은 실패와 섞지 않는다. `--plan` 의 재확인 줄은 늘 `--plan` 이다.
- **이미 있는 애드온**: Gateway API·EG·CNPG·Volcano 가 이미 있으면 감지가 C(호환 — 재사용, **다시 설치하지 않는다**)·C!(EOL·범위 밖 — WARN +
  소유자 요청)·X(비호환 — FAIL)로 가른다. `ADDON_INSTALL=never` 면 없는 애드온(A)을 설치하지 않고 FAIL + 「클러스터 운영자에게 요청」.
  **Volcano 는 아직 DMS 가 설치하지 않는다**(위 0 의 11).
- **업그레이드**: 앱(새 DMS 커밋 — 설치 호스트·빌드 노드를 같은 커밋으로 → `--plan` → 전체 실행 `sh deploy/install.sh --context <ctx>`(S11 은
  앱 층만 적용한다 — 플랫폼 층 변경은 S4·S8·S10 재적용으로 들어간다. 앱 층만 바뀐 커밋이면 `--only S11`) → 포탈 빌드·릴리스)은 `deploy/README.md` §4,
  롤백은 §5(앱은 옛 태그 재릴리스 — `kubectl rollout undo` 금지). 애드온은 `--upgrade-addon cnpg`(minor 하나씩 — 직전 덤프 → SSA → rollout →
  healthy 대기), `--upgrade-addon envoy-gateway` 등(§2f-6·§2g-6), `--upgrade-addon registry`(마스터 pull·고정 목록 먼저 — §2e-5). 이미지 교체를
  막는 가드(exit 3)는 이 플래그일 때만 그 층에서 열린다(전역 `ALLOW_IMAGE_CHANGE=1` 은 S11 의 앱 가드까지 열므로 애드온 업그레이드에 쓰지 않는다).
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
   agent 는 같은 TAG 의 dms·dms-mpifileutils 위에서 빌드되므로 DMS_TAG·DMS_AGENT_TAG·MFU_TAG 를 같게. `REGISTRY` 는 필수 — 기본값이 없다.
   base 매니페스트는 고치지 않는다; 빌드 전 e2e 게이트는 개발 PC 에서 같은 커밋으로 — `deploy/README.md` §8 「이미지 빌드 전 게이트」). 설치 뒤에는
   포탈 빌드가 같은 노드를 쓴다 — 그래서 이 노드는 **NoSchedule taint 없는 워커**·privileged 파드 허용·**로컬 체크아웃**(git worktree 금지)이어야
   한다(`deploy/README.md` §8 0)). 그다음 설치 호스트에서 PAUSE 가 보인 재개 줄(`--stage S5`). 프록시가 TLS 를 가로채는(사내 CA 로 재서명)
   사이트면 이 스크립트는 사내 CA 를 빌드 컨테이너에 넣지 못해 `x509 … unknown authority` 로 실패한다 — 포탈 빌드의 CA 주입(§8 3b-1))은 여기
   적용되지 않는다. 우회(미검증)는 `deploy/README.md` §1 「실패하면」.

---

## values.env 값

`deploy/overlays/prod/values.env.example` 의 주석에 각 값의 뜻·예시가 있고, 정하는 사람·박히는 곳·나중에 바꿀 수 있는지는 `deploy/README.md` §0-1 표다.
요약: 레지스트리(아래) · `DMS_TAG`/`DMS_AGENT_TAG`/`MFU_TAG`(push 한 태그 — 첫 설치는 셋 다 같게, 설치 뒤 `live` 가능) · `SHARED_DIR`
(공용 디렉터리 절대 경로 — 아래) · `EMAIL_DOMAIN`(계정 아이디 = k8s 맵핑 ID 를 인증 메일이 보증) · `LOCAL_ADMIN`(특권 목록 — 전체 PV·직접 경로
권한) · `PORTAL_DOMAIN`(포탈 FQDN) · `PORTAL_VIP`(Avi VIP-A — 둘 다 매니페스트가 아니라 `render.env` 로만 간다: 인증서 SAN·S12 `curl --resolve`
의 기준) · (선택) 입구 `GATEWAY_PROXY_PROTOCOL`·`GATEWAY_LISTEN_PORT`·`PORTAL_TLS_SECRET`·`GATEWAY_ENVOY_*_REQUEST`(`deploy/README.md` §2g-1·
§2g-4 — 위 0 의 7), DB `PG_*`(§2f-1), control-plane 라벨 값 `CONTROL_PLANE_LABEL_VALUE`(RKE2 = `true` — §2f-1, 아래 「선행조건」의 노드 수).
옛 values.env 의 `LDAP_*`·`IDENTITY_SUPPLEMENTARY_GROUPS` 는 무시된다(렌더가 한 줄 알린다).

**렌더가 형식을 보지 않는 값**: `EMAIL_DOMAIN`·`LOCAL_ADMIN`·`PORTAL_DOMAIN`·`PORTAL_VIP` 는 「비었나·자리표시자 그대로인가」만 본다(`render.sh` 의
`KEYS` — 도메인·계정명·IPv4 형식 검사 없음). `LOCAL_ADMIN` 에 오타가 있으면 S13 에서 만드는 첫 관리자가 특권 목록(`root,admin,<LOCAL_ADMIN>`)
밖의 일반 관리자가 되고, `PORTAL_DOMAIN`·`PORTAL_VIP` 가 틀리면 인증서 SAN·S12 VIP 경유 검증이 어긋난다 — 렌더 뒤
`deploy/.rendered/prod/render.env` 와 `deploy/.rendered/prod/app/patch-config.yaml` 의 `DMS_PRIVILEGED_REQUESTERS` 를 눈으로 확인한다.

**레지스트리(2026-11-03 ④, 설계 v6 7)**: `REGISTRY_MODE=in-cluster`(기본) 면 DMS 가 control-plane 노드에 Distribution v3.1.2 하나를
띄운다(`deploy/platform/registry`, 저장소 `<SHARED_DIR>/dms/registry`). `REGISTRY_CLUSTER_IP`(Service 고정 ClusterIP — ServiceCIDR 안·
미사용·하단 정적 대역 — S1 preflight 가 본다)·`REGISTRY_PORT`(5000)로 주소가 정해지고, `REGISTRY` 는 비우면 그 주소로 채워진다(채우면 같아야 한다) — 이
주소가 이미지 newName·`DMS_JOB_IMAGE`·`DMS_BUILD_REGISTRY`·`DMS_BUILD_BUILDER_IMAGE` 와 DB 의 잡 이미지에 박히고 노드 신뢰 drop-in·고정
이미지 이름에도 들어가므로 **한 번 정하면 바꾸지 않는다**. `SHARED_DIR_ID` 는 S0 가 출력한 `.dms-shared-id` 값이다 — 레지스트리 init 이
저장소의 센티널과 대조해 없거나 다르면 기동하지 않는다(마운트 빠진 마스터의 로컬 디스크에 쓰지 않게). `REGISTRY_TLS_VERIFY=false`
(in-cluster 는 평문 HTTP 만 — 포탈 태그 조회·삭제가 `http://` 고정. external TLS 레지스트리면 `true` 로 두면 인터넷 노드 push·설치기 S5 대조가
TLS 를 쓰지만 포탈 태그 조회·삭제·빌드 push 는 그 레지스트리에서 쓰지 못한다). `REGISTRY_MODE=external` 이면 `REGISTRY` 에 사이트 레지스트리
`host:port`(경로 접두 불가)를 쓴다(줄이 없으면 external — 옛 values.env). 절차는 `deploy/README.md` §2e.

**`SHARED_DIR`(2026-11-03, 옛 `SHARED_FS` 대체)**: 사이트가 모든 마스터·워커의 **같은 호스트 경로**에 `/etc/fstab` 으로 미리
마운트한 공용 스토리지(종류 무관)의 마운트포인트 또는 그 아래 디렉터리(절대 경로, `^/[A-Za-z0-9._/-]+$`, `/` 아님, 정규화).
DMS 는 마운트·프로비저닝하지 않고 `<SHARED_DIR>/dms/` 아래만 쓴다. 아티팩트 base·allowlist·api/controller hostPath 가 모두
`<SHARED_DIR>/dms/artifacts` 하나다(오버레이 replacements 가 `DMS_ARTIFACT_BASE_URI` 에서 낸다). 모든 hostPath 는 `type: Directory`
라 마운트가 빠진 노드에서는 파드가 뜨지 않는다(빈 로컬 디렉터리에 조용히 쓰지 않는다). `<SHARED_DIR>/dms` 와 그 상위·하위는 DMS 스토리지로
등록할 수 없다(포탈 422 `storage_overlaps_dms_state`) — 데이터 스토리지와 같은 파일시스템이면 SHARED_DIR 를 그 스토리지의 `mount_path` 밖의
**별도 마운트포인트**로 고른다(같은 파일시스템의 하위 디렉터리로는 못 피한다). 트리·센티널(S0)은 위 0 의 3 —
`dms/{artifacts,pgdata/dms-pgdata-01..05,pgdata/.retired,backup/pg,registry}` 와 `.dms-shared-id` 등을 만들고, SHARED_DIR·상위는 만들지 않으며,
이미 있으면 검사만 한다(종료 0 PASS · 1 FAIL · 2 사용법 · 4 모름). 동작 확인(교차 노드 가시성·root 소유 보존·숫자 uid/gid·POSIX·fsync)은
설치기 S7 이 한다(dms 이미지가 레지스트리에 생기는 S5 뒤). 손으로는:
`python3 deploy/shared-storage-probe.py --context <ctx> --shared-dir <SHARED_DIR> --image <REGISTRY>/dms:<DMS_TAG>`
(`<REGISTRY>` 는 in-cluster 면 `deploy/.rendered/prod/render.env` 의 `REGISTRY=` — values 에서는 비어 있다.
종료 0 PASS · 3 WARN(G3 만, 설치 계속) · 1 FAIL(센티널 없음 = S0 미실행 포함) · 4 모름 · 2 사용법).

**옛 values.env 에서 오는 사이트(`SHARED_FS` 이행 — 이 절 한 곳에만 적는다)**: `SHARED_FS=<이름>` 이 남아 있으면 render 가 FAIL 하고
`SHARED_DIR=/<옛 값>` 으로 바꾸라고 알린다(같은 경로 base). 그 값을 쓰기 전에 확인한다: (1) 포탈 「아티팩트 경로」가 **DB 설정**이고 값이
`file://<SHARED_DIR>/dms/artifacts` 가 아니면 새 배치에서 그 경로는 api·controller 에 보이지 않는다(옛 allowlist 는 공용 디렉터리 전체였다) —
산출물을 옮기고 base 를 다시 저장(force)한다. (2) 등록 스토리지의 `mount_path`·`managed_root` 가 `<SHARED_DIR>/dms` 를 덮으면(옛 배치에서
`mount_path` 가 공용 디렉터리 최상위였던 경우) 그 스토리지 잡에 DMS 상태가 보인다 — SHARED_DIR 는 그 스토리지 밖의 별도 마운트포인트로 고른다.
적용 뒤 「아티팩트 경로」 화면이 금지 경로(DMS 상태 디렉터리)와 겹치는 기존 스토리지를 경고로 보인다. (3) 옛 배치(hostPath 가 공용 디렉터리
최상위) 위에 이 렌더를 적용하면 `guard-images.sh` 가 hostPath 변경으로 **exit 5** 거부한다 — 전환 런북(`deploy/README.md` §2d-4: 노드 마운트·
S0·프로브·산출물 복사)을 마친 뒤 `ALLOW_HOSTPATH_CHANGE=1` 로 적용한다.

렌더러 `sh deploy/render.sh prod --context <ctx>`(태그가 `live` 면 그 클러스터에서 읽는다; 옛 `sh deploy/overlays/prod/render.sh` 는 같은 일의 호환
진입점)가 이 값을 오버레이 템플릿과 플랫폼 층에 넣어 `deploy/.rendered/prod/`(git 밖 — `app/`·`platform/priorityclass/`·`platform/registry/`·
`addons/cnpg/`·`platform/db-*/`·`addons/gateway-api/`·`addons/envoy-gateway/`·`platform/gateway/`·`render.env`)를 만들고, `install.sh` 가 `app/` 을
`kubectl --context <ctx> apply -k` 한다(플랫폼 층은 SSA — `deploy/README.md` §3-1). 렌더러는 in-cluster 면 다섯 곳의 레지스트리 주소가 같은지,
오버레이의 아티팩트 base 가 `SHARED_DIR` 에서 나온 값인지 다시 보고 어긋나면 FAIL 한다(실패하면 결과 디렉터리를 바꾸지 않는다). 오버레이 템플릿
자체(kustomization·patch-*)는 직접 apply 하지 않는다(REPLACE_ 이미지). **`install.sh` 는 매 실행(`--plan`·`--only` 포함) 처음에 다시 렌더한다 —
`deploy/.rendered/prod` 를 손으로 고친 값은 설치기로 적용되지 않는다**(편집은 조용히 사라지고 설치는 편집 없는 값으로 PASS 한다). 필요한 값은
values.env 키로 넣는다 — 키가 없으면 docs/BACKLOG.md 「⑦ 설치기 후속」(예: 클러스터 도메인 ≠ cluster.local 의 EG `KUBERNETES_CLUSTER_DOMAIN`)이고,
그 전까지는 설치기 실행 뒤 손으로 적용한다(`deploy/README.md` §2d~§2g 의 손 명령 — 앱 층 값이면 다음 설치기 S11 apply 가 되돌리므로 아래 「설치 뒤 운영」의
튜닝 키 경로로).

## 선행조건 (설치기가 확인하는 것 — S0 사이트 몫과 설치기가 만드는 것)

| 항목 | 확인 |
|---|---|
| **Kubernetes** 1.34~1.36(설계 #4 — prod minor) / **CNI** 임의(Cilium KPR 없음 + kube-proxy 전제) / CRI-O | S1 preflight 「0. 클러스터 / 컨텍스트」(밖이면 FAIL) |
| **노드 수** | control-plane **3대**(PG 인스턴스 required anti-affinity·Envoy·레지스트리·dms-api·controller — ≠ 3 이면 S1 WARN) + NoSchedule 없는 **워커 ≥ 2**(CNPG 오퍼레이터 2개·EG 컨트롤러·pg_dump·dms-agent·잡·빌드가 워커 전용 — `deploy/README.md` §0-2). 마스터 배치는 라벨 `node-role.kubernetes.io/control-plane` 의 **값 `""`**(kubeadm 관례)로 고른다 — RKE2 처럼 `true` 를 다는 배포판이면 values.env `CONTROL_PLANE_LABEL_VALUE=true` 로 둔다. 아니면 마스터에 서는 레지스트리(S4)·PG(S10)·dms-api·controller(S11)는 Pending 이고, Envoy(S8) DaemonSet 은 파드가 생기지 않는다(DESIRED 0). S1 은 라벨 키만 세므로 PASS 한다 — 확인 `kubectl --context <ctx> get nodes -L node-role.kubernetes.io/control-plane`(`deploy/README.md` §0-2·§2f-1) |
| **SELinux**(RHEL 계열 DMS 노드) | enforcing 아님(재부팅 뒤에도) — S0 사이트 몫, S2b 프로브가 노드마다 재고 FAIL·모름이면 멈춘다(위 「0. 사이트 준비」 2) |
| **시간·방화벽** | 모든 노드 NTP 동기, 노드 간 CNI·apiserver → 워커 9443·8443·Avi SE → 마스터 리스너 포트(기본 :443 — `GATEWAY_LISTEN_PORT`) — 설치기는 보지 않는다(사이트 몫 — `deploy/README.md` §0-3). 방화벽이 막으면 S9(9443)·S9b(8443) dry-run·S12 가 실패로 드러난다 |
| **Volcano** (잡 gang 스케줄러) | ≥ v1.15(K8s 1.36 이면 v1.16) — **사이트가 설치**(DMS 층 미동봉, BACKLOG ⑦). S1 감지가 C 면 재사용, 없으면 S1 이 멈춘다. S9b 가 DMS 모양 vcjob 을 server dry-run(웹훅 8443) |
| **입구(Envoy Gateway)** | MetalLB·ingress-nginx 는 **필요 없다**(2026-11-03 ⑥ — 오버레이가 Ingress·풀을 지운다). Gateway API CRD(없으면 설치기 S8 이 v1.6.1 standard 를 설치한다 — A. 이미 있으면 v1.5.0 이상을 재사용한다 — C, 더 낮으면 X — `deploy/README.md` §2g-3) + EG v1.9.x(없으면 S8 이 설치 — A, 있으면 재사용 — C), 마스터 sysctl `ip_unprivileged_port_start=443`(S0-5 — 리스너 포트 `GATEWAY_LISTEN_PORT` 가 1024 미만일 때만 필요하고, 그때 S8 이 마스터마다 다시 잰다), ns `envoy-gateway-system` PSA privileged, Avi VS-443(PROXY v2 → 마스터 리스너 포트, 기본 :443 — 못 맞추면 `GATEWAY_PROXY_PROTOCOL=off`·`GATEWAY_LISTEN_PORT`, 위 「0. 사이트 준비」 7) — `deploy/README.md` §2g. 확인: S1·S8(SSA 전)이 마스터에 hostPort <리스너 포트> 를 쓰는 다른 파드를 찾으면 멈추고, S8·S12 가 Gateway Programmed + 마스터마다 리스너 포트 LISTEN(`deploy/host-net-probe.sh` — Programmed 는 sysctl 빠진 마스터를 못 잡는다) |
| **PSA** | `dms` 네임스페이스 `enforce: privileged`(00-namespace.yaml) — 사내 정책이 허용해야 잡 파드가 뜸(보안 승인 — `deploy/README.md` §0-4) |
| **설치 자격** | 설치·재실행 컨텍스트 = **cluster-admin**(CRD·ClusterRole·웹훅·PV·Namespace·privileged 프로브·시크릿 값 읽기). 설치기는 미리 검사하지 않는다 — 모자라면 그 단계가 `Forbidden` 으로 멈춘다(`deploy/README.md` §0-2 「설치 호스트」) |
| **데이터·PV 스토리지 root** | 모든 잡이 root(uid 0)다 — export 가 DMS 워커에 root 를 허용해야 한다(root squash 없음 — `deploy/README.md` §2 「root 접근」). 설치기는 보지 않는다 — squash 면 첫 잡의 미리보기·실행이 EACCES/EPERM 으로 실패한다 |
| **PostgreSQL** | CloudNativePG Cluster `dms-pg`(설치기 S9·S10 — `deploy/README.md` §2f): 마스터마다 1개, 공용 디렉터리의 정적 hostPath PV ×5(S0 가 `dms/pgdata/dms-pgdata-01..05` 를 26:26 0700 으로 만든다), `dms-secrets` 의 URL 은 `dms-pg-rw`(설계 6-3). apiserver → 워커 파드 9443(CNPG 웹훅)이 열려 있어야 한다 |
| **공용 디렉터리** | 모든 마스터·워커의 같은 경로 `SHARED_DIR` 에 마운트 + S0 `sh deploy/shared-dir-init.sh <SHARED_DIR>`(한 노드, root — `dms` 0750·`dms/artifacts` 0755 등과 센티널), 각 스토리지 `managed_root` 사전 mkdir. 확인: 설치기 S7(`deploy/shared-storage-probe.py` — S5 뒤: 프로브 파드가 dms 이미지를 그 레지스트리에서 받는다) |
| **클러스터 내 레지스트리**(in-cluster) | 모든 노드 CRI-O 신뢰 `/etc/containers/registries.conf.d/50-dms-registry.conf`(S0-4) · 마스터 전부 레지스트리 이미지 반입 + `/etc/crio/crio.conf.d/99-dms-pinned.conf`(S0-5) · S4 적용 · S5 push·대조 · S6 pull — `deploy/README.md` §2e. 설치기는 레지스트리가 Ready·`/v2/` 200 이 아니면 S4 에서, lock 이미지가 lock digest 가 아니거나 dms·dms-agent·dms-mpifileutils 태그가 없으면 S5(·S11 apply 전)에서 멈춘다 |
| **메일 릴레이**(Knox — 사내 메신저 서버) | 설치기는 보지 않는다. 릴레이 허용 클라이언트 = dms-api 가 뜨는 마스터 3대 IP, 허용 도메인 ∋ `EMAIL_DOMAIN`. 포탈 「연결 확인」·「테스트 메일」로 확인(위 5b, `deploy/README.md` §6-2·§11) — **확인 전에는 사용자에게 열지 않는다** |

세부(Avi VS-443 설정·PROXY 컷오버·원 IP·「80 불가 = 정상」·업그레이드)는 `deploy/README.md` §2g.
api/controller 의 hostPath 는 `SHARED_DIR` 에서 렌더된다(base 는 자리표시자 — 손으로 패치하지 않는다).
**dms-api·dms-controller 는 마스터에 뜬다**(2026-11-03 ⑤, 설계 #24·#25 — `patch-placement.yaml`: control-plane nodeSelector + toleration,
not-ready·unreachable `tolerationSeconds: 30`). 이 오버레이가 제어면 Deployment 에서 바꾸는 것은 이미지·아티팩트 경로·이 배치 두 필드뿐이다
(`tests/test_release_manifest_contract.py`). 마스터에도 `SHARED_DIR` 마운트·레지스트리 신뢰가 있어야 한다(G1·S0-4). 마스터를 내릴 때의 규칙은
`deploy/README.md` §2f-7(VM 전원 확인 전 강제 삭제·out-of-service taint·Node 삭제 금지).

## 설치 뒤 운영

- **설정 바꾸기**(`deploy/README.md` §3-4): 메일·정책·허용 쌍·k8s 연동·컨트롤 상태는 포탈 화면(DB — 재시작 없음). 튜닝 키(로그인 보호·경보·
  보존일 등 base `20-config.yaml` 의 키, 또는 base 에 없고 코드 기본값만 있는 키 — 예: `DMS_MAILER_BACKEND`, 더하면 새 키가 생긴다)는 **사이트 체크아웃의 `deploy/overlays/prod/patch-config.yaml` 에 키를 더하고**
  `sh deploy/install.sh --context <ctx> --only S11`(설정이 바뀌면 S11 이 api·controller·agent 를 다시 띄운다). ConfigMap 손수정은 다음 설치기
  실행이 되돌린다. 추적 파일의 로컬 수정이므로 새 DMS 커밋으로 올릴 때 그 줄을 다시 얹는다. values.env 키는 `--plan` 뒤 해당 단계.
- **시크릿·인증서·토큰**(§3b): 포탈 TLS 교체(만료 감시 없음 — §14-2 달력에), `DMS_SESSION_SECRET` 회전 시 포탈 저장 메일 키·k8s 토큰 재입력,
  공용 스토리지 밖 사본(§2f-5).
- **업그레이드**(§4)·**롤백·복구**(§5)·**포탈 빌드·릴리스**(§8·§9)·**계정·인증**(§10)·**메일**(§11)·**작업 삭제·결과 만료**(§12·§12b).
- **일상 점검·문제 해결**(§14): `VERIFY_NO_PROBES=1 CACERT=<사내CA> sh deploy/install.sh --context <ctx> --only S12`(읽기만), 포탈의 보안 이벤트·
  작업 정리 상태·아티팩트 경로·릴리스 드리프트 배지, 증상별 색인.
