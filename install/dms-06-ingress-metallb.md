# Ingress (ingress-nginx) + LoadBalancer (MetalLB) 설치

bare-metal 클러스터에는 클라우드 LoadBalancer가 없어 `type: LoadBalancer` 서비스가 영원히
`<pending>`으로 남는다. **MetalLB**가 그 자리를 채워 실제 IP를 할당하고, **ingress-nginx**가 그 IP
하나로 여러 서비스를 호스트/경로 기반 라우팅한다.

운영 전환 시 달라지는 부분은 §7에 모았다.

- 포탈 설치: [portal-01-setup.md](portal-01-setup.md) (§8이 노출 방식을 다룬다)
- DMS API를 ingress로 노출할 때: `kubernetes/ingress.example.yaml` (mTLS 종단 예시)

> **placeholder 규약.** 아래 `$REGISTRY`(로컬 레지스트리)·`$LB_IP`(MetalLB가 할당한 IP)·풀 대역은
> 환경 값으로 치환한다. 먼저 잡아두면 편하다: `export REGISTRY=<registry-host:port>`.

## 1. 버전 선택 (호환성 필수 확인)

| 구성요소 | 적용 버전 | 근거 |
|---|---|---|
| Kubernetes | `kubectl version` 으로 확인 | 아래 ingress-nginx 지원 범위 안에 있어야 한다 |
| ingress-nginx | **v1.15.1** | 공식 지원표에서 k8s `1.31`–`1.35` 지원 |
| MetalLB | **v0.16.0** | 최신 안정 |

