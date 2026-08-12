"""open_artifact_stream / stream_artifact_fd — 다운로드 TOCTOU 불변식(슬라이스 26 Task 2).

계약(각각 뮤테이션으로 증명):
  ① 검사한 fd 그대로 스트림 — 경로 재해석 금지.
  ② fstat 시점 size 만큼만 전송 — 파일이 자라도 응답 불변, 줄면 조기 EOF(0 채움 금지).
  ③ 크기 검사(artifact_too_large)는 봉쇄 통과 **뒤에만** — 순서 역전은 봉쇄 밖 파일의
     존재·크기 오라클이다.
  ④ open/봉쇄 실패는 artifact_not_found 로 뭉갠다(errno 비유출) — artifact_forbidden 은
     서버 내부 구분용으로만 남고 라우트에서 같은 404 로 합쳐진다.
  ⑤ 제너레이터 완주·절단(close()) 양쪽에서 fd 반납 — starlette 1.3.1 은 body_iterator 를
     명시적으로 close 하지 않으므로(실측) try/finally 가 유일한 방어다.
"""
import os
import threading

import pytest
from dms.api.artifacts import (ArtifactError, open_artifact_stream,
                               stream_artifact_fd)

JOB = "0" * 32


def _mkfile(tmp_path, content: bytes, name: str = "stdout.log"):
    d = tmp_path / JOB / "execution"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_bytes(content)
    return p


def test_open_stream_returns_fd_and_fstat_size(tmp_path):
    _mkfile(tmp_path, b"hello world")
    fd, size = open_artifact_stream(str(tmp_path), JOB, "execution", "stdout.log",
                                    max_bytes=None)
    try:
        assert size == 11
        # 반환된 것은 경로가 아니라 열린 fd 다 — 그대로 읽어서 내용이 일치해야 한다.
        assert os.read(fd, 100) == b"hello world"
    finally:
        os.close(fd)


def test_open_stream_symlink_name_is_not_found(tmp_path):
    d = tmp_path / JOB / "execution"
    d.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    os.symlink(outside, d / "stdout.log")
    with pytest.raises(ArtifactError) as e:
        open_artifact_stream(str(tmp_path), JOB, "execution", "stdout.log",
                             max_bytes=None)
    # O_NOFOLLOW 의 ELOOP 도 단순 미존재와 구별되면 안 된다(존재 오라클 차단).
    assert e.value.reason_code == "artifact_not_found"


def test_open_stream_fifo_returns_promptly_not_found(tmp_path):
    """FIFO 는 블록 없이 즉시 artifact_not_found — read_artifact 의 기존 FIFO 테스트
    (test_artifacts_paths.py)와 같은 방식으로 스레드+타임아웃으로 시간을 잰다.
    O_NONBLOCK 이 빠지면 os.open 이 writer 를 영원히 기다린다."""
    d = tmp_path / JOB / "execution"
    d.mkdir(parents=True)
    os.mkfifo(d / "stdout.log")
    result: dict = {}

    def call():
        try:
            fd, _ = open_artifact_stream(str(tmp_path), JOB, "execution",
                                         "stdout.log", max_bytes=None)
        except ArtifactError as exc:
            result["reason"] = exc.reason_code
        except BaseException as exc:  # 진단용 — 예상 못 한 예외도 기록해 둔다
            result["error"] = repr(exc)
        else:
            os.close(fd)
            result["returned"] = True

    t = threading.Thread(target=call, daemon=True)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive(), "open_artifact_stream이 FIFO에서 블록했다 (O_NONBLOCK 누락)"
    assert result.get("reason") == "artifact_not_found", result


def test_open_stream_swapped_phase_dir_is_not_found_not_too_large(tmp_path):
    """순서 계약의 핵심: phase 디렉터리가 봉쇄 밖으로 심링크됐고 그 안의 파일이
    max_bytes 보다 크다 — 크기 검사가 봉쇄보다 먼저면 artifact_too_large 가 나와
    봉쇄 밖 파일의 존재·크기가 새는 오라클이 된다. 404 계열이어야 한다."""
    (tmp_path / JOB).mkdir(parents=True)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    cap = 1024
    (outside_dir / "stdout.log").write_bytes(b"X" * (cap + 100))
    os.symlink(outside_dir, tmp_path / JOB / "execution")
    with pytest.raises(ArtifactError) as e:
        open_artifact_stream(str(tmp_path), JOB, "execution", "stdout.log",
                             max_bytes=cap)
    assert e.value.reason_code != "artifact_too_large"
    # artifact_forbidden 은 내부 구분용 — 라우트에서 artifact_not_found 와 같은 404 로
    # 합쳐진다(routes_artifacts.py). 어느 쪽이든 오라클은 닫혀 있다.
    assert e.value.reason_code in ("artifact_not_found", "artifact_forbidden")


