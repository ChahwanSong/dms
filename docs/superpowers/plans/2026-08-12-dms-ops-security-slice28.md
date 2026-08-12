# 슬라이스 28 — 운영·보안 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** BACKLOG §2.3 의 세 항목을 각자 정직한 크기로 닫는다. **항목 1 — 레지스트리 태그 검증 fail-open 비침묵화(코드)**: `submit_releases` 는 레지스트리가 침묵하면 태그 존재 검증을 건너뛰고 202 를 준다 — 이것은 그 자리 주석이 명시한 **의도적 tradeoff**("잘못된 차단보다 낫다", 설계 §7)라 **뒤집지 않는다(fail-closed 금지)**. 고치는 것은 침묵이다: 검증이 건너뛰어진 제출임을 (a) 응답 플래그 `tag_verified: false` 로 즉시, (b) `release_tag_unverified` 이벤트(`record_event`)로 영속되게 알리고, (c) 포탈 릴리스 화면이 배너로 보여준다. 실측상 이벤트의 포탈 노출 경로는 요청 상세뿐이라(request_id 기반) **응답 플래그+배너가 유일한 가시 채널**이고 이벤트는 DB 영속 흔적이다 — 셋 다 한다. **항목 2 — DaemonSet 600s 타임아웃(순수 실증, 코드 0)**: `DMS_ROLLOUT_TIMEOUT_SECONDS` 는 이미 설정 키다(`config.py:55` + `20-config.yaml`) — 상수 빼기는 할 일이 없다. 게다가 실측 결과 **백로그의 전제 자체가 낡았다**: I6(정체 기준 시계) 이후 600s 는 "5노드 총 수렴 상한"이 아니라 **노드-단위 정체 상한**이다(`_note_daemonset_progress` 가 진행 틱마다 `applied_at` 을 재장전). 남는 일은 라이브 측정 한 번(에이전트 롤아웃의 노드당 교체 시간 실측)과 전제 정정 기록뿐 — 「플랜 이후: 배포·실증」으로 보낸다. **항목 3 — LDAP 인증 바인드(코드 강화 + 자격증명 블록 보고)**: 인증 바인드는 `identity_ldap.py:51-54` 가 **이미 완전 지원**한다 — 미해결의 실체는 테스트베드에 바인드 자격증명이 없어 익명으로 돈다는 것뿐이고, 그 전환은 이 세션에 없는 자격증명이 막는다. 자격증명 없이 가능한 강화만 코드로 한다: `DMS_LDAP_REQUIRE_AUTH_BIND` 옵션 — 켰는데 bind DN/PW 가 비거나 자리표시자면 **기동 거부(SettingsError, fail-closed)** 해서 "인증 바인드를 의도했는데 익명으로 돌고 있다"를 구조적으로 불가능하게 만든다. 새 pip/npm 의존성 0, 새 테이블 0, 새 컬럼 0(스키마 무변경 — migrate 재실행 불요), 새 사유 코드는 프론트 전용 1건(`tag_unverified`), 새 설정 키 1건(config.py + 20-config.yaml 양쪽).

**Architecture:** 항목 1 은 `routes_releases.py` 의 제출 루프가 침묵 리포를 `unverified` 리스트로 추적한다 — 기존 `if tags is not None and tag not in tags` 를 `if tags is None: 추적 / elif tag not in tags: 422` 로 재구성(검증 강제 거동은 문자 그대로 동치). 이벤트는 `create_batch` **성공 뒤에만** 기록한다 — 거절된 제출(422/409)은 아무것도 커밋되지 않았으므로 "검증을 건너뛰고 통과시켰다"는 사실 자체가 없다. `record_event` 는 절대 예외를 올리지 않는 계약(observability.py)이라 진단 실패가 202 를 500 으로 바꿀 수 없다. 이벤트 타입 `release_tag_unverified` 는 **계약 대상이 아니다** — 커버리지 추출기(test_reason_codes_coverage.py)는 `detail=`/`reason_code=` 키워드와 예외 생성자 리터럴만 읽고 `event_type=` 은 안 본다(실측). 반면 프론트 배너 문구 키 `tag_unverified` 는 **프론트 전용 사유 코드**로 `registry_unreachable` 선례(api.ts:157 주석)를 그대로 따른다 — reasonCodes.json 과 api.ts REASON_MESSAGES **양쪽**에 넣어야 커버리지·죽은키 테스트 둘 다 초록이다. 항목 3 의 강제 지점은 **config 경계(from_env) 단독**이다 — 프로덕션의 Settings 는 전부 `cli.py:31` 의 `from_env` 를 지나므로(api·controller·migrate 셋 다) 여기서 거부하면 어떤 프로세스도 익명 강등 상태로 뜨지 못하고, `identity_ldap.py` 는 한 줄도 안 바꾼다(resolver 는 검증된 Settings 만 받는다). 자리표시자 검사는 `_is_placeholder` 재사용 — 빈 값과 `CHANGE_ME_*` 를 같은 구멍으로 취급하는 기존 규약이다. **함정 실측 1건**: 레지스트리 전면 다운이면 targets 의 태그 목록이 비어 **포탈 드롭다운에 고를 것이 없다** — UI 로는 미검증 제출 자체가 불가능하다. 배너가 실제로 잡는 창은 "목록 로드 후 제출 전 장애"(TOCTOU)와 부분 장애(리포별 침묵), 그리고 API 직접 제출이다 — 배너를 만들되 이 한계를 정직하게 기록한다.

**Tech Stack:** 백엔드 Python 3.11 표준 라이브러리만(신규 import 0). 프론트 React+TanStack Query+msw(기존 스택, 신규 npm 0) — `useSubmitReleases` 의 mutationFn 에 응답 타입을 달고 배너 1개. DB 무접촉(이벤트는 기존 events 테이블 INSERT). 배포는 제어면 `dms` d38→d39 만 코드상 필요하고, `dms-agent` d39 재빌드는 항목 2 측정의 수단이다(에이전트 코드 무변경 — 내용 동일 재빌드).

## Global Constraints

- **설계 문서 없음** — 이 슬라이스의 「왜」는 BACKLOG §2.3 세 항목과 이 플랜의 「전제 재확인」이 담는다. 플랜과 코드 실측이 충돌하면 실측이 이긴다.
- **fail-open tradeoff 를 뒤집지 않는다** — 레지스트리 침묵 시 태그 제출은 여전히 202 다. 이 플랜의 어떤 태스크도 `unknown_tag` 를 fail-closed 로 만들지 않는다(그건 설계 결정 번복이고 과제 범위 밖이다).
- **새 pip/npm 의존성 금지. 새 테이블·새 컬럼 0.** 새 사유 코드는 `tag_unverified`(프론트 전용) 1건 — reasonCodes.json + api.ts **양쪽**(Task 2). 새 설정 키는 `DMS_LDAP_REQUIRE_AUTH_BIND` 1건 — config.py + 20-config.yaml **양쪽**(Task 3). 새 이벤트 타입 `release_tag_unverified` 는 계약 대상 아님(record_event).
- **`deploy/k8s` 의 이미지 태그를 바꾸지 않는다**(d39 범프는 「플랜 이후: 배포·실증」의 배포자 몫). 단 `20-config.yaml` 에 새 설정 키 1줄 추가는 Task 3 의 계약이다(이미지 태그 무접촉).
- **커밋은 pathspec 으로 한정한다**: 항상 `git commit -m "..." -- <경로들>`. `git add` 계열 **금지**(워크트리 공유 중 인덱스 섞임 사고 — BACKLOG §2.6). 커밋 메시지 말미에 반드시:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01NxyaMVBiYCAHbZaHcYJGBq`
- **뮤테이션 원복에 `git checkout` 금지** — 뮤테이션 전 `cp <파일> /tmp/slice28-<파일명>.bak` 으로 사본을 뜨고, 확인 후 `cp` 로 되돌린다.
- **origin push 금지, 브랜치 변경 금지**(현재 `worktree-dms-slice22plus`, HEAD eb0bad6 = origin/main). `docs/` 아래는 이 플랜 파일 외 생성·수정 금지(실증 후 BACKLOG 갱신은 플랜 밖 관례). `legacy/` 읽기 전용.
- 백엔드 테스트 명령(이 워크트리 전용, `.venv` 는 워크트리 밖 공용):
  `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest <대상> -q`
  전체 스위트는 **포그라운드**, Bash timeout 900000ms. **기준선 1259 passed.**
- 프론트: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run`(**기준선 255 passed**)·`npx tsc -b`(무출력 exit 0). e2e `npm run test:e2e`(**9 passed** — 이 슬라이스는 e2e 무접촉, 확인만).
- 주석은 **한국어**로 「왜」를 적는다.

