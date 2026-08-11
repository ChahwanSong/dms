# 슬라이스 26 — 포탈 기능 잔여 설계

백로그 §2.2 의 후보를 전부 하지 않는다. 이 설계의 첫 번째 일은 범위를 자르는 것이다:
**아티팩트 다운로드(스트리밍) + FAST-FOLLOW 6건 + 고급 sync 옵션 폼 + Sparkline 1점**
만 넣고, 나머지(삭제·보존, 배치 CSV 일체, rm 배치)는 §7 에 이유와 함께 남긴다.

## 1. 실측으로 확인한 전제

1. **아티팩트 열람은 256KB 꼬리 텍스트 전용이다.** `read_artifact` 는 파일 끝에서
   `MAX_BYTES`(256KB, `src/dms/api/artifacts.py:34`)만 lseek 로 잘라 읽고(`:204-207`)
   utf-8 `errors="replace"` 로 강제 디코드한다(`:212`). JobViewer 는 그 문자열을
   `<pre>` 로 렌더한다(`JobViewer.tsx:87`). 즉 **전체 파일·바이너리를 얻을 수단이
   포탈에 없다.** 256KB 를 넘은 dscan-report 는 scan-path 통계도 503
   `scan_report_too_large` 로 접힌다(`routes_scan_paths.py:143-146`) — 대체 경로 부재.
2. **artifacts.py 의 보안 불변식**(전부 재확인): 위협 모델은 "요청자가 자기 잡 phase
   디렉터리의 소유자"다(머리주석 `:5-8`). 단일 open `O_NOFOLLOW|O_NONBLOCK`(`:189`),
   fstat `S_ISREG`(`:196-197`), 열린 fd 의 `/proc/self/fd` realpath 봉쇄(`:202`),
   open 실패 전부를 `artifact_not_found` 로 뭉개는 존재 오라클 차단(`:191-193` +
   `routes_artifacts.py:40-44` 의 404 통일), FIFO+스레드풀(~40) DoS 방어 주석
   (`:183-188`), 하드링크 한계 명문화(`:69-77`), `MAX_ENTRIES`/`MAX_SCAN`(`:37,41`).
3. **인증은 세션 쿠키다**(`dms_session`, `api/app.py:44-45`). 쿠키는 `<a href>`
   네비게이션에도 실리므로 다운로드 링크에 fetch/blob 이 필요 없다. 소유권은
   `_owned_job`(`routes_jobs.py:24-33`, admin 은 전체 열람) 재사용.
4. **Starlette 는 동기 제너레이터를 청크 단위로 스레드풀에 위임한다** — 설치본
   0.52.1 실측: `StreamingResponse` 가 `iterate_in_threadpool` 로 감싸고
   (`responses.py:233`), 그것은 청크마다 `to_thread.run_sync(next, it)` 를 부른다
   (`concurrency.py:49-57`). **느린 클라이언트가 스레드를 응답 내내 점유하지 않는다**
   — §1-2 의 FIFO 주석이 경고한 스레드풀 고갈과 다른 프로파일이다. 신규 의존성 0.
5. **아티팩트 파일을 지우는 코드는 여전히 없다.** 저장소 전체에서 unlink 는
   `artifact_base.py:78`(validate 가 만든 자기 프로브 파일 삭제) 하나뿐 — 삭제·보존
   UI 는 "첫 파괴 경로 신설"이라 이 슬라이스 감이 아니다(§7).
6. **고급 sync 옵션은 백엔드가 이미 완비다**: 검증 `domain.py:124-130`(chmod/chown
   정규식 `:112-113`), 플래그 매핑 `execution_manifests.py:12-13`, 사용자가 chown 을
   명시하면 auto-chown 주입이 억제된다(`:69-71`). 프론트는 bool 4종만 노출한다
   (`SubmitJob.tsx:78`) — **`open_noatime` 은 bool 인데도 빠져 있고**(`domain.py:126`),
   `SubmitBody.options` 타입이 string 을 배제해(`useJobs.ts:28`) chmod/chown 을 못 싣는다.
7. **배치는 scan|sync 만이다**(`validate_batch`, `domain.py:222-224` — rm 은 의도적
   거부). BatchCreate 는 textarea + 프론트 파싱(`BatchCreate.tsx:27-29`, `lib/csv.ts`),
   options 고정 `{}`(`BatchCreate.tsx:20`). 파일 업로드·템플릿·내보내기·드롭다운 전무.