def test_open_stream_too_large_is_rejected_and_fd_closed(tmp_path):
    cap = 1024
    _mkfile(tmp_path, b"Y" * (cap + 1))
    before = len(os.listdir("/proc/self/fd"))
    with pytest.raises(ArtifactError) as e:
        open_artifact_stream(str(tmp_path), JOB, "execution", "stdout.log",
                             max_bytes=cap)
    assert e.value.reason_code == "artifact_too_large"
    assert e.value.detail == str(cap + 1)
    # 거절 경로에서 fd 가 새면 요청 반복만으로 프로세스 fd 예산이 소진된다.
    assert len(os.listdir("/proc/self/fd")) == before


def test_zero_byte_artifact_streams_empty_not_error(tmp_path):
    # 0 바이트는 정상값이다(빈 파일) — 404 로 뭉개지 않는다. 0·빈문자열 truthy 함정.
    _mkfile(tmp_path, b"")
    fd, size = open_artifact_stream(str(tmp_path), JOB, "execution", "stdout.log",
                                    max_bytes=1024)
    assert size == 0
    assert b"".join(stream_artifact_fd(fd, size)) == b""


def test_stream_sends_exactly_fstat_size_when_file_grows(tmp_path):
    """open 후 append — 사용자가 자기 파일을 키워 응답을 무한정 늘리는 공격.
    전송량은 fstat 시점 size 로 캡돼야 한다(Content-Length 일치)."""
    p = _mkfile(tmp_path, b"A" * 100)
    fd, size = open_artifact_stream(str(tmp_path), JOB, "execution", "stdout.log",
                                    max_bytes=None)
    assert size == 100
    with open(p, "ab") as f:
        f.write(b"B" * 500)
    out = b"".join(stream_artifact_fd(fd, size, chunk=64))
    assert out == b"A" * 100  # 뒤에 붙은 것은 한 바이트도 안 나간다


def test_stream_truncate_causes_early_honest_eof(tmp_path):
    """open 후 truncate — 조기 EOF 로 정직하게 끊는다. 0 채움으로 size 를 맞추면
    클라이언트가 절단을 감지할 수 없다. 무한 루프도 없어야 한다."""
    p = _mkfile(tmp_path, b"C" * 1000)
    fd, size = open_artifact_stream(str(tmp_path), JOB, "execution", "stdout.log",
                                    max_bytes=None)
    os.truncate(p, 300)
    out = b"".join(stream_artifact_fd(fd, size, chunk=128))
    assert out == b"C" * 300  # 잘린 내용 그대로, 0 채움 없음
    assert len(out) < size


def test_stream_closes_fd_on_completion(tmp_path):
    _mkfile(tmp_path, b"D" * 10)
    fd, size = open_artifact_stream(str(tmp_path), JOB, "execution", "stdout.log",
                                    max_bytes=None)
    assert b"".join(stream_artifact_fd(fd, size)) == b"D" * 10
    with pytest.raises(OSError):  # EBADF — 완주 후 fd 반납
        os.fstat(fd)


def test_stream_closes_fd_on_early_close(tmp_path):
    """클라이언트 절단의 단위 모델: starlette 1.3.1 은 body_iterator 를 명시적으로
    close 하지 않는다(실측) — 제너레이터의 try/finally 만이 fd 를 반납한다."""
    _mkfile(tmp_path, b"E" * 300)
    fd, size = open_artifact_stream(str(tmp_path), JOB, "execution", "stdout.log",
                                    max_bytes=None)
    gen = stream_artifact_fd(fd, size, chunk=100)
    assert next(gen) == b"E" * 100
    gen.close()  # GeneratorExit → finally
    with pytest.raises(OSError):  # EBADF — 절단 후에도 fd 반납
        os.fstat(fd)