## 전제 재확인 (2026-08-12, 코드 직접 실측)

과제가 제시한 사전 조사 전부를 코드로 재확인했다 — 정정 2건(하나는 백로그 전제 자체) + 추가 발견 4건.

| 전제 | 재확인 결과 |
|---|---|
| 1. `fetch_repo_tags` 실패 시 None(`registry.py:42-43`), 호출자 `routes_releases.py:124-126` 이 tags None 이면 검증 건너뜀 + 의도적 tradeoff 주석 | ✓ 전부 유지. 정확한 위치: None 반환은 `registry.py:43`(연결 실패)과 `:48`(`{"tags": null}`·비JSON 형식 불량도 None 으로 접힘 — "침묵"보다 약간 넓다), 건너뛰기는 `routes_releases.py:123-126`(주석 그대로). None 과 [] 구분이 계약(registry.py 모듈 주석) — 이번 변경은 그 구분 위에 얹힌다 |
| 2. `:130` 근처 "현재 선언 이미지 관측도 fail-open" 이 같은 성격인가 | **같은 침묵 fail-open 성격, 결과 등급은 다르다.** `_current_image` 가 None(observe 실패/404)이면 `same_tag` 검사를 건너뛴다(`:128-132`) — 이쪽의 최악은 "같은 태그 재적용이 통과해 no-op 롤아웃이 Applied 로 오표시"이고, 보안·가용성 결과가 아니라 편의 가드 스킵이다. targets 화면이 current_image null("—")로 이미 부분 노출한다. **이번 범위 밖으로 판단** — 열린 질문 1 에 근거와 함께 남긴다 |
| 3. targets 는 이미 `registry_ok` 로 강등을 노출 | ✓ `routes_releases.py:76-93` + 프론트 배너(`ReleasesPage.tsx:96-98`, `registry_unreachable` 문구). **제출 경로만 침묵** — 응답은 `{"items": [...]}` 뿐이고 이벤트도 없다 |
| 4. DaemonSet 600s: "5노드 순차가 600s 를 넘기면 거짓 실패" | **최대 정정 — 백로그 전제가 낡았다.** `_note_daemonset_progress`(`rollout_watcher.py:77-106`, I6)가 `updated_number_scheduled` 증가 틱마다 `releases.note_progress` 로 `applied_at` 을 재장전한다(`releases.py:136-148` — UPDATE 가 applied_at=now). 즉 600s 는 **마지막 진행 이후 정체 상한**이지 총 수렴 상한이 아니다 — 거짓 실패 조건은 "총 >600s"가 아니라 "**한 노드** 교체가 >600s 정체"(또는 observe 지속 실패 >600s). 백로그 문구는 I6 이전(슬라이스 13 시점)의 전제다. 측정 대상도 그에 맞춘다: 총 소요가 아니라 **노드당 교체 간격의 최대값** vs 600s |
| 5. `DMS_ROLLOUT_TIMEOUT_SECONDS` 가 config.py 에 이미 있는가 | **있다** — `config.py:55`(`_SERVER_INT_KEYS`, 기본 600) + 필드 `:152` + `20-config.yaml` 에 키·설명 주석까지. 과제의 판단 분기 (b) "상수를 설정 키로"는 **할 일이 없다** → 항목 2 는 순수 실증(코드 0) |
| 6. LDAP 인증 바인드 이미 완전 지원, env 배선 존재 | ✓ `identity_ldap.py:51-54`(bind_dn/pw 있으면 authenticated, 없으면 `or None` → 익명), `config.py:133-134`·`:194-195`. `20-secret.example.yaml:36-42` 가 테스트베드 OpenLDAP 의 익명 바인드 허용을 실측 근거와 함께 문서화 — 플래그 기본값 "false" 의 근거다 |
| 7. 배포 태그 | 실측: 제어면 `dms` 5곳 전부 **d38**(슬라이스 27 배포 완료 반영 — `30-migrate-job.yaml:25`, `40-api.yaml:67·84`, `41-controller.yaml:35·52`), `dms-agent`(`50-agent-daemonset.yaml:72`)·`DMS_JOB_IMAGE`(`20-config.yaml`)는 d35. 이 슬라이스는 제어면 **d39** + 에이전트 재빌드(측정 수단, 아래 「배포·실증」) |

**추가 발견(과제 지시에 없던 것):**

- **이벤트는 포탈에서 요청 상세로만 보인다** — events UI 는 `RequestDetail.tsx`(request_id 기반)뿐이고 관리자 이벤트 화면이 없다(프론트 전수 grep). `release_tag_unverified` 는 request_id 없는 이벤트라 **포탈 어디에도 안 뜬다** — `db_reconnected`(슬라이스 22)와 같은 "DB 조회용 영속 흔적" 계층이다. 그래서 응답 플래그+배너가 가시 채널로 반드시 함께 가야 한다(플래그만 vs 이벤트만의 선택지는 실측상 성립하지 않는다).
- **레지스트리 전면 다운이면 포탈로는 미검증 제출이 불가능하다** — targets 의 tags 가 [] 라 드롭다운에 선택지가 없다(`ReleasesPage.tsx` select 는 `t.tags` 만 옵션으로 준다). 배너의 실효 창은 ① 목록 로드 후 제출 전 장애(TOCTOU), ② 리포별 부분 침묵(api/controller 의 `dms` 는 응답, `dms-agent` 만 침묵 같은), ③ curl 등 API 직접 제출의 확인 채널. 라이브 실증 §2 는 ① 을 그대로 재현한다.
- **커버리지 추출기의 시야 실측**: `test_reason_codes_coverage.py` 의 `_LITERAL_KEYWORDS` 는 `{detail, reason_code}` 뿐 — `event_type=` 리터럴은 추출되지 않는다. 과제 지시의 "어느 쪽인지 구분해라"에 대한 답: `release_tag_unverified` 는 계약 밖(이벤트), `tag_unverified` 는 계약 안(프론트 전용 코드, reasonCodes.json 의 `registry_unreachable` 과 같은 칸에 두고 api.ts 죽은키 테스트까지 양쪽 갱신).
- **conftest 의 `client` 는 `db` 픽스처를 공유한다**(`conftest.py:22-24`) — API 테스트가 같은 테스트 함수에서 `db.query(...)` 로 events 행을 직접 단언할 수 있다(신규 픽스처 불요).
- **bind 자격증명은 자리표시자 검증이 전혀 없다** — `DMS_LDAP_BIND_DN: "CHANGE_ME_..."` 가 그대로 실 DN 으로 ldap3 에 흘러간다(현행은 바인드 실패 → resolve 시점 IdentityUnavailable 소음). REQUIRE 플래그가 `_is_placeholder` 로 빈 값과 자리표시자를 함께 걸러 이 구멍도 닫는다.

## 파일 구조

