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

갱신: 2026-08-11. 기준: 슬라이스 18~22 가 `origin/main` 에 병합됨(fast-forward,
`beed7b2`). 로컬 `main` 체크아웃은 `git pull` 로 따라와야 한다 — 병합을 워크트리에서
`git push origin HEAD:main` 으로 했기 때문이다(공유 체크아웃의 브랜치는 그 세션에서
직접 갱신할 수 없다).

---

## 0. 현재 상태

- 슬라이스 1~24 완료(25·26 은 설계만 있고 미착수).
- 테스트베드 이미지: 제어면 `dms:d35`, 에이전트 `dms-agent:d35`,
  잡 러너 `dms-mpifileutils:d35` — 슬라이스 24 에서 **세 이미지의 태그가 처음으로
  일치**한다(층3 러너가 잡 이미지에 살아서 d27 로 방치할 수 없었다). 태그 체계를 dNN/jobNN 분리에서 단일 dNN 으로
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

### 슬라이스 22 후보 «SSH 의존 점검·완화» — **실증으로 대부분 해소됨(2026-08-11)**

> **결론 먼저**: Cilium 기본 구성(`routing-mode = tunnel`, `tunnel-protocol = vxlan`,
> 라이브 cilium-config 실측)에서는 **노드 간 22 번 차단이 MPI 를 깨지 않는다.**
> 테스트베드에서 직접 재현했다: w1·w2 에 `FORWARD -p tcp --dport 22 -j REJECT` 를 건
> 상태로 w2 파드 → w1 파드 22 번이 **CONNECTED**, 같은 규칙 아래 대조군(파드→외부 22)은
> **BLOCKED**. 즉 규칙이 무효했던 게 아니라 **VXLAN 캡슐화가 안쪽 22 를 가린 것**이다.
> → 아래 할 일 2·3(포트 이동·런처 대체)은 **프로덕션이 native routing 일 때만** 필요하다.
> 프로덕션에서 확인할 한 줄:
> `kubectl -n kube-system get cm cilium-config -o jsonpath='{.data.routing-mode}'`
> `tunnel` 이면 끝. `native` 면 그때 2 부터 착수한다.
>
> **남는 것은 설치 시점 SSH 하나뿐이다**(할 일 4) — 사용자 판단으로 **나중에 다룬다**.



**계기**: 사용자 프로덕션 클러스터는 노드 간 네트워크는 되지만 **SSH 가 제한**된다.

**조사 결과(2026-08-11, 코드 전수)**:
- **DMS 런타임에 노드↔노드 SSH 는 없다.** `paramiko` 0건, 노드 호스트명 접속 0건.
  legacy DMS 는 노드 root SSH 를 썼지만(`testbed/docs/ARCHITECTURE.md` §15 의
  `ssh-host-exec`) **clean-slate 구현은 그 의존이 없다.**
- **SSH 는 정확히 한 곳, MPI rank 기동에 파드↔파드로만 쓰인다.**
  vcjob 이 `plugins: {"ssh": [], "svc": []}` 선언(`execution_manifests.py:359,399`)
  → Volcano 가 파드에 키쌍 물질화 + task 별 hostfile 제공. **워커 파드가 컨테이너 안에서
  `sshd -D`** 를 띄우고(`deploy/docker/Dockerfile.mpifileutils:121`), 런처가
  `OMPI_MCA_plm_rsh_agent="ssh -o StrictHostKeyChecking=no …"`
  (`dms_job_runner/commands.py:20-22`)로 **워커 파드 호스트명**에 접속한다
  (`runner.py:165` — `/etc/volcano/<task>.host`). 대상은 전부 파드이지 노드가 아니다.
- **그래도 프로덕션에서 깨질 수 있다**: 파드↔파드 SSH 도 물리적으로는 노드 사이를
  지난다. **CNI 데이터패스에 달렸다** — 오버레이/캡슐화(VXLAN·Geneve·IPIP)면 안쪽 22 가
  터널에 감싸여 무관하지만, **네이티브 라우팅이면 노드 간 22 차단이 MPI 를 깬다.**

