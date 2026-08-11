# 슬라이스 22 — DB 커넥션 재연결 설계

계기(2026-08-11 라이브): API 파드가 90분간 `0/1 Running` 으로 방치됐다. DB 커넥션
객체가 죽었는데(`TCP 연결은 정상, SELECT 1 만 실패`) 재연결이 없어 모든 요청이 영구
실패했고, `/healthz` 가 DB 를 안 봐서 kubelet 은 재시작하지 않았다 — Service 에서만
빠진 채 아무 신호 없이 멈춰 있었다(`BACKLOG.md:373-404`). 파드 삭제로 즉시 복구됐다.
같은 사건에서 컨트롤러는 크래시→재시작으로 스스로 살아났다(재시작 1회). 이 슬라이스는
① `Database` 재연결 ② 트랜잭션 경계 ③ 방치 탈출(자기 종료) ④ 관측을 넣는다.
끊김의 원인은 여전히 미상이다 — 재연결은 원인과 무관하게 복구한다는 것이 요지다.

## 1. 실측으로 확인한 전제

1. **`Database` 는 단일 커넥션 + RLock 이고 재연결이 0건이다.** `self._conn` 하나를
   `self._lock`(RLock) 아래에서 전 스레드가 공유한다(`src/dms/db.py:31-34`).
   `execute`/`query`/`query_one`(`:57-68`) 어디에도 예외 처리·`closed` 검사·재시도가
   없다. `connect()` 는 URL 을 보관하지 않아(`:36-50`) 지금 구조로는 재연결 자체가
   불가능하다. sqlite 는 `isolation_level=None`(`:42`), psycopg 는
   `autocommit=True`(`:48`) — 양쪽 다 명시적 트랜잭션 제어라, `transaction()` 밖의
   문장은 **문장 단위 autocommit** 이다(재시도 단위가 문장 1개로 깔끔히 떨어진다).
2. **`transaction()` 은 BEGIN/COMMIT/ROLLBACK 수동 제어다**(`db.py:70-80`). 실패
   경로가 죽은 커넥션에 `ROLLBACK` 을 또 날린다(`:77`) — 커넥션 사망 시 ROLLBACK 이
   새 `OperationalError` 를 일으켜 **원래 예외를 가린다**(현행 버그, §2.3).
3. **psycopg 3 예외 계층(3.3.4 로컬 실증)**: `OperationalError`·`ProgrammingError` 는
   `DatabaseError` 하위, `InterfaceError` 는 `Error` 직속. 문법 오류는
   `errors.SyntaxError ⊂ ProgrammingError` 라 Operational/Interface 를 잡아도 안
   걸린다. **함정**: `DiskFull`·`ConnectionTimeout`·`AdminShutdown` 이 전부
   `OperationalError` 하위다 — 예외 클래스만으로 "커넥션 죽음"을 판정하면 디스크
   가득참까지 재시도한다. 판정에는 `Connection.closed`(`pgconn.status == BAD`)가
   필요하고, `broken` 은 closed 의 부분집합이다(둘 다 property 소스로 확인).
4. **sqlite3 는 죽음 판정이 불가능한 계층이다(3.45.1 실증)**: 문법 오류(`SELEC 1`)와
   no-such-table 이 **둘 다 `sqlite3.OperationalError`** 다. 닫힌 커넥션 사용은
   `ProgrammingError`("Cannot operate on a closed database")이고 `closed` 속성 자체가
   없다. in-process 파일 DB 라 "서버가 커넥션을 끊는" 모드도 없다.
5. **`/readyz` 는 정직하고 `/healthz` 는 DB 를 안 본다**: readyz 가 `SELECT 1` 실패
   시 503(`src/dms/api/app.py:51-60`), healthz 는 무조건 200(`:47-49`). 프로브는
   readiness `/readyz` 10s 주기, liveness `/healthz` 30s 주기, failureThreshold 는 둘
   다 기본값 3(`deploy/k8s/40-api.yaml:101-110`). api replicas 1(`:40`, 슬라이스 11 의
   빌드 활성 가드가 replicas=1 전제), controller replicas 1 에 **프로브 전무**
   (`41-controller.yaml:21,50-70` — HTTP 서버가 없다).
