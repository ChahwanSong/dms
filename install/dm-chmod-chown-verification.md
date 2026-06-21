# DM sync `--chmod` / `--chown` — 클러스터 검증 리포트

mpifileutils `dsync`/`nsync`에 추가된 `--chmod`/`--chown`(목적지 권한·소유자 강제)을 DMS sync 옵션으로
통합하고, **실행 신원(누가 실행하느냐)에 따른 동작 차이**를 PVS DMS 클러스터에서 실측한 기록이다.

> 요약: `--chmod`는 일반 사용자/operator-root 모두 동작(소유 파일 기준). `--chown`은 **임의 소유자 변경에
> 런타임 권한이 필요**하므로, LDAP uid로 실행되는 일반 요청자는 *다른* 소유자 지정 시 **실행 단계에서 실패**하고,
> root(privileged) 요청자만 임의 chown에 성공한다. 같은 옵션이라도 요청자에 따라 결과가 갈린다.

---

## 1. 무엇이 바뀌었나

### 1.1 mpifileutils 이미지
- 소스: `chahwansong/mpifileutils@ae8dee6` — 커밋 *"Add --chown and --chmod options to dsync and nsync"*.
  - `--chown USER:GROUP` (이름/숫자 id; **`needs privilege`** 명시), `--chmod SPEC` 가 `dsync`/`nsync` 양쪽에 추가.
  - 목적지의 uid/gid/mode를 source mirror 대신 강제(디스크상의 source는 불변). dsync는 `--link-dest`/DAOS와 병용 거부.
  - `--chmod`: octal `0750` 또는 `D<oct>,F<oct>`(디렉토리/파일 분리), 특수비트(setuid/setgid/sticky) 지원, 심링크는 chmod 안 함.
- 이미지: `pkg-01:5000/dms-mpifileutils:ae8dee6-u2`
  - `-u1`: base Dockerfile.mpifileutils로 빌드 → **테스트 LDAP 유저 누락**으로 `runuser -u cocoa.song` 실패(아래 §4 교훈).
  - `-u2`: 이전 `e3bfee1-u1`과 동일하게 테스트 유저를 files-NSS에 baked-in
    (`cocoa.song:10003:10000`, `outsider:10010:10001`, base의 `alice:10000:10000`). DMS는 런타임에 `/etc/passwd`를
    주입하지 않고 job 이미지의 NSS로 `runuser` 사용자를 해석하므로, 테스트베드 이미지엔 해당 유저가 있어야 한다(운영은 실제 LDAP NSS).

### 1.2 DMS 통합 (코드)
- `domain.py`: `DATA_SYNC_OPTION_TYPES`에 `chmod: str`, `chown: str` 추가 + 구조 검증
  (`_validate_sync_chmod_spec`/`_validate_sync_chown_spec`, mpifileutils 문법과 일치). 잘못된 spec은 제출 시 `422`.
- `volcano.py`: `_sync_flags`에 `--chmod`/`--chown` 매핑(값은 `shlex.quote`). `_sync_flags`는 `dsync`·`nsync` 공용이라
  한 곳 수정으로 양쪽 적용. 옵션은 `option_fingerprint`에 포함 → preview→confirm 무결성 유지.
- 단위 테스트 `tests/test_dm_sync_chmod_chown.py` 43건(검증 valid/invalid + 플래그 렌더 + shell-quote 방어). 전체 스위트 332 passed.

### 1.3 실행 신원 모델 (왜 "누가 실행하느냐"가 중요한가)
DMS는 sync 잡을 **요청자의 POSIX 신원으로** 실행한다(§10 참조):
- **일반 요청자**(`requester_id`/`owner_username`): preflight가 LDAP로 uid/gid 해석 → job pod는 root로 뜨되
  rank 스크립트가 `runuser -u <user>`로 **그 사용자에게 강등**해 `dsync`/`nsync` 실행.
- **root(privileged) 요청자**(`DMS_DM_ALLOW_ROOT_REQUESTER=true`, mTLS operator): uid/gid 0 합성 → **root로 실행**.

