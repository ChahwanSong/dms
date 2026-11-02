# dms-k8s 프로덕션 설계 v6 (최종본, 논의용)

## 이 문서에 대해

- **기준**: 워크트리 `k8s-sync-main` 7483ba55(dms-k8s 라인), 2026-11-02~03. 저장소·git·클러스터는 바꾸지 않았습니다. kubectl 은 `--context dmsk8s` 로 get·logs 만 실행했습니다. 사본: `/home/mason/.claude/jobs/18e6f484/tmp/v6/dms-k8s-prod-design-v6.md`
- **이 문서가 v1~v5 를 대체합니다.**
  - v1~v3 에서 무효였던 것은 계속 무효입니다: 마스터 12코어, export 분리(D11), PG 전용 VM, PG StatefulSet(P1), 인증번호 60회/시간, `timeouts.request: 0s`.
  - v4 에서 무효가 된 것: csi-driver-nfs 동적 PVC, NFS 를 전제로 한 export·fstab 표, `--all`·index digest, 「PG 로컬 디스크는 테스트베드만」, containerd 경로, NFS 마운트 옵션으로 FAIL 하는 preflight.
  - v5 에서 무효가 된 것: 레지스트리 2개(`REGISTRY_REPLICAS`, S7 증설 게이트, required anti-affinity, PDB, sessionAffinity), G3 필수(S7 ② FAIL), SELinux 대응(`spc_t`·`seLinuxOptions`, `<SHARED_DIR>/dms` 라벨·`semanage`·`restorecon`·`virt_use_nfs`, S7 의 SELinux 왕복 항목 — 그래서 v6 의 S7 은 ①~⑥), 오프라인 번들(S-1 번들 tarball, 번들 파일 반입, 운영자 PC port-forward push), v5 의 질문 Q1~Q6.
  - 앞선 질문의 상태: v4 의 U1·V1·W3 는 4회차에 닫았고, K5 는 대역 겹침만 「K5 잔여」로 남았습니다. U2(레지스트리 개수)는 #46 으로, v4 N6(부팅 때 재마운트)은 5회차 운영자 답(`/etc/fstab`, #50)으로 닫았습니다.