6. **컨트롤러가 살아난 기전**: `run_all_once` 의 리스 획득
   `try_acquire_lease`(`src/dms/controller.py:103-105`)가 per-loop try(`:109-115`)
   **밖**이고, `run_forever` 의 while 에도 예외 처리가 없다(`:126-133`).
   `try_acquire_lease` 는 `transaction()` 을 연다(`repositories/control.py:149`) —
   죽은 커넥션에서 BEGIN(`db.py:73`)이 던지면 프로세스가 그대로 죽고, Deployment 가
   재시작하며 `cli.py:38` 에서 새 `Database.connect` 를 얻는다. **우연히 올바른
   crash-restart** 다. API 는 요청 핸들러 예외가 FastAPI 에서 500 으로 접혀 프로세스가
   안 죽는다 — 그래서 API 만 함정에 빠진다.
7. **트랜잭션 밖 단독 문장의 성격**: 단독 `execute` 는 대부분 idempotent UPDATE
   (예: `control.py:137-142`)거나 진단 INSERT 다 — `record_event` 는 의도적으로
   트랜잭션 밖 + 자체 try/except(`repositories/observability.py:16-29`). 업무 INSERT
   경로(요청/잡 생성 등)는 `transaction()` 안이다(repositories 전반, transaction 사용
   28곳). 재시도의 이중 적용 위험 평가에 쓴다(§2.2).
8. **psycopg 는 `psycopg[binary]>=3.1` optional dependency**(`pyproject.toml:19`) —
   새 의존성 없이 재연결을 구현할 수 있고, 클러스터는 postgresql 방언으로 돈다
   (라이브 파드 재확인: api·controller 각 1대, 사건 파드는 교체돼 RESTARTS 0).

## 2. 핵심 결정

### 2.1 죽음 판정 — 방언별로 다르게, psycopg 는 예외 클래스 + `closed` 이중 게이트

- **postgresql**: `(psycopg.OperationalError, psycopg.InterfaceError)` 를 잡은 뒤
  `self._conn.closed` 가 True 일 때만 "커넥션 죽음"으로 판정한다. 클래스만 보면
  DiskFull·ConnectionTimeout 같은 살아있는 커넥션의 서버 오류까지 재시도하고(§1-3),
  `closed` 만 보면 잡을 예외 범위가 없다 — 둘의 교집합이 정확하다. 문법 오류는
  ProgrammingError 계열이라 애초에 안 잡힌다(§1-3 실증). 판정 밖 예외는 그대로
  전파한다.
- **sqlite**: **재연결을 넣지 않는다.** `OperationalError` 가 문법 오류를 포함하므로
  (§1-4) 죽음 신호로 쓸 수 없고, in-process 라 죽음 모드 자체가 없다. 닫힌 커넥션의
  `ProgrammingError` 는 코드 버그다 — 숨기지 않고 전파한다. dev/test 경로 무변경.

### 2.2 재연결 지점 — `_run()` 헬퍼 한 곳, 재시도는 정확히 1회, 백오프 없음

`execute`/`query` 의 `self._conn.execute(...)` 를 공용 `_run(sql, params)` 로 모은다
(`query_one` 은 지금처럼 `query` 위임 유지, `db.py:66-68`). `connect()` 가 URL 을
`self._url` 로 보관하고, `_run` 은: 실행 → §2.1 판정 → 죽음이면 `_reconnect()`(구
커넥션 close 시도 후 `connect` 의 psycopg 분기 재수행) → **같은 문장 1회 재시도** →
재시도 실패는 그대로 전파. 전부 기존 RLock 안에서 일어난다 — 단일 커넥션이라 재연결
지점이 한 곳이고, 락이 동시 재연결 경합을 원천 차단한다. **재시도 1회·백오프 없음**의
근거: RLock 은 전 스레드를 직렬화하므로 락 안에서 대기하면 API 전체가 멈춘다. 지속
장애는 재시도로 이길 문제가 아니라 readyz 503(§1-5)과 §2.4 가 다룰 문제다.
정직한 한계 — **이중 적용 창**: 문장이 서버에 도달한 뒤 응답만 유실된 죽음이면
재시도가 non-idempotent 문장을 두 번 적용할 수 있다. 이번 사건 계열(이미 죽은
커넥션에 전송 시도 → 즉시 실패)은 창 밖이고, 트랜잭션 밖 단독 문장은 idempotent
UPDATE·진단 INSERT 가 대부분이며(§1-7) 업무 INSERT 는 §2.3 이 재시도를 금지한다.
남는 창은 "단독 INSERT + 응답 유실 순간의 죽음"으로, 이벤트 중복 1행 수준이다.

