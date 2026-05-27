# DMS Phase 2 Implementation Prompt

이 문서는 `docs/dms-phase1.md` 완료 이후 두 번째 구현 에이전트에게 전달할 compact 구현 프롬프트다. Phase 2의 목표는 Phase 1 skeleton을 실제 테스트베드 PostgreSQL과 중앙 LDAP identity system에 연결해, DMS의 source-of-truth persistence와 requester POSIX identity mapping을 live 검증하는 것이다.

Phase 2는 기능을 넓히지 않는다. 실제 storage quota, Kubernetes ResourceQuota mutation, Volcano live execution, filesystem quota adapter 구현은 다음 phase로 미룬다. 이번 phase는 이후 기능들이 의존할 운영 DB 기반과 중앙 identity 기반을 먼저 단단히 닫는다.

## Phase 2 목표

Phase 2에서는 다음 두 축만 구현한다.

1. PostgreSQL live baseline
2. LDAP-only Identity Mapping integration

구현 완료 기준은 다음과 같다.

- DMS migration, API, Planner, RM Worker, DM Worker, Operational Query가 테스트베드 PostgreSQL을 실제 운영 DB로 사용한다.
- Observability/log DB는 운영 DB와 분리된 PostgreSQL database 또는 schema로 검증 가능해야 한다.
- Phase 1의 request/plan/run/result lifecycle 계약이 PostgreSQL에서도 동일하게 통과한다.
- Identity Mapping API는 중앙 LDAP을 read-only authoritative source로 직접 조회한다.
- Identity Mapping 구현과 검증은 로컬 OS 계정, local NSS, local `/etc/passwd`, local group file, local-only mock identity를 사용하지 않는다.
- SSSD/getent/id 기반 검증은 Phase 2 Identity Mapping의 성공 기준으로 인정하지 않는다. 필요하면 테스트베드 상태 진단용 보조 확인으로만 남길 수 있고, DMS Identity Mapping 자체는 LDAP direct read 결과로 판단한다.
- LDAP에 user, group, group membership을 생성, 수정, 삭제하는 코드는 만들지 않는다.

## 현재 전제

Phase 1은 다음 골격을 이미 제공한다.

- API server / Frontend
- Planner
- RM Worker runtime
- DM Worker runtime
- DMS Agent report ingestion
- Operational Query skeleton
- request/plan/run/result persistence
- data job state machine skeleton
- stub filesystem, Kubernetes quota, Volcano adapter
- SQLite 기반 unit/integration contract tests

테스트베드에는 다음 시스템이 준비되어 있어야 한다.

- Vagrant VM 기반 Kubernetes multi-cluster testbed
- OpenLDAP
- PostgreSQL on `cluster-a`
- Volcano
- local registry

PostgreSQL 테스트베드 정보는 `/home/mason/workspace/testbed/PostgreSQL.md`를 기준으로 확인한다.

## Phase 2A: PostgreSQL Live Baseline

### 목적

Phase 1의 DB-API abstraction이 실제 PostgreSQL에서도 동작한다는 것을 확인한다. 이 단계는 Identity Mapping보다 먼저 수행한다. LDAP mapping이 운영 DB에 저장되므로 PostgreSQL live baseline이 닫히지 않은 상태에서 Identity Mapping으로 넘어가면 안 된다.

### 구현 범위

- `DMS_DATABASE_URL`과 `DMS_OBSERVABILITY_DATABASE_URL`을 테스트베드 PostgreSQL에 연결하는 설정 예시를 추가한다.
- 운영 DB와 observability/log DB를 같은 PostgreSQL instance 안의 별도 database 또는 schema로 분리한다.
- `dms migrate`가 PostgreSQL에서 성공해야 한다.
- Phase 1 repository queries가 PostgreSQL DB-API wrapper에서 동작해야 한다.
- SQLite와 PostgreSQL 양쪽에서 테스트 가능한 구조를 유지한다.
- 테스트베드 PostgreSQL 접속 정보와 secret 참조는 문서에 평문 비밀번호 없이 기록한다.

