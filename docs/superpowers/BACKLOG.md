# DMS 백로그

이 저장소에는 로드맵 문서가 없었다. 범위는 원본 설계
(`specs/2026-08-02-dms-clean-slate-design.md`)와 슬라이스별 design+plan 쌍에 흩어져
있고, 미완 항목은 각 설계의 "이 슬라이스에서 하지 않는 것", 플랜의 "알려진 위험",
SDD 레저의 `minor (deferred)`, `deploy/README.md`의 미해결 값에 흩어져 있었다.

**이 파일을 만든 이유**: SDD 레저(`.superpowers/sdd/*/progress.md`)는 `.gitignore`가
`*`라 git에 들어가지 않는다. 실제로 **슬라이스 11·12·13의 레저가 이미 소실**됐고 —
그 슬라이스들이 파킹한 마이너 항목 기록은 복구 불가다. 코드 내 TODO/FIXME는 사실상
0건이라 백로그를 상기시켜 줄 장치가 아무것도 없었다. 앞으로 **파킹하는 항목은 레저가
아니라 이 파일에 적는다.**

갱신: 2026-08-11. 기준: 슬라이스 18·19·20 은 `main` 에 병합됨(fast-forward),
슬라이스 21 은 브랜치 `worktree-dms-slice21`.

---

## 0. 현재 상태

- 슬라이스 1~21 완료.
- 테스트베드 이미지: 제어면 `dms:d33`, 에이전트 `dms-agent:d33`,
  잡 러너 `dms-mpifileutils:d27`. 태그 체계를 dNN/jobNN 분리에서 단일 dNN 으로
  바꿨다 — 세 이미지가 같은 소스 트리에서 나오고 agent 는 나머지 둘을 같은 태그로
  참조해 빌드되므로 분리가 오히려 함정이었다.
- ✅ 슬라이스 15 nsync 카운트 파서 **실증 완료**(d27, 잡 ace0581d):
  `{"files": 10, "bytes": 50}` + 컬럼 채워짐. 이로써 네 도구 전부(dsync·dscan·drm·
  nsync) 검증됨. cephfs-third(w1-3) -> cephfs-secondary(w4-5) 는 노드가 겹치지 않아
  placement 가 nsync 를 고른다 — 재현 시 목적지 이름을 매번 새로 잡아야 실제 복사가
  일어나 카운트가 0 이 아니다.
- 네트워크 지표는 **물리 인터페이스만** 합산으로 정정됨(설계 §2.6, d27 실증:
  전체 2,806,890,347,761 vs 물리 2,802,248,354,570 — 가상 4.64GB 제외).

---

## 1. 예정 슬라이스

### ✅ 슬라이스 16 «배포 안전망» — **완료**(2026-08-10, d26 배포·§6 실증 6/6 통과)

실증 결과: (1) 매니페스트-우선 관례대로 배포하니 드리프트 배지 없음. (2) 포탈로
controller 를 d25 로 롤아웃하자 배지가 뜨고 원복하니 사라짐. (3) api·controller 를
동시에 올려 initContainer 두 개가 병렬 migrate → 둘 다 "migrated"(락 없으면 42701
로 하나가 죽는다). (4) 콜드 스타트 sync 가 즉시 거부 대신 Pending 유지 후 성공,
`identity_propagating` 이벤트에 source/destination 5노드 전부 기록. (5) 워커 5개가
w1~w5 **각각 다른 노드**에 — Volcano 가 템플릿 라벨을 파드에 전파함도 함께 확인.
(6) 에이전트 rx=2,806,825,281,085 vs 호스트 실측 2,806,825,725,719(파드 veth 는
8,536) — 호스트 netns 를 읽는다.

아래는 착수 당시의 원 항목이다(기록 보존용).

### 슬라이스 16 «배포 안전망» — 조용히 프로덕션을 깨는 것들
1. **매니페스트 드리프트 표시·경고**. 포탈 롤아웃은 살아있는 오브젝트만 바꾸므로
   이후 `kubectl apply -f deploy/k8s/`가 **옛 태그로 되돌린다**.
   `deploy/README.md` §9-4가 "드리프트 표시는 슬라이스 14 대시보드의 몫"이라 적었으나
   슬라이스 14는 만들지 않았다(설계 §2.4는 이미지+ready+판정으로만 범위 한정).
2. **스키마 변경 배포의 migrate 자동화**. `set image`는 migrate Job을 재실행하지
   않는다 — 슬라이스 14에서 실 500(`column "files_count" does not exist`), 슬라이스
   15에서도 수동 재실행 필요. "교훈"으로만 기록되고 자동화 안 됨.
3. **플래너 신원 전파 유예/재시도**. `placement.py:61,68,92`가 `no_eligible_nodes`/
   `no_ready_sync_candidate`를 **즉시 영구 거부**한다. `identity_probe_targets` 등록이
   첫 요청 시점이라 **신규 사용자의 첫 요청은 항상 실패**한다. phase3c에서 파킹
   (`progress.md:126`)됐고 슬라이스 15 실증에서 재현.
