"""k8s 인벤토리 저장소(dms-k8s RBAC·PV 설계 §3·§4·§5, 2026-10-25).

무엇을 담나:
  - k8s_sync_settings(단일 행): 호출 노드 primary·secondary, 주기.
  - k8s_clusters: 등록한 외부 클러스터(API 서버·TLS·봉인 토큰)와 동기화 요약(last_*·연속 실패·대체 예약·config_rev).
  - k8s_sync_runs: 시도 1건 = 1행(claimed → received → applied | failed | expired | superseded | discarded).
  - 스냅숏 4표(k8s_storage_classes·k8s_pvs·k8s_pvcs·k8s_role_bindings)와 StorageClass → 스토리지 매핑.

동기화 흐름(누가 무엇을 쓰나):
  - API(에이전트 라우트): claim(CAS 로 run 생성) · receive(결과 수용 -- 성공은 payload 만 저장, 실패는 클러스터 요약까지).
    큰 스냅숏 적용을 API 단일 커넥션(db.py RLock)에서 하지 않는다.
  - 컨트롤러 k8s-inventory 루프: apply_run(클러스터마다 가장 새 received 하나만, diff 적용 + 요약 갱신을 한 트랜잭션) ·
    expire_overdue · cleanup_orphans. 적용은 run 상태 CAS(received → applied 영향 1)로 멱등하다 -- 롤아웃 겹침으로 루프가 두 벌
    돌아도 같은 run 을 두 번 적용하지 않고, applied_run_seq 보다 오래된 run 은 superseded 로 닫아 스냅숏이 과거로 돌아가지 않는다.
  - config_rev: 주소·토큰·TLS 가 바뀌면 +1. 바뀌기 전 설정으로 가져온 결과는 적용하지 않는다(discarded) -- 실패도 요약에 반영하지
    않는다(고친 뒤 옛 토큰의 401 이 「실패」로 보이지 않게).

조회(화면·다음 슬라이스의 제출 판정)는 **적용된 적 있는 클러스터**(applied_run_seq IS NOT NULL)의 행만 본다 -- 지웠다가 같은
이름으로 다시 만든 클러스터가 정리 전의 옛 행을 물려받지 않게. 한 번도 성공하지 않은 클러스터는 모름(k8s_mapping_unknown)이지
「권한 없음」이 아니다(null ≠ 0).
"""
import uuid

from .. import k8s_sync
from ..db import Database, dump_json, iso_plus, load_json, utc_now_iso
from ..domain import DomainValidationError
from ..k8s_rbac import derive_binding
from ..pv_paths import effective_template, resolve_root
from .text_search import contains_pattern, search_tokens

RUN_STATUSES = ("claimed", "received", "applied", "failed", "expired", "superseded", "discarded")
PV_CAPABLE_KINDS = ("pv", "both")
STORAGE_KINDS = ("filesystem", "pv", "both")
CHANGE_NAMES_MAX = 50
_RUN_LIST_COLUMNS = ("seq, run_id, cluster_name, node_name, role, trigger, status, claimed_at, deadline_at, finished_at, "
                     "applied_at, config_rev, reason, phase, http_status, error, server_version, duration_ms, counts, "
                     "warnings, changes")
# 감사·응답에 실리지 않는 칸(봉인 토큰은 비밀의 파생물이다 -- mail_settings 와 같은 규칙).
_SECRET_COLUMNS = ("token_enc",)
# 바뀌면 config_rev 를 올리는 칸(이 칸으로 가져온 결과만 유효하다).
CONFIG_COLUMNS = ("api_server", "tls_server_name", "ca_pem", "insecure_skip_tls_verify", "token_enc")
CLUSTER_EDITABLE = CONFIG_COLUMNS + ("enabled",)

# 스냅숏 표: (테이블, 키 칸, 값 칸, JSON 칸, data 키, changes 이름)
_SNAPSHOT = (
    ("k8s_storage_classes", ("name",), ("provisioner", "parameters", "reclaim_policy", "volume_binding_mode"),
     {"parameters"}, "storage_classes", "storage_classes"),
    ("k8s_pvs", ("name",), ("driver", "volume_handle", "volume_attributes", "nfs_server", "nfs_path", "storage_class",
                            "claim_namespace", "claim_name", "capacity", "access_modes", "phase", "reclaim_policy",
                            "volume_mode"),
     {"volume_attributes", "access_modes"}, "pvs", "pvs"),
    ("k8s_pvcs", ("namespace", "name"), ("uid", "storage_class", "volume_name", "phase", "capacity", "access_modes",
                                         "created_at"),
     {"access_modes"}, "pvcs", "pvcs"),
    ("k8s_role_bindings", ("namespace", "name"), ("role_kind", "role_name", "subjects", "dms_user", "access",
                                                  "subject_mismatch"),
     {"subjects"}, "role_bindings", "bindings"),
)
SNAPSHOT_TABLES = tuple(spec[0] for spec in _SNAPSHOT)


class RunClosed(Exception):
    """결과를 받을 수 없는 run(없음·다른 노드·이미 종단·기한 지남) -- 라우트가 409 로 바꾼다."""


def storage_kind(row) -> str:
    """storages.kind(NULL = filesystem, 알 수 없는 값도 filesystem -- 더 좁은 쪽)."""
    kind = (row or {}).get("kind")
    return kind if kind in STORAGE_KINDS else "filesystem"


def _clusters_join(alias: str) -> str:
    return f"JOIN k8s_clusters kc ON kc.name = {alias}.cluster_name AND kc.applied_run_seq IS NOT NULL"


