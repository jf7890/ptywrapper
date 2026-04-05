from __future__ import annotations

import argparse
import os
import sys

from .config import AppConfig, default_config_text, has_runtime_overrides, load_config, persist_config
from .chat_client import build_debug_printer, build_status_printer, run_chat_turn
from .logging_utils import configure_logging
from .mock_endpoint import run_mock_endpoint
from .repl import run_repl
from .telemetry import TelemetryClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cyber-shell",
        description="Local PTY shell wrapper for cyber range telemetry.",
    )
    parser.add_argument(
        "--config",
        help="Global config path shortcut for default start command.",
    )
    parser.add_argument(
        "--endpoint-url",
        help="Override telemetry endpoint for the wrapped shell session.",
    )
    parser.add_argument(
        "--api-key",
        help="Override telemetry API key for the wrapped shell session.",
    )
    parser.add_argument(
        "--burp-mcp-url",
        help="Override the local Burp Suite MCP HTTP URL.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug output for chat, MCP, and transport flows.",
    )

    subparsers = parser.add_subparsers(dest="command")

    start_parser = subparsers.add_parser("start", help="Start wrapped Bash session.")
    start_parser.add_argument(
        "--config",
        help="Path to config.yaml. Defaults to ~/.config/cyber-shell/config.yaml",
    )
    start_parser.add_argument(
        "--endpoint-url",
        help="Override telemetry endpoint for this session.",
    )
    start_parser.add_argument(
        "--api-key",
        help="Override telemetry API key for this session.",
    )
    start_parser.add_argument(
        "--burp-mcp-url",
        help="Override the local Burp Suite MCP HTTP URL for this session.",
    )
    start_parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug output.",
    )

    mock_parser = subparsers.add_parser(
        "mock-endpoint",
        help="Run a local mock telemetry endpoint.",
    )
    mock_parser.add_argument("--host", default="127.0.0.1")
    mock_parser.add_argument("--port", type=int, default=8080)
    mock_parser.add_argument("--api-key")

    ask_parser = subparsers.add_parser(
        "ask",
        help="Ask the backend AI to analyze terminal telemetry and local Burp MCP data.",
    )
    ask_parser.add_argument("prompt", help="Prompt to send to the backend AI chat.")
    ask_parser.add_argument(
        "--burp-mcp-url",
        help="Override the local Burp Suite MCP HTTP URL for this request.",
    )
    ask_parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug output.",
    )

    repl_parser = subparsers.add_parser(
        "repl",
        help="Start an interactive AI chat session (no shell wrapping).",
    )
    repl_parser.add_argument("--endpoint-url", help="Override telemetry endpoint.")
    repl_parser.add_argument("--api-key", help="Override telemetry API key.")
    repl_parser.add_argument(
        "--burp-mcp-url",
        help="Override the local Burp Suite MCP HTTP URL for this session.",
    )
    repl_parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug output.",
    )

    subparsers.add_parser(
        "print-default-config",
        help="Print a default config.yaml template.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "start"

    if command == "print-default-config":
        sys.stdout.write(default_config_text())
        return 0

    if command == "mock-endpoint":
        return run_mock_endpoint(args.host, args.port, args.api_key)

    config_path = getattr(args, "config", None)
    config = load_config(config_path)
    if getattr(args, "endpoint_url", None):
        config.endpoint_url = args.endpoint_url
    if getattr(args, "api_key", None):
        config.api_key = args.api_key
    if getattr(args, "burp_mcp_url", None):
        config.burp_mcp_url = args.burp_mcp_url
    if getattr(args, "debug", False):
        config.debug = True
    logger = configure_logging(config.state_dir, debug=config.debug)

    if command == "ask":
        return _run_ask(args.prompt, config)

    if command == "repl":
        telemetry = TelemetryClient(config, logger)
        try:
            return run_repl(config, telemetry, logger)
        finally:
            telemetry.close()

    if command == "start":
        if os.name == "nt":
            print(
                "cyber-shell: start is only supported on POSIX/Linux. On Windows, use PowerShell and run `cyber-shell ask \"...\"` for chat and Burp MCP interaction.",
                file=sys.stderr,
            )
            return 1
        if has_runtime_overrides(
            {
                "endpoint_url": getattr(args, "endpoint_url", None),
                "api_key": getattr(args, "api_key", None),
                "burp_mcp_url": getattr(args, "burp_mcp_url", None),
            }
        ):
            persisted_path = persist_config(config)
            print(
                f"cyber-shell: updated config -> {persisted_path}",
                file=sys.stderr,
            )
        if config.endpoint_url:
            print(
                f"cyber-shell: telemetry -> {config.endpoint_url}",
                file=sys.stderr,
            )
        else:
            print(
                "cyber-shell: telemetry disabled; set endpoint_url in config, "
                "export CYBER_SHELL_ENDPOINT_URL, or pass --endpoint-url",
                file=sys.stderr,
            )
        if config.endpoint_url and not config.api_key:
            print(
                "cyber-shell: telemetry has no API key; pass --api-key or "
                "export CYBER_SHELL_API_KEY if the endpoint requires auth",
                file=sys.stderr,
            )
        if config.burp_mcp_url:
            print(
                f"cyber-shell: burp mcp -> {config.burp_mcp_url}",
                file=sys.stderr,
            )
        if config.debug:
            print("cyber-shell: debug -> enabled", file=sys.stderr)
        from .shell_wrapper import ShellWrapper

        telemetry = TelemetryClient(config, logger)
        try:
            wrapper = ShellWrapper(config, telemetry, logger)
            return wrapper.run()
        finally:
            telemetry.close()

    parser.print_help()
    return 1


def _run_ask(prompt: str, config: AppConfig) -> int:
    session_id = os.environ.get("CYBER_SHELL_SESSION_ID", "").strip()
    if not session_id and os.name != "nt":
        print(
            "cyber-shell: no active wrapped shell session detected; continuing without terminal history context",
            file=sys.stderr,
        )

    try:
        if config.debug:
            print("cyber-shell: debug -> enabled", file=sys.stderr)
        response = run_chat_turn(
            config,
            message=prompt,
            session_id=session_id or None,
            status_callback=build_status_printer(),
            debug_callback=build_debug_printer(config.debug),
        )
    except RuntimeError as exc:
        print(f"cyber-shell: {exc}", file=sys.stderr)
        return 1

    answer = str(response.get("answer") or "").rstrip("\n")
    if not answer and response.get("status") != "completed":
        print("cyber-shell: backend returned no answer", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
