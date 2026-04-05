from __future__ import annotations

import json
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

from .config import AppConfig
from .markdown_terminal import TerminalMarkdownRenderer
from .mcp_client import query_local_mcp


StatusCallback = Callable[[str], None]


def run_chat_turn(
    config: AppConfig,
    *,
    message: str | None = None,
    session_id: str | None = None,
    conversation_id: int | None = None,
    status_callback: StatusCallback | None = None,
    debug_callback: StatusCallback | None = None,
    renderer: TerminalMarkdownRenderer | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {}
    if message:
        payload["message"] = message
    if session_id:
        payload["session_id"] = session_id
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    _emit_debug(debug_callback, "chat.turn.request", payload)

    response = _stream_chat(config, payload, renderer=renderer, debug_callback=debug_callback)

    while response.get("status") == "requires_local_action":
        action = response.get("action") or {}
        tool = str(action.get("tool") or "").strip()
        tool_args = action.get("args") if isinstance(action.get("args"), dict) else {}
        response_conversation_id = response.get("conversation_id")
        _emit_debug(
            debug_callback,
            "chat.turn.local_action",
            {
                "conversation_id": response_conversation_id,
                "tool": tool,
                "args": tool_args,
            },
        )

        if tool != "query_local_mcp":
            raise RuntimeError(f"unsupported local tool requested: {tool}")

        if status_callback is not None:
            status_callback("[+] Fetching data from local Burp Suite...")

        tool_result = query_local_mcp(
            str(tool_args.get("query") or ""),
            base_url=config.burp_mcp_url,
            debug_callback=debug_callback,
        )
        _emit_debug(
            debug_callback,
            "chat.turn.local_result",
            {
                "tool": tool,
                "tool_result": tool_result,
            },
        )
        response = _stream_chat(
            config,
            {
                "session_id": session_id,
                "conversation_id": response_conversation_id,
                "tool_name": tool,
                "tool_result": tool_result,
            },
            renderer=renderer,
            debug_callback=debug_callback,
        )

    _emit_debug(debug_callback, "chat.turn.completed", response)
    return response


def build_status_printer(stream=None) -> StatusCallback:
    output_stream = stream or sys.stderr

    def emit(message: str) -> None:
        print(message, file=output_stream)

    return emit


def build_debug_printer(enabled: bool, stream=None) -> StatusCallback | None:
    if not enabled:
        return None

    output_stream = stream or sys.stderr

    def emit(message: str) -> None:
        print(f"[debug] {message}", file=output_stream)

    return emit


def _stream_chat(
    config: AppConfig,
    payload: dict[str, object],
    *,
    renderer: TerminalMarkdownRenderer | None = None,
    debug_callback: StatusCallback | None = None,
) -> dict[str, object]:
    chat_url = _chat_url_from_endpoint(config.endpoint_url)
    body = json.dumps({**payload, "stream": True}).encode("utf-8")
    request = urllib.request.Request(
        chat_url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {config.api_key or ''}",
        },
    )
    timeout = max(config.chat_timeout_ms / 1000.0, 0.1)
    markdown_renderer = renderer or TerminalMarkdownRenderer()
    current_event = "message"
    current_data_lines: list[str] = []
    state: dict[str, object] = {}
    _emit_debug(
        debug_callback,
        "chat.stream.open",
        {
            "url": chat_url,
            "payload": {**payload, "stream": True},
            "timeout_seconds": timeout,
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line:
                    _handle_sse_event(
                        current_event,
                        current_data_lines,
                        state,
                        markdown_renderer,
                        debug_callback=debug_callback,
                    )
                    current_event = "message"
                    current_data_lines = []
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    current_event = line[6:].strip() or "message"
                    continue
                if line.startswith("data:"):
                    current_data_lines.append(line[5:].lstrip())
            if current_data_lines:
                _handle_sse_event(
                    current_event,
                    current_data_lines,
                    state,
                    markdown_renderer,
                    debug_callback=debug_callback,
                )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"chat request failed with HTTP {exc.code}: {_format_error_body(detail)}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"could not reach backend chat API: {exc.reason}") from exc
    except socket.timeout as exc:
        raise RuntimeError(
            f"chat request timed out after {int(timeout)}s; increase chat_timeout_ms or retry"
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"could not reach backend chat API: {exc}") from exc

    markdown_renderer.finalize()
    if state.get("printed_output"):
        sys.stdout.write("\n")
        sys.stdout.flush()
    return state


def _handle_sse_event(
    event: str,
    data_lines: list[str],
    state: dict[str, object],
    renderer: TerminalMarkdownRenderer,
    *,
    debug_callback: StatusCallback | None = None,
) -> None:
    if not data_lines:
        return
    try:
        payload = json.loads("\n".join(data_lines))
    except json.JSONDecodeError:
        return

    if not isinstance(payload, dict):
        return
    _emit_debug(
        debug_callback,
        f"chat.stream.event.{event}",
        payload,
    )

    if payload.get("conversation_id") is not None:
        state["conversation_id"] = payload.get("conversation_id")

    if event == "delta":
        text = str(payload.get("text") or "")
        if text:
            renderer.feed(text)
            state["printed_output"] = True
        return

    if event == "requires_local_action":
        state["status"] = "requires_local_action"
        state["action"] = payload.get("action") or {}
        return

    if event == "completed":
        state.update(payload)
        state["status"] = payload.get("status") or "completed"
        return

    if event == "error":
        raise RuntimeError(str(payload.get("error") or "streaming chat failed"))


def _emit_debug(
    debug_callback: StatusCallback | None,
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


def _format_error_body(detail: str) -> str:
    if not detail:
        return "no details"
    sse_error = _parse_sse_error_body(detail)
    if sse_error:
        return sse_error
    try:
        parsed = json.loads(detail)
    except json.JSONDecodeError:
        condensed = " ".join(detail.split())
        return condensed[:280] + ("..." if len(condensed) > 280 else "")

    if isinstance(parsed, dict):
        if parsed.get("error"):
            return str(parsed["error"])
        condensed = json.dumps(parsed, ensure_ascii=False)
        return condensed[:280] + ("..." if len(condensed) > 280 else "")
    return str(parsed)


def _parse_sse_error_body(detail: str) -> str | None:
    data_lines: list[str] = []
    for raw_line in detail.splitlines():
        line = raw_line.strip()
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())
    if not data_lines:
        return None
    joined = "\n".join(data_lines)
    try:
        parsed = json.loads(joined)
    except json.JSONDecodeError:
        return joined
    if isinstance(parsed, dict) and parsed.get("error"):
        return str(parsed["error"])
    return json.dumps(parsed, ensure_ascii=False)


def _chat_url_from_endpoint(endpoint_url: str | None) -> str:
    if not endpoint_url:
        raise RuntimeError("backend endpoint_url is not configured")

    parsed = urllib.parse.urlparse(endpoint_url)
    if not parsed.scheme or not parsed.netloc:
        raise RuntimeError("endpoint_url is invalid")

    path = parsed.path or ""
    if path.endswith("/api/terminal-events"):
        chat_path = path[: -len("/api/terminal-events")] + "/api/chat"
    else:
        chat_path = "/api/chat"

    rebuilt = parsed._replace(path=chat_path, params="", query="", fragment="")
    return urllib.parse.urlunparse(rebuilt)