| 파일 | 책임 |
|---|---|
| `src/dms/api/routes_releases.py` (수정) | Task 1: 제출 루프 unverified 추적 + `tag_verified` 응답 플래그 + `release_tag_unverified` 이벤트(성공 시에만) |
| `tests/test_api_releases.py` (수정) | Task 1: 미검증 플래그·이벤트 / 검증 시 무이벤트 / 거절 시 무이벤트 |
| `frontend/src/lib/reasonCodes.json` (수정) | Task 2: `tag_unverified` 추가(프론트 전용 칸) |
| `frontend/src/lib/api.ts` (수정) | Task 2: REASON_MESSAGES 에 `tag_unverified` 문구 |
| `frontend/src/features/releases/useReleases.ts` (수정) | Task 2: `SubmitReleasesResult` 타입 + mutationFn 응답 타입 |
| `frontend/src/features/releases/ReleasesPage.tsx` (수정) | Task 2: 미검증 경고 배너 |
| `frontend/src/features/releases/ReleasesPage.test.tsx` (수정) | Task 2: 배너 표시/비표시 |
| `src/dms/config.py` (수정) | Task 3: `ldap_require_auth_bind` 필드 + from_env 파싱·fail-closed 검증 |
| `tests/test_config.py` (수정) | Task 3: 기동 거부(결측·자리표시자)·통과(실값)·기본값 유지 |
| `deploy/k8s/20-config.yaml` (수정) | Task 3: `DMS_LDAP_REQUIRE_AUTH_BIND: "false"` + 「왜」 주석(이미지 태그 무접촉) |

---

### Task 0: 기준선 확인 (커밋 없음)

**Files:** 없음(검증만)

**Interfaces:** 이후 모든 태스크의 판정 기준(기준선 초록)을 만든다.

- [ ] **Step 1: 백엔드 기준선**

Run(포그라운드, Bash timeout 900000ms): `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests -q`
Expected: `1259 passed`. 여기 빨강이면 이 슬라이스 밖의 문제다 — 진행 전에 보고.

- [ ] **Step 2: 프론트·e2e 기준선**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run && npx tsc -b && npm run test:e2e`
Expected: vitest `255 passed`, tsc 무출력 exit 0, e2e `9 passed`. 수치를 기록해 둔다 — Task 4 에서 vitest 만 +2 이고 e2e 는 동일해야 한다.

---

### Task 1: 항목 1 백엔드 — 레지스트리 침묵 제출 비침묵화 (fail-open 유지)

**Files:**
- Modify: `src/dms/api/routes_releases.py`
- Modify: `tests/test_api_releases.py`

**Interfaces:**
- Produces: `POST /api/admin/releases` 202 응답에 `tag_verified: bool` — 제출된 모든 컴포넌트의 태그 존재가 레지스트리로 확인됐으면 true, 하나라도 침묵 리포였으면 false. false 인 202 마다 `release_tag_unverified` 이벤트 1건(component="api", severity="warning", payload 에 미검증 컴포넌트 목록).
- Consumes: `record_event`(절대 예외를 올리지 않는 계약 — observability.py), `_tags_for` 요청 단위 캐시(리포당 1회 조회 유지).
- **함정 명시 2건**: ① 거동 동치 재구성 — `if tags is not None and tag not in tags: raise` 를 `if tags is None: 추적 / elif ...: raise` 로 바꾸는 것이라 **검증 강제 자체는 1비트도 안 바뀐다**(기존 fail-open 테스트 2건이 그대로 초록이어야 하는 이유). ② 이벤트는 `create_batch` 성공 **뒤** — 422/409 로 거절된 제출은 커밋된 것이 없으므로 "건너뛰고 통과시켰다"는 사실이 성립하지 않는다. 사전에 기록하면 경합 창(409)에서 유령 이벤트가 남는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_api_releases.py` — 파일 끝에 추가:

```python
def test_submit_reports_unverified_when_repo_is_silent(rollout_client, db,
                                                       monkeypatch):
    """슬라이스 28(BACKLOG §2.3): fail-open 은 유지하되 침묵을 걷어낸다.
    리포별 부분 침묵(dms 는 응답, dms-agent 만 None)으로 검증의 리포 단위
    granularity 까지 고정한다 -- 응답한 리포의 태그는 정상 검증되고(dev 태그가
    목록에 있어 통과), 침묵 리포만 미검증으로 표시된다."""
    monkeypatch.setattr(
        "dms.api.routes_releases.fetch_repo_tags",
        lambda registry, repo: {"dms": ["d22", "d23"]}.get(repo))  # dms-agent 는 None
    r = rollout_client.post(
        "/api/admin/releases",
        json={"items": [{"component": "dms-api", "tag": "d23"},
                        {"component": "dms-agent", "tag": "dev9"}]},
        headers=ADMIN)
    assert r.status_code == 202
    assert r.json()["tag_verified"] is False
    events = db.query(
        "SELECT payload FROM events WHERE event_type = 'release_tag_unverified'")
    assert len(events) == 1
    assert "dms-agent" in events[0]["payload"]
    assert "dms-api" not in events[0]["payload"]   # 검증된 컴포넌트는 안 싣는다


def test_submit_verified_when_registry_answers(rollout_client, db):
    # 정상 경로: 레지스트리가 답했고 태그가 존재 -- 플래그 true, 이벤트 0건.
    # 이벤트가 정상 제출마다 쌓이면 "경고"의 의미가 죽는다(늑대 소년).
    r = rollout_client.post("/api/admin/releases",
                            json={"items": [{"component": "dms-api", "tag": "d23"}]},
                            headers=ADMIN)
    assert r.status_code == 202
    assert r.json()["tag_verified"] is True
    assert db.query("SELECT id FROM events"
                    " WHERE event_type = 'release_tag_unverified'") == []


def test_rejected_submit_leaves_no_unverified_event(rollout_client, db,
                                                    monkeypatch):
    # 레지스트리 침묵 + 형식 불량 태그 -> 422 거절. 거절엔 커밋된 것이 없으므로
    # "건너뛰고 통과시켰다"는 사실 자체가 없다 -- 이벤트도 없어야 한다.
    # (현행도 그렇다 -- 이 테스트는 이벤트 기록이 create_batch 앞으로 끌려가는
    # 회귀를 막는 그물이다.)
    monkeypatch.setattr("dms.api.routes_releases.fetch_repo_tags",
                        lambda registry, repo: None)
    r = rollout_client.post("/api/admin/releases",
                            json={"items": [{"component": "dms-api", "tag": "has space"}]},
                            headers=ADMIN)
    assert r.status_code == 422
    assert db.query("SELECT id FROM events"
                    " WHERE event_type = 'release_tag_unverified'") == []
```

