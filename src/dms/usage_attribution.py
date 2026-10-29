"""사용량 분석 필터의 PV 소속 판정(2026-10-29 사용자 요청: 사용량 분석을 사용자별·PV 이름·namespace 등으로 거르기) -- 순수
함수와 입력 모음(DB 를 직접 만지지 않는다: collect 가 저장소 객체를 주입받아 한 번씩 묻고, 판정은 그 결과만 본다).

무엇을 정하나: 사용량 목록의 한 머리(storage S, target t)가 **지금** 어느 PV(= PVC 정체 cluster·namespace·pvc·volume·
storage_class)에 속하는가. data_jobs 에는 PV 칸이 없고(스키마 무변경 -- 계획 시점 기록 안은 기각), 요청 payload 의 pv 블록은 PV
주소로 낸 scan 에만 있다(면제 운영자의 직접 경로 scan·배치 scan 은 없다). 그래서 읽을 때 정한다 -- 순서:

  1. inventory  지금 인벤토리(repositories.k8s.pv_root_index)에서 t 를 덮는 **가장 깊은** PV 루트 R 의 PVC 정체 전부(루트 충돌이면
                여럿). 덮음 = t == R 또는 t 가 R + '/' 로 시작(pv_root_covering·k8s_access.recheck_request 와 같은 글자 경계) --
                t 와 그 조상(마지막 '/' 앞까지씩 줄인 것)을 깊은 것부터 사전에서 찾는다(루트는 정규화돼 있어 같은 규칙이다).
  2. recorded   (1)이 없을 때 t 를 PV 주소로 scan 한 성공 요청의 pv 블록 중 최신의 **유효한** 하나(parse_block -- DB 가 신뢰 경계라
                모양·루트 포함을 다시 본다). 지운 PVC·바뀐 루트의 옛 scan 이 여기서 산다.
  3. unknown    S 가 등록돼 있지 않거나, PV 를 담는 종류(pv·both)인데 SC 매핑이 없거나, 한 번도 적용되지 않은 클러스터가 있다
                (그 클러스터의 PV 를 모른다 -- null ≠ 0: 「PV 밖」으로 접지 않는다).
  4. outside    나머지(filesystem 종류이거나 지금 어떤 PV 루트에도 속하지 않음 -- PV 루트의 상위 디렉터리도 여기다).

권한 사용자 = 소속 PVC 들의 (cluster, namespace)마다 지금 인식된 RoleBinding 의 dms_user 합집합(users_by_namespace -- 면제 운영자의
전체 접근은 바인딩이 아니라 여기 없다). 비활성·관리자 전용 스토리지도 소속은 있다(routes_k8s._mark_unusable_storages 를 쓰지
않는다 -- 그것은 「지금 고를 수 있나」이지 「어디에 속하나」가 아니다).

필터 결합(matches): PV 축(cluster·namespace·pvc·storage_class·user)은 **같은 정체 하나**가 모두 만족해야 한다(루트 충돌로 정체가
여럿이면 축을 정체마다 따로 맞춰 섞지 않는다). PV 축이 있으면 구분은 pv(inventory·recorded)로 본다 -- outside·unknown 과 함께면
0건(정상값).

검색(search_matches, 2026-10-29 사용자 요청: 「경로로 검색할 뿐 아니라, pv 이름도 검색할 수 있게 하고 일부 단어만 포함해도」): 토큰
(text_search.search_tokens -- 작업 목록 검색과 같은 뜻) **모두**가 그 대상의 경로 또는 소속 PV 정체 하나의 PV 이름(= PVC 이름 --
inventory·recorded 모두)에 부분 일치. 토큰마다 어느 쪽이든 된다. PV 이름이 생길 수 없는 스토리지(may_attribute_pv 가 False)는 이
판정이 경로 부분 일치와 같아 라우트가 SQL 로 건다(routes_usage._select_heads).
"""
from dataclasses import dataclass, field
from typing import Mapping

from .db import load_json
# 종류 집합의 정의는 repositories.k8s 한 곳이다(repositories/storages.py 주석 -- k8s_access 도 같은 것을 import 한다). 순수 함수와
# 상수만 쓰고 저장소 객체는 collect 의 인자로 받는다.
from .repositories.k8s import PV_CAPABLE_KINDS, storage_kind

