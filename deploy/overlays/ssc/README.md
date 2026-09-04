# DMS SSC 설치 — 웹 노드 hostNetwork 엣지 (한 화면 체크리스트)

**모드 요약**: MetalLB 를 쓰지 않는다. public IP 는 웹 노드 호스트 NIC 에 직접
설정돼 있고(사용자 검증 완료 — 방화벽 통과 확인), 그 노드에 고정된 ingress-nginx
가 hostNetwork 로 **방화벽이 연 HTTPS 포트**(`PORTAL_PORT`, 예 30080; 표준이면 443)
를 직접 리슨한다. 사용자는 `https://<public IP>:<PORT>` 로 바로 포탈에 닿는다.
DMS 는 Secure 쿠키·강제 리다이렉트라 **HTTPS 전용**이다(평문 HTTP 포트는 로그인이
조용히 깨진다).

```
사용자 → https://<public IP>:<PORT>   (예: https://203.0.113.10:30080)
  → 웹 노드 커널(호스트 IP, 네이티브 — MetalLB/VIP 광고 없음)
  → ingress-nginx [hostNetwork, 웹 노드 고정] : 그 포트로 https 리슨, TLS 종단(dms-portal-tls), 라우팅
  → dms-api Service(ClusterIP) → dms-api 파드(아무 노드나)
```

overlays/prod(MetalLB VIP) 와 같은 base·앱을 쓰고, 다른 것은 **노출 방식뿐**이다.

> **보안(웹 노드의 public IP 가 곧 노드 IP 인 경우 필수)**: ion2110 처럼 노드의
> 유일 IP 가 public 이면 그 인터페이스에 kubelet(10250)·NodePort(30000–32767)·
> (제어면이면 6443/etcd) 등 클러스터 포트가 함께 뜬다. 방화벽에서 public 쪽은
> **PORTAL_PORT(예 30080) 만** 허용하고 나머지는 전부 차단할 것. 30080 이 NodePort
> 대역이라, kube-proxy 가 그 번호를 NodePort 로 가져가 충돌하지 않도록 hostNetwork
> 로 직접 바인딩한다(이 모드가 그렇게 한다).

## 절차

```sh
# 1) 웹 노드 라벨 (public IP 가 있는 노드)
kubectl label node <WEB_NODE> dms.io/web-node=true

# 2) ingress-nginx 를 hostNetwork 로 설치(웹 노드 고정, 방화벽 포트로 https 리슨) — 애드온
#    values-hostnetwork.yaml 의 controller.containerPort.https 를 PORTAL_PORT(예 30080)로 맞출 것
#    helm:  helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx -n ingress-nginx \
#             --create-namespace -f deploy/addons/ingress-nginx/values-hostnetwork.yaml
#    정적:  기존 컨트롤러에 deploy/addons/ingress-nginx/controller-hostnetwork-patch.yaml 패치
#    (자세히: deploy/addons/ingress-nginx/README.md)

# 3) 값 채우기 — 파일 하나 (PORTAL_VIP 없음; WEB_NODE·PORTAL_PUBLIC_IP·PORTAL_PORT)
cp deploy/overlays/ssc/values.env.example deploy/overlays/ssc/values.env
$EDITOR deploy/overlays/ssc/values.env   # PORTAL_PORT=30080, WEB_NODE=ion2110, PORTAL_PUBLIC_IP=<bond0 IP>

# 4) 이미지 4종(dms·dms-agent·dms-mpifileutils·buildah) 사내 레지스트리에 push (1회, 설치와 분리)

# 5) 시크릿 · TLS (out-of-band) — 인증서 SAN 에 PORTAL_PUBLIC_IP 를 반드시 포함
kubectl -n dms create secret generic dms-secrets \
  --from-literal=DMS_DATABASE_URL='postgresql://USER:PW@HOST:5432/DB' \
  --from-literal=DMS_SHARED_TOKEN="$(openssl rand -hex 32)" \
  --from-literal=DMS_ADMIN_TOKEN="$(openssl rand -hex 32)" \
  --from-literal=DMS_SESSION_SECRET="$(openssl rand -hex 32)" \
  --from-literal=DMS_LDAP_BIND_PW='<LDAP 검색 계정 비밀번호>'
kubectl -n dms create secret tls dms-portal-tls --cert=tls.crt --key=tls.key   # SAN: <public IP> (+도메인)

# 6) 설치 — dry-run(게이트+렌더+서버검증, 변경 없음) 후 실제
sh deploy/overlays/ssc/install.sh --dry-run
sh deploy/overlays/ssc/install.sh

# 7) 검증 — PASS/FAIL 한 줄씩 (사용자 접속: https://<public IP>:<PORTAL_PORT>)
PORTAL_PUBLIC_IP=<public IP> PORTAL_PORT=30080 PORTAL_DOMAIN=<도메인> CACERT=<사내CA> \
  sh deploy/overlays/ssc/verify.sh
```

