"""대시보드 「큐 현황」 파이프라인 조립(2026-10-16) -- 순수 함수(DB·k8s 를 모른다).

사용자 요청(2026-10-16): 「대시보드에서 큐 현황에 대해 조금만 더 자세히 그리고 개선해서 보여주면 좋겠어.」
옛 카드는 Volcano PodGroup 만 보여 DMS 안쪽의 대기(계획·픽업·사전 점검·확인·배치 쓰로틀)가 통째로 안 보였다 --
테스트베드 실측으로 Volcano 대기는 늘 5~10초이고 정말 긴 대기는 사람의 확인(ConfirmPending p95 7.3h)이다. 그래서
요청이 지나가는 단계 전부를 한 줄(pipeline)로, 기다리는 항목을 한 표(waiting)로 낸다.

단계(STAGES -- 화면 띠의 순서이자 대기 목록 동률 정렬의 순서):
  planning     요청 Pending -- planner(기본 10s 주기)가 도구·노드를 정하기 전. 신원 전파 유예(기본 300s)도 여기다.
  pickup       잡 Pending -- 계획됐고 stepper(기본 5s 주기)가 집기 전. 드레인(control_state.drain)이면 여기서 멈춘다.
  preflight    잡 Preflight -- 노드 사전 점검 파드. 확인 뒤 실행 전 재점검(Executing 인데 execution ref 가 아직 없는 잡 --
               exec_preflight 파드, vcjob 이 없다)도 여기다(detail.exec_preflight). 예전엔 「Volcano 실행 중」에 들어가
               재점검 파드가 Pending 에 멈춰도 대기로 보이지 않았다(검증 지적).
  volcano_wait PodGroup Pending/Inqueue -- Volcano 가 gang 을 아직 못 올렸다(유일하게 k8s 에서 오는 단계).
  running      잡 PreviewRunning/Executing/Running 중 Volcano 대기·실행 전 재점검이 아닌 것 -- 대기가 아니라 띠에만 있다.
  confirm      잡 ConfirmPending 중 **사람이 지금 확인할 수 있는 것** -- 단건의 컨펌, 확인 대기(PreviewReady) 배치의 자식
               (배치별 한 줄로 묶는다).
  batch        배치가 차례를 쥔 항목 -- 아직 요청이 안 된 Queued 항목(동시 실행 상한) + 굴러가는 배치의 ConfirmPending
               자식(held: 이미 확인된 Running 배치에서 실행 슬롯을 기다리거나, Previewing 배치에서 형제 미리보기를
               기다린다). 배치별 한 줄. held 를 확인 대기에 세면 사람이 할 일이 없는데 「확인 대기 996 · 12시간」이 적색으로
               떴다(검증 지적). 아무도 굴리지 않는 배치(취소 중·취소됨·완료·행 없음)의 자식은 어느 칸에도 세지 않는다 --
               기다리는 것이 아니라 취소 정리를 기다린다(queue_batches 의 Queued 규칙과 같다). held 는 잡 축에서 오므로
               이 단계는 배치·잡 두 축을 다 읽었을 때만 낸다(하나라도 모르면 None -- 「대기 없음」으로 접지 않는다).

null(모름) ≠ 0: 재료 축이 읽히지 않은 단계는 None 이다({"count": 0} 이 아니다). 0 건 단계의 oldest_seconds 는 None 이고
count 0 이 「없음」을 말한다. 대기 목록(waiting)은 읽힌 단계의 항목만 담는다 -- 어느 단계가 빠졌는지는 pipeline 의 None
이 말하고, 화면은 그 단계를 「알 수 없음」으로 표시한다(목록에 없다고 「없음」으로 읽지 않게). 모든 대기 단계를 못 읽었으면
waiting 자체가 None 이다.

대기 목록 상한은 **단계마다**(WAITING_PER_STAGE)다. 전체에서 오래된 순 N 건으로 자르면, 몇 시간짜리 확인 대기가 상한을
다 채워 막 쌓이기 시작한 계획 대기가 목록에서 사라진다 -- 단계 필터를 눌러도 띠의 숫자와 다른 「없음」이 보인다. 잘린
단계는 waiting_truncated 가 이름으로 말한다.

시각은 전부 서버가 뺀다(나이·남은 시간) -- 브라우저 시계 스큐가 대기 시간을 왜곡하지 않게(라우트의 wait_seconds 와 같은
이유). 대조(PodGroup → 잡)는 표시용이다: 아무것도 지우거나 바꾸지 않으므로 이름 접두 폴백을 써도 된다(purge 의 「전체
job_id 로 확인된 것만」 규칙은 삭제 경로의 규칙이다)."""
from .db import iso_epoch
from .domain import PRIORITY_CLASS

