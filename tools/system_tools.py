"""
System tools: execute_command, web_search, web_fetch.

Real implementations for command execution and web search.
web_fetch is currently a mock — replace with real implementation when needed.
"""

import json
import logging
import os
import subprocess
from typing import Optional

from tools.tool_manager import tool_manager

logger = logging.getLogger(__name__)


def _execute_command(
    command: str,
    working_dir: Optional[str] = None,
    timeout: int = 30,
) -> str:
    """
    Execute a shell command and return its output.

    Args:
        command: The shell command to execute
        working_dir: Optional working directory (defaults to task working_directory or configured working directory)
        timeout: Command timeout in seconds
    """
    from tools.execution_context import resolve_base_path

    if working_dir is None:
        working_dir = resolve_base_path()
    else:
        working_dir = resolve_base_path(working_dir)

    working_dir = str(working_dir)
    logger.info(f"Executing command: {command} in {working_dir}")

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=working_dir,
            timeout=timeout,
        )
        return json.dumps({
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.returncode,
        })
    except subprocess.TimeoutExpired:
        logger.warning(f"Command timed out after {timeout}s: {command}")
        return json.dumps({
            "success": False,
            "stdout": "",
            "stderr": f"Command timed out after {timeout} seconds",
            "return_code": -1,
        })
    except Exception as e:
        logger.error(f"Command execution failed: {e}")
        return json.dumps({
            "success": False,
            "stdout": "",
            "stderr": str(e),
            "return_code": -1,
        })


def _web_search(
    query: str,
    num_results: int = 5,
) -> str:
    """
    Search the web using DuckDuckGo and return relevant results.

    Args:
        query: The search query
        num_results: Number of results to return (default: 5)
    """
    logger.info(f"web_search called: {query}")
    try:
        from duckduckgo_search import DDGS

        with DDGS() as ddgs:
            raw_results = list(ddgs.text(keywords=query, max_results=num_results))

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


def _web_fetch(
    url: str,
    extract_text: bool = True,
    max_length: int = 10000,
) -> str:
    """
    Fetch content from a URL.

    Args:
        url: The URL to fetch
        extract_text: Whether to extract clean text (default: True)
        max_length: Maximum content length to return
    """
    logger.info(f"web_fetch called: {url}")
    try:
        import requests
        from bs4 import BeautifulSoup

        # Add headers to avoid being blocked
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        content_type = response.headers.get('content-type', 'unknown')

        if extract_text and 'text/html' in content_type:
            # Parse HTML and extract text
            soup = BeautifulSoup(response.text, 'html.parser')

            # Remove script and style elements
            for script in soup(['script', 'style', 'nav', 'footer', 'header']):
                script.decompose()

            # Get text and clean it up
            text = soup.get_text()
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = '\n'.join(chunk for chunk in chunks if chunk)

            content = text[:max_length] if len(text) > max_length else text
        else:
            # Return raw content
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


def register_system_tools() -> None:
    """Register system tools with the global tool manager."""

    tool_manager.register_tool(
        name="execute_command",
        description=(
            "Execute a shell command and return its output. "
            "Use this for running scripts, building projects, or system operations."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute (e.g., 'ls -la', 'python script.py')"
                },
                "working_dir": {
                    "type": "string",
                    "description": "Optional working directory. Defaults to outputs/"
                },
                "timeout": {
                    "type": "integer",
                    "description": "Command timeout in seconds (default: 30)"
                }
            },
            "required": ["command"]
        },
        fn=_execute_command,
        auto_inject_context=False
    )

    tool_manager.register_tool(
        name="web_search",
        description=(
            "Search the web for information using DuckDuckGo. "
            "Returns a list of relevant results with titles, URLs, and snippets."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query"
                },
                "num_results": {
                    "type": "integer",
                    "description": "Number of results to return (default: 5)"
                }
            },
            "required": ["query"]
        },
        fn=_web_search,
        auto_inject_context=False
    )

    tool_manager.register_tool(
        name="web_fetch",
        description=(
            "Fetch content from a URL. "
            "Returns the page content, optionally extracted as clean text."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch (must include http:// or https://)"
                },
                "extract_text": {
                    "type": "boolean",
                    "description": "Whether to extract clean text from HTML (default: true)"
                },
                "max_length": {
                    "type": "integer",
                    "description": "Maximum content length to return (default: 10000)"
                }
            },
            "required": ["url"]
        },
        fn=_web_fetch,
        auto_inject_context=False
    )

    logger.info("System tools registered: execute_command, web_search, web_fetch")
