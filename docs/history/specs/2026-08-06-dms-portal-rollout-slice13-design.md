# 슬라이스 13 — 포탈 주도 롤아웃 설계

상위 스펙 §7의 **롤아웃 절반**을 구현한다. 빌드 절반(슬라이스 11)이 만든 이미지 태그를
운영자가 포탈에서 골라 클러스터에 적용하고, 그 진행과 결과를 본다.

지금은 새 태그를 쓰려면 매니페스트의 `image:` 를 손으로 고쳐 `kubectl apply` 해야 한다.

`releases` 테이블은 마이그레이션에 있으나 이를 읽고 쓰는 코드가 한 줄도 없다.

---

## 1. 실측으로 확인한 전제

| 사실 | 확인 방법 |
|---|---|
| 대상 워크로드: Deployment `dms-api`(컨테이너 `api`), `dms-controller`(`controller`), DaemonSet `dms-agent`(`agent`) | 매니페스트 |
| **컨테이너 이름이 워크로드 이름에서 유도되지 않는다** (`dms-controller` → `controller`) | 매니페스트 |
| 세 개의 **독립된 태그 계보**: `dms:d22`(api+controller), `dms-agent:dev5`, `dms-mpifileutils:job3`(ConfigMap `DMS_JOB_IMAGE`) | 매니페스트 + 레지스트리 |
| api 파드가 레지스트리 API 에 직접 도달 (`/v2/_catalog`, `/v2/<repo>/tags/list` → 200) | 실 클러스터 |
| `progressDeadlineSeconds=600` 이 두 Deployment 에 이미 설정 | 실 클러스터 |
| DaemonSet 에는 conditions 도 progressDeadlineSeconds 도 **없다** | k8s 계약 |
| 컨트롤러 Role 에 `apps` apiGroup **없음** | `kubectl auth can-i --list` |
| 모든 매니페스트가 `imagePullPolicy: IfNotPresent` | 매니페스트 |
| 리스는 per-loop, **갱신되지 않고 반납되지도 않는다**; 만료는 `max(interval*3, 30)초` | `controller.py`, `control.py` |
| holder 는 PID 기반이라 후임이 죽은 holder 의 리스를 **펜싱할 수 없다** | `cli.py`, `migrations.py` |

---

## 2. 핵심 결정 — 컨트롤러 자기 갱신을 **허용하되 2단계로**

컨트롤러가 자기 Deployment 를 패치하면 **롤아웃을 수행하던 파드 자신이 죽는다.**
세 선택지를 놓고 (c) 2단계 record-then-patch 를 (b) 순서 규칙과 **함께** 쓴다.

**(a) 컨트롤러 제외** — 상위 스펙 §7이 "이후 코드 업데이트 → 빌드 → 롤아웃은 전부
포탈에서"라고 명시한 것과 정면 충돌한다. planner/stepper 를 담은 바로 그 컴포넌트만
영원히 `kubectl` 을 요구하게 된다.

**(b) 순서만** — 필요하지만 불충분하다. 순서는 컨트롤러가 마지막에 죽는 것만 보장할 뿐,
**자기 자신의 기록**에 대해서는 아무 말도 하지 않는다.

**(c) 2단계 record-then-patch** — 기존 메커니즘에서 그대로 따라 나온다:

1. 한 트랜잭션 안에서 `releases` 행(`state='Applying'`)과 감사 항목을 쓰고 **커밋**한다.
2. 그 다음에 strategic-merge patch 를 호출한다.
3. **나중 틱**에 살아 있는 워크로드를 관찰해 `Applying` → `Applied` 로 넘긴다.
   "방금 patch 를 불렀다"는 사실은 프로세스 죽음을 넘지 못하므로 **절대 근거로 쓰지 않는다.**

적용 순서는 **`dms-agent` → `dms-api` → `dms-controller`** 로 고정한다. 순서를 **DB에
지속**시켜(컴포넌트별 행 + 시퀀스), 배치 중간에 죽은 컨트롤러가 이미 끝낸 패치를 다시
하지 않게 한다.