STAGES = ("planning", "pickup", "preflight", "volcano_wait", "running", "confirm", "batch")
WAITING_STAGES = tuple(s for s in STAGES if s != "running")
WAITING_PER_STAGE = 20
# Volcano PodGroup phase 중 「아직 스케줄 전」. Running·Completed 는 대기가 아니다(옛 카드의 표 규칙 그대로).
PODGROUP_WAITING_PHASES = frozenset({"Pending", "Inqueue"})
RUNNING_JOB_STATES = ("PreviewRunning", "Executing", "Running")
# held(배치 대기로 세는 ConfirmPending 자식)의 배치 상태 -- orchestrator 가 실행 쪽으로 굴리는 것만(모듈 docstring 의 batch).
_HELD_BATCH_STATES = ("Previewing", "Running")
# 계획 유예·오류 이벤트 message 상한 -- plan_error 는 예외 문자열이라 길이가 정해져 있지 않다.
NOTE_MESSAGE_MAX = 200

_STAGE_ORDER = {s: i for i, s in enumerate(STAGES)}
_CLASS_PRIORITY = {v: k for k, v in PRIORITY_CLASS.items()}
# 같은 vcjob 이름을 두 잡이 주장하면(job_id[:12] 충돌·변조 행) 어느 쪽인지 모른다 -- 붙이지 않는다.
_AMBIGUOUS = object()


def age_seconds(now: float, iso) -> "int | None":
    """now(epoch 초) - iso. 시각이 없거나 깨졌으면 None(지어내지 않는다), 시계 스큐로 음수면 0."""
    if not isinstance(iso, str):
        return None
    try:
        return max(0, int(now - iso_epoch(iso)))
    except ValueError:
        return None


def until_seconds(now: float, iso) -> "int | None":
    """iso - now(남은 시간). 이미 지났으면 0 -- stepper 가 다음 틱에 만료시키기 직전의 찰나다."""
    if not isinstance(iso, str):
        return None
    try:
        return max(0, int(iso_epoch(iso) - now))
    except ValueError:
        return None


def _clip(text, limit):
    if not isinstance(text, str):
        return None
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _min_iso(values):
    # 시각은 고정 포맷 ISO-8601 UTC 라 문자열 최솟값 = 가장 이른 시각(SQL MIN 과 같은 비교). 없으면 None.
    vals = [v for v in values if isinstance(v, str)]
    return min(vals) if vals else None


def _stat(count: int, oldest_at, now: float) -> dict:
    # 0 건이면 「가장 오래」가 없다 -- None. count 가 0 이 아닌데 None 이면 시각이 깨진 것(모름)이다.
    return {"count": count, "oldest_seconds": age_seconds(now, oldest_at) if count > 0 else None}


def vcjob_index(submitted) -> dict:
    """{vcjob 이름: (잡 행, phase_refs 키)} -- phase_refs 의 "vcjob/<이름>" 값만(pod/·pods/ 는 PodGroup 이 없다)."""
    idx = {}
    for job in submitted:
        refs = job.get("phase_refs")
        if not isinstance(refs, dict):
            continue
        for phase, ref in refs.items():
            if not (isinstance(ref, str) and ref.startswith("vcjob/") and len(ref) > len("vcjob/")):
                continue
            name = ref[len("vcjob/"):]
            prev = idx.get(name)
            if prev is None:
                idx[name] = (job, phase)
            elif prev is not _AMBIGUOUS and prev[0].get("job_id") != job.get("job_id"):
                idx[name] = _AMBIGUOUS
    return idx


def match_podgroup(pg: dict, idx: dict):
    """PodGroup → (잡 행, phase 키) 또는 None. 소유자 참조(리더의 vcjob)가 있으면 그것만 믿는다. 없을 때만 이름
    으로 찾는다 -- 지금 Volcano 의 PodGroup 이름은 「<vcjob>-<uid>」라 '-' 자리마다 앞부분을 vcjob 이름 후보로 보고
    (정규식으로 DMS 명명 규칙(execution_manifests._job_name)이나 uid 모양에 묶이지 않게), 옛 Volcano 처럼 vcjob
    이름 그대로인 경우를 위해 전체 이름을 먼저 본다."""
    owner = pg.get("vcjob")
    if isinstance(owner, str) and owner:
        hit = idx.get(owner)
    else:
        hit = None
        name = pg.get("name")
        if isinstance(name, str):
            hit = idx.get(name)
            cut = name.rfind("-")
            while cut > 0 and hit is None:
                hit = idx.get(name[:cut])
                cut = name.rfind("-", 0, cut)
    return None if hit is _AMBIGUOUS else hit


