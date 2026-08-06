import { describe, it, expect } from "vitest";
import { REASON_MESSAGES } from "./api";
import BACKEND_CODES from "./reasonCodes.json";

// 백엔드가 실제로 낼 수 있는 사유 코드. 문자열 조립(f"prefix:{...}") 때문에 정적
// 추출이 완전할 수 없어 사람이 유지하는 목록을 둔다 -- 새 코드를 추가하면서 매핑을
// 빠뜨리면 이 테스트가 빨간불이 되는 것이 목적이다.
//
// 이 목록은 이제 reasonCodes.json 파일 하나다(과거에는 이 파일 안에 하드코딩된
// 배열이었다). tests/test_reason_codes_coverage.py가 같은 파일을 src/dms/의 실제
// detail=/reason_code=/예외 생성자 리터럴과 대조한다 -- 프론트/백엔드 두 "목록"이
// 따로 있으면 한쪽만 갱신됐을 때 어긋남을 아무도 못 잡는다(I2에서 지적된 바로 그
// 결함). 파일을 하나로 합치면 어긋날 여지 자체가 없다: 새 코드를 추가하려면 이
// JSON을 고쳐야 하고, 그러면 REASON_MESSAGES 커버리지(below)와 백엔드 추출
// 테스트가 동시에 그 변경을 본다.
const codes: string[] = BACKEND_CODES;

describe("REASON_MESSAGES 커버리지", () => {
  it("백엔드가 내는 모든 코드에 한국어 매핑이 있다", () => {
    const missing = codes.filter((c) => !(c in REASON_MESSAGES));
    expect(missing).toEqual([]);
  });

  it("죽은 키가 없다 -- 백엔드가 내지 않는 코드는 두지 않는다", () => {
    const allowed = new Set([...codes, "http_401", "http_422", "http_500", "http_503"]);
    const dead = Object.keys(REASON_MESSAGES).filter((k) => !allowed.has(k));
    expect(dead).toEqual([]);
  });
});