4. **워커 `podAntiAffinity` 미구현**. 원본 설계 §181이 "워커 anti-affinity(노드당 1개)"를
   명시했고 phase3c 레저 `:9`가 "플랜 텍스트엔 있으나 코드엔 없음"으로 파킹.
   `grep -rn "antiAffinity" src/dms/` → 0건. **`max_nodes` 정책이 노드가 아니라
   레플리카만 제한**하게 되어 MPI 팬아웃이 한 노드로 붕괴할 수 있다.
5. **에이전트 `hostNetwork` 네트워크 지표**. `probe_os_metrics()`가 파드 netns의
   `/proc/net/dev`를 읽어 `network_rx_bytes`/`tx`가 **veth 값**이다(`deploy/README.md`
   미해결 값). 슬라이스 14가 이 값을 차분해 대시보드 라인차트로 만들어 운영자가
   신뢰하게 됐다 — 틀린 수를 신뢰하게 만든 상태.

### ✅ 슬라이스 17 «큐 가시성» — **완료**(2026-08-10, d28 배포·§6 실증 6/6 통과)

실증 결과: (1) RBAC 적용 전에는 두 축 모두 `null`(알 수 없음)이고 엔드포인트는 200 —
`[]`로 뭉갰다면 운영자가 "큐가 한가하다"로 읽었을 상황이다. 적용 후 `Open` + `[]`가
되어 앞의 `null`과 구분된다. (2) 실 sync 잡에서 PodGroup 이 Inqueue(3초) →
Running(6초)로 잡히고 완료 후 사라졌다 — "PodGroup 은 잡 종료와 함께 삭제된다"가
화면에서 확인됐다. (3) 백필된 52건 중 **47건이 0초**였다(1초 해상도). falsy 검사가
한 곳이라도 남았다면 데이터의 90%가 조용히 사라졌을 것 — SQL 술어·히스토그램 가드·
라우트 세 계층에 각각 심은 가드가 값어치를 했다.

파생 항목(→ 슬라이스 20): PodGroup 이 잡과 함께 삭제되므로 **끝난 잡의 스케줄링
대기 이력이 없다**. 설계 §7 이 후속을 예고했다.

`runs` 테이블 부활은 **명시적으로 배제**하고 `data_jobs.submit_wait_seconds` 파생
컬럼 + 커버링 인덱스를 택했다(설계 §2.3). 아래는 착수 당시의 원 항목이다(기록 보존용).