def _entry(stage, now, *, since, request_id=None, job_id=None, operation=None, requester=None,
           priority=None, batch_id=None, detail=None) -> dict:
    return {"stage": stage, "request_id": request_id, "job_id": job_id, "operation": operation,
            "requester": requester, "priority": priority, "batch_id": batch_id,
            "since": since if isinstance(since, str) else None,
            "wait_seconds": age_seconds(now, since), "detail": detail or {}}


def _job_entry(stage, row, now, *, since=None, detail=None) -> dict:
    return _entry(stage, now, since=row.get("entered_at") if since is None else since,
                  request_id=row.get("request_id"), job_id=row.get("job_id"),
                  operation=row.get("operation"), requester=row.get("requester_id"),
                  priority=row.get("priority"), batch_id=row.get("batch_id"), detail=detail)


def _sort_key(e):
    # 오래 기다린 것이 위(표의 목적은 「무엇이 막혀 있나」). 나이를 모르는 항목은 맨 뒤 -- 0 초로 접어 앞에 끼우지
    # 않는다. 동률은 단계 순서 → 식별자로 고정(5초 폴링마다 행이 뒤섞여 보이지 않게).
    w = e["wait_seconds"]
    ident = e["request_id"] or e["batch_id"] or e["detail"].get("podgroup") or ""
    return (w is None, -w if w is not None else 0, _STAGE_ORDER[e["stage"]], ident)