def resolve_volume(pvc: dict, pv, sc_map, storage) -> dict:
    """PVC 한 행(+ 그 PV·SC 매핑·스토리지) → 해석 결과. 순수 함수 -- volumes()·resolve_pvc()·SC 예시가 같은 규칙을 쓴다.

    reason(선택 불가 사유, 다음 슬라이스의 제출 사유 코드와 같은 이름):
      pv_not_bound             PVC 가 Bound 가 아니거나 볼륨 이름이 없다
      pv_storageclass_unmapped StorageClass 매핑이 없다(또는 매핑된 스토리지가 없거나 PV 를 담는 종류가 아니다)
      pv_path_unresolved       경로 템플릿이 없거나 값이 없어 렌더할 수 없다(PV 정보 없음·claimRef 불일치 포함)
    """
    sc = pvc.get("storage_class") or (pv or {}).get("storage_class")
    access_modes = pvc.get("access_modes")
    if isinstance(access_modes, str):
        access_modes = load_json(access_modes) or []
    out = {"cluster": pvc["cluster_name"], "namespace": pvc["namespace"], "pvc": pvc["name"],
           "volume": pvc.get("volume_name") or None, "storage_class": sc, "phase": pvc.get("phase"),
           "capacity": pvc.get("capacity"), "access_modes": access_modes or [], "created_at": pvc.get("created_at"),
           "uid": pvc.get("uid"), "storage_name": None, "storage_kind": None, "root": None, "abs_root": None,
           "template": None, "template_source": None, "selectable": False, "reason": None, "problem": None}
    if pvc.get("phase") != "Bound" or not out["volume"]:
        out.update(reason="pv_not_bound", problem=f"PVC phase is {pvc.get('phase') or 'unknown'}")
        return out
    if sc_map is None:
        out.update(reason="pv_storageclass_unmapped", problem=f"StorageClass {sc or '(none)'} is not mapped")
        return out
    out["storage_name"] = sc_map["storage_name"]
    if storage is None:
        out.update(reason="pv_storageclass_unmapped", problem=f"mapped storage {sc_map['storage_name']} does not exist")
        return out
    out["storage_kind"] = storage_kind(storage)
    if out["storage_kind"] not in PV_CAPABLE_KINDS:
        out.update(reason="pv_storageclass_unmapped", problem=f"storage {storage['storage_name']} is not a PV storage")
        return out
    template, source = effective_template(sc_template=sc_map.get("path_template"),
                                          backend_type=storage.get("backend_type"))
    out.update(template=template, template_source=source)
    attrs, handle = None, None
    if pv is not None:
        claim = (pv.get("claim_namespace"), pv.get("claim_name"))
        if claim[0] and claim != (pvc["namespace"], pvc["name"]):
            out.update(reason="pv_path_unresolved", problem=f"PV claimRef is {claim[0]}/{claim[1]}")
            return out
        attrs = pv.get("volume_attributes")
        if isinstance(attrs, str):
            attrs = load_json(attrs)
        handle = pv.get("volume_handle")
    resolved = resolve_root(template=template, managed_root=storage.get("managed_root"), volume=out["volume"],
                            pvc=pvc["name"], namespace=pvc["namespace"], storage_class=sc,
                            volume_attributes=attrs, volume_handle=handle)
    if resolved["problem"] is not None:
        problem = resolved["problem"] if pv is not None else f"{resolved['problem']} (PV not synced)"
        out.update(reason="pv_path_unresolved", problem=problem)
        return out
    out.update(root=resolved["root"], abs_root=resolved["abs_root"], selectable=True)
    return out


def delete_maps_for_storage_in_txn(db: Database, storage_name: str, actor: str) -> list:
    """스토리지 삭제(StoragesRepository.delete)의 **같은 트랜잭션 안에서** 그 스토리지를 가리키는 SC 매핑을 지우고 감사를
    남긴다(설계 D10 -- sync_pairs 선례: 같은 이름으로 다시 등록된 스토리지가 옛 매핑을 물려받지 않게). 트랜잭션을 열지 않는다
    (db.transaction 은 중첩되지 않는다). 지운 매핑 행 목록을 돌려준다. 호출 연결은 다음 슬라이스(스토리지 라우트) 몫."""
    rows = db.query("SELECT * FROM k8s_storage_class_maps WHERE storage_name = :n ORDER BY storage_class",
                    {"n": storage_name})
    if rows:
        db.execute("DELETE FROM k8s_storage_class_maps WHERE storage_name = :n", {"n": storage_name})
        for row in rows:
            db.execute(
                """INSERT INTO audit_log (mutation_class, operation, target_key, actor, before_state, after_state, at)
                   VALUES ('k8s_storage_class_map', 'remove', :key, :actor, :b, NULL, :at)""",
                {"key": row["storage_class"], "actor": actor, "b": dump_json(row), "at": utc_now_iso()})
    return rows


