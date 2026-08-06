# 슬라이스 11 — 포탈 주도 이미지 빌드 설계

상위 스펙 §7(이미지 빌드·배포)의 **빌드 절반**을 구현한다. 롤아웃(컴포넌트별 배포 태그
선택 → Deployment/DaemonSet 이미지 교체)은 슬라이스 12로 미룬다.

`builds` 테이블은 마이그레이션에 이미 있으나 이를 읽고 쓰는 코드는 한 줄도 없다. 즉 이
슬라이스는 back-compat 제약이 없고, 대신 고정된 컬럼 목록을 그대로 써야 한다.

---

## 1. 실측으로 확인한 전제

설계 전에 테스트베드에서 직접 확인했다. 이 사실들이 아래 결정의 근거다.

| 사실 | 확인 방법 |
|---|---|
| pkg-01(10.10.10.30)은 **K8s 노드가 아니다** (클러스터 = dms-cp1, dms-w1~w5) | `kubectl get nodes -o wide` |
| dms-w1에 인터넷 egress가 있다 | 파드에서 `https://github.com` → 200 |
| 레지스트리 `pkg-01:5000`은 **평문 HTTP·무인증** | `http://pkg-01:5000/v2/` → 200 |
| `quay.io/buildah/stable`을 노드가 pull할 수 있고 buildah 1.43.1 + git 2.54.0을 담고 있다 | dms-w1에서 실행 |
| 네임스페이스 `dms`는 PSA `enforce=privileged` | namespace 라벨 |
| 리포는 익명 clone 가능 | `git-upload-pack` → 200 |
| 컨트롤러 SA에 **pods create/get/list/watch/delete** 있음, `batch`·`apps` 권한은 **없음** | `kubectl auth can-i --list` |

**운영 제약(문서화 대상):** 빌드는 지정한 repo/ref를 clone하므로, 포탈 빌드는 GitHub에
push된 커밋만 빌드할 수 있다. 현재 `origin/main`은 로컬 main보다 뒤에 있다. 이 슬라이스는
이 사실을 바꾸지 않는다 — 빌드 화면에 clone 대상 ref와 해석된 commit SHA를 항상 노출해서
운영자가 "무엇이 빌드됐는지"를 오해하지 않게 한다.

---

## 2. 핵심 결정 — 빌드는 **bare Pod**로 돈다 (batch/v1 Job 아님)

상위 스펙은 "빌드는 그 노드에 고정된 K8s Job"이라고 적었다. 구현 표면을 조사한 결과
**bare Pod가 명백히 우월**해서 여기서 벗어난다. 근거는 전부 기존 코드에 있다:

| 항목 | bare Pod | batch/v1 Job |
|---|---|---|
| K8s 클라이언트 | `kind == "Pod"` 분기가 이미 있다 (`execution_volcano.py`) | `BatchV1Api` 분기 신설 필요. 게다가 `_KIND["vcjob"]`가 이미 `"Job"`이라 kind 문자열이 충돌한다 |
| 로그 | `read_log`가 bare Pod ref만 지원한다 — 그대로 동작 | Job → 파드 이름 해석에 label selector가 필요한데 `K8sClient`에 없다 (Volcano 잡 로그가 `log_not_available`로 거부되는 바로 그 이유) |
| 상태 폴링 | `_POD_PHASE` 매핑이 이미 있다 | 세 번째 매핑(`succeeded`/`failed` 카운트 + conditions) 신설 |
| 노드 고정 | preflight 파드가 쓰는 `nodeName` 그대로 | 동일하나 파드 템플릿 한 겹 더 |
| RBAC | 컨트롤러가 **이미** pods create/delete 보유 → **변경 0** | `apiGroups:["batch"]` 룰 신설 필요 |
| 정리 | `PodGarbageCollector`가 `pod/` 접두 ref를 이미 처리 (슬라이스 10) | Job TTL은 되지만 GC 경로가 갈린다 |

