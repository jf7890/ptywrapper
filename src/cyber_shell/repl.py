from __future__ import annotations

import atexit
import os
import sys
from pathlib import Path

from .chat_client import build_debug_printer, build_status_printer, run_chat_turn
from .markdown_terminal import TerminalMarkdownRenderer


ANSI_RESET = "\033[0m"
ANSI_CYAN = "\033[36m"
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_RED = "\033[31m"
ANSI_DIM = "\033[2m"
ANSI_BOLD = "\033[1m"


def run_repl(config, telemetry, logger) -> int:
    _enable_windows_ansi()
    _configure_line_editing(config.state_dir)

    current_conversation_id = None
    session_id = os.environ.get("CYBER_SHELL_SESSION_ID", "standalone-repl")
    status_printer = build_status_printer()
    debug_printer = build_debug_printer(config.debug)

    print(f"{ANSI_CYAN}{ANSI_BOLD}cyber-shell repl{ANSI_RESET}")
    print(f"{ANSI_DIM}Type `exit` or `quit` to leave. Arrow-key editing/history works when supported by your terminal.{ANSI_RESET}")
    if config.debug:
        print(f"{ANSI_YELLOW}[debug]{ANSI_RESET} verbose debug output enabled.", file=sys.stderr)

    while True:
        try:
            prompt = input(_prompt_text())
        except EOFError:
            print()
            break
        except KeyboardInterrupt:
            print()
            break

        user_text = prompt.strip()
        if not user_text:
            continue
        if user_text.lower() in {"exit", "quit"}:
            break

        try:
            print(f"{ANSI_GREEN}{ANSI_BOLD}assistant{ANSI_RESET}")
            response = run_chat_turn(
                config,
                message=user_text,
                session_id=session_id,
                conversation_id=current_conversation_id,
                status_callback=lambda message: status_printer(f"{ANSI_CYAN}[⚡] {message[4:] if message.startswith('[+] ') else message}{ANSI_RESET}"),
                debug_callback=debug_printer,
                renderer=TerminalMarkdownRenderer(),
            )
            current_conversation_id = response.get("conversation_id") or current_conversation_id
            if not response.get("answer") and response.get("status") != "completed":
                print(f"{ANSI_YELLOW}cyber-shell: backend returned no answer{ANSI_RESET}", file=sys.stderr)
            print()
        except RuntimeError as exc:
            logger.warning("REPL chat error: %s", exc)
            print(f"{ANSI_RED}error{ANSI_RESET}: {exc}", file=sys.stderr)
            print()

    return 0


def _enable_windows_ansi() -> None:
    if os.name == "nt":
        os.system("")


def _prompt_text() -> str:
    return f"{ANSI_CYAN}{ANSI_BOLD}you{ANSI_RESET} {ANSI_CYAN}❯{ANSI_RESET} "


def _configure_line_editing(state_dir: Path) -> None:
    try:
        import readline  # type: ignore
    except ImportError:
        return

    history_path = state_dir / "repl-history"
    try:
        readline.read_history_file(str(history_path))
    except FileNotFoundError:
        pass
    except OSError:
        return

    readline.set_history_length(500)

    def save_history() -> None:
        try:
            history_path.parent.mkdir(parents=True, exist_ok=True)
            readline.write_history_file(str(history_path))
        except OSError:
            return

    atexit.register(save_history)