**멱등성 요구:** 같은 이미지로 재패치하면 새 ReplicaSet 이 생기지 않는다. 복구 경로는
"행은 `Applying` 인데 클러스터가 이미 목표 이미지를 돌리고 있다"를 **정상 케이스**로
취급해 `Applied` 로 수렴해야 한다 — 그것이 정확히 patch 직후 죽은 상태다.

**타이밍:** 롤아웃 루프 간격을 10–15초로 둔다. 그러면 리스가 30–45초라
자기유발 정지와 RollingUpdate 서지 중 중복 실행 위험이 함께 묶인다. 긴 간격을 쓰면
컨트롤러가 자기 롤아웃 추적을 몇 시간 동안 벽돌로 만든다.

---

## 3. 완료 판정 — 상태에서 유도한다, `kubectl rollout status` 를 쉘로 부르지 않는다

### Deployment

**반드시 `observedGeneration >= metadata.generation` 을 먼저 본다.** 그렇지 않으면 다른
모든 상태 필드가 패치 이전 값이라 **옛 ReplicaSet 기준으로 "완료"로 읽힌다** — 전형적인
거짓 성공이다.

그 다음:
- `updatedReplicas == spec.replicas`
- `replicas == updatedReplicas` (옛 파드가 사라졌다)
- `readyReplicas == updatedReplicas`

실패: `Progressing` 조건이 `status=False` + `reason=ProgressDeadlineExceeded`.
`progressDeadlineSeconds=600` 이 이미 설정돼 있어 **10분 상한을 공짜로 물려받는다** —
자체적으로 더 짧은 상한을 두지 않는다. `ReplicaFailure=True` 조건도 노출한다
(`/cephfs` hostPath `type: Directory` 가 없는 노드에서 나는 admission 오류가 여기 실린다).

### DaemonSet

같은 세대 게이트를 적용한 뒤:
- `updatedNumberScheduled == desiredNumberScheduled`
- `numberReady == desiredNumberScheduled`
- `numberUnavailable in (0, unset)`
- `numberMisscheduled == 0`

**DaemonSet 에는 conditions 도 progressDeadlineSeconds 도 없다.** 따라서 자체 벽시계
타임아웃을 둬야 하고, 멈춘 노드의 파드 사유를 보고해야 한다 — 영원히 기다리면 안 된다.

---

## 4. k8s 접근 — 새 좁은 Protocol

`K8sClient`(`create`/`get`/`delete`/`read_pod_log`)를 **확장하지 않는다.** 네 개의 기존
테스트 페어가 그것을 구조적으로 구현하고 있고, 그중 apps/v1 동사가 필요한 것은 하나도
없다. 확장하면 선언된 계약이 네 곳에서 거짓이 된다.

대신 새 모듈에 좁은 `WorkloadClient` Protocol 두 메서드를 선언한다:
`patch_workload(kind, name, namespace, body)`, `get_workload(kind, name, namespace)`.
같은 구체 클래스 `KubernetesClient` 가 구조적 타이핑으로 둘 다 만족한다 —
`BuildRunner` 가 러너 수준에서 분리하되 클라이언트는 공유한 것과 같은 방식이다.

`KubernetesClient` 에 `self._apps = None` 을 더하고 **기존 `_ensure()` 본문 안**에서
`AppsV1Api()` 를 만든다. **`_apps` 로 별도 가드를 만들면 안 된다** — 현재 가드가
`if self._core is None` 이라, 테스트가 `_apps` 만 주입하면 실 in-cluster config 를
로드하러 간다.

패치 본문은 **컨테이너 `name` 을 patchMergeKey 로 쓰는 strategic merge patch** 다:

```python
{"spec": {"template": {"spec": {"containers": [{"name": container, "image": image}]}}}}
```

JSON merge patch 는 `containers` 배열 전체를 교체해 env·volumeMounts 를 날린다.
`_content_type="application/strategic-merge-patch+json"` 을 **명시적으로** 넘긴다 —
클라이언트 내부 기본값에 의존하지 않는다.