Job이 주는 것(재시도·backoff)은 이 시스템이 **의도적으로 거부한 것**이다 —
`config.py`에 "재시도 설정은 두지 않는다"가 명시돼 있고, 실패한 빌드를 자동 재실행하는
것은 운영자가 로그를 보고 판단할 일이다. 재빌드는 포탈에서 다시 누르면 된다.

따라서: ref 접두는 **`buildpod/`**. `_KIND`에 `"buildpod": "Pod"`를 추가하고,
`PodGarbageCollector`가 이 접두도 수거하도록 넓힌다.

---

## 3. 빌드 노드 지정

`control_state`는 `CHECK (id = 1)` 싱글턴이고 UPDATE로만 쓴다. 여기에 컬럼 하나를 더한다:

```
control_state.build_node_name TEXT   -- NULL이면 빌드 기능 비활성
```

`CREATE TABLE` 텍스트와 `_ensure_columns` **양쪽**에 넣는다 —
`CREATE TABLE IF NOT EXISTS`는 이미 있는 테이블을 조용히 건너뛴다.

- 지정 UI는 기존 운영 제어 화면(`features/control/`)에 필드 하나를 추가한다.
- 값은 `agent_nodes`에 보고된 노드 이름 중에서 고른다 (자유 입력 금지 — 오타가
  `nodeName`으로 새면 파드가 영원히 Pending이다).
- 미지정 상태에서 빌드를 제출하면 `422 build_node_not_set`.

---

## 4. 빌드 실행

### 4.1 무엇을 빌드하는가

이미지 3종은 **의존 순서**가 있다: `dms-mpifileutils` → `dms` → `dms-agent`
(agent가 앞의 둘을 `FROM` 한다). 하나의 빌드 파드가 셋을 순서대로 처리한다.

운영자는 `images` 로 부분집합을 고른다. 기본값은 `["dms"]`:
`dms-mpifileutils`는 mpifileutils를 소스에서 `make -j2`로 컴파일해 매우 오래 걸리고,
실제로 자주 바뀌는 것은 `dms`뿐이다. `dms-agent`를 고르면 그것이 `FROM`하는 태그가
레지스트리에 있어야 하므로, 같은 빌드에 `dms-mpifileutils`·`dms`가 함께 선택됐거나
이미 그 태그가 존재해야 한다 — 아니면 빌드가 실패하고 로그에 그대로 남는다.

### 4.2 태그

`imagePullPolicy: IfNotPresent`가 모든 매니페스트에 걸려 있다. **같은 태그를 다시
push하면 클러스터가 절대 집어오지 않는다.** 따라서 빌드마다 반드시 새 태그를 만든다:

```
b<build_id[:8]>          예: b3f9a1c2
```

`git_ref`나 commit SHA를 태그로 쓰지 않는다 — 같은 커밋을 두 번 빌드하는 것은 정상적인
운영 행위(캐시 무효화, base 이미지 갱신)인데 그때 태그가 겹치면 위 함정에 빠진다.
해석된 commit SHA는 `builds.commit_sha`에 따로 기록한다.

### 4.3 빌드 파드

`quay.io/buildah/stable` 단일 이미지, `privileged: true`, `nodeName: <지정 노드>`.

파드 명세는 **순수 함수** `build_build_pod(...)`로 만든다 (기존
`execution_manifests.py`의 규약: 키워드 전용 인자, k8s 클라이언트 접근 없음).

이름: `dms-build-<build_id[:12]>`, DNS-1123 (밑줄 없음), 63자 절단.
라벨: `dms.io/build-id=<build_id>`, `dms.io/phase=build`.

컨테이너 스크립트는 **값을 f-string으로 박아 넣지 않는다** — 전부 `DMS_BUILD_*` 환경
변수로 넘기고 셸에서 참조한다 (기존 매니페스트 빌더의 규약이자 주입 방어).

