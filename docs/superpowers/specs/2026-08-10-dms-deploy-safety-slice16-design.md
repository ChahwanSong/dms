# 슬라이스 16 — 배포 안전망 설계

설계가 약속했는데 코드에는 없는 불변식과, 조용히 프로덕션을 되돌리는 배포 경로를
닫는다. 백로그(`docs/superpowers/BACKLOG.md` §1 슬라이스 16)의 5개 항목이다.

## 1. 실측으로 확인한 전제 (코드·매니페스트 직접 확인)

1. **매니페스트는 파드에서 읽을 수 없다.** `deploy/docker/Dockerfile.dms:45-51`은
   `pyproject.toml`·`src/`·웹 산출물만 COPY한다. `deploy/`는 이미지에 없고, ConfigMap
   마운트도 없으며, api/controller Role에 `configmaps` 권한이 없다
   (`deploy/k8s/10-rbac.yaml`). 즉 **오늘은 파일 대 라이브 비교가 불가능**하다.
   단, 매니페스트를 파싱하는 부분집합 YAML 파서가 이미 테스트에 있다
   (`tests/test_release_manifest_contract.py:21-26`).
2. **`observe()`가 주는 라이브 이미지**는 파드가 아니라 **워크로드 파드템플릿의**
   `images = {container: image}`다(`src/dms/rollout_status.py:19-24`) — `kubectl apply`가
   덮어쓰는 바로 그 필드라 비교 대상으로 정확하다.
3. **`migrate()`는 `migrate` 서브커맨드에서만 불린다**(`src/dms/cli.py:40-44`).
   api/controller 시작 경로에 스키마 생성이 없다. **initContainer는 전무**하다.
4. **`migrate()`는 동시 실행에 안전하지 않다.** `_ensure_columns`
   (`src/dms/migrations.py:346-367`)가 "존재 확인 후 `ALTER TABLE ADD COLUMN`"이라
   두 러너가 동시에 통과하면 뒤쪽이 `42701 column already exists`로 죽는다.
   어드바이저리 락은 어디에도 없다(`src/dms/db.py`의 `RLock`은 프로세스 지역).
5. **플래너는 즉시 영구 거부한다.** `src/dms/planner.py:97-98`의 `PlacementError` →
   `_reject`가 `results` 행까지 써서 종단으로 만든다. 반면 **`run_once`는 예외를
   삼키고 상태를 건드리지 않으면 요청이 Pending으로 남아 다음 틱에 다시 뽑힌다**
   (`planner.py:23-38`, `requests.py:85-90`) — 유예의 자리가 이미 있다.
   `identity_probe_targets` 등록은 `identity.py:76`, 즉 **첫 요청 시점**뿐이고
   로그인·계정생성 어디서도 선등록하지 않는다. 최악 전파 지연 ≈ **130초**
   (보고 주기 60s × 2 + 플래너 10s; 목록이 보고 *응답*에 실려 한 주기 늦는 구조).
6. **워커 파드 템플릿에는 라벨이 아예 없다.** volcano task는
   `{"template": {"spec": {...}}}`뿐이고 `metadata` 키가 없다
   (`src/dms/execution_manifests.py:310,315,320,350,356`). 자기참조
   `labelSelector`를 쓰려면 라벨을 먼저 붙여야 한다. `affinity`는
   `_node_affinity(nodes)`가 **단일 키 dict**를 돌려주는 자리에 들어간다(`:223-226`).
7. **레플리카는 후보 노드 수를 넘지 않는다.** `resolve_fanout`의
   `node_count = min(len(candidates), max_nodes)`(`placement.py:115,118-119`).
   → **required 안티어피니티가 잡을 영구 Pending으로 만들 수 없다.**
8. **에이전트는 포트를 열지 않는다**(`ports:` 없음, httpx 아웃바운드 전용, nslcd는
   유닉스 소켓). 그러나 API를 **클러스터 DNS**로 찾는다
   (`DMS_AGENT_API_URL: http://dms-api:8080`).
9. **`/proc/net/*`는 네트워크 네임스페이스 범위**다. 그래서 파드 안에서 읽은
   `network_rx/tx_bytes`가 veth 값이다. `loadavg`/`meminfo`는 네임스페이스되지 않아
   **이미 호스트 값**이다 — 틀린 것은 네트워크뿐이다. 에이전트는 이미
   `/proc/1/mountinfo`를 hostPath **File**로 마운트해 쓰는 관례가 있고
   (`50-agent-daemonset.yaml:119-123`), 그 경로만 `DMS_AGENT_MOUNTINFO_PATH`로
   주입 가능하다(`src/dms/config.py:191,210`). 나머지 `/proc` 경로는 하드코딩이다
   (`src/dms/agent/probes.py:98,103,121`).