### 필수 검증

PostgreSQL에서 다음 흐름을 검증한다.

- migration 적용
- auth failure는 운영 DB request를 만들지 않고 observability diagnostic event만 기록
- authz failure는 운영 DB에 `AuthorizationFailed` request/result를 만들고 plan/run은 만들지 않음
- filesystem create request 제출
- Planner run-once로 RM plan 생성
- RM Worker run-once로 stub backend 실행 및 result/resource 기록
- data `scan` request 제출
- Planner run-once로 DM plan/data job 생성
- DM Worker run-once로 stub Volcano execution 및 result/data job 기록
- `/api/v1/operations/requests/{request_id}` 또는 동등 query로 lifecycle history 조회
- 운영 DB와 observability DB가 물리적으로 분리되어 있음을 table 존재 여부 또는 connection URL 기준으로 확인

### 산출물

- PostgreSQL live smoke test script 또는 pytest marker
- PostgreSQL용 testbed 실행 명령 문서
- `docs/dms-phase1-verification.md` 또는 Phase 2 검증 문서에서 기존 "PostgreSQL 미검증" gap 해소 기록
- 필요한 경우 `testbed/` 문서 갱신

## Phase 2B: LDAP-only Identity Mapping

### 목적

DMS `requester_id`를 중앙 LDAP에서 조회 가능한 POSIX identity로 연결한다. 이 mapping은 이후 Resource Management filesystem access control과 Data Management POSIX preflight의 공통 기반이다.

### 절대 원칙

Phase 2 Identity Mapping은 중앙 LDAP을 유일한 authoritative identity source로 사용한다.

금지:

- local `/etc/passwd` 또는 `/etc/group`을 source of truth로 사용
- local NSS 조회를 DMS mapping 판정에 사용
- `getent`, `id`, `id -G`, shell command 결과를 DMS Identity Mapping 성공 기준으로 사용
- 테스트 fixture나 mock만으로 Identity Mapping 완료 처리
- LDAP에 user/group/group membership write 수행
- LDAP 조회 실패 시 local fallback으로 Active mapping 생성

허용:

- LDAP direct read-only query
- 테스트베드 health check 차원에서 OpenLDAP/SSSD 상태를 별도로 확인
- unit test에서 LDAP client interface를 fake로 대체해 error handling을 검증

단, Phase 2 완료 검수는 반드시 테스트베드 OpenLDAP에 대한 실제 LDAP read-only integration test를 포함해야 한다. Fake/mock 테스트만으로 Phase 2 Identity Mapping을 완료 처리하면 안 된다.

### 구현 범위

- LDAP direct read adapter를 구현한다.
- 설정은 환경변수 또는 config로 주입한다.
  - LDAP URI
  - base DN
  - bind DN 또는 anonymous bind 정책
  - bind credential secret reference
  - user search base/filter
  - group search base/filter
  - uidNumber/gidNumber/memberUid/member/memberOf attribute mapping
  - timeout
- Identity Mapping upsert는 LDAP에서 `posix_username`을 조회해 UID, primary GID, supplementary groups를 계산한다.
- 요청 payload의 expected UID/GID/groups가 제공되면 LDAP 조회 결과와 비교한다.
- 일치하면 `Active`로 저장한다.
- 불일치하면 저장하지 않거나 `NeedsReview`로 저장한다. 이 상태는 Data Management job과 filesystem access control에 사용하면 안 된다.
- refresh는 LDAP을 다시 조회한다.
- refresh 중 중앙 LDAP 값이 기존 mapping과 다르면 기존 UID/GID/group 값을 조용히 덮어쓰지 않고 `Stale`, `stale_at`, `mismatch_reason`을 기록한다.
- disable은 `Disabled`, `disabled_at`, reason을 기록한다.
- `Disabled` mapping은 refresh로 되살리지 않는다. 다시 사용하려면 명시적 upsert가 필요하다.
- list/query는 `requester_id`, `identity_provider`, `status`, `failed=true` 필터를 지원한다.