### 슬라이스 17 «큐 가시성»
1. **Volcano 큐 현황 대시보드**(사용자 요청): 대기 중 작업 갯수, 대기 시간, 통계.
   슬라이스 14 비목표(`slice14-design.md:79,153`: "코드·RBAC 없음, CRD 읽기 + Role
   변경 필요"). 원본 설계 §300이 "Volcano 큐/우선순위"를 요구.
2. **전역 큐 대기 집계**. `runs` 테이블이 死物이다 — `migrations.py:69`가 만들지만
   읽기·쓰기 0건. 그래서 큐 대기는 요청 상세에서만 유도되고 전역 집계는 풀스캔이라
   금지됐다(`slice14-design.md:63-68`). 원본 설계 §293이 요구한 지표.
   → `runs` 부활 또는 `data_jobs`에 인덱스된 파생 컬럼 중 택일(설계 시 결정).

### ✅ 슬라이스 18 «아티팩트 경로 설정» — **완료**(2026-08-11, d29 배포·§6 실증 6/6 통과)

설계 `specs/2026-08-10-dms-artifact-base-slice18-design.md`, 플랜
`plans/2026-08-10-dms-artifact-base-slice18.md`. 백엔드 1043 / 프론트 219 / tsc 0.

실증 결과(테스트베드, d29):
1. DB 미설정 → `source: env`, `db_value: null`, `effective == env_value` — 기존 배포
   무변화. **라이브 DB 는 오래전에 만들어졌으므로 새 컬럼 5개는 `_ensure_columns`
   ALTER 경로로 들어왔다**(슬라이스 14 의 "한쪽만 넣으면 라이브에서만 컬럼이 없다"
   교훈이 이번엔 통과).
2. base 를 `/cephfs/dms/artifacts-slice18` 로 바꾸고 실 scan 잡을 돌리니
   `artifact_uri` 가 새 경로를 가리키고 `files_count=10` 이 채워졌다 — 컨트롤러가
   **새 경로에서 summary.json 을 실제로 읽어냈다**는 뜻이라 쓰기→읽기 사슬 전체가
   확인됐다. 디스크에도 dscan-report/summary/stdout/stderr 가 요청자 uid 로 있었다.
3. 잡 55건 상태에서 PUT 이 409 `artifact_base_locked`, `force:true` 로 통과.
   감사에 `{"forced": true, "affected_jobs": 53}` 기록. **부수 확인**: `before_state`
   의 `changed_by`/`changed_at` 이 그대로였다 — 전용 UPDATE 가 컨트롤 상태의 변경
   이력을 오염시키지 않는다(설계 §2.1)는 것이 라이브에서 확인됐다.
4. 없는 경로 → `validate` 가 422 `artifact_base_missing`. 파일을 주면
   `artifact_base_not_directory`. **PUT 은 잠금이 먼저라 409 가 난다** — 정규화 →
   잠금 → 즉석 검증 순서(설계 §2.5)가 라이브에서 그대로 관측됐다.
5. **핵심**: `/cephfs` 밖 경로(API 파드에만 만든 `/tmp/dms-outside`)로 바꾸니
   API `ok:true` / 컨트롤러 `artifact_base_missing` / 노드 `exists:false` 로
   **세 홉이 실제로 갈라졌다**. API 혼자 판단했다면 "쓰기 가능"으로 저장하고 끝났을
   상황이다. 아직 프로브하지 않은 노드는 `pending` 으로 실패와 구분됐다.
6. 경로 중간 `file://` → 422 `artifact_base_scheme_in_path`. 상대경로·`..` 도 각각
   고유 사유 코드로 거부.

포탈 화면(`/admin/artifact-base`)도 라이브 확인: 경로 변경 직후 노드 5행이 전부
「확인 대기 중」이었다가 60s 주기로 「있음/가능」으로 수렴하는 것을 브라우저에서 봤다.

**이번에 배포 순서를 틀렸다(기록)**: 이미지를 먼저 빌드하고 매니페스트를 나중에
올렸다. 드리프트 판정은 **그 이미지를 만든 소스 트리의 매니페스트**를 보므로
(`manifest_tags.py` 머리 주석 — Dockerfile.dms 가 deploy/k8s 를 이미지에 COPY 한다),
d29 안의 동봉본은 d28/d27 이고 라이브는 d29 라 배지가 떴다. 슬라이스 16 이 세운
**매니페스트-우선** 관례의 진짜 의미가 이것이다: **매니페스트를 먼저 올려 커밋하고,
그 커밋으로 이미지를 빌드해야** 동봉본과 라이브가 일치한다. 다음 슬라이스 배포부터
그 순서를 지킨다(그때 이 드리프트도 함께 해소된다).

아래는 착수 당시의 원 항목이다(기록 보존용).

### 슬라이스 18 «아티팩트 경로 설정» (사용자 요청)
- 포탈에서 **아티팩트 저장 경로 설정** + **가능여부 검증**(실제로 써도 문제없는지).
- 현재는 ConfigMap 환경변수 `DMS_ARTIFACT_BASE_URI=file:///cephfs/dms/artifacts` 고정.
- **설계 시 반드시 다룰 것**: 경로를 바꾸면 **기존 잡의 아티팩트를 못 읽는다**.
  `execution_volcano.py`가 `self._artifact_base`로 `summary.json` 경로를 재구성하므로
  (`:207-222`) 옛 잡의 요약/로그 열람이 깨진다. 마이그레이션/이중 조회/잡별 base 기록
  중 택일 필요. 검증은 "컨트롤러·API·잡 파드 세 곳에서 쓰기 가능한 공유 FS인가"를
  봐야 하며 노드별 마운트 상태(에이전트 리포트)와 교차 확인해야 한다.

### ✅ 슬라이스 19 «계정 위생» — **완료**(2026-08-11, d30/d31 배포·실증 통과)

설계 `specs/2026-08-10-dms-account-hygiene-slice19-design.md`, 플랜
`plans/2026-08-10-dms-account-hygiene-slice19.md`. 백엔드 1073 / 프론트 224 / tsc 0.

실증 결과(테스트베드, d30):
1. **스푸핑 차단**: 공유 토큰 + `x-dms-actor: root` → **400 `invalid_actor`**,
   `x-dms-actor: alice`(임의 사용자 사칭)도 400. 고치기 **전에** 체인을 실제로
   재현해 `ResolvedIdentity(uid=0, gid=0, privileged=True)` 가 나오는 것을 눈으로
   확인했다 — env 오버라이드 없이 **배포 기본값만으로** 열려 있었다.
2. **에이전트 무손상**: 5개 노드 전부 `node:<이름>` 경로로 계속 리포트(fresh=true).
   이게 깨졌으면 노드 지표·마운트 판정이 전부 멈춘다.
3. **백로그가 지목한 실제 피해 해소**: 유령 관리자 `s3verify` 를 실제로 삭제(204).
4. **마지막 활성 관리자 보호 3경로 전부**: 관리자를 한 명으로 줄인 뒤 삭제·강등·
   비활성화 시도가 **모두 409 `last_active_admin`**.
5. **자기 삭제 차단**: 세션 로그인 상태에서 자기 계정 삭제 → 409
   `cannot_delete_self`(토큰 경로에서는 actor 가 `shared-token` 이라 이 가드가
   발동하지 않는다 — 설계가 적어 둔 그대로).
6. **비종단 요청 가드**: 세션으로 scan 을 제출한 직후 그 계정 삭제 시도 → 409
   `account_has_active_requests`.
7. **세션 로그인으로 잡 제출이 정상 동작** — 설계 §2.2-3 이 예고한 새 실증 방식이
   실제로 성립한다(토큰으로는 더 이상 잡을 못 낸다).

리뷰에서 더 조인 것: `resolve_job_identity(session_authenticated=...)` 기본값이
fail-open 이었다. 프로덕션 호출자는 planner 하나뿐이라 정상 경로는 무관하지만,
미래에 호출자가 늘고 인자를 빠뜨리면 uid 0 승격이 조용히 되살아난다 →
**fail-closed(False)** 로 뒤집고 특권 검증 테스트 5곳이 조건을 명시하게 했다.

포탈에서 잡은 것(라이브에서만 보였다): 삭제 열이 붙으면서 계정 표가 뭉개졌다.
원인 두 가지 — `td` 자체를 flex 컨테이너로 쓴 것, 그리고 AppShell 의 flex 자식에
`min-w-0` 이 없어 안쪽 `overflow-x-auto` 가 발동하지 못하고 레이아웃 전체가 넓어져
사이드바를 밀어낸 것. 후자는 **넓은 표를 가진 모든 화면**의 잠복 결함이었다.

아래는 착수 당시의 원 항목이다(기록 보존용).

- **계정 삭제 API + 포탈 UI**. 슬라이스 9 비목표(`slice9-design.md:39-43`). 실제 피해:
  슬라이스 3 실증이 만든 임시 관리자 **`s3verify`가 아직 살아 있다**(삭제 수단 없음).
- **공유 토큰 actor 스푸핑**. 토큰 보유자가 `x-dms-actor: root`로 uid 0을 얻을 수 있다
  (`deploy/README.md` 미해결 값). 세션 기반 actor로 전환 검토.
- ❌ **회원가입 메일 인증은 이 슬라이스에서 제외** — 사용자 지시: 추후 회사 메일
  인증으로 교체할 계획이므로 지금 손대지 않는다. (현재 `routes_auth.py:22` 더미,
  코드 검증 없이 가입 가능. **의도적 보류**이며 미인지 결함이 아니다.)

### ✅ 슬라이스 21 «포탈 빌드 되살리기» — **완료**(2026-08-11, d33 배포·실증 통과)

설계 `specs/2026-08-11-dms-portal-build-slice21-design.md`, 플랜
`plans/2026-08-11-dms-portal-build-slice21.md`. 백엔드 1131 / 프론트 228 / tsc 0.

**이 테스트베드에서 포탈 빌드가 처음으로 성공했다** — 이전 유일한 기록은
`build_failed` 였다. 빌드 `824ce0e2`: `Succeeded`, tag `b824ce0e2`, commit
`80d06c5b2977`, `pkg-01:5000/dms:b824ce0e2` push 완료.

요구가 착수 당시와 달라졌다: 컨트롤플레인이 아니라 **워커 노드 하나**를 지정하고,
그 워커는 **데이터 잡 풀에서 빠지지 않는다**(잡과 빌드를 동시에). 그래서 아래
「원 항목」의 컨트롤플레인 과제 3건은 소멸했고, 대신 리소스 봉투와 축출 순서가
슬라이스의 핵심이 됐다.

실증 결과(테스트베드, d33):
1. **egress 차단 → 45초 만에 `build_node_no_egress`**, 로그에 실패 호스트 전부
   (`unreachable_443=github.com,quay.io,registry-1.docker.io`). 2시간 generic
   타임아웃이 아니라 45초다 — 이 슬라이스의 핵심 개선.
2. **실 빌드 성공.** 가장 큰 미지수였던 **npm(vite) 빌드가 memory limit 1Gi 안에서
   정상 동작**함이 확인됐다. emptyDir 피크는 **1.2G**(sizeLimit 10Gi 대비 여유 충분)
   — §2.2/§2.4 봉투는 재보정 없이 유지한다.
3. **빌드 중 데이터 잡 무손상**: 빌드가 도는 동안 scan 잡이 20초에 완료,
   `sched_wait=5`·`submit_wait=0` 으로 **평시와 동일**, **잡 파드 축출 0건**.
   (평시 기준선 95초는 신원 전파 지연이 포함돼 오히려 더 느렸다.)
4. **축출 순서의 입력값 확인**: 빌드 파드 `dms-build`/priority **10**/Burstable,
   데이터 잡 `dms-mid`/priority **100**/BestEffort. kubelet 은 "requests 초과 →
   priority 낮은 순"으로 축출하므로 압박 시 빌드가 먼저 죽는다. 실제 노드 메모리
   압박 유발은 클러스터를 위협하므로 **입력값 검증으로 대체**했다 — 정직하게 기록한다.

**실증이 찾아낸 결함 1건(고침)**: 프리플라이트 프로브는 **파드 네트워크**로 egress 를
검사하는데 빌더 이미지 pull 은 kubelet/CRI-O 가 **노드 네트워크**로 수행한다
(`imagePullPolicy: Always` 라 매 빌드마다). 두 경로가 갈려 있어, 노드 egress 만 막으면
프로브는 통과하고 빌드 파드가 `ImagePullBackOff` 로 앉는다(2시간 뒤 `build_stuck_pending`).
→ 빌더 이미지를 **`pkg-01:5000/buildah:stable` 미러**로 돌려 노드의 인터넷 의존을
없앴다. 이제 남는 인터넷 수요가 전부 빌드 파드 안이라 프로브가 검사하는 경로와
실제 필요 경로가 일치한다.

**미실행 실증 2건(정직하게 남긴다)**: §6-5 디스크 부족(`build_node_disk_low`)과
§6-6 레지스트리 차단(`build_registry_unreachable`)은 단위 테스트로만 덮였고
테스트베드 재현은 하지 않았다. 둘 다 프로브 스크립트의 같은 분기 구조라 위험도가
낮다고 판단했으나, 실행하지 않은 것은 실행하지 않은 것이다.

아래는 착수 당시의 원 항목이다(기록 보존용).

### 슬라이스 21 «포탈 빌드 되살리기» (사용자 요청, 2026-08-11)

**운영 방식이 정해졌다**: 이미지 빌드·배포가 필요할 때 **운영자가 DMS 노드 하나에
인터넷을 일시적으로 열어 준다**. 그 노드는 **컨트롤플레인(마스터) 노드** 중 하나로
한다. 포탈에서 그 노드를 빌드 노드로 지정하고, **빌드 착수 전에 인터넷 가능 여부를
먼저 확인**한 뒤 진행한다.

지금 막혀 있는 것(코드 좌표는 확인함):
1. **컨트롤플레인 노드는 빌드 노드로 지정할 수 없다.** `routes_control.py` 가
   `build_node_name` 을 `agent_nodes` 에 실재하는 노드로만 제한한다(422
   `unknown_build_node`). 에이전트는 DaemonSet 인데 컨트롤플레인 taint 때문에
   `dms-cp1` 에는 뜨지 않는다(현재 에이전트 5개 = 워커 5개). → 에이전트에 
   컨트롤플레인 toleration 을 주든, 빌드 노드 검증을 `agent_nodes` 밖으로 넓히든
   택일해야 한다. 전자는 노드 지표·마운트 프로브까지 따라오므로 부작용을 따져야 한다.
2. **egress 사전 확인이 없다.** 지금은 buildah 가 몇 분 돌다가 clone/npm 단계에서
   죽고 `build_failed` 만 남는다 — 원인이 "인터넷이 안 열렸다"인지 알 수 없다.
   짧은 프로브(예: 빌드 파드와 같은 노드·같은 이미지로 `curl -sS -m 5 <ref>` 1회)를
   빌드 제출 경로에 넣고 **고유 사유 코드**(예: `build_node_no_egress`)로 즉시
   거절해야 한다. 그래야 운영자가 "인터넷을 아직 안 열었다"를 바로 안다.
3. **빌드 파드가 컨트롤플레인에서 돌 수 있어야 한다** — `build_manifests.py` 의
   파드 스펙에 컨트롤플레인 toleration 이 필요하다.

배경: 이 테스트베드는 "pkg-01 만 인터넷, dms 노드는 ssh 만" 모델이라 포탈 빌드가
구조적으로 불가했고(§2.3), 슬라이스 18~20 의 이미지는 전부 pkg-01 에서 podman 으로
만들었다. 이 슬라이스는 그 우회를 없애는 것이 목표다.

### ✅ 슬라이스 20 «Volcano 대기 이력» — **완료**(2026-08-11, d32 배포·실증 통과)

설계 `specs/2026-08-10-dms-sched-wait-slice20-design.md`, 플랜
`plans/2026-08-10-dms-sched-wait-slice20.md`. 백엔드 1090 / 프론트 225 / tsc 0.

**원안(컨트롤러가 PodGroup 샘플링)을 기각**하고 스테퍼가 이미 매 틱 하는 vcjob
phase 관측을 썼다 — 추가 k8s 호출 0, **RBAC 변경 0**, 계약 테스트 개정 0. 측정
앵커는 전이 행이 아니라 `data_jobs.exec_submitted_at` 컬럼으로 확정했다: 전이 행
방식도 지금은 동작하지만 그 유일성이 세 모듈 교차 불변식에 얹혀 있어, 어느 쪽이
깨져도 sync/rm 값이 **조용히** 틀어진다(설계 §2.1 이 PodGroup 이름 유도를 기각한
것과 같은 실패 모드).

실증 결과(테스트베드, d32):
1. 배포 직후 `sched_wait_counted=0`, `sched_wait_excluded=55` — 백필이 없으므로
   과거 잡 55건이 전부 "기록 없음"으로 정직하게 표면화됐다(대조군 `submit_wait` 은
   백필이 있어 55건 전부 counted).
2. **실 scan 잡에서 `sched_wait_seconds=5`, 같은 잡의 `submit_wait_seconds=0`** —
   두 지표가 **서로 다른 것을 재고 있음**이 라이브에서 증명됐다. 제출 대기만 봤다면
   운영자는 "대기가 전혀 없었다"고 읽었을 잡이다. 이 슬라이스의 존재 이유다.
3. 집계 반영: `counted=1 → 2`, 히스토그램 `<10s` 버킷에 계상.
4. **Running 미도달 잡**(preflight 거부)은 `exec_submitted_at`·`sched_wait_seconds`
   둘 다 NULL — 앵커가 execution 제출 경로에서만 찍힌다.
5. 포탈: 「제출 대기 분포」와 「스케줄 대기(Volcano) 분포」가 나란히, 각각 집계/제외
   건수와 함께. 스케줄 대기 캡션이 근사 오차(스테퍼 틱 5초)를 그대로 적는다.

**라이브에서 새로 알게 된 것 — 해상도 바닥**: 관측된 두 잡이 **정확히 둘 다 5초**
였다. 스테퍼 틱이 5초라 첫 RUNNING 관측이 사실상 틱 해상도에 걸린다 — 슬라이스 17
의 제출 대기(52건 중 47건이 0초)와 **정반대 분포**다. 즉 이 값의 실질 해상도는
5초이고, 5초 미만의 실제 큐 대기는 구분되지 않는다. 0 초 기록은 vcjob 이 제출 직후
첫 폴링에서 이미 Running 일 때만 나오므로 실제로는 드물다(가드는 그래도 4계층 전부
있고 뮤테이션으로 이빨을 확인했다 — 값이 드물다는 것과 없어도 된다는 것은 다르다).
더 정밀한 값이 필요해지면 스테퍼 틱을 줄이는 것이 아니라 PodGroup
`status.conditions` 를 읽어야 하는데, 그건 슬라이스 17 §7 이 금지한 항목이다.

### 슬라이스 20 «Volcano 대기 이력» (슬라이스 17 파생) — 착수 당시 원 항목(기록 보존용)
- 끝난 잡의 스케줄링 대기가 사후에 없다 — PodGroup 이 잡 종료와 함께 삭제되기 때문.
- 백필 원천이 존재하지 않으므로 **과거 잡은 전부 NULL 이 맞다** — 지어내지 않고
  `excluded` 건수로 표면화한다.

---

## 2. 미분류 백로그 (테마별)

### 2.1 러너 / 실행
- **nsync 실증 재확인**: 파서는 커밋됐으나 `job5` 재빌드 + 실 nsync 잡으로
  `files/bytes` 채워짐 확인 필요.
- **vcjob 런처 파드 로그 열람 불가** — 슬라이스 5 비목표(`409 log_not_available`).
  실행 단계 진단이 10슬라이스째 아티팩트 전용.
- **dscan 총바이트 없음** — 리포트 스키마에 없다(크기 히스토그램뿐).
  `bytes_total`은 sync 전용으로 남는다(`slice15-design.md:121`).
- **소급 백필 없음**(슬라이스 15 이전 잡), **프리뷰 카운트 DB 미승격**,
  **파일별 상세·에러 카운트·전송률 없음**.
- `storages.managed_root = "/"` 허용 → `_abs`가 `//team/data` 생성 가능
  (phase3c `progress.md:24`, 검증 강화 미이행).
- `_abs()` 스토리지 결측 폴백이 로그를 안 남김(phase3c `:22`).
- 고아 복구 쿼리에 `LIMIT` 없음 — 크래시루프 대량시(phase3c `:29`).
- `tool_argv` 미지 도구가 `drm` 분기로 흘러감(phase3c `:6`) — 상류 enum 검증에만 의존.
  **파괴적 경로의 fail-open 형태**.
- `imagePullPolicy: IfNotPresent` + 태그 재사용 = 노드 캐시 stale(phase3c `:88`).
  고유 태그 관례로만 완화됨.

### 2.2 포탈
- 슬라이스 1 FAST-FOLLOW 7건(`.superpowers/sdd/2026-08-04-dms-portal-slice1/progress.md:41-43`,
  레저 소실 위험 있어 여기 옮김): `StatusPill`이 잡 상태 전용이라 스토리지 `Healthy`가
  중립색(설계 §5 "정상=초록" 위반); `api.ts` 401 분기 중복; `Login.tsx`의 무가드
  `as ApiError` 캐스트; `RequestDetail` 로딩 상태 없음; 공유 `useCancelJob`으로 취소
  오류 미표시·확인 오류가 다이얼로그 닫힘에 리셋 안 됨; `Home`이 `me.isError` 미확인;
  요청 무효화 2건 접두 중복.
- 미구현 기능면: 아티팩트 **다운로드**(바이너리 스트리밍)·삭제·보존 UI; 배치 **CSV
  업로드**(현재 프론트 파싱)·결과 내보내기·템플릿·rm 배치; 고급 sync 옵션
  (`chmod`/`chown`/`bufsize`/`batch_files`) 폼 노출; 배치 생성 폼의 스토리지 드롭다운;
  에이전트 설정 푸시; 알림/경보(슬라이스 14 비목표 — "대시보드는 표시만").
- Sparkline 유효점 1개면 bare `M` → 빈 SVG(슬라이스 14 파킹, 도달 가능·영향 경미).
- **의존성 권고(의도적 보류, `frontend/README.md`)**: react-router `GHSA-qwww-vcr4-c8h2`
  high — 현재 어떤 `react-router-dom` 버전도 두 취약 범위를 동시에 피하지 못함.
  재검토 조건: `react-router-dom@8.3.0` 이상 릴리스. vite/vitest 체인 critical 1건은
  dev 전용이며 semver-major 업그레이드 필요(슬라이스 12 §7 범위 밖).
  **`npm audit fix --force` 실행 금지.**

### 2.3 운영 / 배포
- **포탈 빌드(슬라이스 11)는 이 테스트베드에서 쓸 수 없다** — 빌드 파드가 빌드
  노드(=dms 워커) 위에서 돌면서 GitHub·npm·PyPI·dl.k8s.io 로 나가야 하는데, 테스트베드
  아키텍처가 "pkg-01 만 인터넷, dms 노드는 ssh 만"이다(`testbed/docs/ARCHITECTURE.md`
  §15). 실제로 이 클러스터의 유일한 포탈 빌드 기록이 `build_failed` 다. 실 빌드 경로는
  **pkg-01 에서 podman**(`deploy/docker/build-and-push.sh`)이며, 슬라이스 18 의 d29 도
  그렇게 만들었다. 포탈 빌드를 살리려면 빌드 노드를 pkg-01 로 둘 수 있어야 하는데
  pkg-01 은 어느 클러스터에도 join 하지 않으므로 에이전트가 없다 → `build_node_name`
  후보에 뜨지 않는다. **구조적 미해결**이다.
- **클러스터 내 registry 미구축** — 원본 설계 §246은 "클러스터 내 registry:2",
  실제는 호스트 `pkg-01:5000`(슬라이스 11 비목표: "독립된 인프라 작업").
- **`DMS_JOB_IMAGE`는 포탈 롤아웃 불가** — ConfigMap 갱신 + 소비자 재시작이라
  슬라이스 13 범위 밖. 여전히 `20-config.yaml` 수기 편집.
- **태그 동기화가 5파일 수기** — 템플릿 계층 없음(의도적, `deploy/README.md`).
  "하나라도 빠지면 그 컴포넌트만 옛 이미지로 돈다." **슬라이스 16 이후 완화**:
  라이브와 매니페스트가 갈라지면 대시보드 드리프트 배지가 표시하고,
  api/controller 는 initContainer 와 본 컨테이너 이미지가 다르면 계약 테스트가
  RED 다(그 divergence 는 배지가 못 잡는 유일한 구멍이라 테스트로 막았다).
  여전히 못 잡는 것: 매니페스트를 아예 안 고친 채 포탈 롤아웃만 한 상태는
  배지가 잡지만, 그걸 **무시하면** 다음 apply 가 되돌린다.
- 빌드 레이어 캐시 미보존·멀티아치 없음(슬라이스 11 비목표). 빌드 노드는 **인터넷
  egress 필요**(npm/dl.k8s.io/github/PyPI/Debian) — 격리망에서 실패.
- **롤백 버튼 없음**(의도적 — 이력에서 옛 태그 재선택).
- `/cephfs` hostPath `type: Directory` — 비-cephfs 노드가 스케줄 풀에 들어오면 파드
  admission 실패.
- `DMS_LDAP_BIND_DN`/`_PW` 공란(익명 바인드) 미해결.
- 레지스트리 태그 검증 fail-open — 레지스트리 다운 시 `unknown_tag` 통과 →
  `ImagePullBackOff`(`deploy/README.md` §9-7).
- pod GC 86400s가 **프리플라이트 파드 로그(유일한 진단 사본)** 를 파괴
  (슬라이스 10 Important 2).
- DaemonSet 롤아웃 **600s 타임아웃 미측정** — 5노드 순차가 넘기면 거짓 실패
  (슬라이스 13 플랜 알려진 위험).
- Prometheus/Grafana 배포는 **의도적 제외**(포탈 대시보드로 대체).

### 2.4 데이터 모델
- `runs` 테이블 死物 — 슬라이스 17이 **부활을 명시적으로 배제**하고 파생 컬럼을
  택했다. 테이블은 여전히 읽기·쓰기 0건으로 남아 있다(제거 여부는 미결).
- ~~**`data_jobs.created_at` 인덱스 없음**~~ — 슬라이스 17이 커버링 인덱스
  `idx_data_jobs_created (created_at, submit_wait_seconds)` 를 추가해 해소됨.
- `by_storage`가 `COALESCE(storage_name, destination_storage)` — sync를 **도착지 기준**
  으로 센다. 설계가 기준을 명시하지 않은 침묵의 해석.
- KPI 의미 변화(요청 50건 즉석 계산 → 창 내 잡 집계) — 옛 화면과 숫자가 다르다.

### 2.5 테스트 / CI
- **e2e 전무**(Playwright 없음) — 15슬라이스째 단위 테스트 + 수기 실증.
- `KubernetesClient`가 `# pragma: no cover` — 슬라이스 14가 추가한 `threading.Lock`
  이중검사가 무테스트 코드.
- 마이그레이션 **ALTER 경로 일반 회귀 커버리지 갭** — 슬라이스 14가 파킹했고 그
  파킹 항목이 실제 프로덕션 500을 냈다. 슬라이스 15가 `_widen_count_columns`만 보강.
- 슬라이스 15 잔여: `text or ""` 반쪽 약속 무검증; `test_execution_volcano` 픽스처가
  구식 summary 모양; `information_schema` 쿼리에 `table_schema` 미필터(단일 스키마
  배포에서만 안전).
- 슬라이스 14 잔여: Sparkline NaN/Infinity 무검증; `by_state:null` 테스트가
  `Array.isArray`와 `?? []`를 구분 못함.
- 슬라이스 9 Task 6은 진짜 RED 단계가 없었음(구현자 자진 신고).
- 슬라이스 1~4 테스트 부채 다수(`jobState.test`의 `PreviewExpired`/`Planning`/
  `Scheduled` 누락, `idx_requests_batch` 미단언, `BatchDetail` 확인-POST 단언이
  `waitFor` 밖이라 플레이키 위험, `PolicyDialog` tool 필드 `aria-label` 없음,
  `useDenylist` URL 미인코딩 등).

### 2.6 프로세스
- **SDD 레저는 git에 없다**(`.superpowers/sdd/.gitignore` = `*`). 슬라이스 11·12·13
  레저 소실. → 앞으로 파킹은 이 파일에 기록.
- **한 워크트리에서 둘이 동시에 커밋하면 인덱스가 섞인다**(2026-08-11 실제 발생).
  슬라이스 20 Task 2 가 `git add` 로 5개 파일을 스테이징한 사이, 같은 워크트리의 다른
  세션이 `git commit` 을 쳐서 그 5개가 남의 커밋 `6bc2ecb`(원래는 AppShell 수정 1건)
  안으로 통째로 들어갔다. **코드 손실은 없고** 히스토리만 섞였다 — 미푸시였지만 뒤에
  5개 커밋이 쌓여 재작성은 위험 대비 이득이 없어 그대로 뒀다. 그래서 `6bc2ecb` 의
  메시지는 그 안의 백엔드 변경(`mark_exec_submitted`, 스테퍼 훅, 테스트 3개)을 말하지
  않는다 — **이 항목이 그 기록이다**.
  **관례**: 워크트리를 공유하는 동안에는 `git add` 로 인덱스를 거치지 말고
  `git commit -- <경로들>`(pathspec)로 커밋한다. 인덱스를 안 쓰면 남의 파일을 삼키지도
  내 파일이 삼켜지지도 않는다.
- **슬라이스 13 실증 체크리스트가 전부 미체크**(`deploy/README.md` §9, 8개 `[ ]`).
  레저도 없어 컨트롤러 자기 갱신 수렴(핵심 실증)이 시연됐는지 확인 불가.
- **포탈 빌드는 GitHub에 푸시된 커밋만 본다**(`--depth 1 --branch`) — 로컬 전용
  커밋은 빌드 불가. 미푸시 상태와 겹치면 이 브랜치 자체를 빌드할 수 없다.

---

## 3. 규약

- 파킹할 항목은 **이 파일**에 적는다(레저는 워크트리와 함께 사라진다).
- 각 항목은 근거를 `파일:줄`로 남긴다.
- 의도적 보류(회원가입 메일 인증, Prometheus, 롤백 버튼, 템플릿 계층, cron 예약)는
  "미구현"이 아니라 **결정**이므로 그렇게 표시한다.