def build_queue_view(now: float, *, pods, planning, jobs, batches,
                     per_stage: int = WAITING_PER_STAGE) -> dict:
    """{"pipeline", "waiting", "waiting_truncated"}. 재료 축(pods·planning·jobs·batches)은 각각 None(못 읽음)일 수
    있고, 그 축에 기대는 단계만 None 이 된다 -- 축 하나의 실패가 다른 단계를 지우지 않는다(라우트의 축별 fail-soft
    를 단계까지 잇는다). pods 는 리더 모양(read_podgroups), 나머지는 MetricsRepository.queue_* 의 반환 모양이다."""
    pipeline = dict.fromkeys(STAGES)
    lists, totals = {}, {}
    idx = vcjob_index(jobs["submitted"]) if jobs is not None else {}

    if planning is not None:
        pipeline["planning"] = _stat(planning["count"], planning["oldest_at"], now)
        rows = []
        for r in planning["rows"]:
            note = r.get("note")
            detail = ({"note": note.get("event_type"),
                       "message": _clip(note.get("message"), NOTE_MESSAGE_MAX)}
                      if isinstance(note, dict) else {})
            rows.append(_entry("planning", now, since=r.get("created_at"), request_id=r.get("request_id"),
                               operation=r.get("operation"), requester=r.get("requester_id"),
                               priority=r.get("priority"), batch_id=r.get("batch_id"), detail=detail))
        lists["planning"], totals["planning"] = rows, planning["count"]

    queued_job_ids = set()
    if pods is not None:
        waiting_pg = [pg for pg in pods if pg.get("phase") in PODGROUP_WAITING_PHASES]
        rows = []
        for pg in waiting_pg:
            hit = match_podgroup(pg, idx)
            detail = {"podgroup": pg.get("name"), "phase": pg.get("phase"),
                      "min_member": pg.get("min_member"), "reason": pg.get("reason"),
                      "message": pg.get("message"), "priority_class": pg.get("priority_class"),
                      "phase_ref": hit[1] if hit is not None else None}
            if hit is not None:
                queued_job_ids.add(hit[0].get("job_id"))
                rows.append(_job_entry("volcano_wait", hit[0], now, since=pg.get("created_at"), detail=detail))
            else:
                # 대조되지 않은 PodGroup(DMS 밖에서 같은 큐에 넣은 잡·잡 축을 못 읽음·종단 직후)도 큐를 막고 있으면
                # 대기다 -- 목록에서 빼지 않는다. 우선순위는 PriorityClass 이름이 DMS 것일 때만 되돌린다.
                rows.append(_entry("volcano_wait", now, since=pg.get("created_at"),
                                   priority=_CLASS_PRIORITY.get(pg.get("priority_class")), detail=detail))
        ages = [e["wait_seconds"] for e in rows if e["wait_seconds"] is not None]
        pipeline["volcano_wait"] = {"count": len(waiting_pg), "oldest_seconds": max(ages) if ages else None}
        lists["volcano_wait"], totals["volcano_wait"] = rows, len(waiting_pg)

    held_groups = None
    if jobs is not None:
        by_state = jobs["by_state"]

        def state_stat(state):
            s = by_state.get(state) or {}
            return s.get("count", 0), s.get("oldest_at")

        count, oldest = state_stat("Pending")
        pipeline["pickup"] = _stat(count, oldest, now)
        lists["pickup"] = [_job_entry("pickup", r, now) for r in jobs["waiting"].get("Pending", [])]
        totals["pickup"] = count

        # 실행 전 재점검: Executing 인데 실행 ref 가 아직 없다(exec_preflight 파드만). Volcano 대기로 대조된 잡은 제외 --
        # 같은 잡을 두 칸에 세지 않는다. phase_refs 가 깨진 행(None)은 가르지 못한다 -- 실행 중에 남긴다.
        exec_pf = [r for r in jobs["submitted"]
                   if r.get("state") == "Executing" and isinstance(r.get("phase_refs"), dict)
                   and not r["phase_refs"].get("execution") and r.get("job_id") not in queued_job_ids]
        exec_pf_ids = {r.get("job_id") for r in exec_pf}
        count, oldest = state_stat("Preflight")
        pipeline["preflight"] = _stat(count + len(exec_pf),
                                      _min_iso([oldest] + [r.get("entered_at") for r in exec_pf]), now)
        lists["preflight"] = ([_job_entry("preflight", r, now) for r in jobs["waiting"].get("Preflight", [])]
                              + [_job_entry("preflight", r, now, detail={"exec_preflight": True}) for r in exec_pf])
        totals["preflight"] = count + len(exec_pf)

        # 확인 대기 = 사람이 지금 누를 수 있는 것만(모듈 docstring 의 confirm·batch).
        singles = jobs.get("confirm_singles")
        ready = [g for g in jobs["confirm_batches"] if g.get("status") == "PreviewReady"]
        held_groups = [g for g in jobs["confirm_batches"] if g.get("status") in _HELD_BATCH_STATES]
        actionable = ([singles] if singles else []) + ready
        count = sum(g["n"] for g in actionable)
        confirm = _stat(count, _min_iso(g.get("oldest_at") for g in actionable), now)
        soonest = _min_iso(g.get("soonest_expiry") for g in actionable) if count > 0 else None
        confirm["soonest_expiry"] = soonest
        confirm["soonest_expiry_seconds"] = until_seconds(now, soonest)
        pipeline["confirm"] = confirm
        rows = [_job_entry("confirm", r, now, detail={
                    "expires_at": r.get("preview_expires_at"),
                    "expires_in_seconds": until_seconds(now, r.get("preview_expires_at"))})
                for r in jobs["waiting"].get("ConfirmPending", [])]
        for g in ready:
            rows.append(_entry("confirm", now, since=g.get("oldest_at"), operation=g.get("operation"),
                               requester=g.get("requester_id"), priority=g.get("priority"),
                               batch_id=g.get("batch_id"), detail={
                                   "jobs": g["n"], "batch_status": g.get("status"),
                                   "batch_name": g.get("name"),
                                   "expires_at": g.get("soonest_expiry"),
                                   "expires_in_seconds": until_seconds(now, g.get("soonest_expiry"))}))
        lists["confirm"] = rows
        totals["confirm"] = (singles["n"] if singles else 0) + len(ready)

        run_count = sum(state_stat(s)[0] for s in RUNNING_JOB_STATES)
        # PodGroup 을 읽었으면 Volcano 대기로 대조된 잡은 「실행 중」에서 뺀다 -- 같은 잡을 두 칸에 세지 않는다.
        # 못 읽었으면 뺄 수 없다: includes_volcano_wait 가 그 사실을 말한다(화면이 「Volcano 대기 포함」 표기).
        rest = [r for r in jobs["submitted"]
                if r.get("job_id") not in queued_job_ids and r.get("job_id") not in exec_pf_ids]
        running = _stat(max(0, run_count - len(queued_job_ids) - len(exec_pf)),
                        rest[0].get("entered_at") if rest else None, now)
        running["includes_volcano_wait"] = pods is None
        pipeline["running"] = running

    if batches is not None and held_groups is not None:
        by_batch = {g["batch_id"]: {**g, "held": 0, "held_oldest_at": None, "held_expiry": None}
                    for g in batches["groups"]}
        for g in held_groups:
            row = by_batch.get(g["batch_id"])
            if row is None:
                row = by_batch[g["batch_id"]] = {
                    "batch_id": g["batch_id"], "name": g.get("name"), "status": g.get("status"),
                    "operation": g.get("operation"), "requester_id": g.get("requester_id"),
                    "priority": g.get("priority"), "max_concurrency": g.get("max_concurrency"),
                    "queued": 0, "oldest_at": None}
            row.update(held=g["n"], held_oldest_at=g.get("oldest_at"), held_expiry=g.get("soonest_expiry"))
        rows = []
        for g in by_batch.values():
            rows.append(_entry("batch", now, since=_min_iso([g.get("oldest_at"), g.get("held_oldest_at")]),
                               operation=g.get("operation"), requester=g.get("requester_id"),
                               priority=g.get("priority"), batch_id=g.get("batch_id"), detail={
                                   "queued": g["queued"], "held": g["held"],
                                   "max_concurrency": g.get("max_concurrency"),
                                   "batch_status": g.get("status"), "batch_name": g.get("name"),
                                   "expires_at": g.get("held_expiry"),
                                   "expires_in_seconds": until_seconds(now, g.get("held_expiry"))}))
        ages = [e["wait_seconds"] for e in rows if e["wait_seconds"] is not None]
        queued = sum(g["queued"] for g in by_batch.values())
        held = sum(g["n"] for g in held_groups)
        total = queued + held
        pipeline["batch"] = {"count": total, "oldest_seconds": max(ages) if total > 0 and ages else None,
                             "queued": queued, "held": held,
                             "batches": len(rows), "preview_ready_batches": batches["preview_ready"]}
        lists["batch"], totals["batch"] = rows, len(rows)

    if not lists:
        return {"pipeline": pipeline, "waiting": None, "waiting_truncated": None}
    waiting, truncated = [], []
    for stage in WAITING_STAGES:
        if stage not in lists:
            continue
        rows = sorted(lists[stage], key=_sort_key)
        waiting.extend(rows[:per_stage])
        if max(totals[stage], len(rows)) > per_stage:
            truncated.append(stage)
    waiting.sort(key=_sort_key)
    return {"pipeline": pipeline, "waiting": waiting, "waiting_truncated": truncated}


