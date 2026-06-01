-- DMS PostgreSQL bootstrap template.
-- Run as a PostgreSQL superuser or database owner after replacing passwords.
-- The DMS application still runs its own schema migration through `dms migrate`.

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