`install.sh` 가 apply 전에 막아주는 것(전부 한 줄 사유): Volcano/ingress-nginx 미설치,
**웹 노드 라벨 없음**, **ingress-nginx 가 hostNetwork 가 아니거나 웹 노드에 없음**,
`dms-secrets`/`dms-portal-tls` 부재·자리표시자, `values.env` 미치환. 그리고 렌더
산출물에 MetalLB 리소스가 남지 않았는지(삭제 패치 정상)도 확인한다.

---

## prod(MetalLB) 대비 차이

| | overlays/prod | overlays/ssc (이 모드) |
|---|---|---|
| public IP 소유 | MetalLB VIP → ingress svc(LoadBalancer) | 웹 노드 호스트 NIC(직접) → ingress hostNetwork |
| 선행 애드온 | Volcano + MetalLB + ingress-nginx | Volcano + ingress-nginx |
| ingress-nginx | Deployment(replicas 2), Service LoadBalancer | Deployment(replicas 1), hostNetwork, 웹 노드 고정 |
| 값 | PORTAL_VIP + PORTAL_DOMAIN | PORTAL_PUBLIC_IP + PORTAL_PORT + WEB_NODE + PORTAL_DOMAIN |
| 접속 포트 | 443(VIP) | PORTAL_PORT(방화벽이 연 포트, 예 30080) |
| 페일오버 | VIP 재광고(노드 죽어도 자동, ~11s) | **없음 — 웹 노드가 단일 장애점** |

## 트레이드오프 (정직히)

- **웹 노드 = 단일 장애점.** public IP 가 그 호스트에 묶여 있어 노드가 죽으면 포탈
  접근이 끊긴다(페일오버 없음). 제어면·데이터 잡은 계속 돌아 잡 실행에는 영향이
  없다. 포탈 접근 이중화가 필요해지면 웹 노드 2대 + VIP(MetalLB/keepalived)로,
  즉 overlays/prod(MetalLB) 경로로 되돌아가면 된다.
- 웹 노드 호스트의 **80/443 이 비어 있어야** 한다(다른 웹서버 금지).
- 웹 노드를 포탈 전용으로 비우려면 taint(`dms.io/web-node=true:NoSchedule`) — 애드온
  values 에 대응 toleration 이 이미 있다. 필수는 아니다.
- hostNetwork nginx 는 호스트의 모든 IP 에서 리슨한다. 내부 IP 노출이 싫으면
  ingress-nginx `bind-address` 로 public IP 만 바인딩(애드온 values 주석 참조).

## 선행조건 (오버레이 적용 전에 클러스터에 있어야 함)

| 항목 | 확인 |
|---|---|
| Kubernetes 1.3x / CNI 임의(Cilium OK) | `kubectl version` |
| Volcano | `kubectl get crd jobs.batch.volcano.sh` |
| ingress-nginx (hostNetwork, 웹 노드) | `kubectl -n ingress-nginx get pod -l app.kubernetes.io/component=controller -o wide` |
| 웹 노드 라벨 | `kubectl get node -l dms.io/web-node=true` |
| PSA | `dms` 네임스페이스 `enforce: privileged`(00-namespace.yaml) |
| PostgreSQL / LDAP / 공유 FS | prod 와 동일(deploy/overlays/prod/README.md 표) |

세부(공유 FS·인증서·HTTPS 원리)는 `deploy/README.md §10` 과 prod README 를 공유한다.