### 데이터 모델 보강

Phase 1 schema에 부족한 필드가 있으면 migration을 추가한다.

필수 표현:

- `requester_id`
- `identity_provider`
- `posix_username`
- UID
- primary GID
- supplementary group list
- mapping status
- LDAP lookup timestamp
- verified_at
- stale_at
- disabled_at
- verification result
- mismatch reason
- disabled reason
- LDAP source metadata

마이그레이션은 expand-compatible하게 작성한다. 기존 Phase 1 SQLite tests를 깨뜨리면 안 되며, PostgreSQL에서도 같은 migration이 적용되어야 한다.

### API 기대 동작

Endpoint 이름은 구현자가 기존 코드 스타일에 맞춰 조정할 수 있다. 단 capability는 유지한다.

- upsert/register mapping
- refresh mapping
- disable mapping
- list/query mappings

Upsert 입력 예:

```json
{
  "requester_id": "portal:alice",
  "identity_provider": "ldap-main",
  "posix_username": "alice",
  "expected_uid": 10000,
  "expected_primary_gid": 10000,
  "expected_groups": ["developers"]
}
```

성공 결과는 LDAP에서 읽은 값을 기준으로 한다. 요청자가 보낸 expected value를 그대로 저장하면 안 된다.

### 테스트베드 검증

테스트베드 OpenLDAP의 기존 사용자와 group을 사용한다.

최소 검증:

- `portal:alice -> alice` upsert 성공, `Active`
- LDAP에서 읽은 UID/GID/group이 운영 DB에 저장됨
- `portal:bob -> bob` upsert 성공, `Active`
- expected UID mismatch 요청은 `NeedsReview` 또는 rejected result
- 존재하지 않는 `posix_username` 요청은 실패 또는 `NeedsReview`
- refresh가 LDAP을 다시 조회하고 `verified_at`을 갱신
- disable 후 list/query에서 `Disabled` 확인
- disabled mapping이 refresh로 Active가 되지 않음
- `failed=true` 또는 status filter로 `NeedsReview`/`Stale`/`Disabled` 조회

검증은 LDAP direct query adapter를 통해 수행한다. `getent passwd alice`, `id alice` 같은 local command 결과는 Phase 2 Identity Mapping 검증 evidence로 쓰지 않는다.

## Phase 2에서 하지 않을 것

- 실제 filesystem directory create/update/block
- 실제 filesystem quota 적용
- 실제 Kubernetes ResourceQuota create/update/delete
- 실제 VolcanoJob create/watch/terminate
- Data Management POSIX preflight enforcement
- Agent report 기반 effective scheduler 구현
- mTLS Ingress live validation
- rolling upgrade, shutdown/startup recovery runbook 구현

이 항목들은 Phase 2 이후의 기능 phase로 분리한다.

## 구현 순서

1. 테스트베드 PostgreSQL 상태와 접속 정보를 확인한다.
2. PostgreSQL operational DB와 observability DB 분리 구성을 만든다.
3. PostgreSQL migration smoke test를 추가한다.
4. PostgreSQL 기반 Phase 1 lifecycle smoke test를 추가한다.
5. LDAP direct read adapter interface와 설정을 추가한다.
6. Identity Mapping upsert/refresh/disable/list를 LDAP direct read 기준으로 구현한다.
7. Identity Mapping migration이 필요한 경우 expand-compatible하게 추가한다.
8. LDAP live integration test를 작성한다.
9. PostgreSQL + LDAP 통합 smoke test를 테스트베드에서 실행한다.
10. 검증 결과를 문서화한다.

## Phase 2 검증 매트릭스