**상태 읽기는 하나의 키 표기로 정규화한다.** `to_dict()` 는 snake_case 를, 원시 CRD dict 는
camelCase 를 준다. 클라이언트 안에서 작은 정수 dict 로 정규화하지 않으면 **페어는
통과하고 프로덕션은 `None` 을 읽어 "영원히 수렴 안 함"으로 보고한다.**

404 → `None`, 그 외 `ApiException` 은 재전파한다. 403 은 로그에서 구분 가능해야 한다
(RBAC 거부가 "GC 됐다"와 똑같이 렌더된 `read_pod_log` 사고의 교훈).

---

## 5. RBAC

**컨트롤러 Role** — 패치와 상태 읽기:

```yaml
  - apiGroups: ["apps"]
    resources: ["deployments", "daemonsets"]
    resourceNames: ["dms-api", "dms-controller", "dms-agent"]
    verbs: ["get", "patch"]
  - apiGroups: ["apps"]
    resources: ["deployments", "daemonsets"]
    verbs: ["list"]        # resourceNames 는 list 에 적용되지 않는다
  - apiGroups: ["apps"]
    resources: ["deployments/status", "daemonsets/status"]
    verbs: ["get"]
```

`resourceNames` 로 범위를 좁힌다 — 컨트롤러가 네임스페이스의 임의 워크로드를 패치할
이유가 없다. `pods/status` 를 별도 리소스로 부여한 이 파일의 기존 관례를 따라
`*/status` 도 함께 준다.

**api Role — 읽기 전용으로 같은 리소스를 준다:**

```yaml
  - apiGroups: ["apps"]
    resources: ["deployments", "daemonsets"]
    verbs: ["get", "list"]
```

§7의 `GET /api/admin/releases/targets` 가 **클러스터의 현재 이미지**를 보여주려면 api 가
워크로드를 읽어야 한다. 이 파일의 api Role 주석은 **뮤테이션**에 관한 것이다 —
`delete` 가 있는 이유(취소)와 `create` 가 없는 이유(제출을 안 함)를 적은 것이지 읽기를
막는 규칙이 아니다. Deployment 의 이미지 태그는 포탈이 어차피 화면에 띄우는 값이라
읽기 권한이 권한 상승이 아니다. **patch 는 주지 않는다** — 롤아웃은 컨트롤러가 한다.

대안으로 "api 가 이미 가진 `pods list` 로 파드 이미지에서 유도"를 검토했으나 기각한다:
롤링 업데이트 중에는 옛/새 파드가 섞여 있어 **현재 선언된 이미지를 정확히 알 수 없고**,
읽기 전용 grant 하나로 끝날 일에 별도 추상화를 만들게 된다. api 는 컨트롤러와 같은
`get_workload` 를 읽기 전용으로 쓴다 — 새 Protocol 을 만들지 않는다.

---

## 6. 데이터 모델

`releases` 컬럼: `id`, `component`, `image`, `tag`, `digest`, `state`, `actor`, `applied_at`.

**`reason_code` 가 없다.** 컨트롤러 루프의 예외는 상위에서 삼켜지므로 실패가 상태로
드러나야 하는데, 사유를 담을 곳이 없다 → `_ensure_columns` 로 `reason_code TEXT` 와
`seq INTEGER`(배치 순서)를 더한다.

상태 기계:

```
Pending --(patch 호출됨)--> Applying --> Applied
                                     --> Failed (reason_code)
```

"현재 릴리스"는 컴포넌트별 `MAX(id)` 로 유도한다 — `releases` 에 `component` 유니크
제약이 없고 인덱스가 `(component, id)` 다.

---

## 7. API