```sh
set -eu
git clone --depth 1 --branch "$DMS_BUILD_REF" "$DMS_BUILD_REPO" /src
cd /src
git rev-parse HEAD                      # commit_sha 로 회수
for img in $DMS_BUILD_IMAGES; do        # 공백 구분, 의존 순서대로 정렬돼서 온다
  buildah bud --isolation chroot -f "deploy/docker/Dockerfile.$img" \
    -t "$DMS_BUILD_REGISTRY/$img_full:$DMS_BUILD_TAG" .
  buildah push --tls-verify=false "$DMS_BUILD_REGISTRY/$img_full:$DMS_BUILD_TAG"
done
```

레지스트리가 평문 HTTP이므로 push는 `--tls-verify=false`, 그리고 `dms-agent`가
`FROM pkg-01:5000/...`을 하므로 **pull도** insecure로 설정해야 한다
(`/etc/containers/registries.conf.d/`에 insecure 항목을 파드 안에서 써 넣는다).

commit SHA는 로그에서 파싱하지 않는다 — 파싱은 깨지기 쉽다. 대신 스크립트가
`DMS_COMMIT_SHA=<sha>` 한 줄을 stdout에 찍고, 감시 루프가 그 마커 라인만 찾는다.

### 4.4 빌드는 hermetic하지 않다

egress가 필요한 곳: npmjs, dl.k8s.io, github.com, PyPI, Debian bookworm 미러.
따라서 지정 빌드 노드는 인터넷이 되는 노드여야 한다. 노드가 egress를 잃으면 빌드는
실패하고 그 사유가 로그에 남는다 — 이 슬라이스는 egress를 사전 점검하지 않는다
(사전 점검은 또 하나의 실패 지점일 뿐이고, 실제 실패가 더 정확한 신호다).

Dockerfile들이 base 이미지(`python:3.11-slim-bookworm`)와 `MPIFILEUTILS_REF`,
`KUBECTL_VERSION`을 핀해 두고 있고 그 핀은 **load-bearing**이다(trixie의
libopenmpi40 ABI 불일치, 클러스터 버전 일치). 포탈은 이 값들을 **노출하지 않는다** —
빌드 인자로 열어 주면 테스트베드와 안 맞는 이미지를 만들 길이 열린다.

---

## 5. 상태 추적 — `BuildWatcher` 루프

컨트롤러 루프 규약을 따른다: 인자 없는 콜러블, 멱등한 `run_once()`,
`build_loops()`에 등록, per-loop DB 리스로 직렬화, 어느 지점에서 죽어도 복구 가능.

상태 기계 (`builds.state`):

```
Pending  --(파드 생성됨)-->  Running  --> Succeeded
                                       --> Failed      (reason_code)
```

`run_once()`:
1. `Pending` 빌드를 집어 파드를 생성하고 `Running` + `pod_ref` 기록.
2. `Running` 빌드마다 `poll(ref)` 한 번. 종단이면 로그를 읽어
   `DMS_COMMIT_SHA=` 마커에서 `commit_sha`, 태그 목록에서 `images`를 확정하고
   `finished_at`·`state`·`reason_code`를 쓴다.

루프 안의 예외는 상위에서 삼켜지고 stderr로만 나간다 → **실패는 예외가 아니라 DB
상태로 드러낸다.** 파드 생성이 `ExecutionError`면 그 자리에서 `Failed` +
`reason_code=submit_failed`로 기록한다.

폴링 시 객체가 사라졌으면 기존 어댑터 규약대로 **FAILED**로 본다. 따라서 빌드 파드는
소비 전에 지워지면 안 된다 — `PodGarbageCollector`가 종단 빌드의 파드만, 그것도
`pod_gc_after_seconds`(24h) 뒤에 수거하도록 한다.

로그는 파드가 살아 있는 동안 `read_log`로 실시간 제공하고, 종단 전이 시점에 tail을
`builds.log_text`(새 컬럼, 상한 있는 텍스트)에 박제해 GC 후에도 이력이 남게 한다.

