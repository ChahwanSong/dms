# DMS Portal 대시보드 — CSI control host 패널 (서브프로젝트 A) 설계

- 날짜: 2026-06-25
- 상태: **폐기(OBSOLETE) — RM 제거로 전제가 사라짐.** 작성 시점 기록으로만 보존한다.
  이 패널의 데이터 원천인 **ResourceQuota mutation-transport 프로브**
  (`sanity_result.mutation_observed`, `readiness.kubernetes_mutation`)가 RM과 함께 제거되었고,
  구현물이던 `ControlHostsTable` 패널도 삭제되었다. 현재 storage-mapping sanity 축은
  `data_management`와 `inventory` 둘뿐이다 → [`../../api/storage-mappings.md`](../../api/storage-mappings.md).
  CSI 매핑(`ceph-csi`/`gpfs-csi`/`weka-csi`) 자체는 DM의 PVC↔PVC sync 대상으로 계속 존재한다.
- 원래 상태(당시): 승인됨 (구현 대기)
- 범위: 종합 대시보드에 **k8s-CSI 스토리지 매핑의 control host + ResourceQuota mutation transport 상태** 패널 추가
- DMS 변경: **없음** (기존 `GET /api/v1/operations/operations/storage-mappings`의 `sanity_result.mutation_observed` 재사용)
- 더 큰 후속: B(worker node OS 메트릭), C(Volcano 상태)는 별도 스펙

## 1. 배경 / 목표

대시보드의 "워커 노드" 패널은 DMS control cluster의 agent 노드(RM/DM)만 보여준다. k8s-CSI namespace-quota 매핑은 agent가 없고, 대신 RM worker가 **control host로 (ssh-)kubectl** 해서 ResourceQuota를 적용한다. 이 **control host와 그 도달성/권한**을 대시보드에서 보고 싶다.

이 데이터는 이미 DMS sanity에 있다(라이브 확인):
```
ceph-cephfs-ddz26 (ddz26, ceph-csi): mutation_observed = {
  mode: "ssh-kubectl", control_host: "10.10.10.20", reachable: true,
  can_mutate: true, permissions: {create:true, patch:true, delete:true},
  detail: "ResourceQuota mutation transport reachable" }
ceph-cephfs-dms-k8s (dms, ceph-csi): mode: "kubectl" (local), control_host: null, ...
```
`GET /operations/storage-mappings`가 매핑마다 `sanity_result.mutation_observed`를 그대로 반환한다(weka 비밀번호만 redact). 따라서 **읽기 전용·DMS 무변경**으로 구현 가능.

## 2. 아키텍처 / 데이터 소스

```
[Dashboard SPA: ControlHostsTable]
   │ GET /api/operator/dashboard/control-hosts
   ▼
[BFF dashboard.py] → dms.list_storage_mappings(actor)
   │ CSI 매핑만 필터 + mutation_observed 정형화
   ▼  (DMS GET /operations/storage-mappings — 기존)
```

- **CSI 판별**: `backend_template.backend_type` ∉ {`cephfs`,`gpfs`,`wekafs`} (fs 집합이 아니면 CSI/free-form). 포탈 프론트 `helpers.isFsBackend`와 동일 기준을 BFF에서 적용.
- `dms_client.list_storage_mappings(*, actor, cluster_name=None)`는 이미 존재(스토리지 인벤토리가 사용) — 재사용, 신규 dms_client 메서드 불필요.

## 3. BFF (`src/portal/backend/routers/dashboard.py`)

신규 라우트 `GET /api/operator/dashboard/control-hosts`, 운영자 role 게이트(라우터 레벨 이미 적용).