### 2.3 트랜잭션 경계 — BEGIN 이후는 절대 재시도하지 않는다

`Database` 에 락 아래 `_txn_depth` 카운터를 둔다. `transaction()` 진입 시 +1, 종료 시
-1. **`_txn_depth > 0` 이면 `_run` 은 재연결도 재시도도 하지 않고 즉시 전파한다** —
트랜잭션 중간 재연결은 이미 적용된 앞 문장들이 커넥션 소멸과 함께 서버에서
롤백된 상태에서 뒷문장만 새 커넥션에 다시 적용하는 것이라, 부분 적용을 **만들어내는**
동작이다. 경계는 둘로 나뉜다:
- **BEGIN(yield 전, `db.py:73`) 실패**: 아직 아무것도 적용되지 않았다 — 여기만
  재연결 + BEGIN 1회 재시도를 허용한다. 컨트롤러 리스 획득이 정확히 이 지점에서
  죽으므로(§1-6) 이 허용이 컨트롤러 무크래시 복구의 실체다(§2.5).
- **yield 이후 실패**: 재시도 금지. 현재 except 경로의 `ROLLBACK`(`:77`)은 죽은
  커넥션에서 새 예외로 원인을 가린다(§1-2) — **커넥션이 죽어 있으면 ROLLBACK 을
  생략**한다(서버는 커넥션 소멸 시점에 트랜잭션을 폐기한다 — PG 의미론상 안전).
  대신 `_reconnect()` 만 수행해 다음 호출자가 산 커넥션을 받게 하고, **원래 예외를
  그대로 re-raise** 한다. 호출자는 지금과 동일하게 실패를 본다 — 달라지는 건 "그
  다음" 호출이 성공한다는 것뿐이다.

### 2.4 liveness — DB 에 직결하지 않고, 연속 N회 readyz 실패 시 자기 종료

절충안을 채택한다: `/healthz` 는 DB 무관 200 유지, **`/readyz` 가 연속
`DMS_READYZ_EXIT_FAILURES`(기본 30, 0=비활성)회 실패하면 사유를 stderr 에 남기고
SIGTERM 을 자신에게 보낸다**(uvicorn graceful 종료 → 컨테이너 종료 → restartPolicy
재시작). 카운터는 성공 시 0 리셋. 10s 프로브 주기(§1-5) 기준 기본값은 약 5분이다.
- liveness→DB 직결 기각: 90s(30s×3) 만에 발화해 DB 순단·재기동에도 전 파드가
  재시작되고, CrashLoopBackOff 지수 백오프(최대 5분)가 DB 복귀 **후의** 회복을
  오히려 늦춘다. replicas 1(§1-5)이라 백오프 동안 API 가 0대다.
- 현상 유지 기각: 원인 미상 사건이다(`BACKLOG.md:402-403`). 재연결이 못 덮는 파드
  국소 장애(netns/conntrack 오염 등)가 배제되지 않고, 실측된 유일한 처방이 "파드
  삭제"였다 — 그 처방의 자동화가 필요하다.
- 채택 근거: 재연결(§2.2)이 들어간 뒤의 readyz 실패는 "**재연결까지 실패**"를
  뜻한다. 그 상태로 5분이면 파드 교체가 옳고, 이미 readiness 503 으로 Service 에서
  빠져 있어 종료로 잃는 가용성이 0 이다. DB 장기 다운 시 ~5분 주기 재시작 루프가
  생기지만 RESTARTS 증가는 이번 사건에 없던 **신호** 그 자체다. 컨트롤러엔 이 장치를
  넣지 않는다 — HTTP 가 없고(§1-5), 동등물이 이미 있다(§2.5).

### 2.5 컨트롤러 — 크래시 대신 같은 틱 복구, crash-restart 는 안전망으로 승격

