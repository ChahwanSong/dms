# 슬라이스 15 — runner의 files/bytes 파싱 설계

슬라이스 14가 깔아 둔 `data_jobs.files_count`/`bytes_count` 컬럼과 `set_artifact`
승격 파이프라인에 실값을 공급한다. runner가 mpifileutils 출력에서 항목수·바이트수를
뽑아 `summary.json`에 넣으면, 이미 배포된(d24) `set_artifact`가 컬럼을 채우고
대시보드가 표시한다.

## 1. 실측으로 확인한 전제 (테스트베드 실 아티팩트)

- 잡 런처는 `DMS_JOB_IMAGE`(`dms-mpifileutils:job3`)의 `/usr/local/bin/dms-job-runner`
  (`src/dms_job_runner/runner.py`)를 실행한다. `set_artifact`(제어면, d24 배포됨)는
  `result_summary`의 **`files`/`bytes` 키를 평범한 비음수 int** 로만 승격한다
  (`data_jobs.py _as_count`: bool·비int·음수 거부).
- 현재 `_summary_from_stdout`(runner.py:106)은 stdout 마지막 줄 JSON 디코드 시도 후
  실패하면 `{"returncode": rc}` — 실 mpifileutils 는 JSON 을 안 찍으므로 실 잡의
  summary.json 은 전부 `{"returncode": 0}` 이고 컬럼은 NULL 이다(실측 확인).
- **이 빌드의 mpifileutils 는 요약을 stdout 에 찍는다** (stderr 는 SSH 경고뿐 — 실측).
  실 출력(잡 60d24700, 0844e3a3, 8464cdd4):
  - dsync 최종 요약 블록(stdout 끝):
    ```
    [ts] Items: 10
    [ts]   Directories: 3
    [ts]   Files: 7
    [ts]   Links: 0
    [ts] Data: 50.000 B (50 bytes)
    [ts] Rate: 0.991 KiB/s (050 bytes in 0.049 seconds)
    ```
    주의: 같은 stdout 에 walk 단계 요약(`Items     : 0`, 콜론 앞 패딩), 복사 단계
    중간 요약(`Items: 10` + `Data: 50.000 B (7.000 B per file)`),
    `Copy data: 50.000 B (50 bytes)` 도 존재한다. **마지막 매치가 최종 블록이다.**
  - drm: `Removed 1 items` (bytes 미보고). `Walked N items` 줄도 섞여 있다.
  - dscan: stdout 은 사람용 목록. 구조화 수치는 `{artifact_dir}/dscan-report.json`
    의 `summary.total_entries`(=10)·`total_files`(=7). **총 바이트는 리포트에 없다**
    (크기 히스토그램뿐).
- 도구명은 runner 가 이미 env `DMS_JR_TOOL` 로 안다. artifact_dir 도 안다.

## 2. 핵심 결정

### 2.1 파싱은 runner 에서 한다 (제어면 아님)

runner 는 summary 의 생산자이고, stdout 을 메모리에 쥐고 있으며, 도구명을 안다.
변경이 **잡 이미지 하나에 갇힌다**. 기각: 컨트롤러가 stdout.log 를 FS 재읽기(제어면
재빌드+로직 분산), legacy 식 `find|wc` 재계산(이중 walk, rm 은 파일이 사라져 불가).

### 2.2 files 의 의미는 "항목(items)"으로 통일한다 (사용자 확정)

- dsync/nsync = 최종 `Items: N` (디렉터리+파일+링크 전체)
- dscan = `summary.total_entries`
- drm = `Removed N items`
drm 이 파일/디렉터리 구분 없이 items 만 내므로 3도구 교차 일관성이 가장 좋다.
UI 라벨을 "처리 항목/바이트"로 맞춘다(§5).

### 2.3 기존 "마지막 줄 JSON" 계약은 제거한다

실 도구는 JSON 을 찍지 않아 사문이다. 그 계약을 검증하던 runner 테스트는 새 계약
(도구별 파싱)으로 갱신한다. summary 는 항상
`{"returncode": rc, "files": <int|null>, "bytes": <int|null>}` 3키를 명시적으로 담는다.

## 3. 구성요소와 계약

`src/dms_job_runner/parsers.py` (신규) — 순수 함수, I/O 없음(scan 만 파일 읽기):