```python
_FS_BACKENDS = {"cephfs", "gpfs", "wekafs"}

def _control_hosts(mappings: list[dict]) -> list[dict]:
    rows = []
    for m in mappings or []:
        bt = (m.get("backend_template") or {}).get("backend_type") or ""
        if bt in _FS_BACKENDS:
            continue  # fs 매핑은 agent 노드(워커 노드 패널) 소관
        mo = (m.get("sanity_result") or {}).get("mutation_observed") or {}
        rows.append({
            "storage_name": m.get("storage_name"),
            "cluster_name": m.get("cluster_name"),
            "backend_type": bt,
            "sanity_status": m.get("sanity_status"),
            "mode": mo.get("mode"),
            "control_host": mo.get("control_host"),
            "reachable": mo.get("reachable"),
            "can_mutate": mo.get("can_mutate"),
            "permissions": mo.get("permissions") or {},
            "detail": mo.get("detail"),
        })
    rows.sort(key=lambda r: (r.get("cluster_name") or "", r.get("storage_name") or ""))
    return rows

@router.get("/control-hosts")
async def control_hosts(dms=..., user=...) -> list[dict]:
    mappings = await dms.list_storage_mappings(actor=_actor(user, settings))
    return _control_hosts(mappings)
```
- 부분 실패 처리: 이 라우트는 단일 DMS 호출이라 `DmsApiError`가 그대로 HTTPException으로 전파되면 충분(다른 드릴다운 라우트와 동일 패턴; `/summary` fan-in 대상 아님).

## 4. 프론트엔드

- `api.ts`: 타입 `ControlHost` + `operatorApi.dashboard.controlHosts()` 추가.
  ```ts
  export interface ControlHost {
    storage_name: string; cluster_name: string | null; backend_type: string;
    sanity_status?: string; mode?: string | null; control_host?: string | null;
    reachable?: boolean; can_mutate?: boolean;
    permissions?: { create?: boolean|null; patch?: boolean|null; delete?: boolean|null };
    detail?: string | null;
  }
  // dashboard: { ..., controlHosts: () => request<ControlHost[]>("/api/operator/dashboard/control-hosts") }
  ```
- 신규 컴포넌트 `dashboard/ControlHostsTable.tsx` — 기존 `Section`(접기형, 기본 접힘) + `table.grid` 패턴 재사용. 자체 로드(`.catch(()=>[])`), 행수 badge.
  - 컬럼: 스토리지 · 클러스터 · backend · mode · control host · 도달 · 변경권한(can-i) · 비고
  - `control_host` 없으면(local kubectl) "— (local)". `reachable`/`can_mutate`는 `san-ready`/`san-failed` 뱃지. 권한은 `c/p/d ✓✗`.
- `Dashboard.tsx`: 드릴다운에 `<ControlHostsTable/>` 추가(워커 노드 다음 위치 권장). 상단 카드 4개는 그대로.

## 5. 범위

- **모든 CSI 매핑의 mutation transport**를 보여준다(ssh-kubectl의 control_host + kubectl-local 포함) — 전체 그림 + control host prominent. (ssh-kubectl만 보고 싶으면 프론트에서 필터 토글 추가 가능하나 v_A 비범위.)
- 읽기 전용. 액션 없음.

## 6. 테스트 / 검증

- BFF: `_control_hosts` 필터/정형화 단위 확인(파이썬, fs 매핑 제외·CSI 포함·mutation_observed 추출) + create_app import 스모크(라우트 `/control-hosts` 등록).
- 프론트: `npm run build`(tsc).
- 라이브: 대시보드 "CSI control host" 섹션 펼침 → ceph-cephfs-ddz26 (ssh-kubectl @ 10.10.10.20, reachable, can-i ✓), ceph-cephfs-dms-k8s (kubectl local) 표시. 콘솔 에러 0.

## 7. 비범위 / 후속

- B: worker node OS 메트릭(agent + DaemonSet). C: Volcano 상태(신규 DMS 엔드포인트). 각 별도 스펙.
- control host로의 ssh 핑/지연 같은 능동 probe는 비범위(DMS sanity의 reachable/can_mutate를 그대로 표시).
