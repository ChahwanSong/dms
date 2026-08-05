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


def test_rejects_name_with_trailing_newline():
    # re의 '$'는 문자열 끝의 개행 *앞*에서도 매칭되므로 "..\n"은 NAME_RE를 통과했고,
    # set("..\n") == {".", "\n"}이라 점-전용 가드도 발동하지 않았다 — 즉 ".."를 막으려고
    # 넣은 가드가 개행 하나로 우회됐다. fullmatch로 막는다.
    with pytest.raises(ArtifactError) as e:
        resolve_artifact_path("/base", JOB, "execution", "..\n")
    assert e.value.reason_code == "invalid_artifact_name"


def test_rejects_job_id_with_trailing_newline():
    with pytest.raises(ArtifactError) as e:
        artifact_dir("/base", "0" * 32 + "\n")
    assert e.value.reason_code == "invalid_job_id"


def test_symlink_escaping_base_is_rejected(tmp_path):
    base = tmp_path / "base"
    (base / JOB / "execution").mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    os.symlink(outside, base / JOB / "execution" / "stdout.log")
    with pytest.raises(ArtifactError) as e:
        read_artifact(str(base), JOB, "execution", "stdout.log")
    # 탈출 시도와 단순 미존재는 호출자에게 구별되면 안 된다(존재 오라클).
    assert e.value.reason_code == "artifact_not_found"


def test_symlinked_phase_dir_escaping_base_is_rejected(tmp_path):
    # 최종 컴포넌트가 아니라 phase 디렉터리가 심링크인 경우 — O_NOFOLLOW로는 못 막고
    # 열린 fd의 realpath 봉쇄가 막아야 한다.
    base = tmp_path / "base"
    (base / JOB).mkdir(parents=True)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "stdout.log").write_text("TOP-SECRET")
    os.symlink(outside_dir, base / JOB / "execution")
    with pytest.raises(ArtifactError) as e:
        read_artifact(str(base), JOB, "execution", "stdout.log")
    assert e.value.reason_code in ("artifact_forbidden", "artifact_not_found")


def test_symlink_swapped_after_containment_check_does_not_leak(tmp_path, monkeypatch):
    # TOCTOU 재현: 예전 구현은 경로 문자열을 세 번(isfile/getsize/open) 다시 해석했다.
    # 공격자는 검사와 open 사이에 os.replace로 평범한 파일을 심링크로 바꿔치기한다.
    # 여기서는 "검사 시점에는 아직 평범한 파일이었다"를 realpath 스텁으로 재현한다.
    base = tmp_path / "base"
    (base / JOB / "execution").mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("TOP-SECRET")
    link = base / JOB / "execution" / "stdout.log"
    os.symlink(outside, link)
    real_realpath = os.path.realpath

    def stale_realpath(p, *a, **k):
        return str(link) if str(p) == str(link) else real_realpath(p, *a, **k)

    monkeypatch.setattr(os.path, "realpath", stale_realpath)
    try:
        out = read_artifact(str(base), JOB, "execution", "stdout.log")
    except ArtifactError as e:
        assert e.reason_code == "artifact_not_found"
    else:
        pytest.fail(f"leaked outside content: {out['content']!r}")


def test_unreadable_file_is_not_found(tmp_path):
    d = tmp_path / JOB / "execution"
    d.mkdir(parents=True)
    f = d / "stdout.log"
    f.write_text("hello")
    os.chmod(f, 0o000)
    try:
        with pytest.raises(ArtifactError) as e:
            read_artifact(str(tmp_path), JOB, "execution", "stdout.log")
        assert e.value.reason_code == "artifact_not_found"
    finally:
        os.chmod(f, 0o600)


def test_list_is_empty_when_base_missing(tmp_path):
    assert list_artifacts(str(tmp_path / "nope"), JOB) == {"entries": [], "truncated": False}


def test_list_returns_phase_and_name(tmp_path):
    d = tmp_path / JOB / "execution"
    d.mkdir(parents=True)
    (d / "stdout.log").write_text("hello")
    out = list_artifacts(str(tmp_path), JOB)
    assert out["truncated"] is False
    assert [(r["phase"], r["name"], r["size"]) for r in out["entries"]] == \
        [("execution", "stdout.log", 5)]