8. **FAST-FOLLOW 7건 중 1건은 이미 해소됐다**: RequestDetail 로딩 상태는 존재한다
   (`RequestDetail.tsx:112-119`) — 백로그가 낡았다. 나머지 6건은 전부 실재 확인:
   ① 스토리지 상태는 `Healthy` 가 아니라 **Ready/Degraded** 인데(`reconciler.py:19-27`)
   `StoragesList.tsx:48` 이 잡 전용 `pillVariant`(`jobState.ts:7-12`)로 흘려 **둘 다
   neutral 회색**이다. ② `api.ts` 401 분기가 detail 파싱을 통째로 중복(`:193-201` vs
   `:202-208`). ③ `Login.tsx:30` 무가드 `as ApiError`. ④ 잡 취소 오류 미표시
   (`RequestDetail.tsx:187-192` — 요청 취소 오류는 `:164-166` 에서 표시하면서 잡
   취소만 누락) + ConfirmDialog 가 닫힐 때 `confirm.reset()` 을 안 한다(`:25`).
   ⑤ Home 이 `me.isError` 미확인(`router.tsx:29-34`). ⑥ 무효화 접두 중복
   (`useJobs.ts:43-46,53-56,64-67` — `["request", id]` 무효화가 접두 매칭으로
   `["request", id, "jobs"]` 를 이미 포함한다).
9. **Sparkline 유효점 1개**: `sparklinePath` 가 `"M0,16"` 을 돌려줘 비어 있지 않으므로
   "—" 폴백(`Sparkline.tsx:33-34`)이 불발하고, 선분 0개 path 라 **빈 SVG** 가 그려진다
   (`:13` step=0, `:24`). 새로 뜬 노드의 첫 리포트에서 실제 발생(`NodeMetricsSection.tsx:38`).
10. **다운로드가 여는 추가 위협 표면**: 요청자는 자기 phase 디렉터리 소유자라(§1-2)
    희소(sparse) 초대형 파일과 HTML 파일을 만들 수 있다. `X-Content-Type-Options:
    nosniff` 는 저장소 전체 0건(grep) — 타입을 고정하지 않으면 사용자 HTML 이 포탈
    오리진에서 렌더되는 stored-XSS 경로가 생긴다.

## 2. 핵심 결정

### 2.1 범위 — 사용자 가치×비용으로 4건, 나머지는 자른다

넣는 것: **(a) 아티팩트 다운로드** — 유일한 산출물 획득 경로 부재(§1-1)를 닫는 최고
가치 항목이고, 위협 모델·불변식이 이미 문서화돼 있어 비용이 예측된다. **(b)
FAST-FOLLOW 6건** — 각각 반나절 미만인데 ①⑤ 는 운영 오독을 만든다(§2.4). **(c) 고급
sync 옵션 폼** — 백엔드 완비(§1-6)라 순수 프론트 작업 + 타입 확장. **(d) Sparkline
1점** — 함수 한 곳. 자르는 것과 이유는 §7. 전부 넣으면(특히 삭제·보존과 배치 개편)
실증이 두 방향(파괴 경로 + CSV UX)으로 갈라져 슬라이스가 검증 불능이 된다.

### 2.2 다운로드 — 같은 fd 로 검사하고 같은 fd 로 스트림한다

새 라우트 `GET /api/user/jobs/{job_id}/artifacts/{phase}/{name}/download`
(`require_user` + `_owned_job`). `artifacts.py` 에 `open_artifact_stream(base, job_id,
phase, name) -> (fd, size)` 를 추가한다 — `resolve_artifact_path` 동일 화이트리스트 →
**단일 open** `O_RDONLY|O_NOFOLLOW|O_NONBLOCK` → `fstat` `S_ISREG` → `/proc/self/fd`
봉쇄, 전부 `read_artifact` 와 같은 순서·같은 코드 경로다. **경로 문자열을 다시
해석하지 않는다** — 검사한 fd 그대로를 응답 제너레이터(64KiB 청크, try/finally 로
close)에 넘겨 TOCTOU 불변식이 그대로 유지된다. open/봉쇄 실패는 전부
`artifact_not_found` 404 (오라클 유지, §1-2). FIFO 는 기존과 동일하게 S_ISREG 에서
탈락 — O_NONBLOCK 이라 열기에서 블록하지도 않는다.