**할 일**:
1. **프로덕션 CNI 캡슐화 여부 확인**(사용자 환경 정보 필요) — 이게 갈림길이다.
2. 막힌다면 **파드 내 sshd 포트를 22 밖으로 이동**: `Dockerfile.mpifileutils` 의
   sshd_config `Port`, `commands.py` 의 `plm_rsh_agent` 에 `-p <port>` — 포트 기반
   제한이면 이걸로 끝난다. 가장 싼 해법.
3. 그래도 안 되면 SSH 런처 자체를 대체(PMIx/PRRTE 등) — 범위가 크고 mpifileutils
   빌드까지 얽히므로 2 가 실패한 뒤에만 검토한다.
4. **설치 시점 SSH 도 정리 대상**: `deploy/docker/registry-setup.sh` 가 노드에 SSH 해
   `/etc/containers/registries.conf.d/` 를 쓴다. 런타임은 아니지만 SSH 제한 환경에서는
   설치가 막히므로 Ansible/DaemonSet/운영자 수기 중 하나로 대체 경로를 문서화한다.

### 슬라이스 21 잔여 (다음 작업 리스트로)

1. ✅ **미실행 실증 2건 — 완료(2026-08-11)**. 둘 다 **45초** 만에 각자의 사유 코드로
   실패했고 로그에 실측값이 남았다: `build_node_disk_low`
   (`avail_bytes=17918570496 need_bytes=18957493248`, 노드에 3.5GB 파일을 만들어 공식
   아래로 내림), `build_registry_unreachable`(`unreachable_registry=pkg-01:5000`,
   `iptables -I FORWARD -p tcp --dport 5000 -j REJECT`). **세 사유 코드(egress·disk·
   registry)가 각각 구분되어** 나오는 것을 확인했다. 규칙 제거 후 대조 빌드가
   `pushed pkg-01:5000/dms:b2d3749d6` 로 성공해 원상 복구도 확인했다.
   재현 시 주의(실증에서 헛짚고 배운 것): **파드 egress 는 `FORWARD` 로 막아야 한다.**
   `OUTPUT` 은 노드 자신이 만든 트래픽만 걸리므로 파드는 그대로 나간다.
   덤으로 `build_node_report_stale` 도 의도치 않게 실증됐다 — API 가 90분간 불능이던
   동안 에이전트 리포트가 수집되지 못해 전 노드가 stale 이 됐고 제출이 422 로 거절됐다
   (§1 슬라이스 22 후보 참고).
2. **`build_failed` 세분화** — OOMKilled(memory limit 1Gi)와 sizeLimit 축출이 파드
   phase Failed 로 접혀 전부 `build_failed` 가 된다(설계 §4 가 한계로 명시). 로그가
   급단절된 build_failed 를 만나면 운영자가 OOM/축출을 의심해야 하는 상태 — 파드
   `status.containerStatuses[].state.terminated.reason` 을 읽어 구분하면 된다.
3. **리소스 봉투의 설정화** — 지금은 상수다(`build_manifests.py`). 실증에서 emptyDir
   피크 1.2G·memory 1Gi 통과가 확인됐으므로 당장 급하지 않지만, 노드 사양이 다른
   환경에서는 env 튜너블이 필요해진다.
4. **빌더 이미지 미러 갱신 절차의 자동화** — 지금은 `20-config.yaml` 주석의 수기 3줄
   (pkg-01 에서 pull/tag/push)이다. 미러가 낡으면 buildah 버전이 고정된다.
5. **pkg-01 podman 우회 삭제** — 포탈 빌드가 성공하므로 `deploy/README.md` §1 의 수기
   빌드 경로를 "비상용"으로 격하하고 §8 의 "구조적 불가" 경고를 걷어낸다. (슬라이스 21
   이 §8 경고를 아직 안 걷었다 — 실증 통과 뒤 정리하기로 했던 항목이다.)
6. **빌드 동시 2개 허용** — 지금은 `api-replicas=1` 전제의 단일 활성 빌드 가드다.

---

### ✅ 슬라이스 22 «DB 커넥션 재연결» — **구현 완료·부분 실증**(2026-08-11, d34)

설계 `specs/2026-08-11-dms-db-reconnect-slice22-design.md`, 플랜
`plans/2026-08-11-dms-db-reconnect-slice22.md`. 백엔드 1164 / 프론트 228 / tsc 0.