SCOPES = ("pv", "outside", "unknown")
SOURCES = ("inventory", "recorded", "outside", "unknown")
PV_SOURCES = ("inventory", "recorded")
# facets 의 목록마다 상한(대상 수 내림차순 → 이름순으로 자른다 -- 넘으면 lists_truncated). 포탈 select 하나에 그 이상은 못 고른다.
FACET_LIST_CAP = 2000
FACET_LISTS = ("clusters", "namespaces", "pvcs", "storage_classes", "users")


@dataclass(frozen=True)
class PvFilter:
    """PV 쪽 필터(라우트가 이미 검증한 값 -- None = 그 축 없음, 빈 문자열은 라우트가 422 로 막는다)."""
    cluster: "str | None" = None
    namespace: "str | None" = None
    pvc: "str | None" = None
    storage_class: "str | None" = None
    user: "str | None" = None
    scope: "str | None" = None

    @property
    def pv_axes(self) -> bool:
        return any(v is not None for v in (self.cluster, self.namespace, self.pvc, self.storage_class, self.user))

    @property
    def active(self) -> bool:
        return self.pv_axes or self.scope is not None


@dataclass
class Inputs:
    """한 스토리지의 판정 재료(collect 가 한 번씩 모은다). rest = 인벤토리·기록 둘 다 없을 때의 판정(unknown | outside).
    _memo: 같은 루트(기록이면 같은 블록)의 판정은 같은 dict 를 돌려준다 -- 1만 대상 창에서도 판정 객체는 루트 수만큼이다
    (호출자는 바꾸지 않는다)."""
    index: "Mapping[str, tuple]"
    recorded: "Mapping[str, list]"
    users: "Mapping[tuple, list]"
    rest: str
    _memo: dict = field(default_factory=dict, repr=False, compare=False)


def covering_root(index, target) -> "str | None":
    """target 과 같거나 그 위인 index 의 루트 중 가장 깊은 것(없으면 None). 조상 = 마지막 '/' 앞까지씩 줄인 접두 -- 'R + /' 로
    시작한다는 것과 정확히 같은 집합이고, 깊은 것부터 찾으므로 첫 적중이 가장 깊다(pv_root_covering 의 「가장 긴 루트」와 같다)."""
    if not isinstance(target, str) or target == "":
        return None
    cand = target
    while True:
        if cand in index:
            return cand
        cut = cand.rfind("/")
        if cut < 0:
            return None
        cand = cand[:cut]


def parse_block(payload_text, target) -> "tuple | None":
    """요청 payload(TEXT JSON) → (root, 정체) 또는 None(유효하지 않음 -- 예외를 던지지 않는다). 유효: payload 가 dict 이고 pv 가
    dict, cluster·namespace·pvc 는 비어 있지 않은 문자열, volume·storage_class 는 문자열이거나 None, root 는 비어 있지 않은
    문자열이고 target 이 root 와 같거나 root + '/' 아래(제출 때 얼린 블록이 이 대상의 것인가 -- DB 가 신뢰 경계다)."""
    try:
        payload = load_json(payload_text) if isinstance(payload_text, str) else payload_text
    except (ValueError, RecursionError):
        return None
    if not isinstance(payload, dict):
        return None
    pv = payload.get("pv")
    if not isinstance(pv, dict):
        return None
    ident = (pv.get("cluster"), pv.get("namespace"), pv.get("pvc"), pv.get("volume"), pv.get("storage_class"))
    if not all(isinstance(x, str) and x != "" for x in ident[:3]):
        return None
    if not all(x is None or isinstance(x, str) for x in ident[3:]):
        return None
    root = pv.get("root")
    if not isinstance(root, str) or root == "":
        return None
    if not isinstance(target, str) or not (target == root or target.startswith(root + "/")):
        return None
    return root, ident


def collect(repos, storage, heads) -> Inputs:
    """한 스토리지(storage -- 정확한 이름, 빈 문자열도 이름)의 판정 재료를 한 번씩 모은다: 스토리지 행(등록·종류), PV 루트 index
    (메모), SC 매핑 유무, 미적용 클러스터 유무, 권한 사용자, 그리고 PV 주소 scan 기록이 있을 때만 **인벤토리가 덮지 않은** 대상의
    기록 블록(heads 의 target 들). repos 는 storages·k8s·data_jobs 저장소를 가진 객체다."""
    row = repos.storages.get(storage)
    pv_capable = row is not None and storage_kind(row) in PV_CAPABLE_KINDS
    index = repos.k8s.pv_root_index(storage) if pv_capable else {}
    if row is None:
        rest = "unknown"
    elif pv_capable and (not repos.k8s.maps_for_storage(storage) or repos.k8s.unsynced_cluster_exists()):
        rest = "unknown"
    else:
        rest = "outside"
    recorded: dict = {}
    if repos.data_jobs.has_recorded_pv_scans(storage):
        targets = [h["target"] for h in heads if covering_root(index, h["target"]) is None]
        recorded = repos.data_jobs.recorded_pv_blocks(storage, targets)
    users = repos.k8s.users_by_namespace() if (index or recorded) else {}
    return Inputs(index=index, recorded=recorded, users=users, rest=rest)


