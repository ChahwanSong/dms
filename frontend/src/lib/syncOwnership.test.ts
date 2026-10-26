import { expect, test } from "vitest";
import { CHOWN_NAME_ERROR, chownHasName, chownIsPartial, ownerText, syncOwnership, validOwner } from "./syncOwnership";
import { chownFieldError } from "../features/jobs/optionRules";

// 서버 execution_manifests._auto_chown 의 세 갈래 미러(chown 명시 > root 보존 > 실행 신원 uid:gid) +
// 검증 워크플로 2회(2026-10-01, 포크 소스·테스트베드 실측)가 확인한 도구 동작.

test("사용자(비 root·chown 없음): 요청자 본인 uid:gid(주 그룹), 소스 소유자와 무관", () => {
  const o = syncOwnership({ chown: "", root: false, runAs: "alice", self: true });
  expect(o.short).toBe("요청자 본인(alice)의 uid:gid(주 그룹)");
  expect(o.long).toContain("기본적으로 목적지(새로 만드는 경우 포함)와 복사된 파일·디렉토리는 요청자 본인(alice)의 "
    + "uid:gid(주 그룹)로 셋업됩니다 — 소스 소유자와 관계없습니다.");
  expect(o.long).toContain("권한 비트·수정 시각은 소스 그대로라 소스의 그룹 권한이 이 주 그룹에 적용됩니다");
  // 이미 있던 남의 소유 항목: 도구·항목별로 결과가 갈려(검증 3차) "실패하거나 일부가 안 바뀔 수 있다" 로만 말한다
  expect(o.long).toContain("목적지에 이미 있던 같은 경로의 항목도 이 소유로 다시 맞춰지므로, 그 안에 다른 사용자 소유 항목이 "
    + "있으면 작업이 실패하거나 일부 항목의 소유·권한이 바뀌지 않을 수 있습니다.");
  expect(o.long).not.toContain("실행 신원");
  // 자동 지정은 주 그룹 -- 다른 소유는 명시 chown(LDAP 보조 그룹 안내는 2026-10-26 에 지웠다)
  expect(o.long).toContain("다른 소유로 남기려면 chown 에 숫자 uid:gid 를 지정하세요(자동 지정은 주 그룹).");
  expect(o.long).not.toContain("보조");
});

test("chown 칸이 없는 화면(비관리자, chownEditable=false): 지정하라는 대신 관리자에게 요청하라고 말한다", () => {
  // G8(2026-10-08 리뷰): SubmitJob 의 chown 칸은 관리자 전용 고급 옵션 -- 사용자가 할 수 없는 지시를 내보내지 않는다.
  const o = syncOwnership({ chown: "", root: false, runAs: "alice", self: true, chownEditable: false });
  expect(o.long).not.toContain("chown 에 숫자 uid:gid 를 지정하세요");
  expect(o.long).toContain("자동 지정은 주 그룹입니다 — 다른 소유가 필요하면 관리자에게 chown 지정을 요청하세요.");
  expect(o.long).not.toContain("실행 신원");                              // 사용자 화면 용어(본인)를 지킨다
  expect(o.short).toBe("요청자 본인(alice)의 uid:gid(주 그룹)");            // 요약은 같다
  // 기본값(생략)은 true -- 관리자 전용 화면(배치 생성·상세)은 그대로
  expect(syncOwnership({ chown: "", root: false, runAs: "alice", self: true }).long)
    .toContain("다른 소유로 남기려면 chown 에 숫자 uid:gid 를 지정하세요(자동 지정은 주 그룹).");
  // root·chown 지정 갈래엔 영향 없음
  expect(syncOwnership({ chown: "", root: true, runAs: null, self: true, chownEditable: false }).long)
    .not.toContain("관리자에게");
});

test("비 root + chmod: 권한 비트는 chmod 값이라고 말한다(소스 그룹 권한 문구 대신)", () => {
  const o = syncOwnership({ chown: "", chmod: "D770,F660", root: false, runAs: "alice", self: true });
  expect(o.long).toContain("권한 비트는 chmod 지정값(D770,F660), 수정 시각은 소스 그대로입니다.");
  expect(o.long).not.toContain("소스의 그룹 권한이 이 주 그룹에 적용");
});