def test_list_skips_symlinked_file(tmp_path):
    d = tmp_path / JOB / "execution"
    d.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("TOP-SECRET-AND-LARGE")
    os.symlink(outside, d / "stdout.log")
    (d / "real.log").write_text("ok")
    out = list_artifacts(str(tmp_path), JOB)
    assert [r["name"] for r in out["entries"]] == ["real.log"]


def test_list_does_not_follow_symlinked_phase_dir(tmp_path):
    (tmp_path / JOB).mkdir(parents=True)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    for n in ("a.conf", "b.conf"):
        (outside_dir / n).write_text("x")
    os.symlink(outside_dir, tmp_path / JOB / "execution")
    out = list_artifacts(str(tmp_path), JOB)
    assert out["entries"] == []


def test_list_caps_entries(tmp_path):
    from dms.api.artifacts import MAX_ENTRIES  # 픽스 이전 코드에는 없는 상수

    d = tmp_path / JOB / "execution"
    d.mkdir(parents=True)
    for i in range(MAX_ENTRIES + 50):
        (d / f"f{i:05d}.log").write_text("x")
    out = list_artifacts(str(tmp_path), JOB)
    assert len(out["entries"]) == MAX_ENTRIES
    assert out["truncated"] is True


def test_read_truncates_large_file_from_the_end(tmp_path):
    d = tmp_path / JOB / "execution"
    d.mkdir(parents=True)
    (d / "stdout.log").write_text("A" * (MAX_BYTES + 100) + "TAIL")
    out = read_artifact(str(tmp_path), JOB, "execution", "stdout.log")
    assert out["truncated"] is True
    assert out["content"].endswith("TAIL")
    assert len(out["content"].encode()) <= MAX_BYTES


def test_read_is_capped_even_if_size_changes_after_stat(tmp_path, monkeypatch):
    # stat과 open 사이에 파일이 커지거나 통째로 바꿔치기된 상황. 예전 구현은 stat이
    # 알려준 크기만 믿고 f.read()로 파일 전체를 읽어 256KB 캡을 넘겼다(리뷰어는
    # 20MB 응답을 측정). 이제는 읽기 자체가 MAX_BYTES로 묶여 있어야 한다.
    d = tmp_path / JOB / "execution"
    d.mkdir(parents=True)
    (d / "stdout.log").write_bytes(b"B" * (MAX_BYTES * 4))
    monkeypatch.setattr(os.path, "getsize", lambda p, *a, **k: 10)
    out = read_artifact(str(tmp_path), JOB, "execution", "stdout.log")
    assert len(out["content"].encode()) <= MAX_BYTES


def test_read_tail_lines(tmp_path):
    d = tmp_path / JOB / "execution"
    d.mkdir(parents=True)
    (d / "stdout.log").write_text("\n".join(f"line{i}" for i in range(100)))
    out = read_artifact(str(tmp_path), JOB, "execution", "stdout.log", tail=3)
    assert out["content"].split("\n") == ["line97", "line98", "line99"]


def test_read_tail_does_not_split_carriage_return_progress(tmp_path):
    # rsync/dsync류 진행률 출력은 한 줄 안에서 '\r'로 덮어쓴다. str.splitlines()는
    # '\r'·'\v'·'\f'·'\x85'에서도 쪼개므로 tail=N이 줄이 아니라 조각을 돌려줬다.
    d = tmp_path / JOB / "execution"
    d.mkdir(parents=True)
    (d / "stdout.log").write_text("start\n10%\r20%\r30%\rdone\n")
    out = read_artifact(str(tmp_path), JOB, "execution", "stdout.log", tail=2)
    assert out["content"] == "start\n10%\r20%\r30%\rdone"


def test_read_missing_file(tmp_path):
    with pytest.raises(ArtifactError) as e:
        read_artifact(str(tmp_path), JOB, "execution", "nope.log")
    assert e.value.reason_code == "artifact_not_found"