**통과한 실증(핵심)**: pkg-01 에서 `pg_terminate_backend` 로 dmsdb 커넥션 **3개를 전부
강제 종료**했는데 — 즉 사건과 같은 상황을 만들었는데 — **첫 요청이 200** 이었고
`reconnects: 1`, `last_reconnect_at` 이 찍혔다. **API·컨트롤러 둘 다 RESTARTS=0**
(사건 당시 컨트롤러는 크래시로 재시작했다). events 에 `db_reconnected` 2건이
남았다(api 13:43:32 / controller 13:43:31) — 양쪽 배선도 확인됐다.

**🔴 실증이 이 슬라이스 자체의 결함을 찾았다 — 자기 종료가 발화하지 않는다.**
API 파드 노드에서 5432 를 차단해 "재연결조차 실패하는" 상황을 만들었더니, 파드
이벤트가 `Readiness probe failed: context deadline exceeded (x35 over 11m)` 였다 —
**`/readyz` 가 503 을 낸 게 아니라 응답 자체를 못 했다.** 원인 사슬:
1. `Database` 는 단일 커넥션 + RLock 이라 모든 쿼리가 직렬화된다.
2. 재연결의 `connect()` 에 **연결 타임아웃이 없다**(psycopg 가 내부적으로 재시도한다 —
   로그의 `raise last_ex.with_traceback(None)` 가 그 흔적).
3. 그래서 readyz 핸들러가 락을 오래 쥔 채 매달리고, 프로브가 타임아웃한다.
4. **연속 실패 카운터는 `except` 분기에서만 증가**하는데 거기 도달하지 못하므로
   카운터가 안 늘고 **자기 종료가 영원히 발화하지 않는다.**

즉 재연결(§2.2)은 실증됐지만 **"재연결까지 실패"를 다루려던 §2.4 가 정확히 그
상황에서 무력하다.** 설계가 스스로 적은 "RLock 안에서 대기하면 API 전체가 멈춘다"가
재연결 경로에서 실현된 것이다.

**✅ 후속 조치 완료(2026-08-11)**: `db.py` 에 `DB_CONNECT_TIMEOUT_SECONDS = 5` 를
두고 `_open()` 의 psycopg 분기가 `connect_timeout` kwarg 로 넘긴다. 5 를 고른 이유는
**프로브 주기(10s)보다 짧아야 매 프로브가 반드시 503 으로 끝나기** 때문이다 — 이
값을 10 이상으로 올리면 위 사슬이 그대로 되살아난다(코드 주석에 박아 뒀다).
URL 이 이미 `connect_timeout=` 을 지정했으면 우리 기본값을 얹지 않는다(libpq 는
kwargs 를 URL 파라미터보다 우선하므로, 안 걸러내면 운영자 명시값이 조용히 무시된다).
테스트 2건 신설(`tests/test_db_reconnect.py`): 기본값 전달 / URL 명시 시 미덮어씀.
뮤테이션으로 이빨 확인(가드를 `if True:` 로 바꾸면 후자가 빨개진다).

**남은 것**: **라이브 발화 재실증** — 5432 를 다시 차단해 `/readyz` 가 (타임아웃이
아니라) 503 을 내는지, 카운터가 30 에 닿아 자기 종료가 실제로 발화하는지 확인.
단위 테스트는 카운터·리셋·비활성을 이미 고정했으므로 남은 건 라이브 확인뿐이다.

아래는 착수 당시의 원 항목이다(기록 보존용).

### 🔴 슬라이스 22 후보 «DB 커넥션 재연결» — **최우선(프로덕션 영향)**

**2026-08-11 라이브에서 실제로 발생했다.** API 파드가 90분간 `0/1 Running` 으로
방치돼 있었다. 포탈·API 전면 불능이었고 **아무도 몰랐다**.

**원인**: `src/dms/db.py` 의 `Database` 는 **단일 커넥션**(`self._conn`)을 들고
**재연결 로직이 전혀 없다** — `OperationalError`/`InterfaceError` 처리도, `closed`
검사도, 재시도도 0건이다(`db.py:32,57-80` 전수 확인). 커넥션이 한 번 끊기면 이후
모든 쿼리가 영구히 실패한다.

