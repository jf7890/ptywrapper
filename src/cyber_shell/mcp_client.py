from __future__ import annotations

import json
import urllib.parse
import urllib.error
import urllib.request
from collections.abc import Callable


DEFAULT_MCP_URL = "http://127.0.0.1:3000"
DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_ITEMS = 15
MAX_TEXT_CHARS = 1000
DebugCallback = Callable[[str], None]


def query_local_mcp(
    query: str,
    base_url: str = DEFAULT_MCP_URL,
    debug_callback: DebugCallback | None = None,
) -> str:
    candidate_urls = _candidate_mcp_urls(base_url)
    last_error = "Could not connect to local Burp MCP. Is it running?"
    _emit_debug(
        debug_callback,
        "mcp.query.start",
        {
            "query": query,
            "base_url": base_url,
            "candidates": candidate_urls,
        },
    )
    for candidate_url in candidate_urls:
        try:
            client = BurpMcpClient(candidate_url, debug_callback=debug_callback)
            return client.query(query)
        except (urllib.error.URLError, TimeoutError, OSError, RuntimeError, ValueError) as exc:
            last_error = str(exc) or last_error
            _emit_debug(
                debug_callback,
                "mcp.query.candidate_error",
                {
                    "candidate_url": candidate_url,
                    "error": last_error,
                },
            )

    return json.dumps(
        {
            "error": "Could not connect to local Burp MCP. Is it running?",
            "details": last_error,
            "candidates": candidate_urls,
        }
    )

 
