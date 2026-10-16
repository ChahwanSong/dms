import config from "../tailwind.config";
// 슬라이스 31: 화면은 hex 를 모른다(전부 토큰 경유) -- 토큰이 지워지면 tailwind 는
// 클래스를 조용히 생성하지 않아 화면이 무색으로 깨진다. 존재를 여기서 못 박는다.
// 값은 문자열이거나(평면 토큰) 객체다(stage 묶음) -- 존재만 본다.
const colors = (config.theme?.extend?.colors ?? {}) as Record<string, unknown>;
for (const key of ["accent", "accenthover", "navy", "infobg", "panel", "line",
                   "ok", "okbg", "bad", "badbg", "busy", "busybg", "attn", "attnbg",
                   "canvas", "surface", "ink", "muted"]) {
  test(`토큰 ${key} 가 팔레트에 있다`, () => expect(colors[key]).toBeTruthy());
}
// 큐 흐름 히어로(2026-10-16) 단계 파스텔 -- 면과 짝 잉크가 함께 있어야 한다(QueueFlowHero 의 SKIN 맵이 둘 다 쓴다).
const stage = colors.stage as Record<string, string>;
for (const key of ["batch", "planning", "pickup", "preflight", "volcano", "running", "done", "confirm"]) {
  test(`단계 토큰 stage-${key}·stage-${key}-ink 가 팔레트에 있다`, () => {
    expect(stage[key]).toBeTruthy();
    expect(stage[`${key}-ink`]).toBeTruthy();
  });
}