test("이름을 아직 모르면(me 로딩) 이름 없이 '요청자 본인'", () => {
  expect(syncOwnership({ chown: "", root: false, runAs: null, self: true }).short)
    .toBe("요청자 본인의 uid:gid(주 그룹)");
});

test("다른 실행 신원(운영자 지정, 비 root): 그 사용자의 uid:gid", () => {
  expect(syncOwnership({ chown: "", root: false, runAs: "cocoa.song", self: false }).short)
    .toBe("실행 신원 cocoa.song의 uid:gid(주 그룹)");
});

test("root 실행: 소스 소유 보존 -- 이미 있던 같은 경로 항목 전부(최상위 포함)가 소스 것으로", () => {
  const o = syncOwnership({ chown: "", root: true, runAs: "mason", self: true });
  expect(o.short).toBe("소스의 소유자·그룹 그대로(root 실행)");
  expect(o.long).toContain("목적지에 이미 있던 같은 경로의 항목(최상위 디렉토리 포함)도 소유자·그룹·권한·시각이 소스 것으로 바뀝니다");
  expect(o.long).not.toContain("다른 소유로 남기려면");                 // root 는 소스 그룹을 보존 -- 안내 불필요
  expect(syncOwnership({ chown: "", chmod: "F640", root: true, runAs: null, self: true }).long)
    .toContain("(권한 비트는 chmod 지정값 F640)");
});

test("chown(숫자) + root: 그 값으로 셋업, 기존 같은 경로 항목도 -- '자동 지정' 언급 없음", () => {
  const o = syncOwnership({ chown: " 10003:10000 ", root: true, runAs: null, self: true });
  expect(o.short).toBe("chown 지정값 10003:10000");
  expect(o.long).toContain("chown 옵션으로 지정한 10003:10000 소유로 셋업됩니다 — 목적지에 이미 있던 같은 경로의 항목도 이 소유로 바뀝니다.");
  expect(o.long).not.toContain("자동 소유 지정");
  expect(syncOwnership({ chown: "  ", root: false, runAs: "alice", self: true }).short)
    .toBe("요청자 본인(alice)의 uid:gid(주 그룹)");
});

test("chown + 비 root(옛 행): uid 는 본인, gid 는 주 그룹만, 아니면 dsync 는 실패, nsync 는 건너뜀", () => {
  const o = syncOwnership({ chown: "10003:10000", root: false, runAs: "alice", self: true });
  expect(o.short).toBe("chown 지정값 10003:10000 — 비 root: uid 는 본인, gid 는 주 그룹만(아니면 실패)");
  expect(o.long).toContain("(자동 소유 지정은 꺼집니다)");
  expect(o.long).toContain("root 실행이 아니면 uid 는 실행 신원 본인, gid 는 실행 신원의 주 그룹이어야 하고, 아니면 바꿀 권한이 없어 ");
  expect(o.long).not.toContain("보조");
  // 다른 소유 안내는 기본(자동 지정) 문구의 몫 -- chown 을 이미 적었으면 붙지 않는다
  expect(o.long).not.toContain("다른 소유로 남기려면");
  expect(o.long).toContain("dsync(같은 노드) 는 데이터를 복사한 뒤 작업이 실패하고, nsync(노드 간) 는 소유 변경이 적용되지 않아 실행 신원 소유로 남습니다");
});

test("chown 한쪽만: 비워 둔 쪽은 소스 값이 유지된다고 말한다", () => {
  expect(chownIsPartial("10003")).toBe(true);
  expect(chownIsPartial(":10000")).toBe(true);
  expect(chownIsPartial("10003:")).toBe(false);            // 끝 콜론은 형식 오류(도구 "empty group") -- 부분 지정 아님
  expect(chownIsPartial("10003:10000")).toBe(false);
  expect(chownIsPartial("")).toBe(false);
  expect(syncOwnership({ chown: "10003", root: true, runAs: null, self: true }).long)
    .toContain("uid·gid 중 비워 둔 쪽은 소스 값이 유지됩니다.");
});