class BurpMcpClient:
    def __init__(self, base_url: str, debug_callback: DebugCallback | None = None) -> None:
        self._base_url = _normalize_mcp_url(base_url)
        self._request_id = 0
        self._debug_callback = debug_callback
        self._sse_response = None
        self._post_url = self._discover_post_url()
        self._initialize()

    def query(self, query: str) -> str:
        tools = self._list_tools()
        named_tools = {
            str(tool.get("name") or ""): tool
            for tool in tools
            if isinstance(tool, dict) and tool.get("name")
        }
        history_regex_tool = _find_tool(
            tools,
            preferred_names=[
                "get_proxy_http_history_regex",
                "getProxyHttpHistoryRegex",
                "GetProxyHttpHistoryRegex",
            ],
            description_keywords=["proxy http history", "regex"],
        )
        history_tool = _find_tool(
            tools,
            preferred_names=[
                "get_proxy_http_history",
                "getProxyHttpHistory",
                "GetProxyHttpHistory",
                "get_proxy_history",
                "getProxyHistory",
                "GetProxyHistory",
            ],
            description_keywords=["proxy http history"],
        )
        scanner_tool = _find_tool(
            tools,
            preferred_names=[
                "get_scanner_issues",
                "getScannerIssues",
                "GetScannerIssues",
                "get_scan_issues",
                "getScanIssues",
                "GetScanIssues",
            ],
            description_keywords=["scanner", "issues"],
        )
        results: dict[str, object] = {
            "query": query,
            "transport_url": self._base_url,
            "post_url": self._post_url,
            "tools": sorted(name for name in named_tools if name),
            "results": {},
        }
        _emit_debug(
            self._debug_callback,
            "mcp.tools.list",
            tools,
        )
        _emit_debug(
            self._debug_callback,
            "mcp.tools.selected",
            {
                "history_regex_tool": history_regex_tool,
                "history_tool": history_tool,
                "scanner_tool": scanner_tool,
            },
        )

        normalized_query = (query or "").strip()
        if normalized_query and history_regex_tool is not None:
            regex_result = self._call_tool(
                history_regex_tool["name"],
                {"regex": normalized_query, "count": MAX_ITEMS, "offset": 0},
            )
            if _is_end_of_items_result(regex_result) and history_tool is not None:
                results["results"][history_regex_tool["name"]] = regex_result
                results["results"][history_tool["name"]] = self._call_tool(
                    history_tool["name"],
                    {"count": MAX_ITEMS, "offset": 0},
                )
            else:
                results["results"][history_regex_tool["name"]] = regex_result
        elif history_tool is not None:
            results["results"][history_tool["name"]] = self._call_tool(
                history_tool["name"],
                {"count": MAX_ITEMS, "offset": 0},
            )
        elif history_regex_tool is not None:
            results["results"][history_regex_tool["name"]] = self._call_tool(
                history_regex_tool["name"],
                {"regex": ".*", "count": MAX_ITEMS, "offset": 0},
            )

        if scanner_tool is not None:
            results["results"][scanner_tool["name"]] = self._call_tool(
                scanner_tool["name"],
                {"count": MAX_ITEMS, "offset": 0},
            )

        if not results["results"]:
            results["error"] = (
                "Connected to Burp MCP, but no supported history or scanner tools were exposed."
            )
            results["tool_details"] = [
                {
                    "name": tool.get("name"),
                    "description": tool.get("description"),
                }
                for tool in tools
                if isinstance(tool, dict)
            ]

        _emit_debug(self._debug_callback, "mcp.query.result.raw", results)
        return json.dumps(_truncate_payload(results), ensure_ascii=True)

    def _discover_post_url(self) -> str:
        try:
            post_url = self._open_legacy_sse_transport()
            _emit_debug(
                self._debug_callback,
                "mcp.transport.discovered_post_url",
                {
                    "base_url": self._base_url,
                    "post_url": post_url,
                },
            )
            return post_url
        except RuntimeError:
            _emit_debug(
                self._debug_callback,
                "mcp.transport.fallback_post_url",
                {
                    "base_url": self._base_url,
                    "post_url": self._base_url,
                },
            )
            return self._base_url

    def _open_legacy_sse_transport(self) -> str:
        request = urllib.request.Request(
            self._base_url,
            method="GET",
            headers={"Accept": "text/event-stream"},
        )
        try:
            self._sse_response = urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS)
            while True:
                event_name, event_data = self._read_sse_event()
                _emit_debug(
                    self._debug_callback,
                    "mcp.sse.event",
                    {
                        "event": event_name,
                        "data": event_data,
                    },
                )
                if event_name == "endpoint":
                    endpoint = str(event_data or "").strip()
                    if endpoint:
                        return urllib.parse.urljoin(self._base_url, endpoint)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"SSE discovery failed at {self._base_url}: HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"SSE discovery failed at {self._base_url}: {exc.reason}") from exc
        raise RuntimeError(f"SSE discovery at {self._base_url} did not provide an endpoint event")

    def _initialize(self) -> None:
        _emit_debug(
            self._debug_callback,
            "mcp.initialize",
            {
                "base_url": self._base_url,
                "post_url": self._post_url,
            },
        )
        self._rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "cyber-shell",
                    "version": "0.1.0",
                },
            },
        )
        self._notify(
            "notifications/initialized",
            {},
        )

    def _list_tools(self) -> list[dict[str, object]]:
        result = self._rpc("tools/list", {})
        tools = result.get("tools")
        return tools if isinstance(tools, list) else []

    def _call_tool(self, name: str, arguments: dict[str, object]) -> object:
        _emit_debug(
            self._debug_callback,
            "mcp.tool.call",
            {
                "name": name,
                "arguments": arguments,
            },
        )
        result = self._rpc(
            "tools/call",
            {
                "name": name,
                "arguments": arguments,
            },
        )
        _emit_debug(
            self._debug_callback,
            "mcp.tool.result",
            {
                "name": name,
                "result": result,
            },
        )
        return result.get("content", result)

    def _notify(self, method: str, params: dict[str, object]) -> None:
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self._post_url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        _emit_debug(
            self._debug_callback,
            "mcp.notify.request",
            {
                "url": self._post_url,
                "method": method,
                "params": params,
            },
        )
        with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            payload = response.read().decode("utf-8", errors="replace")
        _emit_debug(
            self._debug_callback,
            "mcp.notify.response_raw",
            {
                "method": method,
                "payload": payload,
            },
        )

    def _rpc(self, method: str, params: dict[str, object]) -> dict[str, object]:
        self._request_id += 1
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": method,
                "params": params,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self._post_url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        _emit_debug(
            self._debug_callback,
            "mcp.rpc.request",
            {
                "url": self._post_url,
                "method": method,
                "params": params,
                "request_id": self._request_id,
            },
        )
        with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            payload = response.read().decode("utf-8", errors="replace")
        _emit_debug(
            self._debug_callback,
            "mcp.rpc.response_raw",
            {
                "method": method,
                "payload": payload,
            },
        )

        parsed = _parse_json_or_sse_payload(payload)
        if (
            self._sse_response is not None
            and _looks_like_accepted_only_response(payload, parsed)
        ):
            parsed = self._wait_for_sse_rpc_response(self._request_id, method)
        _emit_debug(
            self._debug_callback,
            "mcp.rpc.response_parsed",
            {
                "method": method,
                "parsed": parsed,
            },
        )
        if not isinstance(parsed, dict):
            raise RuntimeError(f"Unexpected MCP response for {method}")
        if parsed.get("error"):
            raise RuntimeError(str(parsed["error"]))
        result = parsed.get("result")
        return result if isinstance(result, dict) else {}

    def _wait_for_sse_rpc_response(self, request_id: int, method: str) -> object:
        _emit_debug(
            self._debug_callback,
            "mcp.rpc.waiting_for_sse_response",
            {
                "request_id": request_id,
                "method": method,
            },
        )
        while True:
            event_name, event_data = self._read_sse_event()
            _emit_debug(
                self._debug_callback,
                "mcp.sse.event",
                {
                    "event": event_name,
                    "data": event_data,
                },
            )
            if event_name != "message":
                continue
            if isinstance(event_data, dict) and event_data.get("id") == request_id:
                return event_data

    def _read_sse_event(self) -> tuple[str, object]:
        if self._sse_response is None:
            raise RuntimeError("SSE transport is not open")

        event_name = "message"
        data_lines: list[str] = []
        for raw_line in self._sse_response:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                if not data_lines:
                    event_name = "message"
                    continue
                return event_name, _parse_sse_event_data(data_lines)
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_name = line[6:].strip() or "message"
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        raise RuntimeError("SSE stream closed before MCP response was received")


