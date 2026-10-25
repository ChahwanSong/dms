"""PV → 스토리지 안 상대 경로 템플릿(dms-k8s 설계 D9): 세 프리셋·자리표시자·검증 거부·{subdir} 대소문자·우선순위."""
import json
from pathlib import Path

import pytest

from dms.agent.k8s_fetch import project_pv
from dms.domain import DomainValidationError
from dms.pv_paths import (PRESETS, PvPathUnresolved, effective_template, render, resolve_root,
                          validate_template)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "k8s"
VOL = "pvc-5c25f53f-1111-4aaa-8bbb-000000000001"


def test_presets_cover_the_three_vendor_rules():
    assert PRESETS == {"gpfs": "{volume}/{volume}-data", "onefs": "data/csi/{volume}", "nfs": "{subdir}"}
    # 사용자 지시: GPFS {managed_root}/{VOLUME}/{VOLUME}-data, OneFS {managed_root}/data/csi/{VOLUME}
    assert render(PRESETS["gpfs"], volume=VOL) == f"{VOL}/{VOL}-data"
    assert render(PRESETS["onefs"], volume="k8s-44c29ccbde") == "data/csi/k8s-44c29ccbde"
    assert render(PRESETS["nfs"], volume_attributes={"subdir": "team-a/datasets"}) == "team-a/datasets"
    for template in PRESETS.values():
        assert validate_template(template) == template


@pytest.mark.parametrize("key", ["subdir", "subDir", "SUBDIR", "SubDir"])
def test_subdir_key_is_case_insensitive(key):
    # csi-driver-nfs 는 SC 에 쓴 대소문자 그대로 volumeAttributes 키를 둔다(setKeyValueInMap EqualFold)
    assert render("{subdir}", volume_attributes={key: "ns/pvc"}) == "ns/pvc"


def test_all_placeholders():
    attrs = {"internalName": "trident_pvc_abc", "subDir": "/a/b/"}
    kw = dict(volume="v1", pvc="models", namespace="team-a", storage_class="tb-nfs",
              volume_attributes=attrs, volume_handle="dir/v1/fs1:snap/csi-volumes/v1-abcdef")
    assert render("{namespace}/{pvc}", **kw) == "team-a/models"
    assert render("sc/{storageclass}/{volume}", **kw) == "sc/tb-nfs/v1"
    assert render("{attr:internalname}", **kw) == "trident_pvc_abc"          # 키 대소문자 무시
    assert render("x/{subdir}/y", **kw) == "x/a/b/y"                          # 앞뒤 '/' 를 벗긴다
    assert render("{handle:/:1}", **kw) == "v1"
    assert render("{handle:/:3+}", **kw) == "csi-volumes/v1-abcdef"           # i 번째부터 끝까지(WekaFS innerPath 류)
    gpfs_handle = "2;2;15461220949750720109;69230B0A:67B82745;;pvc-0c56;/ibm/fs1/pvc-0c56/pvc-0c56-data"
    assert render("{handle:;:5}", volume_handle=gpfs_handle) == "pvc-0c56"
    assert render("{handle:#:2}", volume_handle="10.0.0.1#share#team-a/models#uuid#retain") == "team-a/models"


@pytest.mark.parametrize("template,kw", [
    ("{volume}", {"volume": None}),                                    # 값 없음(Pending)
    ("{volume}", {"volume": ""}),
    ("{volume}", {"volume": "a/b"}),                                    # 단일 성분 자리표시자에 '/'
    ("{pvc}", {"pvc": ".."}),
    ("{namespace}", {"namespace": "."}),
    ("{subdir}", {"volume_attributes": {}}),                           # subdir 없음 → 해석 불가(null ≠ 실패)
    ("{subdir}", {"volume_attributes": {"subdir": ""}}),               # 빈 subdir = 공유 루트 전체 -- 거부
    ("{subdir}", {"volume_attributes": {"subdir": "/"}}),
    ("{subdir}", {"volume_attributes": {"subdir": "a/../b"}}),
    ("{subdir}", {"volume_attributes": {"subdir": "a/./b"}}),
    ("{subdir}", {"volume_attributes": {"subdir": "a//b"}}),
    ("{subdir}", {"volume_attributes": {"subdir": "a\x00b"}}),
    ("{attr:path}", {"volume_attributes": {"x": "y"}}),
    ("{handle:#:9}", {"volume_handle": "a#b"}),
    ("{handle:#:0}", {"volume_handle": None}),
    ("{handle:#:1}", {"volume_handle": "a##c"}),                        # 빈 필드
])
def test_unresolvable_values(template, kw):
    with pytest.raises(PvPathUnresolved):
        render(template, **kw)


