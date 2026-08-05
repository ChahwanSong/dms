"""KubernetesClient.read_pod_log 의 디코딩 계약.

실증에서 포탈 로그 뷰어에 `b'DMS_PREFLIGHT_OK\\n'` 이 그대로 보였다 — 쿠버네티스 파이썬
클라이언트가 본문을 역직렬화하면서 bytes 의 repr 을 문자열로 돌려주는 경우가 있기 때문이다.
원시 응답(`_preload_content=False`)을 받아 우리가 직접 디코드해야 한다.
"""
from dms.execution_volcano import KubernetesClient


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeCore:
    def __init__(self, data):
        self._data = data
        self.calls = []

    def read_namespaced_pod_log(self, name, namespace, **kwargs):
        self.calls.append((name, namespace, kwargs))
        return _Resp(self._data)


def _client(data):
    c = KubernetesClient("dms")
    c._core = _FakeCore(data)
    c._ensure = lambda: None          # in-cluster config 로드를 건너뛴다
    return c


def test_decodes_bytes_body():
    c = _client(b"DMS_PREFLIGHT_OK\n")
    assert c.read_pod_log("p1", "dms") == "DMS_PREFLIGHT_OK\n"


def test_requests_the_raw_response():
    c = _client(b"x")
    c.read_pod_log("p1", "dms")
    assert c._core.calls[0][2].get("_preload_content") is False


def test_invalid_utf8_is_replaced_not_raised():
    c = _client(b"ok \xff\xfe end")
    out = c.read_pod_log("p1", "dms")
    assert out.startswith("ok ") and out.endswith(" end")


def test_str_body_passes_through():
    c = _client("already text")
    assert c.read_pod_log("p1", "dms") == "already text"