> ingress-nginx는 k8s 버전 지원 범위가 좁다. 업그레이드 전 반드시
> [지원표](https://github.com/kubernetes/ingress-nginx#supported-versions-table)를 확인한다.
> 범위를 벗어난 조합은 admission webhook 실패나 CRD 불일치로 조용히 깨진다.

## 2. 이미지 미러링 (폐쇄망/로컬 레지스트리)

노드가 `registry.k8s.io`·`quay.io`에 직접 나가지 못하므로 로컬 레지스트리(`$REGISTRY`)로 미러링한다.
인터넷이 되는 작업 머신에서:

```bash
# ingress-nginx (매니페스트가 digest 고정이므로 digest까지 함께 pull)
C="registry.k8s.io/ingress-nginx/controller:v1.15.1@sha256:594ceea76b01c592858f803f9ff4d2cb40542cae2060410b2c95f75907d659e1"
W="registry.k8s.io/ingress-nginx/kube-webhook-certgen:v1.6.9@sha256:01038e7de14b78d702d2849c3aad72fd25903c4765af63cf16aa3398f5d5f2dd"
docker pull "$C" && docker tag "$C" $REGISTRY/ingress-nginx-controller:v1.15.1
docker pull "$W" && docker tag "$W" $REGISTRY/ingress-nginx-certgen:v1.6.9
docker push $REGISTRY/ingress-nginx-controller:v1.15.1
docker push $REGISTRY/ingress-nginx-certgen:v1.6.9

# MetalLB
for i in controller speaker; do
  docker pull quay.io/metallb/$i:v0.16.0
  docker tag  quay.io/metallb/$i:v0.16.0 $REGISTRY/metallb-$i:v0.16.0
  docker push $REGISTRY/metallb-$i:v0.16.0
done
```

## 3. MetalLB 설치

```bash
curl -sSL -o metallb-native.yaml \
  https://raw.githubusercontent.com/metallb/metallb/v0.16.0/config/manifests/metallb-native.yaml

# 이미지 참조를 로컬 레지스트리로 치환
sed -i 's#quay.io/metallb/controller:v0.16.0#$REGISTRY/metallb-controller:v0.16.0#g;
        s#quay.io/metallb/speaker:v0.16.0#$REGISTRY/metallb-speaker:v0.16.0#g' metallb-native.yaml

kubectl apply -f metallb-native.yaml
kubectl -n metallb-system rollout status deploy/controller --timeout=180s
kubectl -n metallb-system rollout status daemonset/speaker --timeout=180s
```

### 3.1 IP 풀 — ⚠️ 대역 선정이 가장 중요하다

MetalLB가 나눠줄 IP는 **노드와 같은 L2 세그먼트의 미사용 주소**여야 한다. 이미 쓰는 주소를 넣으면
IP 충돌로 해당 장비와 서비스가 동시에 죽는다. 적용 전 반드시 두 가지를 확인한다.

```bash
SEG=10.10.10        # 노드가 속한 /24 세그먼트로 치환

# (1) 실제 사용 중인 IP 스캔
for i in $(seq 1 254); do (ping -c1 -W1 $SEG.$i >/dev/null 2>&1 && echo "$SEG.$i 사용중") & done; wait

# (2) DHCP 임대 범위와 겹치지 않는지 — 겹치면 나중에 임대가 나가 충돌한다
grep -r dhcp-range /var/lib/libvirt/dnsmasq/*.conf 2>/dev/null || echo "DHCP range 없음(DNS 전용)"
```

두 결과로 **연속된 미사용 대역**을 골라 [`kubernetes/metallb-pool.yaml`](kubernetes/metallb-pool.yaml)의
`spec.addresses`에 넣는다. 이 매니페스트 상단 주석에 예시 환경의 조사 결과와 대역 선정 근거가 있다.

```bash
kubectl apply -f install/kubernetes/metallb-pool.yaml
kubectl -n metallb-system get ipaddresspool,l2advertisement
```

L2 모드를 쓴다(BGP 피어가 없는 단순 브리지 환경). L2는 노드 하나가 해당 IP의 ARP에 응답하는 방식이라
네트워크 장비 설정이 전혀 필요 없다.

## 4. ingress-nginx 설치

MetalLB가 있으므로 **`baremetal`(NodePort)이 아니라 `cloud`(LoadBalancer) 매니페스트**를 쓴다.

```bash
curl -sSL -o ingress-nginx.yaml \
  https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.15.1/deploy/static/provider/cloud/deploy.yaml

# digest 고정 참조를 로컬 태그로 치환
sed -i -E 's#registry\.k8s\.io/ingress-nginx/controller:v1\.15\.1@sha256:[a-f0-9]+#$REGISTRY/ingress-nginx-controller:v1.15.1#g;
           s#registry\.k8s\.io/ingress-nginx/kube-webhook-certgen:v1\.6\.9@sha256:[a-f0-9]+#$REGISTRY/ingress-nginx-certgen:v1.6.9#g' ingress-nginx.yaml

kubectl apply -f ingress-nginx.yaml
kubectl -n ingress-nginx rollout status deploy/ingress-nginx-controller --timeout=240s

# EXTERNAL-IP가 <pending>이 아니라 풀의 IP로 채워져야 성공이다
LB_IP=$(kubectl -n ingress-nginx get svc ingress-nginx-controller \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}'); echo "$LB_IP"
```

`<pending>`이면 MetalLB가 IP를 못 준 것이다 — §3.1의 풀이 적용됐는지, speaker 파드가 모든 노드에서
Running인지 확인한다.

## 5. 포탈 Ingress

**DNS 없이 IP로 접속**하는 환경이면 `host:` 없는 규칙을 쓴다 →
[`kubernetes/portal-ingress-testbed.yaml`](kubernetes/portal-ingress-testbed.yaml). 호스트명 + TLS로
서빙하는 운영 구성은 §7과 `src/portal/deploy/kubernetes/portal-ingress.example.yaml`.

```bash
kubectl apply -f install/kubernetes/portal-ingress-testbed.yaml
kubectl get ingress -n dms-portal      # ADDRESS 열에 $LB_IP
curl -s -o /dev/null -w "%{http_code}\n" "http://$LB_IP/"          # 200
```

> **`host:`를 넣으면 IP 접속이 404가 된다.** nginx는 `host:`가 있는 규칙을 그 Host 헤더에만 매칭하고,
> 브라우저가 IP로 접속하면 `Host: <IP>`라 어느 규칙에도 안 걸려 default backend(404)로
> 떨어진다. `host:` 없는 규칙은 catch-all(`server_name _;`)이 되어 어떤 Host든 받는다.
> 호스트명과 IP를 **동시에** 쓰려면 두 규칙을 나란히 두면 된다 —
> `src/portal/deploy/kubernetes/portal-ingress.example.yaml` 참고.

### ⚠️ `/healthz`는 ingress를 통과하지 못한다

ingress-nginx가 자기 nginx.conf에 `location /healthz { return 200; }`를 갖고 있어, **포탈의
`/healthz`보다 우선**한다. ingress로 접속하면 빈 body의 200이 오고 포탈 JSON은 보이지 않는다.

- k8s liveness/readiness 프로브는 **파드에 직접** 붙으므로 영향 없다.
- 포탈 healthz를 확인하려면 NodePort나 파드에 직접 접근한다:
  ```bash
  kubectl -n dms-portal exec deploy/dms-portal -- \
    python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8090/healthz').read().decode())"
  ```

## 6. 접근 경로 — L2 세그먼트 밖에서는 닿지 않는다

MetalLB L2 IP는 **그 IP가 속한 L2 세그먼트 안에서만** 도달 가능하다(NodePort와 같은 제약이며, 설정
문제가 아니라 네트워크 도달성 문제다). 노드망이 라우팅되지 않는 사설 브리지(예: 하이퍼바이저의 가상
브리지)라면 그 호스트에서는 `http://$LB_IP/`가 되지만 바깥 PC에서는 안 된다. 이때는 라우팅을 열거나,
임시로 브리지 호스트에서 포워딩한다:

```bash
# 브리지에 접근 가능한 호스트에서 실행. 파드 교체 시 자동 재연결.
while true; do
  kubectl -n ingress-nginx port-forward svc/ingress-nginx-controller 8898:80 --address 0.0.0.0 >/dev/null 2>&1
  sleep 2
done
# → 외부 PC에서 http://<호스트IP>:8898/
```

포탈 NodePort(`dms-portal` 30090)는 **남겨 두는 편이 좋다** — ingress 장애 시 우회 경로가 된다.

## 7. 운영 전환 시 달라지는 것

| 항목 | 간이(IP·평문) | 운영 |
|---|---|---|
| 접속 | IP + HTTP | **호스트명 + TLS** (Ingress에 `host:` + `tls:` 추가) |
| 인증서 | 없음 | 사내 CA 발급 또는 cert-manager |
| 포탈 쿠키 | `PORTAL_SESSION_HTTPS_ONLY=false` | **`true`** (TLS 서빙 후 반드시 전환) |
| ssl-redirect | `"false"` | `"true"` (HTTP→HTTPS 강제) |
| MetalLB 풀 | 미사용 대역 직접 조사(§3.1) | 운영 네트워크 담당자와 협의해 **예약된 대역** 할당 |
| DMS API | NodePort/내부 평면 | mTLS 종단 ingress (`kubernetes/ingress.example.yaml`) |

> 운영에서 MetalLB L2 모드는 **한 노드가 IP를 대표**하므로 그 노드 장애 시 수 초의 절체가 있다.
> 무중단이 필요하면 BGP 모드(라우터 피어링) 또는 외부 L4 로드밸런서를 쓴다.

## 8. 트러블슈팅

| 증상 | 원인 · 조치 |
|---|---|
| `EXTERNAL-IP`가 `<pending>` | MetalLB 미설치/풀 미적용. `kubectl -n metallb-system get ipaddresspool` |
| IP 접속이 404 | Ingress 규칙에 `host:`가 있다 (§5). 호스트 없는 규칙을 추가한다 |
| Ingress 생성이 webhook 오류로 거부 | `ingress-nginx-admission` Job이 실패했거나 인증서 미생성. `kubectl -n ingress-nginx get job,pod` |
| IP 충돌(다른 장비가 죽음) | 풀 대역이 사용 중이거나 DHCP와 겹친다 (§3.1). 즉시 풀을 좁히고 재적용 |
| `/healthz`가 빈 200 | ingress-nginx가 선점한다 (§5). 정상 동작이며 포탈 상태는 파드에서 직접 확인 |
| 이미지 `ImagePullBackOff` | 미러링 누락 또는 노드의 insecure-registry 설정 누락. `crictl pull $REGISTRY/...`로 노드에서 확인 |