test("chown 의 이름은 지원하지 않는다(서버 거부) -- 문구는 상황 중립(단건·새 배치 폼에도 쓰인다)", () => {
  expect(chownHasName("10003:10000")).toBe(false);
  expect(chownHasName(":10000")).toBe(false);
  expect(chownHasName("cocoa.song:mig")).toBe(true);
  expect(chownHasName("10003:mig")).toBe(true);
  expect(chownHasName("")).toBe(false);
  const o = syncOwnership({ chown: "alice:10000", root: true, runAs: "alice", self: true });
  expect(o.short).toBe("chown alice:10000 — 이름은 지원하지 않음(숫자 uid:gid 로 지정)");
  expect(o.long).toContain("숫자 uid:gid 만 지정할 수 있습니다");
  expect(o.long).not.toMatch(/배치|남은 항목/);              // 배치 문맥 안내는 BatchDetail 몫
  expect(o.long).not.toContain("소유로 셋업됩니다");
});

test("chownFieldError: 숫자는 통과, 이름은 이름 문구, 그 밖의 모양은 형식 문구", () => {
  for (const ok of ["", "  ", "10003", "10003:10000", ":10000", "0:0"]) expect(chownFieldError(ok)).toBeNull();
  for (const name of ["alice", "alice:users", "10003:mig", ":mig", "root"])
    expect(chownFieldError(name)).toBe(CHOWN_NAME_ERROR);
  for (const bad of ["10003:", "1.5:10", "a b", "1:2:3"])
    expect(chownFieldError(bad)).toContain("형식이 올바르지 않습니다");
});

// 2026-10-26 사용자 결정: PV 목적지 root sync 의 결과는 목적지 PV 루트 소유자(서버 _auto_chown 의 pv_owner_measured 갈래).
test("root + PV 목적지: 목적지 PV 루트 소유자 -- 점검 전에는 「점검 뒤 확정」, 잰 뒤에는 그 값", () => {
  const before = syncOwnership({ chown: "", root: true, runAs: "ops", self: true, pvDestination: true });
  expect(before.short).toBe("목적지 PV 루트의 소유자·그룹(점검 뒤 확정)");
  expect(before.long).toContain("목적지 PV 루트의 소유자·그룹(uid:gid)으로 맞춰집니다");
  expect(before.long).toContain("실제 값은 점검(preflight)에서 확인돼 확인 창에 보입니다");
  expect(before.long).toContain("미리보기 뒤 그 소유자가 바뀌면 실행하지 않고 멈춥니다");
  const after = syncOwnership({ chown: "", root: true, runAs: "ops", self: true, pvDestination: true, pvOwner: "1000:100" });
  expect(after.short).toBe("목적지 PV 소유자 1000:100(PV 루트 기준)");
  expect(after.long).toContain("점검에서 확인한 값은 1000:100 입니다");
  // 형식이 틀린 값(DB 신뢰 경계)은 모름으로
  expect(syncOwnership({ chown: "", root: true, runAs: null, self: true, pvDestination: true, pvOwner: "x:y" }).short)
    .toBe("목적지 PV 루트의 소유자·그룹(점검 뒤 확정)");
});

test("PV 목적지여도 명시 chown 이 이기고, 직접 경로 목적지 root 는 종전대로 소스 소유 보존", () => {
  expect(syncOwnership({ chown: "7:7", root: true, runAs: "ops", self: true, pvDestination: true }).short)
    .toBe("chown 지정값 7:7");
  expect(syncOwnership({ chown: "", root: true, runAs: "ops", self: true, pvDestination: false }).short)
    .toBe("소스의 소유자·그룹 그대로(root 실행)");
});

// 2026-10-26 사용자 결정(2차): 「각 k8s 스토리지 항목마다 기본 uid,gid … sync 목적지가 pv 일 때 그 스토리지 기본 uid gid 로 chown.
// 사용자는 항상 자동, 운영자는 옵션으로 PV 루트 소유자」 -- 요청의 destination_pv.owner_rule(storage | pv_root | 없음 = 옛 행).
test("validOwner·ownerText: 숫자 uid:gid(각 0..4294967294)만 값, 0:0 은 「0:0(root)」 -- 0 은 정상값", () => {
  for (const ok of ["0:0", "1500:1500", "4294967294:0"]) expect(validOwner(ok)).toBe(ok);
  for (const bad of [null, undefined, 7, "", "x:y", "1500", "1500:", ":1500", "-1:0", "4294967295:0", "1:2:3", " 1:2"])
    expect(validOwner(bad)).toBeNull();
  expect(ownerText("0:0")).toBe("0:0(root)");
  expect(ownerText("0:100")).toBe("0:100");
  expect(ownerText("1500:1500")).toBe("1500:1500");
});

