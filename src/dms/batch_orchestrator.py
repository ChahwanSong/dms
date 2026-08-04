"""배치 오케스트레이터: 배치 자식(item)을 실제 request로 throttle-materialize하고,
자식 종단 상태를 집계해 배치를 완료시키는 controller-loop.

스캔 경로(이 파일): Running 배치에서 max_concurrency - in_flight 만큼 Queued item을
materialize하고, 자식 request가 종단이면 item을 종단화 + counts를 bump하며,
전 item이 종단이면 배치를 Completed로 전이한다.

sync 분기(Previewing/confirm/preview-expiry)는 이후 작업에서 `_drive`를 확장한다.
"""
from .domain import (Operation, RequestState, TERMINAL_REQUEST_STATES,
                     DataJobState, build_data_payload)

_ITEM_TERMINAL = {"Succeeded", "Failed", "Rejected", "Cancelled"}
_REQ_TERMINAL = {s.value for s in TERMINAL_REQUEST_STATES}


class BatchOrchestrator:
    def __init__(self, repos, *, settings):
        self._repos = repos
        self._settings = settings

    def run_once(self):
        for batch in self._repos.batches.list_active():
            self._drive(batch)

    def _child_state(self, request_id):
        req = self._repos.requests.get(request_id)
        if req is None:
            return ("in_flight", None)
        if req["state"] in _REQ_TERMINAL:
            return ("terminal", req["state"])
        jobs = self._repos.data_jobs.list_jobs(request_id=request_id)
        job = jobs[0] if jobs else None
        if job is not None and job["state"] == DataJobState.CONFIRM_PENDING.value:
            return ("previewed", job)
        return ("in_flight", None)

    def _record_terminal(self, batch_id, item, req_state):
        if req_state == RequestState.SUCCEEDED.value:
            self._repos.batches.set_item_status(batch_id, item["seq"], "Succeeded")
            self._repos.batches.bump_counts(batch_id, succeeded=1)
        else:
            status = "Rejected" if req_state == RequestState.REJECTED.value else "Failed"
            self._repos.batches.set_item_status(batch_id, item["seq"], status,
                                                reason_code=req_state)
            self._repos.batches.bump_counts(batch_id, failed=1)

    def _materialize(self, batch, item):
        payload, key = build_data_payload(batch["operation"], options=batch["options"],
                                          **item["payload"])
        rid = self._repos.requests.create(
            operation=batch["operation"], requester_id=batch["requester_id"],
            actor=batch["actor"], resource_key=key, payload=payload,
            priority="mid", batch_id=batch["batch_id"])
        self._repos.batches.set_item_materialized(batch["batch_id"], item["seq"], rid)

    def _drive(self, batch):
        bid = batch["batch_id"]
        items = self._repos.batches.list_items(bid)
        queued, in_flight, previewed, terminal = [], [], [], 0
        for item in items:
            st = item["status"]
            if st in _ITEM_TERMINAL:
                terminal += 1; continue
            if st == "Queued":
                queued.append(item); continue
            kind, info = self._child_state(item["request_id"])
            if kind == "terminal":
                self._record_terminal(bid, item, info); terminal += 1
            elif kind == "previewed":
                previewed.append((item, info))
            else:
                in_flight.append(item)
        total = len(items)
        if terminal == total:
            self._repos.batches.set_status(bid, "Completed")
            return
        if batch["status"] == "Running":
            slots = batch["max_concurrency"] - len(in_flight)
            for item in queued[:max(0, slots)]:
                self._materialize(batch, item)