## 2. 핵심 결정

### 2.1 드리프트는 **이미지에 매니페스트를 동봉해** 비교한다

`deploy/k8s/*.yaml`를 `Dockerfile.dms`가 COPY하고, 테스트에 있던 부분집합 YAML
파서를 `src/dms/manifest_tags.py`로 승격해 api가 읽는다. 비교는
`observe().images[container]`(라이브) vs 매니페스트의 `image:`다.

의미가 정확한 이유: 동봉본은 "**이 이미지를 만든 소스 트리의 매니페스트**"다.
포탈 롤아웃은 매니페스트를 고치지 않으므로, 롤아웃 직후에는 반드시
`live != manifest`가 되어 **정확히 그 위험(다음 `kubectl apply`가 되돌림)을 표시**한다.
반대로 우리 관례대로 매니페스트를 먼저 고치고 빌드·배포하면 일치해 조용하다.

기각: ConfigMap 마운트(ConfigMap 자체가 또 드리프트하고 RBAC 확대 필요),
CI 전용 검사(라이브 상태를 모름).

`DMS_JOB_IMAGE`도 같은 위험이라 함께 낸다 — api의 실제 env 값 vs 동봉된
`20-config.yaml` 값.

### 2.2 migrate는 **initContainer + 어드바이저리 락**으로 자동화한다

api·controller 파드에 `initContainer: dms migrate`를 넣어 **스키마 변경 배포가
자동으로 마이그레이션**되게 한다(슬라이스 14는 이걸 안 해서 실 500을 냈고,
슬라이스 15도 수동 재실행이 필요했다).

두 파드가 동시에 뜨면 §1-4의 경합이 그대로 터지므로 **PostgreSQL 어드바이저리 락**
(`pg_advisory_lock`/`unlock`, 고정 키)으로 `migrate()` 전체를 감싼다. SQLite는 no-op.
락은 initContainer뿐 아니라 기존 migrate Job에도 그대로 적용돼 전체 경로가 안전해진다.
기존 one-shot Job은 **유지**한다(명시적 실행·복구 수단).

### 2.3 플래너는 신원 전파를 **유예**한다 (거부하지 않음)

`PlacementError`에 `rejections`를 실어 보내고, `_plan_one`이
**(a) 사유가 정확히 `identity_not_ready_on_node`인 노드가 하나라도 있고
(b) `now - requests.created_at < grace`** 인 경우에만 아무 상태도 바꾸지 않고
반환한다. 요청은 Pending으로 남아 다음 틱에 다시 계획된다. 유예 초과 시 기존대로
거부한다.

> **정정(구현 중 발견)**: 초안은 "(a) 사유가 `no_eligible_nodes`이고 (b) **모든**
> 노드의 사유가 identity"였다. 이 규칙은 **§6-4의 합격 기준을 스스로 못 맞춘다** —
> 슬라이스 15에서 실패한 시나리오에는 sync(`no_ready_sync_candidate`)가 포함됐고,
> 게다가 실 테스트베드 형상에서 사유가 **섞인다**(cephfs-third는 w1-3 Ready /
> w4-5 Missing이라 전파 전에는 w1-3이 identity, w4-5가 `missing_target_mount`).
> 그래서 "하나라도"로 바꾸고 `no_ready_sync_candidate`까지 포함한다.
>
> "하나라도"로 충분한 근거는 코드 구조에 있다: `eligible_nodes`는 노드마다 **첫
> 실패 사유 하나**만 기록하고 검사 순서가 mount → writable → tool → **identity(마지막)**
> 다. 따라서 사유가 identity인 노드는 마운트·쓰기·도구를 **이미 통과**했고 신원 전파만
> 남았다 — 전파되면 그 노드는 반드시 적격이 된다.
>
> sync의 `rejections`는 `{"source": ..., "destination": ...}` 형태이므로 두 쪽의
> **합집합**에 같은 규칙을 적용한다. 받아들이는 트레이드오프: source에만 identity
> 노드가 있고 destination이 전부 미마운트면 끝내 실패하지만 grace만큼 Pending 후
> 거부된다 — 영구 오거부보다 낫고 스스로 수렴한다.

grace 기본값 **300초**(`DMS_PLANNER_IDENTITY_GRACE_SECONDS`) — 최악 전파 130초의
2배 남짓. 짧게 두는 이유: 같은 `resource_key`의 후속 요청이 `find_active`에 걸려
Conflict가 되므로(`planner.py:56-63`) 무한정 붙잡으면 안 된다.