def _normalize_mcp_url(base_url: str) -> str:
    normalized = (base_url or DEFAULT_MCP_URL).strip()
    if not normalized:
        normalized = DEFAULT_MCP_URL
    if not normalized.startswith(("http://", "https://")):
        normalized = "http://" + normalized
    return normalized.rstrip("/")


def _candidate_mcp_urls(base_url: str) -> list[str]:
    normalized = _normalize_mcp_url(base_url)
    parsed = urllib.parse.urlparse(normalized)
    path = parsed.path.rstrip("/")

    candidates: list[str] = []
    if path.endswith("/sse"):
        candidates.append(normalized)
        root_url = urllib.parse.urlunparse(parsed._replace(path=path[: -len("/sse")] or ""))
        candidates.append(root_url.rstrip("/"))
    else:
        candidates.append(normalized)
        sse_url = urllib.parse.urlunparse(parsed._replace(path=(path + "/sse") or "/sse"))
        candidates.append(sse_url.rstrip("/"))

    deduped: list[str] = []
    for item in candidates:
        cleaned = item.rstrip("/")
        if cleaned not in deduped:
            deduped.append(cleaned)
    return deduped


def _parse_json_or_sse_payload(payload: str) -> object:
    text = (payload or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return _parse_sse_payload(text)


def _parse_sse_payload(payload: str) -> object:
    event_name = "message"
    data_lines: list[str] = []
    parsed_events: list[tuple[str, object]] = []

    for raw_line in payload.splitlines():
        line = raw_line.rstrip("\r")
        if not line:
            if data_lines:
                data_text = "\n".join(data_lines)
                try:
                    data = json.loads(data_text)
                except json.JSONDecodeError:
                    data = data_text
                parsed_events.append((event_name, data))
            event_name = "message"
            data_lines = []
            continue
        if line.startswith("event:"):
            event_name = line[6:].strip() or "message"
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())

    if data_lines:
        data_text = "\n".join(data_lines)
        try:
            data = json.loads(data_text)
        except json.JSONDecodeError:
            data = data_text
        parsed_events.append((event_name, data))

    for event_name, data in reversed(parsed_events):
        if event_name == "message":
            return data
    return parsed_events[-1][1] if parsed_events else {}


def _parse_sse_event_data(data_lines: list[str]) -> object:
    data_text = "\n".join(data_lines)
    try:
        return json.loads(data_text)
    except json.JSONDecodeError:
        return data_text


def _looks_like_accepted_only_response(payload: str, parsed: object) -> bool:
    text = (payload or "").strip()
    if not text:
        return True
    if isinstance(parsed, dict) and parsed:
        return False
    return text.lower() == "accepted"


def _find_tool(
    tools: list[dict[str, object]],
    *,
    preferred_names: list[str],
    description_keywords: list[str],
) -> dict[str, object] | None:
    normalized_name_map = {
        _normalize_tool_name(str(tool.get("name") or "")): tool
        for tool in tools
        if isinstance(tool, dict) and tool.get("name")
    }
    for candidate in preferred_names:
        matched = normalized_name_map.get(_normalize_tool_name(candidate))
        if matched is not None:
            return matched

    for tool in tools:
        if not isinstance(tool, dict):
            continue
        haystack = " ".join(
            [
                str(tool.get("name") or ""),
                str(tool.get("description") or ""),
            ]
        ).lower()
        if all(keyword.lower() in haystack for keyword in description_keywords):
            return tool
    return None


def _normalize_tool_name(name: str) -> str:
    return "".join(character.lower() for character in name if character.isalnum())


def _truncate_payload(value):
    if isinstance(value, dict):
        truncated: dict[str, object] = {}
        for key, inner in value.items():
            lowered = str(key).lower()
            if isinstance(inner, list):
                truncated[key] = [_truncate_payload(item) for item in inner[:MAX_ITEMS]]
                continue
            if any(token in lowered for token in ("body", "response", "content", "raw")):
                text = json.dumps(inner, ensure_ascii=False) if not isinstance(inner, str) else inner
                truncated[key] = _truncate_text(text)
                continue
            truncated[key] = _truncate_payload(inner)
        return truncated

    if isinstance(value, list):
        return [_truncate_payload(item) for item in value[:MAX_ITEMS]]

    if isinstance(value, str):
        return _truncate_text(value)

    return value


def _truncate_text(value: str) -> str:
    if len(value) <= MAX_TEXT_CHARS:
        return value
    return value[:MAX_TEXT_CHARS] + "...[truncated]"


def _is_end_of_items_result(value: object) -> bool:
    if isinstance(value, str):
        return "reached end of items" in value.lower()
    if isinstance(value, dict):
        return any(_is_end_of_items_result(inner) for inner in value.values())
    if isinstance(value, list):
        return any(_is_end_of_items_result(item) for item in value)
    return False


def _emit_debug(
    debug_callback: DebugCallback | None,
    label: str,
    payload: object,
) -> None:
    if debug_callback is None:
        return
    try:
        rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    except TypeError:
        rendered = repr(payload)
    debug_callback(f"{label}: {rendered}")
