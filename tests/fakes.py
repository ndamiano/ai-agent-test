"""Stand-ins shared across test files. Imported, not injected — a fixture would put a parameter
on every signature that only needs a class."""


class FakeResponse:
    """What `requests` hands back, as much of it as the callers touch."""

    def __init__(self, status_code=200, body=None, content=b"", text="", headers=None):
        self.status_code = status_code
        self._body = body or {}
        self.content = content
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._body

    def raise_for_status(self):
        pass