`chown`은 임의 소유자 변경에 `CAP_CHOWN`(=root)이 필요하다. 따라서 동일한 `--chown`이라도 실행 신원이
일반 사용자면 커널이 거부(EPERM), root면 성공한다. `chmod`는 소유자면 가능하므로 대체로 양쪽 동작하지만,
일반 사용자는 자기가 만든 목적지 파일에 한해 적용된다.

---

## 2. 검증 방법

- 스토리지 `cephfs-dms`(managed_root `/cephfs/dms`, `DMS_DM_PATH_BASE=managed_root`).
- 소스 트리 `chmodchk/src` (root:root, dir `0755`/file `0644`, world-readable):
  `src/top.txt`, `src/sub/nested.txt`.
- 각 케이스: `POST /sync`(preview) → `:confirm` → execution. 일반 요청자는 평문 NodePort(30080, `x-dms-actor: operator`),
  root 요청자는 **nginx mTLS 프록시(30443) + operator 클라이언트 인증서**(actor `mtls:<subject>` 주입 → privileged gate 통과).
- 결과 관찰: job 최종 state + execution `error_count`, 그리고 목적지의 `stat`(mode/owner)과 artifact `stderr.log`.

---

## 3. 결과 요약 (PVS 클러스터 실측, 2026-06-21)

이미지 `pkg-01:5000/dms-mpifileutils:ae8dee6-u2`, DMS 앱 `pkg-01:5000/dms:chmod-chown1`.
소스(불변): `dir 0755 / file 0644 / root:root` — 4 케이스 실행 후에도 동일(디스크상 source 불변 확인).

| # | 요청자(실행 uid) | 전송 | 옵션 | 잡 상태 | err | 목적지 실측(mode / owner) | 판정 |
|---|---|---|---|---|---|---|---|
| A | `cocoa.song` (10003) | 평문 | `chmod D0750,F0640` | Succeeded | 0 | dir **0750** / file **0640**, `cocoa.song:developers` | ✅ 적용 |
| B | `cocoa.song` (10003) | 평문 | `chown cocoa.song` (self) | Succeeded | 0 | mode=소스(0755/0644), owner 그대로 `cocoa.song` | ✅ no-op 성공 |
| C | `cocoa.song` (10003) | 평문 | `chown root:root` (타 소유자) | **Succeeded** | 0 | mode=소스, owner **여전히 `cocoa.song`**(root 미적용) | ⚠️ **조용한 무시** |
| D | `root` (0, privileged) | **mTLS 프록시** | `chmod 0700` + `chown 1000:1000` | Succeeded | 0 | **0700** / owner **1000:1000** | ✅ 적용 |

부가 경계(§10.4):
- mTLS operator 인증서 경유(`https://…:30443`) → `200`(프록시가 `x-dms-actor: mtls:<subject>` 주입 → privileged gate 통과).
- **평문 root 요청**(actor=`operator`) → **`403`**. root 합성 실행은 mTLS operator 채널에서만.
  (테스트베드는 `DMS_REQUIRE_MTLS_VERIFIED_HEADER=false`라 게이트는 actor가 `mtls:` 접두가 아니라는 점에 의존; 운영은 `true`로 헤더 스푸핑 차단 — §10.4.)

> **핵심**: 같은 `--chown`이라도 **C(일반 사용자)는 잡이 Succeeded인데 소유권은 안 바뀌고, D(root)는 적용**된다.
> C는 에러도 아니고 실패도 아닌 **조용한 no-op**라 가장 위험하다.

---

## 4. 케이스별 상세 (커맨드 · 목적지 · 로그)

execution rank 스크립트는 `id -u == 0`이면 `runuser -u <user>`로 강등 후 실행한다(일반=해당 LDAP 유저, root=`root`).

