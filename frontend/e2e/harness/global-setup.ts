import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";

import {
  ADMIN, ADMIN_TOKEN, BASE_URL, PORT, SHARED_TOKEN, STORAGE_NAME,
  backendEnv, distDir, fakeBinDir, findDmsBin,
} from "./env";

// 풀스택 부팅·시드. 이 파일이 성공했다는 사실 자체가 시나리오 ④(풀스택 부팅)의
// 단언이다 -- migrate/api/controller/agent 가 한 번이라도 어긋나면 여기서 throw 다.
// **어떤 실패도 skip 이 아니다**(설계 §4): 검사하지 않은 것을 초록으로 위장하지 않는다.

const READYZ_TIMEOUT_MS = 30_000;
const STORAGE_READY_TIMEOUT_MS = 10_000;
const POLL_INTERVAL_MS = 500;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** :8093 에 이미 누가 있으면 실패시킨다. 낡은 서버(낡은 dist·낡은 DB)에 붙어
 *  초록이 나는 오염이 e2e 에서 가장 조용한 거짓말이라 부팅 시점에 끊는다. */
function assertPortFree(): Promise<void> {
  return new Promise((resolve, reject) => {
    const socket = net.createConnection({ host: "127.0.0.1", port: PORT });
    const done = (fn: () => void) => { socket.destroy(); fn(); };
    socket.setTimeout(2000);
    socket.on("connect", () => done(() => reject(new Error(
      `:${PORT} 를 이미 누군가 듣고 있다 -- 이전 하네스 잔재로 보인다. ` +
      `해당 프로세스를 정리한 뒤 다시 실행하라(포트를 바꾸지 말 것).`))));
    socket.on("timeout", () => done(() => reject(new Error(
      `:${PORT} 연결이 응답 없이 타임아웃했다 -- 포트 상태를 먼저 확인하라.`))));
    // ECONNREFUSED = 아무도 없다 = 정상.
    socket.on("error", () => done(resolve));
  });
}

function tail(file: string, lines = 40): string {
  try {
    return fs.readFileSync(file, "utf8").split("\n").slice(-lines).join("\n");
  } catch {
    return "(로그 없음)";
  }
}

async function pollUntil(label: string, timeoutMs: number,
                         probe: () => Promise<boolean>,
                         diagnose: () => string): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let lastError = "";
  for (;;) {
    try {
      if (await probe()) return;
      lastError = "";
    } catch (e) {
      lastError = `${e}`;
    }
    if (Date.now() >= deadline) {
      throw new Error(`${label} 폴링이 ${timeoutMs}ms 안에 성공하지 못했다` +
        (lastError ? ` (마지막 오류: ${lastError})` : "") + `\n${diagnose()}`);
    }
    await sleep(POLL_INTERVAL_MS);
  }
}

/** 시드 호출은 상태를 반드시 단언한다 -- 조용히 지나가면 한참 뒤 시나리오
 *  단언에서야 터져 원인이 뭉개진다. */
async function seed(label: string, url: string, init: RequestInit, expected: number) {
  const response = await fetch(url, init);
  if (response.status !== expected) {
    const body = await response.text().catch(() => "");
    throw new Error(`시드 실패 [${label}]: ${response.status} (기대 ${expected}) ${body}`);
  }
}

