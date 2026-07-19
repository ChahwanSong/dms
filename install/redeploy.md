# 소스 수정 후 재배포 (DMS 코어 · Portal)

이미 설치된 클러스터에 **소스코드 변경**을 반영하는 절차다. 최초 설치는
[dms-02-core.md](dms-02-core.md) / [portal-01-setup.md](portal-01-setup.md), 무중단·drain·백업·rollback
까지 포함한 **정식 업그레이드 절차**는 [../docs/operations-runbook.md](../docs/operations-runbook.md) §8~§10
을 따른다. 이 문서는 "빌드 → 이미지 교체(rollout)"의 빠른 참조다.

## 0. 어떤 소스 → 어떤 이미지 → 어떤 워크로드

| 수정한 소스 | 재빌드 이미지 (`IMAGES` 토큰) | 반영 대상 |
|---|---|---|
| `src/dms/` (api·planner·workers·adapters·query·repositories 등) | `dms` | ns `dms` Deployment: `dms-api`·`dms-planner`·`dms-rm-worker`·`dms-dm-worker`·`dms-retention`·`dms-sanity-reconciler` |
| `src/dms/agent*.py` (노드 agent) | `agent` (dms 위에 빌드되므로 `dms`도 함께) | ns `dms` DaemonSet: `dms-rm-agent`·`dms-dm-agent` |
| DM 잡 도구 이미지(mpifileutils) | `mpifileutils` | DM 잡 런타임 이미지(Deployment 아님; dm-worker가 잡 생성 시 참조) |
| `src/portal/` (BFF `backend/` · SPA `frontend/`) | `portal` | ns `dms-portal` Deployment: `dms-portal` |
| DB **schema**(`src/dms/migrations.py`) 변경 동반 | `dms` | 이미지 교체 **전에** migrate — 아래 §2 주의 |

> 컨테이너명 규약: ns `dms` Deployment는 이름에서 `dms-`를 뗀 것(`dms-api`→`api`,
> `dms-rm-worker`→`rm-worker` …), agent DaemonSet은 `agent`, 포탈은 `portal`.

## 1. 공통: 이미지 빌드 · push

빌드 컨텍스트는 **repo root**이고 **작업 트리 그대로** 이미지에 담긴다(커밋 없이도 빌드되지만, 배포하는
커밋은 남기는 것을 권장). 수정 범위에 해당하는 `IMAGES`만 빌드한다.

```bash
export REGISTRY=<registry>      # 예: pkg-01:5000
export TAG=<새 태그>            # 프로젝트 관례: 단조 증가 vNNN (예: v206→v207) 또는 git short SHA

# 포탈만 수정
REGISTRY=$REGISTRY TAG=$TAG IMAGES="portal" PUSH=1 ./install/docker/build-images.sh
# DMS 코어 수정 (agent 코드도 바뀌었으면 "dms agent")
REGISTRY=$REGISTRY TAG=$TAG IMAGES="dms" PUSH=1 ./install/docker/build-images.sh
# DM 잡 도구 이미지 수정
REGISTRY=$REGISTRY TAG=$TAG IMAGES="mpifileutils" PUSH=1 ./install/docker/build-images.sh
```

- **프록시 전용망**: `PROXY=http://127.0.0.1:7227` 를 추가하면 빌드 시에만 프록시를 타고 런타임 이미지엔
  남지 않는다(메커니즘은 [dms-02-core.md](dms-02-core.md) §1, [portal-01-setup.md](portal-01-setup.md) §5).
- **push 전 로컬 점검**: `PUSH` 생략 후 `docker run --rm -p 18090:8090 -e PORTAL_ALLOW_INSECURE_DEFAULTS=1 <이미지> &`
  → `curl /healthz`.

## 2. DMS 코어 재배포 (ns `dms`)

### 2.1 코드만 변경 (schema·env 무변경) — 이미지 교체

`dms` 이미지를 쓰는 **모든 Deployment**를 새 태그로 교체한다. 현재 대상 목록을 먼저 확인한다(테스트베드는
Deployment가 늘 수 있다):

```bash
NEW="$REGISTRY/dms:$TAG"
kubectl -n dms get deploy -o wide | grep -E "/dms:"     # dms 이미지를 쓰는 Deployment 확인

kubectl -n dms set image deploy/dms-api               api=$NEW
kubectl -n dms set image deploy/dms-planner           planner=$NEW
kubectl -n dms set image deploy/dms-rm-worker         rm-worker=$NEW
kubectl -n dms set image deploy/dms-dm-worker         dm-worker=$NEW
kubectl -n dms set image deploy/dms-retention         retention=$NEW
kubectl -n dms set image deploy/dms-sanity-reconciler sanity-reconciler=$NEW

for d in dms-api dms-planner dms-rm-worker dms-dm-worker dms-retention dms-sanity-reconciler; do
  kubectl -n dms rollout status deploy/$d --timeout=180s
done
```