# ---- 흐름(2026-10-16, 사용자 지시 「큐 대기가 일종의 파이프라인 이잖아? … 이 흐름을 단순히 숫자만 카드형태로 보여쥬는게
# 아니라, 시각적으로 흐름 형식으로 애니메이션화해서」) -- 대시보드 히어로의 입자(구간마다 최근 처리량)·「완료」 역의 재료.
# 위 build_queue_view 가 「지금 어디에 몇 건」이라면 이것은 「최근 창 동안 어디서 어디로 몇 건」이다.
#
# 엣지 = "<from 단계>><to 단계>". 단계는 STAGES 에 화면 전용 둘을 더한다: submit(직접 제출 유입), done(성공 종단).
# 실제 흐름은 직선이 아니다(모듈 docstring): 미리보기를 마친 잡은 확인 관문(단건 confirm · 배치 held)을 거쳐 실행 전
# 재점검으로 사전 점검에 **되돌아온다** -- 그래서 confirm>preflight·batch>preflight 가 있다.
#   submit>planning      요청 None→Pending, 배치 아님          batch>planning   같은 전이, 배치 자식(materialize)
#   planning>pickup      요청 Pending→Planned                   pickup>preflight 잡 Pending→Preflight
#   preflight>volcano_wait  Preflight→Running(scan 실행 제출) · Preflight→PreviewRunning(미리보기 제출) ·
#                        Executing→Executing(재점검 뒤 실행 제출 -- 상태는 그대로인 자기 전이)
#   running>confirm      PreviewRunning→ConfirmPending, 단건    running>batch    같은 전이, 배치 자식(held)
#   confirm>preflight    ConfirmPending→Executing, 사람의 컨펌  batch>preflight  같은 전이, actor batch-orchestrator
#   running>done         잡 →Succeeded(= done)
# volcano_wait>running 은 키가 없다: PodGroup 이 스케줄되는 순간은 전이로 남지 않는다(k8s 에서만 보인다) -- 들어온
# 수와 같다고 지어내지 않는다(화면이 「측정 없음」으로 그린다).
#
# 버리는 행(같은 사건의 사본): plan 행 전부(저장소가 이미 거른다) · 잡 None→Pending(요청 Pending→Planned 와 같은 계획
# 트랜잭션) · 요청 Planned→종단(잡 종단·고아 복구의 사본).
#
# 이탈(exits) = 종단 실패·거부·취소·만료로 흐름을 떠난 수를 **떠난 단계**로 센다. 갈래가 상태만으로 갈리지 않는 곳:
#   Executing 에서의 이탈은 재점검·실행 제출 실패면 사전 점검(저장소의 pre), 아니면 실행 중. PreviewRunning·Running 에서의
#   이탈은 Volcano 대기 중 차단과 실행 중 실패가 사유로만 갈려 실행 중으로 접는다(volcano_wait 이탈 키가 없는 이유).
#   ConfirmPending 에서의 이탈은 배치 자식이면 배치 대기, 아니면 확인 대기.
FLOW_EDGES = ("submit>planning", "batch>planning", "planning>pickup", "pickup>preflight",
              "preflight>volcano_wait", "running>confirm", "running>batch", "confirm>preflight",
              "batch>preflight", "running>done")