살아난 기전은 §1-6 이 확정했다: 리스 획득의 BEGIN 이 try 밖에서 던져 프로세스가
죽고 재시작이 새 커넥션을 만들었다. 재연결이 들어가면: BEGIN 죽음은 §2.3 의 BEGIN
재시도로 복구되어 **크래시 없이 같은 틱에서 루프가 계속된다** — 재시작으로 잃던
루프 한 바퀴(stepper 5s~retention 3600s)와 파드 기동 시간이 사라진다. 재연결조차
실패하는 지속 장애면 예외가 지금처럼 try 밖으로 전파돼 crash-restart 한다 — 이
경로는 이제 우연이 아니라 컨트롤러의 자기 종료 동등물이므로, `controller.py:103-105`
에 "리스 획득 실패는 의도적 crash-restart 경로"라는 주석을 달아 규약으로 못박는다
(try 안으로 옮기는 리팩터링을 금지하는 문서화다). replicas 1 이지만 리스가 per-loop
DB 리스라(§1-6) 재시작·다중 기동 모두 안전하다는 기존 전제도 유지된다.

### 2.6 관측 — 조용한 재연결 금지, 흔적은 세 곳

재연결이 조용하면 "DB 가 자주 끊긴다"는 상류 문제가 숨는다. 새 테이블·새 사유 코드
없이 세 곳에 남긴다:
1. **stderr 구조 로그**: 재연결 시도/성공/실패마다 한 줄(예외 타입·소요·방언).
   `kubectl logs` 가 1차 창구다.
2. **인메모리 카운터**: `Database.reconnect_count`·`last_reconnect_at` 을 두고
   `/readyz` 200 본문에 포함한다(`app.py:60` 확장 —
   `{"status":"ok","reconnects":N,"last_reconnect_at":...}`). kubelet 은 상태 코드만
   보므로 프로브 무영향, 운영자는 curl 한 번으로 본다.
3. **events 영속 1건**: 재연결 성공 직후 `record_event(component="db",
   event_type="db_reconnected")` — 기존 테이블·기존 retention(`controller.py:51-55`)
   그대로이고, 자체 try/except(§1-7)라 직후 재실패에도 안전하다. "얼마나 자주
   끊기는가"를 SQL 로 셀 수 있는 유일한 영속 흔적이다.

## 3. 화면

무변경. 사용자에게 보이는 실패 표면이 없고(재연결은 성공하면 투명, 실패하면 기존
500/503 그대로), 신설 사유 코드가 0 이라 `reasonCodes.json`/`api.ts` 계약도 건드리지
않는다. 재연결 카운트의 대시보드 노출은 §7 로 미룬다.

## 4. 오류 처리

- 죽음 판정 밖 예외(DiskFull, 문법 오류, closed=False 인 Operational)는 재시도 없이
  전파 — 잘못 삼키면 진짜 오류가 재시도 지연 뒤에 나타나 진단을 흐린다.
- `_reconnect()` 자체 실패(DB 다운)는 원 예외 대신 재연결 예외를 전파하지 않도록
  원 예외에 chain 해 던진다 — readyz 는 어느 쪽이든 503 으로 정직하다.
- 자기 종료는 readyz 핸들러 안의 단순 int 카운터다(GIL 하 단일 증가). SIGTERM 후에도
  프로브가 더 올 수 있으나 카운터는 이미 임계 초과라 무해하다.
- 트랜잭션 실패 시 ROLLBACK 생략은 **커넥션이 죽었을 때만**이다 — 살아있는 커넥션의
  업무 예외(제약 위반 등)는 지금처럼 ROLLBACK 후 전파(`db.py:76-78` 유지).
- sqlite 경로는 예외 처리 포함 완전 무변경 — 로컬·CI 의 기존 동작을 흔들지 않는다.

## 5. 테스트

- `_run` 재연결(가짜 psycopg 커넥션 주입): 죽은 커넥션 → 재연결 + 1회 재시도 성공 /
  재시도도 실패 → 전파 / `closed=False` 인 OperationalError → 재시도 0회 /
  ProgrammingError → 재시도 0회 / 재연결 후 `reconnect_count` 증가.