**정확히 fstat 시점의 size 만큼만 보낸다**: Content-Length=size 로 선언하고 스트림도
size 바이트에서 멈춘다 — 스트리밍 중 사용자가 파일을 키워도(자기 소유라 가능) 응답이
무한히 자라지 않는다. 반대로 파일이 줄면(truncate) 조기 EOF 로 전송이 Content-Length
미달로 끊긴다 — 헤더는 이미 나갔으므로 정정 불가, 클라이언트에게 **실패한
다운로드로 보이는 정직한 실패**다(조용히 0 을 채워 넣지 않는다). 동시성은 §1-4 대로
청크당 스레드 대여라 느린 클라이언트 40명이 API 를 세우지 못한다.

헤더 3종 고정: `Content-Type: application/octet-stream`, `Content-Disposition:
attachment; filename="<name>"`(NAME_RE `[A-Za-z0-9._-]+` 라 헤더 인젝션이 구성상
불가, `artifacts.py:32`), `X-Content-Type-Options: nosniff` — §1-10 의 HTML 렌더
경로를 셋이 함께 닫는다(inline 표시 절대 금지).

### 2.3 상한 — 뷰 256KB 는 그대로, 다운로드는 별도 상한 413

`MAX_BYTES` 256KB 는 **화면 뷰의 상한**으로 그대로 둔다(JSON 응답에 통째로 실리는
구조라 낮아야 맞다). 다운로드는 새 설정 `DMS_ARTIFACT_DOWNLOAD_MAX_BYTES`(기본
268435456 = 256MiB, `_SERVER_INT_KEYS` 관례 `config.py:9`)를 두고, fstat size 가
넘으면 **열자마자 닫고 413 `artifact_too_large`** 로 거부한다 — 헤더 전 판정이라
절단이 아닌 명시적 실패다. sparse 1TB 파일 공격(§1-10)이 이 한 줄에서 죽는다. 413
은 봉쇄·소유권 검사를 **통과한 뒤에만** 나오므로 존재 오라클이 아니다(자기 잡
디렉터리 안에서만 관측 가능). 정직한 한계: 상한 이하 파일의 반복 다운로드로 인한
대역폭 소진은 못 막는다 — 인증된 사용자의 자원 남용은 rate limit 의 몫이고 §7 이다.

### 2.4 FAST-FOLLOW 6건 — 각각 무엇을 깨뜨리는가

1. **StatusPill/스토리지**: Ready 도 Degraded 도 neutral 회색(§1-8①)이라, 일부 노드
   마운트가 죽은 **Degraded**(`reconciler.py:25-27`)가 정상과 같은 색으로 보인다.
   planner 는 Degraded 스토리지에도 잡을 보내므로(`planner.py:149`) 운영자가 색만
   훑으면 부분 장애를 놓친 채 잡이 느려지거나 실패한다. `storagePillVariant` 를
   별도 신설(빌드 전용 `buildPillVariant` 선례, `jobState.ts:14-24`): Ready→ok,
   Degraded→busy(황색 주의), 그 외→neutral. 공유 매핑은 건드리지 않는다(M5 관례).
2. **api.ts 401 중복**(§1-8②): detail 파싱이 두 벌이라 한쪽만 고치는 드리프트가
   기다린다 — 구조적 detail 처리를 `!res.ok` 쪽에만 넣으면 401 문구가 조용히
   갈라진다. 파싱을 한 번으로 합치고 401 이면 `dms:unauthorized` 만 추가 발화.
3. **Login 무가드 캐스트**(`Login.tsx:30`): fetch 네트워크 단절은 `ApiError` 가 아닌
   `TypeError` 로 reject 되므로 영어 원문("Failed to fetch")이 그대로 노출된다.
   `instanceof ApiError` 분기 + 일반 실패 문구.
4. **잡 취소 오류 미표시**(§1-8④): 취소 실패(409 `cancel_failed` — 문구까지 이미
   등록돼 있다, `api.ts:92`)가 화면에 안 보여, 사용자는 버튼이 무시됐다고 여기고
   잡은 계속 돈다. `cancel.isError` 를 해당 잡 카드에 렌더(`cancel.variables` 로
   어느 잡인지 안다). ConfirmDialog 는 닫기 시 `confirm.reset()` — 지금은 지문
   만료로 실패한 오류가 다이얼로그를 다시 열어도 남아 새 시도 결과와 혼동된다.
5. **Home `me.isError`**(§1-8⑤): `/api/auth/me` 가 일시 500/네트워크 오류면 data 가
   없어 `/jobs` 로 보내고, RequireRole 이 isError 를 `/login` 으로 보낸다
   (`RequireRole.tsx:6`) — **로그인된 관리자가 서버 일시 오류를 "세션 만료"로
   오독하고 재로그인한다.** isError 면 오류 문구 + 재시도를 렌더한다.
