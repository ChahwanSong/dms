# DMS worker node OS 메트릭 (서브프로젝트 B) 설계

- 날짜: 2026-06-25
- 상태: 승인됨 (구현 대기)
- 범위: DMS 노드 agent가 호스트 OS 메트릭(CPU/메모리/load/disk)을 수집 → AgentReport → 포탈 대시보드 워커 노드 패널 표시
- DMS 변경: agent 코드 + AgentReport 스키마 + (disk만) DaemonSet read-only host-root 마운트 + agent 이미지 2개 재빌드·재배포

## 1. 핵심 사실 (라이브 확인)

- agent 컨테이너의 **자체 `/proc`가 호스트 노드 값**을 보여준다(procfs cpu/mem/loadavg는 네임스페이스 분리 안 됨). 라이브: dms-w3 agent에서 `/proc/meminfo` MemTotal=2014860kB = 노드 capacity 정확히 일치, `/proc/stat` 2코어 = 노드 capacity. → **CPU/메모리/load는 마운트 없이** 읽는다.
- 단 **disk 사용률(statvfs)**은 fs 접근이 필요한데, 라이브 agent 컨테이너는 storage 마운트(cephfs)를 갖고 있지 않다. → OS 루트 디스크 사용률은 **read-only host-root 마운트**(`/`→`/host`)가 있어야 statvfs 가능. (DaemonSet은 이미 `/proc/1/mountinfo` hostPath를 쓰므로 동일 패턴의 read-only 추가.)

## 2. DMS 변경

### 2.1 `src/dms/domain.py` — AgentReport
`os_metrics: dict[str, Any] = Field(default_factory=dict)` 추가. (schema_version bump.)

### 2.2 `src/dms/agent_daemon.py` — `probe_os_metrics()`
메트릭별 **fail-soft**(try/except → 해당 항목 생략, 보고는 안 깨짐). `build_agent_report`에서 호출해 `report["os_metrics"]`로.

```python
def probe_os_metrics(proc_path: str = "/proc", host_root: str | None = None) -> dict[str, Any]:
    m: dict[str, Any] = {}
    # load average
    try:
        a, b, c = open(f"{proc_path}/loadavg").read().split()[:3]
        m["load"] = {"load1": float(a), "load5": float(b), "load15": float(c)}
    except Exception: pass
    # memory (kB)
    try:
        info = {}
        for line in open(f"{proc_path}/meminfo"):
            k, _, rest = line.partition(":"); info[k.strip()] = int(rest.split()[0])
        total = info.get("MemTotal", 0); avail = info.get("MemAvailable", info.get("MemFree", 0))
        m["memory"] = {"total_kb": total, "available_kb": avail,
                       "used_pct": round((total-avail)/total*100, 1) if total else None}
    except Exception: pass
    # cpu% (two /proc/stat samples)
    try:
        def cpu():
            for line in open(f"{proc_path}/stat"):
                if line.startswith("cpu "):
                    v = [int(x) for x in line.split()[1:]]
                    return sum(v), v[3] + (v[4] if len(v) > 4 else 0)   # total, idle+iowait
        t, sl = cpu(), time.sleep(0.4); t2 = cpu()
        if t and t2 and t2[0] != t[0]:
            m["cpu"] = {"percent": round((1 - (t2[1]-t[1])/(t2[0]-t[0]))*100, 1), "cores": os.cpu_count()}
    except Exception: pass
    # disk (OS root) — needs host-root mount; fail-soft if absent
    root = host_root or os.environ.get("DMS_AGENT_HOST_ROOT")
    if root:
        try:
            s = os.statvfs(root); total_b = s.f_frsize*s.f_blocks; free_b = s.f_frsize*s.f_bavail
            m["disk"] = {"path": "/", "total_gb": round(total_b/1e9, 1),
                         "used_pct": round((total_b-free_b)/total_b*100, 1) if total_b else None}
        except Exception: pass
    return m
```
config(`AgentDaemonConfig`)에 `host_root` 추가(env `DMS_AGENT_HOST_ROOT`) 또는 위처럼 env 직접 읽기.

### 2.3 DaemonSet 매니페스트 (`deploy/kubernetes/dms-agent-daemonset.yaml` + `install/kubernetes/agent-daemonset.yaml`)
양 DaemonSet(RM/DM)에 **read-only host-root** 볼륨 + env 추가:
```yaml
        env: [{ name: DMS_AGENT_HOST_ROOT, value: /host }]
        volumeMounts: [{ name: host-root, mountPath: /host, readOnly: true }]
      volumes: [{ name: host-root, hostPath: { path: /, type: Directory } }]
```
> ⚠️ 보안: read-only host-root 마운트는 agent가 호스트 fs를 **읽기**로 보게 한다(statvfs 목적). 기존 `/proc/1/mountinfo` hostPath와 유사한 read-only 노출. disk 메트릭을 원치 않으면 이 마운트만 제거하면 CPU/메모리/load는 그대로 동작(disk만 fail-soft 생략).

### 2.4 재배포
agent 이미지 2개 재빌드(rm=`dms:*`, dm=`dms-agent:*-mfu2` — 둘 다 src/dms 포함) → 두 DaemonSet `set image` + 매니페스트 볼륨 patch + rollout(전 노드).

## 3. 포탈 변경 (DMS API 변경 없음)
- `GET /operations/agent-reports`가 `report.os_metrics`를 그대로 전달 → BFF `/dashboard/nodes`(latest per node) 통과.
- 프론트 `AgentReport` 타입에 `os_metrics?` 추가. `NodesTable` 행에 **CPU% · 메모리% · load1 · disk%** 셀 추가(값 없으면 "—"). 노드 카드는 유지.

## 4. 테스트 / 검증
- 단위: `probe_os_metrics`를 mock proc 텍스트(임시파일)로 cpu/mem/load 파싱 검증 + disk statvfs(tmp_path) 검증 + 누락 시 fail-soft.
- agent: 재배포 전 `dms agent-probe --once`(또는 한 노드에서) os_metrics 포함 확인.
- 라이브: 대시보드 워커 노드 패널에 실값(dms-w*: cores 2, mem 사용률, load, disk%) 표시. 콘솔 에러 0. agent freshness 유지(probe 실패로 stale 안 되게).

## 5. 위험 / 범위
- 전 노드 agent 교체(양 DaemonSet) — probe fail-soft + DM worker report-freshness 게이트로 보호. probe가 보고를 절대 못 깨게.
- disk는 OS 루트(`/host`) 사용률만. cephfs 스토리지 용량은 비범위(분산 스토리지, 별도 모니터링).
- 비범위: per-process, I/O throughput, GPU 등.