test("PV 목적지 storage 규칙: 목적지 스토리지 기본 소유자 -- 값을 알면 그 값(0:0 은 root 로), 모르면 「(uid:gid)」", () => {
  const o = syncOwnership({ chown: "", root: true, runAs: null, self: true, pvDestination: true, pvOwnerRule: "storage",
                            storageOwner: "1500:1500" });
  expect(o.short).toBe("목적지 스토리지 기본 소유자 1500:1500");
  expect(o.long).toContain("결과는 목적지 스토리지 기본 소유자 1500:1500 로 맞춰집니다");
  expect(o.long).toContain("컨펌 전에 관리자가 그 값을 바꾸면 실행하지 않고 멈춥니다(이미 컨펌한 작업은 확인 창에 보인 값으로 실행됩니다)");
  expect(o.long).toContain("일반 파일의 setuid·setgid 비트는 지워집니다");
  expect(o.long).not.toContain("PV 루트");
  const root = syncOwnership({ chown: "", root: true, runAs: null, self: true, pvDestination: true, pvOwnerRule: "storage",
                               storageOwner: "0:0" });
  expect(root.short).toBe("목적지 스토리지 기본 소유자 0:0(root)");
  expect(root.long).toContain("결과는 목적지 스토리지 기본 소유자 0:0(root) 로 맞춰집니다");
  const unknown = syncOwnership({ chown: "", root: true, runAs: null, self: true, pvDestination: true, pvOwnerRule: "storage" });
  expect(unknown.short).toBe("목적지 스토리지 기본 소유자(uid:gid)");
  expect(unknown.long).toContain("실제 값은 목적지 PV 를 고르면 보입니다");
  // 형식이 틀린 값(DB·응답 신뢰 경계)은 모름으로 -- 0 으로 뭉개지 않는다
  expect(syncOwnership({ chown: "", root: true, runAs: null, self: true, pvDestination: true, pvOwnerRule: "storage",
                         storageOwner: "-1:0" }).short).toBe("목적지 스토리지 기본 소유자(uid:gid)");
  // 명시 chown 이 이긴다
  expect(syncOwnership({ chown: "7:7", root: true, runAs: null, self: true, pvDestination: true, pvOwnerRule: "storage",
                         storageOwner: "1500:1500" }).short).toBe("chown 지정값 7:7");
});

test("PV 목적지 pv_root 규칙(운영자 옵션): 잰 PV 루트 소유자 + 「운영자 지정」, 옛 행(규칙 없음)은 종전 문구 그대로", () => {
  const before = syncOwnership({ chown: "", root: true, runAs: null, self: true, pvDestination: true, pvOwnerRule: "pv_root" });
  expect(before.short).toBe("목적지 PV 루트의 소유자·그룹(점검 뒤 확정 — 운영자 지정)");
  expect(before.long).toContain("결과 소유를 PV 루트 소유자로 골랐습니다(특권 운영자 옵션 — 기본은 목적지 스토리지 기본 소유자)");
  const after = syncOwnership({ chown: "", root: true, runAs: null, self: true, pvDestination: true, pvOwnerRule: "pv_root",
                                pvOwner: "1000:100", storageOwner: "1500:1500" });
  expect(after.short).toBe("목적지 PV 소유자 1000:100(PV 루트 기준 — 운영자 지정)");
  expect(after.long).not.toContain("1500:1500");                 // 스토리지 기본 소유자는 이 규칙에서 쓰이지 않는다
  const legacy = syncOwnership({ chown: "", root: true, runAs: null, self: true, pvDestination: true, pvOwner: "1000:100" });
  expect(legacy.short).toBe("목적지 PV 소유자 1000:100(PV 루트 기준)");
  expect(legacy.long).not.toContain("운영자");
  expect(syncOwnership({ chown: "", root: true, runAs: null, self: true, pvDestination: true, pvOwnerRule: null }).short)
    .toBe("목적지 PV 루트의 소유자·그룹(점검 뒤 확정)");
});

// 2026-10-26 사용자 결정 (4): 직접 경로 목적지의 스토리지가 PV 를 담는 종류면 그 스토리지 기본 소유자(pv 늘, both 는 PV 루트 아래만)
// -- 서버 payload.dest_owner(k8s_access.direct_dest_owner) 미러.
const ROOT_BASE = { chown: "", root: true, runAs: null, self: true } as const;

