from ai_ecosystem.tools.local.web_search import web_search_tools


HTML = '''
<a class="result__a" href="https://example.com/a">Raphael result</a>
<a class="result__snippet">A useful summary.</a>
<a class="result__a" href="https://example.com/b">Second result</a>
<a class="result__snippet">Another summary.</a>
'''


def test_web_search_returns_bounded_sources(monkeypatch):
    tool, handler = web_search_tools()[0]

    def fake_open(request, timeout):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, *args):
                return HTML.encode()

        assert request.full_url.startswith("https://html.duckduckgo.com/html/")
        assert timeout == 15.0
        return Response()

    monkeypatch.setattr("ai_ecosystem.tools.local.web_search.urlrequest.urlopen", fake_open)
    result = handler({"query": "raphael", "max_results": 1})
    assert result.success is True
    assert result.output["sources"] == [
        {
            "title": "Raphael result",
            "origin": "example.com",
            "url": "https://example.com/a",
            "content": "A useful summary.",
            "claims": [],
        }
    ]


def test_web_search_rejects_invalid_query():
    _, handler = web_search_tools()[0]
    result = handler({"query": "", "max_results": 5})
    assert result.success is False
    assert "query" in (result.error or "")


def test_web_search_network_failure_is_safe(monkeypatch):
    _, handler = web_search_tools()[0]

    def fail(*args, **kwargs):
        raise OSError("private detail")

    monkeypatch.setattr("ai_ecosystem.tools.local.web_search.urlrequest.urlopen", fail)
    result = handler({"query": "test", "max_results": 5})
    assert result.success is False
    assert result.error == "web search providers are unavailable"
    assert "private detail" not in result.error