전부 admin 전용, 감사 로그를 쓴다(`mutation_class="release"`).

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/admin/releases` | 이력(최신순) + 컴포넌트별 현재 |
| GET | `/api/admin/releases/targets` | 롤아웃 대상 3종과 **클러스터의 현재 이미지**, 그리고 각 리포의 **레지스트리 태그 목록** |
| POST | `/api/admin/releases` | `{items: [{component, tag}, ...]}` → `202` — 순서를 서버가 강제한다 |

거부: `unknown_component`, `unknown_tag`(레지스트리에 없음), `same_tag`(현재와 동일 —
`IfNotPresent` 라 아무 일도 안 일어난다), `rollout_in_progress`(동시 롤아웃 1개), 유지보수 모드.

**레지스트리 조회는 실패할 수 있다** — 레지스트리가 죽었다고 롤아웃 화면 전체가 죽으면
안 된다. 태그 목록은 실패 시 빈 목록과 경고를 주고, 제출 시 `unknown_tag` 검증은
레지스트리가 응답할 때만 강제한다(응답 불가면 통과시키고 patch 후 ImagePullBackOff 로
드러나게 한다 — 잘못된 차단보다 낫다).

## 8. 포탈 화면 — 「릴리스」

`features/releases/`. 기존 admin 화면 규약(`RequireRole role="admin"` + `{isAdmin && 링크}`),
h1 은 정확히 **릴리스**.

- 컴포넌트 3행: 이름 / 현재 이미지·태그 / 새 태그 선택(select) / 상태
- 제출 버튼 하나로 선택한 것들을 **한 배치**로 보낸다(서버가 순서 강제)
- 진행 중이면 배지와 함께 폴링, 종단이면 폴링 정지
- 이력 표: 시각 / 컴포넌트 / 태그 / 상태 / 사유 / actor
- 사유 코드는 `reasonText()` 경유(슬라이스 12), 새 코드는 `REASON_MESSAGES` 와
  백엔드 커버리지 목록 **양쪽**에 넣는다

**경고를 반드시 보여준다:** 컨트롤러를 갱신하면 컨트롤러가 재시작되어 롤아웃 추적이
잠시 끊긴다는 것. 운영자가 화면이 멈춘 것을 장애로 오해하면 안 된다.

## 9. 매니페스트 동기화

정적 YAML 을 선언적 진실로 유지한다 — 롤아웃 성공 후 `40-api.yaml`/`41-controller.yaml`/
`30-migrate-job.yaml`/`50-agent-daemonset.yaml` 의 태그를 **손으로** 맞춰야 한다는 것을
문서화한다. 이 슬라이스는 파일을 자동으로 고치지 않는다(컨트롤러 파드에 저장소가 없다).
포탈이 "매니페스트와 클러스터가 어긋났다"를 보여주는 것은 슬라이스 14 대시보드의 몫이다.

Helm/kustomize 를 도입하지 않는다 — `deploy/README.md` 에 기록된 설계 결정이다.

---

## 10. 이 슬라이스에서 하지 않는 것

- `DMS_JOB_IMAGE`(ConfigMap) 변경 — 이미지 패치가 아니라 ConfigMap 갱신 + 소비자 재시작이다.
- 롤백 버튼 — 이력에서 옛 태그를 다시 고르면 되므로 별도 기능이 필요 없다.
- 매니페스트 파일 자동 수정.
- 모니터링 대시보드(§9) → 슬라이스 14.

---

## 11. 실증 (테스트베드)

1. `GET /api/admin/releases/targets` 가 세 컴포넌트의 **현재 이미지**와 레지스트리 태그
   목록을 주는지.
2. 현재와 같은 태그 제출이 `same_tag` 로 거절되는지 (`IfNotPresent` 함정).
3. 레지스트리에 없는 태그가 `unknown_tag` 로 거절되는지.
4. **`dms-agent` 를 `dev5` → `d22` 로 롤아웃** — DaemonSet 세대 게이트와 수렴 판정이
   실제로 동작하는지, `Applied` 로 넘어가는지.
5. **`dms-api` 롤아웃** — Deployment 조건 기반 판정.
6. **`dms-controller` 자기 갱신** — 컨트롤러가 죽은 뒤 **새 파드가 `Applying` 행을
   이어받아 `Applied` 로 수렴**시키는지. 이것이 이 슬라이스의 핵심 실증이다.
7. 감사 로그에 `mutation_class=release` 가 남는지.
8. 존재하지 않는 태그로 강제 패치 시 `ImagePullBackOff` 가 나고, 롤아웃이 타임아웃으로
   `Failed` 가 되는지(자체 상한 또는 `ProgressDeadlineExceeded`).
