import json

from maestro.call_log import LoggingConnector


class FakeConn:
    def __init__(self):
        self.calls = []
        self.model = "fake-model"

    def generate_with_tools(self, messages, schemas=None, **kwargs):
        self.calls.append((messages, schemas, kwargs))
        return {"choices": [{"message": {"content": "hi"}}]}


def test_logs_each_call_to_numbered_file(tmp_path):
    inner = FakeConn()
    conn = LoggingConnector(inner, tmp_path / "llm_calls")

    schemas = [{"function": {"name": "write_node"}}]
    out = conn.generate_with_tools([{"role": "user", "content": "go"}], schemas,
                                   max_tokens=8000, reasoning="high")

    assert out == {"choices": [{"message": {"content": "hi"}}]}
    assert inner.calls  # delegated to the real connector

    files = sorted((tmp_path / "llm_calls").glob("*.json"))
    assert [f.name for f in files] == ["0001.json"]
    rec = json.loads(files[0].read_text())
    assert rec["seq"] == 1
    assert rec["request"]["messages"] == [{"role": "user", "content": "go"}]
    assert rec["request"]["tool_names"] == ["write_node"]
    assert rec["request"]["kwargs"] == {"max_tokens": 8000, "reasoning": "high"}
    assert rec["response"] == {"choices": [{"message": {"content": "hi"}}]}


def test_sequence_increments_per_call(tmp_path):
    conn = LoggingConnector(FakeConn(), tmp_path / "llm_calls")
    for _ in range(3):
        conn.generate_with_tools([{"role": "user", "content": "x"}], [])
    files = sorted((tmp_path / "llm_calls").glob("*.json"))
    assert [f.name for f in files] == ["0001.json", "0002.json", "0003.json"]


def test_delegates_unknown_attributes(tmp_path):
    conn = LoggingConnector(FakeConn(), tmp_path / "llm_calls")
    assert conn.model == "fake-model"


def test_exception_is_logged_then_reraised(tmp_path):
    class Boom(FakeConn):
        def generate_with_tools(self, messages, schemas=None, **kwargs):
            raise RuntimeError("kaboom")

    conn = LoggingConnector(Boom(), tmp_path / "llm_calls")
    try:
        conn.generate_with_tools([{"role": "user", "content": "x"}], [])
        assert False, "should have raised"
    except RuntimeError as e:
        assert "kaboom" in str(e)

    rec = json.loads((tmp_path / "llm_calls" / "0001.json").read_text())
    assert "kaboom" in rec["response"]["exception"]