### A — 일반 사용자 chmod (적용 성공)
```sh
# job_aa014be8…  requester=cocoa.song uid=10003 gid=10000 provider=ldap
exec runuser -u "$DMS_POSIX_USERNAME" --preserve-environment -- \
  dsync --chmod D0750,F0640 "$DMS_MPI_SYNC_SOURCE" "$DMS_MPI_SYNC_DESTINATION"
```
```
# stat dstA (실측)
drwxr-x--- (0750) cocoa.song:developers  dstA
drwxr-x--- (0750) cocoa.song:developers  dstA/sub
-rw-r----- (0640) cocoa.song:developers  dstA/top.txt
-rw-r----- (0640) cocoa.song:developers  dstA/sub/nested.txt
```
→ 자기 소유 목적지라 `chmod` 그대로 적용. `D0750,F0640`로 디렉토리/파일 모드 분리 동작.

### B — 일반 사용자 chown(self) (no-op 성공)
```sh
# job_fe75add8…  uid=10003
exec runuser -u … -- dsync --chown cocoa.song "$SRC" "$DST"
```
```
# stat dstB: mode=소스(0755/0644), owner=cocoa.song:developers (변화 없음)
```
→ 대상 소유자가 자기 자신이라 `chown(2)`이 no-op로 성공. Succeeded.

### C — 일반 사용자 chown(타 소유자) → **조용한 무시** ⚠️
```sh
# job_614d8e75…  uid=10003 (cocoa.song)
exec runuser -u … -- dsync --chown root:root "$SRC" "$DST"
```
```
# execution stdout 발췌 (실측)
Overriding destination ownership: uid=set gid=set
Setting ownership, permissions, and timestamps.
Completed sync                       # ← exit 0
# stat dstC: owner = cocoa.song:developers  (root:root 아님!)
```
→ dsync는 override를 인지하고(`uid=set gid=set`) ownership 설정을 시도하지만, 비-root가 root로 `chown(2)`하면 **EPERM**.
dsync는 이를 **에러로 surface하지 않고 무시**하고 exit 0 → DMS는 Succeeded로 기록. **소유권은 안 바뀌었는데 잡은 녹색**이다.
(`stderr`엔 chown 관련 줄 없음; `errno=2 Failed to stat dstC`는 생성 전 dest stat로 정상.)

### D — root(privileged) chmod+chown (적용 성공)
```sh
# job_eabafa51…  requester=root → preflight uid=0 gid=0 provider=privileged (mTLS operator 경유)
exec runuser -u "$DMS_POSIX_USERNAME" --preserve-environment -- \
  dsync --chmod 0700 --chown 1000:1000 "$SRC" "$DST"
```
```
# stat dstD (실측)
drwx------ (0700) 1000:1000  dstD
drwx------ (0700) 1000:1000  dstD/sub
-rwx------ (0700) 1000:1000  dstD/top.txt
-rwx------ (0700) 1000:1000  dstD/sub/nested.txt
```
→ root 실행이라 **임의 uid/gid로 chown + chmod 모두 적용**. (bare octal `0700`은 dir·file 동일 적용.)

### nsync (코드 공유 — 동일 동작)
nsync는 dsync와 **동일한 `_sync_flags`**(volcano.py 단일 함수)로 플래그를 만든다 — `_nsync_launcher_command`가 `flags = _sync_flags(options)` 호출. 렌더 결과:
```
nsync --role-mode map --role-map "$DMS_NSYNC_ROLE_MAP" --chmod 0700 --chown 1000:1000 "$SRC" "$DST"
```
실행 신원 경로(runuser/root)도 dsync와 동일하므로 위 신원별 동작(C의 조용한 무시, D의 성공)이 그대로 적용된다.
이미지 `nsync --help`에도 `--chmod/--chown` 노출 확인. (단위 테스트 `test_dm_sync_chmod_chown.py`가 렌더·검증을 커버.)


---

## 5. chown 변형별 세분화 — uid/gid 어느 부분을 바꾸나 (실측)

비-root 요청자가 `--chown`의 **어느 부분(uid/gid)** 을, **어떤 대상**으로 바꾸려 하는지에 따라 비교했다.
요청자 `cocoa.song` = uid `10003`, primary gid `10000`(`developers`), **보조 gid `10001`(`outsiders`)**(이미지 `ae8dee6-u3`에서 보조멤버로 추가).
목적지는 cocoa.song이 생성하므로 기본 `10003:10000`. 별도 표기 없으면 source는 `root:root`.
참고 id: alice `10000`(developers), outsider `10010`(outsiders), nogroup `65534`.

