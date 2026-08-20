# DMS 프로덕션 오버레이

테스트베드 매니페스트(`deploy/k8s/*.yaml`)를 base 로 두고 **사이트별 값만** 여기서
덮는다. 테스트베드는 `kubectl apply -f deploy/k8s/` 로 그대로 계속 쓴다 — 이 오버레이는
그 base 를 건드리지 않는다.

```
kubectl kustomize deploy/overlays/prod     # 렌더 미리보기(값 확인)
kubectl apply -k deploy/overlays/prod      # 적용
```

## 0. 선행조건 (오버레이 적용 전에 클러스터에 있어야 함)

| 항목 | 확인 |
|---|---|
| **Kubernetes** | 1.34.x (이미지 kubectl 과 정렬) |
| **CNI** | 임의(Cilium 확인됨) |
| **Volcano** 설치 | `kubectl get crd jobs.batch.volcano.sh` 존재. `05-…yaml`(Queue·PriorityClass)은 이 오버레이가 적용한다 |
| **MetalLB** 설치(L2) | `kubectl -n metallb-system get pods`. 풀·광고는 이 오버레이가 적용한다 |
| **ingress-nginx** 설치 | IngressClass `nginx` 존재. 컨트롤러에 `--default-ssl-certificate=dms/dms-portal-tls`, svc 에 어노테이션 `metallb.io/address-pool: dms-public-pool` (README §5) |
| **PSA** | `dms` 네임스페이스는 `00-namespace.yaml` 이 `enforce: privileged` 라벨을 붙인다 — 사내 보안정책이 이를 허용해야 잡 파드가 뜬다 |
| **PostgreSQL** | 도달 가능 + DB/계정 생성 |
| **레지스트리** | 이미지 4종 push(아래) + (평문이면) 각 노드 CRI-O insecure 등록 |
| **공유 FS** | 컨트롤러/잡 노드에 마운트 + `<base>/dms/artifacts`, 각 스토리지 `managed_root` 사전 mkdir |
| **공인 존 노드 라벨** | `kubectl label node <공인노드…> network-zone=public` |

## 1. 채울 값 (자리표시자 한눈에)

`grep -rn REPLACE_ deploy/overlays/prod` 로 남은 자리를 확인한다.

| 자리표시자 | 파일 | 예시 |
|---|---|---|
| `REPLACE_REGISTRY` | kustomization.yaml(images), patch-config.yaml | `registry.corp.example` |
| `REPLACE_DMS_TAG` / `REPLACE_DMS_AGENT_TAG` / `REPLACE_MFU_TAG` | kustomization.yaml, patch-config.yaml | `d99` / `d80` / `d80` |
| `REPLACE_SHARED_FS` | patch-config.yaml | `cephfs`(→ `file:///cephfs/dms/artifacts`) |
| `REPLACE_LDAP_HOST` / `REPLACE_LDAP_USER_BASE` / `REPLACE_LDAP_GROUP_BASE` | patch-config.yaml | `ldap.corp.example` / `ou=People,dc=corp,dc=example` / `ou=Groups,…` |
| `REPLACE_EMAIL_DOMAIN` | patch-config.yaml | `corp.example` |
| `REPLACE_LOCAL_ADMIN` | patch-config.yaml | 로컬 운영자 계정명 |
| `REPLACE_PORTAL_DOMAIN` | patch-ingress.yaml | `dms.corp.example` |
| `REPLACE_PORTAL_VIP` | patch-metallb.yaml | `203.0.113.10` |

주의: **base 의 스토리지 마운트 경로**(`/cephfs`·`/cephfs-third`·`/cephfs-secondary`)는
`40-api.yaml`/`41-controller.yaml`/`50-agent-daemonset.yaml` 의 hostPath 에 리터럴로 박혀
있다. 사내 마운트 경로가 다르면 base 를 그 경로로 바꾸거나, 오버레이에 hostPath 패치를
추가해야 한다(문의 주면 패치를 만들어 준다).

## 2. 이미지 빌드·push (레지스트리에 있어야 함)

`deploy/docker/build-and-push.sh` (순서: mpifileutils → dms → agent). buildah 는 미러:
`podman pull quay.io/buildah/stable && podman tag … <REGISTRY>/buildah:stable && podman push …`.
4종: `dms`, `dms-agent`, `dms-mpifileutils`, `buildah:stable`.

## 3. Secret (out-of-band — 오버레이가 만들지 않는다)

예제 파일을 apply 하지 말 것(재적용 때 라이브 자격증명을 덮는다). 직접 생성:

```
kubectl -n dms create secret generic dms-secrets \
  --from-literal=DMS_DATABASE_URL='postgresql://USER:PW@HOST:5432/DB' \
  --from-literal=DMS_SHARED_TOKEN="$(openssl rand -hex 32)" \
  --from-literal=DMS_ADMIN_TOKEN="$(openssl rand -hex 32)" \
  --from-literal=DMS_SESSION_SECRET="$(openssl rand -hex 32)" \
  --from-literal=DMS_LDAP_BIND_DN='' \
  --from-literal=DMS_LDAP_BIND_PW=''
```

바인드 계정을 쓰면 DN/PW 를 채우고 patch-config.yaml 의 `DMS_LDAP_REQUIRE_AUTH_BIND` 를
`"true"` 로. 순서: 계정 발급 → Secret 주입 → 값 true (거꾸로 하면 제어면 CrashLoop).

## 4. TLS 인증서 (out-of-band)

사내 PKI 발급분(SAN 에 `REPLACE_PORTAL_DOMAIN` + `REPLACE_PORTAL_VIP` 포함):

```
kubectl -n dms create secret tls dms-portal-tls --cert=tls.crt --key=tls.key
```

## 5. 적용 순서

`apply -k` 는 전부 한 번에 올린다 — migrate Job 이 끝나기 전 api/controller 는
CrashLoop 재시도하다 migrate 완료 후 안정화된다(자기 치유). 깔끔하게 나누려면:

```
# 0) 선행: Volcano·MetalLB·ingress-nginx 설치, 노드 라벨, FS mkdir, 이미지 push,
#    Secret·TLS 생성 (위 §0/§2/§3/§4)
# 1) 렌더 확인
kubectl kustomize deploy/overlays/prod | less
# 2) 네임스페이스·애드온 리소스·RBAC·Config 먼저
kubectl apply -k deploy/overlays/prod           # (전체 적용 — 아래는 그룹 적용 대안)
# 3) migrate 완료 대기
kubectl -n dms wait --for=condition=complete job/dms-migrate --timeout=180s
# 4) 롤아웃 확인
kubectl -n dms rollout status deploy/dms-api deploy/dms-controller
# 5) ingress-nginx svc 에 풀 어노테이션(한 번만)
kubectl -n ingress-nginx annotate svc ingress-nginx-controller \
  metallb.io/address-pool=dms-public-pool --overwrite
```

## 6. 배포 후 검증

```
curl --cacert <사내CA> https://REPLACE_PORTAL_DOMAIN/api/healthz     # 200
# 로그인·admin API·SPA 딥링크 https 200, http→308
# 잡 1건 제출 후 vcjob/파드가 Volcano 로 스케줄되는지
```

세부(공인 존 제약·페일오버·인증서 재발급 등)는 `deploy/README.md §10`.
