# DMS 프로덕션 설치 (한 화면 체크리스트)

> **노출 모드 선택** — 이 오버레이는 **MetalLB VIP**(공인 VIP 를 L2 로 광고, 노드
> 죽어도 페일오버)로 포탈을 노출한다. public IP 가 특정 웹 노드 호스트에 직접
> 붙어 있는 환경(MetalLB 없음)이라면 `deploy/overlays/ssc/`(웹 노드 hostNetwork
> 엣지)를 대신 쓴다 — 단일 노드지만 MetalLB 가 불필요하다.

테스트베드 매니페스트(`deploy/k8s/*`)를 base 로 두고 **사이트별 값만** 여기서
덮는다. 아래 6단계면 끝난다 — 각 단계는 한 명령이고, 스크립트가 실패를 **한 줄
사유**로 알린다(파드 로그를 뒤질 일이 없다).

```sh
# 1) 선행 점검(읽기 전용). Volcano/MetalLB/ingress-nginx·PSA·노드 라벨 확인.
PORTAL_VIP=<서비스 VIP> sh deploy/preflight-cluster.sh

# 2) 값 채우기 — 파일 하나. (비밀 아님. 비밀은 4단계.)
cp deploy/overlays/prod/values.env.example deploy/overlays/prod/values.env
$EDITOR deploy/overlays/prod/values.env          # REPLACE_* 를 실값으로

# 3) 이미지 4종 빌드·push (한 번만. 설치와 분리 — 매 설치마다 빌드하지 않는다)
#    dms · dms-agent · dms-mpifileutils · buildah:stable  →  values.env 의 REGISTRY 로
#    deploy/docker/build-and-push.sh 참고. (이미 push 돼 있으면 건너뜀)

# 4) 시크릿·TLS (out-of-band — git 밖, 오버레이가 만들지 않는다)
kubectl -n dms create secret generic dms-secrets \
  --from-literal=DMS_DATABASE_URL='postgresql://USER:PW@HOST:5432/DB' \
  --from-literal=DMS_SHARED_TOKEN="$(openssl rand -hex 32)" \
  --from-literal=DMS_ADMIN_TOKEN="$(openssl rand -hex 32)" \
  --from-literal=DMS_SESSION_SECRET="$(openssl rand -hex 32)" \
  --from-literal=DMS_LDAP_BIND_PW='<LDAP 검색 계정 비밀번호>'
kubectl -n dms create secret tls dms-portal-tls --cert=tls.crt --key=tls.key
#   (LDAP 바인드 DN 은 비밀이 아니라 values.env; 비밀번호만 여기. TLS SAN 에
#    PORTAL_DOMAIN + PORTAL_VIP 둘 다. 익명 바인드 사이트면 DMS_LDAP_BIND_PW=''
#    + values.env 렌더 후 patch-config 의 REQUIRE_AUTH_BIND="false".)

# 5) 설치 — 먼저 dry-run(변경 없음)으로 게이트+렌더+서버검증, 통과하면 실제 설치
sh deploy/install.sh --dry-run
sh deploy/install.sh

# 6) 검증 — PASS/FAIL 한 줄씩
PORTAL_DOMAIN=<도메인> CACERT=<사내CA> sh deploy/verify.sh
```

`install.sh` 가 apply 전에 막아주는 것: 애드온 미설치, `dms-secrets`/`dms-portal-tls`
부재, 시크릿 자리표시자(CHANGE_ME) 잔존, `values.env` 미치환. 전부 **한 줄 사유**로
중단하므로 CrashLoop→로그 트롤링 루프가 없다.

---

## values.env 값 (13개)

`deploy/overlays/prod/values.env.example` 의 주석에 각 값의 뜻·예시가 있다.
요약: `REGISTRY` · `DMS_TAG`/`DMS_AGENT_TAG`/`MFU_TAG`(push 한 태그) · `SHARED_FS`
(공유 FS 최상위) · `LDAP_HOST`/`LDAP_USER_BASE`/`LDAP_GROUP_BASE`/`LDAP_BIND_DN` ·
`EMAIL_DOMAIN` · `LOCAL_ADMIN` · `PORTAL_DOMAIN` · `PORTAL_VIP`.

`render.sh` 가 이 값을 오버레이 템플릿에 넣어 `deploy/overlays/.prod-rendered/`
(git 밖)를 만들고, `install.sh` 가 거기서 `kubectl apply -k` 한다. 오버레이
템플릿 자체(kustomization·patch-*)는 손대지 않는다. LDAP URI 콤마 목록(페일오버)
처럼 값 하나로 안 되는 건 렌더 후 `.prod-rendered/patch-config.yaml` 에서 직접
편집하면 된다.

## 선행조건 (오버레이 적용 전에 클러스터에 있어야 함)

| 항목 | 확인 |
|---|---|
| **Kubernetes** 1.3x / **CNI** 임의 | preflight §0 |
| **Volcano** (잡 gang 스케줄러) | `kubectl get crd jobs.batch.volcano.sh` |
| **MetalLB(L2)** | `kubectl -n metallb-system get pods` — 풀·광고는 오버레이가 적용 |
| **ingress-nginx** | IngressClass `nginx` + 컨트롤러에 `--default-ssl-certificate=dms/dms-portal-tls` |
| **PSA** | `dms` 네임스페이스 `enforce: privileged`(00-namespace.yaml) — 사내 정책이 허용해야 잡 파드가 뜸 |
| **PostgreSQL** | 도달 가능 + DB/계정 생성 |
| **공유 FS** | 컨트롤러/잡 노드에 마운트 + `<SHARED_FS>/dms/artifacts`·각 스토리지 `managed_root` 사전 mkdir |
| **공인 존 노드 라벨** | `kubectl label node -l <셀렉터> network-zone=public --overwrite` |

세부(공인 존 제약·페일오버·인증서 재발급·HTTPS 원리)는 `deploy/README.md §10`.
base 스토리지 hostPath 경로(`/cephfs` 등)가 사내와 다르면 문의 — hostPath 패치를 만들어 준다.
