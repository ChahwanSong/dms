import fs from "node:fs";

// 정리. 다만 "조용한 정리"는 하지 않는다: 스위트 도중 API·컨트롤러가 죽어 있었다면
// 부팅 스모크(시나리오 ④)의 주장이 깨진 것이므로 러너를 **실패로 승격**한다.
// 정리 자체는 승격 전에 끝낸다 -- 실패했다고 고아 프로세스를 남기면 다음 실행이
// 포트 선점 검사에서 막힌다.

const SIGTERM_GRACE_MS = 3_000;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function alive(pid: number): boolean {
  try {
    process.kill(pid, 0);   // 시그널 0 = 생존 확인만
    return true;
  } catch {
    return false;
  }
}

export default async function globalTeardown(): Promise<void> {
  const tmpDir = process.env.DMS_E2E_TMPDIR;
  const targets: { label: string; pid: number }[] = [];
  for (const [label, key] of [["api", "DMS_E2E_API_PID"],
                              ["controller", "DMS_E2E_CTRL_PID"]] as const) {
    const raw = process.env[key];
    const pid = raw ? Number(raw) : NaN;
    if (Number.isInteger(pid) && pid > 0) targets.push({ label, pid });
  }

  const dead = targets.filter((t) => !alive(t.pid)).map((t) => t.label);

  for (const { pid } of targets) {
    if (alive(pid)) {
      try { process.kill(pid, "SIGTERM"); } catch { /* 경합: 방금 죽었다 */ }
    }
  }
  const deadline = Date.now() + SIGTERM_GRACE_MS;
  while (targets.some((t) => alive(t.pid)) && Date.now() < deadline) {
    await sleep(100);
  }
  for (const { pid } of targets) {
    if (alive(pid)) {
      try { process.kill(pid, "SIGKILL"); } catch { /* 방금 죽었다 */ }
    }
  }

  const healthy = targets.length === 2 && dead.length === 0;
  // 이상이 있었으면 tmpdir(=로그·DB)를 **남긴다** -- 진단 자료를 지우면서
  // "확인해 보라"고 하는 것은 정리가 아니라 증거 인멸이다.
  if (healthy && tmpDir && tmpDir.includes("dms-e2e-")) {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }

  if (targets.length !== 2) {
    throw new Error("하네스 PID 기록이 불완전하다 -- 부팅이 끝까지 가지 않았다: " +
      `${JSON.stringify(targets)}`);
  }
  if (dead.length > 0) {
    throw new Error(`스위트가 끝나기 전에 죽어 있던 프로세스: ${dead.join(", ")} ` +
      `-- 잡이 이미 끝난 뒤 죽었다면 시나리오는 초록이지만 부팅 스모크는 거짓이다. ` +
      `로그를 남겼다: ${tmpDir}/*.log`);
  }
}