(픽스처 `db` 는 conftest 의 것 — `client`/`rollout_client` 가 같은 db 인스턴스를 공유한다. payload 는 dump_json 문자열로 저장되므로 부분 문자열 단언이 성립한다.)

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_api_releases.py -q`
Expected: 신규 FAIL 2건 — `test_submit_reports_unverified...` 와 `test_submit_verified...` 는 `r.json()["tag_verified"]` 에서 `KeyError`(현행 응답은 items 뿐). `test_rejected_submit_leaves_no_unverified_event` 는 **즉시 PASS 가 맞다**(현행도 이벤트가 없다 — 회귀 방지 그물, 슬라이스 27 의 멱등 가드 테스트와 같은 성격). 기존 테스트 전부 PASS — 특히 fail-open 2건(`test_unknown_tag_enforced_only_when_registry_answers`·`test_malformed_tag_is_rejected_even_when_registry_is_silent`)이 초록인 것이 "tradeoff 무변경"의 증거다.

- [ ] **Step 3: routes_releases.py 를 고친다**

`submit_releases`(`:96-141`)의 루프와 반환을 다음으로 교체(다른 부분 무변경):

```python
    tags_cache: dict = {}
    records = []
    unverified: list[str] = []
    for item in body.items:
        spec = COMPONENTS[item.component]
        tag = (item.tag or "").strip()
        if not _TAG_RE.fullmatch(tag):
            raise HTTPException(status_code=422, detail="unknown_tag")
        tags = _tags_for(tags_cache, settings.build_registry, spec["repository"])
        # 레지스트리가 응답할 때만 강제한다 -- 응답 불가면 통과시키고 잘못된 태그는
        # patch 후 ImagePullBackOff로 드러나게 한다(잘못된 차단보다 낫다, 설계 §7).
        # 슬라이스 28: tradeoff 는 유지하되 침묵은 걷어낸다 -- 건너뛴 사실을
        # 추적해 응답 플래그와 이벤트로 운영자에게 알린다(조용한 fail-open 은
        # "검증됐다"와 구분이 안 된다는 것이 BACKLOG §2.3 항목의 실체다).
        if tags is None:
            unverified.append(item.component)
        elif tag not in tags:
            raise HTTPException(status_code=422, detail="unknown_tag")
        image = f"{settings.build_registry}/{spec['repository']}:{tag}"
        # IfNotPresent 함정: 같은 태그 재적용은 아무 일도 안 일어나는데 롤아웃은
        # 성공한 것처럼 보인다(설계 §7). 현재 선언 이미지를 못 읽으면(observe
        # None/실패) 검사를 건너뛴다 -- 여기서도 fail-open이다.
        if _current_image(runner, spec) == image:
            raise HTTPException(status_code=422, detail="same_tag")
        records.append({"component": item.component, "image": image, "tag": tag})
    try:
        # 감사 로그는 create_batch가 트랜잭션 안에서 직접 쓴다(mutation_class="release")
        # -- 여기서 또 쓰면 같은 제출이 감사 로그에 두 번 나타난다.
        rows = repos.releases.create_batch(items=records, actor=audit_actor(identity))
    except DomainValidationError as e:
        # 사전 체크와 이 사이의 경합 창 -- 트랜잭션 안 가드가 잡는다.
        raise HTTPException(status_code=409, detail=e.reason_code)
    if unverified:
        # create_batch 성공 **뒤에만** 기록한다 -- 거절된 제출(422/409)은 커밋된
        # 것이 없으므로 "건너뛰고 통과시켰다"는 사실이 성립하지 않는다. 이벤트는
        # 포탈에 안 뜨는 영속 흔적(관리자 이벤트 화면 없음 -- db_reconnected 와
        # 같은 계층)이고, 즉시 가시 채널은 아래 tag_verified 플래그 + 포탈 배너다.
        repos.observability.record_event(
            component="api", severity="warning",
            event_type="release_tag_unverified",
            message=f"registry silent; unverified={','.join(unverified)}",
            payload={"components": unverified})
    return {"items": [_public(r) for r in rows], "tag_verified": not unverified}
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_api_releases.py tests/test_releases_repo.py tests/test_rollout_watcher.py tests/test_reason_codes_coverage.py -q`
Expected: 전부 PASS. `test_reason_codes_coverage` 초록 = `event_type=` 리터럴이 추출 대상이 아니라는 판단의 실측 확인(만약 여기 빨강이면 판단이 틀린 것 — reasonCodes.json 에 넣지 말고 **보고**).

- [ ] **Step 5: 뮤테이션으로 이빨 확인 후 원복**

`cp src/dms/api/routes_releases.py /tmp/slice28-routes_releases.py.bak` 후: `unverified.append(item.component)` 한 줄을 `pass` 로 교체(fail-open 거동은 그대로, 추적만 죽인다 — "고치는 척"의 정확한 모형) → `test_submit_reports_unverified...` 가 `tag_verified is False` 단언에서 RED(실제값 True, 이벤트 0건). **기존 fail-open 테스트 2건은 초록으로 남는 것을 함께 관찰한다** — 기존 그물은 통과 여부만 보고 침묵 여부는 못 본다는 증거다. `cp /tmp/slice28-routes_releases.py.bak src/dms/api/routes_releases.py` 로 원복, Step 4 재확인.

- [ ] **Step 6: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git commit -m "$(cat <<'EOF'
feat(releases): 레지스트리 침묵 제출 비침묵화 — tag_verified 응답 플래그 + release_tag_unverified 이벤트(fail-open tradeoff 는 유지)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NxyaMVBiYCAHbZaHcYJGBq
EOF
)" -- src/dms/api/routes_releases.py tests/test_api_releases.py
```

---

### Task 2: 항목 1 프론트 — 릴리스 화면 미검증 경고 배너

**Files:**
- Modify: `frontend/src/lib/reasonCodes.json`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/features/releases/useReleases.ts`
- Modify: `frontend/src/features/releases/ReleasesPage.tsx`
- Modify: `frontend/src/features/releases/ReleasesPage.test.tsx`

**Interfaces:**
- Produces: 제출 202 응답의 `tag_verified === false` 일 때 경고 배너(문구 키 `tag_unverified` — 프론트 전용 코드, `registry_unreachable` 선례). `SubmitReleasesResult` 타입.
- **계약**: `tag_unverified` 는 reasonCodes.json **과** api.ts REASON_MESSAGES **양쪽**에 — 커버리지 테스트("모든 코드에 매핑")와 죽은키 테스트("목록 밖 키 금지")가 각각 한쪽 누락을 잡는다.
- **한계 명시(전제 재확인 추가 발견 2)**: 레지스트리 전면 다운이면 드롭다운이 비어 UI 제출 자체가 불가 — 이 배너가 잡는 것은 TOCTOU(목록 로드 후 제출 전 장애)와 리포별 부분 침묵이다. 배너 주석에 박는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`frontend/src/features/releases/ReleasesPage.test.tsx` — describe 블록 끝에 추가:

```tsx
  it("미검증 제출(tag_verified=false)은 경고 배너를 띄운다", async () => {
    // 레지스트리 전면 다운이면 드롭다운이 비어 UI 제출 자체가 불가하다 --
    // 이 배너가 잡는 실 창은 "목록 로드 후 제출 전 장애"(TOCTOU)와 리포별
    // 부분 침묵이다. msw 로 그 결과(202 + tag_verified:false)만 재현한다.
    server.use(http.post("/api/admin/releases", () =>
      HttpResponse.json({ items: [], tag_verified: false }, { status: 202 })));
    wrap(<ReleasesPage />);
    await screen.findByRole("heading", { name: "릴리스" });
    await userEvent.selectOptions(screen.getByLabelText("dms-api"), "d23");
    await userEvent.click(screen.getByRole("button", { name: "롤아웃 시작" }));
    expect(await screen.findByText(/태그 존재를 확인하지 못한 채/)).toBeInTheDocument();
  });

  it("검증된 제출(tag_verified=true)에는 경고 배너가 없다", async () => {
    server.use(http.post("/api/admin/releases", () =>
      HttpResponse.json({ items: [], tag_verified: true }, { status: 202 })));
    wrap(<ReleasesPage />);
    await screen.findByRole("heading", { name: "릴리스" });
    await userEvent.selectOptions(screen.getByLabelText("dms-api"), "d23");
    await userEvent.click(screen.getByRole("button", { name: "롤아웃 시작" }));
    // 성공 시 선택이 비워지는 기존 거동을 settle 신호로 쓴다.
    await waitFor(() => expect(screen.getByLabelText("dms-api")).toHaveValue(""));
    expect(screen.queryByText(/태그 존재를 확인하지 못한 채/)).toBeNull();
  });
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run src/features/releases/ReleasesPage.test.tsx`
Expected: 첫 테스트 FAIL(`findByText` 타임아웃 — 배너 없음). 둘째는 **즉시 PASS 가 맞다**(배너가 아직 없으니 — 회귀 방지 그물). 기존 테스트 전부 PASS.

- [ ] **Step 3: 구현한다**

**(1)** `frontend/src/lib/reasonCodes.json` — `"registry_unreachable",` 줄 바로 아래에:

```json
  "tag_unverified",
