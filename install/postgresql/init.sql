-- DMS PostgreSQL 초기화 템플릿.
-- 비밀번호를 교체한 뒤 PostgreSQL superuser 또는 database owner로 실행한다.
-- DMS 애플리케이션은 여전히 `dms migrate`를 통해 자체 schema migration을 실행한다.

CREATE ROLE dms_app LOGIN PASSWORD 'CHANGE_ME_DMS_APP_PASSWORD';
CREATE ROLE dms_obs LOGIN PASSWORD 'CHANGE_ME_DMS_OBS_PASSWORD';

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
