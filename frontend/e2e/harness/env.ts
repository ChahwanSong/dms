import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// e2e 하네스 공용 상수·경로 탐색. 부작용 없음(프로세스를 띄우지 않는다) --
// 스펙 파일이 ADMIN 하나 때문에 import 해도 안전해야 한다.

export const PORT = 8093;
export const BASE_URL = `http://127.0.0.1:${PORT}`;

// config.py 의 _is_placeholder 가 "CHANGE_ME"/"REPLACE_WITH_" 부분일치로 기동을
// 막는다 -- 여기 값들은 그 검사에 걸리지 않는 실값이어야 한다. 로컬 tmp DB 전용이라
// 비밀이 아니지만, 그렇다고 예시 자리표시자를 쓰면 하네스가 아예 안 뜬다.
export const SHARED_TOKEN = "e2e-shared-token";
export const ADMIN_TOKEN = "e2e-admin-token";
export const SESSION_SECRET = "e2e-session-secret";

// username 은 반드시 "admin" 이다 -- 기본 privileged_requesters={root,admin} 에
// 들어야 E4 의 특권 경로(LDAP 없이 신원 검사 생략)가 성립한다.
export const ADMIN = { username: "admin", password: "e2e-admin-pw" };

// 시드 스토리지 이름. 시드(global-setup)와 시나리오가 같은 값을 봐야 한다.
export const STORAGE_NAME = "e2e-store";

const here = path.dirname(fileURLToPath(import.meta.url));
// frontend/e2e/harness -> frontend/e2e -> frontend -> 저장소 루트
export const repoRoot = path.resolve(here, "..", "..", "..");
export const distDir = path.join(repoRoot, "frontend", "dist");
export const fakeBinDir = path.join(repoRoot, "frontend", "e2e", "fixtures", "bin");

/**
 * dms CLI 실행 파일. 워크트리에는 venv 가 없어 본 저장소의 공용 venv 로 떨어진다
 * (`.claude/worktrees/<이름>` 이라 3단계 위가 본 저장소).
 * 없으면 **throw** 한다 -- skip 은 "검사하지 않았다"를 초록으로 위장한다(설계 §4).
 */
export function findDmsBin(): string {
  const candidates = [
    path.join(repoRoot, ".venv", "bin", "dms"),
    path.resolve(repoRoot, "..", "..", "..", ".venv", "bin", "dms"),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  throw new Error(
    `dms CLI 부재: ${candidates.join(", ")} 어디에도 없다. venv 를 만들고 편집 설치하라.`);
}

/**
 * 모든 백엔드 자식 프로세스의 공통 env.
 *
 * PYTHONPATH 가 핵심이다: 본 저장소 venv 의 dms 편집 설치는 **본 저장소** src 를
 * 가리키므로, 이걸 빼면 e2e 가 워크트리가 아니라 본 저장소 코드를 검사한다(조용한
 * 거짓 초록). sys.path 에서 PYTHONPATH 가 site-packages 보다 앞서 결정적이다.
 */
export function backendEnv(dbPath: string, extra: Record<string, string> = {})
    : NodeJS.ProcessEnv {
  return {
    ...process.env,
    PYTHONPATH: path.join(repoRoot, "src"),
    // dbPath 가 절대경로라 sqlite:/// + /tmp/... = 슬래시 4개가 된다(의도된 형태).
    DMS_DATABASE_URL: `sqlite:///${dbPath}`,
    DMS_SHARED_TOKEN: SHARED_TOKEN,
    DMS_ADMIN_TOKEN: ADMIN_TOKEN,
    DMS_SESSION_SECRET: SESSION_SECRET,
    ...extra,
  };
}