**왜 자동 복구가 안 되는가(이게 진짜 문제다)**:
- `/readyz` 는 정직하게 503 을 낸다(`api/app.py:51-60` — DB 에 SELECT 1). 좋은 설계다.
- 그런데 `/healthz`(liveness)는 DB 를 안 보고 **200 을 낸다** → **kubelet 이 파드를
  재시작하지 않는다.**
- readiness 503 → Service 에서 빠짐 → **API 가 죽지도 살지도 않은 채 무한정 방치**된다.
- 컨트롤러는 같은 사건에서 크래시해 재시작으로 살아났다(재시작 1회 기록) — 즉 **API 만
  이 함정에 빠진다.** 죽는 편이 나았던 셈이다.

**실측 확인**: 파드에서 DB 로 TCP 는 정상(`10.10.10.30:5432` connect OK)인데
`SELECT 1` 만 실패 — 네트워크가 아니라 **커넥션 객체가 죽은 것**이다. 파드를 지우니
즉시 복구됐다.

**할 일**:
1. `Database` 에 **재연결**을 넣는다 — 쿼리 실패 시 커넥션 상태를 보고 1회 재연결 후
   재시도. 단일 커넥션 + RLock 구조라 재연결 지점이 한 곳이라는 게 그나마 다행이다.
   트랜잭션 중간 실패는 재시도하면 안 된다(부분 적용) — 그 경계를 설계에서 정할 것.
2. **liveness 를 DB 에 묶을지 결정**한다. 묶으면 DB 가 잠깐 흔들릴 때 전 파드가 재시작
   루프에 빠질 수 있고, 안 묶으면 이번처럼 영구 방치된다. 절충안: liveness 는 그대로
   두되 **연속 N 회 readiness 실패가 지속되면 자기 종료**(고전적 패턴).
3. 이번 사건이 **왜 커넥션을 끊었는지**는 미확인이다 — 조사 중 iptables 실험을 한
   시각과 겹치지만 5432 를 막은 적은 없다. 원인 불명이라는 사실 자체를 적어 둔다.
   재연결이 있으면 원인과 무관하게 복구된다는 것이 이 항목의 요지다.

---

### ✅ 슬라이스 23 «포탈 e2e 테스트» — **완료·e2e 9건 통과**(2026-08-12, 클러스터 무관)

설계 `specs/2026-08-11-dms-portal-e2e-slice23-design.md`, 플랜
`plans/2026-08-11-dms-portal-e2e-slice23.md`. **15슬라이스째 e2e 0건의 종결.**
백엔드 1189 / 프론트 228·49 / tsc 0 / **e2e 9 passed (24.2s)**.

**앱 코드 변경 0 이 계약이었고 지켰다** — `git diff 70561a8..HEAD -- src frontend/src
deploy/k8s` 가 **빈 출력**이다. data-testid 도 달지 않았다. e2e 가 앱을 바꾸기 시작하면
"실제로 배포되는 것을 검사한다"는 전제가 무너지기 때문이다. 유일한 예외는
`vite.config.ts` 의 vitest include 잠금 4줄(e2e 스펙이 vitest 에 빨려드는 것을 막는다).
새 의존성은 승인된 `@playwright/test` 1건뿐이고 **브라우저 다운로드도 0**(시스템 크롬
147, `channel:"chrome"`).

**이빨 검증 — 이 슬라이스의 존재 증명.** 두 결함을 각각 재주입해 두 계층을 비교했다:

| 재주입한 결함 | 단위 228건 | e2e |
|---|---|---|
| 사이드바 밀림(6bc2ecb 이전: `md:shrink-0`·`min-w-0` 제거) | **전부 초록** | `[L1] /admin/accounts: 표가 자기 컨테이너 안에서 스크롤하지 못하고 문서 전체가 넘쳤다(scrollWidth=1567 > clientWidth=1280)` |
| 계정 표 뭉개짐(9fbef86 이전: `td` 자체를 flex) | **전부 초록** | `[L2] /admin/accounts: computed display 가 table-cell 이 아닌 셀 4개(기대 0개)` + 어느 셀인지 전문 |