> **누락 주의**: `dms-dm-worker`·`dms-retention`·`dms-sanity-reconciler`를 빠뜨리면 스테일 이미지로 남는다.
> 운영 런북 §9는 핵심 4개만 나열하므로, 반드시 위 `grep /dms:` 로 실제 대상 전체를 교체한다.

### 2.2 schema · env · Secret 변경을 동반할 때

- **schema(`migrations.py`) 변경**: 이미지 교체 **전에** 두 DB 백업(런북 §8) → migrate Job 재생성·실행
  (런북 §9 ③). 새 코드가 없는 스키마로 뜨지 않도록 순서를 지킨다.
- **env / Secret 변경**: `kubernetes/control-plane.yaml`(ConfigMap·Secret) 수정·apply 후 `dms-api`와
  `dms-sanity-reconciler`를 rollout restart 해야 반영된다(런북 §3·§9).
- **무중단·drain·복구·rollback 포함 정식 절차는 [../docs/operations-runbook.md](../docs/operations-runbook.md)
  §9(업그레이드)를 따른다.**

### 2.3 agent 코드 변경 (DaemonSet)

`agent` 이미지는 `dms` + `mpifileutils` 위에 빌드되므로 `IMAGES="dms agent"`로 함께 빌드한다
(`dms-mpifileutils:$TAG`가 로컬에 없으면 운영 중인 태그를 pull 후 `docker tag`로 재태그 — 도구
이미지는 agent 코드 변경과 무관하므로 재컴파일 불필요). **두 DaemonSet의 이미지가 다르다**:
`dms-rm-agent`는 **plain `dms` 이미지**, `dms-dm-agent`만 `dms-agent`(dms + mfu 도구) 이미지다.

```bash
kubectl -n dms set image daemonset/dms-rm-agent agent=$REGISTRY/dms:$TAG
kubectl -n dms set image daemonset/dms-dm-agent agent=$REGISTRY/dms-agent:$TAG
kubectl -n dms rollout status daemonset/dms-rm-agent daemonset/dms-dm-agent --timeout=180s
```

## 3. Portal 재배포 (ns `dms-portal`)

### 3.1 코드만 변경 (env·Secret·manifest 무변경) — **권장, Secret 보존**

포탈은 단일 Deployment다. **`kubectl set image`로 이미지만 교체**하면 라이브 Secret은 그대로 유지된다
(`kubectl apply`를 쓰지 않으므로 재주입 불필요).

```bash
kubectl -n dms-portal set image deployment/dms-portal portal=$REGISTRY/dms-portal:$TAG
kubectl -n dms-portal rollout status deployment/dms-portal --timeout=120s

# 검증: dms/db 연결 OK
kubectl -n dms-portal exec deploy/dms-portal -- \
  python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8090/healthz').read().decode())"
# → {"status":"ok","dms_configured":true,"db_configured":true}
```

- 포탈 DB(`portal` 스키마)의 테이블/컬럼 추가는 **기동 시 idempotent DDL로 자동 반영**되므로 별도 migrate가
  없다(신규 컬럼·테이블도 재기동만으로 생성).

### 3.2 manifest(env·Secret·리소스) 변경을 동반할 때

`kubernetes/portal.yaml`을 바꿨을 때만 apply한다. **`kubectl apply`는 Secret 값을 placeholder로 덮으므로**
apply 후 반드시 [portal-01-setup.md](portal-01-setup.md) §7.2 로 실값(`PORTAL_SESSION_SECRET`·
`PORTAL_OPERATOR_USERS`·`PORTAL_DMS_TOKEN`·`PORTAL_ADMIN_TOKEN`·`PORTAL_DB_URL`)을 재주입하고 §7.3 rollout
restart 한다. **코드만 바뀌었으면 apply 하지 말고 §3.1의 set image만 쓴다.**

## 4. Rollback

이미지 교체 후 문제가 있으면 직전 리비전으로 되돌린다.

```bash
# DMS 코어 (교체한 Deployment 전부)
for d in dms-api dms-planner dms-rm-worker dms-dm-worker dms-retention dms-sanity-reconciler; do
  kubectl -n dms rollout undo deploy/$d
done
# Portal
kubectl -n dms-portal rollout undo deployment/dms-portal
```

schema를 바꾼 배포의 rollback은 데이터 마이그레이션 역방향을 수반할 수 있으니 반드시
[../docs/operations-runbook.md](../docs/operations-runbook.md) §10을 따른다.

## 참조

- [docker/build-images.sh](docker/build-images.sh) — 이미지 빌드(프록시/캐시/PUSH 옵션)
- [dms-02-core.md](dms-02-core.md) §1 — 코어 이미지 3종 빌드·push (프록시 빌드 포함)
- [portal-01-setup.md](portal-01-setup.md) §5·§6·§7 — 포탈 빌드·manifest·Secret 주입·rollout
- [../docs/operations-runbook.md](../docs/operations-runbook.md) §8 백업 · §9 업그레이드 · §10 rollback