| Area | Required verification | Expected evidence |
| --- | --- | --- |
| PostgreSQL migration | `dms migrate`가 테스트베드 PostgreSQL에서 성공한다. | migration version row, no SQLite-only assumption |
| DB separation | operational DB와 observability DB가 분리된다. | operational tables와 diagnostic_events 저장소 분리 |
| Lifecycle on PostgreSQL | request -> plan -> run -> result가 PostgreSQL에서 동작한다. | request, plan, run, result rows |
| Auth failure | 인증 실패는 request lifecycle에 들어가지 않는다. | observability diagnostic event only |
| Authz failure | 인가 실패는 `AuthorizationFailed` terminal result다. | no plan, no run, no backend side effect |
| RM stub smoke | filesystem create가 Planner/RM Worker를 거쳐 성공한다. | resource desired/applied/observed row |
| DM stub smoke | data scan이 Planner/DM Worker를 거쳐 성공한다. | data_jobs and result rows |
| LDAP direct lookup | Identity Mapping이 중앙 LDAP을 직접 read-only 조회한다. | LDAP query evidence, no local fallback |
| LDAP no-write | DMS가 LDAP user/group/group membership을 수정하지 않는다. | code path and test evidence |
| Active mapping | alice/bob mapping이 LDAP 값으로 Active 저장된다. | UID/GID/groups from LDAP in operational DB |
| Mismatch handling | expected UID/GID/group mismatch가 Active로 저장되지 않는다. | NeedsReview or rejected result |
| Refresh drift | refresh mismatch는 기존 값을 덮어쓰지 않고 Stale로 기록한다. | stale_at, mismatch_reason |
| Disable | Disabled mapping은 refresh로 Active가 되지 않는다. | disabled_at, status remains Disabled |
| Query filters | status/failed filter가 동작한다. | list response |

## Phase 2 문서 산출물

다음 문서를 구현 결과와 함께 최신화한다.

- `docs/dms-phase2-verification.md`: 실행한 PostgreSQL/LDAP test, 명령, 주요 output, 성공/실패 결과
- `docs/usage.md`가 이미 있다면 Identity Mapping 사용 예시 추가. 없다면 최소한 Phase 2 범위의 사용 예시는 verification 문서에 남긴다.
- `testbed/PostgreSQL.md`: PostgreSQL 접속/검증 정보가 바뀐 경우 갱신
- `testbed/OpenLDAP-SSSD.md`: LDAP 접속/테스트 사용자/group 정보가 바뀐 경우 갱신

## 구현 및 검증 진입점

Phase 2 구현의 주요 진입점은 다음과 같다.

- `src/dms/adapters.py`: `LdapIdentityLookupAdapter` direct read-only LDAP lookup
- `src/dms/api.py`: Identity Mapping upsert/refresh/disable/list API
- `src/dms/repositories.py`: Identity Mapping status, verification, stale, disable persistence
- `src/dms/migrations.py`: Phase 2 identity migration columns
- `tests/test_phase2_identity.py`: LDAP lookup boundary와 status transition unit tests
- `scripts/phase2_postgres_ldap_smoke.py`: PostgreSQL + LDAP live smoke body
- `scripts/verify-phase2-testbed.sh`: 테스트베드 PostgreSQL DB 생성, 환경변수 구성, live smoke 실행
- `docs/dms-phase2-verification.md`: 실행 결과와 검증 evidence

대표 검증 명령:

```bash
cd /home/mason/workspace/dms
python3 -m pytest -q
PATH="/tmp/dms-phase2-venv/bin:$PATH" ./scripts/verify-phase2-testbed.sh
```

## 다음 Phase 후보

Phase 2 완료 후 다음 순서는 아래 중 하나를 선택한다.

- Phase 3A: Agent inventory와 `storage_name` mapping sanity
- Phase 3B: Kubernetes namespace storage quota live adapter
- Phase 3C: Data Management preflight와 tool option registry 강화

추천은 Phase 3A다. LDAP Identity Mapping과 PostgreSQL live baseline 이후에는 Agent inventory와 storage mapping sanity를 먼저 닫아야 RM/DM live backend 선택이 안전해진다. Phase 3 구현 프롬프트는 `docs/dms-phase3.md`를 기준으로 한다.