| # | source 소유자 | `--chown` | 의도 | 잡 상태 | 목적지 소유자(실측) | 적용 |
|---|---|---|---|---|---|---|
| 1 | `root:root` | `10010` | uid→타그룹 유저 | Succeeded | `10003:10000` | ✗ |
| 2 | `root:root` | `10000` | uid→**동일그룹** 유저(alice) | Succeeded | `10003:10000` | ✗ |
| 3 | `root:root` | `:10001` | gid→**멤버** 그룹 | Succeeded | `10003:10000` | ✗ |
| 4 | `root:root` | `:65534` | gid→비멤버 그룹 | Succeeded | `10003:10000` | ✗ |
| 5 | `root:root` | `10010:10001` | uid+gid 결합 | Succeeded | `10003:10000` | ✗ |
| 6 | `root:root` | `10010:10001` (**root** 요청자) | uid+gid | Succeeded | **`10010:10001`** | ✓ |
| 7 | **`cocoa.song`**:dev | `:10001` | gid→멤버 그룹 | Succeeded | **`10003:10001`** | ✓ |

**관찰**
- 비-root는 **uid 변경을 절대 못 한다**(1·2·5) — **같은 그룹의 유저라도(2) 불가**. 그룹 멤버십은 uid 변경과 무관.
- 비-root의 gid 변경은 멤버 그룹이라도 **source가 root 소유면 실패(3)**, 그러나 **source가 자기 소유면 성공(7 → 그룹이 `outsiders`로 변경)**. 비멤버 그룹은 당연히 실패(4).
- **root는 임의 uid/gid 적용**(6). 7개 케이스 모두 잡은 **Succeeded(error_count 0)** — 실패조차 녹색.

**메커니즘** — dsync는 목적지 ownership을 **단일 `lchown(uid, gid)`** 로 적용한다(메타데이터 동기화). `--chown :GROUP`은 실행 로그상 `Overriding destination ownership: uid=keep gid=set`이지만, "keep"은 **source 레코드의 uid를 그대로 쓴다**는 뜻이다 — source가 root면 `lchown(0, gid)`가 되어 비-root는 EPERM으로 **전체가 실패**(gid도 미반영). 따라서 **uid 부분이 요청자 자신의 uid와 일치할 때에만** lchown이 통과해 gid 변경까지 반영된다. 케이스 **3 vs 7**(둘 다 `:10001`, source 소유자만 다름)이 이를 분리 입증한다. 모든 실패는 **에러 없이 exit 0**.

**타 유저 소유 source — "bob이 alice 데이터를 가져갈 때"** (요청자 `cocoa.song`=bob: uid 10003, groups `developers`10000(primary)·`outsiders`10001·`dev3`10007; source `chownx/src_alice` = `alice:outsiders` `10000:10001`; 목적지는 bob이 생성 → `10003:10000`):

| `--chown` | 의도 | 목적지 소유자(실측) | 결과 |
|---|---|---|---|
| `cocoa.song:outsiders` | bob:dev (source 그룹, bob 멤버) | `10003:10001` | ✓ |
| `cocoa.song:dev3` | bob:dev2 (bob의 또다른 그룹) | `10003:10007` | ✓ |
| `cocoa.song` (uid만) | self + gid keep | `10003:10001` | ✓ (keep gid=source `outsiders`, bob 멤버) |
| `alice:outsiders` | 소유자를 alice로 유지 | `10003:10000` | ✗ (uid=alice 불가) |
| `:outsiders` (gid만) | 그룹만 dev로 | `10003:10000` | ✗ (keep uid=**alice** → EPERM, **bob이 outsiders 멤버여도**) |
| `cocoa.song:65534` | bob + 비멤버 그룹 | `10003:10000` | ✗ |