```

**(2)** `frontend/src/lib/api.ts` — REASON_MESSAGES 의 `registry_unreachable` 항목 바로 아래에:

```ts
  // 프론트 전용 코드다(registry_unreachable 과 같은 관례) -- 백엔드는 detail 이
  // 아니라 제출 202 응답의 tag_verified:false 필드로 알린다(슬라이스 28).
  tag_unverified: "레지스트리가 응답하지 않아 태그 존재를 확인하지 못한 채 접수되었습니다 — 태그가 틀리면 ImagePullBackOff 로 드러납니다",
```

**(3)** `frontend/src/features/releases/useReleases.ts` — `SubmitReleasesBody` 옆에 타입을 추가하고 mutationFn 에 단다(`Release` 는 `../../lib/types` 에서 import — 파일 상단 기존 import 에 합류):

```ts
export interface SubmitReleasesBody { items: { component: string; tag: string }[] }
// tag_verified 는 옵셔널이다 -- 구 서버(d38 이전)와 겹치는 배포 순간에 필드가
// 없어도 배너 로직(false 일 때만 표시)이 조용히 꺼질 뿐 깨지지 않는다.
export interface SubmitReleasesResult { items: Release[]; tag_verified?: boolean }

export const useSubmitReleases = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: SubmitReleasesBody) =>
      apiSend<SubmitReleasesResult>("POST", "/api/admin/releases", b),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["releases"] });
      qc.invalidateQueries({ queryKey: ["release-targets"] });
    },
  });
};
```

**(4)** `frontend/src/features/releases/ReleasesPage.tsx` — `{submit.isError && ...}` 줄 바로 아래에:

```tsx
          {/* fail-open 비침묵화(슬라이스 28): 서버가 태그 존재를 검증하지 못한 채
              접수했다 -- 202 라서 성공처럼 보이는 바로 그 순간에 보여야 한다.
              레지스트리 전면 다운이면 드롭다운이 비어 여기까지 못 오고, 이 배너가
              잡는 실 창은 목록 로드 후 제출 전 장애(TOCTOU)와 리포별 부분 침묵이다. */}
          {submit.data?.tag_verified === false && (
            <p className="rounded-lg bg-busybg px-3 py-2 text-busy">
              {reasonText("tag_unverified")}
            </p>
          )}
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run && npx tsc -b`
Expected: `257 passed`(255 + 2 — reasonCodes 커버리지·죽은키 테스트가 양쪽 갱신을 검증하며 초록), tsc 무출력 exit 0.

- [ ] **Step 5: 뮤테이션으로 이빨 확인 후 원복**

`cp frontend/src/features/releases/ReleasesPage.tsx /tmp/slice28-ReleasesPage.tsx.bak` 후: Step 3-(4)의 배너 JSX 블록을 삭제 → 첫 테스트만 RED(배너 부재), 둘째는 초록 유지. `cp /tmp/slice28-ReleasesPage.tsx.bak frontend/src/features/releases/ReleasesPage.tsx` 로 원복, Step 4 재확인.

- [ ] **Step 6: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git commit -m "$(cat <<'EOF'
feat(portal): 릴리스 미검증 제출 경고 배너 — tag_unverified(프론트 전용 코드), SubmitReleasesResult 타입

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NxyaMVBiYCAHbZaHcYJGBq
EOF
)" -- frontend/src/lib/reasonCodes.json frontend/src/lib/api.ts frontend/src/features/releases/useReleases.ts frontend/src/features/releases/ReleasesPage.tsx frontend/src/features/releases/ReleasesPage.test.tsx
```

---

### Task 3: 항목 3 — `DMS_LDAP_REQUIRE_AUTH_BIND` fail-closed (자격증명 없이 가능한 강화)

**Files:**
- Modify: `src/dms/config.py`
- Modify: `tests/test_config.py`
- Modify: `deploy/k8s/20-config.yaml`

**Interfaces:**
- Produces: `Settings.ldap_require_auth_bind: bool = False`. `from_env` 에서 플래그가 true 인데 `DMS_LDAP_BIND_DN`/`DMS_LDAP_BIND_PW` 중 하나라도 `_is_placeholder`(빈 값·CHANGE_ME·REPLACE_WITH_)면 `SettingsError` — `cli.py:31-35` 를 지나는 모든 프로세스(api·controller·migrate)가 **기동 거부**(exit 2 → CrashLoopBackOff, 시끄럽다).
- Consumes: `_parse_bool`(기존), `_is_placeholder`(기존 — 빈 값과 자리표시자를 같은 구멍으로 보는 규약 재사용).
- **왜 config 경계 단독인가(주석으로 박는다)**: 프로덕션 Settings 는 전부 from_env 를 지난다. `identity_ldap.py` 의 connect 는 검증된 Settings 만 받으므로 이중 가드는 사족이고, resolver 쪽에 넣으면 발화 시점이 "첫 resolve"(런타임, 잡 제출 순간)로 밀려 fail-closed 의 요점 — 운영자가 배포 순간에 알아챈다 — 이 죽는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_config.py` — 파일 끝에 추가:

```python
def test_require_auth_bind_refuses_startup_without_credentials():
    # 슬라이스 28(BACKLOG §2.3): 인증 바인드를 의도(플래그 true)했는데 자격증명이
    # 없으면 익명으로 조용히 떨어지는 대신 기동을 거부한다 -- 운영자가 "인증
    # 바인드로 돌고 있다"고 믿는 채 익명으로 도는 상태가 이 항목의 실체다.
    with pytest.raises(SettingsError) as e:
        Settings.from_env({**VALID, "DMS_LDAP_REQUIRE_AUTH_BIND": "true"})
    text = str(e.value)
    assert "DMS_LDAP_BIND_DN" in text
    assert "DMS_LDAP_BIND_PW" in text


def test_require_auth_bind_rejects_placeholder_credentials():
    # 빈 값만 걸면 20-secret.example.yaml 의 CHANGE_ME 류가 실 DN 으로 흘러간다 --
    # _is_placeholder 를 재사용해 결측과 자리표시자를 같은 구멍으로 본다(기존 규약).
    with pytest.raises(SettingsError):
        Settings.from_env({**VALID, "DMS_LDAP_REQUIRE_AUTH_BIND": "1",
                           "DMS_LDAP_BIND_DN": "cn=CHANGE_ME_BIND_DN,dc=dms,dc=local",
                           "DMS_LDAP_BIND_PW": "REPLACE_WITH_BIND_PW"})


def test_require_auth_bind_passes_with_real_credentials():
    s = Settings.from_env({**VALID, "DMS_LDAP_REQUIRE_AUTH_BIND": "true",
                           "DMS_LDAP_BIND_DN": "cn=dms-svc,ou=People,dc=dms,dc=local",
                           "DMS_LDAP_BIND_PW": "s3cret"})
    assert s.ldap_require_auth_bind is True
    assert s.ldap_bind_dn == "cn=dms-svc,ou=People,dc=dms,dc=local"