- sqlite: OperationalError(문법 오류)가 재시도 없이 그대로 전파되는지.
- 트랜잭션 경계: yield 이후 죽음 → 재시도 0회 + ROLLBACK 미호출 + **원 예외 보존**
  + 다음 단독 호출은 성공(재연결 됐음) / BEGIN 죽음 → BEGIN 1회 재시도로 트랜잭션
  정상 진행 / 살아있는 커넥션의 업무 예외 → ROLLBACK 호출 유지.
- readyz 자기 종료(exit_fn 주입): 연속 N 실패 → 호출, N-1 실패 후 성공 → 카운터
  리셋, `DMS_READYZ_EXIT_FAILURES=0` → 비활성. 200 본문에 reconnects 필드.
- 컨트롤러: 리스 BEGIN 1회 죽음 → run_all_once 가 예외 없이 결과 반환(무크래시 복구),
  지속 죽음 → 예외 전파(crash-restart 경로 보존, `tests/test_controller.py` 확장).
- 이벤트: 재연결 성공 시 `db_reconnected` 1건 기록.
- 기준선: 백엔드 1136 passed(추정) + 신규, 프론트 228 / 49 files 무변경 유지.

## 6. 실증 (테스트베드)

전부 되돌릴 수 있는 조작만 쓴다. DB 는 pkg-01 의 postgres(10.10.10.30:5432)다.

1. **API 재연결(핵심)**: pkg-01 에서 `SELECT pid, client_addr, state FROM
   pg_stat_activity WHERE datname='dms';` 로 API 파드 IP 의 backend 를 식별하고
   `SELECT pg_terminate_backend(<pid>);` 로 강제 종료한다(1개 backend 종료는 비파괴,
   되돌림 불요). 직후 포탈 아무 화면(또는 `curl /api/...`) — **첫 요청부터 200**,
   RESTARTS 불변, 로그에 재연결 한 줄, `/readyz` 본문 `reconnects` 증가, events 에
   `db_reconnected` 1건이 남아야 한다. 사건 재현 조건(커넥션만 죽고 TCP 는 정상)과
   동형이다.
2. **컨트롤러 무크래시 복구**: 컨트롤러 backend 를 같은 방법으로 종료 →
   **RESTARTS 불변**(사건 때는 +1 이었다 — 대조가 증거다), `component_leases` 의
   `expires_at` 이 계속 전진(루프 생존), 재연결 로그 확인.
3. **자기 종료 탈출**: pkg-01 에서 `iptables -I INPUT -p tcp -s <API파드IP> --dport
   5432 -j REJECT` → readyz 503 누적 → 약 N×10s 후 RESTARTS +1 과 종료 사유 로그 →
   `iptables -D INPUT ...` 로 즉시 제거 → 파드 Ready 복귀. 규칙은 검증 즉시 제거한다
   (백오프 성장 방지). 90분 방치 사건의 자동 탈출이 이것으로 증명된다.
4. **중복 미적용 확인**: 1번 직후 제출한 요청 1건이 DB 에 정확히 1행인지 확인
   (재시도 이중 적용 창의 스모크). 트랜잭션 중간 죽음은 라이브에서 타이밍이
   비결정적이라 §5 단위 테스트가 담당한다 — 여기 적어 숨기지 않는다.

## 7. 이 슬라이스에서 하지 않는 것

- **커넥션 풀(psycopg_pool) 도입** — 새 의존성 금지. 단일 커넥션 + RLock 구조는
  유지하고 재연결만 넣는다. 처리량 문제가 실측되면 별도 슬라이스.
- 다회 재시도·백오프 루프(§2.2 근거로 1회 고정), liveness 의 DB 직결(§2.4 기각).
- 컨트롤러 HTTP 헬스 서버 추가 — crash-restart 안전망(§2.5)으로 충분하다.
- sqlite 재연결(§2.1 — 죽음 모드가 없다).
- 재연결 지표의 대시보드(슬라이스 14) 패널 — 로그·readyz 본문·events 로 관측은
  성립한다. 화면은 후속.
- 사건 원인 규명 — `BACKLOG.md:402-404` 의 "원인 불명" 기록을 유지한다. 이 설계는
  원인과 무관한 복구 장치다.
- 신설 사유 코드 0 — `reasonCodes.json`/`api.ts` 무변경(계약 조항은 준수 확인만).
