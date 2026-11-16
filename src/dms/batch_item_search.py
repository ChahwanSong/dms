"""배치 상세의 항목 검색(2026-11-16 사용자 요청 「배치 상세에 사용량 분석처럼 검색 기능」 -- GET /api/admin/batches/{id}
?view=items&q=). 순수 판정 + 판정 재료 모으기(저장소 객체를 주입받는다 -- usage_attribution 과 같은 결).

뜻은 **사용량 분석 경로 검색과 같다**(usage_attribution.search_matches): 검색어를 text_search.search_tokens 로 나누고(공백 분리·
최대 8토큰·화살표와 「(PV 전체)」는 구분자), 토큰마다 대소문자 무시 부분 일치, 토큰 사이는 AND, 토큰마다 어느 칸이든 된다.
칸(hay)은 항목의 경로와 그 경로의 PV 이름(= PVC 이름)이다:
  scan·rm   target + target 의 PV 이름
  sync      source·destination + 각 경로의 PV 이름(출발·도착 스토리지마다 따로 판정해 합친다 -- 「models run」 = 출발 PV 이름
            models 이고 도착 경로에 run 이어도 걸린다. 한 항목은 한 쌍이라 칸을 합쳐도 다른 항목과 섞이지 않는다)
PV 이름이 필요한 이유: 사용자가 PV 피커로 만든 배치도 payload 에는 경로만 남는다(BatchCreate.k8s.test.tsx) -- 화면이 그 배치를
PV 이름으로 찾을 길이 이것뿐이다. PV 소속 판정은 사용량과 같은 usage_attribution.collect + attribute(지금 인벤토리의 가장 깊은 PV
루트, 없으면 PV 주소 scan 기록)다. PV 가 생길 수 없는 스토리지(may_attribute_pv False -- PV 종류가 아니고 PV 주소 scan 기록도
없다)는 collect 를 부르지 않고 경로만 본다(판정이 경로 일치와 같다).

스토리지 이름은 칸에 넣지 않는다 -- 배치는 스토리지 한 종류(쌍)라(domain.validate_batch) 거를 뜻이 없고, 넣으면 스토리지 이름의
낱말(「nfs」)이 모든 항목에 걸린다. payload 텍스트에 SQL LIKE 를 걸지 않는 이유: 저장 payload 는 dump_json 원문이라 키 이름
(「storage」·「target」)이 모든 행에 걸리고 이스케이프된 글자(따옴표·역슬래시)가 화면 글자와 맞지 않는다(db.dump_json).

dms-k8s 전용이다(dms-ssc 의 view=items q 는 경로 부분 문자열·대소문자 구분 -- API 모양 `q` 만 같다). 파일을 따로 둔 것은 두 라인을
골라서 cherry-pick 할 때 routes_batches 의 충돌을 줄이려는 것이다.
"""
from __future__ import annotations

import posixpath
from collections import defaultdict

from .repositories.text_search import search_tokens
from .usage_attribution import attribute, collect, may_attribute_pv

# (스토리지 키, 경로 키) -- scan·rm 은 첫 쌍, sync 는 뒤의 두 쌍. 연산으로 가르지 않는다: payload 의 모양이 곧 연산의 모양이고
# (build_data_payload), 모르는 키는 없는 것으로 본다(DB 가 신뢰 경계 -- 문자열이 아닌 값은 칸이 아니다).
_SIDES = (("storage", "target"), ("source_storage", "source"), ("destination_storage", "destination"))


def _attr_key(path: str) -> str:
    """PV 소속 판정에 넘길 경로 -- 저장 payload 는 받은 항목 그대로라(생성 검증은 정규화 결과를 저장하지 않는다) 「a/b/」·
    「./a」 같은 표기가 남을 수 있다. 사용량 대상(data_jobs.target)은 정규화된 상대경로라 같은 규칙(posixpath.normpath)으로 맞춘다.
    정규화가 뜻을 바꾸는 표기(절대경로·빈 값 -- 원래 생성 검증을 못 지나간다)는 그대로 둔다(판정은 그 글자 그대로 한다)."""
    if path == "" or path.startswith("/"):
        return path
    norm = posixpath.normpath(path)
    return path if norm == "." else norm


def _sides(payload) -> list:
    """payload → [(스토리지, 경로)] -- 둘 다 문자열인 쌍만. payload 가 dict 가 아니면(깨진 JSON → None) 빈 목록."""
    if not isinstance(payload, dict):
        return []
    out = []
    for skey, tkey in _SIDES:
        s, t = payload.get(skey), payload.get(tkey)
        if isinstance(s, str) and isinstance(t, str):
            out.append((s, t))
    return out


def matching_heads(repos, heads, q) -> list:
    """heads(BatchesRepository.list_item_heads 의 행 -- seq·payload·status) 중 검색어 q 에 걸리는 것(순서 유지). q 가 None·
    빈 문자열·공백뿐이면(토큰 없음) 거르지 않고 그대로 돌려준다. payload 가 깨진 행(None)은 어떤 검색어에도 걸리지 않는다.

    PV 소속 재료는 스토리지마다 한 번 모은다(collect -- 인벤토리 루트 index 는 저장소가 메모하고, PV 주소 scan 기록은 이 배치
    경로들만 묻는다). 판정 객체는 같은 루트끼리 공유된다(Inputs._memo) -- 수천 항목에서도 판정 비용은 경로 수에 선형이다."""
    tokens = [t.lower() for t in search_tokens(q)]
    if not tokens:
        return list(heads)
    wanted: dict = defaultdict(set)
    for h in heads:
        for s, t in _sides(h["payload"]):
            wanted[s].add(_attr_key(t))
    inputs = {s: collect(repos, s, [{"target": t} for t in sorted(paths)])
              for s, paths in wanted.items() if may_attribute_pv(repos, s)}

    def hay(payload) -> list:
        out = []
        for s, t in _sides(payload):
            out.append(t.lower())
            inp = inputs.get(s)
            if inp is not None:
                out += [v["pvc"].lower() for v in attribute(_attr_key(t), inp)["volumes"]
                        if isinstance(v.get("pvc"), str)]
        return out

    hits = []
    for h in heads:
        cells = hay(h["payload"])
        if cells and all(any(tok in c for c in cells) for tok in tokens):
            hits.append(h)
    return hits