test("직접 경로 storage(확정): 「목적지 스토리지 기본 소유자 <v>(직접 경로)」 -- 0:0 은 root 로, 모르면 (uid:gid)", () => {
  const o = syncOwnership({ ...ROOT_BASE, directDest: { rule: "storage", owner: "1500:1500" } });
  expect(o.short).toBe("목적지 스토리지 기본 소유자 1500:1500(직접 경로)");
  expect(o.long).toContain("결과는 목적지 스토리지 기본 소유자 1500:1500 로 맞춰집니다");
  expect(o.long).toContain("직접 경로여도 목적지 스토리지가 PV 를 담는 종류라 PV 목적지와 같은 규칙입니다");
  expect(o.long).toContain("일반 파일의 setuid·setgid 비트는 지워집니다");          // --chown 이 넘어간다(0:0 도)
  expect(o.long).toContain("스토리지 종류·PV 를 바꿔 결과 소유가 달라지면 실행하지 않고 멈춥니다");
  expect(syncOwnership({ ...ROOT_BASE, directDest: { rule: "storage", owner: "0:0" } }).short)
    .toBe("목적지 스토리지 기본 소유자 0:0(root)(직접 경로)");
  for (const owner of [null, "-1:0", "x"])                                          // 모름·형식 오류는 값으로 보이지 않는다
    expect(syncOwnership({ ...ROOT_BASE, directDest: { rule: "storage", owner } }).short)
      .toBe("목적지 스토리지 기본 소유자(uid:gid)(직접 경로)");
});

test("직접 경로 both(폼): PV 루트 아래면 스토리지 기본 소유자, 아니면 소스 그대로 -- 화면은 PV 루트를 모른다고 정직하게", () => {
  const o = syncOwnership({ ...ROOT_BASE, directDest: { rule: "storage_if_under_pv", owner: "2500:2600" } });
  expect(o.short).toBe("PV 루트 아래면 목적지 스토리지 기본 소유자 2500:2600, 아니면 소스 그대로");
  expect(o.long).toContain("PV·파일시스템 겸용이라 결과 소유는 목적지 경로에 따라 갈립니다");
  expect(o.long).toContain("그 밖이면 root 실행이라 소스의 소유자·그룹을 그대로 유지합니다");
  expect(o.long).toContain("어느 쪽인지는 제출 때 서버가 정해 요청 상세의 「목적지 소유」에 보입니다");
});

test("배치 화면(varies): 기본(소스 그대로 + 2026-09-30 경고)은 그대로 말하고 PV 스토리지 예외를 덧붙인다", () => {
  const o = syncOwnership({ ...ROOT_BASE, directDest: { rule: "varies" } });
  expect(o.short).toBe("소스의 소유자·그룹 그대로(root 실행) — 단 목적지 스토리지가 PV 종류(겸용이면 PV 루트 아래)면 그 스토리지 기본 소유자");
  expect(o.long).toContain("목적지에 이미 있던 같은 경로의 항목(최상위 디렉토리 포함)도 소유자·그룹·권한·시각이 소스 것으로 바뀝니다.");
  expect(o.long).toContain("그 스토리지의 기본 소유자(uid:gid)로 맞춰지고 일반 파일의 setuid·setgid 비트는 지워집니다");
  expect(o.long).toContain("항목마다의 값은 자식 작업의 요청 상세 「목적지 소유」에 보입니다");
});

test("직접 경로 규칙의 우선순위: 명시 chown > PV 목적지 > 직접 경로 > root 보존, 비 root 옛 행은 그대로", () => {
  const direct = { rule: "storage", owner: "1500:1500" } as const;
  expect(syncOwnership({ ...ROOT_BASE, chown: "7:7", directDest: direct }).short).toBe("chown 지정값 7:7");
  expect(syncOwnership({ ...ROOT_BASE, pvDestination: true, pvOwnerRule: "storage", storageOwner: "9:9",
                         directDest: direct }).short).toBe("목적지 스토리지 기본 소유자 9:9");
  expect(syncOwnership({ ...ROOT_BASE, directDest: null }).short).toBe("소스의 소유자·그룹 그대로(root 실행)");
  expect(syncOwnership({ chown: "", root: false, runAs: "alice", self: true, directDest: direct }).short)
    .toBe("요청자 본인(alice)의 uid:gid(주 그룹)");
});
