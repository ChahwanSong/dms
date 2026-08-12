import config from "../tailwind.config";
// 슬라이스 31: 화면은 hex 를 모른다(전부 토큰 경유) -- 토큰이 지워지면 tailwind 는
// 클래스를 조용히 생성하지 않아 화면이 무색으로 깨진다. 존재를 여기서 못 박는다.
const colors = (config.theme?.extend?.colors ?? {}) as Record<string, string>;
for (const key of ["accent", "accenthover", "navy", "infobg", "panel", "line",
                   "ok", "okbg", "bad", "badbg", "busy", "busybg",
                   "canvas", "surface", "ink", "muted"]) {
  test(`토큰 ${key} 가 팔레트에 있다`, () => expect(colors[key]).toBeTruthy());
}