def test_anonymous_bind_remains_the_default():
    # 플래그 미설정이면 현행 유지 -- 테스트베드는 익명 바인드가 실 구성이다
    # (20-secret.example.yaml). 기본값을 true 로 하면 이 배포 자체가 못 뜬다.
    s = Settings.from_env(VALID)
    assert s.ldap_require_auth_bind is False
    assert s.ldap_bind_dn == ""
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_config.py -q`
Expected: 신규 4건 전부 FAIL — 거부 2건은 `SettingsError` 미발생(`pytest.raises` 실패), 통과 2건은 `ldap_require_auth_bind` 필드 부재로 `AttributeError`. 기존 테스트 PASS.

- [ ] **Step 3: config.py 를 고친다**

**(1)** `Settings` 필드 — `ldap_bind_pw: str = ""` 바로 아래에:

```python
    # 슬라이스 28: true 면 bind DN/PW 결측·자리표시자 시 기동 거부(fail-closed).
    # 익명 바인드로의 침묵 강등을 막는 스위치다 -- identity_ldap 이 아니라 여기서
    # 거부하는 이유는 발화 시점이 배포 순간(기동)이어야 운영자가 알아채기 때문.
    ldap_require_auth_bind: bool = False
```

**(2)** `from_env` — `extra = {...}` 파싱 뒤, `if problems: raise` **앞**에:

```python
        ldap_bind_dn = environ.get("DMS_LDAP_BIND_DN", "")
        ldap_bind_pw = environ.get("DMS_LDAP_BIND_PW", "")
        ldap_require_auth_bind = _parse_bool(environ, "DMS_LDAP_REQUIRE_AUTH_BIND")
        if ldap_require_auth_bind:
            # 인증 바인드를 의도했는데 자격증명이 없으면 identity_ldap 이 익명으로
            # 조용히 떨어진다(bind_dn or None) -- 그 침묵을 기동 거부로 바꾼다.
            # _is_placeholder 라 빈 값과 CHANGE_ME 류를 같은 구멍으로 본다.
            for env_key, value in (("DMS_LDAP_BIND_DN", ldap_bind_dn),
                                   ("DMS_LDAP_BIND_PW", ldap_bind_pw)):
                if _is_placeholder(value):
                    problems.append(
                        f"DMS_LDAP_REQUIRE_AUTH_BIND is true but {env_key}"
                        " is missing or a placeholder")
```

**(3)** 생성자 호출의 `ldap_bind_dn=`/`ldap_bind_pw=` 두 줄을 위 로컬 변수 사용으로 바꾸고 그 아래에 `ldap_require_auth_bind=ldap_require_auth_bind,` 를 추가한다(environ.get 중복 제거 — 검증과 생성이 같은 값을 봐야 한다).

**(4)** `deploy/k8s/20-config.yaml` — LDAP 절(`DMS_LDAP_GROUP_BASE` 아래)에 추가(**이미지 태그 무접촉**):

```yaml
  # 슬라이스 28: "true"면 DMS_LDAP_BIND_DN/PW(dms-secrets)가 비거나 자리표시자일 때
  # api/controller/migrate 가 기동을 거부한다(SettingsError) -- 인증 바인드를
  # 의도했는데 익명으로 조용히 도는 상태를 막는 스위치. 테스트베드 OpenLDAP 은
  # 익명 바인드를 허용하므로(20-secret.example.yaml) 바인드 계정이 준비되기
  # 전까지 "false"로 둔다 -- 전환 절차: 계정 발급 -> dms-secrets 에 DN/PW 주입
  # -> 이 값을 "true"로. 순서를 어기면 제어면 전체가 CrashLoopBackOff 다(의도된
  # 시끄러움이지만, 켜기 전에 자격증명부터).
  DMS_LDAP_REQUIRE_AUTH_BIND: "false"
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_config.py tests/test_identity_ldap.py -q`
Expected: 전부 PASS — test_identity_ldap 초록 = resolver 무접촉(익명/인증 바인드 거동 무변경)의 증거.

- [ ] **Step 5: 뮤테이션으로 이빨 확인 후 원복**

`cp src/dms/config.py /tmp/slice28-config.py.bak` 후: Step 3-(2)의 `if ldap_require_auth_bind:` 검증 블록(for 루프 포함)을 삭제 → 거부 테스트 2건이 RED(`SettingsError` 미발생 — 플래그가 있어도 익명으로 조용히 뜨는, 정확히 이 태스크가 막는 상태), 통과 2건은 초록 유지(필드·파싱은 남아 있으니). `cp /tmp/slice28-config.py.bak src/dms/config.py` 로 원복, Step 4 재확인.

- [ ] **Step 6: 커밋**

```bash
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus
git commit -m "$(cat <<'EOF'
feat(config): DMS_LDAP_REQUIRE_AUTH_BIND — 인증 바인드 의도 시 자격증명 결측·자리표시자면 기동 거부(익명 침묵 강등 봉쇄)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NxyaMVBiYCAHbZaHcYJGBq
EOF
)" -- src/dms/config.py tests/test_config.py deploy/k8s/20-config.yaml
```

---

### Task 4: 마감 검증 — 전체 스위트 + 프론트·e2e + 불변 조항 (커밋 없음)

**Files:** 없음(검증만)

- [ ] **Step 1: 백엔드 전체 스위트**

Run(포그라운드, Bash timeout 900000ms): `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests -q`
Expected: **약 1266 passed**(기준선 1259 + 신규 7: T1 3 + T3 4 — 근사치다. 수가 다르면 신규 수를 다시 세되 **failed 0 이 본질**이다).

- [ ] **Step 2: 프론트·e2e**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus/frontend && npx vitest run && npx tsc -b && npm run test:e2e`
Expected: vitest **257 passed**(255 + T2 2), tsc 무출력 exit 0, e2e `9 passed`(무접촉 — 빨개지면 환경 문제로 판단하되 진행 전에 보고).

- [ ] **Step 3: 계약·불변 조항 확인**

Run: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice22plus && git status --porcelain && git log --oneline -4 && git diff HEAD~3 --stat -- deploy/k8s docs legacy`
Expected: 작업 트리 clean(커밋 3건 + 이 플랜 파일 외 잔여물 없음), `deploy/k8s` diff 는 **20-config.yaml 1파일 키 추가뿐**(이미지 태그 줄 무변경 — `git diff HEAD~3 -- deploy/k8s | grep 'image:'` 가 빈 출력), `legacy/` 무변경. 커밋 3건의 대상이 정확히 10파일(T1 2 + T2 5 + T3 3)인지 확인. fail-open 의미 무변경의 최종 증거: `test_unknown_tag_enforced_only_when_registry_answers` 가 Step 1 에서 초록이었다.

---

## 플랜 이후: 배포·실증 (별도 ops, 플랜 태스크 밖)

플랜 실행이 끝나면 배포자가 테스트베드에서 수행한다(슬라이스 12~27 관례). **매니페스트-우선**: 태그를 먼저 bump→커밋하고 그 커밋에서 빌드한다. **스키마 무변경이므로 migrate Job 재실행은 불요다**(initContainer 의 migrate 는 어차피 돌지만 no-op — 슬라이스 27 과 다른 점을 명시해 둔다). 빌드는 클러스터 내 `build_build_pod`(슬라이스 24·25·27 실적), DB 확인은 API 파드 안 python. 되돌릴 수 있는 조작만, 원복까지.

**1. 태그 범프 커밋 + 빌드 + apply**

```bash
# (a) 매니페스트 범프 -- 제어면 dms d38→d39 5곳(30-migrate-job.yaml:25 /
#     40-api.yaml:67,84 / 41-controller.yaml:35,52) + 50-agent-daemonset.yaml:72
#     dms-agent d35→d39(에이전트 코드 무변경이지만 §3 DaemonSet 측정의 롤아웃
#     수단 -- 리포별 태그는 독립이라 d36 이 아니라 d39 로 맞춰도 무방하고, 빌드
#     파드 1회로 끝난다). DMS_JOB_IMAGE(20-config.yaml)는 d35 유지.
git commit -m "deploy(k8s): 제어면 d39 + 에이전트 d39 (슬라이스 28 운영·보안)" -- deploy/k8s
# main 병합·push 후 그 커밋에서(빌드 파드는 GitHub 에서 clone 한다):

