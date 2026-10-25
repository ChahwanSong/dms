// 대상 클러스터에 만들 **읽기 전용** 리더 계정(설계 §7 클러스터 Dialog, §8 테스트베드 리더와 같은 모양). DMS 는 이 SA 의 장기
// 토큰으로 rolebindings·pvc·pv·storageclasses 를 **list** 하고 kube-system namespace 를 get(같은 클러스터 중복 등록 감지용
// fingerprint)할 뿐이다 — 쓰기 권한은 없다. 필수는 rolebindings + persistentvolumeclaims(권한·PV 이름의 원천, 설계 D7)이고,
// persistentvolumes·storageclasses 가 빠지면 그 목록만 「모름」이 된다(경로의 {subdir}·{attr:}·{handle:} 해석이 안 됨).
// 토큰 Secret 은 SA.secrets 에 넣지 않는다(kubernetes.io/service-account-token + annotation 으로 컨트롤러가 채운다).

export const READER_NAMESPACE = "dms-reader";
export const READER_SA = "dms-inventory";
export const READER_SECRET = "dms-inventory-token";

export const READER_YAML = `apiVersion: v1
kind: Namespace
metadata:
  name: ${READER_NAMESPACE}
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: ${READER_SA}
  namespace: ${READER_NAMESPACE}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: dms-inventory-reader
rules:
  - apiGroups: ["rbac.authorization.k8s.io"]
    resources: ["rolebindings"]
    verbs: ["list"]
  - apiGroups: [""]
    resources: ["persistentvolumeclaims", "persistentvolumes"]
    verbs: ["list"]
  - apiGroups: ["storage.k8s.io"]
    resources: ["storageclasses"]
    verbs: ["list"]
  - apiGroups: [""]
    resources: ["namespaces"]
    resourceNames: ["kube-system"]
    verbs: ["get"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: dms-inventory-reader
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: dms-inventory-reader
subjects:
  - kind: ServiceAccount
    name: ${READER_SA}
    namespace: ${READER_NAMESPACE}
---
apiVersion: v1
kind: Secret
metadata:
  name: ${READER_SECRET}
  namespace: ${READER_NAMESPACE}
  annotations:
    kubernetes.io/service-account.name: ${READER_SA}
type: kubernetes.io/service-account-token
`;

/** 토큰·CA 꺼내기(대상 클러스터에서). 토큰은 base64 로 감싼 값(data.token)을 그대로 붙여넣어도 서버가 풀어 저장한다. */
export const READER_TOKEN_CMD =
  `kubectl -n ${READER_NAMESPACE} get secret ${READER_SECRET} -o jsonpath='{.data.token}' | base64 -d`;
export const READER_CA_CMD =
  `kubectl -n ${READER_NAMESPACE} get secret ${READER_SECRET} -o jsonpath='{.data.ca\\.crt}' | base64 -d`;
export const READER_CHECK_CMD =
  `kubectl auth can-i list rolebindings -A --as=system:serviceaccount:${READER_NAMESPACE}:${READER_SA}`;
