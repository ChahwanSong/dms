import { expect, test } from "vitest";
import { CHOWN_NAME_ERROR, chownHasName, chownIsPartial, syncOwnership } from "./syncOwnership";
import { chownFieldError } from "../features/jobs/optionRules";

// 서버 execution_manifests._auto_chown 의 세 갈래 미러(chown 명시 > root 보존 > 실행 신원 uid:gid) +
// 검증 워크플로 2회(2026-10-01, 포크 소스·테스트베드 실측)가 확인한 도구 동작.

test("사용자(비 root·chown 없음): 요청자 본인 uid:gid(주 그룹), 소스 소유자와 무관", () => {
  const o = syncOwnership({ chown: "", root: false, runAs: "alice", self: true });
  expect(o.short).toBe("요청자 본인(alice)의 uid:gid(주 그룹)");
  expect(o.long).toContain("기본적으로 목적지(새로 만드는 경우 포함)와 복사된 파일·디렉토리는 요청자 본인(alice)의 "
    + "uid:gid(LDAP 계정의 주 그룹)로 셋업됩니다 — 소스 소유자와 관계없습니다.");
  expect(o.long).toContain("권한 비트·수정 시각은 소스 그대로라 소스의 그룹 권한이 이 주 그룹에 적용됩니다");
  // 이미 있던 남의 소유 항목: 도구·항목별로 결과가 갈려(검증 3차) "실패하거나 일부가 안 바뀔 수 있다" 로만 말한다
  expect(o.long).toContain("목적지에 이미 있던 같은 경로의 항목도 이 소유로 다시 맞춰지므로, 그 안에 다른 사용자 소유 항목이 "
    + "있으면 작업이 실패하거나 일부 항목의 소유·권한이 바뀌지 않을 수 있습니다.");
  expect(o.long).not.toContain("실행 신원");
  // 자동 지정은 주 그룹 -- 프로젝트(보조) 그룹 소유는 명시 chown(2026-10-07 D7)
  expect(o.long).toContain("프로젝트(보조) 그룹 소유로 남기려면 chown 에 uid:<그룹 gid> 를 지정하세요(자동 지정은 주 그룹).");
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
  expect(o.long).not.toContain("프로젝트(보조) 그룹");                  // root 는 소스 그룹을 보존 -- 안내 불필요
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

test("chown + 비 root: gid 는 소속 그룹(주·보조)만(계획 단계 거부), uid 가 본인이 아니면 dsync 는 실패, nsync 는 건너뜀", () => {
  const o = syncOwnership({ chown: "10003:10000", root: false, runAs: "alice", self: true });
  expect(o.short).toBe("chown 지정값 10003:10000 — 비 root: uid 는 본인, gid 는 소속 그룹만(아니면 거부·실패)");
  expect(o.long).toContain("(자동 소유 지정은 꺼집니다)");
  expect(o.long).toContain("root 실행이 아니면 gid 는 실행 신원이 속한 그룹(주·보조)이어야 하고(아니면 계획 단계에서 거부), "
    + "uid 가 실행 신원 본인이 아니면 바꿀 권한이 없어 ");
  expect(o.long).not.toContain("주 그룹 gid 외의 값");
  // 프로젝트 그룹 안내는 기본(자동 지정) 문구의 몫 -- chown 을 이미 적었으면 붙지 않는다
  expect(o.long).not.toContain("프로젝트(보조) 그룹 소유로 남기려면");
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