6. **무효화 접두 중복**(§1-8⑥): 동작 버그는 아니나, 접두 매칭을 모르는 후속
   수정자가 한 줄만 고치는 함정이다 — 중복 호출을 제거하고 주석 한 줄로 접두
   매칭을 명시한다.

이미 해소된 ④ RequestDetail 로딩 상태(§1-8)는 코드 변경 없이 백로그에서 지운다.

### 2.5 고급 sync 옵션 — 폼 노출만, 검증은 서버가 최종 심판

SubmitJob sync 분기에 접힘 섹션(`<details>`)으로 `open_noatime`(체크박스, §1-6 누락
복구), `batch_files`(정수 1..1,000,000), `bufsize`(정수, **바이트** 단위 4096..1GiB —
단위를 라벨에 명기), `chmod`/`chown`(텍스트) 를 추가한다. 빈 값은 body 에서 생략(현
`checkedOptions` 관례). `SubmitBody.options` 를 `Record<string, boolean | number |
string>` 으로 확장(`useJobs.ts:28`). 클라이언트 검증은 `domain.py:112-113` 정규식의
미러(즉답용)로만 두고 서버 422 `invalid_option`(문구 등록됨, `api.ts:82`)이 최종이다.
**함정을 폼 캡션에 그대로 적는다**: chown 을 명시하면 auto-chown 이 꺼지고
(`execution_manifests.py:69-71`), 비특권 요청자가 타인 소유를 지정하면 도구가 chown
권한이 없어 **잡이 Failed 로 끝난다** — 데이터는 복사되고 메타데이터에서 죽는
반쪽 실패다(`:59-66` 주석의 실패 모드가 사용자 입력으로 재현 가능해진다).

### 2.6 Sparkline 1점 — 점을 그린다, "—" 로 뭉개지 않는다

유효점이 정확히 1개면 그 좌표에 반지름 1.5 의 `<circle>` 을 렌더한다. "—"(데이터
없음)로 접지 않는 이유: 첫 리포트 1점은 **실측값이지 결측이 아니다** — 0 과 null 을
뭉개지 않는 원칙의 SVG 판이다. `sparklinePath` 는 그대로 두고 컴포넌트에서 분기한다.

### 2.7 사유 코드 — 1종 신설, 양쪽 등록

`artifact_too_large`(413, §2.3) 를 `frontend/src/lib/reasonCodes.json` 과
`api.ts` REASON_MESSAGES 양쪽에 등록한다 — 계약 테스트
(`tests/test_reason_codes_coverage.py`, `reasonCodes.test.ts`) 조건. 그 외 신설 없음
— 다운로드 실패는 기존 `artifact_not_found` 로 접히는 것이 설계다(오라클, §2.2).

## 3. 화면

- **JobViewer**: 각 아티팩트 탭 콘텐츠 상단에 「다운로드 (크기)」 링크 — 목록
  entries 에 size 가 이미 있다(`artifacts.py:138`). `<a href download>` 로 충분하다
  (§1-3). 뷰가 `truncated` 면 「뒷부분만 표시」 배지 옆에 "전체는 다운로드로" 를
  붙인다 — 256KB 꼬리와 전체 파일의 관계를 화면이 말한다.
- **SubmitJob**: §2.5 접힘 섹션 + 경고 캡션. 기본 접힘 — 기존 사용자 동선 불변.
- **StoragesList/대시보드**: 배지 색만 바뀐다(§2.4-1). Ready 가 처음으로 초록이 된다.
- **RequestDetail**: 잡 카드에 취소 실패 문구(§2.4-4). Home/Login 은 오류 문구 추가.

## 4. 오류 처리

- 다운로드 open 계열 실패는 전부 404 `artifact_not_found` — errno·경로를 흘리지
  않는다(§1-2 의 오라클 원칙을 새 라우트가 똑같이 진다). 413 만 예외이며 그 안전
  근거는 §2.3. 스트리밍 중 실패(조기 EOF)는 전송 중단으로 표면화 — 숨기지 않는다.
- `<a href>` 다운로드가 404/413 을 받으면 브라우저가 JSON 오류 본문으로 이동할 수
  있다 — fetch+blob 로 감싸면 상한 크기까지 메모리 버퍼링이라 더 나쁘다. 정직한
  트레이드오프로 수용하고, 목록이 이미 죽은 항목을 안 보여주므로 발생 창이 좁다.
- 클라이언트 절단 시 fd 는 제너레이터 try/finally 로 닫는다. 종료 경로가 GC 에
  얹히는 경우까지 단위 테스트로 못 박는다(§5) — 조용한 fd 누수를 만들지 않는다.