유예는 **관측 가능해야** 하므로 매 유예마다 이벤트를 남긴다(사유
`identity_propagating`). 스토리지 미마운트 같은 진짜 결격은 그대로 즉시 거부된다 —
`rejections`로 구분되기 때문이다.

기각: 로그인 시 선등록(포탈 계정명과 잡 소유자가 항상 같지 않고, 결국 전파 시간은
그대로라 근본 해결이 아니다).

### 2.4 워커 anti-affinity는 **라벨을 먼저 붙이고** required로 건다

워커 task 템플릿에 `metadata.labels`(`dms.io/job-id`, `dms.io/task`)를 추가하고,
`_node_affinity`가 반환하는 dict에 `podAntiAffinity`를 병합한다
(`requiredDuringSchedulingIgnoredDuringExecution`, `topologyKey:
kubernetes.io/hostname`, 셀렉터는 **같은 job의 같은 task**).

required로 두는 근거는 §1-7이다 — 레플리카가 후보 노드 수를 넘지 않으므로 배치
불가가 생기지 않는다. nsync는 source/destination이 별도 task라 각자 자기 풀 안에서
퍼진다.

이것이 없으면 `max_nodes`가 **노드가 아니라 레플리카만** 제한해 MPI 팬아웃이 한
노드로 붕괴할 수 있다(원본 설계 §181의 약속 위반).

### 2.5 네트워크 지표는 **hostNetwork 없이** 고친다

`/proc/1/net/dev`(PID 1 = 호스트 netns)를 hostPath **File**로
`/host/proc/1/net/dev`에 마운트하고, `probe_os_metrics`의 net 경로를
`DMS_AGENT_NET_DEV_PATH`로 주입 가능하게 만든다 — 이미 `mountinfo`가 쓰는 바로 그
관례다.

기각한 `hostNetwork: true`는 (a) `dnsPolicy: ClusterFirstWithHostNet`을 반드시 같이
바꿔야 하고(안 바꾸면 `dms-api` DNS가 죽어 에이전트가 **조용히 영구 보고 중단**),
(b) 네트워크 노출면을 넓힌다. 얻는 것은 같은데 위험만 크다.
평범한 `/host/proc` 디렉터리 마운트도 기각 — `/proc/net`은 **읽는 쪽 netns**를
반영하므로 그 경로로는 고쳐지지 않는다. PID 1 경로여야 한다.

`loadavg`/`meminfo`는 이미 호스트 값이라 건드리지 않는다.

### 2.6 네트워크 지표는 **물리 인터페이스만** 합산한다 (§2.5 후속)

§2.5 배포 후 실측하니, `lo` 만 제외한 합은 호스트 netns 의 **모든** 인터페이스를
더하고 있었다(dms-w3 기준 `eth0` 2.80TB + `cilium_vxlan` 4.46GB + 파드 veth 호스트쪽
`lxc*` + `cilium_host`/`cilium_net`). 두 가지가 어긋난다:

- **노드 간 파드 트래픽이 이중 계상**된다 — `cilium_vxlan` 을 타고 캡슐화돼 `eth0`
  으로도 나가므로 같은 바이트가 두 번 잡힌다.
- **같은 노드 안 파드끼리의 트래픽**은 `eth0` 을 안 거치고 `lxc*` 에만 잡힌다.

현 테스트베드는 `eth0` 이 99.8% 라 사실상 물리 NIC 를 따라가지만, 그건 지금의 트래픽
구성 때문이지 보장이 아니다. 대시보드가 "이 노드의 네트워크 처리량"으로 읽히므로
**물리 인터페이스만** 합산한다.

**판별은 이름이 아니라 커널 등록 위치로 한다.** 접두 블록리스트
(`lxc*`/`cilium_*`/`cali*`/`flannel*`/…)는 CNI 마다 달라 조용히 틀린다. 커널은 가상
인터페이스를 `/sys/devices/virtual/net/<name>` 아래 등록하므로 **`/proc/net/dev` 에는
있는데 그 디렉터리에는 없는 것이 물리**다. 실측 확인(dms-w3): `/proc/net/dev` 8개 중
`/sys/devices/virtual/net/` 에 7개가 있고 빠진 것은 `eth0` 하나뿐이다. `lo` 도 가상이라
자동으로 걸러진다.

