// 검색어 강조(2026-10-29 사용자 요청 「경로뿐 아니라 PV 이름도 검색 … 일부 단어만 포함해도 뜨도록」 -- 사용량 분석 경로별 표의
// 경로·PV 이름). 서버가 행을 건 규칙(text_search.search_tokens + usage_attribution.search_matches: 토큰마다 대소문자 무시 부분
// 일치, 토큰 사이 AND)과 같은 토큰·같은 비교로 맞은 글자를 <mark> 로 감싼다. 강조는 보여 주기일 뿐이다 -- 행을 거르지 않는다(거르는
// 것은 서버, 화면이 받은 행은 이미 걸린 것이다).
//
// React 노드로 나눈다(dangerouslySetInnerHTML 금지 -- 경로·PV 이름은 DB 가 신뢰 경계라 어떤 글자든 올 수 있다). 겹치거나 맞닿은
// 일치(「team te」 → 「team」 하나, 「aa」 in 「aaa」)는 한 덩어리로 합친다 -- <mark> 가 중첩·조각나지 않게.

/** 한 검색칸의 토큰 상한 -- 서버 text_search.MAX_SEARCH_TOKENS 와 같다(넘치는 토큰은 서버도 버린다). */
export const MAX_SEARCH_TOKENS = 8;

// 파이썬 str.split() 의 공백(유니코드 공백 + \x1c-\x1f·\x85). JS \s 는 그 둘을 빼고 ﻿ 를 넣는다 -- 앞의 둘만 더해 맞춘다
// (﻿ 하나가 다른 것은 강조 범위만 바꾼다).
const WS = /[\s\x1c-\x1f\x85]+/u;

/** 검색어 → 토큰. 서버 search_tokens 의 거울: NUL·화살표(→)는 구분자, 공백으로 나눔, 순서 유지·중복 제거(대소문자 구분 -- 낮추기는
 *  비교 때), 최대 MAX_SEARCH_TOKENS 개. 빈 값·공백뿐 = [] = 검색 없음. */
export function searchTokens(text: string | null | undefined): string[] {
  if (typeof text !== "string" || text === "") return [];
  const parts = text.replace(/[\0→]/g, " ").split(WS).filter((t) => t !== "");
  return [...new Set(parts)].slice(0, MAX_SEARCH_TOKENS);
}

// 대소문자 무시 비교용 사본 + 사본 글자 → 원문 위치. 코드 포인트마다 낮춘다(서로게이트 쌍을 가르지 않는다): 낮추면 길이가 바뀌는
// 글자(「İ」 → 「i̇」 두 글자)가 있어 사본의 위치를 원문에 그대로 쓸 수 없다. 끝 시그마(ς)는 σ 로 접는다 -- 서버(파이썬 lower())는
// 문자열 전체를 낮춰 낱말 끝 Σ 를 ς 로 만들고 글자 단위 낮추기는 σ 로 만들어, 「ΟΔΟΣ」를 「οσ」·「ος」로 쳤을 때 강조가 빠졌다.
const fold = (s: string) => s.toLowerCase().replace(/ς/g, "σ");
function folded(text: string): { low: string; start: number[]; end: number[] } {
  let low = "";
  const start: number[] = [], end: number[] = [];
  let i = 0;
  for (const ch of text) {
    const l = fold(ch);
    for (let k = 0; k < l.length; k++) { start.push(i); end.push(i + ch.length); }
    low += l;
    i += ch.length;
  }
  return { low, start, end };
}

/** text 에서 토큰(어느 것이든)이 대소문자 무시로 맞은 원문 구간들 [시작, 끝) -- 정렬되고 겹침·맞닿음은 합쳐진다. 빈 토큰은 없는
 *  것으로 본다(빈 문자열은 어디에나 「맞아」 전체를 강조하게 된다). */
export function matchRanges(text: string, tokens: readonly string[]): [number, number][] {
  if (text === "" || tokens.length === 0) return [];
  const { low, start, end } = folded(text);
  const hits: [number, number][] = [];
  for (const t of tokens) {
    const q = fold(t);
    if (q === "") continue;
    // 한 글자씩 밀어 겹치는 일치도 모두 찾는다(「aa」 in 「aaa」 = 0·1 -- 합치면 전체).
    for (let at = low.indexOf(q); at !== -1; at = low.indexOf(q, at + 1)) hits.push([start[at], end[at + q.length - 1]]);
  }
  hits.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const out: [number, number][] = [];
  for (const [s, e] of hits) {
    const last = out[out.length - 1];
    if (last !== undefined && s <= last[1]) last[1] = Math.max(last[1], e);
    else out.push([s, e]);
  }
  return out;
}

/** 토큰이 text 에 대소문자 무시로 들어 있나(서버 search_matches 의 한 토큰 판정과 같은 뜻). */
export const containsToken = (text: string, token: string): boolean => matchRanges(text, [token]).length > 0;

// 강조 모양: 형광펜(주의 바탕 토큰) + 글자색은 둘레 그대로(링크 파랑·흐린 글자를 바꾸지 않는다 -- UA 기본 mark 는 검정 글자).
// 여백·굵기를 바꾸지 않아 강조가 켜지고 꺼져도 글자 폭(말줄임 자리)이 그대로다.
export const MARK_CLASS = "rounded-sm bg-attnbg text-inherit";

/** text 를 그대로 그리되 토큰에 맞은 글자만 <mark>. 토큰이 없거나 맞은 데가 없으면 글자 그대로(감싸는 요소 없음). */
export function Highlight({ text, tokens, className = MARK_CLASS }: {
  text: string; tokens: readonly string[]; className?: string;
}) {
  const ranges = matchRanges(text, tokens);
  if (ranges.length === 0) return <>{text}</>;
  const parts: React.ReactNode[] = [];
  let at = 0;
  ranges.forEach(([s, e], i) => {
    if (s > at) parts.push(text.slice(at, s));
    parts.push(<mark key={i} className={className}>{text.slice(s, e)}</mark>);
    at = e;
  });
  if (at < text.length) parts.push(text.slice(at));
  return <>{parts}</>;
}