즉 **라이브에서만 드러나던 결함 유형이 이제 로컬에서 잡힌다.** 원복 후 9건 재통과.

**🔎 e2e 가 만들자마자 실물 결함 1건을 찾았다** — `StoragesList.tsx:50` 이 9fbef86 이
계정 표에서 걷어낸 것과 같은 구조로 남아 있다(§2.2 에 항목 등록). 앱 무변경 계약이라
고치지 못하고 `knownNonTableCells: 1` 로 정확한 개수를 못박았다.

**시나리오 6개(E1~E6)**: 부팅+세션 / SPA fallback 딥링크 / 레이아웃 불변식 L1~L4 순회
(1280×800 + 375×667) / 잡 종단 흐름(UI 제출→리로드 없이 Succeeded) / 목록 폴링 수렴 /
상세 잡 폴링 종단 중지. 실행은 로컬 풀스택(tmp sqlite + migrate + api **dist 서빙** +
controller 1s + agent --once) — **클러스터 불요**. dev 서버가 아니라 dist 를 서빙하는
이유는 vite dev 의 자체 fallback 이 `spa_fallback` 코드를 가려 그 회귀를 영원히 못 잡기
때문이다.

**구현 중 에이전트가 플랜을 고친 것들(전부 "검사하는 척"을 막는 강화)**:
- 플랜의 전제 단언이 사이드바 결함에 **정반대 수리를 지시**했다 — `min-w-0` 이 없으면
  래퍼의 `overflow-x-auto` 가 발동조차 못 해 래퍼는 안 넘치고 문서가 넘치는데, 플랜대로면
  `E2E_SEED_TOO_NARROW`(= "시드를 늘려라")로 보고된다. 문서 오버플로를 먼저 갈라 `[L1]`
  로 보고하도록 고쳤다.
- E6 의 `getByText("Succeeded").first()` 는 폴링 없는 **요청 카드** 배지에 걸려 잡 쿼리에
  대해 아무것도 증명하지 못했고, 잡 배열이 비어도 통과했다(§1-11 의 빈 배열 함정).
  잡 ID 가시성 + API 에서 유도한 개수 단언으로 교체.
- `window.__dmsE2eNoReload` 표식으로 "새로고침 없이"를 주석 규율이 아니라 **실행 시점에**
  강제(누가 `page.reload()` 를 끼워 넣으면 조용히 무의미해지는 대신 빨개진다).
- 하네스: 컨트롤러 spawn 을 시드 **뒤로** 옮겼다 — 기동 첫 틱의 리스 획득 버스트가 sqlite
  쓰기 락과 경합해 6회 중 1회 `POST /storages` 500 을 실제로 냈다(운영은 PG 라 하네스 한정).
- `forbidOnly: true` — CI 가 없어 수기 실행이 유일한 게이트인데 남겨진 `test.only` 하나면
  스위트가 1건으로 줄고도 exit 0 이다.

**정직한 한계**: CI 는 없다(설계 §7). 이 게이트는 **수기**이고 `deploy/README` 의
"이미지 빌드 전" 단계로 명문화했을 뿐, 기술적 강제 수단은 없다 — 숨기지 않는다.

---

### ✅ 슬라이스 24 «파괴적 경로 fail-open 봉인» — **완료·실증 5/5**(2026-08-12, d35)

설계 `specs/2026-08-11-dms-destructive-failopen-slice24-design.md`, 플랜
`plans/2026-08-11-dms-destructive-failopen-slice24.md`.
백엔드 **1189 passed**(기준선 1166 +23) / 프론트 228 / tsc 0. §2.1 의 4건을 닫았다.
이미지는 제어면·에이전트·**잡 러너까지** d35 (층3 이 잡 이미지에 살기 때문 —
`DMS_JOB_IMAGE` 가 d27 로 뒤처져 있었다).

**실증(전부 실 클러스터, 되돌릴 수 있는 조작만)**

1. **§6-1 파괴적 정상 경로 무회귀** — 전용 드릴 디렉터리
   `/cephfs/dms/slice24-rm-drill`(f1·f2·sub/f3, 소유 10003:10000)에 rm 잡을
   preview→confirm→실행. **Succeeded**, `{"files": 5, "returncode": 0}`.
   드릴 디렉터리는 사라졌고 **형제 9개는 전부 무손상**. 층1~3 을 모두 무변경
   통과함을 파괴적 연산으로 직접 확인했다.