@pytest.mark.parametrize("bad", [
    "", "   ", None, 42, "/abs/{volume}", "{unknown}", "{volume", "volume}", "{volume}/{", "a/../{volume}",
    "./{volume}", "{attr:}", "{attr:bad key}", "{handle:#}", "{handle::}", "{handle:#:x}",
    "plain/path", "a//{volume}", "{volume}\x00", "x" * 600,
])
def test_validate_template_rejects(bad):
    with pytest.raises(DomainValidationError) as e:
        validate_template(bad)
    assert e.value.reason_code == "invalid_pv_path_template"


def test_validate_template_accepts_and_trims():
    assert validate_template("  {namespace}/{pvc}  ") == "{namespace}/{pvc}"
    assert validate_template("{handle:;:6}") == "{handle:;:6}"
    assert validate_template("{handle:/:3+}") == "{handle:/:3+}"
    assert validate_template("data/{attr:internalName}") == "data/{attr:internalName}"


def test_effective_template_storageclass_override_beats_preset():
    assert effective_template(sc_template="{namespace}/{pvc}", backend_type="gpfs") == ("{namespace}/{pvc}", "storageclass")
    assert effective_template(sc_template=None, backend_type="gpfs") == ("{volume}/{volume}-data", "preset")
    assert effective_template(sc_template="  ", backend_type="onefs") == ("data/csi/{volume}", "preset")
    assert effective_template(sc_template=None, backend_type="cephfs") == (None, None)      # 프리셋 없는 백엔드
    assert effective_template(sc_template=None, backend_type=None) == (None, None)


def test_resolve_root_gives_relative_and_absolute_paths():
    r = resolve_root(template="data/csi/{volume}", managed_root="/k8s_pv/onefs", volume="k8s-44c29ccbde")
    assert r == {"root": "data/csi/k8s-44c29ccbde", "abs_root": "/k8s_pv/onefs/data/csi/k8s-44c29ccbde", "problem": None}
    miss = resolve_root(template="{subdir}", managed_root="/k8s_pv/nfs", volume_attributes={})
    assert miss["root"] is None and miss["abs_root"] is None and "subdir" in miss["problem"]
    none = resolve_root(template=None, managed_root="/x")
    assert none["root"] is None and none["problem"]


def test_synthetic_fixture_pvs_render_with_the_testbed_storage_layout():
    # §8 테스트베드: tb-gpfs(subDir ${pv}/${pv}-data) ↔ gpfs 프리셋, tb-nfs(subDir ns/pvc) ↔ nfs 프리셋, tb-onefs 정적 PV ↔ onefs 프리셋
    raw = json.loads((FIXTURES / "synthetic-pvs.json").read_text())
    pvs = {p["name"]: p for p in (project_pv(i) for i in raw["items"])}
    gpfs = pvs[VOL]
    assert render(PRESETS["gpfs"], volume=gpfs["name"]) == gpfs["volume_attributes"]["subDir"]   # 두 규칙이 같은 곳을 가리킨다
    nfs = pvs["pvc-9d0e1f2a-2222-4ccc-8ddd-000000000002"]
    assert render(PRESETS["nfs"], volume=nfs["name"], volume_attributes=nfs["volume_attributes"]) == "team-a/datasets"
    onefs = pvs["k8s-44c29ccbde"]
    assert render(PRESETS["onefs"], volume=onefs["name"]) == "data/csi/k8s-44c29ccbde"
    with pytest.raises(PvPathUnresolved):                       # 정적 OneFS PV 에는 subdir 가 없다 -- nfs 프리셋으론 안 풀린다
        render(PRESETS["nfs"], volume_attributes=onefs["volume_attributes"])
    legacy = pvs["legacy-nfs-pv"]                               # in-tree nfs PV: csi 칸 없음
    assert legacy["driver"] is None and legacy["nfs_path"] == "/data/legacy" and legacy["volume_attributes"] == {}