# (b) 빌드 파드 -- images=["dms","dms-agent"], DMS_BUILD_TAG="d39". 로그에서
#     DMS_COMMIT_SHA=<범프 커밋> 과 두 push 성공 확인 후 빌드 파드 삭제.

# (c) 제어면 apply(migrate Job 재적용 불요 -- 스키마 무변경):
kubectl apply -f deploy/k8s/20-config.yaml   # DMS_LDAP_REQUIRE_AUTH_BIND: "false" 추가분
kubectl apply -f deploy/k8s/40-api.yaml -f deploy/k8s/41-controller.yaml
kubectl -n dms rollout status deploy/dms-api deploy/dms-controller
# 50-agent-daemonset.yaml 은 여기서 apply **하지 않는다** -- 에이전트는 §2 에서
# 포탈 롤아웃으로 올려야 rollout_watcher(측정 대상)가 개입한다. 포탈 롤아웃 완료
# 후 apply 하면 같은 이미지라 no-op(드리프트 배지도 정리된다).
```

**2. 항목 1 실증 — 미검증 제출의 가시화 (TOCTOU 창 재현) + 항목 2 측정을 한 롤아웃으로**

```bash
# (a) 포탈 릴리스 화면을 연다(레지스트리 정상) -- dms-agent 태그 d39 를 고른다.
#     아직 제출하지 않는다.
# (b) pkg-01 에서 레지스트리를 잠시 멈춘다(컨테이너명은 podman ps 로 실측):
#     ssh pkg-01 "podman stop <registry-container>"
# (c) 포탈에서 「롤아웃 시작」 클릭 -> 202. **기대: 경고 배너**
#     "레지스트리가 응답하지 않아 태그 존재를 확인하지 못한 채 접수되었습니다…".
#     (전면 다운 상태로 화면을 새로 로드하면 드롭다운이 비어 제출 자체가 불가 --
#      이 순서(로드->정지->제출)가 배너의 실효 창인 TOCTOU 의 정확한 재현이다.)
# (d) **즉시 레지스트리 재기동**(첫 노드의 pull 이 실패하기 전에):
#     ssh pkg-01 "podman start <registry-container>"
#     -- 몇 초 겹치면 첫 파드가 ImagePullBackOff 1~2회 후 백오프 재시도로
#     회복한다(무해, 관찰만).
# (e) 이벤트 영속 흔적 확인:
kubectl -n dms exec deploy/dms-api -c api -- python -c "
import os
from dms.db import Database
db = Database.connect(os.environ['DMS_DATABASE_URL'])
print(db.query(\"SELECT message, payload, at FROM events\"
              \" WHERE event_type = 'release_tag_unverified'\"))"
# 기대: 1행, payload 에 dms-agent. 0행이면 비침묵화가 안 먹은 것 -- 진행 중단·보고.
```

**3. 항목 2 실증 — DaemonSet 롤아웃 실측 (위 (c)의 롤아웃을 그대로 관찰)**

```bash
# (a) 별도 터미널에서 미리 켜 둔다(§2 (a) 시점부터):
kubectl -n dms get pods -l app.kubernetes.io/name=dms-agent -w \
  --output-watch-events | ts '%FT%T'   # ts 없으면 while read 로 date 찍기
# (b) 기록할 것: 노드별 (old 파드 삭제 -> new 파드 Ready) 교체 간격 5개,
#     그 최대값, 총 소요. 판정: **노드당 최대 교체 간격** vs 600s -- I6(정체
#     기준 시계) 이후 600s 는 노드-단위 정체 상한이므로 총 소요는 600s 를
#     넘어도 거짓 실패가 아니다(전제 재확인 4 -- 이 정정 자체가 기록 대상).
# (c) 시계 재장전의 실물 확인 -- progress 가 1..5 로 걸어 올라갔는지:
kubectl -n dms exec deploy/dms-api -c api -- python -c "
import os
from dms.db import Database
db = Database.connect(os.environ['DMS_DATABASE_URL'])
print(db.query(\"SELECT component, tag, state, reason_code, progress, applied_at\"
              \" FROM releases ORDER BY id DESC LIMIT 3\"))"
# 기대: dms-agent 행 state=Applied, reason_code None, progress=5.
# rollout_timeout 이 났다면 그 자체가 측정 결과다(노드당 >600s) -- 값과 함께 보고,
# DMS_ROLLOUT_TIMEOUT_SECONDS(이미 설정 키) 상향을 별도 결정으로 올린다.
# (d) 측정치(노드당 최대·총 소요)를 BACKLOG §2.3 항목 갱신에 실을 것.
```

**4. 항목 3 실증 — fail-closed 발화 확인 (자격증명 없이 가능한 부분) + 자격증명 블록 보고**

```bash
# (a) 정상 기동 확인(플래그 "false" -- 현행 익명 바인드 유지): d39 apply 후
#     api/controller Running, 포탈 로그인·잡 제출 정상(익명 바인드 resolve).
# (b) fail-closed 발화를 1회성 프로세스로 실증 -- 실 파드 무영향, 원복 불요:
kubectl -n dms exec deploy/dms-api -c api -- env DMS_LDAP_REQUIRE_AUTH_BIND=true \
  python -c "
import os
from dms.config import Settings
Settings.from_env(os.environ)"
# 기대: SettingsError 트레이스백에 'DMS_LDAP_BIND_DN … missing or a placeholder'
# 와 'DMS_LDAP_BIND_PW …' 두 줄, exit 비0 -- 라이브 env(빈 바인드 자격증명)
# 그대로에 플래그만 켠 것이라 "지금 이 플래그를 켜면 기동이 거부된다"의 실증이다.
#
# (c) **보고 필요(자격증명 블록)**: 실제 인증 바인드 전환은 이 세션에 없는 것들이
#     막는다 --
#     ① OpenLDAP(10.10.10.30)에 바인드 서비스 계정(DN+비밀번호) 생성 권한/자격증명,
#     ② 그 DN/PW 를 dms-secrets(DMS_LDAP_BIND_DN/PW)에 주입,
#     ③ 20-config.yaml 의 DMS_LDAP_REQUIRE_AUTH_BIND 를 "true"로 전환(순서 엄수 --
#        ③ 을 먼저 하면 제어면 전체 CrashLoopBackOff, 의도된 시끄러움).
#     ①②가 준비되면 ③ + 재적용만으로 끝난다 -- 코드는 이 슬라이스로 완비.
```

**5. 무회귀 스모크**

```bash
# 포탈 주요 화면(릴리스·대시보드·요청 목록) 로드 무오류. 릴리스 화면에서 정상
# 조건 제출 1건(제어면은 이미 d39 라 same_tag -- 이력만 확인해도 충분) 또는
# releases 이력에서 §2 롤아웃 행이 Applied 인 것으로 갈음. events 에 새 error
# 이벤트가 없는지 확인.
```

실증 통과 후 `docs/superpowers/BACKLOG.md` 갱신(슬라이스 28 완료 기록 + §2.3 세 항목: fail-open→비침묵화 해소, DaemonSet 항목에 **전제 정정**(600s 는 노드-단위 정체 상한, I6)과 실측치 기입, LDAP 항목에 REQUIRE 플래그 완비 + 자격증명 블록 잔여 명시)을 별도 커밋으로 — 플랜 밖 관례.

---

## Self-Review

**1. 과제 커버리지**

| 과제 항목 | 담당 |
|---|---|
| 1: fail-open 뒤집지 않기(비침묵화만) | Task 1 — 검증 강제 로직은 거동 동치 재구성, 기존 fail-open 테스트 2건 초록이 증거. 표면화 수단은 응답 플래그(즉시)+이벤트(영속)+배너(화면) 셋 다 — 이벤트만으로는 포탈 비가시라는 실측(추가 발견 1)이 근거 |
| 1: `:130` same_tag observe fail-open 성격 확인 | 전제 재확인 2 — 같은 침묵 fail-open, 결과 등급 상이(편의 가드) → 범위 밖, 열린 질문 1 |
| 1: 계약 테스트 설계 | T1 백엔드 3건(부분 침묵 granularity·무이벤트 2방향) + T2 프론트 2건 + 기존 reasonCodes 양방향 커버리지가 `tag_unverified` 누락을 잡는다. `release_tag_unverified` 는 계약 밖(추출기 시야 실측) |
| 2: (a) 순수 실증 vs (b) 설정 키 빼기 판단 | **(a)** — 키가 이미 있다(`config.py:55`, 실측). 측정 대상도 정정: 총 수렴이 아니라 노드당 정체(I6 실측) — 「배포·실증」 §3 이 측정 명령·판정 기준·기록 항목까지 담는다. 코드 0 |
| 3: 자격증명 없이 가능한 코드 강화 판단 | **가치 있음 → Task 3** — REQUIRE 플래그 + 기동 거부. 자리표시자까지 걸러 신규 발견(bind 무검증) 동반 해소. 자격증명 필요 지점은 「배포·실증」 §4-(c)에 ①②③ 로 명시 |
| 3: 자격증명이 실제로 막는 지점 명확화 | 실 인증 바인드 전환의 ①(LDAP 서버 측 계정)·②(dms-secrets 주입)만 블록 — 코드·플래그·검증·실증(b)까지는 이 슬라이스가 끝낸다 |
| 배포·실증(제어면 d39·스키마 무변경 확인·LDAP 블록 보고) | 「플랜 이후」 §1~5 — migrate 재실행 불요 명시, 에이전트 d39 는 측정 수단, §2·§3 을 한 롤아웃으로 겸한다 |

**2. 뮤테이션(이빨) 매트릭스** — T1: `unverified.append` → `pass`(fail-open 거동 유지, 추적만 제거 — "고치는 척"의 모형) → 미검증 테스트만 RED, 기존 fail-open 테스트 초록(기존 그물이 침묵을 못 본다는 실증). T2: 배너 JSX 삭제 → 배너 테스트만 RED. T3: from_env 검증 블록 삭제 → 거부 테스트 2건 RED(플래그를 켜도 조용히 뜨는, 정확히 이 태스크가 막는 상태), 통과 테스트 초록. 각 Task 1건.

**3. 타입·이름 일관성** — `tag_verified`(백엔드 응답 필드) = `SubmitReleasesResult.tag_verified`(T2 타입) = msw 목 JSON 키 동일 철자. `tag_unverified` 는 reasonCodes.json·api.ts·`reasonText("tag_unverified")` 3곳 동일 철자. `release_tag_unverified` 는 routes_releases 리터럴과 테스트 SELECT 동일 철자. `ldap_require_auth_bind`/`DMS_LDAP_REQUIRE_AUTH_BIND` 는 config.py 필드·env 키·20-config.yaml·테스트 동일 철자. 테스트 이름 6건(`test_submit_reports_unverified_when_repo_is_silent`·`test_submit_verified_when_registry_answers`·`test_rejected_submit_leaves_no_unverified_event`·`test_require_auth_bind_refuses_startup_without_credentials`·`test_require_auth_bind_rejects_placeholder_credentials`·`test_require_auth_bind_passes_with_real_credentials`·`test_anonymous_bind_remains_the_default`)은 각 Step 1 과 뮤테이션 절 동일 철자. 픽스처 `rollout_client`·`db`·상수 `ADMIN` 은 기존 파일의 것 재사용(신설 0).

**알려진 위험 / 판단:**
- **배너의 실효 창은 좁다**(TOCTOU·부분 침묵·API 직접 제출) — 전면 다운이면 드롭다운이 비어 UI 제출 불가. 그래도 만드는 이유: 그 좁은 창이 정확히 "성공처럼 보이는 202" 가 나오는 창이고, API 직접 제출자도 응답 플래그로 같은 신호를 받는다. 한계는 코드 주석과 이 플랜에 박제.
- **`tag_verified` 옵셔널 타입** — 구 서버(d38)와 신 프론트가 겹치는 배포 순간에 필드가 없어도 배너가 조용히 꺼질 뿐 화면이 깨지지 않는다(`?.` + `=== false`).
- **fetch_repo_tags 의 None 은 "침묵"보다 넓다**(형식 불량·`{"tags": null}` 포함, registry.py:44-48) — 배너 문구 "응답하지 않아"가 1% 부정확하지만, 운영자 행동(레지스트리 확인)은 동일해 그대로 둔다. 열린 질문 4.
- **REQUIRE 플래그 기본 false** — 테스트베드의 실 구성이 익명 바인드라 true 기본은 배포 즉사다. 기본값 유지 테스트(`test_anonymous_bind_remains_the_default`)가 이 결정을 박제한다.
- **`ldap_require_auth_bind` + LDAP 미설정(uri 공란) 조합은 에러가 아니다** — 바인드 자체가 없으니 "익명 바인드로 도는" 상태도 없다(공허 충족). 플래그의 계약은 "바인드가 일어난다면 반드시 인증"이 아니라 "자격증명 없이 인증 바인드를 의도하지 마라"다 — 주석에 안 박고 이 플랜에 기록(코드가 단순한 쪽을 택했다).
- **에이전트 d39 는 내용 무변경 재빌드** — 측정을 위한 태그다. IfNotPresent 라 같은 태그 재적용은 no-op 이므로 반드시 **새** 태그여야 하고(same_tag 가드도 이미지 문자열 비교라 새 태그면 통과), 빌드 파드 1회에 dms 와 함께 실어 d39 로 통일한다.
- **전체 수치 기대(≈1266·257)는 근사 명시** — 어긋나면 재계산하되 failed 0 이 판정 기준.

## 결정이 필요한 열린 질문

1. **same_tag observe fail-open(`routes_releases.py:128-132`)도 표면화할 것인가** — 같은 침묵 구조지만 최악이 "no-op 롤아웃 Applied 오표시"라 등급이 낮고, targets 의 current_image null 이 부분 노출한다. 응답에 두 번째 플래그(`current_checked`)를 얹으면 대칭적이지만 이번 항목("태그 검증 fail-open")의 범위 밖 — 다음 위생 슬라이스 후보로 남긴다.
2. **관리자 이벤트 화면 부재** — `release_tag_unverified`·`db_reconnected` 류(request_id 없는 이벤트)는 포탈 어디에도 안 뜨고 DB 조회로만 보인다. 운영 이벤트 피드 화면은 별도 슬라이스 감이다(이 플랜은 응답 플래그+배너로 즉시 가시성을 확보했으므로 블로커 아님).
3. **레지스트리 전면 다운 중 UI 제출 불가**(드롭다운 공백)를 "자유입력 태그 필드"로 뚫을 것인가 — fail-open 을 UI 까지 확장하는 결정이라 보류. 지금은 curl(API 직접)이 그 창의 우회로이고 응답 플래그가 신호를 준다.
4. **`fetch_repo_tags` 의 None 의미 폭** — 연결 실패와 "응답했으나 형식 불량"을 한 값으로 접는 현 계약(registry.py 모듈 주석의 명시적 설계)이 비침묵화 이후에도 적절한가. 이벤트에 원인 구분을 실으려면 registry.py 반환 계약 변경이 필요해 범위 밖.
5. **인증 바인드 전환 시 익명 검색 차단을 LDAP 서버 쪽에서도 할 것인가** — 서버가 익명 검색을 계속 허용하면 DMS 만 인증 바인드로 바꿔도 정보 노출면은 남는다. DMS 코드 밖(OpenLDAP ACL) 결정이라 자격증명 발급 시점에 함께 판단할 것.