- 고급 옵션의 서버 거부(422)는 기존 ApiError 경로 그대로 폼 하단에 뜬다.

## 5. 테스트

- 다운로드 불변식: 심링크 이름→404, mkfifo→404(블록 없이 즉답), phase 디렉터리
  바꿔치기→404(fd 봉쇄), 크기 초과→413, 헤더 3종(§2.2), Content-Length==fstat size,
  스트림이 열림 후 append 된 바이트를 보내지 않음, truncate 시 조기 종료, 정상·조기
  종료 양쪽에서 fd close(제너레이터 close() 직접 호출로 검증), 뷰 라우트 무변경.
- 존재 오라클: 다운로드 404 응답이 기존 뷰 라우트 404 와 body 까지 동일한지.
- 사유 코드 계약: `artifact_too_large` 양쪽 등록.
- 프론트: 다운로드 링크 href(encodeURIComponent 포함)·truncated 시 안내,
  storagePillVariant 매핑(Ready=ok/Degraded=busy)과 공유 pillVariant 불변 단언,
  Login instanceof 분기, 잡 취소 오류 렌더+해당 잡 카드 한정, ConfirmDialog 재열림
  시 오류 초기화, Home isError 렌더(리다이렉트 없음), 401 통합 후 dms:unauthorized
  발화 유지, 고급 옵션 빈 값 미전송·문자열 옵션 전송·클라이언트 정규식 거부,
  Sparkline 1점 circle·0점 "—" 유지.
- 기준선: 백엔드 1131 / 프론트 228(49 files) 에서 증가만 허용, tsc 0.

## 6. 실증 (테스트베드)

1. 실 scan 잡의 `dscan-report.json` 을 포탈에서 다운로드 → 디스크 원본과 sha256
   일치. 뷰는 여전히 256KB 꼬리(두 경로 공존이 화면에서 보인다).
2. **위협 모델 재현**(핵심): 요청자 소유 phase 디렉터리에 ① `/etc/passwd` 대상
   심링크 ② mkfifo ③ `truncate -s 10G` sparse 파일을 직접 만들고 → ①② 404(②는
   즉답 — 행 없음), ③ 413. 목록에는 ①② 가 아예 안 뜬다.
3. 256KB 초과 파일: 뷰 「뒷부분만 표시」 + 다운로드로 전체 획득 — §1-1 의 503
   `scan_report_too_large` 상황에서 운영자가 리포트를 손에 넣는 경로가 생겼는지.
4. `chmod=D770,F660` 실 sync → 목적지 권한 실측. 비특권 요청자가 chown 에 타인
   지정 → 잡 Failed(§2.5 캡션의 함정이 실제임을 재현·기록).
5. 노드 하나의 스토리지 마운트를 죽여 Degraded 유발 → 배지가 황색으로 구분되는지,
   복구 후 초록 Ready. 대시보드 첫 리포트 노드에서 Sparkline 1점이 점으로 보이는지.
6. 브라우저 devtools 로 `/api/auth/me` 를 차단하고 `/` 진입 → 로그인 화면이 아니라
   오류+재시도가 뜨는지(§2.4-5 의 오독 시나리오 재현).

## 7. 이 슬라이스에서 하지 않는 것

- **아티팩트 삭제·보존/GC UI** — 파일을 지우는 코드 자체가 없다(§1-5). 첫 파괴
  경로는 보존 정책·감사·복구 불능 경고까지 한 세트라 독립 슬라이스 감이다.
- **배치 CSV 일체**(파일 업로드·템플릿·결과 내보내기·스토리지 드롭다운) — textarea
  경로가 동작하는 관리자 전용 편의 기능이라 가치 대비 뒤로 민다. 드롭다운은 행마다
  스토리지가 들어가는 현 CSV 구조와 맞지 않아 CSV 개편과 함께 다뤄야 한다.
- **rm 배치** — `validate_batch` 가 의도적으로 거부한다(§1-7). 대량 삭제는 항목별
  미리보기·확인 흐름 설계가 선행돼야 하는 파괴 경로다.
- 다운로드 Range/재개·다중 파일 zip·rate limit(§2.3 한계 명시)·뷰 라우트의
  바이너리 감지, 고급 옵션의 배치 폼 노출, nosniff 의 전역(전체 응답) 적용 —
  다운로드 응답에만 건다. 전역화는 SPA 자산 영향 검토가 따로 필요하다.
- FAST-FOLLOW 중 이미 해소된 RequestDetail 로딩 상태(§1-8) — 백로그 기록 정정만.