2. **§6-2 `"/"` 등록 거부** — `{mount "/", root "/"}` 와 `{mount "/cephfs", root "/"}`
   둘 다 **422 `invalid_storage`**. 같은 요청에서 정상 조합은 **201** (무회귀).
3. **§6-3 층1 — 이번 슬라이스의 핵심 증거.** Pending 잡 2건(대조군 `dscan` +
   변조 `dwalk`)을 만들고 drain 해제. 변조 잡은 **Pending → Rejected
   `unknown_tool`**(중간 상태 없음), **pod/vcjob 0건** — 제출 자체가 막혔다.
   같은 틱에 대조군은 **Succeeded, 453 파일 스캔** — 정상 경로는 끝까지 정상이다.
   대조군이 있어서 "막혔다"와 "그냥 안 돌았다"가 구분된다.
4. **§6-4 층3 단독** — d35 잡 이미지 파드에서 `DMS_JR_TOOL=sh` 로 러너만 실행:
   `rc=1`, stderr `DMS_JR_UNKNOWN_TOOL tool='sh' allowed=('dscan','dsync','nsync','drm')`,
   `summary.json == {"returncode": 1, "files": null, "bytes": null}`(3키 계약, 모름은
   null). mpirun/ssh 시도 흔적 0 — 부작용 이전에 끊겼다.
5. **§6-5 고아 복구** — 3건 재현 → **한 틱에 전부 복구**(`orphan_recovery` 전이 3건),
   재스윕 **0건**.

**🔎 실증이 설계 전제 2건을 정정했다(§1-10 관련):**
- 설계는 "`record_result` 가 무조건 INSERT 라 **results 중복 삽입**이 가능하다 —
  복구가 이력을 오염시킨다"고 적었다. **틀렸다.** `results.request_id` 는
  **PRIMARY KEY**(`migrations.py:117`)라 중복은 구조적으로 불가능하고, 실제로는
  `UniqueViolation` 으로 **시끄럽게** 실패한다. 조용한 오염 위험은 없었다.
  따라서 플랜 §6-5(d) 의 "중복 results 원복 DELETE" 절차도 불필요했다 — 실측
  결과 request 당 results 는 정확히 1행이었다.
- 그 대신 **행 단위 격리(§2.3)가 라이브에서 실제로 발화했다**: 위 재현이
  (요청을 되돌리는 방식 탓에 results 행이 이미 있어) 독 행 3개를 만들었고,
  세 행이 **각각 독립적으로** 실패해 `orphan_recovery_failed` 이벤트 3건을 남겼으며
  **서로를 막지 않았다**. 플랜은 이 경로를 "독 행 없인 발화하지 않으니 단위 테스트
  몫"이라고 정직하게 적었는데, 재현이 우연히 진짜 독 행을 만들어 **프로덕션에서
  증명**됐다. 구 코드였다면 첫 예외가 나머지 전부를 다음 틱으로 밀었을 자리다.
- 부수 관찰: `finalize_from_job` 은 원자적이지 않다 — 상태 전이는 커밋되고 그
  뒤 `record_result` 가 터졌다(그래서 재스윕은 0인데 이벤트는 3건). 실 고아
  (finalize 가 아예 안 돈 경우)엔 results 행이 없어 이 경로가 안 생기지만,
  "부분 적용된 finalize" 자체는 남는 관찰이다 → §2.1 에 항목으로 남긴다.

**구현 중 에이전트가 잡은 플랜 결함 3건**(전부 고쳐서 반영):
- **가장 중요**: 플랜의 `posixpath.join(root, rel)` 은 `rel` 이 절대경로면 root 를
  **통째로 버린다**(`join("/cephfs/dms", "/etc") == "/etc"`). 기존 f-string 은
  `"/cephfs/dms//etc"` 로 **안에 가두고 있었다** — `//` 를 없애는 수정이 그 봉쇄까지
  걷어내면 fail-open 하나를 닫으면서 **더 나쁜 것(drm 이 managed_root 밖을 삭제)을
  연다**. `rel.lstrip("/")` 로 구현하고 회귀 테스트
  (`test_absolute_target_in_db_cannot_escape_managed_root`)를 추가했다.