- `parse_sync_counts(stdout: str) -> tuple[int | None, int | None]`
  - files: 정규식 `r"Items: (\d+)\s*$"` (멀티라인, 콜론 앞 패딩 없는 형태만) 의
    **마지막** 매치. walk 단계의 `Items     : 0`(패딩)은 자연 배제되고, 복사 단계와
    최종 블록 중 마지막(=최종)이 잡힌다.
  - bytes: 정규식 `r"\((\d+) bytes\)"` 의 **마지막** 매치. `(50 bytes in …)` 류는
    닫는 괄호가 바로 안 와 매치되지 않으므로 `Copy data:`/최종 `Data:` 만 잡히고
    마지막(=최종 Data)이 남는다.
- `parse_rm_counts(stdout: str) -> tuple[int | None, None]`
  - files: `r"Removed (\d+) items"` 의 마지막 매치. bytes 는 항상 None.
- `parse_scan_counts(report_path: str) -> tuple[int | None, None]`
  - `dscan-report.json` 을 읽어 `summary.total_entries` 가 비음수 int 면 그 값.
    파일 없음/JSON 깨짐/키 없음/타입 이상 → None. bytes 는 항상 None.

`runner.py`: `_summary_from_stdout(stdout, rc)` → `_build_summary(tool, stdout, rc,
artifact_dir)` 로 교체. 디스패치: dsync·nsync → parse_sync_counts, drm →
parse_rm_counts, dscan → parse_scan_counts, 미지 도구 → (None, None).
execution·preview 두 단계 모두 동일하게 적용한다(preview 의 files/bytes 는
`set_preview` 가 무시하지만 dryrun 예상치로서 정보 가치가 있고 분기가 없어 단순하다).

## 4. 오류 처리 — 전면 fail-soft

매치 실패, 리포트 없음, 정규식 불일치, `--quiet` 로 출력 억제 → 해당 값만 null.
returncode 는 항상 보존. **파싱이 잡을 죽이는 경로는 없다** — `_build_summary` 는
예외를 삼키고 `{"returncode": rc, "files": null, "bytes": null}` 로 강등한다.
nsync 출력이 dsync 와 다르면(미확인 가정) 같은 fail-soft 로 null 이 될 뿐이다.
`_as_count` 가 2차 방어(bool/비int/음수 거부)로 이미 배포되어 있다.

## 5. UI 미세조정 (제어면 1줄)

`JobStatsSection.tsx` 의 라벨 `처리 파일/바이트` → `처리 항목/바이트` (items 의미
정합). 해당 라벨을 단언하는 vitest 1건 갱신. 이것만이 제어면(dms 이미지) 변경이다.

## 6. 테스트

- **실 캡처 출력을 픽스처로**: §1 의 dsync 전체 stdout(중간/최종 요약 공존), drm
  (`Removed 1 items` + `Walked` 혼재), dscan-report.json(실 스키마)을 파이썬 테스트
  상수/파일로 고정.
- 파서 단위: 최종 블록이 마지막 매치로 잡히는지(중간 요약 배제), 패딩 `Items     :`
  배제, `(N bytes in …)` 오탐 배제, 빈/무관 stdout → None, 리포트 깨짐 → None.
- runner 통합: 기존 test_job_runner_runner.py 의 JSON-계약 테스트를 새 계약으로
  교체 — 도구별 summary.json 3키 형태, fail-soft.
- 승격 경로는 기존 test_repo_data_jobs.py 가 이미 커버(변경 없음).

## 7. 배포·실증 (테스트베드)

1. `IMAGES=mpifileutils TAG=job4` 빌드/푸시 → `20-config.yaml` 의 `DMS_JOB_IMAGE`
   job3→job4 반영·apply → controller rollout restart(envFrom 재주입).
2. 프론트 라벨 포함 `dms:d25` 빌드 → api/controller set image.
3. 신규 잡 실증: sync·scan·rm 각 1건 제출 → 완료 후 `data_jobs.files_count/
   bytes_count` 채워짐 확인(sync 는 둘 다, scan·rm 은 files 만, bytes null),
   `metrics/jobs` 의 `files_total/bytes_total` 이 null 에서 실값으로, 대시보드
   "처리 항목/바이트" 표시 확인.

## 8. 이 슬라이스에서 하지 않는 것

- 기존 잡 43건의 소급 백필(신규 잡부터 채워진다).
- 프리뷰 카운트의 DB 승격(`set_preview` 는 지문만 — 현행 유지).
- 파일별 상세·에러 카운트·전송률 등 추가 지표.
- dscan 총바이트(리포트에 없음 — 리포트 스키마 확장은 별도 슬라이스).
- mpifileutils 버전 교체·`--quiet` 기본값 변경.