---

## 6. API

전부 admin 전용. 모든 뮤테이션은 같은 트랜잭션에서 `audit_log`를 쓴다
(`mutation_class="build"`).

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/api/admin/builds` | `{repo_url?, git_ref, images[]}` → `202 {build_id, state}`. `repo_url` 미지정이면 서버 기본값 |
| GET | `/api/admin/builds` | 목록 (최신순, `limit`) |
| GET | `/api/admin/builds/{build_id}` | 상세 |
| GET | `/api/admin/builds/{build_id}/log` | 로그 — 파드가 살아 있으면 실시간, 아니면 박제된 tail |
| PUT | `/api/admin/control-state` | 기존 엔드포인트에 `build_node_name` 추가 |

유지보수 모드에서는 빌드 제출을 거부한다 (`reject_when_maintenance`).

거부 사유 코드: `build_node_not_set`, `unknown_image`, `invalid_git_ref`,
`build_in_progress`(동시 빌드 1개로 제한 — 같은 노드에서 buildah 두 개가 도는 것은
디스크·캐시 충돌을 부른다).

---

## 7. 포탈 화면 — 「빌드」

`features/builds/`. 기존 admin 화면 규약을 그대로 따른다:
`<RequireRole role="admin"><AppShell>`, 사이드바 링크는 `{isAdmin && …}`,
h1은 정확히 **빌드** (라우터 테스트가 이 문자열을 assert한다).

- 상단: 지정 빌드 노드 표시 + 미지정이면 운영 제어 화면으로 유도.
- 제출 폼: git ref(기본 `main`), 이미지 체크박스 3종(기본 `dms`만 체크).
- 목록 테이블: 시각 / ref / commit(짧게) / 이미지 / 노드 / 상태 / 소요.
- 상세: 태그, 사유 코드, 로그 뷰어(기존 `tail_lines` 재사용).

새 사유 코드는 전부 `lib/api.ts`의 `REASON_MESSAGES`에 넣는다 — 컴포넌트에 한글
문자열을 하드코딩하지 않는다. 백엔드 응답은 `asArray` 류로 방어적으로 정규화한다
(느슨한 페이로드가 SPA 전체를 흰 화면으로 만든 슬라이스 9 사고 재발 방지).

---

## 8. 이 슬라이스에서 하지 않는 것

- **롤아웃** (컴포넌트 이미지 교체, `releases` 테이블, apps/* RBAC) → 슬라이스 12.
- 레지스트리를 클러스터 안으로 옮기는 것 (상위 스펙은 in-cluster registry:2를 말하지만
  현행 `pkg-01:5000`이 동작하고 있고, 이전은 독립된 인프라 작업이다).
- 빌드 레이어 캐시 영속화. 매 빌드가 처음부터 받는다 — `dms` 이미지에서는 견딜 만하고,
  `dms-mpifileutils`가 느린 것은 위에서 기본 선택 해제로 완화했다.
- 다중 아키텍처 빌드.

---

## 9. 실증 (테스트베드)

1. 운영 제어 화면에서 빌드 노드를 `dms-w1`로 지정 → `control_state`에 반영 확인.
2. 미지정 상태 제출이 `422 build_node_not_set`인지 (지정 전에 먼저 확인).
3. 포탈에서 `git_ref=main`, `images=[dms]`로 빌드 제출 → 파드가 **dms-w1에** 뜨는지
   `kubectl get pod -o wide`로 확인.
4. 빌드 진행 중 로그가 포탈에서 실시간으로 보이는지.
5. 종단 후 `builds` 행에 `commit_sha`, `images`, `state=Succeeded`가 채워졌는지.
6. `curl http://pkg-01:5000/v2/dms/tags/list`에 새 태그 `b<...>`가 있는지.
7. 동시 빌드 제출이 `build_in_progress`로 거부되는지.
8. 감사 로그에 `mutation_class=build` 행이 남았는지.