class K8sRepository:
    def __init__(self, db: Database):
        self._db = db

    @property
    def _lock(self) -> str:
        return " FOR UPDATE" if self._db.dialect == "postgresql" else ""

    def _audit(self, mutation_class, operation, key, before, after, actor):
        self._db.execute(
            """INSERT INTO audit_log (mutation_class, operation, target_key, actor, before_state, after_state, at)
               VALUES (:mc, :op, :key, :actor, :b, :a, :at)""",
            {"mc": mutation_class, "op": operation, "key": key, "actor": actor,
             "b": dump_json(before) if before else None, "a": dump_json(after) if after else None,
             "at": utc_now_iso()})

    # --- 호출 노드·주기 -------------------------------------------------------------------------

    def get_settings(self):
        return self._db.query_one("SELECT * FROM k8s_sync_settings WHERE id = 1")

    def update_settings(self, *, primary_node, secondary_node, interval_seconds, actor):
        """세 칸을 통째로 쓴다(PUT 의미 -- 생략한 칸을 NULL 로 지우는 함정이 없도록 라우트가 세 값을 다 준다)."""
        now = utc_now_iso()
        with self._db.transaction():
            before = self.get_settings()
            self._db.execute("INSERT INTO k8s_sync_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
            self._db.execute(
                """UPDATE k8s_sync_settings SET primary_node = :p, secondary_node = :s, interval_seconds = :i,
                       updated_by = :by, updated_at = :at WHERE id = 1""",
                {"p": primary_node, "s": secondary_node, "i": interval_seconds, "by": actor, "at": now})
            after = self.get_settings()
            self._audit("k8s_sync_settings", "update", "k8s_sync_settings", before, after, actor)
        return after

    # --- 클러스터 -----------------------------------------------------------------------------------

    @staticmethod
    def redacted(row):
        """감사용 사본 -- 봉인 토큰은 "set"/None 으로만."""
        if row is None:
            return None
        out = {k: v for k, v in row.items() if k not in _SECRET_COLUMNS}
        out["token"] = "set" if row.get("token_enc") else None
        return out

    def list_clusters(self):
        return self._db.query("SELECT * FROM k8s_clusters ORDER BY name")

    def get_cluster(self, name):
        return self._db.query_one("SELECT * FROM k8s_clusters WHERE name = :n", {"n": name})

    def create_cluster(self, *, name, api_server, tls_server_name, ca_pem, insecure_skip_tls_verify, token_enc,
                       enabled, actor):
        now = utc_now_iso()
        with self._db.transaction():
            if self.get_cluster(name) is not None:
                raise DomainValidationError("k8s_cluster_exists", name)
            self._db.execute(
                """INSERT INTO k8s_clusters (name, api_server, tls_server_name, ca_pem, insecure_skip_tls_verify,
                       token_enc, enabled, consecutive_failures, config_rev, created_by, created_at, updated_by,
                       updated_at)
                   VALUES (:n, :api, :sni, :ca, :ins, :tok, :en, 0, 0, :by, :at, :by, :at)""",
                {"n": name, "api": api_server, "sni": tls_server_name, "ca": ca_pem,
                 "ins": 1 if insecure_skip_tls_verify else 0, "tok": token_enc, "en": 1 if enabled else 0,
                 "by": actor, "at": now})
            after = self.get_cluster(name)
            self._audit("k8s_cluster", "create", name, None, self.redacted(after), actor)
        return after

    def update_cluster(self, name, changes: dict, *, actor, guard=None):
        """보낸 칸만 바꾼다(changes ⊆ CLUSTER_EDITABLE). guard(before) 는 잠근 현재 행을 보고 거절(예외)할 기회 -- 「주소를
        바꾸면 토큰을 다시」 검사가 여기서 돈다(mail_settings 선례: 트랜잭션 밖 검사는 동시 저장 사이에서 새 토큰을 그 토큰을
        입력한 적 없는 주소에 묶을 수 있다).

        설정 칸(CONFIG_COLUMNS)이 실제로 바뀌면 config_rev +1 하고 sync_requested_at = 지금(바뀐 설정을 바로 확인한다).
        비활성 → 활성 전환도 sync_requested_at = 지금(활성 시각이 곧 due -- 설계 D16). 없는 클러스터 = KeyError."""
        unknown = set(changes) - set(CLUSTER_EDITABLE)
        if unknown:
            raise ValueError(f"unknown k8s_clusters columns: {sorted(unknown)}")
        now = utc_now_iso()
        with self._db.transaction():
            before = self._db.query_one(f"SELECT * FROM k8s_clusters WHERE name = :n{self._lock}", {"n": name})
            if before is None:
                raise KeyError(name)
            if guard is not None:
                guard(dict(before))
            norm = dict(changes)
            if "insecure_skip_tls_verify" in norm:
                norm["insecure_skip_tls_verify"] = 1 if norm["insecure_skip_tls_verify"] else 0
            if "enabled" in norm:
                norm["enabled"] = 1 if norm["enabled"] else 0
            config_changed = any(col in norm and norm[col] != before.get(col) for col in CONFIG_COLUMNS)
            enabling = norm.get("enabled") == 1 and not before.get("enabled")
            sets = ["updated_at = :at", "updated_by = :by"]
            params = {"n": name, "at": now, "by": actor}
            for col, value in norm.items():
                sets.append(f"{col} = :{col}")
                params[col] = value
            if config_changed:
                sets.append("config_rev = config_rev + 1")
            if config_changed or enabling:
                sets.append("sync_requested_at = :at")
            self._db.execute(f"UPDATE k8s_clusters SET {', '.join(sets)} WHERE name = :n", params)
            after = self.get_cluster(name)
            a = self.redacted(after)
            if "token_enc" in changes:
                a["token_change"] = "replaced" if before.get("token_enc") else "set"
            self._audit("k8s_cluster", "update", name, self.redacted(before), a, actor)
        return after

    def delete_cluster(self, name, *, actor):
        """클러스터 행만 지운다(스냅숏 행은 컨트롤러 cleanup_orphans 가 묶음 단위로 -- 큰 삭제를 API 커넥션에서 하지 않는다).
        조회는 클러스터 행과 조인하므로 지운 순간부터 그 스냅숏은 보이지 않는다. 동기화 이력은 보존 정리에 맡긴다."""
        with self._db.transaction():
            before = self.get_cluster(name)
            if before is None:
                raise KeyError(name)
            self._db.execute("DELETE FROM k8s_clusters WHERE name = :n", {"n": name})
            self._audit("k8s_cluster", "delete", name, self.redacted(before), None, actor)
        return before

    def request_sync(self, name) -> bool:
        """「지금 동기화」: sync_requested_at = 지금(다음 claim 에 due). 감사는 남기지 않는다(조회 권한으로 되는 무해한 요청)."""
        return self._db.execute_count(
            "UPDATE k8s_clusters SET sync_requested_at = :at WHERE name = :n", {"n": name, "at": utc_now_iso()}) == 1

    # --- 실행(run) ----------------------------------------------------------------------------------

    def active_cluster_names(self, now: str) -> set:
        """활성 run(claimed 이고 기한 안 ∪ received)이 있는 클러스터 -- 그동안 새 claim 을 만들지 않는다."""
        rows = self._db.query(
            """SELECT DISTINCT cluster_name FROM k8s_sync_runs
               WHERE status = 'received' OR (status = 'claimed' AND deadline_at >= :now)""", {"now": now})
        return {r["cluster_name"] for r in rows}

    def claim(self, *, node_name, role, now, interval, primary_configured, token_ok, deadline_seconds):
        """이 노드가 지금 처리할 run 하나(없으면 None). 후보 = 활성·토큰 열림·활성 run 없음·claim_trigger 가 trigger 를 주는
        클러스터, 대체(failover) → due 이른 순. 경합은 last_attempt_at CAS(+ config_rev)로 정리한다: 같은 클러스터를 두 노드가
        동시에 집으면 한쪽만 영향 1 행을 얻는다. 토큰은 여기서 열지 않는다(token_ok 는 열 수 있는지만 본다)."""
        active = self.active_cluster_names(now)
        candidates = []
        for c in self._db.query("SELECT * FROM k8s_clusters WHERE enabled = 1 ORDER BY name"):
            if c["name"] in active or not c.get("token_enc") or not token_ok(c["token_enc"]):
                continue
            trigger = k8s_sync.claim_trigger(c, role=role, now=now, interval=interval,
                                             primary_configured=primary_configured)
            if trigger is None:
                continue
            order = c.get("failover_after") if trigger == "failover" else k8s_sync.due_at(c, interval)
            candidates.append((0 if trigger == "failover" else 1, order or "", c["name"], c, trigger))
        candidates.sort(key=lambda t: t[:3])
        for _, _, _, c, trigger in candidates:
            seen = c.get("last_attempt_at")
            where = "last_attempt_at = :seen" if seen is not None else "last_attempt_at IS NULL"
            run_id = uuid.uuid4().hex
            with self._db.transaction():
                won = self._db.execute_count(
                    f"""UPDATE k8s_clusters SET last_attempt_at = :now, sync_requested_at = NULL, failover_after = NULL
                        WHERE name = :c AND enabled = 1 AND config_rev = :rev AND {where}""",
                    {"now": now, "c": c["name"], "rev": c["config_rev"], "seen": seen})
                if won != 1:
                    continue
                self._db.execute(
                    """INSERT INTO k8s_sync_runs (run_id, cluster_name, node_name, role, trigger, status, claimed_at,
                           deadline_at, config_rev)
                       VALUES (:r, :c, :node, :role, :trig, 'claimed', :now, :dl, :rev)""",
                    {"r": run_id, "c": c["name"], "node": node_name, "role": role, "trig": trigger, "now": now,
                     "dl": iso_plus(now, int(deadline_seconds) + k8s_sync.UPLOAD_GRACE_SECONDS),
                     "rev": c["config_rev"]})
            return {"run": self.get_run(run_id), "cluster": self.get_cluster(c["name"])}
        return None

    def get_run(self, run_id, *, with_payload=False):
        cols = "*" if with_payload else _RUN_LIST_COLUMNS
        return self._db.query_one(f"SELECT {cols} FROM k8s_sync_runs WHERE run_id = :r", {"r": run_id})

    def _record_failure_in_txn(self, run, *, reason, error, now, server_version=None) -> "int | None":
        """실패 run 의 클러스터 요약 갱신(같은 config_rev 일 때만). 반환 = 갱신 전 연속 실패 수(갱신 안 했으면 None)."""
        cluster = self._db.query_one(f"SELECT * FROM k8s_clusters WHERE name = :c{self._lock}",
                                     {"c": run["cluster_name"]})
        if cluster is None or cluster["config_rev"] != run["config_rev"]:
            return None
        failover = k8s_sync.failover_after_for(role=run["role"], trigger=run["trigger"], reason=reason, now=now)
        self._db.execute(
            f"""UPDATE k8s_clusters SET last_reason = :reason, last_error = :err,
                   consecutive_failures = consecutive_failures + 1
                   {", failover_after = :fo" if failover else ""}
                   {", server_version = :sv" if server_version else ""}
                WHERE name = :c""",
            {"reason": reason, "err": error, "fo": failover, "sv": server_version, "c": run["cluster_name"]})
        return cluster["consecutive_failures"] or 0

    def receive(self, run_id, *, node_name, now, ok, data=None, fingerprint=None, reason=None, phase=None,
                http_status=None, error=None, server_version=None, duration_ms=None, warnings=None) -> dict:
        """에이전트 결과 수용. run 이 그 노드의 claimed 이고 기한(업로드 유예 포함) 안일 때만 -- 아니면 RunClosed.
        성공은 정규화된 data 를 payload 로 저장(received -- 적용은 컨트롤러). 실패는 run failed + 클러스터 요약(사유·연속 실패·
        대체 예약)까지 즉시. 반환 {"status", "cluster", "previous_failures"}."""
        with self._db.transaction():
            run = self._db.query_one(f"SELECT * FROM k8s_sync_runs WHERE run_id = :r{self._lock}", {"r": run_id})
            if (run is None or run["node_name"] != node_name or run["status"] != "claimed"
                    or now > run["deadline_at"]):
                raise RunClosed(run_id)
            common = {"r": run_id, "now": now, "dur": duration_ms, "sv": server_version,
                      "w": dump_json(warnings) if warnings else None}
            if ok:
                self._db.execute(
                    """UPDATE k8s_sync_runs SET status = 'received', finished_at = :now, duration_ms = :dur,
                           server_version = :sv, warnings = :w, counts = :counts, payload = :payload
                       WHERE run_id = :r AND status = 'claimed'""",
                    {**common, "counts": dump_json(k8s_sync.snapshot_counts(data)),
                     "payload": dump_json({"fingerprint": fingerprint, "data": data})})
                return {"status": "received", "cluster": run["cluster_name"], "previous_failures": None}
            if reason not in k8s_sync.SYNC_REASONS:
                reason = "k8s_sync_bad_response"
            err = (error or "")[:500] or None
            self._db.execute(
                """UPDATE k8s_sync_runs SET status = 'failed', finished_at = :now, duration_ms = :dur,
                       server_version = :sv, warnings = :w, reason = :reason, phase = :phase, http_status = :hs,
                       error = :err, payload = NULL
                   WHERE run_id = :r AND status = 'claimed'""",
                {**common, "reason": reason, "phase": phase, "hs": http_status, "err": err})
            prev = self._record_failure_in_txn(run, reason=reason, error=err, now=now, server_version=server_version)
            return {"status": "failed", "cluster": run["cluster_name"], "previous_failures": prev, "reason": reason}

    def expire_overdue(self, now) -> list:
        """기한(업로드 유예 포함)이 지난 claimed → expired(k8s_sync_expired) + 클러스터 요약·대체 예약. 반환 = 바뀐 run 마다
        {"run_id", "cluster", "node_name", "previous_failures"}(이벤트용)."""
        out = []
        for run in self._db.query(
                "SELECT * FROM k8s_sync_runs WHERE status = 'claimed' AND deadline_at < :now ORDER BY seq",
                {"now": now}):
            with self._db.transaction():
                won = self._db.execute_count(
                    """UPDATE k8s_sync_runs SET status = 'expired', finished_at = :now, reason = 'k8s_sync_expired',
                           error = :err
                       WHERE run_id = :r AND status = 'claimed'""",
                    {"r": run["run_id"], "now": now,
                     "err": f"node {run['node_name']} did not report a result before the deadline"})
                if won != 1:
                    continue
                prev = self._record_failure_in_txn(run, reason="k8s_sync_expired", error=None, now=now)
            out.append({"run_id": run["run_id"], "cluster": run["cluster_name"], "node_name": run["node_name"],
                        "previous_failures": prev})
        return out

    def received_runs(self) -> dict:
        """클러스터 → received run 목록(seq 오름차순, payload 제외)."""
        out: dict = {}
        for r in self._db.query(f"SELECT {_RUN_LIST_COLUMNS} FROM k8s_sync_runs WHERE status = 'received' ORDER BY seq"):
            out.setdefault(r["cluster_name"], []).append(r)
        return out

    def supersede(self, run_id) -> bool:
        return self._db.execute_count(
            """UPDATE k8s_sync_runs SET status = 'superseded', payload = NULL, applied_at = :now
               WHERE run_id = :r AND status = 'received'""", {"r": run_id, "now": utc_now_iso()}) == 1

    def apply_run(self, run_id, *, now=None) -> dict:
        """received run 하나를 스냅숏에 diff 로 적용한다(한 트랜잭션). 반환 {"outcome": applied|discarded|superseded|skipped,
        "cluster", "previous_failures", "changes"}. 예외는 그대로 올린다(트랜잭션 롤백 -- 호출자가 fail_apply 로 닫는다)."""
        now = now or utc_now_iso()
        with self._db.transaction():
            won = self._db.execute_count(
                "UPDATE k8s_sync_runs SET status = 'applied', applied_at = :now WHERE run_id = :r AND status = 'received'",
                {"r": run_id, "now": now})
            if won != 1:
                return {"outcome": "skipped", "cluster": None}
            run = self.get_run(run_id, with_payload=True)
            name = run["cluster_name"]
            cluster = self._db.query_one(f"SELECT * FROM k8s_clusters WHERE name = :c{self._lock}", {"c": name})
            if cluster is None or cluster["config_rev"] != run["config_rev"]:
                why = "cluster was deleted" if cluster is None else "cluster settings changed after the claim"
                self._db.execute("UPDATE k8s_sync_runs SET status = 'discarded', payload = NULL, error = :e "
                                 "WHERE run_id = :r", {"r": run_id, "e": why})
                return {"outcome": "discarded", "cluster": name}
            if cluster.get("applied_run_seq") is not None and run["seq"] <= cluster["applied_run_seq"]:
                self._db.execute("UPDATE k8s_sync_runs SET status = 'superseded', payload = NULL WHERE run_id = :r",
                                 {"r": run_id})
                return {"outcome": "superseded", "cluster": name}
            payload = load_json(run["payload"]) or {}
            data = payload.get("data")
            if not isinstance(data, dict):
                raise ValueError("received run has no data")
            changes = self._apply_snapshot(name, data)
            self._db.execute(
                "UPDATE k8s_sync_runs SET changes = :ch, payload = NULL WHERE run_id = :r",
                {"r": run_id, "ch": dump_json(changes)})
            self._db.execute(
                """UPDATE k8s_clusters SET last_success_at = :fin, consecutive_failures = 0, last_reason = NULL,
                       last_error = NULL, applied_run_seq = :seq, fingerprint = :fp,
                       server_version = COALESCE(:sv, server_version)
                   WHERE name = :c""",
                {"fin": run.get("finished_at") or now, "seq": run["seq"], "fp": payload.get("fingerprint"),
                 "sv": run.get("server_version"), "c": name})
            return {"outcome": "applied", "cluster": name, "previous_failures": cluster["consecutive_failures"] or 0,
                    "changes": changes}

    def fail_apply(self, run_id, *, error, now=None) -> "dict | None":
        """apply_run 이 예외로 롤백된 run 을 failed(k8s_sync_apply_failed)로 닫는다(별도 트랜잭션). 스냅숏은 그대로다."""
        now = now or utc_now_iso()
        with self._db.transaction():
            run = self._db.query_one(f"SELECT * FROM k8s_sync_runs WHERE run_id = :r{self._lock}", {"r": run_id})
            if run is None or run["status"] != "received":
                return None
            err = (error or "")[:500] or None
            self._db.execute(
                """UPDATE k8s_sync_runs SET status = 'failed', reason = 'k8s_sync_apply_failed', error = :e,
                       payload = NULL WHERE run_id = :r""", {"r": run_id, "e": err})
            prev = self._record_failure_in_txn(run, reason="k8s_sync_apply_failed", error=err, now=now)
        return {"cluster": run["cluster_name"], "previous_failures": prev}

    # --- 스냅숏 diff 적용 ---------------------------------------------------------------------------

    def _apply_snapshot(self, cluster: str, data: dict) -> dict:
        changes = {}
        for table, keys, cols, json_cols, data_key, change_name in _SNAPSHOT:
            items = data.get(data_key)
            if items is None:
                changes[change_name] = None        # 그 목록은 이번에 못 가져왔다(403) -- 이전 값 유지
                continue
            if table == "k8s_role_bindings":
                items = [{**it, **derive_binding(it)} for it in items]
            changes[change_name] = self._diff_apply(table, cluster, items, keys, cols, json_cols)
        return changes

    def _diff_apply(self, table, cluster, items, keys, cols, json_cols) -> dict:
        existing = {tuple(r[k] for k in keys): r for r in
                    self._db.query(f"SELECT * FROM {table} WHERE cluster_name = :c", {"c": cluster})}
        new = {}
        for it in items:
            row = {k: it[k] for k in keys}
            for col in cols:
                value = it.get(col)
                row[col] = dump_json(value if value is not None else ([] if col in ("access_modes", "subjects")
                                                                       else {})) if col in json_cols else value
            new[tuple(row[k] for k in keys)] = row
        added = [k for k in new if k not in existing]
        removed = [k for k in existing if k not in new]
        changed = [k for k in new if k in existing and any(existing[k].get(c) != new[k][c] for c in cols)]
        self._insert_many(table, cluster, ("cluster_name",) + keys + cols, [new[k] for k in added])
        where_keys = " AND ".join(f"{k} = :k_{k}" for k in keys)
        for k in changed:
            params = {"cluster": cluster, **{f"k_{key}": v for key, v in zip(keys, k)},
                      **{f"v_{c}": new[k][c] for c in cols}}
            self._db.execute(
                f"UPDATE {table} SET {', '.join(f'{c} = :v_{c}' for c in cols)} "
                f"WHERE cluster_name = :cluster AND {where_keys}", params)
        for k in removed:
            self._db.execute(f"DELETE FROM {table} WHERE cluster_name = :cluster AND {where_keys}",
                             {"cluster": cluster, **{f"k_{key}": v for key, v in zip(keys, k)}})

        def names(ks):
            return sorted("/".join(str(p) for p in k) for k in ks)[:CHANGE_NAMES_MAX]
        return {"added": len(added), "removed": len(removed), "changed": len(changed),
                "names": {"added": names(added), "removed": names(removed), "changed": names(changed)}}

    def _insert_many(self, table, cluster, columns, rows):
        """다행 INSERT 묶음(첫 적용은 전부 INSERT) -- 묶음당 바인딩 수를 900 이하로(옛 sqlite 상한 999)."""
        if not rows:
            return
        chunk = max(1, 900 // len(columns))
        for start in range(0, len(rows), chunk):
            part = rows[start:start + chunk]
            params = {}
            values = []
            for i, row in enumerate(part):
                names = []
                for col in columns:
                    key = f"p{i}_{col}"
                    params[key] = cluster if col == "cluster_name" else row[col]
                    names.append(f":{key}")
                values.append(f"({', '.join(names)})")
            self._db.execute(f"INSERT INTO {table} ({', '.join(columns)}) VALUES {', '.join(values)}", params)

    # --- 정리·보존 --------------------------------------------------------------------------------

    def cleanup_orphans(self, *, batch: int = 1000) -> int:
        """지운 클러스터의 스냅숏 행을 표마다 최대 batch 행씩 지운다(한 틱 예산 안 -- 남은 것은 다음 틱). 지운 행 수."""
        rid = "ctid" if self._db.dialect == "postgresql" else "rowid"
        total = 0
        for table in SNAPSHOT_TABLES:
            orphans = self._db.query(
                f"""SELECT DISTINCT cluster_name FROM {table}
                    WHERE cluster_name NOT IN (SELECT name FROM k8s_clusters)""")
            for row in orphans:
                total += self._db.execute_count(
                    f"""DELETE FROM {table} WHERE {rid} IN
                        (SELECT {rid} FROM {table} WHERE cluster_name = :c LIMIT :n)""",
                    {"c": row["cluster_name"], "n": batch})
                if total >= batch:
                    return total
        return total

    def prune_runs(self, cutoff_iso: str, *, keep_per_cluster: int = 50, batch_size: int = 5000) -> int:
        """보존 정리: cutoff 보다 오래된 종단 run 을 지운다. 클러스터마다 최근 keep_per_cluster 건과 마지막 applied run 은
        남긴다(화면의 「마지막 성공」이 이력에서 사라지지 않게). 활성 run(claimed·received)은 건드리지 않는다."""
        total = 0
        for row in self._db.query("SELECT DISTINCT cluster_name FROM k8s_sync_runs"):
            c = row["cluster_name"]
            nth = self._db.query_one(
                "SELECT seq FROM k8s_sync_runs WHERE cluster_name = :c ORDER BY seq DESC LIMIT 1 OFFSET :o",
                {"c": c, "o": keep_per_cluster - 1})
            if nth is None:
                continue
            last_applied = self._db.query_one(
                "SELECT MAX(seq) AS s FROM k8s_sync_runs WHERE cluster_name = :c AND status = 'applied'", {"c": c})
            keep = last_applied["s"] if last_applied and last_applied["s"] is not None else -1
            while True:
                with self._db.transaction():
                    ids = [r["seq"] for r in self._db.query(
                        """SELECT seq FROM k8s_sync_runs
                           WHERE cluster_name = :c AND seq < :thr AND claimed_at < :cutoff AND seq <> :keep
                                 AND status NOT IN ('claimed', 'received')
                           ORDER BY seq LIMIT :n""",
                        {"c": c, "thr": nth["seq"], "cutoff": cutoff_iso, "keep": keep, "n": batch_size})]
                    if not ids:
                        break
                    params = {f"i{k}": v for k, v in enumerate(ids)}
                    self._db.execute(
                        f"DELETE FROM k8s_sync_runs WHERE seq IN ({', '.join(':' + k for k in params)})", params)
                    total += len(ids)
        return total

    # --- 이력 조회 ---------------------------------------------------------------------------------

    def list_runs(self, *, cluster=None, node=None, before=None, limit=50) -> list:
        where, params = [], {"lim": limit}
        if cluster:
            where.append("cluster_name = :c")
            params["c"] = cluster
        if node:
            where.append("node_name = :node")
            params["node"] = node
        if before is not None:
            where.append("seq < :before")
            params["before"] = before
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        return self._db.query(
            f"SELECT {_RUN_LIST_COLUMNS} FROM k8s_sync_runs {clause} ORDER BY seq DESC LIMIT :lim", params)

    def latest_run_per_cluster(self) -> dict:
        rows = self._db.query(
            f"""SELECT {_RUN_LIST_COLUMNS} FROM k8s_sync_runs
                WHERE seq IN (SELECT MAX(seq) FROM k8s_sync_runs GROUP BY cluster_name)""")
        return {r["cluster_name"]: r for r in rows}

    def node_run_stats(self) -> dict:
        """노드 → {last_claim_at, last_ok_at, last_fail_at, last_reason}(개요의 호출 노드 카드)."""
        out: dict = {}
        for r in self._db.query("SELECT node_name, MAX(claimed_at) AS t FROM k8s_sync_runs GROUP BY node_name"):
            out.setdefault(r["node_name"], {})["last_claim_at"] = r["t"]
        for r in self._db.query(
                """SELECT node_name, MAX(finished_at) AS t FROM k8s_sync_runs
                   WHERE status IN ('received', 'applied') GROUP BY node_name"""):
            out.setdefault(r["node_name"], {})["last_ok_at"] = r["t"]
        for r in self._db.query(
                f"""SELECT node_name, finished_at, reason FROM k8s_sync_runs
                    WHERE seq IN (SELECT MAX(seq) FROM k8s_sync_runs
                                  WHERE status IN ('failed', 'expired') GROUP BY node_name)"""):
            entry = out.setdefault(r["node_name"], {})
            entry["last_fail_at"] = r["finished_at"]
            entry["last_reason"] = r["reason"]
        return out

    # --- 스냅숏 조회 ---------------------------------------------------------------------------------

    def cluster_counts(self) -> dict:
        """적용된 적 있는 클러스터 → counts(ClusterSummary.counts). 한 번도 적용되지 않은 클러스터는 키가 없다(모름)."""
        out = {r["name"]: {"storage_classes": 0, "pvs": 0, "pvcs": 0, "pvcs_bound": 0, "namespaces": 0,
                           "bindings": 0, "recognized_bindings": 0, "users": 0}
               for r in self._db.query("SELECT name FROM k8s_clusters WHERE applied_run_seq IS NOT NULL")}

        def put(sql, mapping):
            for r in self._db.query(sql):
                if r["cluster_name"] in out:
                    for key, col in mapping.items():
                        out[r["cluster_name"]][key] = r[col] or 0
        put("SELECT cluster_name, COUNT(*) AS n FROM k8s_storage_classes GROUP BY cluster_name", {"storage_classes": "n"})
        put("SELECT cluster_name, COUNT(*) AS n FROM k8s_pvs GROUP BY cluster_name", {"pvs": "n"})
        put("""SELECT cluster_name, COUNT(*) AS n, SUM(CASE WHEN phase = 'Bound' THEN 1 ELSE 0 END) AS b
               FROM k8s_pvcs GROUP BY cluster_name""", {"pvcs": "n", "pvcs_bound": "b"})
        put("""SELECT cluster_name, COUNT(*) AS n, COUNT(dms_user) AS r, COUNT(DISTINCT dms_user) AS u
               FROM k8s_role_bindings GROUP BY cluster_name""",
            {"bindings": "n", "recognized_bindings": "r", "users": "u"})
        put("""SELECT cluster_name, COUNT(*) AS n FROM
                 (SELECT cluster_name, namespace FROM k8s_pvcs
                  UNION SELECT cluster_name, namespace FROM k8s_role_bindings) ns
               GROUP BY cluster_name""", {"namespaces": "n"})
        return out

    def totals(self) -> dict:
        def one(sql):
            row = self._db.query_one(sql)
            return (row or {}).get("n") or 0
        return {
            "clusters": one("SELECT COUNT(*) AS n FROM k8s_clusters"),
            "namespaces": one(f"""SELECT COUNT(*) AS n FROM
                                   (SELECT p.cluster_name, p.namespace FROM k8s_pvcs p {_clusters_join('p')}
                                    UNION SELECT b.cluster_name, b.namespace FROM k8s_role_bindings b
                                    {_clusters_join('b')}) ns"""),
            "pvcs": one(f"SELECT COUNT(*) AS n FROM k8s_pvcs p {_clusters_join('p')}"),
            "bindings": one(f"SELECT COUNT(*) AS n FROM k8s_role_bindings b {_clusters_join('b')}"),
            "users": one(f"SELECT COUNT(DISTINCT b.dms_user) AS n FROM k8s_role_bindings b {_clusters_join('b')}"),
        }

    def cluster_synced(self, name) -> bool:
        """한 번 이상 적용된 클러스터인가(제출의 k8s_mapping_unknown 판정 재료). 없는 클러스터도 False."""
        row = self.get_cluster(name)
        return row is not None and row.get("applied_run_seq") is not None

    # StorageClass 매핑 ----------------------------------------------------------------------------

    def sc_maps(self) -> dict:
        return {r["storage_class"]: r for r in self._db.query("SELECT * FROM k8s_storage_class_maps")}

    def get_sc_map(self, storage_class):
        return self._db.query_one("SELECT * FROM k8s_storage_class_maps WHERE storage_class = :s", {"s": storage_class})

    def maps_for_storage(self, storage_name) -> list:
        """그 스토리지를 가리키는 매핑(스토리지 화면의 「연결된 StorageClass」, kind 변경 거부 storage_has_sc_mappings 재료)."""
        return self._db.query("SELECT * FROM k8s_storage_class_maps WHERE storage_name = :n ORDER BY storage_class",
                              {"n": storage_name})

    def storage_class_known(self, storage_class) -> bool:
        """적용된 스냅숏(SC 목록 또는 PVC 의 storageClassName)에 보이는 이름인가 -- 또는 이미 매핑이 있나(해제용)."""
        params = {"s": storage_class}
        return bool(
            self._db.query_one(f"SELECT 1 AS x FROM k8s_storage_classes s {_clusters_join('s')} WHERE s.name = :s",
                               params)
            or self._db.query_one(f"SELECT 1 AS x FROM k8s_pvcs p {_clusters_join('p')} WHERE p.storage_class = :s",
                                  params)
            or self.get_sc_map(storage_class))

    def set_sc_map(self, storage_class, *, storage_name, path_template, actor):
        """매핑 저장(upsert). 대상 스토리지는 같은 트랜잭션에서 다시 본다 -- 없으면 storage_missing, PV 를 담는 종류(pv·both)가
        아니면 storage_not_pv_capable. 템플릿 형식 검증은 라우트(pv_paths.validate_template)."""
        now = utc_now_iso()
        with self._db.transaction():
            storage = self._db.query_one(f"SELECT * FROM storages WHERE storage_name = :n{self._lock}",
                                         {"n": storage_name})
            if storage is None:
                raise DomainValidationError("storage_missing", storage_name)
            if storage_kind(storage) not in PV_CAPABLE_KINDS:
                raise DomainValidationError("storage_not_pv_capable", storage_name)
            before = self.get_sc_map(storage_class)
            if before is None:
                self._db.execute(
                    """INSERT INTO k8s_storage_class_maps (storage_class, storage_name, path_template, updated_by,
                           updated_at) VALUES (:s, :n, :t, :by, :at)""",
                    {"s": storage_class, "n": storage_name, "t": path_template, "by": actor, "at": now})
            else:
                self._db.execute(
                    """UPDATE k8s_storage_class_maps SET storage_name = :n, path_template = :t, updated_by = :by,
                           updated_at = :at WHERE storage_class = :s""",
                    {"s": storage_class, "n": storage_name, "t": path_template, "by": actor, "at": now})
            after = self.get_sc_map(storage_class)
            self._audit("k8s_storage_class_map", "set", storage_class, before, after, actor)
        return after

    def delete_sc_map(self, storage_class, *, actor):
        with self._db.transaction():
            before = self.get_sc_map(storage_class)
            if before is None:
                return None
            self._db.execute("DELETE FROM k8s_storage_class_maps WHERE storage_class = :s", {"s": storage_class})
            self._audit("k8s_storage_class_map", "remove", storage_class, before, None, actor)
        return before

    def storage_class_rows(self) -> list:
        """(cluster, SC 이름)마다 SC 객체(없으면 None -- SC 목록 403 이거나 PVC 만 그 이름을 쓴다)와 PVC 수."""
        rows = {}
        for r in self._db.query(f"SELECT s.* FROM k8s_storage_classes s {_clusters_join('s')}"):
            rows[(r["cluster_name"], r["name"])] = {"cluster": r["cluster_name"], "name": r["name"],
                                                    "provisioner": r["provisioner"],
                                                    "parameters": load_json(r["parameters"]) or {},
                                                    "reclaim_policy": r["reclaim_policy"],
                                                    "volume_binding_mode": r["volume_binding_mode"], "pvc_count": 0}
        for r in self._db.query(
                f"""SELECT p.cluster_name, p.storage_class, COUNT(*) AS n FROM k8s_pvcs p {_clusters_join('p')}
                    WHERE p.storage_class IS NOT NULL GROUP BY p.cluster_name, p.storage_class"""):
            key = (r["cluster_name"], r["storage_class"])
            entry = rows.setdefault(key, {"cluster": key[0], "name": key[1], "provisioner": None, "parameters": {},
                                          "reclaim_policy": None, "volume_binding_mode": None, "pvc_count": 0})
            entry["pvc_count"] = r["n"]
        return [rows[k] for k in sorted(rows)]

    def sample_volume(self, cluster, storage_class):
        """그 (클러스터, SC)의 Bound PVC 하나(이름순 첫째)의 해석 결과 -- SC 화면의 「예시 경로」. 없으면 None."""
        row = self._db.query_one(
            """SELECT * FROM k8s_pvcs WHERE cluster_name = :c AND storage_class = :s AND phase = 'Bound'
               ORDER BY namespace, name LIMIT 1""", {"c": cluster, "s": storage_class})
        if row is None:
            return None
        return self.resolve_pvc(cluster, row["namespace"], row["name"])

    # 볼륨·권한 ---------------------------------------------------------------------------------

    def _storages(self) -> dict:
        return {r["storage_name"]: r for r in self._db.query("SELECT * FROM storages")}

    def resolve_pvc(self, cluster, namespace, pvc) -> "dict | None":
        """(cluster, namespace, pvc) → resolve_volume 결과. 적용된 스냅숏에 그 PVC 가 없으면 None(제출의 pv_not_found).
        클러스터가 한 번도 동기화되지 않았는지는 cluster_synced 로 먼저 가른다(k8s_mapping_unknown)."""
        row = self._db.query_one(
            f"""SELECT p.* FROM k8s_pvcs p {_clusters_join('p')}
                WHERE p.cluster_name = :c AND p.namespace = :ns AND p.name = :n""",
            {"c": cluster, "ns": namespace, "n": pvc})
        if row is None:
            return None
        pv = None
        if row.get("volume_name"):
            pv = self._db.query_one("SELECT * FROM k8s_pvs WHERE cluster_name = :c AND name = :v",
                                    {"c": cluster, "v": row["volume_name"]})
        sc = row.get("storage_class") or (pv or {}).get("storage_class")
        sc_map = self.get_sc_map(sc) if sc else None
        storage = None
        if sc_map is not None:
            storage = self._db.query_one("SELECT * FROM storages WHERE storage_name = :n",
                                         {"n": sc_map["storage_name"]})
        return resolve_volume(row, pv, sc_map, storage)

    def volumes(self, *, cluster=None, namespace=None, storage_class=None, q=None, dms_user=None,
                limit=None) -> "tuple[list, bool]":
        """PVC 목록(해석 포함) → (rows, truncated). dms_user 를 주면 그 사용자가 인식된 바인딩을 가진 namespace 만(권한 범위)
        이고 행마다 access(admin 이 edit 보다 우선)를 싣는다. q 는 공백으로 나눈 토큰마다 PVC 이름·namespace·볼륨·SC 부분 일치
        (토큰 사이 AND, 대소문자 무시)."""
        where, params = [], {}
        if cluster:
            where.append("p.cluster_name = :c")
            params["c"] = cluster
        if namespace:
            where.append("p.namespace = :ns")
            params["ns"] = namespace
        if storage_class:
            where.append("p.storage_class = :sc")
            params["sc"] = storage_class
        for i, token in enumerate(search_tokens(q)):
            key = f"q{i}"
            params[key] = contains_pattern(token)
            where.append("(" + " OR ".join(
                f"lower(COALESCE({col}, '')) LIKE lower(:{key}) ESCAPE '\\'"
                for col in ("p.name", "p.namespace", "p.volume_name", "p.storage_class")) + ")")
        if dms_user is not None:
            where.append("""EXISTS (SELECT 1 FROM k8s_role_bindings b WHERE b.cluster_name = p.cluster_name
                             AND b.namespace = p.namespace AND b.dms_user = :u)""")
            params["u"] = dms_user
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        lim = ""
        if limit is not None:
            lim = " LIMIT :lim"
            params["lim"] = int(limit) + 1
        rows = self._db.query(
            f"""SELECT p.*, v.name AS pv_name, v.volume_handle AS pv_volume_handle,
                       v.volume_attributes AS pv_volume_attributes, v.claim_namespace AS pv_claim_namespace,
                       v.claim_name AS pv_claim_name, v.storage_class AS pv_storage_class
                FROM k8s_pvcs p {_clusters_join('p')}
                LEFT JOIN k8s_pvs v ON v.cluster_name = p.cluster_name AND v.name = p.volume_name
                {clause}
                ORDER BY p.cluster_name, p.namespace, p.name{lim}""", params)
        truncated = limit is not None and len(rows) > int(limit)
        rows = rows[:int(limit)] if limit is not None else rows
        maps, storages = self.sc_maps(), self._storages()
        access = self.namespace_access(dms_user) if dms_user is not None else {}
        out = []
        for r in rows:
            pv = None
            if r.get("pv_name"):
                pv = {"name": r["pv_name"], "volume_handle": r["pv_volume_handle"],
                      "volume_attributes": r["pv_volume_attributes"], "claim_namespace": r["pv_claim_namespace"],
                      "claim_name": r["pv_claim_name"], "storage_class": r["pv_storage_class"]}
            sc = r.get("storage_class") or (pv or {}).get("storage_class")
            sc_map = maps.get(sc) if sc else None
            storage = storages.get(sc_map["storage_name"]) if sc_map else None
            item = resolve_volume(r, pv, sc_map, storage)
            # 권한 기준 사용자를 줬을 때만 의미가 있다(관리자 전체 목록은 null -- 「권한 없음」이 아니라 「묻지 않음」).
            item["access"] = access.get((r["cluster_name"], r["namespace"])) if dms_user is not None else None
            out.append(item)
        return out, truncated

    def namespace_access(self, dms_user) -> dict:
        """(cluster, namespace) → 이 사용자의 접근 수준("admin"|"edit") -- 인식된 바인딩만, admin 우선."""
        out = {}
        for r in self._db.query(
                f"""SELECT b.cluster_name, b.namespace, b.access FROM k8s_role_bindings b {_clusters_join('b')}
                    WHERE b.dms_user = :u""", {"u": dms_user}):
            key = (r["cluster_name"], r["namespace"])
            if out.get(key) != "admin":
                out[key] = r["access"]
        return out

    def access_for(self, dms_user, cluster, namespace) -> "str | None":
        """제출 판정용: 이 사용자가 그 namespace 의 PVC 에 권한이 있나 → "admin"|"edit"|None."""
        return self.namespace_access(dms_user).get((cluster, namespace)) if dms_user else None

    def users_by_namespace(self) -> dict:
        """(cluster, namespace) → 권한 사용자 목록(관리자 PV 탐색의 users)."""
        out: dict = {}
        for r in self._db.query(
                f"""SELECT DISTINCT b.cluster_name, b.namespace, b.dms_user FROM k8s_role_bindings b
                    {_clusters_join('b')} WHERE b.dms_user IS NOT NULL ORDER BY b.dms_user"""):
            out.setdefault((r["cluster_name"], r["namespace"]), []).append(r["dms_user"])
        return out

    def bindings(self, *, recognized: bool, q=None) -> list:
        """인식된(dms_user 있음) 또는 인식하지 않은 바인딩. q 는 토큰마다 dms_user·이름·namespace 부분 일치."""
        where = ["b.dms_user IS NOT NULL" if recognized else "b.dms_user IS NULL"]
        params = {}
        for i, token in enumerate(search_tokens(q)):
            key = f"q{i}"
            params[key] = contains_pattern(token)
            where.append("(" + " OR ".join(
                f"lower(COALESCE({col}, '')) LIKE lower(:{key}) ESCAPE '\\'"
                for col in ("b.dms_user", "b.name", "b.namespace")) + ")")
        rows = self._db.query(
            f"""SELECT b.* FROM k8s_role_bindings b {_clusters_join('b')} WHERE {' AND '.join(where)}
                ORDER BY b.dms_user, b.cluster_name, b.namespace, b.name""", params)
        for r in rows:
            r["subjects"] = load_json(r["subjects"]) or []
        return rows

    def pvc_counts_by_namespace(self) -> dict:
        return {(r["cluster_name"], r["namespace"]): r["n"] for r in self._db.query(
            f"""SELECT p.cluster_name, p.namespace, COUNT(*) AS n FROM k8s_pvcs p {_clusters_join('p')}
                GROUP BY p.cluster_name, p.namespace""")}
