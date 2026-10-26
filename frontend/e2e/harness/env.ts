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

// username 은 반드시 "admin" 이다 -- 기본 privileged_requesters={root,admin} 에 들어야 세션 관리자가 면제자(전체 PV·
// 직접 경로 권한, me.unrestricted)가 되어 하네스의 시드 스토리지에 직접 경로로 scan·sync·rm 을 낼 수 있다. 백엔드는 k8s 신원
// 모드 전용(2026-10-26 LDAP 정리 -- DMS_IDENTITY_MODE 미설정 = k8s): 모든 단건 작업이 root 이고(서버가 run_as_root 를 강제,
// 명시 false 는 422), 공유 토큰 제출은 403 이며, k8s 클러스터가 없는 하네스라 PV 주소는 쓰지 않는다. 바디의
// run_as_root: true(E5/E6 등)는 생략과 같은 뜻이지만 「명시 = 화면과 같은 바디」라 남긴다.
export const ADMIN = { username: "admin", password: "e2e-admin-pw" };

// 시드 스토리지 이름. 시드(global-setup)와 시나리오가 같은 값을 봐야 한다.
export const STORAGE_NAME = "e2e-store";

const here = path.dirname(fileURLToPath(import.meta.url));
// frontend/e2e/harness -> frontend/e2e -> frontend -> 저장소 루트
export const repoRoot = path.resolve(here, "..", "..", "..");
export const distDir = path.join(repoRoot, "frontend", "dist");
export const fakeBinDir = path.join(repoRoot, "frontend", "e2e", "fixtures", "bin");
// e2e 컨트롤러 진입점(2026-10-21 E12): `dms controller` 와 같은 배선에 drm 잡만 가짜 drm 으로 실제로 돌리는 어댑터를
// 끼운다 -- 배치 rm 의 「확인한 경로가 실제로 지워진다」를 stub 으로는 볼 수 없다(그 파일 docstring).
export const e2eControllerPath = path.join(repoRoot, "frontend", "e2e", "fixtures", "e2e_controller.py");

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

/** dms CLI 와 같은 venv 의 python -- e2e 컨트롤러 진입점을 같은 인터프리터·같은 PYTHONPATH 로 띄운다. */
export function pythonBesideDms(dmsBin: string): string {
  const candidate = path.join(path.dirname(dmsBin), "python");
  if (!fs.existsSync(candidate)) {
    throw new Error(`python 부재: ${candidate} -- dms CLI 옆 venv 인터프리터가 있어야 e2e 컨트롤러를 띄운다.`);
  }
  return candidate;
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
    // 하니스는 무인증 signup 으로 계정을 시딩한다(인증번호 흐름은 vitest 몫).
    DMS_ACCOUNT_VERIFICATION_REQUIRED: "false",
    // 시드·apiLogin 은 Node fetch 로 평문 password 를 보낸다(라이브 기본은 봉인 필수).
    // 브라우저 UI 로그인(E1)은 이 스위치와 무관하게 항상 봉인해 보내므로 실제
    // 브라우저 WebCrypto ↔ 파이썬 서버의 상호운용은 E1 이 그대로 검증한다.
    DMS_PASSWORD_ENCRYPTION_REQUIRED: "false",
    ...extra,
  };
}