**함정(반드시 지킬 것): 기본값은 "필터 없음" 이어야 한다.** 파드 안에도
`/sys/devices/virtual/net/` 이 존재하고 거기엔 파드 자신의 가상 인터페이스가
들어 있는데 그 이름이 보통 **`eth0`** 이다. 그래서 기본값을 `/sys/devices/virtual/net`
으로 두면, 마운트가 없는 배포에서 파드의 sysfs 를 읽어 **호스트의 `eth0` 을 가상으로
오판해 제외**해 버린다 — 정확히 고치려던 것보다 더 나쁜 값이 된다. 따라서
`DMS_AGENT_VIRTUAL_NET_PATH` 는 **기본 미설정**이고, 명시적으로 설정됐고 읽히는
경우에만 필터한다. 미설정·읽기 실패면 기존처럼 `lo` 만 제외하고 전부 더한다
(지표를 잃는 것보다 덜 정밀한 값을 유지하는 편이 낫다).

DaemonSet 은 hostPath **Directory** `/sys/devices/virtual/net` 를
`/host/sys/devices/virtual/net` 에 읽기 전용으로 마운트하고 env 로 그 경로를 준다 —
`mountinfo`/`net_dev` 와 같은 관례다.

## 3. 화면

- 대시보드 컴포넌트 카드: 라이브 이미지 옆에 **불일치 배지**와 매니페스트 값,
  "다음 `kubectl apply`가 이 태그로 되돌립니다"라는 한 줄. 일치하면 아무것도 안 낸다.
- 잡 이미지(`DMS_JOB_IMAGE`) 불일치도 같은 카드에 한 줄.

## 4. 오류 처리

전면 fail-soft를 유지한다. 매니페스트 동봉본이 없거나 파싱 실패면 `manifest_image`는
null이고 배지를 내지 않는다(추측하지 않는다). 어드바이저리 락 획득 실패는 예외로
올려 initContainer를 실패시킨다 — 스키마가 불확실한 채 앱이 뜨는 것보다 낫다.

## 5. 테스트

- 매니페스트 파서: 실제 `deploy/k8s/*.yaml`을 그대로 파싱해 태그를 뽑는 단언
  (테스트에서 승격한 파서라 기존 계약 테스트와 동일 대상).
- 드리프트: live==manifest면 무경고, 다르면 경고, manifest 없음이면 무경고.
- 어드바이저리 락: PG 방언에서 lock/unlock SQL이 실제로 발행되는지, SQLite에서는
  발행되지 않는지(가짜 db로 단언). 락 해제가 예외 경로에서도 일어나는지.
- 플래너 유예: identity-only 거절 + grace 내 → 상태 불변·이벤트 기록·다음 틱 재계획;
  grace 초과 → 거부; 스토리지 결격이 섞이면 → 즉시 거부.
- anti-affinity: 워커 task에 라벨과 required 규칙이 붙는지, nsync 두 워커가 각각
  자기 task 셀렉터를 갖는지, 런처에는 안 붙는지.
- net 경로 주입: 기본값과 주입값 모두에서 `probe_os_metrics`가 올바른 파일을 읽는지.

## 6. 실증 (테스트베드)

1. 새 이미지 배포 후 대시보드에 드리프트 배지가 **없는지**(매니페스트를 먼저 고치는
   우리 관례대로면 일치).
2. 포탈에서 이전 태그로 롤아웃 → 배지가 **뜨는지** → 매니페스트를 고치면 사라지는지.
3. 스키마 변경 없이도 initContainer가 정상 통과하고 api/controller가 뜨는지,
   두 파드 동시 기동에서 마이그레이션 충돌이 없는지(로그 확인).
4. 신규 사용자로 첫 요청 → 즉시 거부되지 않고 Pending 유지 후 자동 성공하는지
   (이번 슬라이스 15 실증에서 실패했던 바로 그 시나리오).
5. `max_nodes ≥ 2`인 정책으로 sync 실행 → 워커 파드가 **서로 다른 노드**에 뜨는지.
6. 에이전트 재배포 후 `network_rx_bytes`가 파드 veth가 아닌 호스트 값인지
   (노드에서 직접 읽은 값과 대조).

## 7. 이 슬라이스에서 하지 않는 것

- 진짜 파일-대-라이브 비교(동봉본은 빌드 시점 스냅샷이다) — 필요해지면 별도 슬라이스.
- 매니페스트 자동 수정/커밋, 템플릿 계층(Helm/kustomize) 도입 — 의도적 제외 유지.
- `DMS_JOB_IMAGE`의 포탈 롤아웃(ConfigMap 갱신 + 소비자 재시작이라 별개 작업).
- 롤백 버튼, 알림/경보.
- 플래너의 일반 재시도(신원 전파 유예만 다룬다. 다른 사유는 기존대로 즉시 거부).
- `loadavg`/`meminfo` 경로 변경, `hostPID`, 에이전트 권한 축소.
