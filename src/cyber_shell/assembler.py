from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from .config import AppConfig
from .models import ShellEvent


INTERACTIVE_COMMANDS = {
    "alsamixer",
    "ftp",
    "htop",
    "less",
    "man",
    "more",
    "mongo",
    "mysql",
    "nano",
    "nmtui",
    "psql",
    "python",
    "python3",
    "redis-cli",
    "sftp",
    "ssh",
    "sqlite3",
    "telnet",
    "tig",
    "tmux",
    "top",
    "vi",
    "view",
    "vim",
    "watch",
}

PREFIX_WRAPPERS = {
    "builtin",
    "chronic",
    "command",
    "env",
    "exec",
    "nohup",
    "stdbuf",
    "time",
}

ANSI_ESCAPE_RE = re.compile(
    r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x1b\x07]*(?:\x07|\x1b\\))"
)


@dataclass(slots=True)
class ActiveCommand:
    started_at: str
    cmd: str
    output_buffer: bytearray
    truncated: bool = False


class EventAssembler:
    def __init__(self, config: AppConfig, session_id: str) -> None:
        self._config = config
        self._session_id = session_id
        self._seq = 0
        self._current: ActiveCommand | None = None

    def start_command(self, started_at: str, cmd: str) -> None:
        self._current = ActiveCommand(
            started_at=started_at,
            cmd=cmd.strip(),
            output_buffer=bytearray(),
        )

    def append_output(self, chunk: bytes) -> None:
        if self._current is None or not chunk:
            return
        remaining = self._config.max_output_bytes - len(self._current.output_buffer)
        if remaining <= 0:
            self._current.truncated = True
            return
        self._current.output_buffer.extend(chunk[:remaining])
        if len(chunk) > remaining:
            self._current.truncated = True

    def finish_command(
        self,
        *,
        finished_at: str,
        exit_code: int,
        cwd: str,
    ) -> ShellEvent | None:
        current = self._current
        self._current = None
        if current is None or not current.cmd:
            return None

        self._seq += 1
        return ShellEvent(
            session_id=self._session_id,
            user_id=self._config.user_id,
            lab_id=self._config.lab_id,
            target_id=self._config.target_id,
            hostname=self._config.hostname,
            shell="bash",
            seq=self._seq,
            cwd=cwd,
            cmd=current.cmd,
            exit_code=exit_code,
            output=_sanitize_output(
                current.output_buffer.decode("utf-8", errors="replace")
            ),
            output_truncated=current.truncated,
            started_at=current.started_at,
            finished_at=finished_at,
            is_interactive=is_interactive_command(current.cmd),
            metadata=dict(self._config.metadata),
        )


def is_interactive_command(command: str) -> bool:
    executable = _extract_command_name(command)
    if executable is None:
        return False
    return executable in INTERACTIVE_COMMANDS


def _extract_command_name(command: str) -> str | None:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None

    index = 0
    while index < len(tokens):
        token = tokens[index]
        if _looks_like_env_assignment(token):
            index += 1
            continue
        if token == "sudo":
            index += 1
            while index < len(tokens):
                sudo_token = tokens[index]
                if sudo_token == "--":
                    index += 1
                    break
                if not sudo_token.startswith("-"):
                    break
                index += 1
                if sudo_token in {"-g", "-h", "-p", "-u"} and index < len(tokens):
                    index += 1
            continue
        if token == "env":
            index += 1
            while index < len(tokens) and _looks_like_env_assignment(tokens[index]):
                index += 1
            continue
        if token in PREFIX_WRAPPERS:
            index += 1
            continue
        return token
    return None


def _looks_like_env_assignment(token: str) -> bool:
    if "=" not in token or token.startswith("="):
        return False
    name, _ = token.split("=", 1)
    return name.replace("_", "A").isalnum()


def _sanitize_output(value: str) -> str:
    cleaned = ANSI_ESCAPE_RE.sub("", value)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    return cleaned