- 플랜이 지정한 storages 뮤테이션이 **살아남았다**(일반 경로 규칙이 이미 `"/"` 를
  잡으므로 명시 분기는 이빨이 0이었다) → `detail == "root filesystem is not a storage"`
  까지 고정하는 테스트를 추가해 명시 분기 자체를 계약으로 걸었다.
- 플랜이 **기존 테스트 1건을 놓쳤다** — `test_run_job_unknown_tool_summary_is_nulls`
  가 미지 도구의 `rc == 0`(= 이 슬라이스가 닫는 fail-open)을 고정하고 있었다.
  삭제 대신 `_build_summary` 순수 함수 층으로 **재조준**해 회귀 그물을 보존했다.

**남은 것**: 없음(설계 §7 의 비목표는 의도적 제외). 잔여 창은 §2.4 의
check-then-act 비원자성 — `_abs` fail-closed 가 최종 방어라는 것을 코드 주석에
명시했다.

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
- ✅ **[슬라이스 24 에서 4건 전부 닫힘]** — 아래 §2.1-완료 참조.
  - ~~`storages.managed_root = "/"` 허용 → `_abs`가 `//team/data` 생성 가능~~
  - ~~`_abs()` 스토리지 결측 폴백이 로그를 안 남김~~
  - ~~고아 복구 쿼리에 `LIMIT` 없음~~
  - ~~`tool_argv` 미지 도구가 `drm` 분기로 흘러감~~
- `imagePullPolicy: IfNotPresent` + 태그 재사용 = 노드 캐시 stale(phase3c `:88`).
  고유 태그 관례로만 완화됨.
- **`finalize_from_job` 이 원자적이지 않다**(슬라이스 24 실증에서 관찰). 상태 전이는
  커밋되고 그 뒤 `record_result` 가 터질 수 있다 — 그러면 요청은 종단인데 results
  행이 없다. 실 고아 경로(finalize 가 아예 안 돈 경우)엔 results 행이 없어 이 창이
  안 생기지만, 두 쓰기를 한 트랜잭션으로 묶으면 구조적으로 닫힌다. 지금은 행 단위
  격리(슬라이스 24 §2.3)가 이걸 이벤트로 표면화하므로 조용하지는 않다.

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
- 🔴 **`StoragesList.tsx:50` 의 `<td className="flex gap-2 py-2">`** — 9fbef86 이 계정
  표에서 걷어낸 것과 **같은 구조의 미수정 결함**이다(td 자체가 flex 면 표 레이아웃
  계산에서 빠진다). **슬라이스 23 e2e 가 첫 클린 런에서 스스로 찾았다** — 만들자마자
  실물 결함 1건을 잡은 셈이고, 단위 228건은 지금도 이걸 못 본다. 슬라이스 23 은 앱
  코드 무변경이 계약이라 고치지 못하고 `knownNonTableCells: 1` 로 **정확한 개수**를
  못박아 뒀다(`e2e/03-layout.spec.ts:45`) — 2가 되면 새 회귀로, **0이 되면 "수리됐으니
  이 줄을 지우라"고** 빨개진다. 고칠 때 그 인자도 함께 지울 것.
- 🔴 **로그아웃이 URL 을 안 바꾼다** — 슬라이스 23 E1 이 실측했다: 로그아웃 후
  `/admin/dashboard` 에 30초간 그대로 남는다. `useLogout` 의 `onSettled: qc.clear()` 가
  `me` 쿼리를 제거하는데, 제거된 쿼리의 관찰자는 마지막 결과를 그대로 들고 재조회가
  안 걸려 `RequireRole` 이 401 을 볼 기회가 없다. 세션 자체는 정상 파기된다(하드
  내비게이션하면 재차단되고 쿠키도 사라진다 — e2e 가 그것만 단언한다). 즉 보안
  결함은 아니고 **UX 결함**이다. 앱 코드 무변경 계약 때문에 슬라이스 23 이 고치지
  않았고, 없는 동작을 계약으로 굳히지도 않았다.
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