- **v5 대비 바뀐 것(5회차)**
  1. 레지스트리는 **1개**로 돌아갑니다(v4 의 단일 구성, 7-3). 오퍼레이터를 쓰지 않는 이유와 「HA 는 나중에 다시 볼 수 있음」만 남겼습니다(7-1).
  2. G3(노드 간 가시성)는 필수가 아닙니다. S7 ② 실패는 WARN 이고 영향을 함께 알립니다(3-2, 8-1). 다른 교차 노드 확인(S7 ①④⑥, S10 경로 대조)은 재시도 뒤 판정해서 캐시 지연을 FAIL 로 오판하지 않습니다. 레지스트리 교체(롤링)는 빌드·push 가 없을 때 합니다(7-3).
  3. PG 볼륨은 정적 hostPath PV 풀 5개(인스턴스 3 + 여분 2)로 확정했습니다(6-2).
  4. **RHEL 계열 DMS 노드는 SELinux 가 enforcing 이 아니어야 합니다**(permissive 또는 disabled, 필수 설치 조건, 2-3). 「끄는걸」은 enforcing 이 아닌 상태로 읽었습니다 [해석]. DMS 쪽 SELinux 코드·라벨 작업은 모두 지웠습니다. 설치기는 dms 네임스페이스가 생긴 직후(S2b) privileged 임시 DS 로 DMS 노드(마스터 + DMS 워커)를 재고, enforcing 이거나 재부팅하면 enforcing 이 될 노드가 있으면 FAIL 합니다(8-1).
  5. K8s minor 는 prod 가 쓰는 것을 따릅니다(지원 범위 1.34~1.36, #4). 테스트베드도 prod minor 에 맞춥니다 [해석].
  6. 테스트베드: 일반 검증은 Ubuntu 로 충분합니다. 워커는 한 대씩 Rocky Linux 9(SELinux permissive)로 재구성해 EL9 고유 항목과 SELinux 프로브를 실증합니다(10절 T-SE).
  7. 운영자 답: Cilium 은 kube-proxy replacement 를 쓰지 않고 kube-proxy 가 있습니다. 공용 스토리지는 장애 때 대기합니다(G8 충족). 부팅 때 재마운트는 `/etc/fstab` 항목입니다.
  8. airgap 이미지: 오프라인 번들 대신 **외부 인터넷이 되는 지정 노드 1대**(기존 DMS 빌드 노드)가 상류 이미지를 받아 클러스터 안 레지스트리에 직접 push 합니다. 검증은 `images.lock` digest 로 합니다(7-5). 첫 설치에는 K8 로 받은 노드를 쓰고, 설치 뒤 컨트롤 상태의 빌드 노드로 등록합니다(S13).
  9. 남은 사용자 결정은 없습니다(끝 절).
- **v3 를 참조할 때 읽는 법**: §5·§9② 의 세부는 v3 의 2(마)절과 5절 ①·② 에 맡깁니다. 그 본문은 다음처럼 바꿔 읽습니다.
  - 전체 오답 60 → **1000**/시간(`DMS_VERIFICATION_GUESS_GLOBAL_LIMIT` 기본 1000)
  - `0s` → **3600s**
  - 503 `login_busy` 상한과 슬롯 대기 상한은 3600s 에서도 필요합니다(v3 의 #25 수정).
  - v3 의 R·D 번호는 이 문서에서 쓰지 않습니다.
- **표기**
  - **[확정]**: 사용자 결정 또는 CLAUDE.md 규약입니다. 회차는 다섯입니다.
    - 「1회차」: v3 본문 시점
    - 「2회차」: v3 변경 절(C1~C3, 1000/h, no_root_squash, etcd, 공용 NFS — 공용 NFS 는 4회차에 공용 스토리지로 일반화)
    - 「3회차」: PG 검토 요청과 회신(CNPG, 레지스트리, B3 등)
    - 「4회차」: 2026-11-03 사용자 답변·요청
      - U1 권고 수용
      - 레지스트리 이중화 조사
      - Avi·클러스터 플랫폼·서비스 CIDR·방화벽 답변
      - 공용 스토리지 일반화
      - 테스트베드도 공용 경로 사용
    - 「5회차」: 2026-11-03 사용자 답변(v5 의 Q1~Q6, 테스트베드 검증 방식, 인터넷 노드)과 운영자 답(Cilium 모드, 공용 스토리지 장애 거동·재마운트)
  - **[설계]**: 확정 사항을 구현하는 세부라서 결정이 필요 없습니다.
  - **[권고]**: 설계자가 권하는 기본값입니다. 이의가 없으면 이대로 구현합니다(따로 결정을 묻지 않음). 남은 사용자 결정은 없습니다(끝 절).
  - **[해석]**: 사용자 문장을 해석한 것입니다.
  - 「추정」: 실측하지 않은 값입니다.
- **참조 약칭**(경로는 `/home/mason/.claude/jobs/18e6f484/tmp/` 기준)
  - 앞선 문서
    - 「1차」 `vip/final.md`
    - 「2차」 `idrev/final.md`
    - 「v3」 `design/dms-k8s-prod-design-v3.md`
    - 「v4」 `design/dms-k8s-prod-design-v4.md`
    - 「v5」 `design/dms-k8s-prod-design-v5.md`
    - 「PG 검토」 `pgop/final.md`
  - 「REG 제품」「REG 접근」「설치 순서」: v4 하위 보고. 원자료는 `reg/` 에 있습니다.
  - 「공용 스토리지 조사」「REG HA」「플랫폼」: v5 하위 보고. 원자료는 `v5/dl/`, `v5/regha/`, `v5/plat/dl/` 에 있습니다.

---

## 0. 요약

```
사내 PC ─https://<포탈 FQDN>─▶ [Avi VS-443 · VIP-A] L4, PROXY v2(감사용) ─▶ 마스터 ×3 :443
kubectl·kubelet·「k8s 연동」──▶ [Avi VS-6443 · VIP-B] L4, PROXY 없음      ─▶ 마스터 ×3 :6443

┌ 마스터 VM ×3 (8 vCPU/32GB, RHEL 9.6 x86_64, SELinux 꺼짐, CRI-O, ESXi 분리, control-plane taint 유지) ┐
│ 공통: etcd(로컬) · apiserver · kcm · scheduler · Cilium · kube-proxy(KPR 없음)                         │
│       Envoy DS(hostNetwork :443 TLS 종단 → svc dms-api:8080) · CNPG 인스턴스 1개(hostPath PV)          │
│ 한 대씩: dms-api ×1 · dms-controller ×1(→2) · dms-registry ×1(고정 ClusterIP <IP>:5000)                │
└────────────────────────────────────────────────────────────────────────────────────────────────────────┘
┌ 베어메탈 워커 ×N(SELinux 꺼짐): dms-agent DS · Volcano 잡 · 빌드 파드 · pg_dump CronJob                │
│                   CNPG 오퍼레이터 ×2 · EG 컨트롤러 · Volcano 3종                                       │
│ 인터넷 노드 1대(지정된 빌드 노드): 상류 이미지를 받아 레지스트리 ClusterIP 로 직접 push(7-5)           │
└────────────────────────────────────────────────────────────────────────────────────────────────────────┘
공용 스토리지(종류 무관, 사이트가 모든 노드의 같은 경로에 /etc/fstab 으로 마운트, 그 경로 또는 하위 = <SHARED_DIR>)
  <SHARED_DIR>/dms/{artifacts, pgdata(정적 hostPath PV ×5), backup/pg, registry}
```

**핵심 다섯 가지**
1. **공용 스토리지**
   - DMS 는 마운트·프로비저닝을 하지 않고 계약(3-2 G1~G12)만 요구합니다. 설치기는 S7 동작 프로브로 계약을 확인합니다. G3(노드 간 가시성)는 필수가 아니어서 미달이면 WARN 입니다.
   - PG 도 이 스토리지 위의 정적 hostPath PV 5개를 씁니다(6-2, 확정). 애드온·CSI 는 필요 없습니다.
2. **레지스트리** [확정: 1개]
   - CNCF Distribution v3.1.2 를 **1개** 둡니다. 위치는 마스터, 저장소는 hostPath `<SHARED_DIR>/dms/registry`, 주소는 고정 ClusterIP 입니다.
   - 쓸 만한 오퍼레이터가 없어서 오퍼레이터 없이 띄웁니다. HA 는 나중에 다시 볼 수 있습니다(7-1).
   - 이미지는 외부 인터넷이 되는 지정 노드 1대가 상류에서 받아 이 레지스트리에 직접 push 합니다(7-5).
3. **설치 순서**: 레지스트리 → push·pull 프로브 → 공용 스토리지 프로브 → GW API·EG → CNPG → Volcano → DB(Ready 대기) → DMS. 애드온은 모두 「감지 → 없으면 설치」 상태 기계로 판정합니다(8절).
4. **443 바인드**: Envoy 가 hostNetwork 로 443 에 직접 바인드합니다. Envoy 가 비 root 라서 마스터에 sysctl `ip_unprivileged_port_start=443` 이 필요합니다(4-3).
5. **SELinux** [확정]: RHEL 계열 DMS 노드는 SELinux enforcing 금지(필수 조건, 설치기가 S2b 에서 확인, 2-3).

**구현 순서(9절)**: ① B3 → ② 로그인 → ③ 공유 FS → ④ 레지스트리 → ⑤ DB → ⑥ 입구 → ⑦ 설치기·문서.
- 묶음마다 dmsk8s 에서 먼저 실증합니다.
- **⑦ 전에는 prod 에 설치할 수 없습니다.** 지금 install.sh 는 MetalLB·ingress-nginx 가 없으면 중단합니다(`deploy/install.sh:74-80`).

**남은 사용자 결정**: 없습니다(끝 절). 남은 것은 11절의 정보 요청입니다.

**먼저 보낼 요청**(리드타임이 긴 것)
- K1 잔여: prod K8s minor(DMS 가 따를 값), CRI-O 버전·공급원, 노드 셸 결과
- N1: 공용 스토리지가 G7(fsync 내구성)을 만족하는지, DMS 밖에서 같은 스토리지를 마운트하는 호스트
- U5: 포탈 FQDN
- K8: 인터넷 노드(첫 설치에 쓸 노드 = 지정된 빌드 노드 이름, 인터넷 경로 — 직접·프록시·사내 CA, 마스터 3대와 설치 호스트로 파일을 옮기는 수단), 마스터의 skopeo 패키지

---

## 1. 결정 사항

| # | 영역 | 결정 | 출처 |
|---|---|---|---|
| 1 | 마스터 | VMware VM 3대, 각 **8 vCPU/32GB** | 3회차(정정) |
| 2 | 마스터 | 서로 다른 ESXi 호스트에 둠(규칙 있음) | 3회차 |
| 3 | 워커 | 베어메탈 | 1회차 |
| 4 | K8s | **prod 가 쓰는 minor 를 따름**(지원 범위 1.34~1.36). 참고: 1.34 는 2026-10-27 EOL(정보, 결정 아님). [해석] 테스트베드도 prod minor 를 따름: K1 잔여로 값을 받으면 T-SE 재구성 때 cp1 포함 한 단계씩 맞춤(testbed 저장소 몫, 일정은 U7) | 3회차, 5회차(사용자 2026-11-03) 「내 프로덕션에 너가 따라가면 됨」 |
| 5 | LB | Avi L4 TCP, VIP 2개: 6443→6443(apiserver HA), 443→443(풀 = 마스터 3대 **:443**). 80 없음 | 1회차, C3(2회차) |
| 6 | DNS | 포탈 FQDN 은 사내 DNS 에 신청 | 1회차 |
| 7 | 망 | apiserver → 워커 파드 통신 허용(CNPG 9443, Volcano 8443 등) | 3회차 |
| 8 | 공유 저장소 | 공용 스토리지 하나(**종류 무관**)를 사이트가 **모든 마스터·워커의 같은 호스트 경로**에 미리 마운트하고, 하위 디렉터리로 용도를 나눔. 경로(마운트포인트 또는 그 아래)는 초기 셋업 env `SHARED_DIR`. DMS 는 마운트·프로비저닝하지 않음 | 2회차, 4회차(사용자 2026-11-03) 정정 |
| 9 | root 보존 | uid 0 소유 보존, root chown 가능(squash 없음, 3-2 G4). NFS 면 `no_root_squash` | 2회차, 4회차 일반화 |
| 10 | etcd | 공용 스토리지의 예외로 마스터 로컬 디스크 | 2회차 |
| 11 | 실증 | 공용 스토리지 위 base·DB, EG, PROXY 는 테스트베드에서 먼저 실증 | 2회차 |
| 12 | 레지스트리 | 「그리고 로컬 이미지 레지스트리도 k8s에 띄우고, nfs로 노드들에 마운트된 공용 디렉토리 밑에 붙이고 싶어.」 → k8s 안에 레지스트리를 띄우고 hostPath `<SHARED_DIR>/dms/registry` 에 붙임 [설계]. [4회차] 스토리지 종류는 무관 | 3회차 |
| 13 | 입구 | 벤더 중립 **Envoy Gateway**. prod 는 MetalLB·ingress-nginx 의존을 없앰 | 1회차 |
| 14 | EG 기본값 | 아래 「EG 기본값 세부」 | 1·2회차 |
| 15 | 원 IP | PROXY v2 는 **감사·로그 전용**. 앱은 XFF 마지막 항목(IP 형식 검증) → 피어 순으로 읽고 X-Real-IP 는 무시. Envoy 는 X-Real-IP 를 지우고 clientIPDetection 은 두지 않음 | 1회차 |
| 16 | 로그인 상한 | 키는 아이디만(IP 축 3곳 삭제). 아이디당 실패 10회/60초 → 429 | 1회차, C2(2회차) |
| 17 | 인증번호 | 6자리, 5분 TTL, 코드당 5회, 아이디별 10회/24h, **전체 오답 1000회/시간**(거절형 429) | 2회차 |
| 18 | 운영자 구제 | 전체 상한이 발동해도 관리자 화면에서 관리 가능: 관리자 비밀번호 재설정(봉인 전송), 인증번호 잠금 해제(아이디별·전체) | 2회차 |
| 19 | scrypt | 대기형 동시 슬롯 2개 | 1회차 |
| 20 | 경보 | 실패, 전체 시도(성공 포함), 원 IP 별(경보만) | 1회차 |
| 21 | 비밀번호 규칙 | **없음** | 1회차 |
| 22 | 전체 스위치 | 기본 꺼짐. 켜면 대기형으로 속도 조절. 503 은 과부하 가드에만 씀 | 1회차, C2 |
| 23 | 선택 4종 | 아이디별 총 시도 상한(성공 포함), 가입·비밀번호 변경 완료 메일, 비밀번호 변경 시 세션 무효화, 보안 이벤트 화면 + 해제 | C1(2회차) |
| 24 | 배치 | dms-api 는 마스터, replicas 1, not-ready·unreachable `tolerationSeconds: 30` | 3회차 |
| 25 | 배치 | dms-controller 는 마스터(1개 → 실증 뒤 2개) | 3회차(권고 수용) |
| 26 | DB | **CloudNativePG** | 3회차 |
| 27 | DB 구성 | 3인스턴스, 마스터마다 1개(required anti-affinity, control-plane toleration), `smartShutdownTimeout: 0`, PG 17 digest 고정 | 3회차 + PG 검토 |
| 28 | 복제 | **동기 `any / 1 / preferred`**(「복제모두 동기」 → 권고 수용) | 3회차, 4회차 확정 |
| 29 | PITR | 필요 없음(오브젝트 스토리지 없음) | 3회차 |
| 30 | CNPG 수명 | 연 2회 이상 업그레이드 수용 | 3회차 |
| 31 | PG 데이터 | prod·테스트베드 모두 `<SHARED_DIR>/dms/pgdata` 하위(#40). **어느 렌더 오버레이에도 로컬 디스크 PG 를 두지 않음.** 테스트베드 로컬 디스크는 T6' fio·pgbench 비교용 일회용 대상으로만 씀 | 3회차, 4회차(사용자 2026-11-03) 정정 |
| 32 | csi-driver-nfs | **필수 아님**. 스토리지가 NFS 일 때만 쓸 수 있는 선택지지만, #40 방식이 NFS 에서도 그대로 동작하므로 DMS 설치기는 다루지 않음(6-2) | 3회차, 4회차(사용자 2026-11-03) 정정 |
| 33 | 오퍼레이터 | CNPG 오퍼레이터 ×2, 워커에 배치, 전용 PriorityClass | 3회차(권고 수용) |
| 34 | 기설치 | CNPG 를 감지(CRD·오퍼레이터·버전)해서 **없을 때만 설치** | 3회차 |
| 35 | 백업 | pg_dump CronJob → `backup/`. 덤프와 `dms-secrets` 는 공용 스토리지 밖에도 사본을 둠 | 3회차(권고 수용) |
| 36 | DMS 접속 | host `<cluster>-rw`, keepalives, `tcp_user_timeout=15000`, `target_session_attrs=read-write`, `sslmode=require` | 3회차(권고 수용) |
| 37 | 코드 | **B3 먼저**. B4·B1 은 우선순위 낮음 | 3회차 |
| 38 | 태도 | 벤더 중립, 보안 과잉 금지, 사용성 우선. 구현은 논의 뒤 | 1회차 |
| 39 | 기존 규약 | k8s 신원 모드 전용(LDAP 없음), 모든 잡 root, RoleBinding 맵핑, 제어면 root·cap drop ALL, airgap 런타임, base 사이트 중립, 단건 sync·rm 은 사람이 컨펌 | CLAUDE.md |
| 40 | PG 볼륨 | 정적 hostPath PV 풀(인스턴스 3 + 여분 2), Retain, nodeAffinity·claimRef 없음 | 4회차 권고, 5회차(사용자 2026-11-03) 「너가 말한대로」 확정 |
| 41 | 플랫폼 | 노드 OS RHEL 9.6, x86_64, 런타임 CRI-O, CNI Cilium(**kube-proxy replacement 없음, kube-proxy 있음**) | 4회차, 5회차(운영자 답) |
| 42 | Avi | 최신 버전. 최신 SDK 기준으로 PROXY v2 는 에디션과 무관하게 허용됨(`design/alb_tcp_application_profile.go:17,20`) | 4회차 |
| 43 | 서비스 CIDR | 사내망에 광고되지 않음 → 익명 레지스트리는 클러스터 안에서만 보임 | 4회차 |
| 44 | 방화벽 | 사용자가 처리하고 필요할 때 정보를 요청함. 4-2 는 참고 목록 | 4회차 |
| 45 | 테스트베드 | DMS 상태(아티팩트·PG·레지스트리·백업)를 **모든 dmsk8s 노드에 마운트된 공용 경로**에 둠. 노드 간 이동·가시성·소유권·fsync 를 prod 와 같은 방식으로 실증하기 위해서임 | 4회차(사용자 2026-11-03) |
| 46 | 레지스트리 개수 | **1개**(v4 단일 구성: control-plane nodeSelector + toleration, `tolerationSeconds: 30`, RollingUpdate `maxSurge: 1`·`maxUnavailable: 0`, 고정 ClusterIP). HA 는 나중에 다시 볼 수 있음 | 5회차(사용자 2026-11-03) 「레지스트리개수 1개로 그냥 하자」 |
| 47 | 노드 간 가시성 | G3 는 필수 요구가 아님. S7 ② 실패는 WARN(영향을 알림) | 5회차(사용자 2026-11-03) 「레지스트리 1개」(v5 Q2 답) |
| 48 | SELinux | **RHEL 계열 DMS 노드(마스터 + DMS 워커)는 SELinux 가 enforcing 이 아님**(permissive 또는 disabled) — 필수 설치 조건. 설치기가 S2b 에서 DMS 노드를 재고, enforcing 이거나 재부팅하면 enforcing 이 될 노드가 있으면 FAIL. DMS 쪽 SELinux 코드·라벨 작업 없음. [해석] 「끄는걸」을 enforcing 이 아닌 상태(permissive 포함)로 읽음 | 5회차(사용자 2026-11-03) 「Dms 노드가 rhel 종류이면 SELinux 끄는걸 필수조건으로 하자」 |
| 49 | 테스트베드 검증 | 일반 검증은 Ubuntu 테스트베드로 충분. dmsk8s 워커(w1·w2)는 한 대씩 Rocky Linux 9(EL9, 가능하면 prod 와 같은 9.6, SELinux permissive)로 재구성해 prod 를 흉내 냄. cp1 은 Ubuntu 유지 [해석: 문장이 「워커노드」만 말함]. IaC 변경은 testbed 저장소 몫 | 5회차(사용자 2026-11-03) 「테스트베드에서는 ubuntu로 검증해도 돼」「워커노드를 그냥 rhel이나 rocky로 구성해」 |
| 50 | 공용 스토리지 운영 | 장애 때 I/O 를 버리지 않고 대기(G8 충족). 부팅 때 재마운트는 노드 `/etc/fstab` 항목 | 5회차(운영자 답) |
| 51 | 이미지 반입 | 외부 인터넷이 되는 지정 노드 1대(기존 DMS 빌드 노드, `deploy/README.md:544-547,561-565`)가 상류 이미지를 받아 클러스터 안 레지스트리에 직접 push. 첫 설치에는 K8 로 받은 노드를 쓰고 설치 뒤 컨트롤 상태 빌드 노드로 등록. 여러 이미지를 묶은 오프라인 번들 없음 | 5회차(사용자 2026-11-03) 「기존 dms 구현대로 외부 인터넷되는 노드 한대 지정되있을거야」 |

**#14 EG 기본값 세부**
- Envoy DS 를 마스터 3대에 둡니다(control-plane toleration).
- 리스너는 hostname 없는 HTTPS 443 하나입니다(인증서 `dms-portal-tls`). 80 리스너와 HSTS 는 두지 않습니다.
- HTTPRoute `timeouts.request: 3600s`, BTP `requestBuffer` 20Mi.
- `envoyService` 는 ClusterIP, `concurrency: 2`.
- 자원: requests 100m/256Mi, limits 1CPU/2Gi.

---

## 2. 목표 아키텍처

### 2-1. 구성요소 배치

| 구성요소 | 노드 | 개수 | requests | limits | 비고 |
|---|---|---|---|---|---|
| etcd | 마스터 | 3 | 1000m/2Gi [권고](기본 100m) | 없음 | 로컬 VMDK. 클러스터 운영자 몫 |
| kube-apiserver | 마스터 | 3 | 1000m/2Gi [권고](기본 250m) | 없음 | kubeadm patches |
| kcm / scheduler | 마스터 | 3 | 200m/512Mi / 100m/256Mi | 없음 | — |
| Cilium·kube-proxy | 전 노드 | DS | 약 250m/0.5Gi | 기존 | KPR 없음, kube-proxy 있음(#41) |
| Envoy 프록시 | 마스터 | DS 3 | 100m/256Mi | 1/2Gi | #14, 4-3 |
| CNPG 인스턴스 | 마스터 | 노드당 1 | 1000m/4Gi | 메모리 4Gi, CPU 없음 | `shared_buffers` 1GB(PG 검토 5-2). 볼륨은 6-2 |
| dms-api | 마스터 | 1 | 250m/512Mi | 1/1Gi | `tolerationSeconds: 30` |
| dms-controller | 마스터 | 1→2 | 100m/256Mi | 1/1Gi | 2개가 되면 preferred anti-affinity |
| **dms-registry** | 마스터 | **1**(#46) | 100m/128Mi(추정) | 1/512Mi | control-plane nodeSelector + toleration, `tolerationSeconds: 30`. T4 실측으로 보정 |
| CNPG 오퍼레이터 | 워커 | 2 | 100m/100Mi(상류) | 100m/200Mi | **`dms-addon-critical`**(1000 > dms-high 200). 상류 Deployment 에는 PriorityClass 가 없음 |
| EG 컨트롤러 | 워커 | 1 | 상류 | — | `dms-addon-critical` [권고]. 죽어도 Envoy 는 마지막 설정으로 동작 |
| Volcano 3종 | 워커 | 각 1 | 상류 | — | **상류 `system-cluster-critical` 유지**(바꾸면 우선순위가 내려감, dmsk8s 실측) |
| dms-agent | 워커 | DS | 기존 | 기존 | 마스터에는 두지 않음 |
| 잡·빌드 파드 | 워커 | — | 기존 | 기존 | dms-low/mid/high |
| pg_dump CronJob | 워커 | 매시간 | 100m/256Mi(추정) | 1/1Gi | `-rw` 에서 읽음 |

- **PriorityClass 를 두는 이유**: 잡이 워커 CPU 를 채운 상태에서 애드온 컨트롤러가 재스케줄되면, 잡 파드를 선점해야 자리를 얻을 수 있습니다.
- **`dms-addon-critical` 은 S4 에서 만듭니다**(8-1).

### 2-2. 마스터 자원 예산 (8 vCPU / 32GB)

- 전제 [권고]: kubelet 예약 system 500m/1Gi, kube 500m/1Gi, eviction 500Mi → allocatable 약 **7000m / 29.5Gi**.

| 묶음 | CPU requests | 메모리 requests |
|---|---|---|
| 공통(etcd 1000 + apiserver 1000 + kcm·sched 300 + CNI 류 250 + Envoy 100 + PG 1000) | 3650m | 9.5Gi |
| + dms-api / controller / registry | +250 / +100 / +100m | +0.5 / +0.25 / +0.125Gi |
| **최악 마스터(셋이 한 곳에 몰림)** | **4100m (59%)** | **약 10.4Gi (35%)** |
| controller 2번째가 있는 마스터 | 3750m | 9.75Gi |

- 레지스트리가 1개라서 값은 v4 와 같습니다.
- 가장 빠듯한 자원은 CPU 지만 여유가 있습니다.
- limits 는 버스트용입니다. 모든 구성요소가 동시에 최대치를 쓸 때만 포화하고, 그때는 etcd·apiserver 의 requests 가중치가 이들을 보호합니다(추정).
- 마스터 1대를 유지보수하는 동안
  - 그 마스터의 PG 는 Pending 이 되어 인스턴스가 2개로 줄어듭니다.
  - api·controller 는 남은 2대로 옮겨집니다.
  - 레지스트리도 남은 마스터로 옮겨지고, 그동안 약 10~20초 공백이 생깁니다(7-6).

### 2-3. 노드 플랫폼 (4·5회차)

| 항목 | prod(4·5회차 답변) | 테스트베드 dmsk8s(실측 2026-11-02) | 설계 영향 |
|---|---|---|---|
| OS·아키텍처 | RHEL 9.6, x86_64, **SELinux 꺼짐 전제**(#48) | Ubuntu 24.04.4, 커널 6.8, amd64. **w1·w2 는 Rocky Linux 9(SELinux permissive)로 재구성**, cp1 은 Ubuntu 유지 [해석, #49] | SELinux 전제(아래), 이미지는 amd64 하나(7-5) |
| 런타임 | CRI-O(버전·공급원은 K1) | CRI-O 1.34.9 | containerd 경로 삭제, drop-in 파일 이름은 `99-…`(7-4·7-5) |
| CNI | Cilium, **KPR 없음 + kube-proxy 있음**(5회차 운영자 답) | Cilium 1.19.4, KPR false + kube-proxy(iptables), vxlan — prod 와 같은 모드 | 호스트 → ClusterIP 경로는 kube-proxy 가 처리(7-4, S6 가 실증), firewalld 영향(4-2) |
| K8s | prod 가 쓰는 minor 를 따름(1.34~1.36, #4) | v1.34.6, control-plane 1대 | 1.34 는 2026-10-27 EOL(정보). 테스트베드도 prod minor 로 맞춤(#4 [해석]) |

**SELinux 전제** [확정, 5회차]
- RHEL 계열(RHEL·Rocky 등 EL) **DMS 노드**는 SELinux 가 **enforcing 이 아니어야** 합니다(permissive 또는 disabled). 필수 설치 조건입니다.
  - DMS 노드 = 마스터 + DMS 워커. DMS 워커는 dms-agent DS 가 뜨는 노드이고, agent 는 nodeSelector 없이 NoExecute toleration 만 가지므로 NoSchedule taint 가 없는 워커 전부입니다(`50-agent-daemonset.yaml:86-88`, K3). 클러스터를 다른 팀과 나눠 쓰면 그 밖의 노드는 판정하지 않습니다.
  - [해석] 사용자 문장 「끄는걸」을 enforcing 이 아닌 상태(permissive 포함)로 읽었습니다.
- 사이트 조치(S0, RHEL 9 *Using SELinux* 「Changing SELinux states and modes」)
  - 권장: `/etc/selinux/config` 의 `SELINUX=permissive` 로 고정하고 `setenforce 0`.
  - 완전 비활성이 필요하면 `grubby --update-kernel ALL --args selinux=0` 후 재부팅합니다. Red Hat 은 프로덕션에서 `selinux=0` 을 권하지 않고, disabled 에서 되돌릴 때는 전체 relabel 이 일어납니다.
  - `setenforce 0` 만 하면 재부팅 때 되돌아갑니다. 설치기는 이 경우도 잡습니다(S2b).
  - Ubuntu 처럼 SELinux 가 없는 노드는 그대로 통과합니다.
- 확인: 설치기의 **SELinux 노드 프로브(S2b)** 가 DMS 노드를 재고, enforcing 이거나 재부팅하면 enforcing 이 될 노드가 하나라도 있으면 FAIL 하고 안내를 출력합니다(8-1). S1 노드 셸 체크리스트의 `getenforce` 와 대조합니다.
- 그래서 DMS 는 SELinux 를 위한 코드 변경(`spc_t`·`seLinuxOptions`)도, `<SHARED_DIR>/dms` 라벨 작업(`semanage`·`restorecon`·`virt_use_nfs`)도 하지 않습니다. ARCHITECTURE 문구도 바꾸지 않습니다. privileged 로 뜨는 것은 판정용 일회용 프로브 파드 하나뿐이고, DMS 워크로드는 그대로입니다.
- 근거(enforcing 이면 생기는 일, v5 조사): CRI-O 는 SELinux 가 켜진 호스트에서 컨테이너를 `container_t` + 파드별 MCS 로 띄우고(`crio_config_linux.go:32-34`), hostPath 는 kubelet 도 CRI-O 도 relabel 하지 않습니다(`host_path.go:232-237`, `crio_ccl.go:361-368`). 그래서 agent 의 `/proc/1` 읽기, 잡의 등록 스토리지 접근, 제어면의 `<SHARED_DIR>/dms` 접근마다 도메인·라벨·불리언 작업이 필요해집니다. 거부는 EACCES 로 돌아와 DMS 사유가 엉뚱하게 나옵니다(예: `artifact_base.py:120-129` 가 `artifact_base_group_writable` 로 보고, 추정).

---

## 3. 저장소 레이아웃

### 3-1. 공용 디렉터리 트리 (`<SHARED_DIR>`, 종류 무관)

| 경로 | 소유:모드 | 쓰는 주체 | 붙이는 방식 | 비고 |
|---|---|---|---|---|
| `<SHARED_DIR>` | 사이트 | — | 사이트 마운트(G1). 마운트포인트이거나 그 아래 디렉터리 | **DMS 「스토리지」로 등록 금지**(③ 가드). DMS 는 만들지 않음 |
| `…/dms` | root:root 0750 | — | — | 기존 규칙 750/700(711·755 금지). 센티널 `.dms-shared-id`(S0 가 무작위 값으로 씀) |
| `…/dms/artifacts` | root:root 0755 | api·controller·잡(uid 0) | hostPath `Directory` | `DMS_ARTIFACT_BASE`. allowlist 를 이 경로로 좁힘(지금은 `/REPLACE_SHARED_FS`, `patch-config.yaml:27`) |
| `…/dms/pgdata` | root:root 0700 | CNPG 인스턴스(uid 26) | 정적 hostPath PV(6-2) | 하위 `dms-pgdata-01..05/` 는 26:26 0700 + `.dms-pv-id`(값 = PV 이름). S0 가 만듦(hostPath 는 fsGroup 을 적용하지 않음). PGDATA 는 그 아래 `pgdata/`(`cnpg-pods.go:85`). `.retired/` 는 재활용 보관용 |
| `…/dms/backup/pg` | 26:26 0700 | pg_dump(uid 26) | hostPath `Directory` | 14일 보존. 비밀번호 해시와 봉인된 키 포함 |
| `…/dms/registry` | 65532:65532 0750 | dms-registry | hostPath `Directory` | 센티널 `.dms-registry-id`(S0 가 씀, 값 = `.dms-shared-id`, 7-3) |

- hostPath(볼륨·PV)는 하위 디렉터리를 직접 마운트합니다. 그래서 `…/dms` 가 0750 이어도 uid 26·65532 가 각자의 디렉터리에 접근할 수 있습니다.
- 모든 hostPath 는 `<SHARED_DIR>` 자체가 아니라 `<SHARED_DIR>/dms/…` 하위를 가리킵니다.
  - 마운트가 빠지면 하위 경로가 로컬에 없으므로 `Directory` 검사가 실패합니다(G2). 이 보장은 「아무도 마운트 없는 노드에서 그 경로를 만들지 않는다」는 전제에 기대므로, S0 초기화 스크립트와 6-2 재활용 런북은 마운트를 확인하고 상위 디렉터리를 만들지 않습니다.
  - 지금 base 는 마운트포인트 `/cephfs` 자체를 씁니다(`40-api.yaml:164-167`, `41-controller.yaml:90-93`). 그래서 마운트가 빠져도 빈 로컬 디렉터리로 검사를 통과합니다. ③ 에서 고칩니다.

### 3-2. 공용 스토리지 계약 (종류 무관, 4회차)

- DMS 는 공용 스토리지를 마운트하거나 프로비저닝하지 않고, 마운트 옵션도 관리하지 않습니다.
- 사이트가 아래 요구를 만족하는 마운트를 준비하고, 경로를 초기 셋업 env 로 줍니다.
  - values.env 의 **`SHARED_DIR`**
    - 절대 경로, `/` 가 아님, normpath, 모든 노드에서 같은 경로.
    - 마운트포인트이거나 그 아래 디렉터리입니다. 이 경로를 담은 마운트(`findmnt -n -o TARGET -T <SHARED_DIR>`)가 `/` 가 아니어야 합니다.
    - 문자는 `^/[A-Za-z0-9._/-]+$` 만 씁니다. render.sh 의 sed `|` 구분자와 YAML 에서 안전한 문자만 허용하기 위해서입니다.
- 확인은 S7 동작 프로브가 합니다. 프로브로 잴 수 없는 것 가운데 G8 은 운영자 답으로 충족했고(#50), G7 은 스토리지 담당이 확인합니다(N1).

| # | 요구 | 확인 | NFS 라면 |
|---|---|---|---|
| G1 | 모든 마스터·워커의 같은 호스트 경로 `SHARED_DIR` 에서 같은 스토리지가 보임 | S7 ①(교차 노드 nonce)로 판정. 포함 마운트 TARGET 이 `/` 이면 FAIL. SOURCE·FSTYPE 은 보고만(노드마다 서버 이름·IP 표기가 다를 수 있음) | export 클라이언트 = 모든 노드 |
| G2 | 마운트가 빠지면 fail-closed. DMS 는 모든 hostPath(볼륨·PV)를 하위 경로 `type: Directory` 로 둠(`DirectoryOrCreate` 금지). 부팅 때 재마운트는 노드 `/etc/fstab` 항목(#50). 워커는 kubelet 앞에 대기 가드를 둬도 됨. **마스터는 kubelet 을 막지 않음**(스토리지 장애 중 재부팅하면 etcd·apiserver 정족수를 잃을 수 있음, 추정). 유지보수 재마운트는 아래 「G2 보충」 | ③ 렌더 계약 | `_netdev,nofail` |
| G3 | 노드 간 가시성 **[권장, 필수 아님 — #47]**. 다른 노드가 만들거나 rename 한 이름이 곧바로 보이고(부정 캐시 없음), rename 으로 교체한 내용이 곧바로 보임. 미달이면 멀티 노드 rank 가 ENOENT 로 실패할 수 있고, api·controller 가 잡 산출물을 늦게 볼 수 있음. 등록 스토리지 마운트도 같은 조건(N7·N8) | S7 ②(새 이름·rename 교체). 실패는 **WARN** | `lookupcache=positive`(또는 `none`) 권장, `nocto` 없음. remount 로는 바꿀 수 없음(G2 보충) |
| G4 | root 보존. uid 0 이 만든 파일이 0 소유로 남고, root 가 chown·chmod 할 수 있고, 노드 root(kubelet)가 0700 디렉터리를 통과할 수 있음 | S7 ③ | `no_root_squash` |
| G5 | 숫자 uid/gid 를 그대로 저장·표시하고, 모든 노드에서 같음(0·26·65532) | S7 ④ | `sec=sys`. NFSv4 idmap 불일치 주의 |
| G6 | 권한 판단의 기준은 POSIX mode. NTFS/mixed 보안 스타일과 상속 ACL 은 금지 | `artifact_base.py:115-119` | UNIX 보안 스타일 |
| G7 | fsync 가 반환되면 데이터가 안정 저장소에 있음(쓰기 캐시는 비휘발이거나 write-through). PG 는 POSIX 의미를 지키는 FS 면 쓸 수 있음(PG 17 문서 18.2.2) | N1. S7 ⑤ 는 왕복만 확인 | 서버 `sync` export |
| G8 | 장애 때 I/O 를 오류로 버리지 않고 대기함(hard 마운트에 해당) | **충족**(#50, 5회차 운영자 답) | `hard` |
| G9 | POSIX 의미: rename 원자성, O_EXCL, 정확한 nlink, 심링크(`O_NOFOLLOW` → ELOOP), 디렉터리 fsync. `open_artifact_fd` 가 `nlink==1`·`O_NOFOLLOW` 에 기댐(`artifact_files.py:21-30`) | S7 ⑥ | — |
| G10 | RHEL 계열 DMS 노드는 SELinux 가 enforcing 이 아님(permissive 또는 disabled, 재부팅 뒤에도). 라벨 요구는 없음 | S2b SELinux 노드 프로브(2-3) | — |
| G11 | 노드 `fs.protected_hardlinks/symlinks=1`, 호스트 `/` 는 shared 전파(agent). 같은 스토리지를 마운트하는 **DMS 밖 호스트**도 이 sysctl 대상이고, 그 목록을 받음 | `deploy/README.md:252-258` | — |
| G12 | 용량·쿼터는 사이트 몫. hostPath PV 크기는 명목값 | 3-3 | — |

- **G2 보충: 재마운트·유지보수 절차**(사이트와 합의, N1)
  - 사이트가 한 노드의 공용 스토리지를 내리기 전에, 그 노드의 DMS 상태 파드(api·controller·registry·CNPG 인스턴스·pg_dump)를 먼저 비웁니다(cordon 후 삭제). PG 는 그 노드가 primary 면 switchover 한 뒤 인스턴스 파드를 지웁니다.
  - 다시 마운트한 뒤에 uncordon 해서 파드를 되돌립니다.
  - 이유: hostPath bind 는 파드가 시작될 때의 마운트를 붙잡습니다. NFS 는 sharecache 때문에 옛 마운트에 살아 있는 참조가 있으면 새 마운트도 옛 옵션을 물려받을 수 있어서(추정, nfs(5) sharecache), 옵션(예: `lookupcache`)을 바꾸려고 내렸다 올려도 효과 없이 끝날 수 있습니다.
- **v4 에서 지운 조건**
  - export 옵션 세부와 노드 fstab 문자열
  - SC `mountOptions` = fstab. DMS 가 다시 마운트하지 않으므로 sharecache 문제가 생기지 않습니다.
  - csi-nfs-node 마스터 배치
  - NFS 서버·share 값
  - preflight 의 NFS 옵션 판정(nfs4·hard·lookupcache). S7 동작 프로브로 대체했습니다.
- **출처 정정**: v4 는 testbed `group_vars/all.yml:77-82` 의 `lookupcache=positive` 를 「fstab 값」으로 인용했습니다. 실제로는 PV 마운트(`k8s_pv_mount_options`)용 값이고, mgmt 마운트(`all.yml:60`)에는 이 옵션이 없습니다.

### 3-3. 용량 (추정)

| 대상 | 추정 | 근거 |
|---|---|---|
| artifacts | 운영 패턴에 따름(보존 1500일) | 미정 |
| pgdata | 인스턴스당 DB(테스트베드 65MB) + WAL ≈ 수 GiB, × 3. 여분 PV 2개는 비어 있음. Released 상태 PV 와 `.retired` 보존분이 쌓이므로 정리 대상 | PG 검토 |
| backup/pg | 336개 × 수십 MB ≈ 수~십수 GiB | — |
| registry | DMS 태그당 약 45MiB(실측, 61태그 = 2.7GiB). 애드온은 amd64 하나만. 쿼터 예: 200GiB [권고] | REG 제품 §1, 7-5 |

### 3-4. 로컬 디스크에 남는 것

- etcd: fsync p99 < 10ms 가 필요하므로 공용 스토리지나 NAS 데이터스토어에 두지 않습니다.
- CRI 저장소와 `/var/lib/kubelet`: overlayfs upper 는 NFS 에 둘 수 없습니다. 다른 네트워크 FS 도 대체로 안 됩니다(추정).
- 고정한 레지스트리 이미지, `/etc/kubernetes`, 파드 로그
- **PG 데이터는 어느 환경에서도 로컬 디스크에 두지 않습니다**(#31). 테스트베드 로컬 디스크는 T6' 비교 측정용 일회용 대상으로만 쓰고, 렌더 오버레이 밖에서 만듭니다.

---

## 4. 네트워크·접근

### 4-1. 이름·주소

| 대상 | 주소 | 푸는 방법 | 상태 |
|---|---|---|---|
| 포탈 | `https://<포탈 FQDN>` | 사내 DNS → VIP-A. 개통 전에는 `curl --resolve` | [확정], 이름은 U5 |
| apiserver | `<API FQDN 또는 VIP-B>:6443` | 사내 DNS 또는 IP | W1 |
| **레지스트리** | **`<REGISTRY_CLUSTER_IP>:5000`**(서비스 CIDR 하단의 정적 대역, 예: `….0.50`) | DNS 없음. 서비스 CIDR 은 사내망에 광고되지 않음(#43) | [권고] 7-4 |
| PG | `dms-pg-rw.dms.svc:5432` | CoreDNS | [설계] |
| 에이전트 → api | `http://dms-api:8080` | CoreDNS | 기존 |

### 4-2. 흐름·포트 (방화벽 참고 목록)

- 방화벽은 사용자가 처리하고, 필요할 때 정보를 요청합니다(#44). 아래는 그때 드릴 기준표입니다.

| 출발 → 도착 | 포트 | 비고 |
|---|---|---|
| 사용자망 → VIP-A | 443 | 확정 |
| Avi SE 대역 → 마스터 ×3 | 443(헬스 모니터 포함) | **마스터 :443 은 SE 대역에서만 받음**(V4) |
| 노드·운영자·설치 호스트 → VIP-B → 마스터 | 6443 | 설치 호스트의 port-forward(S4 확인, S5 digest 대조)도 이 경로 |
| apiserver → 전 노드 kubelet | 10250 | — |
| 마스터 ↔ 마스터 | 2379-2380 | etcd |
| apiserver → 워커 파드 | 9443(CNPG), 8443(Volcano, 실측), 기타 webhook | #7. 터널 모드면 Cilium 터널 안으로 감 |
| 전 노드 ↔ 전 노드(마스터 포함) | Cilium vxlan 8472/udp, health 4240/tcp, ICMP(터널 모드일 때) | **마스터도 워크로드를 돌리므로 필수**(K6) |
| 파드망 내부 | Envoy → EG 18000, Envoy → api 8080, api·controller·pg_dump → PG 5432, PG ↔ PG 5432, 오퍼레이터 → 인스턴스 8000, 노드·파드 → 레지스트리 5000 | NetworkPolicy 가 있으면 허용 필요(K11) |
| 워커 → PV 백엔드·메일 릴레이·대상 API 6443, 워커 ↔ 워커 MPI | — | 기존 |
| 인터넷 노드(지정된 빌드 노드) → 인터넷·프록시 443 / → 레지스트리 ClusterIP 5000 / → 마스터 ×3 파일 전달(ssh 등) | — | 이미지 반입(7-5)·포탈 빌드. 마스터 전달은 레지스트리 자기 이미지를 처음 넣을 때만 |

- 공용 스토리지 트래픽은 사이트가 이미 마운트해 쓰는 경로라서, DMS 가 새로 여는 흐름이 아닙니다.
- RHEL firewalld
  - 기본 zone 은 public 이고, zone 사이 포워딩은 기본으로 거부합니다.
  - prod 는 KPR 이 없으므로(#41) eBPF host routing(KPR + BPF masquerade)이 아니고, 파드 트래픽이 netfilter FORWARD 를 지나 firewalld 의 영향을 받습니다(`tuning.rst:171-200`). 테스트베드 Rocky 워커에서 확인합니다(T-SE).
- 없어지는 흐름: 워커 → 외부 PG 5432, MetalLB, 80.

### 4-3. 입구 세부 [설계]

- **443 바인드** [권고]: hostNetwork 로 컨테이너가 443 에 직접 바인드합니다.
  - EnvoyProxy `useListenerPortAsContainerPort: true` 로 포트 이동을 끕니다. 기본값은 1023 이하 포트를 비특권 포트로 옮깁니다(`eg_envoyproxy_types.go:577-582`).
  - DS patch 에 `hostNetwork: true` 와 **`dnsPolicy: ClusterFirstWithHostNet`** 을 넣습니다 [설계].
    - EG v1.9 부트스트랩의 xDS 주소가 서비스 DNS 이름 `envoy-gateway.<ns>.svc.<domain>.:18000` 입니다(EG `resource.go:123`, `bootstrap.go:31-33,42-43`). 그래서 hostNetwork 파드도 클러스터 DNS 를 써야 합니다.
    - 이 DNS·xDS 연결은 레지스트리 pull 과 같은 「호스트 → ClusterIP」 경로라서 S6 가 함께 증명합니다.
  - EG 는 Envoy 를 **비 root(65532)** 로 띄웁니다(`internal_infrastructure_kubernetes_proxy_resource.go:520-522`). k8s 는 ambient capability 를 설정하지 않으므로 `NET_BIND_SERVICE` 만 더해서는 bind 가 실패합니다(kubernetes#56374). 그래서 다음 중 하나가 필요합니다.
    - **(i) [권고]** 마스터 sysctl 을 바꿉니다. Envoy 는 비 root 로 남습니다.
      - S0 IaC 로 `/etc/sysctl.d/90-dms-envoy.conf` 에 `net.ipv4.ip_unprivileged_port_start = 443` 을 쓰고 `sysctl --system` 을 실행합니다.
      - 확인: `sysctl -n` 값이 443 이어야 합니다.
      - 이 값은 netns 별이라 파드 netns 에는 영향이 없습니다. 또 hostNetwork 파드에는 net.* sysctl 을 줄 수 없어서 호스트에 설정해야 합니다(k8s sysctl 문서).
      - 부작용: 마스터 호스트의 비 root 프로세스도 443~1023 을 bind 할 수 있게 됩니다. 마스터에 일반 사용자가 없다는 전제입니다.
    - (ii) EnvoyProxy `container.securityContext: {runAsUser: 0, runAsNonRoot: false, capabilities: {add: [NET_BIND_SERVICE]}}`. strategic merge 로 덮어쓸 수 있습니다(같은 파일 `:561-566`).
  - Envoy 파드가 뜨는 네임스페이스(기본 `envoy-gateway-system`)는 hostNetwork 때문에 PSA privileged 가 필요합니다(S1 승인).
  - 마스터의 :443 과 Envoy 보조 포트가 비어 있어야 합니다(K6).
  - 대안인 hostPort 443→10443 은 CNI 가 hostPort 를 지원할 때만 쓸 수 있습니다. Cilium 의 자체 hostPort 지원은 KPR 기능이라 prod(KPR 없음)에서는 꺼져 있고, `cni.chainingMode=portmap` 체이닝이 있을 때만 쓸 수 있습니다(`kubeproxy-free.rst:1599-1606,1621`). K1 에서 portmap 체이닝 여부를 확인합니다.
- **PROXY·헤더 계약**(근거는 실측 `design/ppexp/`)
  - CTP `proxyProtocol` 은 필수입니다. 컷오버 중에만 optional 로 둡니다.
  - `earlyRequestHeaders.remove: [X-Real-IP]`
  - clientIPDetection 은 두지 않고, 앱은 XFF 마지막 항목을 읽습니다.
- **레지스트리는 Avi·PROXY 와 무관합니다.**
- **PROXY 를 못 쓰면** 원 IP 자리에 SE IP 가 남습니다.
  - 이때는 원 IP 별 경보를 끄고, Avi VS 로그를 원 IP 출처로 씁니다(V5).
  - Avi 가 최신 버전이므로(#42) 이 경우는 설정 화면에 옵션이 보이지 않을 때뿐입니다.

---

## 5. 인증·로그인·감사

| 장치 | 값 | 넘치면 | 상태 |
|---|---|---|---|
| 아이디 축 감속 | 실패 10회/60초 | 429 `login_rate_limited` | 확정 |
| 아이디별 총 시도(성공 포함) | 값 미정(자동화·e2e 로그인 빈도를 잰 뒤) | 429 | 확정(값은 U3) |
| 인증번호 | 6자리, 5분, 코드당 5회, 아이디별 10회/24h | 429 | 확정 |
| 인증번호 전체 오답 | **1000회/1시간**. 발동하면 셀프 가입·재설정이 최대 1시간 429(맞는 코드도 거절) | 429, 새 사유 코드(`verification_client_locked` 대체) | 확정 |
| scrypt 슬롯 | 2개, 최대 대기 5초 | 503 `login_busy` | 슬롯 확정 / **5초 [권고]** |
| 전체 스위치 | 기본 꺼짐. 켜면 예약형 토큰 버킷 L/W, 최대 대기 M | 예상 대기 > M 이면 503 `login_busy` + Retry-After | 확정 / W=60·M=15 [권고] |
| 경보 | 실패, 전체 시도, 원 IP 별. (종류, 키)마다 창 하나에 한 번 | 이벤트만 | 확정 / 임계·수신은 U3 |
| 인증 메일 | IP 축 제거. 수신자 축·전체 300/10분·동시 8 은 유지 | 기존 | 확정 |
| 비밀번호 규칙 | 없음 | — | 확정 |

- **처리 순서**(v3 2(마)를 60→1000, 0s→3600s 로 바꿔 읽음)
  1. 아이디 축
  2. 봉인 해제(422)
  3. 전체 줄(켜졌을 때만)
  4. 슬롯 직접 획득(`run_sync` 에 limiter 를 넘기지 않음)
  5. 슬롯 안에서 아이디 축 재확인
  6. 검증
  7. 401 이 아니면 토큰 환불

  `/healthz` 는 async 로 바꿉니다.
- **운영자 구제(#18)**
  - 관리자 비밀번호 재설정 라우트
    - `accounts_session_required` 를 거칩니다.
    - 봉인 전송을 씁니다(`postWithSealedPassword` ↔ `_password_from`).
    - 특권 계정은 `guard_privileged_account` 로 보호합니다.
    - 감사 행을 남기고, 저장은 기존 `accounts.set_password` 를 씁니다(`repositories/accounts.py:107`).
  - 잠금 해제는 아이디별(`verification_failures`)과 전체(메모리) 둘 다 둡니다.
  - 보안 이벤트 화면 하나에 이벤트 목록과 해제 버튼을 둡니다.
- **선택 4종 메모**
  - 세션 무효화: `password_changed_at`(또는 세션 epoch) 열을 **CREATE TABLE 과 `_ensure_columns` 양쪽**에 넣고 세션 쿠키와 비교합니다. Bearer 토큰은 대상이 아닙니다.
  - 완료 메일 2종도 기존 메일 상한을 거칩니다.
- **원 IP 기록**
  - 이벤트 payload 에 `{client_ip, peer}` 를 넣습니다(스키마 변경 없음).
  - Envoy 접근 로그에 원 IP 필드를 둡니다(K15).
  - 보존 범위는 보안 S2 에서 정합니다.
- **상태**
  - 카운터는 프로세스 메모리에 있고 replicas 1 이 전제입니다. 재시작하면 비워집니다.
  - 스위치를 켜는 방법은 U4 에서 정합니다.
  - 사유 코드는 `reasonCodes.json` 과 `api.ts` 양쪽에 등록합니다.

---

## 6. DB (CNPG)

### 6-1. 구성 [설계]

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata: {name: dms-pg, namespace: dms}
spec:
  instances: 3
  imageName: <REG>/postgresql:17.x-minimal-trixie@sha256:<amd64 매니페스트 digest>   # 7-5
  imagePullPolicy: IfNotPresent
  smartShutdownTimeout: 0
  enablePDB: true
  postgresql:
    synchronous: {method: any, number: 1, dataDurability: preferred}   # #28 확정
    parameters: {shared_buffers: 1GB}
  bootstrap: {initdb: {database: dms, owner: dms, secret: {name: dms-pg-app}}}
  storage: {storageClass: dms-pg-shared, size: 20Gi, resizeInUseVolumes: false}   # hostPath PV 는 크기를 강제하지 않음. size == PV capacity(⑤ 계약)
  affinity:
    podAntiAffinityType: required
    nodeSelector: {node-role.kubernetes.io/control-plane: ""}   # 키는 K3 실측
    tolerations: [{key: node-role.kubernetes.io/control-plane, operator: Exists, effect: NoSchedule}]
  resources: {requests: {cpu: "1", memory: 4Gi}, limits: {memory: 4Gi}}
---
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata: {name: dms-pg-shared}
provisioner: kubernetes.io/no-provisioner
reclaimPolicy: Retain
volumeBindingMode: Immediate
---
apiVersion: v1
kind: PersistentVolume
metadata: {name: dms-pgdata-01, labels: {app.kubernetes.io/managed-by: dms-install, dms.io/pg-cluster: dms-pg}}
spec:
  capacity: {storage: 20Gi}
  accessModes: [ReadWriteOnce]
  persistentVolumeReclaimPolicy: Retain
  storageClassName: dms-pg-shared
  hostPath: {path: <SHARED_DIR>/dms/pgdata/dms-pgdata-01, type: Directory}
# dms-pgdata-02 ~ 05 동일. nodeAffinity·claimRef 없음. 디렉터리는 S0 가 26:26 0700 + .dms-pv-id 로 미리 만듦
```

- **DMS 가 오퍼레이터를 설치할 때(A 경로) 피해야 할 airgap 함정 셋**
  1. `image` 와 env `OPERATOR_IMAGE_NAME` 을 **둘 다** `<REG>/…@digest` 로 바꿉니다.
  2. `Always` 를 `IfNotPresent` 로 바꿉니다.
  3. `imageName` 을 명시합니다. 생략하면 ghcr.io 에서 18.x 를 받으려 합니다.
  - 그 밖에 replicas 2, 워커 배치, `dms-addon-critical` 을 줍니다.
- **동기 복제** [확정, 4회차]: `ANY 1 preferred`
  - standby 가 빠지면 요구 수가 자동으로 줄어서 쓰기가 멈추지 않습니다(`pgop/dl/cnpg_repl.txt:267-300`).
  - `dataDurability: required` 는 쓰지 않습니다. 기본값이 required 인데(`cnpg_repl.txt:224-226`), standby 가 없으면 COMMIT 이 무한 대기하는 것을 실측했습니다.
  - 커밋 지연은 T6 에서 잽니다.

### 6-2. PG 볼륨: 정적 hostPath PV 풀 [확정, 5회차]

- **방식**
  - 전용 SC `dms-pg-shared`(no-provisioner, Immediate, Retain)를 둡니다.
  - PV `dms-pgdata-01..05` 를 만듭니다(인스턴스 3 + 여분 2).
  - 각 PV 는 `<SHARED_DIR>/dms/pgdata/dms-pgdata-0N` 하위를 `type: Directory` 로 가리키고, nodeAffinity·claimRef 가 없습니다.
- **고른 이유**
  - 스토리지 종류에 따른 분기가 없습니다. 새 애드온·CSI·이미지가 하나도 필요 없습니다.
  - artifacts·registry·backup 과 같은 hostPath 방식입니다. 사이트가 줄 값은 `SHARED_DIR` 하나입니다(`NFS_SERVER`/`NFS_SHARE` 불필요).
  - 스토리지가 NFS 여도 노드가 이미 마운트한 경로를 그대로 씁니다. 그래서 csi-driver-nfs 를 쓸 이유가 없습니다(#32).
  - PV 에 nodeAffinity 가 없어서 인스턴스가 어느 마스터에서든 같은 PGDATA 로 뜹니다. 마스터를 교체하면 재복제 없이 새 노드에서 기동합니다(T6 에서 실증).
- **채택하지 않은 후보**(2026-11 기준)
  - local-path-provisioner `sharedFileSystemPath`(v0.0.37)
    - helper 파드가 hostPath 로 `mkdir 0777`·`rm -rf` 를 합니다.
    - **생성하는 PV 가 hostPath `DirectoryOrCreate` 입니다**(`lpp-provisioner.go:55,965-971`. shared 모드면 nodeAffinity 는 nil, `:519-524`). 그래서 마운트가 빠진 노드에서 로컬 디렉터리를 만들어 G2(fail-closed)를 깹니다. 쓰려면 포크·패치가 필요합니다.
    - 이미지 2개와 감지 상태 기계 행이 하나 더 생깁니다.
    - 얻는 것은 여분 관리가 사라지는 것뿐입니다.
  - kubevirt hostpath-provisioner: 노드 로컬 설계라서 PV 가 생성 노드에 묶입니다.
  - `local` PV: nodeAffinity 가 필수이고, 공용 FS 위에서 재귀 chown·relabel 이 생깁니다.
  - csi-driver-host-path: 테스트용 드라이버입니다.
  - csi-driver-nfs
    - NFS 전용이고 서버·share 값이 필요합니다.
    - 같은 export 를 두 번 마운트하게 되고, 재귀 chown 을 합니다.
- **CNPG 거동**
  - CNPG 1.30.0 부터 인스턴스 번호를 재사용합니다. 가장 낮은 빈 번호를 쓰므로 PVC 이름은 늘 `dms-pg-1..3` 입니다(`cnpg-rn-1.30.md:349-356`). 이것은 1.30 이상에만 맞습니다. C 경로에서 남이 설치한 1.25~1.29 면 번호가 늘어나 PVC 이름이 바뀌지만, 전용 SC 매칭이라 풀 동작은 같습니다.
  - 새 PVC 가 생기는 경우는 인스턴스를 볼륨과 함께 지우고 다시 만들 때뿐입니다.
    - `delete pvc/X pod/X`
    - `kubectl cnpg destroy`
    - 볼륨 축소 절차의 임시 +1
    - `reusePVC: false`
  - drain·재시작은 같은 PVC 를 다시 씁니다.
  - 새 PVC 는 Available PV 하나를 가져가고, 옛 PV 는 Released 로 남습니다. Released PV 는 claimRef 에 옛 UID 가 남아 있어서 같은 이름의 새 PVC 에도 붙지 않습니다.
  - 그래서 claimRef 로 미리 묶지 않고 전용 SC 매칭만 씁니다. 미리 묶으면 재생성 때 자가 치유가 안 됩니다.
  - 여분 2개가 「임시 +1 과 교체 1」을 사람 개입 없이 흡수합니다.
  - 여분이 0개면 새 PVC 가 Pending 이 될 뿐 데이터 위험은 없습니다.
  - **Cluster 삭제**: CNPG 는 PVC 에 Cluster ownerReference 를 달아 Cluster 와 함께 PVC 를 지울 것으로 봅니다(추정, 내려받은 `cnpg-pvc-build.go` 에서는 확인하지 못함, T6 확인). 그러면 PV 3개가 Released 가 되고 여분은 2개뿐이라, 새 Cluster 의 셋째 인스턴스는 런북을 돌린 뒤에 뜹니다. 옛 PGDATA 는 지우지 않고 `.retired` 로 옮겨 보관합니다(덤프 밖의 유일한 사본).
  - **크기**: `storage.size` 를 PV capacity 보다 크게 바꾸면 맞는 PV 가 없어 새 PVC 가 Pending 이 됩니다. ⑤ 계약 테스트가 `storage.size == PV capacity` 를 고정합니다.
- **Released 재활용 런북** [설계]
  0. 실행하는 노드에서 `[ "$(findmnt -n -o TARGET -T <SHARED_DIR>)" != / ]` 이고 `<SHARED_DIR>/dms/.dms-shared-id` 가 있는지 확인합니다. 아니면 중단합니다. 마운트가 빠진 노드에서 디렉터리를 만들면 그 노드의 `Directory` 검사가 통과해 PG 가 로컬 디스크에 씁니다(#31 의 조용한 위반).
  1. 그 PV 를 쓰는 파드가 없는지 확인합니다.
  2. 같은 FS 안에서 옛 디렉터리를 보관 위치로 옮기고 빈 디렉터리를 새로 만듭니다. 상위 디렉터리는 만들지 않습니다(`install -d`·`mkdir -p` 금지).
     - `mv <SHARED_DIR>/dms/pgdata/dms-pgdata-0N <SHARED_DIR>/dms/pgdata/.retired/dms-pgdata-0N-<UTC>`
     - `mkdir -m 0700 <SHARED_DIR>/dms/pgdata/dms-pgdata-0N && chown 26:26 <SHARED_DIR>/dms/pgdata/dms-pgdata-0N`
     - `.dms-pv-id`(값 = PV 이름)를 다시 씁니다.
     - 옛 PGDATA 가 남아 있으면 join(pg_basebackup)이 실패할 것으로 봅니다(추정, T6 확인).
  3. claimRef 를 지워 Available 로 되돌립니다(`k8s-pv.md:472`).
     - `kubectl --context C patch pv dms-pgdata-0N --type=json -p '[{"op":"remove","path":"/spec/claimRef"}]'`
- **상류 비권장을 감수함**(5회차 확정)
  - CNPG 는 사전 프로비저닝을 피하라고 권합니다. HA·자가 치유·선언 모델에 영향을 준다는 이유입니다(`cnpg-storage.md:408-424`).
  - k8s 문서는 hostPath PV 를 「single node testing only」라고 적습니다(`k8s-pv.md:503-505`). 이 경고는 노드마다 로컬 디스크가 다르다는 전제입니다. 우리 경로는 모든 노드에서 같은 공용 FS 입니다.
  - 대응
    - 여분 2개
    - 인스턴스 번호 재사용
    - S10·S12 의 「Available 여분 < 1 → WARN」과 경로 대조 프로브
    - 재활용 런북
    - S7 실측
- **소유·보안**
  - hostPath 는 fsGroup 을 적용하지 않습니다(`host_path.go:232-255`).
    - 그래서 S0 가 `dms-pgdata-0N` 을 26:26 0700 으로 미리 만듭니다. 그러려면 G4·G5 가 필요합니다.
    - 대신 CNPG 의 재귀 chown 이 없어집니다.
  - CNPG 파드에는 PVC 만 보이므로 PSA 수준이 바뀌지 않습니다. baseline 이 금지하는 것은 `spec.volumes[*].hostPath` 입니다(`k8s-pss.md:139-149`).
  - 대신 보안 경계가 바뀝니다. 「누가 PV 를 만드는가(설치자)」와 「누가 `dms-pg-shared` PVC 를 만드는가」가 경계입니다.
    - [권고, 선택] ValidatingAdmissionPolicy(GA 1.30) 하나로 이 SC 의 PVC 를 ns `dms` 로 제한합니다.
  - access mode 는 쓰기 보호가 아니고, RWOP 는 CSI 전용입니다(`k8s-pv.md:643,664`). 이중 마운트 방지는 6-6 운영 규칙이 맡습니다.
  - `resizeInUseVolumes: false`(`cnpg-api.md:3111`): 확장할 수 없는 SC 에서 크기를 바꾸려는 시도를 막습니다. 크기는 명목값입니다.

### 6-3. DMS 접속 [설계]

```
postgresql://dms:<PW>@dms-pg-rw.dms.svc:5432/dms?sslmode=require&target_session_attrs=read-write
  &keepalives=1&keepalives_idle=10&keepalives_interval=5&keepalives_count=3&tcp_user_timeout=15000
```

- URL 은 `dms-secrets` 의 `DMS_DATABASE_URL` 에 직접 조립합니다. CNPG `-app` 시크릿의 `uri` 에는 파라미터를 붙일 수 없기 때문입니다.
- `<PW>` 는 `dms-pg-app` 과 같은 값입니다. `openssl rand -hex 24` 로 만들면 URL 인코딩이 필요 없습니다.
- install 게이트가 host·DB·사용자·비밀번호가 일치하는지 메모리에서만 확인합니다.
- `connect_timeout` 기본 5초는 그대로 두므로 코드 변경이 없습니다.

| 장애조치 상황(추정, PG 검토 4-2) | 시간 |
|---|---|
| switchover, PG 파드 재시작 | 수 초~십수 초 동안 500 |
| primary 가 있던 마스터 사망 | 약 1~1.5분 |
| dms-api 가 있던 마스터 사망 | 약 1.5~2.5분. **새 마스터에 `dms` 이미지가 캐시돼 있다는 전제**(S11 과 릴리스 뒤에 마스터 3대에서 사전 pull). 캐시가 없고 레지스트리도 같은 마스터에 있었으면, 레지스트리가 다시 뜰 때까지 pull 이 백오프해서 수 분 더 걸릴 수 있음(추정, 7-6) |

- keepalive 와 `tcp_user_timeout` 이 없으면 API 가 최대 약 15분 매달립니다(T6' 에서 실증).

### 6-4. 백업·복구 (PITR 없음)

| 층 | 방법 | 막는 것 |
|---|---|---|
| 동기 복제 | 6-1 | 노드·VM·파드 장애 1건 |
| pg_dump | 매시간 `17 * * * *`, `-Fc`, `-rw` 에서 읽음, 14일 보존 → `backup/pg/` | 잘못된 마이그레이션, 삭제, 재구축 |
| **공용 스토리지 밖 사본** | 덤프 + `dms-secrets`·`dms-pg-app`(암호화). 보관처는 U6·S5 | **공용 스토리지 손실**(복제본 3개와 덤프가 같은 스토리지에 있음) |

- 복구: initdb 로 새 Cluster 를 만든 뒤 `pg_restore` 합니다. 첫 이관에는 CNPG 「initdb import」를 씁니다.
- 복원 조건
  - 원래의 `DMS_SESSION_SECRET` 이 있어야 합니다. 없으면 메일 키와 k8s 토큰을 다시 넣어야 합니다(`mail_config.py:128,205`).
  - DB 와 아티팩트의 시점이 어긋나면 404 나 고아 디렉터리가 생깁니다(`artifact_trash.py:21-22`).

### 6-5. 업그레이드 운영 (연 2회 이상)

| 경우 | 절차 |
|---|---|
| CNPG minor | 아래 「CNPG minor 절차」 |
| PG minor | `imageName` digest 교체 → 롤링 + switchover |
| PG major | 별도 설계 |
| K8s minor | 신·구 K8s 를 모두 지원하는 버전으로 애드온을 먼저 올림. 예: 1.36 에는 Volcano v1.16 필요. CRI-O minor 도 함께 올림(CRI-O minor = K8s minor) |
| 남이 설치한 CNPG(C) | 업그레이드는 소유자 몫. DMS 는 실행할 때마다 EOL·지원 범위를 경고 |
| 롤백 | 다운그레이드에 기대지 않음. 새 Cluster + pg_restore |

**CNPG minor 절차**
1. `versions.env`·`images.lock`·`addons/cnpg/<v>` 를 커밋합니다.
2. 인터넷 노드가 새 이미지를 레지스트리에 push 합니다(7-5 ③).
3. 테스트베드에서 리허설합니다.
4. `install.sh --context C --upgrade-addon cnpg` 를 실행합니다. 이 명령이 하는 일은 다음과 같습니다.
   - K8s 지원 범위 확인
   - **minor 는 하나씩**(건너뛰기 금지)
   - 직전 덤프
   - SSA → 롤아웃 → switchover 완료 대기(그동안 수 초간 500)
- `kubectl cnpg` 바이너리도 인터넷 노드가 받아 설치 호스트로 옮깁니다(수단은 K8).
- 지원 창: 1.30.x 는 K8s 1.34~1.36 을 지원하고 EOL 은 약 2026-12 입니다. 구현 시점의 최신 minor 로 고정합니다(지금은 1.30.1).

### 6-6. 마스터 운영 규칙 (이중 마운트 방지)

- VM 전원이 꺼진 것을 확인하기 전에는 다음 셋을 하지 않습니다.
  - 파드 강제 삭제
  - `out-of-service` taint
  - Node 객체 삭제
- 마스터를 교체할 때는 옛 VM 을 끈 다음에 Node 를 지웁니다. 공용 경로 hostPath PV 에서는 k8s 가 노드 간 RWO 를 강제하지 않기 때문입니다(PG 검토 6절, `k8s-pv.md:643,664`).
- drain 은 한 대씩 하고, 다음 대로 넘어가기 전에 Cluster 가 healthy 인지 확인합니다.

---

## 7. 이미지 레지스트리

### 7-1. 제품 [확정: 1개]: CNCF Distribution v3.1.2, 오퍼레이터 없이

| 후보 | 판단 |
|---|---|
| **Distribution v3.1.2**(권고) | `registry:3.1.2@sha256:d4032d4c…`, amd64 19.5MiB, CNCF, Apache-2.0. Redis·DB 같은 추가 구성요소가 없음 |
| Zot v2.1.23 | 다중화에 공유 캐시와 S3 가 필요(`articles/scaleout`)해서 이점이 없음. 온라인 GC 가 꼭 필요할 때만 고려 |
| Harbor(Helm HA 포함) | 프로젝트 경로 접두와 push 인증이 필요해서 DMS 코드와 충돌(`BACKLOG.md:353-358`). HA 구성에는 PG·Redis HA 가 더 붙음 |
| **오퍼레이터**(4회차 질문) | 쓰지 않음. 근거는 표 아래 |

- 오퍼레이터를 쓰지 않는 근거
  - goharbor/harbor-operator 는 아카이브됐습니다. 마지막 정식 릴리스는 v1.3.0(2022-07)입니다.
  - OpenShift Image Registry Operator 는 OpenShift 전용입니다.
  - Distribution 전용 오퍼레이터는 없습니다.
  - Quay 는 오브젝트 스토리지와 조직 경로가 필요합니다(추정).
- 부수 효과: 테스트베드의 `registry:2`(2.8.3, 2023-10 이후 갱신 없음)도 함께 바뀝니다.
- **개수는 1개입니다**(#46). HA(2개 이상)는 나중에 다시 볼 수 있습니다. 그때 알아 둘 것
  - Distribution 은 오퍼레이터 없이 다중화할 수 있습니다(공식 요건: 같은 저장소, 같은 `http.secret`, Redis 는 공유하거나 쓰지 않음, `deploying.md:343-366`).
  - 전제는 G3 입니다. 공식 문서는 filesystem 드라이버 공유를 「같은 머신」에서만 보증하고(같은 곳), NFS 의 부정 캐시 때문에 `blob unknown` 이 난 사례가 있습니다(Red Hat BZ 1873586). 그래서 다시 볼 때는 S7 ② 가 PASS 인 사이트에서만 늘립니다.

### 7-2. DMS 가 강제하는 조건

| 조건 | 근거 |
|---|---|
| 평문 HTTP | `registry.py:161-162,236,256`, `build_manifests.py:125` |
| 익명 접근만 | `registry.py:17-20` |
| `host:port` 만(경로 접두 불가) | `BACKLOG.md:353-358` |
| 매니페스트 DELETE(405 → 409 `registry_delete_disabled`) | `routes_registry.py:77-104` |
| 태그 목록 Link 페이지 처리 | `registry.py:138-220` |
| 클러스터마다 레지스트리 하나 | 「사용 중」 보호가 자기 클러스터만 봄 |

- 포탈은 `dms`·`dms-agent`·`dms-mpifileutils` 만 다룹니다(`repositories/builds.py:7`). 그래서 애드온 이미지는 포탈 화면에서 지워지지 않습니다.

### 7-3. 구성 [설계]

- **ConfigMap → `/etc/distribution/config.yml`**
  - `rootdirectory: /var/lib/registry`
  - `delete.enabled: true`
  - `uploadpurging` 168h/24h
  - `readonly.enabled: false`
  - **cache 없음**: inmemory 캐시는 GC 뒤에 낡은 「있음」을 돌려줄 수 있습니다. Redis 도 필요 없습니다.
  - `http.addr :5000`, `health.storagedriver` 켬
  - 이미지에 든 기본 설정(debug, :5001)은 이 파일로 덮어씁니다.
- **Secret `dms-registry-http`**(`REGISTRY_HTTP_SECRET`)
  - 설치할 때 없으면 무작위로 만들고 출력하지 않습니다.
  - `secretKeyRef` 로 넣습니다. 롤링 중 잠깐 두 파드가 함께 떠도 업로드 세션이 이어집니다(`configuration.md:896`).
- **Deployment `dms-registry`**(ns `dms`)
  - **개수·배치**: replicas 1, control-plane nodeSelector + toleration, not-ready·unreachable `tolerationSeconds: 30`.
  - **롤링**: RollingUpdate `maxSurge: 1, maxUnavailable: 0`. 새 파드가 Ready 가 된 뒤 옛 파드를 내립니다. Recreate 를 쓰면 업그레이드 때 새 이미지를 받을 곳이 없어 교착합니다.
    - 교체는 빌드·push 가 없을 때 합니다. 두 파드가 겹치는 동안 Service 가 요청을 둘로 나누므로, S7 ② 가 WARN 인 사이트에서는 그 사이 push 가 `blob unknown` 으로 실패할 수 있습니다(7-1 의 BZ 1873586 과 같은 구조). 이미 있는 이미지의 pull 은 영향이 없습니다.
  - **저장소**: hostPath `<SHARED_DIR>/dms/registry`, `type: Directory`.
  - **이미지**: `<REG>/registry:3.1.2`(**태그 ref**, 7-5), `IfNotPresent`.
  - **readiness**: HTTP `GET /v2/_catalog?n=1`(timeout 5초, period 10초, failure 3)
    - 저장소를 실제로 만지므로 마운트가 멈추면 Ready 가 풀립니다.
    - `/v2/` 는 저장소 health 검사가 타임아웃 없이 동기로 돌아서, 공용 스토리지가 멈춰도(I/O 대기, G8) 200 을 줍니다(`app.go:389-401`, `health.go:155-166`). 그래서 readiness 로 쓰지 않습니다.
    - 빈 레지스트리에서도 200 인지는 추정입니다(T4 에서 확인).
  - **liveness**: TCP 5000. 저장소를 만지면 안 됩니다. D state 컨테이너는 재시작해도 회복되지 않습니다.
  - **init 센티널**(검사만)
    - 센티널 `/var/lib/registry/.dms-registry-id` 는 **S0 초기화 스크립트가** 마운트를 확인한 뒤 씁니다(값 = `<SHARED_DIR>/dms/.dms-shared-id`).
    - init 컨테이너는 센티널을 **만들지 않고** 검사만 합니다. 없거나 ConfigMap 값과 다르면 기동하지 않습니다(사유: 「센티널 없음 = S0 미실행 또는 마운트 없음」 / 「센티널 불일치」).
    - 막는 상황: 마운트가 빠진 마스터에 우연히 같은 이름의 로컬 디렉터리가 있어서 레지스트리가 로컬 디스크에 쓰는 경우. init 이 「없으면 만든다」면 바로 이 상황에서 센티널을 만들어 버려 무력해집니다.
  - (선택) uid 65532, `readOnlyRootFilesystem`, drop ALL
- **Service `dms-registry`**: `clusterIP: <REGISTRY_CLUSTER_IP>` 고정, 포트 5000.
- **DMS 코드**(`registry.py`·`routes_registry.py`)는 바꾸지 않습니다. 바꿀 곳은 배포 렌더, values, 계약 테스트, 설치 게이트입니다.
- **테스트베드**: 1개라서 control-plane 1대(cp1)에 그대로 둡니다. 배치 완화가 필요 없습니다.

### 7-4. 접근 방식 [권고: 고정 ClusterIP]

- **판단**
  - 「k8s에 띄우고」라는 요청과 맞습니다.
  - 외부 신청이 필요 없습니다.
  - 익명 push·delete 가 클러스터 안에서만 보입니다. 서비스 CIDR 이 사내망에 광고되지 않기 때문입니다(#43).
  - 이미지는 인터넷 노드(클러스터 노드)가 ClusterIP 로 직접 push 합니다(7-5). 운영자 PC 에서는 push 하지 않습니다.
- **같은 주소가 여섯 곳에 들어갑니다**
  - `REGISTRY`, 오버레이 `newName`, `DMS_JOB_IMAGE`, `DMS_BUILD_REGISTRY`, `DMS_BUILD_BUILDER_IMAGE`
  - **DB `control_state.job_image`**: 포탈에서 릴리스한 뒤에는 env 보다 우선합니다(`job_image.py:3-15`).
  - 한 번 정하면 바꾸지 않는 계약입니다. 계약 테스트가 Service clusterIP == values 를 고정합니다.
  - 주소를 바꾸거나 DB 를 이관하면(U5) job 이미지를 다시 릴리스합니다.
- **노드 신뢰 설정**(전 노드, IaC 1회, CRI-O)
  - `/etc/containers/registries.conf.d/50-dms-registry.conf` 에 `[[registry]] location="<IP>:5000" insecure=true` 를 쓰고 `systemctl reload crio`(SIGHUP)를 실행합니다. 자동 반영은 기본으로 꺼져 있습니다.
  - 파일 형식
    - v2 형식이고 `.conf` 만 읽으며, 같은 prefix 는 덮어씁니다(`containers-registries.conf.d.5.md:19-30`).
    - `crio.conf` 의 `insecure_registries` 는 폐기 예정이라 쓰지 않습니다.
    - 테스트베드가 같은 모양을 쓰고 있어서 문법은 실증됐습니다(testbed `roles/crio/templates/99-pkg-01-insecure.conf.j2`).
  - 서명 정책: 패키지로 설치한 CRI-O 는 `/etc/crio/policy.json` 을 봅니다. 사이트 정책이 reject 기본이면 `<IP>:5000` 을 허용해야 합니다(S1).
  - 런타임에 프록시 env 가 있으면 `NO_PROXY` 에 `<REGISTRY_CLUSTER_IP>` 를 넣습니다(S1).
- **전제**: 노드의 호스트 netns 에서 ClusterIP 로 갈 수 있어야 합니다.
  - prod 는 KPR 이 없고 kube-proxy 가 있으므로(#41, 5회차 운영자 답) kube-proxy 가 이 경로를 처리합니다. 테스트베드와 같은 모드입니다.
  - KPR 도 kube-proxy 도 없으면 안 됩니다(`kubeproxy-free.rst:145-146,2151-2154`).
  - **S6 pull 프로브가 노드마다 이것을 실증하는 게이트**입니다. 인터넷 노드의 push 도 같은 경로를 씁니다.
- **의존 규칙**: 시스템 이미지(kubeadm·CNI·kube-proxy·CoreDNS·pause)와 레지스트리 자기 이미지는 이 레지스트리에 기대지 않습니다.
- **대안**
  - A: 노드 `/etc/hosts` + CoreDNS hosts 로 같은 ClusterIP 에 이름을 붙입니다.
  - B: 마스터 hostNetwork + Avi VS:5000(PROXY 없음) + 사내 DNS. S6 가 실패하거나 클러스터 밖에서 pull 해야 할 때 씁니다.
  - 전환은 되돌릴 수 있지만 주소 문자열이 바뀌므로 다시 릴리스해야 합니다.

### 7-5. airgap 이미지 반입 [설계]

- **인터넷 노드**(#51): 기존 DMS 구현대로 외부 인터넷이 되는 노드 1대가 지정돼 있다고 봅니다. 기존 구현에서는 포탈 「컨트롤 상태」의 빌드 노드(`build_node_name`)이고, 운영자가 빌드할 때(이제는 반입할 때도) 인터넷(직접 또는 프록시·사내 CA)을 엽니다(`deploy/README.md:544-547,561-565,619-624,647-658`). 빌드 파드가 뜨는 클러스터 노드라서 레지스트리 ClusterIP 에 닿습니다(S6 가 실증).
  - 첫 설치 때는 DB 가 없어 `build_node_name` 이 아직 없습니다. 그래서 K8 로 받은 노드를 쓰고, 설치 뒤 컨트롤 상태에 `build_node_name`·`build_source_path`·프록시를 같은 노드로 등록합니다(S13).
  - 컨트롤 상태의 프록시 값은 빌드·프리플라이트 파드의 env 로만 들어갑니다(`deploy/README.md:647-652`). 호스트에서 도는 반입 스크립트(skopeo·podman)는 프록시를 `HTTPS_PROXY`·`NO_PROXY` env 로 받습니다.
  - 여러 이미지를 묶은 오프라인 번들은 없습니다. 파일로 옮기는 것은 레지스트리 자기 이미지 1개(oci-archive, ②)뿐이고, 반입 뒤 지웁니다.
1. **`images.lock`**: 모든 노드가 x86_64 이므로(#41) **linux/amd64 매니페스트 digest 하나로 통일**합니다.
   - `images.lock`·렌더·`imageName`·S5 대조에 모두 이 digest 를 씁니다. lock 은 저장소에 커밋합니다(⑤ `versions.env` 옆).
   - lock 생성(인터넷 노드에서): `skopeo inspect --raw docker://<img>:<tag>` 가 index 면 그 안의 `linux/amd64` 항목 digest 를, 단일 매니페스트면 그 매니페스트 digest(`skopeo inspect --format '{{.Digest}}'`)를 적습니다.
   - `--all` 과 index digest 는 쓰지 않습니다. index digest 를 적으면 레지스트리에 index 가 없어서 `manifest unknown` 이 납니다.
   - `docker save` 는 쓰지 않습니다.
2. **레지스트리 자기 이미지**(레지스트리가 뜨기 전, 1회): 인터넷 노드가 한 번 받아 마스터 3대에 넣고 고정합니다(태그 ref 라서 digest 보존은 필요 없음).
   - 인터넷 노드: `skopeo copy docker://docker.io/library/registry@sha256:<amd64 digest> oci-archive:/tmp/registry-3.1.2.tar`
   - 마스터 3대로 파일을 옮기고(ssh·scp 등, K8), 각 마스터에서 `skopeo copy oci-archive:/tmp/registry-3.1.2.tar containers-storage:<IP>:5000/registry:3.1.2` 를 실행합니다. skopeo 는 RHEL AppStream 패키지입니다(사이트 미러, K8).
   - `/etc/crio/crio.conf.d/99-dms-pinned.conf` 에 아래 내용을 쓰고 reload 합니다.
     ```
     [crio.image]
     pinned_images = ["<IP>:5000/registry:*", <기존 목록>]
     ```
   - drop-in 병합 규칙
     - 같은 옵션은 이름순으로 마지막 파일이 이기고, 배열은 합치지 않습니다(`crio.conf.d.5.md:17-23`).
     - 그래서 파일 이름을 `99-` 로 시작해 맨 뒤에 둡니다.
     - S1 에서 `crio config` 로 기존 목록을 읽어 합치고, 적용 뒤 실효값을 다시 확인합니다.
     - 패키지 `10-crio.conf` 는 업그레이드 때 덮어써지므로 건드리지 않습니다.
   - `pinned_images` 는 정확 일치와 끝 와일드카드를 받습니다(`crio.conf.5.md:499-500`). kubelet 은 pinned 이미지를 GC 하지 않습니다.
3. **나머지는 레지스트리가 뜬 뒤 인터넷 노드가 직접 push 합니다**(S5).
   - `skopeo copy --preserve-digests --dest-tls-verify=false docker://<상류 이미지>@sha256:<amd64 digest> docker://<IP>:5000/<이름>:<태그>`. 평문 HTTP 이고, 소스를 amd64 매니페스트 digest 로 집으므로 그 매니페스트 하나만 복사합니다.
   - 대상: buildah, CNPG 2, EG 3(gateway·envoy·shutdown-manager 명시), Volcano 3, 레지스트리 자기 이미지(업그레이드·새 마스터용)
   - 전부 **태그를 붙여** push 합니다(GC 보호).
   - 순서: install.sh 는 S4 뒤에 멈추고 인터넷 노드에서 돌릴 push 명령(`images.lock` → `skopeo copy`)을 출력합니다. push 가 끝나면 `install.sh --stage S5` 로 재개합니다.
   - 인터넷 노드가 프록시를 거치면 `NO_PROXY` 에 `<REGISTRY_CLUSTER_IP>` 를 넣습니다. push 는 프록시를 타지 않습니다(포탈 빌드와 같은 규칙, `deploy/README.md:650-652`).
   - DMS 3종(B3 포함 dN)은 상류 이미지가 아니므로, 처음에는 같은 노드에서 최초 부트스트랩 경로로 빌드해 push 합니다(`deploy/README.md:135-139`, `REGISTRY=<IP>:5000 TAG=dN ./deploy/docker/build-and-push.sh`, podman `--tls-verify=false`, `build-and-push.sh:29-30,44-45`). 설치 뒤에는 포탈 빌드(§8)가 같은 노드를 빌드 노드로 씁니다 [해석]. 빌드 전 e2e 게이트(`deploy/README.md:139-141`)는 인터넷 노드가 아니라 개발 PC 에서 같은 커밋으로 돌립니다.
   - 레지스트리 자기 이미지는 계약상 **태그 ref 예외**입니다. containers-storage 반입본의 RepoDigest 가 레지스트리의 digest 와 같은지는 실증하지 못했습니다(추정).
4. **검증**: install.sh 가 port-forward 로 레지스트리의 각 태그 digest 를 읽어 `images.lock` 과 대조합니다. 없거나 다르면 FAIL 입니다(S5).
5. 새 워커는 신뢰 설정만 있으면 됩니다. 새 마스터는 레지스트리가 살아 있으면 `crictl pull <IP>:5000/registry:3.1.2` 뒤 고정하고, 아니면 2단계를 반복합니다.

### 7-6. 운영 [설계]

- **업그레이드**: 인터넷 노드가 새 이미지를 push → 마스터 3대에서 `crictl pull` 하고 고정 목록 갱신 → Deployment 이미지 교체. `maxSurge: 1` 이라 새 파드가 Ready 가 된 뒤 옛 파드가 내려가서 공백이 거의 없습니다. 교체는 빌드·push 가 없을 때 합니다(7-3, 겹치는 동안의 `blob unknown`).
- **GC**: 수동으로 연 몇 번 합니다. 빌드가 없고 레지스트리가 Ready 일 때만 합니다.
  1. readonly 를 켭니다. 방법은 둘 중 하나입니다(env 의 JSON 맵 파싱은 추정, T4 에서 확인).
     - `kubectl --context C -n dms set env deploy/dms-registry REGISTRY_STORAGE_MAINTENANCE_READONLY='{"enabled":true}'`
     - ConfigMap 수정 + `rollout restart`
  2. `rollout status` 가 끝나고 옛 RS 파드(Terminating 포함)가 0개인지 본 뒤, `terminationGracePeriodSeconds` 만큼 더 기다립니다.
  3. `POST /v2/dms/blobs/uploads/` 가 405 인지 확인합니다. 읽기 전용이라는 증거입니다.
  4. `kubectl --context C -n dms exec <파드> -- registry garbage-collect --dry-run --delete-untagged /etc/distribution/config.yml` 로 확인한 뒤 본 실행합니다.
  5. readonly 를 끄고, 롤아웃이 끝나면 시험 push·삭제를 합니다.
  - GC 창 동안 포탈 삭제는 409, 빌드 push 는 실패합니다. pull 은 됩니다.
  - **태그 없는 digest 이미지는 지워집니다.**
- **백업**: 애드온은 인터넷 노드가 `images.lock` 의 digest 로 다시 받을 수 있습니다. **현장에서 빌드한 DMS 태그는 레지스트리에만 있으므로** `skopeo sync --dest dir` 로 공용 스토리지 밖 사본에 더합니다.
- **장애 영향**(시간은 추정. 노드 판정 50초는 kube-controller-manager `--node-monitor-grace-period` 기본값)

| 상황 | 영향 |
|---|---|
| 레지스트리가 있는 마스터 VM 급사 | 전면 공백 약 90~110초. 같은 마스터에 있던 api 의 복구도 늦어짐 |
| 마스터 drain·유지보수 | 약 10~20초 공백 |
| 레지스트리 업그레이드 / 크래시·OOM | 거의 0(`maxSurge: 1`) / 수 초 |
| 레지스트리가 있는 마스터의 공용 스토리지 정지 | 레지스트리 정지. readiness(catalog)가 NotReady 로 드러내고(`/v2/` 는 계속 200), 그 마스터를 cordon 하고 파드를 지우는 수동 조치 전까지 이어짐 |
| 공용 스토리지 전체 장애 / GC 창 | 정지(캐시된 이미지는 동작) / 위 절차 |

- 이미 도는 파드는 영향이 없습니다. 모든 매니페스트가 `IfNotPresent` 라서 캐시된 이미지는 계속 씁니다(`40-api.yaml:100,117`, `41-controller.yaml:45,62`, `50-agent-daemonset.yaml:92`).
- 깨지는 것: 캐시가 없는 노드의 새 파드, 빌드 push, 이미지가 없는 노드의 purge 파드(`purge_runner.py:50-52`).
- P2P 이미지 배포는 쓰지 않습니다. Spegel 은 containerd 전용이고, Dragonfly 는 manager·scheduler·DB 가 더 붙습니다.
- **릴리스는 레지스트리가 정상일 때만** 합니다.

---

## 8. 설치 순서

### 8-1. 단계 (C = kube-context, 모든 kubectl 에 `--context C`)

| 단계 | 담당 | 내용 | 판정 |
|---|---|---|---|
| S-1 이미지 lock | 릴리스 담당 | 7-5 ① `images.lock`(amd64 매니페스트 digest). CNPG yaml cosign 검증, `kubectl-cnpg`·SHA256SUMS 는 인터넷 노드가 받음. **dms 는 B3 포함 dN** | lock 에 index digest 가 있으면 실패 |
| S0 노드(IaC) | 사이트 | 아래 「S0 세부」 | S1·S2b·S4·S6·S7·S10·T8 이 실증 |
| S1 preflight | 설치자 | 아래 「S1 점검 항목」 | FAIL·P·X 면 중단 |
| S2 값·시크릿 | 설치자 | values.env(`SHARED_DIR` 포함). **`deploy/k8s/00-namespace.yaml` 을 그대로 적용**(PSA privileged 라벨). `dms-pg-app`, `dms-secrets`(4키, URL 은 6-3), `dms-portal-tls`. 값은 출력하지 않음 | — |
| S2b SELinux 노드 프로브 | install.sh | S2 의 네임스페이스 적용 직후 ns `dms` 에서 privileged 임시 DS(아래 「S2b 세부」) | DMS 노드 중 FAIL·모름이 있으면 중단 |
| S3 계획 | `--plan` | 모든 층 렌더, SSA server dry-run, 애드온별 판정 표 | 변경 없음 |
| S4 플랫폼·레지스트리 | install.sh | **PriorityClass `dms-addon-critical`(1000)** SSA → `platform/registry` SSA(manager `dms-install`, replicas 1), http 시크릿(없을 때만 생성), port-forward 로 `/v2/` 200 확인 | 「마스터 X 에 공용 디렉터리 없음」「센티널 없음(S0 미실행)·불일치」「clusterIP 충돌」 |
| S5 push·대조 | 인터넷 노드 + install.sh | install.sh 는 S4 뒤에 멈추고 push 명령을 출력 → 인터넷 노드가 lock 의 이미지를 레지스트리 ClusterIP 로 직접 push(7-5 ③) → `install.sh --stage S5` 로 재개해 port-forward 로 각 태그 digest 를 lock 과 대조 | **없거나 다르면 FAIL**(지금은 호스트 podman push 실패가 WARN, `install.sh:30-37`). 힌트: 인터넷 노드 → ClusterIP(kube-proxy)·NO_PROXY·`--dest-tls-verify=false` |
| S6 pull 프로브 | install.sh | 임시 DS(toleration Exists, 레지스트리 이미지, `Always`), 120초 | 실패 노드 + 「신뢰 설정·서명 정책·프록시·ClusterIP 경로」 |
| S7 공용 스토리지 프로브 | install.sh | 아래 「S7 세부」 | 노드별 FAIL·WARN 사유(아래) |
| S8 GW API + EG | install.sh | A 면 GW API v1.6.1, EG v1.9.3. 프록시 ns PSA privileged | GW API 가 더 낮으면 X |
| S9 CNPG | install.sh | A 면 패치본 SSA → 롤아웃 → **Cluster CR server dry-run**(9443 경로 실증) | 「failed calling webhook」이면 9443 사유 |
| S9b Volcano | install.sh | A 면 v1.16.0. DMS 모양 vcjob dry-run | scheduler 이름이 다르면 X |
| S10 DB 층 | install.sh | 아래 「S10 세부」 | 아래 「S10 세부」 |
| S11 DMS | install.sh | guard-images → migrate → `apply -k` → 대기 → 설정 해시가 바뀌었으면 rollout restart → **마스터 3대 `dms` 이미지 사전 pull**(S6 방식) | 기존 exit 3·4 |
| S12 verify | 설치자 | `--resolve` readyz, CNPG, Available 여분 PV, **PG 경로 대조(S10 과 같은 검사)**, CronJob, Gateway Programmed, 레지스트리 Ready·태그, 애드온 요약, 「80 불가 = 정상」. 실패 원인이 EACCES 면 SELinux 노드 프로브(S2b)를 다시 돌리도록 안내 | — |
| S13 사후 | 운영자 | 공용 스토리지 밖 사본, DNS·Avi 풀, 「k8s 연동」 등록, 컨트롤 상태 빌드 노드 등록(`build_node_name`·`build_source_path`·프록시 = 인터넷 노드, 7-5) | 체크리스트 |

**S0 세부**(순서가 중요합니다)
1. 사이트: 공용 스토리지를 모든 노드에 `/etc/fstab` 으로 마운트하고(#50) `SHARED_DIR` 가 그 안(마운트포인트 또는 하위)에 있게 합니다(3-2). `SHARED_DIR` 자체는 사이트가 만듭니다.
2. 사이트(RHEL 계열 DMS 노드 전부): SELinux 를 enforcing 이 아니게 고정합니다(`/etc/selinux/config` 의 `SELINUX=permissive` + `setenforce 0` 권장, 2-3).
3. DMS 동봉 초기화 스크립트를 한 노드에서 실행합니다.
   - 가드: `[ -d <SHARED_DIR> ]` 이고 `[ "$(findmnt -n -o TARGET -T <SHARED_DIR>)" != / ]` 가 아니면 중단합니다. `SHARED_DIR` 와 그 상위는 만들지 않습니다(`mkdir -p`·`install -d` 금지).
   - `dms/{artifacts, pgdata/dms-pgdata-01..05, pgdata/.retired, backup/pg, registry}` 를 만들고 소유·모드를 맞춥니다(3-1).
   - 센티널을 씁니다: `dms/.dms-shared-id`(무작위), `registry/.dms-registry-id`(같은 값), 각 `dms-pgdata-0N/.dms-pv-id`(PV 이름).
   - 이미 있으면 검사만 하고 고치지 않습니다. 공용 경로라서 통째 chmod 가 전 노드에 번지는 함정이 있기 때문입니다(testbed `roles/nfs_client/tasks/main.yml:21-27`).
4. 전 노드: 레지스트리 신뢰 drop-in(7-4)
5. 마스터 3대: 레지스트리 이미지 반입·고정(인터넷 노드에서 받은 파일, 7-5 ②, `99-dms-pinned.conf`), `/etc/sysctl.d/90-dms-envoy.conf`(4-3)
6. kubelet 예약·`shutdownGracePeriod`, apiserver·etcd requests
7. 설치 호스트: kubectl(클러스터 minor ±1), skopeo, curl, python3(RHEL 9 면 3.9). 인터넷 노드: skopeo, podman(DMS 최초 빌드, 7-5 ③)

**S1 점검 항목**
- K8s minor 가 1.34~1.36 안인지(#4. prod 값을 따르고 기록함), control-plane 노드 3대와 taint 키
- `SHARED_DIR` 형식(절대 경로, `/` 아님, normpath, 문자 집합 `^/[A-Za-z0-9._/-]+$`), `REGISTRY_CLUSTER_IP`(ServiceCIDR 안, 미사용, 하단 정적 대역)
- 애드온 탐지(8-2), PSA, 이름 충돌(SC `dms-pg-shared`, PV `dms-pgdata-*` 포함)
- admission 정책이 hostPath PV·hostNetwork·privileged 파드를 막는지(K11), (선택) VAP
- **노드 셸 체크리스트**: preflight 가 명령을 출력하고, 설치자가 결과를 확인합니다.
  - `getenforce`, `grep ^SELINUX= /etc/selinux/config`(S2b 결과와 대조)
  - `crio config` 의 `pinned_images`·`signature_policy`
  - `findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS -T <SHARED_DIR>`
  - 런타임 프록시 env 의 NO_PROXY

**S2b 세부: SELinux 노드 프로브**(2-3) [설계]
- 왜 S2b 인가: PSA privileged 라벨이 붙은 ns `dms` 는 S2 에서 생깁니다(`00-namespace.yaml:17-19`). baseline 은 hostPath 를 막고(`k8s-pss.md:139-149`), preflight 스크립트는 읽기 전용이라 S1 에서는 파드를 띄우지 않습니다. 레지스트리(S4)보다 앞이므로 pull 이 필요 없는 이미지를 씁니다.
- 임시 DS(ns `dms`)
  - `privileged: true`. CRI-O 는 privileged 컨테이너에 프로세스 라벨을 주지 않아(spc_t) 라벨 거부가 없습니다(`crio_cc.go:768-770`). 일반 컨테이너(container_t)로 띄우면 enforcing 노드에서 selinuxfs(`security_t`) 읽기가 거부될 수 있고(`container.te` 에 `security_t` 허용 0건, 확실하지 않음), 그러면 거짓 PASS 가 날 수 있습니다.
  - hostPath `/sys/fs`·`/etc` 를 읽기 전용으로 붙입니다. CRI-O 가 hostPath 를 rbind 로 붙이므로 하위의 selinuxfs 도 보입니다(`crio_ccl.go:294`). `/etc/selinux` 를 직접 걸지 않는 이유: 없는 hostPath 소스는 CRI-O 가 `MkdirAll` 로 만들어 Ubuntu 호스트에 디렉터리를 남깁니다(`crio_ccl.go:288`).
  - 전 노드에 띄우되(toleration Exists) **판정은 DMS 노드만** 합니다(2-3). 그 밖의 노드 결과는 「정보」로만 표시합니다.
  - 이미지는 모든 노드에 이미 있는 셸 포함 이미지를 `imagePullPolicy: Never` 로 씁니다(예: Cilium agent DS 의 이미지 ref). 끝나면 DS 를 지웁니다.
- 판정(노드별)
  - `/sys/fs/selinux/enforce` == 1 → **FAIL** 「enforcing」
  - 0 인데 `/etc/selinux/config` 가 `SELINUX=enforcing` 이고 `/proc/cmdline` 에 `selinux=0`·`enforcing=0` 이 없음 → **FAIL** 「재부팅하면 enforcing」
  - 0 → PASS
  - `/sys/fs/selinux` 가 없거나 비었고 `/sys/fs/cgroup` 은 보임(rbind 정상) → PASS(SELinux 없음 또는 disabled)
  - 읽기 오류(EACCES 등), `/sys/fs/cgroup` 도 안 보임, 파드 미기동 → **「모름」으로 중단**. 설치자가 그 노드에서 `getenforce` 와 `grep ^SELINUX= /etc/selinux/config` 로 확인합니다(모름과 실패를 섞지 않음).
- FAIL 안내: 노드 이름과 함께 「RHEL 계열 DMS 노드는 SELinux 를 enforcing 이 아니게 둘 것: `/etc/selinux/config` 의 `SELINUX=permissive` 로 고정하고 `setenforce 0`(권장). 완전 비활성은 `grubby --update-kernel ALL --args selinux=0` 후 재부팅」(2-3).

**S7 세부**
- 임시 DS 로 돌립니다: 전 노드, toleration Exists, `dms` 이미지, root, hostPath `<SHARED_DIR>/dms` `Directory`. `…/dms/.probe-<nonce>` 만 쓰고 끝나면 지웁니다.
- **판정은 아래 동작 프로브로만 합니다.** FS 종류나 마운트 옵션으로 FAIL 하지 않습니다(DMS 에는 FS 종류별 분기가 없음).
- **교차 노드 확인의 원칙**(② 제외): ①·④·⑥ 처럼 다른 노드에서 확인하는 검사는 G3 지연과 섞이지 않게 합니다. 확인하는 노드는 생성 전에 그 이름을 조회하지 않고(부정 캐시를 만들지 않음), stat 대신 open + fstat 으로 읽으며(close-to-open 재검증), 실패하면 60초(NFS 기본 acdirmax)까지 재시도한 뒤 판정합니다. G3 가 WARN 인 사이트에서 「다른 노드 파일이 안 보임」「ID 매핑」을 잘못 내지 않게 하기 위해서입니다.
- 검사 항목
  - ① 교차 노드 가시성(G1): nonce 가 모든 노드에서 보이고, `.dms-shared-id` 가 모든 노드에서 같은 값
  - ② 노드 간 일관성(G3). 20회 반복해서 한 번이라도 놓치면 **WARN** 입니다(아래).
    - 새 이름: B 에서 없는 이름을 먼저 stat → A 가 그 이름으로 쓰고 rename → B 에서 1초 안에 stat·open·readdir 로 보여야 함
    - rename 교체: A 가 기존 파일을 새 내용의 임시 파일로 rename 교체 → B 가 1초 안에 open 해서 새 내용을 읽어야 함
  - ③ root 소유 보존(G4)
  - ④ chown 26 왕복(G5, 다른 노드에서도 26 으로 보이는지)
  - ⑤ fsync 왕복(G7 의 내구성은 N1)
  - ⑥ POSIX 의미(G9)
    - 두 노드가 같은 이름을 동시에 O_EXCL 로 만들면 하나만 성공
    - 새 파일 nlink 1, 하드링크 뒤 2
    - symlink 를 `O_NOFOLLOW` 로 열면 ELOOP
    - rename 원자 교체(중간 상태가 보이지 않음)
    - 디렉터리 fsync 성공
- 마스터 `findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS -T <SHARED_DIR>`
  - 포함 마운트 TARGET 이 `/` 이면 「마운트 없음」 FAIL 입니다.
  - SOURCE·FSTYPE·OPTIONS 는 **보고만** 합니다. 노드마다 서버 이름·IP 표기가 합법적으로 다를 수 있습니다.
- FAIL 사유 문구(FS 종류별 힌트를 붙임)
  - 「마운트 없음」
  - 「다른 노드 파일이 안 보임」
  - 「root 소유 변경(squash)」
  - 「ID 매핑」
  - 「POSIX 의미(O_EXCL·nlink·O_NOFOLLOW·rename)」
- **② 는 WARN 입니다**(G3 는 필수 아님, #47). 설치는 계속합니다.
  - 문구: 「새 이름·rename 교체 지연(노드 간 캐시)」
  - 함께 알리는 영향: 멀티 노드 rank 가 다른 노드가 막 만든 파일을 못 봐서 ENOENT 로 실패할 수 있고, api·controller 가 잡 산출물을 늦게 볼 수 있습니다(NFS 기본 acdirmax 60초 기준, 추정). 같은 원인의 ENOENT 를 테스트베드 PV share 에서 실측했습니다(testbed `all.yml:77-82`).
  - NFS 면 힌트: `lookupcache=positive`(또는 none)로 다시 마운트하고 `nocto` 를 빼기를 권장합니다(remount 불가, 3-2 G2 보충).

**S10 세부**
- 순서: SC + PV ×5 → 사전 검사 → Cluster → 900초 폴링 → 경로 대조 프로브 → CronJob → 첫 덤프
  - SC·PV 가 이미 있는데 path·SC·용량이 다르면 FAIL 입니다.
  - PV 경로가 `<SHARED_DIR>/dms/pgdata/` 밖이면 FAIL 입니다.
  - Released PV 는 건드리지 않고 WARN 만 냅니다.
- **사전 검사**: Cluster 를 만들기 전에 일회용 파드가 PV 5개의 디렉터리가 있고 26:26 0700 이며 `.dms-pv-id` 가 PV 이름과 같은지 확인합니다.
- 폴링 조건: `status.readyInstances` ≥ 1, `dms-pg-rw` Endpoints 존재, `status.currentPrimary` 파드 Ready
  - `wait --for=condition=Ready` 는 쓰지 않습니다. 인스턴스가 모두 healthy 일 때만 성립하기 때문입니다(추정).
- **경로 대조 프로브**: Bound PV 마다, 그 인스턴스가 **아닌** 노드에서 일회용 파드가 `<PV 경로>/pgdata/PG_VERSION`(PGDATA = 볼륨의 `pgdata` 하위, `cnpg-pods.go:85`)과 `.dms-pv-id` 를 읽습니다. 없으면 FAIL, 사유는 「PG 가 공용 경로가 아닌 곳에 씀」입니다. 읽기는 S7 의 교차 노드 원칙(open + fstat, 60초 재시도)을 따릅니다.
- 판정
  - readyInstances < 3 → WARN(모름 ≠ 실패)
  - 0 또는 시간 초과 → FAIL
  - Available 여분 PV < 1 → WARN

**순서의 이유**
- 레지스트리가 맨 먼저입니다. hostPath 를 쓰므로 다른 애드온이 필요 없고, 노드 반입이 이미지 1개 × 마스터 3대로 끝납니다.
- SELinux 프로브는 PSA privileged ns 가 생긴 S2 바로 뒤(S2b)에 둡니다. enforcing 노드가 있으면 레지스트리·DB 를 띄우기 전에 멈춥니다.
- PriorityClass 는 그것을 참조하는 EG·CNPG(S8·S9)보다 먼저 만듭니다.
- DMS 는 DB 가 Ready 된 뒤에 적용합니다. migrate 는 `backoffLimit: 3` 이고 install 은 180초만 기다리기 때문입니다(`30-migrate-job.yaml:15,22`, `install.sh:128`).
- Volcano 는 `05-*.yaml` 의 CRD 때문에 DMS 보다 먼저 설치합니다.

### 8-2. 감지 → 없으면 설치 (공통 상태 기계, #34)

| 코드 | 판정 | 동작 |
|---|---|---|
| **A** | 없음 | `ADDON_INSTALL=auto`(기본)면 설치. 라벨 `app.kubernetes.io/managed-by=dms-install`, `dms.io/addon-version`, SSA manager `dms-install`. `never` 면 FAIL + 「클러스터 운영자에게 요청」 |
| **D=** / **D<** / **D>** | DMS 소유: 같음 / 낮음 / 높음 | no-op / WARN `--upgrade-addon` / WARN(다운그레이드 안 함) |
| **C** / **C!** | 남이 설치: 호환 / EOL·범위 밖 | 재사용, 컨트롤러·CRD 는 건드리지 않음 / WARN + 소유자 요청 문구 |
| **X** | 비호환 | FAIL + 사유 |
| **U** | 버전 모름 | dry-run 을 통과하면 WARN |
| **P** | 부분 설치·중복 | FAIL. `--adopt <x>` 일 때만 채택 |

- **최종 판정은 DMS 가 만들 CR 의 server-side dry-run** 입니다.

| 애드온 | 감지 | 호환 규칙 | 고정 |
|---|---|---|---|
| CNPG | CRD `clusters.postgresql.cnpg.io`, 라벨 `app.kubernetes.io/name=cloudnative-pg`, manager 이미지 태그, `WATCH_NAMESPACE` | 하한 1.25(`dataDurability`). 지원 minor 밖이면 C!. **오퍼레이터가 2개 이상이면 P**. watch 범위에 dms 가 없으면 X | 1.30.x |
| Gateway API CRD | `bundle-version`·`channel` | v1.6.x(낮으면 X) | v1.6.1 standard |
| Envoy Gateway | CRD `envoyproxies…`, deploy `envoy-gateway`, controllerName, `watch:` | v1.9.x(v1.8 은 2026-11-08 EOL). C 면 Gateway `parametersRef` 로 DMS EnvoyProxy 만 붙이고, 프록시 ns 의 PSA 가 hostNetwork 를 허용하는지 확인 | v1.9.3 |
| Volcano | CRD 3종, deploy `volcano-scheduler`(**라벨 탐지 버그 수정**, `preflight-cluster.sh:59`), `--scheduler-name` | ≥ v1.15. **1.36 은 v1.16 부터**. C 면 다시 적용하지 않음 | v1.16.0 |
| 레지스트리·DB 층·PriorityClass | `dms-registry`, `cluster dms-pg`, `sc dms-pg-shared`, `pv dms-pgdata-*`, `pc dms-addon-critical` | 늘 DMS 소유. 내용이 다르다고 조용히 다시 만들지 않음 | Distribution v3.1.x |

- SSA 는 addons·platform 층에만 씁니다. 앱 오버레이는 지금처럼 client-side apply 로 둡니다. 포탈 릴리스가 strategic-merge 로 image 를 바꾸기 때문입니다(`rollout_runner.py:22-38`).
- 층 배치
  - `deploy/addons/`: 서드파티
  - `deploy/platform/`: DMS 소유
  - `deploy/.rendered/`: 렌더 결과
  - `overlays/`·`k8s/` 밖에 두는 이유는, 그 안의 디렉터리를 모두 도는 계약 테스트 4건을 피하기 위해서입니다.

---

## 9. DMS 저장소 변경 범위 (지금은 구현하지 않음)

**공통 규칙**
- dms-k8s 라인에서만 합니다. 공용 `46-ingress.yaml` 은 건드리지 않습니다.
- 커밋은 `git commit -- <경로>` 로 합니다.
- 묶음마다 dN 을 만들어 testbed-k8s 에서 실증하고 CHANGELOG 를 씁니다.
- 사유 코드와 새 열은 양쪽에 등록합니다.

구현 순서는 리허설 순서(T 번호)와 같고, 설치 순서(8-1)와는 다릅니다.

**묶음별 범위**

| # | 묶음 | 핵심 변경 | 주요 위치 |
|---|---|---|---|
| **①** | **B3 기존 vcjob·preflight 채택** | 아래 「① B3 세부」 | `execution_volcano.py:206-245,406-413`, `execution_manifests.py:383-384,899-913,960-1010`, `stepper.py:709-716,1105-1113` |
| ② | 로그인·식별 | v3 5절 ① 전부(**전체 오답 60→1000/h** 로 읽음) + 관리자 재설정(봉인)·잠금 해제·보안 이벤트 화면 + 선택 4종 | `auth.py:40-54`, `routes_auth.py`, `routes_accounts.py`, `accounts.py`, `app.py`, `config.py`, `20-config.yaml:56-61`, `Login.tsx`·`api.ts`·`reasonCodes.json`, `ARCHITECTURE.md:494,577,579` |
| ③ | 공유 FS | 아래 「③ 공유 FS 세부」 | `40-api.yaml:61-65,153-167`, `41-controller.yaml:79-93`, `values.env.example:27-29`, `render.sh:76,99`, `patch-config.yaml:24-27`, `deploy/README.md:277`, `overlays/prod/README.md:53,73`, `deploy/overlays/testbed-k8s/values.env`(새), `deploy/testbed/dms-shared-fs.yml:99-103`, `test_release_manifest_contract.py:190-210` |
| ④ | 레지스트리 | `deploy/platform/{priorityclass,registry}`(7-3 의 replicas 1·롤링·probe·검사 전용 센티널 init). values `REGISTRY_MODE`(in-cluster/external)·`REGISTRY_CLUSTER_IP`·`REGISTRY_PORT`, `REGISTRY_TLS_VERIFY=false`. prod 「TLS 권장」 주석 정정(`patch-config.yaml:16-17`). (선택) 마스터 사전 pull, 태그 DELETE | `deploy/platform`, `values.env.example`, `guard-images.sh` |
| ⑤ | DB(CNPG) | `deploy/addons/cnpg/<v>`(원본 + SOURCE·sha256 + 6-1 패치), `deploy/platform/db`(SC·PV ×5·Cluster·CronJob). v4 의 `PG_STORAGE` 키는 없앰(값이 하나뿐 — 테스트베드를 포함해 늘 공용 경로 PV). api·controller 마스터 배치 + `tolerationSeconds: 30` 패치. `versions.env`·`.rendered/` | `deploy/addons`, `deploy/platform/db`, `render.sh` |
| ⑥ | 입구(EG) | v3 5절 ②(0s→3600s): `addons/{gateway-api,envoy-gateway}`, Gateway·HTTPRoute(3600s)·BTP(20Mi)·CTP·EnvoyProxy(#14, hostNetwork·`ClusterFirstWithHostNet`·`useListenerPortAsContainerPort`·이미지 명시). Ingress `$patch: delete`, prod MetalLB 삭제 | `test_{prod,testbed}_k8s_overlay.py` |
| ⑦ | 설치·문서·계약 | install.sh: `--context` 전파, `--stage/--plan/--upgrade-addon/--adopt`, 8-2 감지, MetalLB·ingress 게이트 삭제(`:74-80,135-138`), 호스트 podman push(`:30-37`)를 **S5 lock 대조(FAIL)** 로 바꿈, S4 뒤 정지·`--stage S5` 재개, 인터넷 노드용 push 스크립트(`images.lock` → `skopeo copy`, 프록시는 env), **S2b SELinux 노드 프로브(privileged 임시 DS, DMS 노드만 판정)**, pull 프로브, **S7 공용 스토리지 프로브(①~⑥, ② 는 WARN, 교차 노드 확인은 재시도 뒤 판정)**, S10 사전 검사·경로 대조, 사전 pull, 설정 해시 재시작. preflight(노드 셸 체크리스트·pinned·NO_PROXY 포함)·verify·render. 새 python 코드는 3.9 호환. 문서(`deploy/README.md`, prod README — SELinux 전제·인터넷 노드 반입 포함, `BACKLOG.md:352`) | `deploy/install.sh`, `preflight-cluster.sh`, `verify.sh`, `overlays/prod/*` |

**① B3 세부**
- 현상
  - create 뒤 `_record_ref` 전에 DB 가 실패하면, 다음 틱이 같은 이름(`dms-{op}-{phase}-{job_id[:12]}`)으로 다시 만들려 합니다.
  - 그러면 409 → `submit_failed` 가 되어 **잡은 FAILED 로 잘못 기록되고, vcjob 은 추적되지 않은 채 돕니다.**
- 수정
  - `create` 가 409 AlreadyExists 를 구분해서 올립니다.
  - `submit` 이 기존 객체를 GET 해서 다음을 모두 만족하면 **채택**합니다.
    - 라벨 `dms.io/job-id`·`phase`·`tool`
    - 지문 annotation(build 할 때 붙임, 이미지는 지문에서 빼는 것을 권고)
    - deletionTimestamp 없음
  - 하나라도 어긋나면 지금처럼 실패합니다.
  - 복합 preflight 는 파드별로 처리합니다.

**③ 공유 FS 세부**
- **`SHARED_DIR` 와 출처**
  - values `SHARED_FS`(상대 이름, 예: `cephfs` → `file:///REPLACE_SHARED_FS/...`)를 절대 경로 `SHARED_DIR` 로 바꿉니다(`render.sh:76,99`, `patch-config.yaml:24,27`). `values.env.example:27-29` 의 「현재는 cephfs 만 유효」를 지웁니다.
  - 사이트마다 출처를 하나로 둡니다.
    - prod: `overlays/prod/values.env`
    - testbed-k8s: 새로 커밋하는 `deploy/overlays/testbed-k8s/values.env`(`SHARED_DIR=/dms_shared` 등). 지금 이 오버레이에는 values.env 도 render 도 없습니다(kube-context·kustomization·patch-* 뿐).
  - render.sh 를 일반화해서, 같은 렌더러가 두 오버레이와 `platform/{registry,db}` 를 모두 렌더합니다.
  - 이행: values.env 에 `SHARED_FS` 가 남아 있으면 render.sh 는 FAIL 하고 「`SHARED_DIR=/<옛 값>` 으로 바꿀 것」이라고 안내합니다. 같은 키를 쓰는 `deploy/README.md:277`, `overlays/prod/README.md:53,73` 도 고칩니다.
  - 형식 검사(render·S1): 절대 경로, `/` 아님, normpath, `^/[A-Za-z0-9._/-]+$`.
- api·controller 의 hostPath 를 `<SHARED_DIR>/dms/artifacts` 로 둡니다(오버레이 패치).
  - base 40/41 의 hostPath(`/cephfs` 고정, `40-api.yaml:164-167`, `41-controller.yaml:90-93`)는 이미지처럼 자리표시자로 바꿉니다. 사이트 중립이 되고 raw apply 는 실패합니다.
  - 마운트포인트 자체를 쓰는 지금 모양을 없앱니다.
  - 「DirectoryOrCreate 로 바꿔라」 주석(`40-api.yaml:61-65`)을 지웁니다.
  - 주석에 있는 `/cephfs` 서술(`artifact_files.py:32` 등)을 `<SHARED_DIR>` 로 바꿉니다.
- allowlist·base 를 함께 생성하고, 「hostPath == allowlist == base」 계약 테스트를 둡니다.
- **스토리지 등록 가드**: 상태 디렉터리를 덮는 mount_path·managed_root 를 거부합니다(`storages.py:60-64` 확장).
- **초기화 스크립트**(S0)
  - `deploy/testbed/dms-shared-fs.yml:99-103` 의 `findmnt -n -T … FSTYPE == ceph` 단정을 「포함 마운트 TARGET 이 `/` 가 아님」 판정으로 일반화합니다.
  - 트리·pgdata 디렉터리·센티널(`.dms-shared-id`, `registry/.dms-registry-id`, `.dms-pv-id`)을 만들고, 상위 디렉터리는 만들지 않습니다. 이미 있으면 검사만 합니다.
- v4 의 「preflight 노드 점검: nfs4·hard·lookupcache·root 쓰기 왕복」은 S7 공용 스토리지 프로브(종류 무관, 동작 판정)로 대체합니다.
- Deployment 패치 금지 테스트를 「securityContext 금지」로 좁힙니다.

**테스트·게이트**

| # | 테스트·게이트 |
|---|---|
| ① | 단위: 409+일치 → 채택, 불일치 → submit_failed, 삭제 중 → 채택 안 함. stepper: `_record_ref` 예외 → 다음 틱에 Running·ref 기록. **레지스트리에 넣는 dms 는 이 수정 이후 버전(S-1)** |
| ② | v3 ① 목록 + 재설정·해제·세션 무효화·완료 메일. 비밀번호 규칙 테스트는 없음 |
| ③ | 렌더 계약: 두 오버레이(prod·testbed-k8s) 렌더 결과에서 api·controller·잡 base·allowlist·registry·backup·PV 경로가 모두 **같은** `<SHARED_DIR>/dms/` 접두이고 `<SHARED_DIR>` 자체가 아님, `DirectoryOrCreate` 0건, base 40/41 hostPath 는 자리표시자, `SHARED_FS` 가 남은 values 는 render FAIL. T3 |
| ④ | clusterIP == values, hostPath 가 SHARED_DIR 아래·`type: Directory`, delete.enabled, cache 없음, http secret 은 Secret 참조, replicas 1·`maxSurge: 1`·`maxUnavailable: 0`, init 은 센티널을 만들지 않음(검사만), **레지스트리 이미지는 태그 ref 예외**, PriorityClass 존재 |
| ⑤ | `test_addons_render`: 전 이미지가 자리표시자 + `@sha256` amd64 매니페스트 digest(mediaType 이 index 가 아님), 외부 참조 0건, `Always` 0건, `OPERATOR_IMAGE_NAME` == manager, 오퍼레이터에 `dms-addon-critical`. `test_platform_db_contract`: instances 3, ANY 1 preferred, required, toleration, PV 5개가 hostPath `Directory`·Retain·nodeAffinity 0·claimRef 0·경로가 `<SHARED_DIR>/dms/pgdata/` 하위, **`storage.size` == PV capacity**, SC no-provisioner, `resizeInUseVolumes: false`, **모든 렌더 오버레이(prod·testbed-k8s)에 로컬 디스크 PV·local-path 0건**(③ 의 같은 접두 조건과 함께라야 의미가 있음) |
| ⑥ | CTP 계약 + EnvoyProxy 바인드 모양 |
| ⑦ | `test_install_detect`(kubectl 심으로 A~P, 「CNPG 가 어디에든 있으면 두 번째를 설치하지 않음」, 「C 에서는 apply 를 부르지 않음」), S5 lock 대조(누락·불일치 → FAIL), SELinux 노드 프로브 판정(`1` → FAIL, `0` + config enforcing → FAIL, `0` → PASS, selinuxfs 없음 + cgroup 보임 → PASS, 읽기 오류·cgroup 안 보임 → 모름, DMS 밖 노드는 정보), S7 프로브 판정 단위 테스트(노드별 사유 ①~⑥, ② 는 WARN, 교차 노드 확인은 재시도 뒤 판정, FS 종류·옵션만으로는 FAIL 하지 않음), S10 경로 대조 판정, `test_check_scripts_context`, `test_prod_k8s_overlay`(metallb) |

- 남은 코드 보강은 ⑤ 뒤에 따로 합니다.
  - B4: migrate 도중 재연결 감지 → 실패(우선순위 낮음)
  - B1: 25006 처리·readyz 쓰기 확인(우선순위 낮음)
  - B2·B6: 선택
  - B5: 보류

---

## 10. 테스트베드 리허설 (dmsk8s 만)

**불변 조건**
- kubectl 은 늘 `--context dmsk8s` 로 씁니다.
- ssc 테스트베드(노드·CoreDNS·`pkg-01:5000`·mgmt 마운트)와 dmsk8s2 는 건드리지 않습니다.
- **라이브 d16 이 쓰이고 있습니다.** 파괴적 시험은 사용자 승인과 시간대 조율 뒤에만 합니다.
- testbed IaC 변경은 testbed 저장소의 몫입니다(공용 경로 export·마운트, 워커 Rocky 9 재구성, prod minor 로 맞추기 포함).
- 일반 검증은 Ubuntu 테스트베드로 충분합니다(#49). EL9 고유 항목과 SELinux 프로브는 Rocky 워커에서 확인합니다(T-SE).
- **DMS 상태(아티팩트·PG·레지스트리·백업)는 테스트베드에서도 공용 경로 `SHARED_DIR=/dms_shared` 에 둡니다**(3노드 모두 마운트, #45). 값은 `deploy/overlays/testbed-k8s/values.env` 에 커밋합니다(③).
  - 인스턴스의 노드 이동, 노드 간 가시성, 소유권, fsync 를 prod 와 같은 방식으로 증명하기 위해서입니다.
  - 로컬 디스크는 T6' 비교 측정용 일회용 대상으로만 씁니다.
- 지금 3노드 모두에 있는 공용 경로는 `/mgmt/m2601/storage` 하나뿐입니다.
  - NFS 이고, ssc 와 공유하며, `lookupcache` 옵션이 없습니다(testbed `group_vars/all.yml:53-60`, `playbooks/storage.yml:8-13`).
  - `/cephfs`·`/k8s_pv` 는 워커 전용입니다(`playbooks/cephfs.yml:31-36`, `storage.yml:90-91`).
  - 그래서 T3 에서 dmsk8s 전용 export 를 새로 만듭니다.
  - cp1 의 실제 마운트는 kubectl 로 볼 수 없어서 IaC 기준으로 판단했습니다.

**자원(실측)·플랫폼**
- cp1 은 약 920m/1.5GiB 가 남습니다(allocatable 1800m/1.81GiB, 사용 880m/240Mi). w1·w2 는 메모리 limit 이 이미 239~282% 과다 할당입니다.
- 축소값: PG 100m/256Mi(`shared_buffers` 64MB), Envoy 50m/128Mi, 레지스트리 50m/64Mi. cp1 합계는 약 200m/448Mi 입니다(세 축소값의 합. S7·측정용 일회용 파드는 따로).
- api·controller 는 cp1 메모리가 모자라 워커에 그대로 둡니다. 마스터 쪽 읽기 경로는 S7 프로브와 cp1 의 레지스트리·PG 가 대신 증명합니다.
- **공용 경로 용량**
  - 테스트베드 NFS 서버는 호스트이고, export 는 호스트 `/data`(루트 ext4, 88%, 58G 남음)에 있으며 파일 핸들 경계도 아닙니다(`all.yml:54-55,73-74`). 새 export 도 같은 곳에 놓입니다(T3 위치 규칙).
  - 그래서 PG·레지스트리(필요한 태그만)·백업을 합쳐 작게 두고, 테스트베드 백업 보존은 줄입니다(예: 24개) [권고].
- 플랫폼: cp1 Ubuntu 24.04.4, w1·w2 는 Ubuntu 24.04.4 → Rocky Linux 9(SELinux permissive)로 재구성(T-SE), CRI-O 1.34.9, K8s v1.34.6(1.34 EOL, prod minor 로 맞춤 — #4 [해석]), Cilium 1.19.4 KPR false + kube-proxy(prod 와 같은 모드), control-plane 1대.

| 단계 | 내용 | 확인 | 승인 |
|---|---|---|---|
| T0 기준선 | preflight·verify·e2e 9 passed. 테스트베드 `SHARED_DIR`(`/dms_shared`)과 그 상위가 스토리지로 등록돼 있지 않은지 | 현재 상태 기록 | — |
| T1 B3 | ① dN. (선택) 제출 직후 DB 끊기 주입 | FAILED 오기록 없음, vcjob 채택 | 주입만 |
| T2 로그인 | ② dN. 가짜 메일 릴레이 | 다른 아이디가 함께 잠기지 않음, 위조 X-Real-IP 무시, 6자리, 1000/h, 재설정·해제, 스위치, 대기 중 healthz | — |
| T3 공유 FS | 아래 「T3 세부」 | 아래 「T3 세부」 | base 전환 |
| T4 레지스트리 | cp1 에 레지스트리 이미지 반입·고정(7-5 ②, `99-dms-pinned.conf`) → Deployment(10.112.0.50, 미사용 실측) replicas 1(cp1) → 인터넷 노드 역할의 k8s-w1(빌드 노드, `deploy/README.md:39`)이 lock 의 애드온을 skopeo 로 직접 push(amd64) → d16·buildah push. **`pkg-01:5001` 은 병행 유지** | 아래 「T4 확인 항목」 | cp1 재부팅, 전환 |
| T5 | 결번(v4 의 csi 감지 단계 삭제) | — | — |
| T6 CNPG | 아래 「T6 세부」 | 아래 「T6 세부」 | — |
| T6' 장애 | 아래 「T6' 세부」 | 15분 매달림 가설, 승격 시간, kubelet·etcd 영향 | **필수** |
| T7 DB 전환(선택) | 동결 창에서 pkg-01 `dmsdb_k8s` 덤프 → import → URL 교체 → 재시작 | 6-3 게이트. 롤백 = URL 원복(그 사이 쓰기는 덤프로) | **필수** |
| T8 입구 | v3 의 입구 리허설 항목: GW API·EG(cp1, hostNetwork 바인드), 병행 Gateway, luminous haproxy `send-proxy-v2`, 컷오버(MetalLB 유지) | **비 root 443 bind(sysctl)**, `ClusterFirstWithHostNet` 으로 xDS 연결, 413(21MB), 15초 넘는 스트림, PROXY 필수·optional, X-Real-IP 제거 | 컷오버 |
| T9 설치기 | `install.sh --context dmsk8s --plan`: Volcano 는 C, CNPG·GW·EG 는 A(T6 전) 또는 D=(T6 뒤). A·P·X 조합은 **일회용 클러스터**에서 | `test_install_detect` 와 실물 대조 | 일회용 클러스터 |
| T-SE EL9 워커 | 아래 「T-SE 세부」 | 아래 「T-SE 세부」 | 재구성(라이브 d16·빌드 노드 영향) |

**T3 세부**
- 내용
  - dmsk8s 전용 export 를 새로 만듭니다.
    - 위치는 호스트의 형제 디렉터리 **`/data/dms_shared`** 입니다. `/data/mgmt_storage`(VM 서브넷 전체에 export, `scripts/nfs-export.sh:5-6,17`)와 `/data/k8s_pv`(등록 스토리지, `all.yml:75`) 아래에는 두지 않습니다.
    - 대상은 dmsk8s 노드 IP 만, 옵션은 `rw,sync,no_subtree_check,no_root_squash` 입니다.
  - 3노드 모두 `/dms_shared`(밑줄 경로)에 `lookupcache=positive` 로 마운트합니다.
  - 테스트베드의 공용 스토리지가 NFS 인 사례입니다. NFS 는 일관성이 가장 약해서 하한을 실증하기 좋고, DMS 쪽에는 NFS 전용 분기가 없습니다.
  - `deploy/overlays/testbed-k8s/values.env` 를 커밋하고(③), 초기화 스크립트를 돌린 뒤 **옛 base(`/cephfs/dms/artifacts`)를 소유권을 보존해 복사하고 base 를 전환**합니다.
- 확인
  - 다중 노드에서 미리보기·실행·scan·사용량·정리
  - S7 통과(cp1 포함, ①~⑥)
  - **S7 음성 대조**
    - root_squash export → FAIL
    - `SHARED_DIR=/cephfs` → 「마운트 없음」 FAIL(cp1 에 마운트가 없음). `/cephfs/dms` 밑에 `.probe-*` 만 쓰고 지움
    - 같은 export 를 `nosharecache,lookupcache=all` 로 붙인 별도 경로 → 옵션 검사가 아니라 ② 동작(새 이름·rename 교체)으로 **WARN**(FAIL 아님)이 나야 함. 그 경로에서 ①③④⑤⑥ 은 PASS 여야 함(교차 노드 재시도 원칙 확인)
  - **가드 음성 대조**: 마운트가 없는 경로(예: cp1 의 `/cephfs`)를 주면 초기화 스크립트와 재활용 런북이 거부하고 디렉터리를 만들지 않아야 함

**T6 세부**
- 내용
  - **A 경로**로 설치합니다(오퍼레이터 w1·w2, PriorityClass).
  - Cluster 3개를 둡니다. 테스트베드 오버레이만 cp1·w1·w2 로 완화합니다.
  - SC `dms-pg-shared` + PV ×5(`/dms_shared/dms/pgdata`)
  - `-rw` 전환, switchover, pg_dump·복구, initdb import
- 확인
  - 6-1 airgap 함정 셋, 9443 dry-run, ANY 1 커밋 지연, S10 폴링 판정·사전 검사·**경로 대조 프로브**
  - **26:26 사전 소유**: hostPath 가 fsGroup 을 적용하지 않는 것을 확인합니다. 0 소유로 두는 음성 대조에서는 initdb 가 실패해야 합니다.
  - **PV 순환**: 인스턴스 1개 destroy → 같은 이름 PVC 가 여분 PV 에 바인딩 → 옛 PV 는 Released → 런북 → Available 복귀
  - **Cluster 삭제·재생성**: PVC 가 함께 지워지는지, PV 3개가 Released 가 되는지, 셋째 인스턴스가 Pending 이었다가 런북 뒤에 뜨는지
  - **노드 이동**
    - 잠시 `podAntiAffinityType: preferred` 로 바꾸고 w1 을 cordon 합니다.
    - w1 의 인스턴스 파드를 지웁니다.
    - 다른 노드에서 같은 PVC 로 기동해야 합니다. pg_basebackup 이 없고 timeline 이 같아야 합니다.
    - 끝나면 원복합니다.

**T6' 세부**
- primary 를 워커로 옮긴 뒤 그 VM 전원을 차단합니다. keepalive 없이 한 번, 넣고 한 번 합니다.
- drain(smartShutdown 0 대 180)
- apiserver 불통 흉내
- 공용 스토리지 일시 차단(테스트베드는 export 차단)
- 3-2 「G2 보충」 재마운트 절차 한 번(상태 파드 비우기 → 재마운트 → 복귀)
- **fio·pgbench 공용 대 로컬**
  - 로컬은 렌더 오버레이 밖의 일회용 측정 대상입니다(노드 임시 디렉터리 + 일회용 파드, 끝나면 삭제).
  - CNPG 도 fio·pgbench 로 먼저 재 보라고 권합니다(`cnpg-storage.md:42-48`).

**T-SE 세부**
- 내용
  - w1·w2 를 **한 대씩** Rocky Linux 9(가능하면 prod 와 같은 9.6)로 재구성합니다. w1 → 검증 → w2 순서이고, 그동안 다른 워커가 d16 을 받습니다(api·controller 는 지금 w2 에 있음). k8s-w1 은 빌드 노드라 재구성 중에는 포탈 빌드가 안 됩니다(`deploy/README.md:39`).
  - SELinux 는 `SELINUX=permissive` 로 설치합니다. disabled 로 설치하면 enforcing 시험 때 전체 relabel 이 필요해집니다.
  - CRI-O 는 rpm 입니다. 마운트·설정은 testbed IaC 가 다시 붙입니다: `/dms_shared`·`/cephfs`·`/k8s_pv`, `/cephfs-third`(w1)·`/cephfs-secondary`(w2)(`deploy/README.md:38`), 빌드 소스 NFS ro(w1, `:574-576`), 레지스트리 신뢰(`registry-setup.sh nodes --context dmsk8s`, `:127`).
  - 테스트베드 K8s minor 를 prod 에 맞추는 일(#4 [해석])도 이 재구성 때 함께 합니다(cp1 포함, 한 단계씩).
  - T3 전에 끝나면 T3~T9 가 EL9 워커에서 돌고, 늦어지면 Ubuntu 로 진행한 뒤 아래 확인 항목만 봅니다.
- 확인
  1. SELinux 프로브(S2b)
     - 한 워커에서 `setenforce 1`(재부팅·relabel 없음) → 그 노드만 **FAIL** 이어야 합니다(모름·PASS 가 아님).
     - `setenforce 0` → PASS.
     - config 를 `SELINUX=enforcing` 으로 바꾸고 런타임은 0 으로 둠 → 「재부팅하면 enforcing」 FAIL. 끝나면 config 를 원복합니다.
     - cordon 해도 agent DS 는 그 노드에 남고 enforcing 동안 agent·잡이 실패할 수 있으므로 시험 창은 짧게 잡습니다.
  2. EL9 고유: CRI-O 패키징·drop-in 경로(`/etc/crio/crio.conf.d/99-dms-pinned.conf`, `/etc/containers/registries.conf.d`), `/etc/sysctl.d`(`fs.protected_*`), firewalld 유무와 FORWARD 영향, 경로 차이
  3. S6·S7·agent·잡 정상

**T4 확인 항목**
1. 인터넷 노드 직접 push: k8s-w1 호스트에서 ClusterIP 로 `skopeo copy --preserve-digests` 한 digest 가 lock 과 같고(S5 대조), 세 노드 `crictl pull`(S6)이 성공해야 합니다. 레지스트리 자기 이미지는 oci-archive 로 cp1 에 넣는 7-5 ② 경로를 그대로 밟습니다.
2. 7-6 GC 절차 전체: env readonly 파싱, 405, 포탈 삭제 409, 복귀 뒤 push
3. readiness: 빈 레지스트리에서 catalog 가 200 인지 봅니다. 공용 스토리지 일시 차단(T6') 때 NotReady 가 되는지도 함께 봅니다.
4. cp1 재부팅(파괴적): 고정 이미지만으로 레지스트리가 다시 뜨는지, 그동안 pull 실패 창. 마스터 급사 공백(7-6 표)은 control-plane 이 1대라 여기서 잴 수 없습니다.
5. 업그레이드: 이미지 교체 중 `maxSurge: 1` 로 pull 실패가 0회여야 합니다.
6. 포탈 빌드 → 릴리스 → `unknown_tag` 통과 → 노드 pull 의 종단 흐름
7. 센티널이 없는 디렉터리를 가리키면 기동을 거부해야 하고, init 이 센티널을 만들지 않아야 합니다.
- v4 에서 이어지는 항목
  - amd64 digest 보존
  - Link 페이지·404·405 매핑, 태그 삭제 202, 병렬 pull RSS
  - 오버레이 전환: d17 → 새 레지스트리 → 릴리스, `ALLOW_IMAGE_CHANGE=1`, **`control_state.job_image` 재릴리스**
  - 「모든 이미지가 새 주소로 시작」 게이트. 테스트베드는 인터넷이 있어서 빠진 이미지를 조용히 받아 오기 때문입니다.

**테스트베드로 증명할 수 없는 것**
- 마스터 1대 장애(etcd 정족수·primary 이동·Avi 멤버 제외가 동시에 일어남), 실제 마스터 3대의 PG anti-affinity, 레지스트리의 다른 마스터 재기동
- 8 vCPU 실부하
- prod 공용 스토리지의 의미론·지연·장애 거동(제품·프로토콜이 테스트베드와 다를 수 있음). prod 마스터·워커에서 도는 S7 이 유일한 보증입니다.
- Avi·ESXi 동작
- RHEL 9.6 자체(Rocky 9 로 근사), 마스터의 EL9(cp1 은 Ubuntu), 실제 taint 키
- 테스트베드를 prod minor 로 맞추기 전까지의 K8s·CRI-O 버전 차이(지금 1.34.6, #4 [해석])

---

## 11. 남은 확인 요청 (운영자 정보)

이미 답을 받은 항목은 뺐습니다.
- 앞선 회차에서 답한 것: K8s 버전 범위, apiserver→워커, ESXi, CNPG 설치 허용(#34), PITR, 업그레이드 주기, root 보존(#9), C1~C3, 레지스트리 저장 위치, U1(#28), V1(#42, 설치 때 VS 화면에 PROXY v2 옵션이 보이는지만 확인), K1 대부분(#41), K5 광고 여부(#43, 대역 겹침은 「K5 잔여」), W3(#44), 공용 스토리지 경로(#8)
- 이번(5회차)에 답한 것
  - v5 의 Q1~Q6: 레지스트리 1개(#46), G3 비필수(#47), PG 볼륨(#40), SELinux 꺼짐 전제(#48), K8s minor 는 prod 를 따름(#4, 테스트베드도 따름 [해석]), 테스트베드 Rocky 워커(#49)
  - **K1 의 Cilium 모드**: KPR 없음, kube-proxy 있음(#41)
  - **N1 의 G8·부팅 재마운트**: 장애 때 대기, `/etc/fstab`(#50). v4 N6 도 함께 닫힘
  - **airgap 반입**: 외부 인터넷이 되는 지정 노드 1대(#51). 세부 정보는 K8 로 받습니다.

| 받는 사람 | # | 항목 | 왜·바뀌는 것 | 시점 |
|---|---|---|---|---|
| 사용자 | U3 | 경보 수신 경로·임계, 아이디별 총 시도 상한 값, 오픈 첫 주 가입·재설정 규모 | ② 범위, 1000/h 의 여유 | ② 전 / 오픈 전 |
| 사용자 | U4 | 전체 스위치를 켜는 방법: env + 재시작(권고) 또는 포탈 스위치 | ② 범위 | ② 전 |
| 사용자 + DNS | U5 | 포탈 FQDN, 신청 소요 기간, 첫 설치인지 이관인지(이관이면 `control_state.job_image` 재릴리스) | SAN, `PORTAL_DOMAIN`, 컷오버 | 착수 전 |
| 사용자 | U6 | 공용 스토리지 밖 사본의 보관처·주기·암호화·담당, 아티팩트도 밖에 둘지 | 공용 스토리지 손실 때 404 를 받아들일지 | 설치 전 |
| 사용자 | U7 | 테스트베드 파괴 시험 승인·일정(T1·T3·T4·T6'·T7·T8·T-SE), `/dms_shared` export(testbed IaC, 호스트 `/data/dms_shared`), 워커 w1·w2 Rocky Linux 9 재구성 일정(T-SE, 한 대씩, 라이브 d16·k8s-w1 빌드 영향)과 그때 K8s minor 를 prod 에 맞추는 일정(#4 [해석]) | 일정 | 리허설 전 |
| 사용자 | U8 | `EMAIL_DOMAIN`·`LOCAL_ADMIN`·메일 서비스명, 스토리지 등록표, 도구 정책, 면제 운영자, 관리 클러스터와 RoleBinding 실례 5건 | 렌더 필수 키 | 설치 전 / 오픈 전 |
| Avi | V2·V3 | 443 전용 L4 프로필, 6443 프로필, 풀 = 마스터 :443, 모니터 | 공용 프로필을 고치면 6443 이 깨짐 | 설치 전 |
| Avi + 네트워크 | V4 | SE SNAT 대역, 그 대역에만 마스터 :443 허용 | 원 IP 위조 차단 | 설치 전 |
| Avi | V5 | 유휴 타임아웃, VS 로그의 원 IP, 출발지 차단 운영 | 경보 대응 수단 | 오픈 전 |
| 클러스터 운영자 | K1 잔여 | 배포판·설치 도구, **prod K8s minor**(DMS 와 테스트베드가 따를 값, #4), Cilium 버전·routing mode·hostFirewall·`cni.chainingMode=portmap` 체이닝 여부, CRI-O 버전·공급원(OBS/rhocp)·프록시 env, 아래 「K1 노드 셸」 결과 | 애드온 고정 버전·CRI-O 미러(6-5), hostPort 대안(4-3), SELinux 전제 확인(2-3) | 착수 전 |
| 클러스터 운영자 | K2 | **GW API CRD·Envoy Gateway·Volcano** 를 DMS 가 설치해도 되는지(`ADDON_INSTALL`). 이미 있는 CNPG·Volcano 를 다른 팀이 쓰는지(업그레이드 주체) | A 경로 허용 범위 | 설치 전 |
| 클러스터 운영자 | K3 | control-plane taint·라벨 키, 잡 워커 집합·taint(= SELinux 판정 대상인 DMS 워커, 2-3) | nodeSelector·toleration, S2b 판정 대상 | 설치 전 |
| 클러스터 운영자 + 네트워크 | K5 잔여 | 서비스·파드 CIDR 이 사내 대역(메일 릴레이·PV 백엔드·NTP·DNS 등)과 겹치는지 | 겹치면 파드가 그 대역에 닿지 못함 | 설치 전 |
| 클러스터 운영자 | K6 | Cilium underlay 포트, 마스터 :443·Envoy 보조 포트가 비어 있는지 | 마스터 워크로드 | 설치 전 |
| 클러스터 운영자 | K7 | kubeadm patches, kubelet 예약·`shutdownGracePeriod`, 마스터 `/etc/sysctl.d`·전 노드 `/etc/selinux/config`·crio drop-in 을 관리하는 IaC 담당 | 2-2, 2-3, 4-3 | 설치 전 |
| 클러스터 운영자 | K8 | **인터넷 노드**: 첫 설치에 쓸 노드(= 지정된 빌드 노드) 이름, 인터넷 경로(직접·프록시·사내 CA), skopeo·podman, 마스터 3대와 설치 호스트로 파일(레지스트리 이미지, `kubectl-cnpg`)을 옮기는 수단(ssh 등). 마스터에 skopeo·`99-dms-pinned.conf` 를 둬도 되는지(기존 `pinned_images`), 설치 호스트·컨텍스트 이름 | 7-5, S13, `KUBE_CONTEXT` | 착수 전 |
| 클러스터 운영자 | K11~K16 | dms ns 정책(NetworkPolicy·Kyverno·Gatekeeper·quota — **hostPath PV·hostNetwork·privileged 파드를 막는지**, (선택) VAP 허용), 워커 allocatable, CoreDNS 상위, sysctl, Envoy 접근 로그 수집처, 「k8s 연동」 값 | 운영 값, S1 | 설치 전 / 오픈 전 |
| VMware | E1 | etcd 데이터스토어 지연(fsync p99 < 10ms), CPU·메모리 예약 | etcd 안정성 | 설치 전 |
| 스토리지(공용) | N1 | 아래 「N1 세부」 | 장애 때 대기형이라 스토리지가 멈추면 전부 멈춤. PG 손상·nobody 소유 방지 | 착수 전 |
| 스토리지 | N7·N8 | PV 백엔드 root 매핑·chown, SC·PV 예시, 등록 스토리지 마운트의 노드 간 일관성(G3 와 같은 조건) | 잡 sync·rm | 오픈 전 |
| 네트워크 | W1·W2 | API FQDN·SAN, IPv4 단일 스택 | 리드타임 | 착수 전 |
| 보안 | S1 | 아래 「S1 승인 항목」 | 설치 선행조건 | 착수 전 |
| 보안 | S2·S3·S5 | 감사 보존·위치, 포탈 인증서(SAN = FQDN + VIP-A), 비밀값·공용 스토리지 밖 사본 보관 책임 | — | 설치 전 / 오픈 전 |
| 메일 | L1·L2 | 릴레이 IP·포트·키, 허용 클라이언트(모든 워커), 프로토콜 | 인증 메일 | 오픈 전 |
| 전 담당 | X1 | NTP | 인증서·etcd·토큰·로그 | 설치 전 |

**N1 세부**(공용 스토리지 담당에게)
- 제품·종류·프로토콜, `SHARED_DIR` 로 줄 경로(마운트포인트 또는 그 아래)
- 이중화 방식, 페일오버 시간, 유지보수 창
- 3-2 의 **G7(fsync 내구성)** 을 만족하는지(G8 은 5회차 답으로 충족)
- G4~G6 사전 확인. G3 는 필수가 아니지만 미달이면 S7 이 WARN 하고 영향이 있습니다(3-2). NFS 면 `lookupcache=positive` 로 마운트할 수 있는지
- 유지보수로 다시 마운트하기 전에 DMS 상태 파드를 비우는 절차(3-2 「G2 보충」)에 동의하는지
- 용량·쿼터
- **같은 스토리지를 마운트하는 DMS 밖 호스트 목록**(G11)

**K1 노드 셸**(마스터 1대·워커 1대에서. 결과만 받습니다)
```
getenforce; grep ^SELINUX= /etc/selinux/config
crio --version; rpm -q cri-o skopeo
crio config 2>/dev/null | grep -E '^\s*(pinned_images|signature_policy|default_runtime)'
systemctl show crio -p Environment
findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS -T <SHARED_DIR>; ls -ld <SHARED_DIR> <SHARED_DIR>/dms
```

**S1 승인 항목**
- dms ns PSA privileged, root 컨테이너, hostPath(artifacts·registry·backup) + **hostPath PV(pgdata ×5)**, 공용 스토리지 root 보존(squash 없음)
- privileged 빌드 파드, 에이전트의 `/` 마운트
- **DMS 노드(RHEL 계열) SELinux 꺼짐**(enforcing 아님 — permissive 또는 disabled, #48), 설치기의 일회용 privileged 프로브 DS(S2b)
- **클러스터 안 익명 평문 레지스트리**(서비스 CIDR 이 광고되지 않으므로 클러스터 안으로 한정), 인터넷 노드의 상류 이미지 반입(#51)
- **Envoy ns PSA privileged(hostNetwork) + 마스터 sysctl `ip_unprivileged_port_start=443`(또는 root Envoy)**
- 평문 메일 릴레이, 장기 SA 토큰

---

## 12. 위험과 대응

| # | 위험 | 영향 | 대응 |
|---|---|---|---|
| 1 | **공용 스토리지 하나에 모든 상태가 몰림** | 정지하면 DMS 전체 정지, 손실되면 전부 손실 | N1 이중화, 공용 스토리지 밖 사본(덤프·시크릿·DMS 태그), 쿼터·du 경보 |
| 2 | 공용 스토리지 정지(장애 때 대기형)가 마스터에 번짐 | kubelet·모니터링이 D state, etcd 에 영향(추정) | T6' 측정. 마스터는 hostPath(볼륨·PV)로만 접근. 마스터 kubelet 은 마운트 가드로 막지 않음(G2). 재마운트는 G2 보충 절차 |
| 3 | PG 커밋 지연(공용 스토리지 fsync + 동기 복제) | API 지연으로 그대로 번짐 | T6' 측정(로컬은 비교값으로만). 로컬 디스크로 대체하지 않음(#31). 나쁘면 스토리지 쪽(N1)에서 튜닝 |
| 4 | 같은 PGDATA 를 두 서버가 동시에 엶 | DB 손상 | 6-6 운영 규칙(hostPath PV 는 RWO 를 강제하지 않음) |
| 5 | B3 창(장애조치가 창을 넓힘) | 잡 FAILED 오기록, 추적 없는 vcjob | **① 먼저**, 반입하는 dms 는 B3 포함 dN 이상 |
| 6 | CNPG 수명 주기 | EOL·K8s 미지원 | 연 2회 이상 점검 창(EG 와 묶음), EOL 경고 |
| 7 | 남이 설치한 애드온이 비호환이거나 둘 이상 | 설치 실패, 이중 오퍼레이터 | 8-2(P·X 는 FAIL, dry-run 으로 판정) |
| 8 | 노드 → ClusterIP pull·push 경로 불가(kube-proxy 있음으로 답을 받았지만 미실증), 런타임 프록시, 서명 정책 | 레지스트리 pull·반입 불가 | S6 게이트(인터넷 노드 포함), S1 의 NO_PROXY·`policy.json` 점검, 대안 B |
| 9 | 레지스트리 1개(마스터 장애), GC 실수, 롤링 겹침 | 약 90~110초 동안 새 pull·빌드 push 불가, 레이어 삭제, G3 미달 사이트에서 교체 중 push `blob unknown` | 마스터 3대 사전 pull(S11), 7-6 GC 절차, 릴리스는 레지스트리가 정상일 때만, 교체는 빌드·push 가 없을 때(7-3). HA 는 나중에 다시 봄(7-1) |
| 10 | 익명 push·delete | 이미지 변조 → root 잡 | 서비스 CIDR 미광고(#43)라 클러스터 밖 노출 없음. S1 승인. NetworkPolicy 는 선택 |
| 11 | index digest 를 lock·렌더에 적음 | S5 대조 실패, `manifest unknown` | amd64 매니페스트 digest 하나로 통일(7-5 ①), ⑤ 계약 테스트(mediaType 이 index 가 아님). 레지스트리 자기 이미지만 태그 ref |
| 12 | 비 root Envoy 443 bind 실패 | 포탈 불통 | 4-3 sysctl.d, T8 실증 |
| 13 | 상태 디렉터리를 「스토리지」로 등록 | rm·root 잡이 base·pgdata 에 닿음 | ③ 가드, allowlist 축소 |
| 14 | root squash·ID 매핑 어긋남(FS 종류 무관) | 모든 잡 실패, 404 | N1, S7 프로브 |
| 15 | Avi 공용 프로필 편집 | 6443 에도 PROXY 가 켜져 apiserver 불통 | 443 전용 프로필(V2) |
| 16 | 원 IP 위조 | 감사 기록 오염 | 4-3 계약, V4 봉쇄. 원 IP 는 기록용이라 차단 판정에는 영향 없음 |
| 17 | 비밀번호 규칙 없음 → 스프레이 공격 | 약한 계정이 뚫림 | 경보 → 출발지 차단, 대기형 스위치, scrypt 슬롯 |
| 18 | 인증번호 전체 상한을 DoS 에 악용 | 가입·재설정이 최대 1시간 429 | 1000/h 라 공격 비용이 큼(시간당 메일 약 200통), 경보, 운영자 구제(#18) |
| 19 | ⑦ 전에 prod 설치를 강행 | MetalLB·ingress 요구, ARP 충돌 | ⑦ 출하 전에는 prod install 금지 |
| 20 | install.sh 가 current-context 에 기댐 | 엉뚱한 클러스터에 설치 | ⑦ `--context` 전파, `kube-context` 가드 |
| 21 | 8 vCPU 에서 CPU 경합 | etcd heartbeat 지연 | 2-2 requests 상향(K7), PG CPU limit 없음 |
| 22 | airgap 리드타임 | 일정 지연 | K1 잔여·N1·U5·인터넷 노드(K8)를 먼저 발송 |
| 23 | 정적 PV 여분 소진(인스턴스 재생성, Cluster 삭제) | 인스턴스 재생성이 Pending | 여분 2개, S10·S12 WARN, 6-2 런북 |
| 24 | 다른 ns 의 PVC 가 여분 PV 를 차지 | 재생성이 Pending | 전용 SC, (선택) VAP |
| 25 | 상류 비권장(k8s hostPath PV, CNPG 사전 프로비저닝) | 상류 지원 범위 밖 | 근거 기록(6-2), S7 실측, 여분·런북(5회차 확정) |
| 26 | RHEL 계열 노드의 SELinux enforcing(설치 뒤 재부팅으로 다시 켜지는 경우 포함) | agent 가 모든 스토리지를 Missing 으로 봄, 잡·api 가 EACCES 로 엉뚱한 사유를 냄(추정) | 필수 전제(2-3), S2b 노드 프로브 FAIL(재부팅하면 enforcing 이 될 노드 포함), `/etc/selinux/config` 로 고정, S12 안내, T-SE 실증 |
| 27 | K8s·CRI-O 1.34 EOL(2026-10-27, 정보) | prod 가 1.34 면 상류 보안 패치 없음 | DMS 는 prod minor 를 따름(#4). 올릴 때는 6-5 순서(애드온 먼저) |
| 28 | CRI-O drop-in 덮어쓰기 | 기존 pinned 목록이 사라지거나 DMS 고정이 무효 | `99-` 이름, S1 에서 기존 목록 합침, 적용 뒤 `crio config` 실효값 확인 |
| 29 | 마운트가 빠진 노드에서 상태 디렉터리를 새로 만듦(`mkdir -p`·`install -d`·`DirectoryOrCreate`) | 그 노드의 PG·레지스트리가 로컬 디스크에 씀(#31 의 조용한 위반) | hostPath 는 `Directory` 만, S0·런북 가드(포함 마운트 TARGET ≠ `/`, `.dms-shared-id`, 상위 디렉터리 생성 안 함), 레지스트리 센티널은 S0 만 씀, S10·S12 경로 대조 |
| 30 | 공용 스토리지가 G3 미달(S7 ② WARN 으로 진행) | 멀티 노드 rank ENOENT, api·controller 가 잡 산출물을 늦게 봄 | WARN 에 영향을 표시, 다른 교차 노드 확인은 재시도 뒤 판정(오판 방지, S7), NFS 는 `lookupcache=positive` 권장(N1) |
| 31 | 인터넷 노드를 쓸 수 없음(지정 해제·차단) | 애드온·DMS 이미지 반입과 업그레이드 불가(이미 반입한 이미지는 동작) | K8 로 지정 확인, `images.lock` 으로 재현, DMS 태그는 레지스트리 밖 사본(7-6 백업) |

---

## 출처

- **앞선 문서**
  - 「1차」 `vip/final.md`, 「2차」 `idrev/final.md`
  - 「v3」(v3 변경 절, 2(마), 4·5절, `:330-336`·`:362-370`·`:631-634` 의 60/h·0s 원문)
  - 「v4」(11절 K5·N6 원문 `:738,747`), 「v5」 `design/dms-k8s-prod-design-v5.md`(5회차 반영 전)
  - 「PG 검토」 `pgop/final.md`(1·4·5·6·7절)
  - REG 제품·REG 접근·설치 순서(원자료 `reg/`)
- **v5 하위 보고**(조회 2026-11-02, 원자료 `v5/dl/`·`v5/regha/`·`v5/plat/dl/`): 공용 스토리지·PG 볼륨, 레지스트리 이중화, 플랫폼(RHEL·CRI-O·Cilium).
- **코드**(워크트리 7483ba55)
  - B3: `src/dms/execution_volcano.py:206-245,406-413`, `execution_manifests.py:383-384,899-913`, `stepper.py:709-734,1105-1113`, `pod_gc.py:53-62`
  - 레지스트리·이미지: `src/dms/job_image.py:3-15`, `repositories/control.py:132-143`, `build_manifests.py:125,316-336`, `registry.py:17-20,138-276`, `routes_registry.py:61-104`, `routes_releases.py:83-97`, `repositories/builds.py:7-19`, `purge_runner.py:50-52`
  - 공유 FS: `artifact_base.py:115-129`, `artifact_files.py:21-32`
  - 계정: `repositories/accounts.py:107`
  - 배포
    - `deploy/k8s/00-namespace.yaml:17-19`, `40-api.yaml:61-65,100,117,153-167`, `41-controller.yaml:45,62,79-93`, `50-agent-daemonset.yaml:86-88,92,99-100,149-162`, `30-migrate-job.yaml:15,22`
    - `deploy/install.sh:30-37,72-80,125-128`, `preflight-cluster.sh:57-60`, `deploy/docker/build-and-push.sh:29-30,44-45`
    - `overlays/prod/values.env.example:22-32`(`SHARED_FS` `:27-29`), `overlays/prod/render.sh:76,99`, `overlays/prod/patch-config.yaml:16-17,24,27`, `overlays/prod/README.md:53,73`, `deploy/README.md:38-39,127,135-141,252-258,277,544-547,561-565,574-576,619-624,647-658`(빌드 노드·인터넷·프록시는 `:39` 이후)
    - `deploy/overlays/testbed-k8s/`(kube-context·kustomization·patch-config·patch-ingress·patch-metallb 뿐, values.env·render 없음)
    - `deploy/testbed/dms-shared-fs.yml:99-103`(`findmnt -n -T … -o FSTYPE` == ceph), `tests/test_release_manifest_contract.py:190-210`, `docs/BACKLOG.md:352-358`
- **Avi**: `design/alb_tcp_application_profile.go:17,20`. 최신 SDK 에서 `proxy_protocol_enabled`·`proxy_protocol_version` 이 Enterprise·Essentials·Basic 모두에서 허용됩니다.
- **EG 소스**: `idrev/ctl/eg_envoyproxy_types.go:577-582`, `design/eg/internal_infrastructure_kubernetes_proxy_resource.go:512-524,561-566`, release/v1.9 `internal/xds/bootstrap/bootstrap.go:31-33,42-43`, `internal/infrastructure/kubernetes/proxy/resource.go:123`
- **CNPG**
  - `pgop/dl/cnpg_repl.txt:224-226,267-300`, `pgop/dl/cnpg-1.30.1.yaml:20995-21050`
  - `docs/src/storage.md:42-48,316-336,369-443`, `release_notes/v1.30.md:349-356`, `security.md:276-291`, `kubernetes_upgrade.md:134-147`, `cloudnative-pg.v1.md:632-633,3111`
  - `pkg/specs/pods.go:85,447-462`(PGDATA = `/var/lib/postgresql/data/pgdata`), `pkg/reconciler/persistentvolumeclaim/build.go:64`
- **Kubernetes**
  - 문서: `persistent-volumes.md:472,503-505,643,664,1007-1011`, `volumes.md:389-395,407-410,697`, `pod-security-standards.md:139-149,362-380`, `storage-classes.md:193-200`, `security-context.md:379,694-697,839`, `validating-admission-policy.md:12`
  - 그 밖의 문서: kube-controller-manager(`--node-monitor-grace-period` 50s), sysctl-cluster, dns-pod-service, `data/releases/schedule.yaml`
  - 소스 release-1.34: `pkg/volume/hostpath/host_path.go:232-255`, `pkg/volume/local/local.go:515-520`
- **Distribution**
  - 문서: `docs/content/about/deploying.md:282-293,343-366`, `configuration.md:536-575,896`, `garbage-collection.md:83-93`
  - 소스 v3.1.0·v3.1.2: `registry/handlers/app.go:389-401,1126`, `health/health.go:155-166,245-259`, `registry/storage/catalog.go:38-55,147-163`, `driver/filesystem/driver.go:137-165`(PutContent = 임시 파일 + rename), `blobwriter.go:302-316`
- **OpenShift·사례**: openshift-docs `modules/specific-application-storage-recommendations.adoc:15-17,29-40`(2026-03-27), cluster-image-registry-operator README, Red Hat BZ 1873586(comment 7, 2020-09-02)
- **레지스트리 후보**: goharbor/harbor-operator(archived), goharbor.io harbor-ha-helm, zotregistry.dev v2.1.23 `articles/scaleout`, spegel.dev, d7y.io CRI-O
- **PG 볼륨 후보**: rancher/local-path-provisioner v0.0.37 `provisioner.go:55`(`defaultVolumeType = "hostPath"`), `:519-524`(shared 모드 nodeAffinity nil), `:965-971`(`HostPathDirectoryOrCreate`), README:16-18,160-166,196-203. kubevirt/hostpath-provisioner(v0.26.0), csi-driver-nfs v4.13.6
- **CRI-O·containers**
  - release-1.34 `docs/crio.conf.5.md:15,147-149,333-334,499-500,511-513,521-522`, `docs/crio.conf.d.5.md:17-23`, `pkg/config/config_linux.go:32-34`, `server/container_create_linux.go:288,294,361-368`(로컬 사본 `v5/plat/dl/crio_ccl.go`: 없는 hostPath 소스 `MkdirAll`, `rbind`, spc_t relabel 생략), `server/container_create.go:768-801`(로컬 사본 `crio_cc.go`: privileged 는 프로세스 라벨 없음, spc_t 판정)
  - cri-o/packaging README·`10-crio.conf`·`cri-o.spec:26,157`
  - containers/image `containers-registries.conf.d.5.md:19-30`, `containers-registries.conf.5.md:60-64`
- **SELinux**: 전제의 근거는 위 CRI-O·Kubernetes 소스(`config_linux.go:32-34`, `container_create_linux.go:361-368`, `host_path.go:232-237`)입니다. 라벨 대응 조사(container-selinux·fedora-selinux·RHEL 문서)는 v5 에 남깁니다. S2b 프로브 설계는 `crio_ccl.go:288,294`, `crio_cc.go:768-770`, `v5/plat/dl/container.te:1012-1027`(container_domain 은 sysfs 만, `security_t` 0건)입니다. 사이트 조치는 Red Hat RHEL 9 *Using SELinux* 「Changing SELinux states and modes」(`grubby … selinux=0`, 프로덕션 비권장, disabled 에서 되돌릴 때 relabel)입니다.
- **Cilium**: `kubeproxy-free.rst:22,145-146,1259-1262,1599-1606,1621,2151-2160,2290-2294`, `system_requirements.rst:62-74,356-368`, `performance/tuning.rst:171-200`, `security/host-firewall.rst`
- **NFS**: nfs(5)(lookupcache, close-to-open, acdirmin/acdirmax, sharecache), Linux v6.12 `fs/nfs/dir.c:1242,1547-1586`, PostgreSQL 17 18.2.2·18.2.2.1
- **외부 URL**(조회 2026-11-02)
  - skopeo-copy(1): https://raw.githubusercontent.com/containers/skopeo/main/docs/skopeo-copy.1.md (`--override-arch`, `--multi-arch`, `--preserve-digests`)
  - kubernetes/kubernetes#56374(ambient capability 미설정, open): https://github.com/kubernetes/kubernetes/issues/56374
  - 그 밖의 링크(CNPG 1.30 supported releases, Envoy Gateway 호환표, Volcano v1.16.0, Distribution 설정·GC 등)는 하위 보고에 있습니다.
- **기타**: mpifileutils `dsync.1.rst:45-53`
- **testbed IaC**(직접 읽음)
  - `group_vars/all.yml:47-60,72-84`(mgmt export `:54-55`, mgmt 마운트 `:59-60`, `/data` 한계 `:73-74`, `/data/k8s_pv` `:75`, PV share `lookupcache=positive` 와 「Real GPFS/CephFS are cache-coherent」 주석 `:77-82`), `inventory/hosts.ini`
  - `playbooks/storage.yml:8-13,90-91`, `playbooks/cephfs.yml:31-36`
  - `scripts/nfs-export.sh:5-6,10-18`(`/data/mgmt_storage` → `10.10.10.0/24`)
  - `roles/nfs_client/tasks/main.yml:21-27`, `roles/cephfs_client/tasks/boot_guard.yml:1-13`, `roles/crio/templates/99-pkg-01-insecure.conf.j2`
  - `docs/ARCHITECTURE.md:27,41,43,62,128-129,338,342`
  - 호스트 `/data` 88%
- **dmsk8s 라이브**(2026-11-02, get·logs 만)
  - 노드: k8s-cp1(control-plane) + w1·w2, Ubuntu 24.04.4, 커널 6.8.0-117, v1.34.6, CRI-O 1.34.9, amd64. allocatable cp1 1800m/1.81GiB, cp1 사용 880m/240Mi
  - Cilium v1.19.4, `kube-proxy-replacement=false`, `bpf-lb-sock=false`, kube-proxy DS(iptables), `routing-mode=tunnel`·vxlan
  - dms-api·controller: `pkg-01:5001/dms:d16`, 둘 다 w2. dms-agent 는 w1·w2 에만 있음(toleration 이 NoExecute 뿐)
  - ServiceCIDR 10.112.0.0/12, CNPG·Gateway CRD 없음
  - Volcano webhook `failurePolicy: Fail`, targetPort 8443
  - PriorityClass
    - volcano-admission·controllers·scheduler 와 csi-nfs-controller 는 `system-cluster-critical`, csi-nfs-node 는 `system-node-critical`
    - 클러스터의 PriorityClass 는 dms-build 10, dms-low 50, dms-mid 100, dms-high 200 과 system-* 둘
- **추정으로 남긴 것**
  - CNPG Cluster 의 `Ready` 조건이 모든 인스턴스가 healthy 일 때만 성립한다는 점
  - Cluster 를 지우면 CNPG 가 PVC 도 함께 지운다는 점(ownerReference)
  - 재활용 때 옛 PGDATA 가 남아 있으면 join 이 실패한다는 점
  - kubelet 볼륨 매니저가 마운트가 돌아온 뒤 파드를 스스로 띄운다는 점
  - 마스터 kubelet 을 마운트 가드로 막으면 정족수 위험이 생긴다는 판단
  - NFS sharecache 때문에 살아 있는 참조가 있으면 재마운트가 옛 옵션을 물려받는다는 점
  - 캐시가 일관된 FS(GPFS·CephFS)는 S7 ② 를 그대로 통과한다는 점, G3 미달 때 지연 상한(NFS 기본 acdirmax 60초)과 교차 노드 재시도 60초가 그 상한을 덮는다는 점
  - 레지스트리 관련: 장애 시간(7-6 표), 빈 레지스트리의 catalog 200, env readonly 의 JSON 파싱, 레지스트리 자기 이미지의 RepoDigest 일치
  - SELinux 관련: enforcing 일 때의 거부 양상(agent `/proc/1`, 잡의 스토리지 접근), 일반 컨테이너(container_t)가 selinuxfs 를 못 읽을 수 있다는 점(그래서 프로브는 privileged)
  - CRI-O 가 RHEL 9 기본 저장소에 없다는 점
  - overlayfs upper 를 NFS 이외의 네트워크 FS 에도 둘 수 없다는 일반화
  - cp1 의 실제 호스트 마운트

---

## 추가로 결정할 것 (사용자)

5회차로 남은 사용자 결정은 없습니다. v5 의 Q1~Q6 은 모두 정해졌습니다(#4·#40·#46~#49). 해석이 들어간 곳 두 가지만 적어 둡니다.
- 테스트베드 K8s minor: 「내 프로덕션에 너가 따라가면 됨」을 테스트베드에도 적용한 것으로 읽었습니다(#4 [해석]). 값은 K1 잔여로 받고, 일정은 U7 입니다.
- SELinux: 「끄는걸」을 enforcing 이 아닌 상태(permissive 포함)로 읽었습니다(#48 [해석]). disabled 만 허용하려면 알려 주십시오. 그러면 S2b 의 `0 → PASS` 가 FAIL 이 되고, 사이트 조치는 `grubby … selinux=0` + 재부팅 하나가 됩니다.

남은 것은 11절의 정보 요청(사용자 몫 U3~U8 포함)입니다.