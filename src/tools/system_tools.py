import json
import logging

from tools.tool_manager import tool_manager

logger = logging.getLogger(__name__)


@tool_manager.tool(
    description="Search the web for information using DuckDuckGo. Returns a list of relevant results with titles, URLs, and snippets.",
    auto_inject_context=False,
)
def web_search(
    query: str,
    num_results: int = 5,
) -> str:
    logger.info(f"web_search called: {query}")
    try:
        from ddgs import DDGS

        with DDGS() as ddgs:
            raw_results = list(ddgs.text(query, max_results=num_results))

        results = []
        for r in raw_results:
            results.append({
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
            })

        return json.dumps({
            "success": True,
            "query": query,
            "results": results,
        })
    except Exception as e:
        logger.error(f"web_search failed: {e}")
        return json.dumps({
            "success": False,
            "query": query,
            "results": [],
            "error": str(e),
        })


@tool_manager.tool(
    description="Fetch content from a URL. Returns the page content, optionally extracted as clean text.",
    auto_inject_context=False,
    param_hints={"url": "Must include http:// or https://"},
)
def web_fetch(
    url: str,
    extract_text: bool = True,
    max_length: int = 10000,
) -> str:
    logger.info(f"web_fetch called: {url}")
    try:
        import requests
        from bs4 import BeautifulSoup

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        content_type = response.headers.get('content-type', 'unknown')

        if extract_text and 'text/html' in content_type:
            soup = BeautifulSoup(response.text, 'html.parser')

            for script in soup(['script', 'style', 'nav', 'footer', 'header']):
                script.decompose()

            text = soup.get_text()
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = '\n'.join(chunk for chunk in chunks if chunk)

            content = text[:max_length] if len(text) > max_length else text
        else:
            content = response.text[:max_length] if len(response.text) > max_length else response.text

        return json.dumps({
            "success": True,
            "url": url,
            "content": content,
            "content_type": content_type,
            "status_code": response.status_code,
            "truncated": len(response.text) > max_length
        })

    except requests.exceptions.RequestException as e:
        logger.error(f"web_fetch failed for {url}: {e}")
        return json.dumps({
            "success": False,
            "url": url,
            "error": str(e),
            "content": ""
        })
    except Exception as e:
        logger.error(f"web_fetch unexpected error for {url}: {e}")
        return json.dumps({
            "success": False,
            "url": url,
            "error": f"Unexpected error: {str(e)}",
            "content": ""
        })