핵심: **`bob:<자기그룹>`은 source가 alice 소유여도 성공**한다 — 목적지는 어차피 bob이 생성(→bob 소유)이고 uid를 자기 자신으로 명시하므로. 반면 **`:group`(gid-only)은 bob이 그 그룹 멤버여도 실패**한다 — "keep"이 source의 uid(alice)를 끌어와 `lchown(alice, group)`가 EPERM이기 때문(케이스 3 vs 7과 같은 메커니즘).

**결론** — 비-root `--chown`은 **최종 (uid, gid)가 둘 다 요청자 권한 내일 때만** 적용된다:
- **uid**: 명시하면 반드시 **요청자 자신**, 생략("keep")하면 **source의 uid**를 따른다(→ source가 타인이면 실패).
- **gid**: 명시하면 **요청자가 속한 그룹**, 생략("keep")하면 **source의 gid**를 따른다(→ 비멤버면 실패).

즉 **`<자기>:<자기 그룹>`은 source 소유자와 무관하게 성공**(사본을 자기 소유로 만들기), **gid-only·타 유저 지정은 source uid를 상속해 실패**. 모든 실패는 에러 없이 Succeeded(조용한 no-op). **타인 소유로의 임의 변경은 root(privileged) 요청자 전용**(아래 root 세부).

**root 세부 확인** — root 요청자로 4종 추가 실측(source `root:root`, 전부 Succeeded, error_count 0):

| `--chown` | 목적지 소유자(실측) | 비고 |
|---|---|---|
| `4242` (uid만) | `4242:0` | passwd 엔트리 없는 uid도 설정; gid는 source(root) 유지 |
| `:4242` (gid만) | `0:4242` | group 엔트리 없어도 설정; uid는 source(root) 유지 |
| `31000:32000` | `31000:32000` | 임의 숫자쌍(엔트리 불필요) |
| `alice:outsiders` | `10000:10001` | 이름은 job 이미지 NSS로 해석 |

→ **root는 임의 uid/gid 설정 가능** — 숫자는 항상(엔트리 불필요), 이름은 job 이미지에서 해석될 때. uid/gid 한쪽만 지정하면 나머지는 **source 값을 유지**("keep"; root 소유 source면 0). 단 이는 **cephfs 기준**이며, NFS `root_squash`·userns/idmapped 마운트에선 root도 squash돼 막힐 수 있다(미검증).

---

## 6. 교훈 / 주의

- **job 이미지의 NSS**: DMS는 `runuser -u <user>`로 강등하므로 job 이미지가 그 사용자를 해석할 수 있어야 한다.
  테스트베드는 유저를 baked-in, 운영은 LDAP NSS. 이미지 재빌드 시 이 부분을 빠뜨리면 모든 비-root 잡이 `runuser` 단계에서 실패한다.
- **비-root `--chown`은 "사본을 자기 것으로"만 가능**(§5): 최종 uid는 **요청자 자신**, gid는 **요청자가 속한 그룹**이어야 적용된다.
  따라서 **`<자기>:<자기 그룹>`은 source 소유자와 무관하게 성공**(사본이 요청자 소유가 됨)하지만, **타 유저 유지·gid-only(source uid 상속)·비멤버 그룹**은
  전부 **조용한 no-op**(잡은 Succeeded인데 미적용). "실패 거부"가 아니라 "성공처럼 보이는 미적용"이라 헷갈린다 — 특히 `:group`(gid-only)이
  멤버 그룹인데도 실패하는 점. **타인 소유로의 변경은 root(privileged) 전용**이니, 필요하면 privileged 전용 게이트나 사후 소유자 검증을 검토.
- **root `--chown`은 강력**: D처럼 임의 uid/gid로 목적지 소유권을 만들 수 있다(파일을 임의 사용자 소유로 생성 가능). mTLS operator 전용이지만
  scope/감사 관점에서 주의(§10.4 `DMS_DM_PRIVILEGED_SCOPES`).
- DMS는 spec을 **구조만** 검증한다(이름 존재/권한은 런타임). 잘못된 octal/콜론 등은 `422`로 조기 거부.
