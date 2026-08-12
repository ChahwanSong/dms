# DMS 포탈 — 슬라이스 8 (사용자 scan 경로 등록 + 서브트리 통계 조회) 설계

2026-08-05. 상위 스펙 `2026-08-02-dms-clean-slate-design.md` §8(포탈 — 사용자)의 하위 구현
문서. 슬라이스 1~7은 구현·실증·배포 완료. 충돌 시 상위 스펙이 이긴다.

## 0. 배경 & 범위

상위 스펙 §8은 사용자 인터페이스를 이렇게 규정한다:

> 사용자는 scan을 직접 실행하지 않는다 — (storage, 경로)를 등록해 두면(`user_scan_paths`),
> 그 경로를 커버하는 **최신 완료 scan의 해당 서브트리 통계**를 조회한다. 등록 경로의 소유권
> 검증은 하지 않는다 (노출되는 것은 파일 수·용량·온도 히스토그램 같은 **집계 통계뿐**이다).

`user_scan_paths` 테이블은 마이그레이션에 있지만 **사용처가 0건**이다. 슬라이스 6에서 scan
제출을 관리자 전용으로 못박았으므로, 사용자가 자기 데이터의 통계를 보는 경로는 이것이 유일하다.
슬라이스 5가 아티팩트 읽기를 열어 이제 구현 가능하다.

### 0.1 실측으로 확인한 제약 — 설계를 좌우한다

테스트베드의 실제 `dscan-report.json` 구조:

```
directory, generated_at_epoch, top_k, thresholds,
summary{total_entries,total_files,total_directories,total_symlinks,total_other},
file_size_histogram[{bucket,lower_inclusive,upper_inclusive,count}],
time_histograms{atime,mtime,ctime: [{bucket,min_age_days,max_age_days,bytes}]},
oldest{atime,mtime,ctime: [{path,type,size_bytes,atime,mtime,ctime}]},
broken_paths[]
```

두 가지가 중요하다:

1. **서브트리별 분해가 없다.** 리포트는 스캔한 디렉터리 **전체**의 집계다. 등록 경로가 스캔
   대상보다 하위면 그 경로만의 통계는 **산출 불가**다(dscan 포크의 출력 형식을 바꿔야 하는데
   그건 잡 이미지 소관이라 범위 밖).
2. **`oldest`에는 구체적인 파일 경로가 담긴다**(`/cephfs/dms/team/file1.txt` 등). 스펙이
   "집계 통계뿐"이라 못박았으므로 이 필드는 **노출하지 않는다**.

### 0.2 담는 것

- **`user_scan_paths` CRUD**(백엔드 신규) — 로그인 사용자가 자기 경로를 등록/목록/삭제.
- **커버링 scan 통계 조회**(백엔드 신규) — 등록 경로를 커버하는 최신 성공 scan을 찾아
  **집계만** 반환.
- **사용자 화면**(`/scan-paths`) — 등록/삭제 + 통계(요약·크기 히스토그램·온도 히스토그램).

### 0.3 비목표

- **경로별(서브트리) 정확 집계** — dscan 리포트에 분해가 없다(§0.1). 대신 커버 관계를 **명시**
  한다(정확 일치인지 상위 디렉터리 기준인지).
- `oldest`·`broken_paths` 노출 — 스펙이 금지한 구체 경로 정보.
- 사용자의 scan 제출 — 슬라이스 6에서 관리자 전용으로 확정.
- 등록 경로의 소유권/존재 검증 — 스펙이 명시적으로 하지 않는다고 규정.
- 통계 캐싱·시계열 — §9 대시보드 소관.

## 1. 화면 지도

| 화면 | 경로 | 내용 |
|---|---|---|
| 내 스캔 경로 | `/scan-paths`(신규) | 등록 폼 + 목록 + 행별 통계 열기/삭제 |

로그인 사용자면 누구나(관리자 포함). 내비의 사용자 영역에 추가한다.

## 2. 백엔드

### 2.1 `user_scan_paths` CRUD

```
GET    /api/user/scan-paths                 -> [{id, storage_name, path, created_at}]
POST   /api/user/scan-paths  {storage_name, path}  -> 201 {id, ...}
DELETE /api/user/scan-paths/{id}            -> 200
```

- 전부 `require_user`. **항상 `identity.actor`의 행만** 다룬다 — 다른 사용자의 행은 조회·삭제
  불가(`404 scan_path_not_found`).
- `path`는 `validate_relative_path`로 검증한다(절대경로·`..`·빈 문자열 거부 → `422 unsafe_path`).
  스토리지 상대 경로라는 계약이 잡 제출과 동일해야 커버 판정이 성립한다.
- `storage_name`은 **활성 스토리지 목록에 있어야** 한다(`422 storage_missing`). 슬라이스 6의
  `GET /api/user/storages`와 같은 기준.
- `UNIQUE (username, storage_name, path)` 위반 → `409 scan_path_exists`.
- 신규 리포지토리 `UserScanPathsRepository`(`src/dms/repositories/scan_paths.py`).

### 2.2 커버링 scan 통계 조회

```
GET /api/user/scan-paths/{id}/stats
    -> 200 {covered_by: {target, exact}, generated_at_epoch, summary,
            file_size_histogram, time_histograms}
    -> 404 no_covering_scan
```

**커버링 scan 찾기** — 아티팩트를 뒤지지 않고 **DB만으로** 후보를 좁힌다:

1. `data_jobs`에서 `operation = 'scan'`, `state = 'Succeeded'`, `storage_name = <등록 스토리지>`
   인 잡을 최신순으로 조회한다.
