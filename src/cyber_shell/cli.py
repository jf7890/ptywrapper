from __future__ import annotations

import argparse
import sys

from .config import default_config_text, has_runtime_overrides, load_config, persist_config
from .logging_utils import configure_logging
from .mock_endpoint import run_mock_endpoint
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

    mock_parser = subparsers.add_parser(
        "mock-endpoint",
        help="Run a local mock telemetry endpoint.",
    )
    mock_parser.add_argument("--host", default="127.0.0.1")
    mock_parser.add_argument("--port", type=int, default=8080)
    mock_parser.add_argument("--api-key")

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
    logger = configure_logging(config.state_dir)

    if command == "start":
        if has_runtime_overrides(
            {
                "endpoint_url": getattr(args, "endpoint_url", None),
                "api_key": getattr(args, "api_key", None),
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
        from .shell_wrapper import ShellWrapper

        telemetry = TelemetryClient(config, logger)
        try:
            wrapper = ShellWrapper(config, telemetry, logger)
            return wrapper.run()
        finally:
            telemetry.close()

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
