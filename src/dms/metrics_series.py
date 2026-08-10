"""시계열 조립 순수 함수 -- DB/HTTP 접근 없음(rollout_status.py와 같은 원칙).

agent_reports의 report blob에서 노드 메트릭 포인트를 만든다. 계약(설계 §3):
- fail-soft: 샘플 하나가 깨져도(비 dict, os 없음, 시각 파싱 불가) 그 샘플만 버린다.
- 네트워크는 부팅 이후 누적 카운터다 -- throughput은 인접 샘플 차분으로 여기서
  계산해 프론트가 카운터 의미를 몰라도 되게 한다(설계 §3). 카운터가 감소하면
  (리부팅 리셋) 그 구간은 null -- 음수 대역폭을 그리는 것보다 빈 구간이 정직하다."""
# 사본이던 _epoch 를 db.iso_epoch 로 승격(슬라이스 17) -- 별칭 유지로 호출부 무변경.
from .db import iso_epoch as _epoch


def clamp_window_hours(window, *, retention_days: int) -> int:
    """조회 기간(시간)을 [1, 보존 상한]으로 클램프. retention이 그보다 오래된
    agent_reports를 지우므로(기본 30일=720h) 상한 밖 요청은 거절하지 않고 접는다 --
    운영자가 '한 달 치'를 요청했을 때 422보다 720h 데이터가 낫다(설계 §6-2)."""
    hours = 24 if window is None else int(window)
    return max(1, min(hours, retention_days * 24))


def bucket_chars_for(window_hours: int) -> int:
    """처리량 버킷의 SUBSTR 길이. ISO-8601 UTC 고정 포맷(utc_now_iso)이라 접두
    절단이 곧 시간 절단이다: 13자="YYYY-MM-DDTHH"(시간), 10자="YYYY-MM-DD"(일).
    48h 이하만 시간 버킷 -- 7일 창을 시간으로 쪼개면 막대 168개가 나온다."""
    return 13 if window_hours <= 48 else 10


def _num(value):
    # bool은 int의 서브클래스 -- True가 1.0으로 새면 안 된다
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _mem_used_pct(os_block):
    total = _num(os_block.get("memory_total_kb"))
    avail = _num(os_block.get("memory_available_kb"))
    if total is None or avail is None or total <= 0:
        return None
    # avail > total 은 오염된 os 리포트(used = total - avail 가 음수).
    # 모듈 전역이 fail-soft 이므로 여기도 클램프(포화 100)로 감추지 않고
    # "모름(None)" 으로 낸다 -- 음수/과장 사용률을 그리는 것보다 결측이 정직하다.
    if avail > total:
        return None
    return round((total - avail) / total * 100, 1)


def _disks(os_block):
    out = []
    disks = os_block.get("disks")
    # 비-리스트 disks(트루시 스칼라 5, dict, str 등)는 순회 시 TypeError -- 이는
    # 노드 하나가 아니라 라우트 전체를 500으로 죽인다. mem 필드가 오염 시 None으로
    # 강등되듯, 여기서도 "디스크 없음([])"으로 우아하게 강등한다(모듈 전역 fail-soft).
    if not isinstance(disks, list):
        return out
    for disk in disks:
        if not isinstance(disk, dict) or not isinstance(disk.get("storage_name"), str):
            continue
        total = _num(disk.get("total_bytes"))
        used = _num(disk.get("used_bytes"))
        # used > total 은 mem 과 같은 오염 신호(used_pct 가 100 초과) -- 같은 규칙으로
        # None. 항목 자체는 살린다(storage_name 은 유효하니 이름은 남긴다).
        valid = (total is not None and used is not None and total > 0 and used <= total)
        used_pct = round(used / total * 100, 1) if valid else None
        out.append({"storage_name": disk["storage_name"], "used_pct": used_pct})
    return out


def _rate(prev, cur, dt):
    # 감소 = 카운터 리셋(리부팅)으로 간주 -- 그 구간은 null(설계 §3)
    if prev is None or cur is None or dt <= 0 or cur < prev:
        return None
    return round((cur - prev) / dt, 1)


def build_node_points(samples: list[dict]) -> list[dict]:
    """MetricsRepository.node_series 출력(오름차순) -> 포인트 목록. 샘플 단위 fail-soft."""
    points = []
    prev_t = prev_rx = prev_tx = None
    for sample in samples:
        report = sample.get("report")
        os_block = report.get("os") if isinstance(report, dict) else None
        if not isinstance(os_block, dict):
            continue              # os 증거가 없는 리포트로는 포인트를 만들 수 없다
        try:
            t = _epoch(sample.get("reported_at"))
        except (TypeError, ValueError):
            continue              # 시각이 깨지면 차분의 축 자체가 없다
        rx = _num(os_block.get("network_rx_bytes"))
        tx = _num(os_block.get("network_tx_bytes"))
        net_rx = net_tx = None
        if prev_t is not None:
            dt = t - prev_t
            net_rx = _rate(prev_rx, rx, dt)
            net_tx = _rate(prev_tx, tx, dt)
        points.append({
            "at": sample["reported_at"],
            "load1": _num(os_block.get("load1")),
            "load5": _num(os_block.get("load5")),
            "load15": _num(os_block.get("load15")),
            "mem_used_pct": _mem_used_pct(os_block),
            "net_rx_bps": net_rx,
            "net_tx_bps": net_tx,
            "disks": _disks(os_block),
        })
        # 카운터가 None인 샘플을 지나면 prev도 None이 된다 -- 다음 구간도 null.
        # 마지막 유효 카운터를 기억하는 것보다 단순하고, 빈 구간 하나가 늘 뿐이다.
        prev_t, prev_rx, prev_tx = t, rx, tx
    return points


# (라벨, 상한초). 마지막 ">24h"는 상한 없음. 고정 순서로 내보내 빈 버킷도 0으로
# 남긴다 -- 프론트 막대 폭이 데이터에 따라 출렁이지 않게.
DURATION_BUCKETS = (("<1m", 60), ("1-10m", 600), ("10-60m", 3600),
                    ("1-6h", 21600), ("6-24h", 86400))

# 제출 대기(슬라이스 17)의 버킷. 수행시간 버킷(<1m 시작)을 그대로 쓰면 플래너 틱
# (10s)+스테퍼 틱(5s) 안에 끝나는 정상 대기가 전부 첫 버킷에 뭉쳐 분포가 사라진다 --
# 정상(<30s), 유예·지연(30s-5m: 신원 전파 유예 기본 300s), 백로그(>5m)가 구분되는
# 경계로 자른다.
SUBMIT_WAIT_BUCKETS = (("<10s", 10), ("10-30s", 30), ("30-60s", 60),
                       ("1-5m", 300), ("5-30m", 1800))
SUBMIT_WAIT_OVERFLOW = ">30m"


def duration_histogram(seconds: list, *, buckets=DURATION_BUCKETS,
                       overflow=">24h") -> list[dict]:
    counts = [0] * (len(buckets) + 1)
    for value in seconds:
        v = _num(value)
        # `if not v` 가 아니다 -- 0 은 버려야 할 결측이 아니라 실제 값이다(제출
        # 대기의 시각 해상도가 1초라 같은 초 픽업이 정상적으로 0 을 기록한다).
        if v is None or v < 0:
            continue
        for i, (_, upper) in enumerate(buckets):
            if v < upper:
                counts[i] += 1
                break
        else:
            counts[-1] += 1
    labels = [label for label, _ in buckets] + [overflow]
    return [{"bucket": label, "count": counts[i]} for i, label in enumerate(labels)]