2. 각 잡의 `target`(스토리지 상대 경로)이 등록 경로의 **조상-또는-동일**인지 판정한다:
   `t == p or p.startswith(t + "/")` (둘 다 `posixpath.normpath`로 정규화). 순수 함수로 빼서
   단위 테스트한다.
3. 첫 번째 매치의 `execution/dscan-report.json`을 슬라이스 5의 아티팩트 헬퍼로 읽는다.
4. 읽기 실패(파일 없음·크기 초과 등)면 다음 후보로 넘어간다. 후보가 다 떨어지면
   `404 no_covering_scan`.

**노출 필드 화이트리스트** — 리포트를 그대로 흘리지 않고 **골라 담는다**:
`summary`, `file_size_histogram`, `time_histograms`, `generated_at_epoch`.
`oldest`·`broken_paths`·`directory`·`thresholds`·`top_k`는 **제외**한다. `directory`는 절대
경로라 마운트 배치를 드러내고, `oldest`는 구체 파일 경로다.

**커버 관계 명시**: `covered_by = {target: <스캔 대상 상대경로>, exact: <등록 경로와 동일한가>}`.
`exact=false`면 프론트가 "상위 경로 `<target>` 기준 집계 — 이 경로만의 통계가 아닙니다"를
반드시 보여준다. 통계를 서브트리 통계인 것처럼 보이게 두면 사용자를 속이는 것이다.

**규모**: 성공한 scan 잡 수는 많지 않고 후보 판정은 DB 필드만 쓴다. 아티팩트 읽기는 매치된
1건에 대해서만 일어난다. 조회 상한(예: 최신 200건)만 둔다.

### 2.3 기존 그대로

`data_jobs`·`requests` 리포지토리, 슬라이스 5의 `artifacts.py`(읽기 헬퍼와 그 봉쇄 규칙),
스토리지 라우트는 **변경하지 않는다**. 아티팩트 읽기는 슬라이스 5가 만든 안전한 경로를 재사용한다.

## 3. 프론트엔드

### 훅
- `features/scanpaths/useScanPaths.ts` — `useScanPaths()`, `useAddScanPath()`,
  `useDeleteScanPath()`(각각 `["scan-paths"]` 무효화), `useScanPathStats(id, enabled)`
  (지연 로드, `["scan-path-stats", id]`).

### 화면 (`ScanPaths.tsx`, `/scan-paths`)
- 등록 폼: 스토리지 드롭다운(슬라이스 6의 `useUserStorages` 재사용) + 경로 입력 + "등록".
- 목록 테이블: 스토리지 / 경로 / 등록일 / 작업(통계 보기·삭제).
- "통계 보기"를 누른 행만 `useScanPathStats(id, true)`로 조회한다(지연 로드).
- 통계 패널:
  - `covered_by.exact === false`면 상단에 안내(위 §2.2 문구).
  - 요약 카드(총 항목/파일/디렉터리/심링크).
  - 크기 히스토그램·온도 히스토그램(atime/mtime/ctime) — 표로 렌더한다. 차트는 §9 대시보드
    슬라이스에서 도입하고 여기서는 도입하지 않는다(YAGNI).
  - `404 no_covering_scan`이면 "아직 이 경로를 커버하는 scan 결과가 없습니다 — 관리자가 scan을
    실행하면 표시됩니다".

### 배선/타입
- `router.tsx`: `/scan-paths`(RequireRole 기본 = 로그인 사용자).
- `AppShell.tsx`: 사용자 내비에 "내 스캔 경로" 추가(관리자에게도 보인다).
- `lib/types.ts`: `ScanPath`, `ScanPathStats` 추가.
- `lib/api.ts` reason 코드: `scan_path_exists`, `scan_path_not_found`, `no_covering_scan`.

## 4. 테스트

- **백엔드**: 커버 판정 순수 함수(동일/하위/무관/정규화 케이스); CRUD 소유권(타인 행 404),
  중복 409, 잘못된 경로 422, 미등록 스토리지 422; 통계 조회가 **화이트리스트 필드만** 반환하고
  `oldest`·`broken_paths`·`directory`가 **없음**을 명시 단언; 커버 scan 없으면 404; 상위 스캔이
  커버하면 `exact=false`; 리포트 파일이 없으면 다음 후보로 넘어감.
- **프론트**: 목록/등록/삭제; "통계 보기" 누르기 전에는 요청 없음; `exact=false` 안내 렌더;
  `no_covering_scan` 안내; 히스토그램 표 렌더.

## 5. 배포/실증

마이그레이션 변경 없음(테이블은 이미 있다) → migrate 재실행 불필요. 컨트롤러 변경도 없다 →
이미지 d17로 api·controller 갱신(단일 이미지).

실증: 사용자로 로그인 → 경로 등록 → 커버 scan 없으면 안내 → 관리자가 그 경로 상위로 scan 실행
→ 사용자가 통계 조회(요약·히스토그램) → 응답에 `oldest`/`directory`가 **없음**을 확인 →
정확 일치 scan을 돌려 `exact=true`가 되는지 → 타인 경로 접근 404.

## 6. 결정 기록

- 서브트리 정확 집계는 **불가능**(dscan 리포트에 분해 없음) — 커버 관계를 `exact` 플래그로
  **명시**하고 UI가 상위 기준임을 알린다. 속이지 않는다.
- 노출은 **화이트리스트**(summary/histograms/generated_at)로 하고 `oldest`·`broken_paths`·
  `directory`는 제외한다 — 스펙의 "집계 통계뿐" 요구.
- 커버링 scan 탐색은 **DB 필드로 후보를 좁히고** 매치 1건만 아티팩트를 읽는다.
- 차트는 §9 슬라이스로 미룬다 — 여기서는 표.
- 등록 경로의 소유권·존재 검증은 하지 않는다(스펙 명시).