FLOW_EXIT_STAGES = ("planning", "pickup", "preflight", "running", "confirm", "batch")
_EXIT_STATES = frozenset({"Rejected", "Failed", "TimedOut", "Cancelled", "PreviewExpired", "Conflict"})
_TO_VOLCANO = {("Preflight", "Running"), ("Preflight", "PreviewRunning"), ("Executing", "Executing")}
_JOB_EXIT_STAGE = {"Pending": "pickup", "Preflight": "preflight", "PreviewRunning": "running", "Running": "running"}


def _flow_target(row):
    """전이 묶음 한 행 → ("edge", 키) · ("exit", 단계) · None(버림). row 는 MetricsRepository.queue_flow 의 rows 모양."""
    kind, f, t = row.get("kind"), row.get("f"), row.get("t")
    in_batch = row.get("in_batch") == 1
    if kind == "request":
        if f is None and t == "Pending":
            return ("edge", "batch>planning" if in_batch else "submit>planning")
        if f == "Pending" and t == "Planned":
            return ("edge", "planning>pickup")
        if f == "Pending" and t in _EXIT_STATES:
            return ("exit", "planning")
        return None
    if kind != "data_job" or f is None:
        return None
    if t == "Succeeded":
        return ("edge", "running>done")
    if f == "Pending" and t == "Preflight":
        return ("edge", "pickup>preflight")
    if (f, t) in _TO_VOLCANO:
        return ("edge", "preflight>volcano_wait")
    if f == "PreviewRunning" and t == "ConfirmPending":
        return ("edge", "running>batch" if in_batch else "running>confirm")
    if f == "ConfirmPending" and t == "Executing":
        return ("edge", "batch>preflight" if row.get("by_orch") == 1 else "confirm>preflight")
    if t not in _EXIT_STATES:
        return None
    if f == "ConfirmPending":
        return ("exit", "batch" if in_batch else "confirm")
    if f == "Executing":
        return ("exit", "preflight" if row.get("pre") == 1 else "running")
    stage = _JOB_EXIT_STAGE.get(f)
    return ("exit", stage) if stage is not None else None


def build_flow(raw) -> "dict | None":
    """{"window_seconds", "saturated", "edges", "exits", "done"} 또는 None(재료 축을 못 읽음 -- 모름). raw 는
    MetricsRepository.queue_flow 의 반환 모양. 창 안에 전이가 하나도 없으면 전 엣지 0 이다(읽었고 0 이었다 -- None 과
    다르다). saturated 면 수치는 하한이다(저장소 docstring)."""
    if raw is None:
        return None
    edges = dict.fromkeys(FLOW_EDGES, 0)
    exits = dict.fromkeys(FLOW_EXIT_STAGES, 0)
    for row in raw["rows"]:
        target = _flow_target(row)
        if target is None:
            continue
        n = int(row["n"])
        if target[0] == "edge":
            edges[target[1]] += n
        else:
            exits[target[1]] += n
    return {"window_seconds": raw["window_seconds"], "saturated": bool(raw["saturated"]),
            "edges": edges, "exits": exits, "done": edges["running>done"]}
