# 포탈 스토리지 등록/디테일 — 파일시스템 정리 설계

작성일: 2026-07-23 · 범위: **포탈 프론트 + BFF만** (DMS 백엔드 `src/dms/` 불변, CLAUDE.md 준수)

## 배경 / 문제

운영자 포탈의 스토리지 매핑 UI(`interfaces/operator/storage/`)에서 **파일시스템(cephfs/gpfs/wekafs)**
등록·조회 시 k8s/CSI 전용 개념이 섞여 나온다.

1. **cluster_name 오표현.** 파일시스템 등록에도 `cluster_name`이 필수로 강제되고 "클러스터"로만
   표기돼 k8s 클러스터처럼 보인다. 실제로 DMS는 이를 **RM/DM 에이전트-클러스터 상관키**로 쓴다:
   `inventory.py:_role_readiness`가 `if not cluster_name: return "Unknown"` 후
   `worker_roles[RM][cluster_name]`로 에이전트 증거를 조회한다. 즉 **필요하지만 k8s 개념이 아니고**,
   skeleton 기본값이 실제 에이전트 클러스터와 다른 `"cluster-a"`라 **오설정 footgun**이다(예: 테스트베드
   실제 클러스터명은 `dms` → 그대로 쓰면 RM readiness Missing).
2. **"Kubernetes 관측"이 파일시스템에도 표시(버그).** DMS sanity는 `kubernetes_observed`를 **항상**
   채우고(`inventory.py:325`), 포탈 디테일(`StorageMappingDetail.tsx:129`)이 `{k8s && …}`로 무조건
   렌더한다 → 순수 파일시스템인데 "Kubernetes 관측"(클러스터 + 빈 SC/provisioner)이 뜬다.
3. **무관 항목 잔존.** 파일시스템은 `storage_class_name`이 항상 `—`인데 디테일/리스트에 열/행이 남는다.

## 목표 / 비목표

- 목표: 파일시스템 등록·조회에서 k8s/CSI 전용 요소를 정리하고, `cluster_name`을 올바른 의미로
  재명명·자동 기본값화한다. UI/layout을 다듬는다.
- 비목표: DMS 백엔드/계약 변경. `cluster_name`을 fs에서 완전 제거(멀티클러스터 fs가 이를 실제로 씀).

## 설계

### A. cluster_name → "에이전트 클러스터" + 자동 기본값
- **BFF** (`src/portal/backend/`):
  - `dms_client.py`: `get_inventory(actor)` 추가 → `GET /operations/inventory`.
  - `routers/operator.py`: `GET /api/operator/control-cluster` → `{ "control_cluster_name": … }`만 반환.
    inventory는 정적 config 값이라 **app.state에 가벼운 캐시**(TTL 또는 최초 1회) — 폼 열 때마다 전체
    inventory를 재조회하지 않는다.
- **frontend** (`src/portal/frontend/`):
  - `api.ts`: `operatorApi.controlClusterName()` 추가.
  - `StorageInventory.tsx`: 마운트 시 1회 fetch → `StorageMappingForm`에 `controlCluster` prop 전달.
  - `StorageMappingForm.tsx` + `helpers.ts`: 신규 fs 매핑 생성 시 skeleton의 `cluster_name` 기본값을
    하드코딩 `"cluster-a"` 대신 **실제 control cluster**로 채운다(prop이 없으면 기존 값 유지).
    `FIELD_DOCS`의 fs `cluster_name` 라벨/설명을 **"에이전트 클러스터 — RM/DM 에이전트가 보고하는
    클러스터, 보통 기본 클러스터"**로 재명명. 폼 힌트도 정합화.
- 폼 표현: 기존 **단일 JSON 템플릿** 방식 유지(전용 입력칸 추가 안 함) — `cluster_name`은 JSON에 남되
  기본값·설명만 개선.

### B. "Kubernetes 관측" 버그 (이슈 #2)
- `StorageMappingDetail.tsx`: `{k8s && …}` → `{!isFs && k8s && …}`. 파일시스템에선 "Kubernetes 관측"
  카드를 렌더하지 않는다. (외곽 조건 `(k8s || (isFs && agent) || (!isFs && mutation))`도 `!isFs && k8s`로
  맞춰 fs일 때 빈 카드 그룹이 남지 않게 한다.)

### C. 파일시스템 무관 항목 정리
- 디테일 `overviewItems`: fs일 때 **"storage class" 행 숨김**(항상 `—`), **"클러스터" 라벨 →
  "에이전트 클러스터"**(fs 한정; csi는 "클러스터" 유지).
- 리스트 `StorageInventory`: `storage class` 열은 혼합 목록(csi 행이 씀)이라 **유지**, fs 행은 muted `—`
  그대로. 열 자체는 제거하지 않는다.

### D. UI/layout 폴리시
- 관측 카드 그룹(`obs-cards`)이 fs=Agent 관측만 / csi=Quota transport만 나오도록 B·C로 정합화(빈 카드
  그룹 방지).
- 디테일 섹션 간격·그룹 헤더를 운영자 대시보드 카드 간격 톤과 통일(`styles.css` 소폭).

## 영향 파일
- BFF: `dms_client.py`, `routers/operator.py`
- 프론트: `api.ts`, `interfaces/operator/storage/{StorageInventory,StorageMappingForm,StorageMappingDetail}.tsx`,
  `interfaces/operator/storage/helpers.ts`, (필요 시) `styles.css`

## 검증
- 프론트: `npm run build`(타입/빌드) 통과.
- 파일시스템 매핑: 디테일에 "Kubernetes 관측"·"storage class" 안 뜨고, cluster_name 기본값이 실제
  control cluster로 채워지며 라벨이 "에이전트 클러스터"인지 — 배포 후 Playwright로 확인.
- CSI 매핑: "Kubernetes 관측"·Quota transport·storage class가 정상 유지되는지(회귀 없음) 확인.
- 기존 매핑 편집 라운드트립(cluster_name 유지) 회귀 없음.