def may_attribute_pv(repos, storage) -> bool:
    """이 스토리지의 대상이 PV 소속(= PV 이름)을 가질 수 있나 -- 사용량 검색(q)을 판정 창에서 PV 이름까지 볼지, SQL 로 경로만 정확히
    걸지 가르는 문턱(routes_usage._select_heads). collect 의 두 원천 그대로다: PV 를 담는 종류의 등록 스토리지(인벤토리)이거나, PV 주소
    scan 기록이 있다. 기록 소속은 종류·등록과 무관하게 생긴다(지운 스토리지·종류를 filesystem 으로 바꾼 스토리지의 옛 PV 주소 scan --
    collect 는 종류를 보지 않고 기록을 묻는다) -- 그래서 「filesystem·미등록 = PV 이름 없음」으로 접지 않는다(접으면 표에 보이는 기록
    PV 이름으로 검색해도 그 행이 안 걸린다). False 이면 모든 판정의 volumes 가 비어 search_matches 가 경로 일치와 같다."""
    row = repos.storages.get(storage)
    if row is not None and storage_kind(row) in PV_CAPABLE_KINDS:
        return True
    return repos.data_jobs.has_recorded_pv_scans(storage)


def _attr(source, root, idents, users_by_namespace) -> dict:
    return {"source": source, "root": root,
            "volumes": [{"cluster": c, "namespace": ns, "pvc": p, "volume": v, "storage_class": sc}
                        for c, ns, p, v, sc in idents],
            "users": sorted({u for c, ns, *_ in idents for u in users_by_namespace.get((c, ns), ())})}


def attribute(target, inputs: Inputs) -> dict:
    """한 대상의 소속 → {source, root, volumes:[{cluster, namespace, pvc, volume, storage_class}], users:[…]}(모듈 docstring 의
    순서). 같은 판정은 같은 dict 다(Inputs._memo)."""
    memo = inputs._memo
    root = covering_root(inputs.index, target)
    if root is not None:
        key = ("inventory", root)
        if key not in memo:
            memo[key] = _attr("inventory", root, inputs.index[root], inputs.users)
        return memo[key]
    for text in inputs.recorded.get(target, ()):
        block = parse_block(text, target)
        if block is not None:
            key = ("recorded",) + block
            if key not in memo:
                memo[key] = _attr("recorded", block[0], (block[1],), inputs.users)
            return memo[key]
    key = (inputs.rest,)
    if key not in memo:
        memo[key] = {"source": inputs.rest, "root": None, "volumes": [], "users": []}
    return memo[key]


def _volume_matches(v: dict, flt: PvFilter, users_by_namespace) -> bool:
    return ((flt.cluster is None or v["cluster"] == flt.cluster)
            and (flt.namespace is None or v["namespace"] == flt.namespace)
            and (flt.pvc is None or v["pvc"] == flt.pvc)
            and (flt.storage_class is None or v["storage_class"] == flt.storage_class)
            and (flt.user is None or flt.user in users_by_namespace.get((v["cluster"], v["namespace"]), ())))


def matches(attr: dict, flt: PvFilter, users_by_namespace) -> bool:
    """판정 하나가 필터에 맞나(모듈 docstring 의 결합 규칙). users_by_namespace 는 그 판정을 만든 Inputs.users -- 권한 사용자 축을
    정체마다(그 정체의 namespace 바인딩으로) 본다."""
    if not flt.active:
        return True
    source = attr["source"]
    if not flt.pv_axes:
        return source in PV_SOURCES if flt.scope == "pv" else source == flt.scope
    if flt.scope not in (None, "pv") or source not in PV_SOURCES:
        return False
    return any(_volume_matches(v, flt, users_by_namespace) for v in attr["volumes"])


