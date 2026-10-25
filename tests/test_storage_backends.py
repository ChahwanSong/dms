"""스토리지 백엔드 식별자 셋 계약(2026-09-15).

backend_type 은 검증·저장·표시·변경 가드(routes_storages 의 storage_in_use)에만
쓰이는 라벨이다 -- 마운트 프로브(agent/probes.probe_mounts)·잡 볼륨(execution_volcano
._volumes)·경로 해석(stepper._abs)은 전부 mount_path/managed_root 만 본다. 그래서
값을 더하는 일은 세 곳을 함께 움직여야 하고 이 테스트가 그 일치를 고정한다:
  1. src/dms/repositories/storages._BACKENDS      (서버가 받는 식별자)
  2. frontend .../storages/StorageDialog.tsx BACKENDS (표시명 ↔ 식별자)
  3. frontend/src/lib/api.ts invalid_storage 문구  (422 안내에 식별자 나열)

dms-k8s(2026-10-25 설계 D9·D11): onefs(Dell PowerScale)·nfs(일반 NFS / csi-driver-nfs)를 더했다. backend_type 은 이제
**PV 경로 프리셋 키**이기도 하다(pv_paths.PRESETS -- gpfs·onefs·nfs). 직접 경로 해석은 여전히 managed_root 만 본다.
"""
import re
from pathlib import Path

import pytest

from dms.domain import DomainValidationError
from dms.repositories.storages import _BACKENDS, StoragesRepository

REPO_ROOT = Path(__file__).resolve().parent.parent
DIALOG_TSX = REPO_ROOT / "frontend" / "src" / "features" / "storages" / "StorageDialog.tsx"
API_TS = REPO_ROOT / "frontend" / "src" / "lib" / "api.ts"

EXPECTED = ("cephfs", "gpfs", "wekafs", "lustre", "purestorage", "netapp", "onefs", "nfs")


def test_backend_set_is_the_documented_eight():
    assert _BACKENDS == EXPECTED


@pytest.mark.parametrize("backend", EXPECTED)
def test_every_backend_is_accepted_and_stored_verbatim(db, backend):
    repo = StoragesRepository(db)
    repo.create(storage_name=f"s-{backend}", mount_path=f"/{backend}",
                managed_root=f"/{backend}/dms", backend_type=backend, actor="admin")
    assert repo.get(f"s-{backend}")["backend_type"] == backend


@pytest.mark.parametrize("bad", ["NFS", "CephFS", "Lustre", "pure", "ontap", "isilon", "", None])
def test_unknown_or_miscased_backend_is_rejected(db, bad):
    repo = StoragesRepository(db)
    with pytest.raises(DomainValidationError) as e:
        repo.create(storage_name="s-bad", mount_path="/x", managed_root="/x/dms",
                    backend_type=bad, actor="admin")
    assert e.value.reason_code == "invalid_storage"


def _dialog_values():
    src = DIALOG_TSX.read_text(encoding="utf-8")
    block = re.search(r"export const BACKENDS = \[(.*?)\];", src, re.S)
    assert block, "StorageDialog.tsx 의 BACKENDS 리터럴을 찾지 못함"
    return re.findall(r'value:\s*"([^"]+)"', block.group(1))


def test_frontend_dialog_offers_exactly_the_server_backends():
    values = _dialog_values()
    assert len(values) == len(set(values)), "중복 옵션"
    assert tuple(values) == _BACKENDS, (
        "프런트 BACKENDS value 와 서버 _BACKENDS 가 다르다 -- 순서까지 같아야 셀렉트 "
        "표시 순서 = 서버 문서 순서. 둘 다 고쳐라.")


def test_invalid_storage_message_lists_every_backend():
    src = API_TS.read_text(encoding="utf-8")
    line = next((ln for ln in src.splitlines() if ln.strip().startswith("invalid_storage:")), None)
    assert line, "api.ts 에 invalid_storage 안내 문구가 없다"
    listed = re.search(r"백엔드는 (.+?) 중 하나", line)
    assert listed, "invalid_storage 문구가 '백엔드는 … 중 하나' 형식을 벗어남"
    assert tuple(listed.group(1).split("·")) == _BACKENDS


def test_every_pv_path_preset_key_is_a_registered_backend():
    # 프리셋 키(pv_paths.PRESETS)는 등록 가능한 backend_type 이어야 한다 -- 등록할 수 없는 키의 프리셋은 죽은 규칙이다.
    from dms.pv_paths import PRESETS
    assert set(PRESETS) <= set(_BACKENDS)
