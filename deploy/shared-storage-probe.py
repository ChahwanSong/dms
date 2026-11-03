#!/usr/bin/env python3
"""공용 스토리지 동작 프로브(2026-11-03 ③, 설계 v6 3-2 계약 G1~G12 · 8-1 「S7 세부」).

DMS 상태(<SHARED_DIR>/dms -- 아티팩트, 이후 PG·레지스트리·백업)는 사이트가 **모든 마스터·워커의 같은 호스트 경로**에 미리
마운트한 공용 스토리지(종류 무관) 위에 있다. DMS 는 그 스토리지를 마운트·프로비저닝하지 않고 FS 종류·마운트 옵션으로 판정하지도
않는다(FS 종류별 분기가 없다) -- 대신 이 스크립트가 **동작**으로 계약을 잰다. 설치기(묶음 ⑦ S7)가 부르고, 운영자가 따로 돌려도 된다.

  python3 deploy/shared-storage-probe.py --context <ctx> [--shared-dir <SHARED_DIR>] \\
      (--image <dms 이미지 ref> | --overlay <오버레이 디렉터리>) [--namespace dms] [--nodes a,b] \\
      [--g3-rounds 20] [--retry-seconds 60] [--start-timeout 180] [--json] [--dry-run]
  SHARED_DIR 는 --shared-dir > env SHARED_DIR > `--overlay` 디렉터리의 values.env(SHARED_DIR= 줄) 순서로 정한다.

어떻게: 노드마다 일회용 파드 하나(nodeName 고정, toleration Exists -- control-plane 포함, dms 이미지, root, cap 은 CHOWN·FOWNER·
DAC_READ_SEARCH 만)가 hostPath `<SHARED_DIR>/dms`(type **Directory** -- 없으면 파드가 뜨지 않는다 = 「마운트 없음」, 경로를 만드는
타입 금지)와 호스트 `/proc/1/mountinfo`(읽기 전용)를 붙이고 `sleep` 으로 기다린다. 이 스크립트가 `kubectl exec -i` 로 노드별
단계를 순서대로 지휘한다(도우미 코드는 stdin 으로 보낸다 -- argv 로 보내면 exec 요청 URL 이 ~19KB 라 apiserver 앞 L7 프록시가 414 로
거절할 수 있다). 쓰는 곳은 `<SHARED_DIR>/dms/.probe-<nonce>` 하나뿐이고 끝나면(실패·중단에도) 지우고 파드도 지운다. 파드는
activeDeadlineSeconds 로 스스로도 끝난다.

검사(설계 8-1 S7 ①~⑥):
  ① G1 같은 스토리지: 포함 마운트(호스트 mountinfo -- findmnt -T 처럼 SHARED_DIR 의 최장 접두, 그리고 파드가 본 <SHARED_DIR>/dms
     의 장치 번호(st_dev)와 mountinfo major:minor 대조 -- 심링크를 거친 SHARED_DIR 도 맞게 찾는다)가 '/' 면 「마운트 없음」 FAIL
     (SOURCE·FSTYPE·OPTIONS 는 보고만). 노드마다 프로브 디렉터리를 만들고(이미 있으면 그대로) nonce 파일을 쓴 뒤 모든 노드가 서로의
     파일을 읽는다. 서로 보이는 노드끼리 묶어 **다수 쪽**을 같은 스토리지로 보고 나머지를 「다른 노드 파일이 안 보임」 FAIL 로 낸다
     (첫 노드 하나만 다른 스토리지여도 그 노드가 FAIL 로 찍힌다; 다수를 가릴 수 없으면 모두 FAIL). `.dms-shared-id` 센티널이
     노드마다 다르거나 일부만 있거나 비었으면 FAIL, 전부 없어도 FAIL(「센티널 없음(S0 미실행)」 -- 파드가 떴다면 <SHARED_DIR>/dms
     는 있다; S0 = deploy/shared-dir-init.sh).
  ② G3 노드 간 가시성(**필수 아님 -- 실패는 WARN**, 설계 #47): B 가 없는 이름을 먼저 stat(부정 캐시) → A 가 그 이름을 rename 으로
     만들고 기존 파일을 rename 으로 교체 → B 가 1초 안에 새 이름(stat·open·readdir)과 교체된 내용을 봐야 한다. 기본 20회. 단계
     실행 오류도 「G3 확인 못 함」 WARN 이다(필수 아닌 검사가 설치를 「모름」으로 멈추지 않게).
  ③ G4 root 소유 보존: root 가 만든 파일이 0:0, root chown(26·65532)·chmod, 그리고 root 가 **남(26:26) 소유 0700 디렉터리**를 통과해
     안의 파일을 읽는다(같은 노드와 다른 노드 -- 묶음 ⑤ 의 pgdata 같은 모양. 소유는 0 으로 남지만 root 특권을 주지 않는 export 를
     잡는다; 파드에 DAC_READ_SEARCH 를 주는 이유 -- 노드 root(kubelet)처럼 커널 권한으로 통과하는지 본다).
  ④ G5 숫자 uid/gid: 같은 노드에서 chown 26·65532 뒤 같은 숫자로 보이는지(root 는 0:0 인데 chown 결과만 다르면 squash 가 아니라
     「ID 매핑」), A 가 26:26·65532:65532 로 chown 한 파일이 다른 노드에서도 같은 숫자.
  ⑤ fsync 왕복(G7 의 내구성 자체는 스토리지 담당 확인 -- N1): 1MiB 를 fsync 후 같은 노드·다른 노드가 같은 내용을 읽는다.
  ⑥ G9 POSIX: 두 노드가 같은 이름을 동시에 O_EXCL → 이름마다 정확히 하나만 성공(시작 시각은 A 파드의 시계로 정한다 -- 설치
     호스트 시계와 노드 시계가 어긋나도 경합이 사라지거나 exec 가 시간 초과하지 않게; 대기는 15초로 자른다), nlink 1→2(하드링크),
     심링크 O_NOFOLLOW → ELOOP, rename 원자 교체(같은 노드의 읽기가 중간 상태·ENOENT 를 못 봄), 디렉터리 fsync.
교차 노드 원칙(② 제외): 확인하는 노드는 생성 전에 그 이름을 조회하지 않고, stat 대신 open+fstat 으로 읽고, 못 보면
--retry-seconds(기본 60 = NFS 기본 acdirmax)까지 재시도한 뒤 판정한다 -- G3 지연을 G1·G5 실패로 오판하지 않게.

판정·종료 코드(설계: 모름 ≠ 실패): 0 PASS · 3 WARN 만(설치 계속) · 1 FAIL 하나라도 · 4 모름(파드 미기동·이미지 pull 실패·exec
실패 -- FAIL 은 없음) · 2 사용법·환경 오류(클러스터를 건드리기 전). 마지막 줄은 `RESULT: <PASS|WARN|FAIL|UNKNOWN>`(--json 이면
stdout 은 JSON 하나, 사람용 줄은 stderr). --dry-run 은 계획과 파드 매니페스트만 출력한다(파드를 만들지 않는다 -- --nodes 가
없으면 노드 목록만 읽는다). SIGTERM·Ctrl-C 는 진행 중 kubectl 을 끊고 프로브 디렉터리·파드를 지운 뒤 모름(4)으로 끝난다(두 번째
신호는 무시 -- 정리를 끊지 않게). 정리에 실패하면 지울 경로·명령을 stderr 와 결과(INFO)에 남긴다.

kubectl 은 모든 호출에 --context 를 붙인다(필수 -- 두 테스트베드가 같은 네임스페이스라 current-context 에 기대지 않는다,
CLAUDE.md). 설치 호스트의 python3(RHEL 9 = 3.9)로 돌고 표준 라이브러리만 쓴다. 파드 안 도우미(HELPER)는 이미지의 python3 이다.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time

# 파드 안 경로(호스트 경로와 다르다 -- 도우미는 이 두 곳만 본다).
POD_DMS = "/probe/dms"
POD_MOUNTINFO = "/probe/host-mountinfo"
LABEL = "app.kubernetes.io/name=dms-shared-probe"
NONCE_LABEL = "dms.io/probe-nonce"
# 설계 v6 3-2 SHARED_DIR 형식(render.sh 와 같은 규칙).
_SHARED_DIR_CHARS = re.compile(r"^/[A-Za-z0-9._/-]+$")
_NONCE_RE = re.compile(r"^[0-9a-f]{16}$")
EXIT = {"PASS": 0, "FAIL": 1, "WARN": 3, "UNKNOWN": 4}
_RANK = {"INFO": 0, "PASS": 1, "WARN": 2, "UNKNOWN": 3, "FAIL": 4}
EXCL_NAMES = 200            # ⑥ O_EXCL 경합에 쓰는 이름 수(두 노드가 같은 순서로 시도)
EXCL_LEAD = 3.0             # ⑥ A 파드 시계 기준 시작까지 여유(두 exec 가 다 도착할 시간)
EXCL_MAX_WAIT = 15.0        # ⑥ 노드 시계가 어긋나도 이보다 오래 자지 않는다(exec 시간 초과·정리 경합 방지)
FSYNC_BYTES = 1 << 20       # ⑤ fsync 왕복 크기
ATOMIC_SECONDS = 2.0        # ⑥ rename 원자 교체 관찰 시간
G3_WINDOW = 1.0             # ② 「1초 안에」
PG_UID = 26                 # ③ 남 소유 0700 디렉터리(묶음 ⑤ pgdata 의 소유자 uid/gid)

G3_IMPACT = ("영향: 멀티 노드 rank 가 다른 노드가 막 만든 파일을 못 봐 ENOENT 로 실패할 수 있고, api·controller 가 잡 산출물을 "
             "늦게 볼 수 있다(설치는 계속)")
NFS_HINTS = {
    "squash": "NFS 면 export 에 no_root_squash",
    "idmap": "NFS 면 sec=sys 와 NFSv4 idmap 도메인 일치(숫자 uid/gid 를 그대로)",
    "g3": "NFS 면 lookupcache=positive(또는 none)로 다시 마운트하고 nocto 를 빼라(remount 로는 안 바뀐다 -- 설계 3-2 G2 보충)",
    "visible": "NFS 면 모든 노드가 같은 서버·export 를 같은 경로에 마운트했는지(fstab)",
}


def shared_dir_problem(value) -> "str | None":
    """SHARED_DIR 형식 위반 사유(없으면 None). 절대 경로, '/' 아님, 정규화(// · /./ · /../ · 끝 / 없음), 문자 ^/[A-Za-z0-9._/-]+$."""
    if not isinstance(value, str) or not value:
        return "비어 있음"
    if not value.startswith("/"):
        return "절대 경로가 아님"
    if not _SHARED_DIR_CHARS.fullmatch(value):
        return "허용 밖 문자(영문·숫자·. _ / - 만)"
    if value.endswith("/"):
        return "'/' 자체이거나 끝에 / 가 있음"
    if "//" in value or "/./" in value or "/../" in value or value.endswith("/.") or value.endswith("/.."):
        return "정규화되지 않은 경로(// · /./ · /../)"
    return None


# ---------------------------------------------------------------------------------------------------------------------
# 파드 안 도우미. 표준 라이브러리만, python 3.9+. 한 번 실행에 한 동작을 하고 JSON 한 줄을 출력한다(판정은 바깥이 한다).
# 인자로 받는 base 는 늘 POD_DMS(= 호스트 <SHARED_DIR>/dms), 쓰는 곳은 그 아래 .probe-<nonce> 뿐이다.
# ---------------------------------------------------------------------------------------------------------------------
HELPER = r'''
import errno, hashlib, json, os, re, shutil, stat, sys, time

PG_UID = 26   # ③ 남 소유 0700 디렉터리의 uid/gid(바깥 PG_UID 와 같은 값)

def out(**kw):
    sys.stdout.write(json.dumps(kw) + "\n")

def ename(exc):
    code = getattr(exc, "errno", None)
    return errno.errorcode.get(code, type(exc).__name__) if code else type(exc).__name__

def pdir(base, probe):
    if not re.fullmatch(r"\.probe-[0-9a-f]{16}", probe):
        raise SystemExit("bad probe name")
    return os.path.join(base, probe)

def read_fd(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        chunks = []
        while True:
            b = os.read(fd, 1 << 20)
            if not b:
                break
            chunks.append(b)
        return st, b"".join(chunks)
    finally:
        os.close(fd)

def write_file(path, data, sync=True):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    try:
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            view = view[n:]
        if sync:
            os.fsync(fd)
    finally:
        os.close(fd)

def replace_file(d, name, data, sync=True):
    tmp = os.path.join(d, ".tmp-%s-%d" % (name, os.getpid()))
    write_file(tmp, data, sync)
    os.rename(tmp, os.path.join(d, name))

def unescape(s):
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), s)

def wait_until(deadline, fn):
    last = None
    while True:
        try:
            if fn():
                return True, None
        except OSError as exc:
            last = ename(exc)
        if time.monotonic() >= deadline:
            return False, last
        time.sleep(0.5)

def op_mount(mountinfo, shared, probe_path):
    # 포함 마운트 = 경로 문자열의 최장 접두(findmnt -T 와 같다). 그런데 SHARED_DIR 가 심링크를 거치면(/shared -> /gpfs/fs1/x)
    # 문자열로는 '/' 가 잡혀 거짓 「마운트 없음」이 된다 -- 파드가 실제로 본 <SHARED_DIR>/dms 의 장치 번호를 mountinfo 의
    # major:minor(3번째 필드)와 대조해 문자열 판정과 다르면 장치 쪽을 믿는다. 장치가 아무 줄과도 안 맞으면(btrfs 하위 볼륨 등)
    # 문자열 판정 그대로다.
    entries = []
    with open(mountinfo) as f:
        for line in f:
            left, _, right = line.partition(" - ")
            parts, rp = left.split(), right.split()
            if len(parts) < 6:
                continue
            entries.append({"target": unescape(parts[4]), "dev": parts[2],
                            "source": unescape(rp[1]) if len(rp) > 1 else "", "fstype": rp[0] if rp else "",
                            "options": parts[5] + ("," + rp[2] if len(rp) > 2 else "")})
    def covers(t):
        return t == "/" or shared == t or shared.startswith(t.rstrip("/") + "/")
    by_path = None
    for e in entries:
        if covers(e["target"]) and (by_path is None or len(e["target"]) >= len(by_path["target"])):
            by_path = e
    try:
        st = os.stat(probe_path)
        dev = "%d:%d" % (os.major(st.st_dev), os.minor(st.st_dev))
    except OSError:
        dev = None
    same = [e for e in entries if dev is not None and e["dev"] == dev]
    best, how = by_path, "path"
    if same and (by_path is None or by_path["dev"] != dev):
        how = "device"
        covering = [e for e in same if covers(e["target"])]
        roots = [e for e in same if e["target"] == "/"]
        if covering:
            best = max(covering, key=lambda e: len(e["target"]))
        elif roots:
            best = roots[0]          # 문자열은 다른 마운트를 가리키지만 실제로는 루트 파일시스템에 있다 = 마운트 없음
        else:
            best = max(same, key=lambda e: len(e["target"]))
    if best is None:
        return out(ok=False, error="no mount covers path")
    out(ok=True, target=best["target"], source=best["source"], fstype=best["fstype"], options=best["options"],
        matched_by=how, path_target=by_path["target"] if by_path else None, dev=dev)

def op_prepare(base, probe):
    # 노드마다 만든다(이미 있으면 그대로) -- 첫 노드 하나가 만들면 그 노드만 다른 스토리지일 때 나머지가 전부 「안 보임」으로
    # 찍힌다. 같은 스토리지면 하나가 만들고 나머지는 EEXIST(양성 조회라 부정 캐시를 만들지 않는다).
    d = pdir(base, probe)
    try:
        os.mkdir(d, 0o700)
        created = True
    except FileExistsError:
        created = False
    if not stat.S_ISDIR(os.lstat(d).st_mode):
        return out(ok=False, error="not_a_directory")
    out(ok=True, created=created)

def op_now():
    out(now=time.time())

def op_write_g1(base, probe, name, content, retry):
    d = pdir(base, probe)
    t0 = time.monotonic()
    ok, err = wait_until(t0 + float(retry), lambda: os.close(os.open(d, os.O_RDONLY | os.O_DIRECTORY)) is None)
    if not ok:
        return out(ok=False, error=err or "ENOENT", stage="probe_dir")
    write_file(os.path.join(d, name), content.encode())
    out(ok=True, waited=round(time.monotonic() - t0, 2))

def op_read_many(base, probe, retry, *pairs):
    d = pdir(base, probe)
    want = dict(p.split("=", 1) for p in pairs)
    seen, errors, t0 = {}, {}, time.monotonic()
    deadline = t0 + float(retry)
    for name, content in want.items():
        ok, err = wait_until(deadline, lambda: read_fd(os.path.join(d, name))[1] == content.encode())
        seen[name] = ok
        if not ok:
            errors[name] = err or "content_mismatch"
    out(ok=all(seen.values()), seen=seen, errors=errors, waited=round(time.monotonic() - t0, 2))

def op_sentinel(base):
    try:
        _st, data = read_fd(os.path.join(base, ".dms-shared-id"))
    except OSError as exc:
        return out(present=False, error=ename(exc))
    out(present=True, value=data.decode("utf-8", "replace").strip()[:200])

def op_put(base, probe, name, content):
    replace_file(pdir(base, probe), name, content.encode())
    out(ok=True)

def op_g3_prime(base, probe, new, old, old_content, retry):
    d = pdir(base, probe)
    negative = False
    try:
        os.stat(os.path.join(d, new))
    except OSError as exc:
        negative = exc.errno == errno.ENOENT
    os.listdir(d)
    ok, err = wait_until(time.monotonic() + float(retry),
                         lambda: read_fd(os.path.join(d, old))[1] == old_content.encode())
    out(negative=negative, old_ok=ok, error=err)

def op_g3_write(base, probe, new, new_content, old, replacement):
    d = pdir(base, probe)
    replace_file(d, new, new_content.encode())
    replace_file(d, old, replacement.encode())
    out(ok=True)

def op_g3_check(base, probe, new, new_content, old, replacement, window):
    d = pdir(base, probe)
    t0 = time.monotonic()
    deadline = t0 + float(window)
    res = {"new": None, "repl": None}
    def new_ok():
        os.stat(os.path.join(d, new))
        return read_fd(os.path.join(d, new))[1] == new_content.encode() and new in os.listdir(d)
    def repl_ok():
        return read_fd(os.path.join(d, old))[1] == replacement.encode()
    while True:
        for key, fn in (("new", new_ok), ("repl", repl_ok)):
            if res[key] is None:
                try:
                    if fn():
                        res[key] = round(time.monotonic() - t0, 3)
                except OSError:
                    pass
        if all(v is not None for v in res.values()) or time.monotonic() >= deadline:
            break
        time.sleep(0.05)
    out(new_ok=res["new"] is not None, repl_ok=res["repl"] is not None, new_at=res["new"], repl_at=res["repl"])

def op_owner(base, probe, tag):
    d = pdir(base, probe)
    res, errors = {}, {}
    def step(key, fn):
        try:
            res[key] = fn()
        except OSError as exc:
            errors[key] = ename(exc)
    f = os.path.join(d, "own-" + tag)
    def root_file():
        write_file(f, b"x")
        st = read_fd(f)[0]
        return [st.st_uid, st.st_gid]
    step("root", root_file)
    def chmod():
        os.chmod(f, 0o600)
        return oct(read_fd(f)[0].st_mode & 0o7777)
    step("mode", chmod)
    def traverse():
        # 남(26:26) 소유 0700 디렉터리 안의 파일을 root 가 읽는다 -- root 가 만든 0700 은 소유자 비트로 늘 통과해 아무것도 재지
        # 못한다. 파드의 DAC_READ_SEARCH 는 노드 root(kubelet·S10 경로 대조)의 커널 권한을 흉내 낸다; 소유는 0 으로 남지만 root
        # 특권을 주지 않는 export(-superuser none 등)는 여기서 EACCES 다.
        dd = os.path.join(d, "dir-" + tag)
        os.mkdir(dd, 0o700)
        write_file(os.path.join(dd, "inner"), b"y")
        os.chown(dd, PG_UID, PG_UID)
        st = os.lstat(dd)
        if (st.st_uid, st.st_gid, st.st_mode & 0o777) != (PG_UID, PG_UID, 0o700):
            return "dir %d:%d %o" % (st.st_uid, st.st_gid, st.st_mode & 0o777)
        return read_fd(os.path.join(dd, "inner"))[1] == b"y"
    step("traverse", traverse)
    for ident in (26, 65532):
        def chown(ident=ident):
            p = os.path.join(d, "id%d-%s" % (ident, tag))
            write_file(p, b"z")
            os.chown(p, ident, ident)
            st = read_fd(p)[0]
            return [st.st_uid, st.st_gid]
        step("id%d" % ident, chown)
    out(errors=errors, **res)

def op_owner_check(base, probe, tag, retry):
    d = pdir(base, probe)
    want = {"own-" + tag: [0, 0], "id26-" + tag: [26, 26], "id65532-" + tag: [65532, 65532]}
    seen, t0 = {}, time.monotonic()
    deadline = t0 + float(retry)
    for name, ids in want.items():
        def check(name=name, ids=ids):
            st = read_fd(os.path.join(d, name))[0]
            seen[name] = [st.st_uid, st.st_gid]
            return seen[name] == ids
        ok, err = wait_until(deadline, check)
        if name not in seen:
            seen[name] = err
    # 다른 노드가 만든 26:26 0700 디렉터리를 이 노드의 root 가 통과(G4 -- 묶음 ⑤ S10 경로 대조가 다른 노드에서 pgdata 를 읽는다).
    trav, trav_err = wait_until(deadline, lambda: read_fd(os.path.join(d, "dir-" + tag, "inner"))[1] == b"y")
    out(ok=all(seen[n] == ids for n, ids in want.items()), seen=seen, traverse=trav, traverse_error=trav_err,
        waited=round(time.monotonic() - t0, 2))

def op_fsync_write(base, probe, name, size):
    d = pdir(base, probe)
    data = os.urandom(int(size))
    write_file(os.path.join(d, name), data)
    back = read_fd(os.path.join(d, name))[1]
    out(ok=back == data, sha=hashlib.sha256(data).hexdigest())

def op_fsync_read(base, probe, name, sha, retry):
    d = pdir(base, probe)
    ok, err = wait_until(time.monotonic() + float(retry),
                         lambda: hashlib.sha256(read_fd(os.path.join(d, name))[1]).hexdigest() == sha)
    out(ok=ok, error=err)

def op_excl(base, probe, prefix, count, start_at, max_wait):
    # start_at 은 A 파드의 벽시계 기준 -- 이 노드 시계로 그때까지 자되 max_wait 초를 넘기지 않는다(시계가 크게 어긋나도
    # exec 시간 초과로 「모름」이 되거나, 끊긴 exec 가 파드 안에서 계속 자다 정리 뒤에 파일을 만들지 않게). lead 는 바깥이
    # 두 노드의 시계 차를 가늠하는 데 쓴다.
    d = pdir(base, probe)
    bits, error = [], None
    lead = float(start_at) - time.time()
    end = time.monotonic() + max(0.0, min(lead, float(max_wait)))
    while time.monotonic() < end:
        time.sleep(0.005)
    for k in range(int(count)):
        try:
            os.close(os.open(os.path.join(d, "%s-%d" % (prefix, k)), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
            bits.append("1")
        except FileExistsError:
            bits.append("0")
        except OSError as exc:
            bits.append("E")
            error = error or ename(exc)
    out(bits="".join(bits), error=error, lead=round(lead, 3))

def op_posix(base, probe, tag):
    d = pdir(base, probe)
    res = {}
    try:
        f = os.path.join(d, "nl-" + tag)
        write_file(f, b"n")
        res["nlink1"] = read_fd(f)[0].st_nlink
        os.link(f, f + ".2")
        res["nlink2"] = read_fd(f)[0].st_nlink
    except OSError as exc:
        res["nlink_error"] = ename(exc)
    try:
        ln = os.path.join(d, "ln-" + tag)
        os.symlink("nl-" + tag, ln)
        try:
            os.close(os.open(ln, os.O_RDONLY | os.O_NOFOLLOW))
            res["nofollow"] = "opened"
        except OSError as exc:
            res["nofollow"] = ename(exc)
    except OSError as exc:
        res["nofollow"] = "symlink:" + ename(exc)
    try:
        fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
            res["dirfsync"] = "ok"
        finally:
            os.close(fd)
    except OSError as exc:
        res["dirfsync"] = ename(exc)
    out(**res)

def op_atomic_writer(base, probe, name, seconds):
    d = pdir(base, probe)
    deadline, i = time.monotonic() + float(seconds), 0
    while time.monotonic() < deadline:
        i += 1
        replace_file(d, name, b"v%08d" % i, sync=False)
    out(ok=True, writes=i)

def op_atomic_reader(base, probe, name, seconds):
    d = pdir(base, probe)
    deadline = time.monotonic() + float(seconds)
    reads = missing = torn = 0
    other = None
    while time.monotonic() < deadline:
        try:
            data = read_fd(os.path.join(d, name))[1]
            reads += 1
            if not re.fullmatch(rb"v[0-9]{8}", data):
                torn += 1
        except FileNotFoundError:
            missing += 1
        except OSError as exc:
            other = other or ename(exc)
    out(reads=reads, missing=missing, torn=torn, error=other)

def _reclaim(dfd):
    # 2026-11-03 리뷰: ③ 이 남긴 dir-<tag>(26:26 0700)는 이 파드(cap CHOWN·FOWNER·DAC_READ_SEARCH, DAC_OVERRIDE 없음)가 안의
    # 파일을 지울 수 없다 -- 디렉터리 쓰기는 DAC_READ_SEARCH 로 우회되지 않아, 클라이언트가 권한을 로컬에서 판정하는 FS(CephFS
    # 커널·GPFS·Lustre·로컬)에서 rmtree 가 EACCES 로 매번 .probe-<nonce> 를 남겼다(NFS no_root_squash 는 서버가 판정해 드러나지
    # 않았다). 지우기 전에 프로브 디렉터리 아래 디렉터리를 euid 소유·u+rwx 로 되돌린다(CHOWN cap 으로 가져오고 소유자 비트로
    # 지운다). op_owner 에서 바로 되돌리지 않는 이유: 다른 노드의 owner_check 가 그 26:26 0700 을 통과해야 한다.
    # 링크를 따라가지 않는다(lstat·lchown·O_NOFOLLOW 로 연 fd 에 fchmod) -- 프로브 디렉터리 밖은 건드리지 않는다. 실패는 무시하고
    # rmtree 가 판정한다(root_squash 처럼 chown 이 EPERM 이어도 서버 기준으로는 이미 지울 수 있다).
    euid, egid = os.geteuid(), os.getegid()
    for name in os.listdir(dfd):
        try:
            st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
            if not stat.S_ISDIR(st.st_mode):
                continue
            if st.st_uid != euid:
                try:
                    os.chown(name, euid, egid, dir_fd=dfd, follow_symlinks=False)
                except OSError:
                    pass
            cfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dfd)
        except OSError:
            continue
        try:
            try:
                if os.fstat(cfd).st_mode & 0o700 != 0o700:
                    os.fchmod(cfd, 0o700)
            except OSError:
                pass
            _reclaim(cfd)
        finally:
            os.close(cfd)

def op_cleanup(base, probe):
    # 이미 없으면(같은 스토리지의 다른 노드가 지웠다) 성공이다. 중단 직후에는 끊긴 exec 가 아직 파일을 만들 수 있어
    # ENOTEMPTY·ENOENT 경합이 나므로 몇 번 다시 한다.
    d = pdir(base, probe)
    if not shutil.rmtree.avoids_symlink_attacks:
        return out(ok=False, error="rmtree_not_symlink_safe")
    last = None
    for _ in range(5):
        try:
            try:
                dfd = os.open(d, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            except OSError:
                dfd = None          # 없거나 못 열면 rmtree 가 판정한다
            if dfd is not None:
                try:
                    _reclaim(dfd)
                finally:
                    os.close(dfd)
            shutil.rmtree(d)
            return out(ok=True)
        except FileNotFoundError:
            if not os.path.lexists(d):
                return out(ok=True, gone=True)
            last = "ENOENT"
        except OSError as exc:
            last = ename(exc)
        time.sleep(1)
    out(ok=False, error=last)

OPS = {k[3:]: v for k, v in dict(globals()).items() if k.startswith("op_")}
try:
    OPS[sys.argv[1]](*sys.argv[2:])
except OSError as exc:
    out(ok=False, error=ename(exc))
'''


# ---------------------------------------------------------------------------------------------------------------------
# kubectl
# ---------------------------------------------------------------------------------------------------------------------
class Kube:
    """모든 클러스터 호출에 --context 를 붙인다. config get-contexts(존재 확인)만 로컬 호출이다."""

    def __init__(self, context: str, namespace: str, kubectl: str = "kubectl"):
        self.context, self.namespace, self.kubectl = context, namespace, kubectl
        self._procs, self._lock = set(), threading.Lock()

    def _run(self, args, *, input_text=None, timeout=60, cluster=True):
        # subprocess.run 대신 직접 띄워 진행 중인 kubectl 을 기록한다 -- 중단(SIGTERM) 때 interrupt() 가 끊어야 작업 스레드가
        # exec 시간 초과(최대 수 분)를 기다리지 않고 돌아오고, 그래야 정리가 곧바로 돈다.
        # 자식은 자기 프로세스 그룹으로 띄운다 -- 끊을 때 그 그룹째 죽여 손자(래퍼 셸 아래 kubectl 등)가 파이프를 붙잡고
        # communicate 를 붙들지 않게.
        cmd = [self.kubectl] + (["--context", self.context] if cluster else []) + list(args)
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        with self._lock:
            self._procs.add(proc)
        try:
            out, err = proc.communicate(input_text, timeout=timeout)
        except BaseException:
            self._kill(proc)
            proc.communicate()
            raise
        finally:
            with self._lock:
                self._procs.discard(proc)
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)

    @staticmethod
    def _kill(proc) -> None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            try:
                proc.kill()
            except OSError:
                pass

    def interrupt(self) -> None:
        """진행 중인 kubectl 을 모두 끊는다(중단 처리 -- Probe.parallel·main)."""
        with self._lock:
            procs = list(self._procs)
        for proc in procs:
            if proc.returncode is None:      # 이미 거둔 자식의 pid(재사용될 수 있다)는 건드리지 않는다
                self._kill(proc)

    def context_exists(self) -> bool:
        try:
            return self._run(["config", "get-contexts", self.context], cluster=False, timeout=30).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def nodes(self) -> list:
        r = self._run(["get", "nodes", "-o", "json"])
        if r.returncode != 0:
            raise RuntimeError(f"kubectl get nodes 실패: {r.stderr.strip()[:300]}")
        out = []
        for item in json.loads(r.stdout).get("items", []):
            conds = {c.get("type"): c.get("status") for c in item.get("status", {}).get("conditions", [])}
            labels = item.get("metadata", {}).get("labels", {})
            out.append({"name": item["metadata"]["name"], "ready": conds.get("Ready") == "True",
                        "control_plane": "node-role.kubernetes.io/control-plane" in labels})
        return sorted(out, key=lambda n: n["name"])

    def create(self, manifest: dict) -> None:
        r = self._run(["-n", self.namespace, "create", "-f", "-"], input_text=json.dumps(manifest), timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"프로브 파드 생성 실패: {r.stderr.strip()[:500]}")

    def pods(self, nonce: str) -> list:
        r = self._run(["-n", self.namespace, "get", "pods", "-l", f"{NONCE_LABEL}={nonce}", "-o", "json"])
        return json.loads(r.stdout).get("items", []) if r.returncode == 0 else []

    def events(self) -> list:
        # FailedMount 만 -- 잡이 많은 네임스페이스에서 2초마다 이벤트 전체를 받지 않게.
        r = self._run(["-n", self.namespace, "get", "events", "--field-selector",
                       "involvedObject.kind=Pod,reason=FailedMount", "-o", "json"])
        return json.loads(r.stdout).get("items", []) if r.returncode == 0 else []

    def exec(self, pod: str, argv: list, timeout: float, stdin: "str | None" = None):
        # stdin 이 있으면 -i 로 보낸다(도우미 코드 -- argv 에 실으면 exec 요청 URL 이 길어 프록시가 414 로 거절할 수 있다).
        flags = ["-i"] if stdin is not None else []
        try:
            r = self._run(["-n", self.namespace, "exec"] + flags + [pod, "-c", "probe", "--"] + list(argv),
                          input_text=stdin, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, "", "timeout"
        return r.returncode, r.stdout, r.stderr

    def delete(self, nonce: str) -> bool:
        try:
            r = self._run(["-n", self.namespace, "delete", "pod", "-l", f"{NONCE_LABEL}={nonce}", "--wait=false",
                           "--ignore-not-found"], timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return r.returncode == 0


def pod_manifest(*, nonce: str, index: int, node: str, image: str, shared_dir: str, namespace: str,
                 deadline: int) -> dict:
    return {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {"name": f"dms-shared-probe-{nonce[:8]}-{index}", "namespace": namespace,
                     "labels": {"app.kubernetes.io/name": "dms-shared-probe", "app.kubernetes.io/part-of": "dms",
                                NONCE_LABEL: nonce}},
        "spec": {
            "nodeName": node, "restartPolicy": "Never", "automountServiceAccountToken": False,
            "terminationGracePeriodSeconds": 1, "activeDeadlineSeconds": deadline,
            "tolerations": [{"operator": "Exists"}],
            "containers": [{
                "name": "probe", "image": image, "imagePullPolicy": "IfNotPresent",
                "command": ["sleep", str(deadline)],
                "env": [{"name": "PYTHONDONTWRITEBYTECODE", "value": "1"}],
                # DAC_READ_SEARCH: ③ 의 「root 가 남(26:26) 소유 0700 디렉터리 통과」를 노드 root 처럼 커널 권한으로 잰다(이게
                # 없으면 로컬 의미의 FS(CephFS 커널 클라이언트 등)에서 거짓 FAIL, 있으면 서버가 root 특권을 주는지가 남는다).
                "securityContext": {"runAsUser": 0, "runAsGroup": 0, "allowPrivilegeEscalation": False,
                                    "readOnlyRootFilesystem": True,
                                    "capabilities": {"drop": ["ALL"], "add": ["CHOWN", "FOWNER", "DAC_READ_SEARCH"]}},
                "resources": {"requests": {"cpu": "10m", "memory": "32Mi"},
                              "limits": {"cpu": "500m", "memory": "256Mi"}},
                "volumeMounts": [{"name": "shared-dms", "mountPath": POD_DMS},
                                 {"name": "host-mountinfo", "mountPath": POD_MOUNTINFO, "readOnly": True}],
            }],
            "volumes": [{"name": "shared-dms", "hostPath": {"path": f"{shared_dir}/dms", "type": "Directory"}},
                        {"name": "host-mountinfo", "hostPath": {"path": "/proc/1/mountinfo", "type": "File"}}],
        },
    }


# ---------------------------------------------------------------------------------------------------------------------
# 판정 기록
# ---------------------------------------------------------------------------------------------------------------------
class Report:
    def __init__(self, stream=sys.stdout):
        self.items, self.stream = [], stream

    def add(self, node: str, check: str, status: str, message: str) -> None:
        self.items.append({"node": node, "check": check, "status": status, "message": message})
        self.stream.write(f"  {status:<7} {node:<24} {check}: {message}\n")
        self.stream.flush()

    def result(self) -> str:
        worst = max((_RANK[i["status"]] for i in self.items), default=_RANK["UNKNOWN"])
        if worst <= _RANK["PASS"]:
            return "PASS" if self.items else "UNKNOWN"
        return {v: k for k, v in _RANK.items()}[worst]

    def statuses(self, node: str, check: str) -> list:
        return [i["status"] for i in self.items if i["node"] == node and i["check"] == check]


class Probe:
    """노드별 단계 지휘. kube 는 exec(pod, argv, timeout, stdin=) 만 쓰면 된다(테스트가 로컬 실행으로 바꾼다)."""

    def __init__(self, kube, report: Report, *, shared_dir: str, nonce: str, retry_seconds: float, g3_rounds: int):
        self.kube, self.report, self.shared_dir = kube, report, shared_dir
        self.nonce, self.probe = nonce, f".probe-{nonce}"
        self.retry, self.g3_rounds = float(retry_seconds), int(g3_rounds)
        self.fstype = {}
        # 프로브 디렉터리를 지울 노드 묶음 -- 묶음마다(= 같은 스토리지마다) 한 노드에서 성공하면 된다. 다른 스토리지가 섞이면
        # 묶음이 여럿이다(노드마다 자기 스토리지에 디렉터리를 만들었다).
        self.cleanup_groups = []

    # --- exec 도우미 ----------------------------------------------------------------------------------------------
    def call(self, pod: str, op: str, *args, timeout: "float | None" = None):
        # 도우미 코드는 stdin 으로(`python3 -`) -- argv 에 실으면 exec 요청 URL 이 ~19KB 라 apiserver 앞 L7 프록시가 414 로 끊을 수 있다.
        argv = ["python3", "-", op] + [str(a) for a in args]
        rc, out, err = self.kube.exec(pod, argv, timeout or (self.retry * 3 + 60), stdin=HELPER)
        lines = [ln for ln in (out or "").splitlines() if ln.strip()]
        if rc != 0 or not lines:
            return None, f"exec 실패(rc={rc}): {(err or out or '').strip()[-300:]}"
        try:
            return json.loads(lines[-1]), None
        except ValueError:
            return None, f"도우미 출력 해석 실패: {lines[-1][:200]}"

    def parallel(self, jobs: dict):
        """{key: (pod, op, args...)} 를 동시에 돌려 {key: (result, error)}. 중단(KeyboardInterrupt -- SIGTERM)이면 대기 중인 일을
        취소하고 진행 중인 kubectl 을 끊은 뒤 다시 던진다 -- 스레드 풀이 exec 시간 초과(최대 수 분)를 기다리지 않게."""
        if not jobs:
            return {}
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=min(16, len(jobs)))
        try:
            futs = {k: pool.submit(self.call, v[0], v[1], *v[2:]) for k, v in jobs.items()}
            return {k: f.result() for k, f in futs.items()}
        except BaseException:
            pool.shutdown(wait=False, cancel_futures=True)
            interrupt = getattr(self.kube, "interrupt", None)
            if interrupt is not None:
                interrupt()
            raise
        finally:
            pool.shutdown(wait=True)

    def hint(self, node: str, key: str) -> str:
        return f" -- {NFS_HINTS[key]}" if self.fstype.get(node, "").startswith("nfs") else ""

    # --- 전체 순서 -------------------------------------------------------------------------------------------------
    def run(self, pods: dict) -> None:
        """pods = {node: pod 이름}(Running 인 것만). 이후 단계의 A = ① 의 다수 묶음에서 이름순 첫 노드."""
        nodes = sorted(pods)
        if not nodes:
            return
        try:
            active = self.check_g1(pods, nodes)
            if active:
                self.check_g3(pods, active)
                self.check_g4_g5(pods, active)
                self.check_fsync(pods, active)
                self.check_g9(pods, active)
        finally:
            # 프로브 디렉터리를 만든(만들었을 수 있는) 스토리지마다 지운다 -- 중간 실패·중단에도.
            self.cleanup(pods)

    # ① G1 ---------------------------------------------------------------------------------------------------------
    def check_g1(self, pods: dict, nodes: list) -> list:
        check = "① G1 같은 스토리지"
        mounts = self.parallel({n: (pods[n], "mount", POD_MOUNTINFO, self.shared_dir, POD_DMS) for n in nodes})
        ok_nodes = []
        for n in nodes:
            res, err = mounts[n]
            if res is None or not res.get("ok"):
                self.report.add(n, check, "UNKNOWN", f"호스트 mountinfo 를 읽지 못했다 -- {err or res.get('error')}")
                continue
            self.fstype[n] = res.get("fstype", "")
            where = f"{res['target']} ({res.get('fstype')} {res.get('source')}, {res.get('options')})"
            if res["target"] == "/":
                via = (f" -- 경로 문자열로는 {res.get('path_target')} 아래지만 실제 장치({res.get('dev')})는 루트 파일시스템이다"
                       if res.get("matched_by") == "device" else "")
                self.report.add(n, check, "FAIL", f"마운트 없음 -- {self.shared_dir} 를 담은 마운트가 '/' 다(공용 스토리지가 "
                                                  f"이 노드에 마운트되지 않았다; /etc/fstab 확인){via}")
                continue
            note = (f" (경로 문자열로는 {res.get('path_target')} 아래 -- 장치 번호 {res.get('dev')} 로 찾았다: SHARED_DIR 가 심링크를 "
                    f"거친다. 심링크 없는 실경로를 권장)" if res.get("matched_by") == "device" else "")
            self.report.add(n, check, "INFO", f"포함 마운트 {where} -- 보고만(노드마다 표기가 다를 수 있다){note}")
            ok_nodes.append(n)
        if not ok_nodes:
            return []
        # 노드마다 프로브 디렉터리를 만든다(있으면 그대로) -- 다른 스토리지를 붙인 노드는 자기 스토리지에 만든다.
        preps = self.parallel({n: (pods[n], "prepare", POD_DMS, self.probe) for n in ok_nodes})
        prepared = []
        for n in ok_nodes:
            res, err = preps[n]
            if res is None:
                self.report.add(n, check, "UNKNOWN", err)
            elif not res.get("ok"):
                self.report.add(n, check, "FAIL", f"{self.shared_dir}/dms 에 프로브 디렉터리를 만들지 못했다(root 쓰기) -- "
                                                  f"{res.get('error')}{self.hint(n, 'squash')}")
            else:
                prepared.append(n)
        self.cleanup_groups = [[n] for n in prepared]          # 묶음을 모르는 동안은 노드마다(이미 지워졌으면 성공)
        if not prepared:
            return []
        names = {n: f"g1-{i}" for i, n in enumerate(prepared)}
        content = {n: f"{self.nonce}:{n}" for n in prepared}
        writes = self.parallel({n: (pods[n], "write_g1", POD_DMS, self.probe, names[n], content[n], self.retry)
                                for n in prepared})
        writers = []
        for n in prepared:
            res, err = writes[n]
            if res is None:
                self.report.add(n, check, "UNKNOWN", err)
            elif not res.get("ok"):
                self.report.add(n, check, "FAIL", f"root 쓰기 실패({res.get('error')}){self.hint(n, 'squash')}")
            else:
                writers.append(n)
        reads = self.parallel({n: (pods[n], "read_many", POD_DMS, self.probe, self.retry,
                                   *[f"{names[m]}={content[m]}" for m in writers if m != n]) for n in writers})
        seen = {}
        for n in writers:
            res, err = reads[n]
            if res is None:
                self.report.add(n, check, "UNKNOWN", err)
                continue
            seen[n] = frozenset([n] + [m for m in writers if m != n and res["seen"].get(names[m])])
        groups, major, tie = self._visibility_groups(seen)
        grouped = {n for g in groups for n in g}
        self.cleanup_groups = groups + [[n] for n in prepared if n not in grouped]
        for n in sorted(seen):
            if n in major and not tie:
                continue
            missing = [m for m in writers if m != n and m not in seen[n]]
            mine = next((g for g in groups if n in g), [n])
            if missing:
                why = (f"{', '.join(missing)} 가 쓴 파일을 {int(self.retry)}초 재시도 뒤에도 못 읽었다(같은 스토리지가 아님; "
                       f"같은 스토리지로 보이는 노드: {', '.join(mine) if len(mine) > 1 else '이 노드뿐'}")
            else:
                why = (f"가시성이 비대칭이다(이 노드는 {', '.join(sorted(seen[n]))} 의 파일을 보지만 그 노드들이 이 노드의 파일을 "
                       f"못 본다")
            side = (" -- 다수 쪽을 가릴 수 없다(묶음 크기가 같다)" if tie
                    else f" -- 다수 쪽({', '.join(major)})과 다른 스토리지다" if major
                    else " -- 서로 일관되게 보이는 노드 묶음이 없다")
            self.report.add(n, check, "FAIL", f"다른 노드 파일이 안 보임 -- {why}){side}{self.hint(n, 'visible')}")
        # 센티널은 프로브 디렉터리와 무관하게 마운트가 있는 모든 노드에서 본다(다른 스토리지면 여기서도 갈린다).
        self.check_sentinel(pods, ok_nodes)
        if not tie:
            for n in major:
                if "FAIL" not in self.report.statuses(n, check):
                    self.report.add(n, check, "PASS", f"교차 노드 nonce {len(major)}대 모두 보임")
        # 묶음 크기가 같아도 이후 단계는 이름순 첫 묶음으로 계속한다(①은 이미 FAIL -- 나머지 계약은 정보로 남는다).
        return major

    @staticmethod
    def _visibility_groups(seen: dict):
        """seen = {node: 그 노드가 읽은 nonce 의 노드 집합(자신 포함)}. 같은 집합을 본 노드끼리 묶고, 묶음 구성원과 「본 집합」(읽기
        결과를 아는 노드로 한정 -- exec 실패 노드가 남의 집합을 어긋나게 하지 않게)이 같은 묶음만 일관된 스토리지로 본다. 반환:
        (일관된 묶음들(큰 것·이름순), 다수 묶음, 다수가 동률인가)."""
        known = set(seen)
        by_set = {}
        for n, s in seen.items():
            by_set.setdefault(s, []).append(n)
        groups = sorted((sorted(members) for s, members in by_set.items() if set(members) == (s & known)),
                        key=lambda g: (-len(g), g[0]))
        if not groups:
            return [], [], False
        return groups, groups[0], len(groups) > 1 and len(groups[1]) == len(groups[0])

    def check_sentinel(self, pods: dict, nodes: list) -> None:
        check = "① G1 센티널 .dms-shared-id"
        got = self.parallel({n: (pods[n], "sentinel", POD_DMS) for n in nodes})
        present, absent = {}, []
        for n in nodes:
            res, err = got[n]
            if res is None:
                self.report.add(n, check, "UNKNOWN", err)
            elif res.get("present"):
                present[n] = res.get("value")
            elif res.get("error") == "ENOENT":
                absent.append(n)
            else:
                self.report.add(n, check, "UNKNOWN", f"읽기 오류 {res.get('error')}")
        if not present and absent:
            # 2026-11-03 리뷰: 예전에는 WARN(「S0 전이면 정상」)이었다 -- 그런데 파드가 떴다면 hostPath <SHARED_DIR>/dms(type
            # Directory)가 이미 있다. 「dms 는 있는데 센티널이 없음」은 S0 미실행·미완료(손으로 만든 dms)이고, 설치 순서상 S7 은
            # S0 뒤라 WARN(exit 3 = 설치 계속)으로 넘기면 S0 를 건너뛴 설치가 통과한다(S4 의 「센티널 없음(S0 미실행)」과 같은 FAIL).
            # exit 3 이 「G3 만」이라는 문서 약속도 이것으로 지켜진다.
            self.report.add("-", check, "FAIL", f"센티널 없음(S0 미실행) -- {self.shared_dir}/dms 는 있는데 어느 노드에도 "
                                                "`.dms-shared-id` 가 없다. 한 노드에서 root 로 "
                                                f"'sh deploy/shared-dir-init.sh {self.shared_dir}' 를 돌린 뒤 다시 확인")
            return
        for n in [n for n, v in present.items() if not v]:
            self.report.add(n, check, "FAIL", "센티널이 비어 있다 -- 스토리지를 가릴 수 없다(S0 초기화가 무작위 값을 쓴다)")
            del present[n]
        values = sorted(set(present.values()))
        for n in absent:
            self.report.add(n, check, "FAIL", "다른 노드에는 있는 센티널이 이 노드에 없다 -- 다른 스토리지가 마운트됐다")
        if len(values) > 1:
            for n, v in sorted(present.items()):
                self.report.add(n, check, "FAIL", f"센티널 값이 노드마다 다르다(이 노드 {v!r}) -- 서로 다른 스토리지")
        elif values:
            for n in sorted(present):
                self.report.add(n, check, "PASS", "센티널 값이 모든 노드에서 같다")

    # ② G3 ---------------------------------------------------------------------------------------------------------
    def check_g3(self, pods: dict, active: list) -> None:
        check = "② G3 노드 간 가시성"
        a, others = active[0], active[1:]
        if not others:
            self.report.add(a, check, "INFO", "노드 1대 -- 교차 확인 없음")
            return
        misses = {n: {"new": 0, "repl": 0, "prime": 0} for n in others}
        broken = {}
        for r in range(self.g3_rounds):
            new, old = f"g3n-{r}", f"g3r-{r}"
            res, err = self.call(pods[a], "put", POD_DMS, self.probe, old, f"old-{r}")
            if res is None or not res.get("ok"):
                broken[a] = err or res.get("error")
                break
            primes = self.parallel({n: (pods[n], "g3_prime", POD_DMS, self.probe, new, old, f"old-{r}", self.retry)
                                    for n in others})
            for n in others:
                res, err = primes[n]
                if res is None:
                    broken[n] = err
                elif not res.get("old_ok"):
                    misses[n]["prime"] += 1
            res, err = self.call(pods[a], "g3_write", POD_DMS, self.probe, new, f"new-{r}", old, f"repl-{r}")
            if res is None or not res.get("ok"):
                broken[a] = err or res.get("error")
                break
            checks = self.parallel({n: (pods[n], "g3_check", POD_DMS, self.probe, new, f"new-{r}", old, f"repl-{r}",
                                        G3_WINDOW) for n in others})
            for n in others:
                res, err = checks[n]
                if res is None:
                    broken[n] = err
                    continue
                misses[n]["new"] += 0 if res.get("new_ok") else 1
                misses[n]["repl"] += 0 if res.get("repl_ok") else 1
        # 단계 실행 오류는 「모름」이 아니라 「G3 확인 못 함」 WARN 이다(2026-11-03 리뷰): G3 는 필수가 아니라(#47) 판정 불가가 설치를
        # 「모름」(exit 4)으로 멈추게 해서는 안 된다. FAIL·모름이 필요한 결함은 ①③④⑤⑥ 이 같은 노드에서 따로 잡는다.
        if a in broken:
            self.report.add(a, check, "WARN", f"G3 확인 못 함 -- A 노드 단계 실패({broken[a]}). 필수 검사가 아니라 설치는 계속")
            return
        for n in others:
            if n in broken:
                self.report.add(n, check, "WARN", f"G3 확인 못 함 -- {broken[n]}. 필수 검사가 아니라 설치는 계속")
                continue
            m = misses[n]
            if any(m.values()):
                self.report.add(n, check, "WARN",
                                f"새 이름·rename 교체 지연(노드 간 캐시) -- {self.g3_rounds}회 중 새 이름 {m['new']}회·rename 교체 "
                                f"{m['repl']}회가 {G3_WINDOW:g}초 안에 안 보였다(기존 파일 첫 읽기 지연 {m['prime']}회). "
                                f"{G3_IMPACT}{self.hint(n, 'g3')}")
            else:
                self.report.add(n, check, "PASS", f"{self.g3_rounds}회 모두 {G3_WINDOW:g}초 안에 보임({a} → 이 노드)")

    # ③ G4 · ④ G5 ---------------------------------------------------------------------------------------------------
    def check_g4_g5(self, pods: dict, active: list) -> None:
        g4, g5 = "③ G4 root 소유 보존", "④ G5 숫자 uid/gid"
        tags = {n: f"t{i}" for i, n in enumerate(active)}
        owners = self.parallel({n: (pods[n], "owner", POD_DMS, self.probe, tags[n]) for n in active})
        good = []
        for n in active:
            res, err = owners[n]
            if res is None:
                self.report.add(n, g4, "UNKNOWN", err)
                continue
            # squash 계열(root 특권이 없다)과 ID 매핑 계열(root 는 0:0 으로 남는데 chown 한 숫자만 다르게 보인다 -- NFSv4 idmap
            # 도메인 불일치 같은)을 가른다(2026-11-03 리뷰): 운영자가 고칠 곳(no_root_squash 대 idmap·sec=sys)이 다르다.
            errs, squash, idmap = res.get("errors") or {}, [], []
            root_ok = "root" not in errs and res.get("root") == [0, 0]
            if "root" in errs:
                squash.append(f"root 가 파일을 만들지 못함({errs['root']})")
            elif not root_ok:
                squash.append(f"root 소유 변경(squash) -- root 가 만든 파일이 uid:gid {res.get('root')}")
            for key, want in (("id26", [26, 26]), ("id65532", [65532, 65532])):
                if key in errs:
                    squash.append(f"root chown {want[0]} 실패({errs[key]})")
                elif res.get(key) != want:
                    (idmap if root_ok else squash).append(f"chown {want[0]}:{want[1]} 뒤 {res.get(key)} 로 보임")
            if "mode" in errs or res.get("mode") != "0o600":
                squash.append(f"root chmod 실패({errs.get('mode') or res.get('mode')})")
            if "traverse" in errs or res.get("traverse") is not True:
                squash.append(f"root 가 남(26:26) 소유 0700 디렉터리를 통과 못함({errs.get('traverse') or res.get('traverse')})")
            if squash:
                self.report.add(n, g4, "FAIL", "; ".join(squash) + self.hint(n, "squash"))
            else:
                self.report.add(n, g4, "PASS", "root 소유 0:0 유지, chown 26·65532, chmod, 남(26:26) 소유 0700 통과")
            if idmap:
                self.report.add(n, g5, "FAIL", f"ID 매핑 -- 같은 노드에서 {'; '.join(idmap)}{self.hint(n, 'idmap')}")
            if not squash and not idmap:
                good.append(n)
        a = active[0]
        if a not in good:
            return
        others = [n for n in active[1:]]
        if not others:
            self.report.add(a, g5, "INFO", "노드 1대 -- 교차 대조 없음(같은 노드의 숫자 uid/gid 는 ③ 에서 확인)")
            return
        checks = self.parallel({n: (pods[n], "owner_check", POD_DMS, self.probe, tags[a], self.retry) for n in others})
        for n in others:
            res, err = checks[n]
            if res is None:
                self.report.add(n, g5, "UNKNOWN", err)
                continue
            if not res.get("ok"):
                self.report.add(n, g5, "FAIL", f"ID 매핑 -- {a} 가 0·26·65532 로 만든 파일이 이 노드에서 {res.get('seen')} 로 보인다"
                                               f"{self.hint(n, 'idmap')}")
            else:
                self.report.add(n, g5, "PASS", f"{a} 의 0·26·65532 소유가 같은 숫자로 보임")
            if res.get("traverse") is not True:
                self.report.add(n, g4, "FAIL", f"{a} 가 만든 남(26:26) 소유 0700 디렉터리를 이 노드의 root 가 {int(self.retry)}초 "
                                               f"재시도 뒤에도 통과 못함({res.get('traverse_error')}){self.hint(n, 'squash')}")

    # ⑤ fsync ------------------------------------------------------------------------------------------------------
    def check_fsync(self, pods: dict, active: list) -> None:
        check = "⑤ fsync 왕복"
        a = active[0]
        res, err = self.call(pods[a], "fsync_write", POD_DMS, self.probe, "fsync-data", FSYNC_BYTES)
        if res is None or not res.get("ok"):
            status = "UNKNOWN" if res is None else "FAIL"
            self.report.add(a, check, status, f"fsync 뒤 같은 노드에서 같은 내용을 못 읽음 -- {err or res.get('error')}")
            return
        self.report.add(a, check, "PASS", f"{FSYNC_BYTES} 바이트 fsync 후 같은 내용(내구성 자체는 스토리지 담당 확인 -- N1)")
        reads = self.parallel({n: (pods[n], "fsync_read", POD_DMS, self.probe, "fsync-data", res["sha"], self.retry)
                               for n in active[1:]})
        for n, (r2, e2) in sorted(reads.items()):
            if r2 is None:
                self.report.add(n, check, "UNKNOWN", e2)
            elif not r2.get("ok"):
                self.report.add(n, check, "FAIL", f"{a} 가 fsync 한 내용을 이 노드가 다르게 읽음({r2.get('error')})")
            else:
                self.report.add(n, check, "PASS", f"{a} 가 fsync 한 내용을 같게 읽음")

    # ⑥ G9 ---------------------------------------------------------------------------------------------------------
    def check_g9(self, pods: dict, active: list) -> None:
        check = "⑥ G9 POSIX 의미"
        tags = {n: f"p{i}" for i, n in enumerate(active)}
        posix = self.parallel({n: (pods[n], "posix", POD_DMS, self.probe, tags[n]) for n in active})
        for n in active:
            res, err = posix[n]
            if res is None:
                self.report.add(n, check, "UNKNOWN", err)
                continue
            problems = []
            if res.get("nlink_error") or (res.get("nlink1"), res.get("nlink2")) != (1, 2):
                problems.append(f"nlink 부정확(새 파일 {res.get('nlink1')}, 하드링크 뒤 {res.get('nlink2')}"
                                f"{', ' + res['nlink_error'] if res.get('nlink_error') else ''})")
            if res.get("nofollow") != "ELOOP":
                problems.append(f"심링크를 O_NOFOLLOW 로 열 때 ELOOP 가 아님({res.get('nofollow')})")
            if res.get("dirfsync") != "ok":
                problems.append(f"디렉터리 fsync 실패({res.get('dirfsync')})")
            if problems:
                self.report.add(n, check, "FAIL", "POSIX 의미(O_EXCL·nlink·O_NOFOLLOW·rename) -- " + "; ".join(problems))
            else:
                self.report.add(n, check, "PASS", "nlink 1→2, O_NOFOLLOW → ELOOP, 디렉터리 fsync")
        self.check_excl(pods, active)
        self.check_atomic(pods, active[0])

    def check_excl(self, pods: dict, active: list) -> None:
        check = "⑥ G9 O_EXCL"
        a = active[0]
        note = ""
        if len(active) >= 2:
            b = active[1]
            # 두 노드가 같은 벽시계 시각에 시작하게 한다(exec 지연 차이로 한쪽이 다 끝난 뒤 시작하면 경합이 없다). 시각은 **A 파드의
            # 시계**로 정한다(2026-11-03 리뷰 -- 설치 호스트 시계는 노드와 분 단위로 어긋날 수 있다: 빠르면 두 파드가 그만큼 자다
            # exec 시간 초과로 「모름」, 느리면 경합 없이 PASS). 노드끼리의 시계 차(NTP)만 남고, 도우미는 EXCL_MAX_WAIT 이상 자지 않는다.
            now, err = self.call(pods[a], "now")
            if now is None or not isinstance(now.get("now"), (int, float)):
                self.report.add(f"{a}+{b}", check, "UNKNOWN", f"A 파드 시계를 읽지 못했다 -- {err}")
                return
            start_at = f"{float(now['now']) + EXCL_LEAD:.3f}"
            got = self.parallel({a: (pods[a], "excl", POD_DMS, self.probe, "x", EXCL_NAMES, start_at, EXCL_MAX_WAIT),
                                 b: (pods[b], "excl", POD_DMS, self.probe, "x", EXCL_NAMES, start_at, EXCL_MAX_WAIT)})
            pair = f"{a}+{b}"
            leads = [r.get("lead") for r, _e in got.values() if r is not None]
            if len(leads) == 2 and all(isinstance(x, (int, float)) for x in leads) and abs(leads[0] - leads[1]) > 1.0:
                note = (f" (두 노드의 시작 대기 차 약 {abs(leads[0] - leads[1]):.1f}초 -- 노드 시계 차로 경합이 약했을 수 있다; "
                        f"NTP 확인)")
        else:
            first = self.call(pods[a], "excl", POD_DMS, self.probe, "x", EXCL_NAMES, 0, 0)
            second = self.call(pods[a], "excl", POD_DMS, self.probe, "x", EXCL_NAMES, 0, 0)
            got, pair = {"1": first, "2": second}, a
        results = [res for res, _err in got.values()]
        if any(r is None for r in results):
            self.report.add(pair, check, "UNKNOWN", "; ".join(e for _r, e in got.values() if e))
            return
        errors = [r.get("error") for r in results if r.get("error")]
        if errors:
            self.report.add(pair, check, "FAIL", f"O_EXCL 생성 오류({errors[0]})")
            return
        counts = [sum(r["bits"][k] == "1" for r in results) for k in range(EXCL_NAMES)]
        double, none = sum(c > 1 for c in counts), sum(c == 0 for c in counts)
        if double or none:
            self.report.add(pair, check, "FAIL", f"POSIX 의미(O_EXCL) -- 같은 이름 {EXCL_NAMES}개 중 둘 다 성공 {double}개, "
                                                 f"아무도 성공 못함 {none}개")
        else:
            self.report.add(pair, check, "PASS", f"같은 이름 {EXCL_NAMES}개를 동시에 O_EXCL -- 이름마다 정확히 하나만 성공{note}")

    def check_atomic(self, pods: dict, a: str) -> None:
        check = "⑥ G9 rename 원자 교체"
        res, err = self.call(pods[a], "put", POD_DMS, self.probe, "atomic", "v00000000")
        if res is None or not res.get("ok"):
            self.report.add(a, check, "UNKNOWN", str(err or res.get("error")))
            return
        got = self.parallel({"w": (pods[a], "atomic_writer", POD_DMS, self.probe, "atomic", ATOMIC_SECONDS),
                             "r": (pods[a], "atomic_reader", POD_DMS, self.probe, "atomic", ATOMIC_SECONDS)})
        (w, we), (r, re_) = got["w"], got["r"]
        if w is None or r is None or not w.get("ok"):
            self.report.add(a, check, "UNKNOWN", f"교체·읽기 단계 실패 -- {we or re_ or (w or {}).get('error')}")
            return
        if r.get("missing") or r.get("torn") or r.get("error"):
            self.report.add(a, check, "FAIL", f"POSIX 의미(rename) -- 교체 {w.get('writes')}회 동안 읽기 {r.get('reads')}회 중 "
                                              f"ENOENT {r.get('missing')}회·중간 내용 {r.get('torn')}회(오류 {r.get('error')})")
        elif not r.get("reads"):
            self.report.add(a, check, "UNKNOWN", "읽기가 한 번도 끝나지 않았다")
        else:
            self.report.add(a, check, "PASS", f"교체 {w.get('writes')}회 동안 읽기 {r.get('reads')}회 -- 중간 상태 없음")

    # 정리 ----------------------------------------------------------------------------------------------------------
    def cleanup(self, pods: dict) -> None:
        """묶음(같은 스토리지)마다 구성원 중 하나에서 지워지면 된다. 실패한 묶음은 지울 경로·노드를 stderr 와 결과(INFO -- 판정에는
        넣지 않는다)에 남긴다 -- 남은 .probe-<nonce> 는 계약 위반이 아니지만 운영자가 치워야 한다."""
        path = f"{self.shared_dir}/dms/{self.probe}"
        for group in self.cleanup_groups:
            errors = []
            for n in group:
                res, err = self.call(pods[n], "cleanup", POD_DMS, self.probe, timeout=300)
                if res is not None and res.get("ok"):
                    break
                errors.append(f"{n}: {err or (res or {}).get('error')}")
            else:
                msg = (f"{path} 를 지우지 못했다({'; '.join(errors)}) -- 노드 {', '.join(group)} 중 하나에서 root 로 "
                       f"'rm -rf {path}'")
                sys.stderr.write(f"주의: {msg}\n")
                self.report.add(",".join(group), "정리", "INFO", msg)


# ---------------------------------------------------------------------------------------------------------------------
# 파드 기동
# ---------------------------------------------------------------------------------------------------------------------
_PULL_REASONS = {"ErrImagePull", "ImagePullBackOff", "InvalidImageName", "ErrImageNeverPull"}


def start_pods(kube, report: Report, *, nodes: list, manifests: dict, nonce: str, timeout: float,
               poll: float = 2.0) -> dict:
    """파드를 만들고 Running 을 기다린다. {node: pod} (Running 만). 못 뜬 노드는 FAIL(hostPath 없음 = 마운트 없음) 또는
    UNKNOWN(이미지·그 밖) 으로 기록한다."""
    check = "① G1 같은 스토리지"
    kube.create({"apiVersion": "v1", "kind": "List", "items": list(manifests.values())})
    by_pod = {m["metadata"]["name"]: n for n, m in manifests.items()}
    running, decided = {}, {}
    deadline = time.monotonic() + timeout
    while True:
        mount_failures = {}
        for ev in kube.events():
            name = ev.get("involvedObject", {}).get("name")
            if name in by_pod and ev.get("reason") == "FailedMount":
                mount_failures[name] = (ev.get("message") or "")[:300]
        for pod in kube.pods(nonce):
            name = pod["metadata"]["name"]
            node = by_pod.get(name)
            if node is None or node in running or node in decided:
                continue
            status = pod.get("status", {})
            states = [c.get("state", {}) for c in status.get("containerStatuses", [])]
            if status.get("phase") == "Running" and states and "running" in states[0]:
                running[node] = name
                continue
            waiting = (states[0].get("waiting") or {}) if states else {}
            if waiting.get("reason") in _PULL_REASONS:
                decided[node] = ("UNKNOWN", f"이미지 pull 실패({waiting.get('reason')}) -- 판정 불가")
            elif status.get("phase") in ("Failed", "Succeeded"):
                decided[node] = ("UNKNOWN", f"파드가 끝났다({status.get('reason') or status.get('phase')})")
            elif name in mount_failures:
                decided[node] = ("FAIL", f"마운트 없음 -- hostPath {manifests[node]['spec']['volumes'][0]['hostPath']['path']} "
                                         f"(type Directory)가 이 노드에 없다(공용 스토리지 미마운트 또는 S0 초기화 전 -- "
                                         f"sh deploy/shared-dir-init.sh): {mount_failures[name]}")
        if len(running) + len(decided) >= len(manifests) or time.monotonic() >= deadline:
            break
        time.sleep(poll)
    for node in sorted(manifests):
        if node in decided:
            report.add(node, check, *decided[node])
        elif node not in running:
            report.add(node, check, "UNKNOWN", f"파드가 {int(timeout)}초 안에 Running 이 되지 않았다 -- 판정 불가")
    return running


def resolve_image(overlay: str) -> str:
    """오버레이를 로컬 렌더(kubectl kustomize -- 클러스터를 보지 않는다)해 dms 이미지(…/dms:<tag>)를 고른다."""
    r = subprocess.run(["kubectl", "kustomize", overlay], capture_output=True, text=True, timeout=120,
                       env={**os.environ, "KUBECONFIG": os.devnull})
    if r.returncode != 0:
        raise RuntimeError(f"kubectl kustomize {overlay} 실패: {r.stderr.strip()[:300]}")
    images = sorted(set(re.findall(r"(?m)^\s*(?:- )?image:\s*(\S+)\s*$", r.stdout)))
    dms = [i for i in images if re.search(r"/dms:[^/]+$", i)]
    if len(dms) != 1:
        raise RuntimeError(f"오버레이에서 dms 이미지를 하나로 고르지 못했다: {images}")
    if dms[0].startswith("set-by-overlay.invalid/") or "REPLACE_" in dms[0]:
        raise RuntimeError(f"오버레이 이미지가 자리표시자다({dms[0]}) -- 렌더된 오버레이나 --image 를 쓴다")
    return dms[0]


def overlay_shared_dir(overlay: str) -> "str | None":
    """오버레이 디렉터리의 values.env 에서 SHARED_DIR(첫 `SHARED_DIR=` 줄, 따옴표 제거). 없으면 None. testbed-k8s 는 이 파일을
    커밋한다(렌더 단계가 없어 kustomize 는 읽지 않는다 -- 그 값의 소비자가 이 프로브다); prod 는 사이트의 values.env."""
    try:
        with open(os.path.join(overlay, "values.env"), encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    m = re.search(r"(?m)^SHARED_DIR=(\S*)", text)
    return m.group(1).strip("'\"") if m else None


def parse_args(argv):
    p = argparse.ArgumentParser(prog="shared-storage-probe.py",
                                description="DMS 공용 스토리지 동작 프로브(설계 v6 3-2 G1~G9, S7)")
    p.add_argument("--context", default=os.environ.get("KUBE_CONTEXT") or None,
                   help="kubeconfig 컨텍스트(필수, 또는 env KUBE_CONTEXT -- 플래그가 이긴다)")
    p.add_argument("--shared-dir", default=os.environ.get("SHARED_DIR") or None,
                   help="공용 디렉터리(또는 env SHARED_DIR; 둘 다 없으면 --overlay 디렉터리의 values.env 의 SHARED_DIR)")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--image", help="dms 이미지 ref(예: pkg-01:5001/dms:d16)")
    src.add_argument("--overlay", help="이 오버레이를 로컬 렌더해 dms 이미지를 고른다(예: deploy/overlays/testbed-k8s) -- "
                                       "--shared-dir 가 없으면 그 디렉터리의 values.env 에서 SHARED_DIR 도 읽는다")
    p.add_argument("--namespace", default="dms")
    p.add_argument("--nodes", help="쉼표 목록(기본: 모든 노드 -- 설계 G1 「모든 마스터·워커」)")
    p.add_argument("--g3-rounds", type=int, default=20)
    p.add_argument("--retry-seconds", type=float, default=60.0, help="교차 노드 확인 재시도 상한(기본 60 = NFS acdirmax)")
    p.add_argument("--start-timeout", type=float, default=180.0)
    p.add_argument("--json", action="store_true", help="stdout 에 JSON 결과만(사람용 줄은 stderr)")
    p.add_argument("--dry-run", action="store_true", help="계획과 파드 매니페스트만 출력(파드를 만들지 않는다)")
    return p.parse_args(argv)


def _usage_error(msg: str) -> int:
    sys.stderr.write(f"usage: {msg}\n")
    return 2


def main(argv=None, *, kube_factory=Kube) -> int:
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    if not args.context:
        return _usage_error("--context <ctx> 가 필요하다(또는 KUBE_CONTEXT) -- 파드를 만드는 스크립트라 current-context 에 기대지 않는다")
    if not args.shared_dir and args.overlay:
        args.shared_dir = overlay_shared_dir(args.overlay)
    if not args.shared_dir:
        return _usage_error("--shared-dir <SHARED_DIR> 가 필요하다(또는 env SHARED_DIR, 또는 --overlay 디렉터리의 values.env)")
    why = shared_dir_problem(args.shared_dir)
    if why:
        return _usage_error(f"SHARED_DIR={args.shared_dir!r} -- {why}")
    if not args.image and not args.overlay:
        return _usage_error("--image <dms 이미지> 또는 --overlay <디렉터리> 가 필요하다")
    if args.g3_rounds < 1 or args.retry_seconds <= 0 or args.start_timeout <= 0:
        return _usage_error("--g3-rounds ≥ 1, --retry-seconds·--start-timeout > 0")
    human = sys.stderr if args.json else sys.stdout
    kube = kube_factory(args.context, args.namespace)
    if not kube.context_exists():
        sys.stderr.write(f"컨텍스트 '{args.context}' 가 kubeconfig 에 없습니다\n")
        return 2
    try:
        image = args.image or resolve_image(args.overlay)
        all_nodes = kube.nodes() if not (args.dry_run and args.nodes) else []
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    if args.nodes:
        wanted = [n.strip() for n in args.nodes.split(",") if n.strip()]
        known = {n["name"]: n for n in all_nodes}
        if all_nodes and [n for n in wanted if n not in known]:
            return _usage_error(f"--nodes 에 없는 노드: {[n for n in wanted if n not in known]}")
        nodes = [known.get(n, {"name": n, "ready": True}) for n in wanted]
    else:
        nodes = all_nodes
    if not nodes:
        return _usage_error("대상 노드가 없다")
    nonce = secrets.token_hex(8)
    # 파드 수명 상한(activeDeadlineSeconds) = 최악의 단계 합 + 여유(2026-11-03 리뷰 -- 예전 식은 ② 의 회차마다 retry 까지 기다리는
    # g3_prime 을 담지 못해 기본값에서도 실행 도중 파드가 끝날 수 있었다). retry 를 기다리는 단계: ① 쓰기·읽기 2 + ② 회차마다 1 +
    # ③④ 1 + ⑤ 1(+ 여유 2), 회차마다 exec 왕복 몇 번(10초), ⑥ 시작 대기 상한, 그리고 정리·기동 여유.
    deadline = int(args.start_timeout + args.retry_seconds * (6 + args.g3_rounds) + args.g3_rounds * 10
                   + EXCL_MAX_WAIT + 600)
    report = Report(human)
    human.write(f"[S7] 공용 스토리지 프로브 -- context {args.context} · SHARED_DIR {args.shared_dir} · 노드 {len(nodes)} · "
                f"이미지 {image} · nonce {nonce}\n")
    ready = [n for n in nodes if n.get("ready", True)]
    manifests = {n["name"]: pod_manifest(nonce=nonce, index=i, node=n["name"], image=image, shared_dir=args.shared_dir,
                                         namespace=args.namespace, deadline=deadline) for i, n in enumerate(ready)}
    if args.dry_run:
        human.write(f"  (dry-run) 파드 {len(manifests)}개를 만들고 {args.shared_dir}/dms/.probe-{nonce} 에만 쓴 뒤 지운다\n")
        doc = {"apiVersion": "v1", "kind": "List", "items": list(manifests.values())}
        if args.json:
            sys.stdout.write(json.dumps({"result": "DRY-RUN", "manifest": doc}, ensure_ascii=False) + "\n")
        else:
            human.write(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
            human.write("RESULT: DRY-RUN\n")
        return 0
    for n in nodes:
        if not n.get("ready", True):
            report.add(n["name"], "① G1 같은 스토리지", "UNKNOWN", "노드가 NotReady -- 판정 불가")

    def _terminate(signum, _frame):
        # 첫 신호만 중단으로 바꾸고 이후 신호는 무시한다 -- 두 번째 SIGTERM·Ctrl-C 가 정리(프로브 디렉터리·파드 삭제)를 끊지 않게.
        # 진행 중 kubectl 은 parallel·Kube._run 이 끊는다(스레드가 exec 시간 초과를 기다리지 않는다).
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        raise KeyboardInterrupt(f"signal {signum}")

    previous = {sig: signal.signal(sig, _terminate) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        pods = start_pods(kube, report, nodes=ready, manifests=manifests, nonce=nonce, timeout=args.start_timeout) \
            if manifests else {}
        Probe(kube, report, shared_dir=args.shared_dir, nonce=nonce, retry_seconds=args.retry_seconds,
              g3_rounds=args.g3_rounds).run(pods)
    except KeyboardInterrupt:
        report.add("-", "중단", "UNKNOWN", "중단됨 -- 정리 후 종료")
    except RuntimeError as exc:
        report.add("-", "실행", "UNKNOWN", str(exc))
    finally:
        if manifests and kube.delete(nonce) is False:
            msg = (f"프로브 파드를 지우지 못했다 -- kubectl --context {args.context} -n {args.namespace} delete pod "
                   f"-l {NONCE_LABEL}={nonce} (파드는 activeDeadlineSeconds {deadline}초 뒤 스스로도 끝난다)")
            sys.stderr.write(f"주의: {msg}\n")
            report.add("-", "정리", "INFO", msg)
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    result = report.result()
    if args.json:
        sys.stdout.write(json.dumps({"result": result, "nonce": nonce, "shared_dir": args.shared_dir,
                                     "items": report.items}, ensure_ascii=False) + "\n")
    else:
        human.write(f"RESULT: {result}\n")
    return EXIT[result]


if __name__ == "__main__":
    sys.exit(main())
