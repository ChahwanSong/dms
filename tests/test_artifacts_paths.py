import os
import pytest
from dms.api.artifacts import (ArtifactError, MAX_BYTES, artifact_dir,
                               list_artifacts, read_artifact, resolve_artifact_path)

JOB = "0" * 32


def test_rejects_bad_job_id():
    with pytest.raises(ArtifactError) as e:
        resolve_artifact_path("/base", "../etc", "execution", "stdout.log")
    assert e.value.reason_code == "invalid_job_id"


def test_rejects_unknown_phase():
    with pytest.raises(ArtifactError) as e:
        resolve_artifact_path("/base", JOB, "etc", "stdout.log")
    assert e.value.reason_code == "invalid_phase"


@pytest.mark.parametrize("name", ["../x", "a/b", "..", "", "x\x00y", "/abs"])
def test_rejects_bad_names(name):
    with pytest.raises(ArtifactError) as e:
        resolve_artifact_path("/base", JOB, "execution", name)
    assert e.value.reason_code == "invalid_artifact_name"


def test_builds_expected_path():
    p = resolve_artifact_path("/base", JOB, "execution", "stdout.log")
    assert p == f"/base/{JOB}/execution/stdout.log"


def test_symlink_escaping_base_is_forbidden(tmp_path):
    base = tmp_path / "base"
    (base / JOB / "execution").mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    os.symlink(outside, base / JOB / "execution" / "stdout.log")
    with pytest.raises(ArtifactError) as e:
        read_artifact(str(base), JOB, "execution", "stdout.log")
    assert e.value.reason_code == "artifact_forbidden"


def test_list_is_empty_when_base_missing(tmp_path):
    assert list_artifacts(str(tmp_path / "nope"), JOB) == []


def test_list_returns_phase_and_name(tmp_path):
    d = tmp_path / JOB / "execution"
    d.mkdir(parents=True)
    (d / "stdout.log").write_text("hello")
    rows = list_artifacts(str(tmp_path), JOB)
    assert [(r["phase"], r["name"], r["size"]) for r in rows] == [("execution", "stdout.log", 5)]


def test_read_truncates_large_file_from_the_end(tmp_path):
    d = tmp_path / JOB / "execution"
    d.mkdir(parents=True)
    (d / "stdout.log").write_text("A" * (MAX_BYTES + 100) + "TAIL")
    out = read_artifact(str(tmp_path), JOB, "execution", "stdout.log")
    assert out["truncated"] is True
    assert out["content"].endswith("TAIL")
    assert len(out["content"].encode()) <= MAX_BYTES


def test_read_tail_lines(tmp_path):
    d = tmp_path / JOB / "execution"
    d.mkdir(parents=True)
    (d / "stdout.log").write_text("\n".join(f"line{i}" for i in range(100)))
    out = read_artifact(str(tmp_path), JOB, "execution", "stdout.log", tail=3)
    assert out["content"].splitlines() == ["line97", "line98", "line99"]


def test_read_missing_file(tmp_path):
    with pytest.raises(ArtifactError) as e:
        read_artifact(str(tmp_path), JOB, "execution", "nope.log")
    assert e.value.reason_code == "artifact_not_found"
