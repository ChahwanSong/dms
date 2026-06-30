-- DMS PostgreSQL 초기화 템플릿.
-- 비밀번호를 교체한 뒤 PostgreSQL superuser 또는 database owner로 실행한다.
-- DMS 애플리케이션은 여전히 `dms migrate`를 통해 자체 schema migration을 실행한다.

CREATE ROLE dms_app LOGIN PASSWORD 'CHANGE_ME_DMS_APP_PASSWORD';
CREATE ROLE dms_obs LOGIN PASSWORD 'CHANGE_ME_DMS_OBS_PASSWORD';

-- Belt-and-suspenders 세션 안전장치. 애플리케이션 풀이 connection 단위로
-- statement_timeout / idle_in_transaction_session_timeout 를 설정하지만, 풀을
-- 거치지 않는 임의 접속(예: psql, 잘못 설정된 클라이언트)도 동일하게 보호한다.
-- DMS migration / 대량 유지보수(pooled=False)는 connection 단위로
-- statement_timeout=0 을 명시 설정하여 이 ROLE 기본값을 덮어쓰므로 큰 CREATE INDEX
-- 가 죽지 않는다(src/dms/db.py 참고).
ALTER ROLE dms_app SET statement_timeout = '30s';
ALTER ROLE dms_app SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE dms_obs SET statement_timeout = '30s';
ALTER ROLE dms_obs SET idle_in_transaction_session_timeout = '60s';

CREATE DATABASE dms OWNER dms_app;
CREATE DATABASE dms_observability OWNER dms_obs;

\connect dms
GRANT ALL PRIVILEGES ON DATABASE dms TO dms_app;
GRANT ALL ON SCHEMA public TO dms_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO dms_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO dms_app;

\connect dms_observability
GRANT ALL PRIVILEGES ON DATABASE dms_observability TO dms_obs;
GRANT ALL ON SCHEMA public TO dms_obs;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO dms_obs;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO dms_obs;
