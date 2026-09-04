# ingress-nginx — SSC 웹 노드 hostNetwork 엣지 설치

DMS 는 ingress-nginx 컨트롤러를 트리에 두지 않는다(업스트림 애드온, 관례상
out-of-band 설치·패치). SSC 엣지 모드에서는 컨트롤러를 **웹 노드에 hostNetwork 로**
올려 그 노드의 public IP 의 **방화벽이 연 포트**(values-hostnetwork.yaml 의
containerPort.https, 예 30080; 표준이면 443)를 직접 리슨하게 한다(MetalLB 불필요).
그 포트는 overlays/ssc 의 values.env `PORTAL_PORT` 와 같아야 install.sh 게이트를 통과한다.

이 디렉터리:
- `values-hostnetwork.yaml` — helm chart(ingress-nginx) 값. helm 설치용.
- `controller-hostnetwork-patch.yaml` — 정적 매니페스트로 설치한 경우의 컨트롤러 패치.

## 방법 A — helm (권장)

```sh
kubectl label node <WEB_NODE> dms.io/web-node=true
helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
  -n ingress-nginx --create-namespace \
  -f deploy/addons/ingress-nginx/values-hostnetwork.yaml
# airgap: 이미지가 사내 레지스트리에 있어야 한다
#   --set controller.image.registry=<레지스트리> --set controller.image.image=<경로> ...
#   (admissionWebhooks.patch.image 도 함께 미러)
```

## 방법 B — 정적 매니페스트 + 패치

```sh
kubectl label node <WEB_NODE> dms.io/web-node=true
# (이미 설치돼 있다는 전제 — 없으면 업스트림 deploy.yaml 을 사내 이미지로 먼저 apply)
kubectl -n ingress-nginx patch deploy ingress-nginx-controller \
  --type merge --patch-file deploy/addons/ingress-nginx/controller-hostnetwork-patch.yaml
kubectl -n ingress-nginx patch svc ingress-nginx-controller -p '{"spec":{"type":"ClusterIP"}}'
# 컨트롤러 args 에 --default-ssl-certificate=dms/dms-portal-tls 가 없으면 추가(IP 직접 https).
```

## 확인

```sh
kubectl -n ingress-nginx get pod -l app.kubernetes.io/component=controller -o wide
#  → NODE 가 <WEB_NODE>, 그리고:
kubectl -n ingress-nginx get pod -l app.kubernetes.io/component=controller \
  -o jsonpath='{.items[0].spec.hostNetwork}{"\n"}'   # → true
```

이후 `deploy/overlays/ssc/install.sh` 가 이 상태(hostNetwork·웹 노드 고정·
default-ssl-certificate)를 apply 전에 게이트로 재확인한다.

## 왜 이렇게 (요점)

- **hostNetwork**: Service LoadBalancer/MetalLB 없이 호스트 포트(containerPort.https,
  예 30080)를 직접 노출. public IP 가 노드 NIC 에 있으니 커널이 ARP 를 담당 — 광고
  장치가 불필요. containerPort 가 곧 호스트 리슨 포트라 NodePort 대역 제약도 없다.
- **dnsPolicy: ClusterFirstWithHostNet** (최다 함정): 이게 없으면 hostNetwork 파드가
  호스트 DNS 를 써 `dms-api.dms.svc` 클러스터 서비스명을 해석하지 못한다.
- **nodeSelector dms.io/web-node=true + replicas 1**: public IP 가 있는 그 노드에만.
- **--default-ssl-certificate=dms/dms-portal-tls**: SNI 없는 IP 직접 https 접속도
  정식 인증서를 받게 한다(SAN 에 public IP 포함 필수).