export default async function globalSetup(): Promise<void> {
  await assertPortFree();

  const dmsBin = findDmsBin();
  if (!fs.existsSync(path.join(distDir, "index.html"))) {
    // dist 를 서빙해야 spa_fallback(app.py)이 검사 대상에 들어온다 -- vite dev
    // 서버로는 그 코드가 영영 안 돌아간다(설계 §1-6).
    throw new Error(`${distDir}/index.html 이 없다 -- 먼저 \`npm run build\`. ` +
      `(\`npm run test:e2e\` 는 빌드를 포함한다)`);
  }

  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "dms-e2e-"));
  const dbPath = path.join(tmpDir, "dms.db");
  const storageDir = path.join(tmpDir, "storage");
  const mountinfoPath = path.join(tmpDir, "mountinfo");
  fs.mkdirSync(storageDir);
  // 가짜 mountinfo: parse_mountinfo 는 각 줄의 **5번째 공백 필드**를 마운트포인트로
  // 읽는다. 슬라이스 24 가 스토리지 "/" 등록을 거부하므로(422) 실디렉터리를 쓰되,
  // 그 디렉터리가 마운트포인트라고 에이전트에게 알려줄 유일한 방법이 이 주입이다.
  // 머신의 실제 마운트 상태에 의존하지 않아 오히려 결정적이다.
  fs.writeFileSync(mountinfoPath,
    `36 25 0:32 / ${storageDir} rw,relatime shared:1 - tmpfs tmpfs rw\n`);

  const children: ChildProcess[] = [];
  const spawnLongLived = (label: string, args: string[],
                          extraEnv: Record<string, string>): ChildProcess => {
    const outPath = path.join(tmpDir, `${label}.log`);
    const out = fs.openSync(outPath, "a");
    const child = spawn(dmsBin, args, {
      env: backendEnv(dbPath, extraEnv),
      stdio: ["ignore", out, out],
    });
    children.push(child);
    return child;
  };
  const killAll = () => {
    for (const child of children) {
      if (child.pid !== undefined) {
        try { process.kill(child.pid, "SIGKILL"); } catch { /* 이미 죽음 */ }
      }
    }
  };

  try {
    // 1) 스키마.
    const migrate = spawnSync(dmsBin, ["migrate"], {
      env: backendEnv(dbPath), encoding: "utf8",
    });
    if (migrate.status !== 0) {
      throw new Error(`dms migrate 실패(status=${migrate.status}): ` +
        `${migrate.stderr || migrate.stdout || migrate.error}`);
    }

    // 2) API(장수). dist 서빙 + 리포트 신선도 1시간(agent --once 는 setup 에서
    //    딱 한 번이라 기본 300s 로는 긴 스위트 도중 후보가 사라진다).
    const api = spawnLongLived("api", ["api"], {
      DMS_API_HOST: "127.0.0.1",
      DMS_API_PORT: String(PORT),
      DMS_STATIC_DIR: distDir,
      DMS_AGENT_REPORT_STALE_SECONDS: "3600",
    });
    const apiLog = path.join(tmpDir, "api.log");
    await pollUntil("/readyz", READYZ_TIMEOUT_MS,
      async () => (await fetch(`${BASE_URL}/readyz`)).status === 200,
      () => `api.log tail:\n${tail(apiLog)}`);

    // 3) 시드. admin 부트스트랩 -> 스토리지 -> 사용자 계정 순서다.
    //    스토리지가 **에이전트보다 먼저** 있어야 프로브 대상에 실린다(순서 불변).
    //
    //    컨트롤러는 여기서 띄우지 않고 시드가 **끝난 뒤**에 띄운다(플랜의 순서에서
    //    벗어난 지점이다). 실측 근거: 컨트롤러를 먼저 띄웠을 때
    //    `POST /api/admin/storages` 가 간헐적으로 500 을 반환했다(6회 중 1회).
    //    sqlite 는 프로세스 간 쓰기 락이 하나뿐이고, 컨트롤러 기동 첫 틱은 모든
    //    루프의 리스 획득으로 쓰기 버스트를 낸다 -- 시드의 read-then-write 트랜잭션
    //    (storages.create 의 존재 확인 후 INSERT)이 그 창에서 락 승격에 실패한다.
    //    시드는 컨트롤러를 필요로 하지 않으므로 경합 창 자체를 없애는 것이 맞고,
    //    "컨트롤러가 살아 있다"는 주장은 아래 스토리지 Ready 폴링이 그대로 증명한다.
    //    (운영 DB 는 PostgreSQL 이라 이 경합은 e2e 하네스 한정 문제다.)
    await seed("admin 부트스트랩", `${BASE_URL}/api/admin/accounts`, {
      method: "POST",
      headers: { "content-type": "application/json", "x-admin-token": ADMIN_TOKEN },
      body: JSON.stringify(ADMIN),
    }, 201);

    await seed("스토리지", `${BASE_URL}/api/admin/storages`, {
      method: "POST",
      headers: { "content-type": "application/json",
                 authorization: `Bearer ${SHARED_TOKEN}` },
      // root == mount 는 검증 통과("managed_root must be under mount_path" 는
      // 동일 경로를 허용한다). 둘 다 tmpdir 아래 실디렉터리다.
      body: JSON.stringify({
        storage_name: STORAGE_NAME, mount_path: storageDir,
        managed_root: storageDir, backend_type: "cephfs",
      }),
    }, 201);

    // 긴 이메일 계정 3건: 공백 없는 단일 토큰이라 줄바꿈이 불가능해 계정 표를
    // 강제로 넓힌다 -- E3 의 기하 단언이 볼 재료다(이메일은 무검증 기록이다).
    for (const n of [1, 2, 3]) {
      await seed(`사용자 e2ewide${n}`, `${BASE_URL}/api/auth/signup`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          username: `e2ewide${n}`, password: `e2e-user-pw-${n}`,
          email: `long-${"x".repeat(96)}-${n}@e2e.example.com`,
        }),
      }, 201);
    }

    // 4) 에이전트 1회(--once 는 부트스트랩+실프로브 2사이클이라 1회면 충분).
    //    PATH 앞에 가짜 도구 4종을 두어 which 발견 -> 도구 Ready 로 만든다.
    const agent = spawnSync(dmsBin, ["agent", "--once"], {
      env: backendEnv(dbPath, {
        DMS_AGENT_API_URL: BASE_URL,
        DMS_AGENT_NODE_NAME: "e2e-node",
        DMS_AGENT_MOUNTINFO_PATH: mountinfoPath,
        PATH: `${fakeBinDir}${path.delimiter}${process.env.PATH ?? ""}`,
      }),
      encoding: "utf8",
    });
    if (agent.status !== 0) {
      throw new Error(`dms agent --once 실패(status=${agent.status}): ` +
        `${agent.stderr || agent.stdout || agent.error}`);
    }
    // 종료코드만으로는 부족하다: AgentRunner.run_once 는 POST 실패를 stderr 에
    // 찍고 **삼킨다**(상태를 그대로 반환) -- 종료는 0 이다. 리포트가 실리지
    // 않았는데 0 을 믿으면 아래 폴링이 10s 뒤 엉뚱한 메시지로 터진다.
    if ((agent.stderr || "").includes("agent report failed")) {
      throw new Error(`에이전트 리포트가 실패했다(종료코드는 0):\n${agent.stderr}`);
    }

    // 5) 컨트롤러(장수, 1s 틱). `--once` 반복은 금지다 -- 리스 holder 가
    //    controller-<pid> 라 매번 pid 가 달라져 서로의 리스에 막힌다.
    //    1s 로 줄이는 것은 **잡 전진에 관여하는 루프만**이다(pod-gc·build-watcher·
    //    rollout·retention 은 기본값 -- 1s 로 돌려봐야 무의미한 쿼리만 는다).
    const controller = spawnLongLived("controller", ["controller"], {
      DMS_PLANNER_INTERVAL_SECONDS: "1",
      DMS_STEPPER_INTERVAL_SECONDS: "1",
      DMS_RECONCILE_INTERVAL_SECONDS: "1",
      DMS_BATCH_ORCHESTRATOR_INTERVAL_SECONDS: "1",
      DMS_AGENT_REPORT_STALE_SECONDS: "3600",
    });

    // 6) 부팅 스모크 마감: 스토리지가 Ready 가 되려면 (a) 에이전트 리포트가 실렸고
    //    (b) 컨트롤러의 storage-reconciler 가 **살아서** 틱을 돌아야 한다.
    //    두 사실을 한 번에 증명하는 단언이다.
    await pollUntil(`스토리지 ${STORAGE_NAME} Ready`, STORAGE_READY_TIMEOUT_MS,
      async () => {
        const response = await fetch(`${BASE_URL}/api/user/storages`, {
          headers: { authorization: `Bearer ${SHARED_TOKEN}` },
        });
        if (response.status !== 200) return false;
        const rows = await response.json() as { storage_name: string; status: string }[];
        return rows.some((r) => r.storage_name === STORAGE_NAME && r.status === "Ready");
      },
      () => `controller.log tail:\n${tail(path.join(tmpDir, "controller.log"))}`);

    // 7) teardown 이 읽을 좌표. globalSetup/Teardown 은 러너 메인 프로세스에서
    //    돌아 process.env 가 그대로 전달된다.
    process.env.DMS_E2E_API_PID = String(api.pid);
    process.env.DMS_E2E_CTRL_PID = String(controller.pid);
    process.env.DMS_E2E_TMPDIR = tmpDir;
    console.log(`[e2e harness] up: api pid=${api.pid} controller pid=${controller.pid} ` +
      `tmp=${tmpDir}`);
  } catch (e) {
    // 죽이기 전에 자식이 진단을 다 뱉을 시간을 준다. 실측으로 배운 것: 500 을
    // 만난 직후 곧바로 SIGKILL 하면 uvicorn 이 **응답 후에** 찍는 트레이스백이
    // 잘려나가 api.log 에 상태코드만 남고 원인이 사라진다.
    await sleep(500);
    const logs = ["api", "controller"]
      .map((name) => `${name}.log tail:\n${tail(path.join(tmpDir, `${name}.log`))}`)
      .join("\n");
    // globalSetup 이 throw 해도 Playwright 가 globalTeardown 을 부르는 버전이
    // 있지만(1.62 실측), 그 계약에 기대지 않는다 -- 안 부르는 순간 :8093 을 문
    // 고아 프로세스가 남아 다음 실행의 포트 선점 검사를 영원히 실패시킨다.
    // 두 번 죽여도 무해하다(teardown 의 kill 은 ESRCH 를 삼킨다).
    killAll();
    throw new Error(`${e instanceof Error ? e.message : e}\n--- 하네스 로그 ` +
      `(tmp=${tmpDir}, 진단을 위해 남긴다) ---\n${logs}`);
  }
}