def search_matches(target, attr: dict, tokens) -> bool:
    """검색 토큰(search_tokens 의 결과 -- 빈 목록 = 검색 없음 = True)이 이 대상에 걸리나(모듈 docstring 「검색」). 토큰마다 대상
    경로 또는 attr 의 정체 중 하나의 pvc 에 부분 일치해야 하고(AND), 토큰마다 어느 쪽이든 된다 -- 「models run」 = PV 이름 models 이고
    경로에 run. 대소문자 무시는 대상·토큰 **양쪽 같은** str.lower() 다(같은 검색의 SQL 경로 -- data_jobs.scan_target_heads(tokens=) --
    는 양쪽 DB lower() 라 비 ASCII 대소문자는 방언에 따라 SQL 쪽이 덜 맞을 수 있다, text_search 모듈 docstring). 와일드카드는 없다
    ('%'·'_' 는 글자 그대로 -- SQL 쪽은 contains_pattern 의 이스케이프로 같은 뜻). 문자열이 아닌 경로·pvc(DB 가 신뢰 경계)는 어떤
    토큰에도 맞지 않는다."""
    if not tokens:
        return True
    hay = [target.lower()] if isinstance(target, str) else []
    hay += [v["pvc"].lower() for v in attr["volumes"] if isinstance(v.get("pvc"), str)]
    return all(any(t in h for h in hay) for t in (tok.lower() for tok in tokens))


def ranked(counts: dict, row, cap: int = FACET_LIST_CAP) -> tuple:
    """{키: 대상 수} → (행 목록(대상 수 내림차순 → 키 순, 상한 cap), 잘렸나). row(키, 수) → 응답 행."""
    items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [row(k, n) for k, n in items[:cap]], len(items) > cap


def facet_counts(attrs, *, cap: int = FACET_LIST_CAP) -> dict:
    """판정들(머리 창의 대상마다 하나) → facets 의 PV 부분: scope(source 마다 대상 수), clusters·namespaces·pvcs·storage_classes·
    users(옵션마다 그 옵션에 맞는 대상 수 -- 한 대상은 옵션마다 한 번만 센다: 루트 충돌로 정체가 여럿이어도), lists_truncated.
    pvcs 의 키는 포탈이 고를 때 싣는 (cluster, namespace, pvc) 이고 storage_class 는 인벤토리 판정의 값을 먼저(없으면 처음 본 기록의
    값) -- 지운 뒤 다른 SC 로 다시 만든 PVC 가 한 선택지에 두 줄로 서지 않게. storage_class 가 없거나 빈 정체는 StorageClass
    목록에 세지 않는다(그 값으로는 거를 수 없다)."""
    scope = dict.fromkeys(SOURCES, 0)
    clusters: dict = {}
    namespaces: dict = {}
    pvcs: dict = {}
    pvc_sc: dict = {}
    pvc_sc_inventory: set = set()
    classes: dict = {}
    users: dict = {}
    for a in attrs:
        scope[a["source"]] += 1
        seen_c, seen_ns, seen_p, seen_sc = set(), set(), set(), set()
        for v in a["volumes"]:
            seen_c.add(v["cluster"])
            seen_ns.add((v["cluster"], v["namespace"]))
            key = (v["cluster"], v["namespace"], v["pvc"])
            seen_p.add(key)
            if key not in pvc_sc or (a["source"] == "inventory" and key not in pvc_sc_inventory):
                pvc_sc[key] = v["storage_class"]
                if a["source"] == "inventory":
                    pvc_sc_inventory.add(key)
            if isinstance(v["storage_class"], str) and v["storage_class"] != "":
                seen_sc.add(v["storage_class"])
        for bucket, seen in ((clusters, seen_c), (namespaces, seen_ns), (pvcs, seen_p), (classes, seen_sc),
                             (users, set(a["users"]))):
            for k in seen:
                bucket[k] = bucket.get(k, 0) + 1
    out: dict = {"scope": scope}
    truncated: dict = {}
    out["clusters"], truncated["clusters"] = ranked(clusters, lambda k, n: {"cluster": k, "target_count": n}, cap)
    out["namespaces"], truncated["namespaces"] = ranked(
        namespaces, lambda k, n: {"cluster": k[0], "namespace": k[1], "target_count": n}, cap)
    out["pvcs"], truncated["pvcs"] = ranked(
        pvcs, lambda k, n: {"cluster": k[0], "namespace": k[1], "pvc": k[2], "storage_class": pvc_sc.get(k),
                            "target_count": n}, cap)
    out["storage_classes"], truncated["storage_classes"] = ranked(
        classes, lambda k, n: {"storage_class": k, "target_count": n}, cap)
    out["users"], truncated["users"] = ranked(users, lambda k, n: {"dms_user": k, "target_count": n}, cap)
    out["lists_truncated"] = truncated
    return out
