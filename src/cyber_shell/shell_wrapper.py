from __future__ import annotations

import atexit
import fcntl
import os
import selectors
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import tty
import uuid
from collections.abc import Callable
from pathlib import Path

from .assembler import EventAssembler
from .config import AppConfig
from .models import ShellEvent
from .rcfile import build_wrapper_rcfile
from .telemetry import TelemetryClient

CHILD_ENV_EXCLUDE_PREFIXES = ("CYBER_SHELL_",)
CHILD_ENV_ALLOWLIST = {
    "CYBER_SHELL_CONTROL_FD",
    "CYBER_SHELL_SESSION_ID",
    "CYBER_SHELL_STATE_DIR",
}


class ShellWrapper:
    def __init__(
        self,
        config: AppConfig,
        telemetry: TelemetryClient,
        logger,
    ) -> None:
        self._config = config
        self._telemetry = telemetry
        self._logger = logger
        self._session_id = f"sess-{uuid.uuid4().hex[:12]}"
        self._assembler = EventAssembler(config, self._session_id)
        self._control_buffer = bytearray()
        self._control_tokens: list[str] = []
        self._pending_post: tuple[str, int, str] | None = None

    def run(self) -> int:
        if os.name != "posix":
            raise RuntimeError("cyber-shell only supports POSIX/Linux environments.")
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise RuntimeError("cyber-shell must be started from an interactive terminal.")

        self._config.ensure_state_dir()

        with tempfile.TemporaryDirectory(
            prefix="cyber-shell-",
            dir=str(self._config.state_dir),
        ) as temp_dir:
            rcfile_path = Path(temp_dir) / "wrapper.bashrc"
            rcfile_path.write_text(build_wrapper_rcfile(), encoding="utf-8")
            return self._run_session(rcfile_path)

    def _run_session(self, rcfile_path: Path) -> int:
        master_fd, slave_fd = os.openpty()
        control_read_fd, control_write_fd = os.pipe()
        os.set_inheritable(control_write_fd, True)
        self._sync_window_size(sys.stdout.fileno(), slave_fd)

        process = subprocess.Popen(
            [
                self._config.shell_path,
                "--noprofile",
                "--rcfile",
                str(rcfile_path),
                "-i",
            ],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            env=self._build_environment(control_write_fd),
            pass_fds=(control_write_fd,),
            preexec_fn=self._build_child_setup(slave_fd),
            close_fds=True,
        )
        os.close(slave_fd)
        os.close(control_write_fd)

        selector = selectors.DefaultSelector()
        selector.register(sys.stdin.fileno(), selectors.EVENT_READ, "stdin")
        selector.register(master_fd, selectors.EVENT_READ, "pty")
        selector.register(control_read_fd, selectors.EVENT_READ, "control")

        stdin_fd = sys.stdin.fileno()
        stdout_fd = sys.stdout.fileno()
        original_tty_mode = termios.tcgetattr(stdin_fd)
        previous_sigwinch = signal.getsignal(signal.SIGWINCH)
        previous_sigterm = signal.getsignal(signal.SIGTERM)
        previous_sighup = signal.getsignal(signal.SIGHUP)

        def restore_terminal() -> None:
            try:
                termios.tcsetattr(stdin_fd, termios.TCSADRAIN, original_tty_mode)
            except termios.error:
                pass

        atexit.register(restore_terminal)

        def on_resize(signum, frame) -> None:  # noqa: ANN001
            self._sync_window_size(stdout_fd, master_fd)

        def on_terminate(signum, frame) -> None:  # noqa: ANN001
            try:
                os.killpg(process.pid, signum)
            except OSError:
                pass

        try:
            tty.setraw(stdin_fd)
            signal.signal(signal.SIGWINCH, on_resize)
            signal.signal(signal.SIGTERM, on_terminate)
            signal.signal(signal.SIGHUP, on_terminate)
            self._sync_window_size(stdout_fd, master_fd)

            while True:
                ready = selector.select(timeout=0.25)
                if not ready and process.poll() is not None:
                    break

                for key, _ in sorted(ready, key=_selector_priority):
                    if key.data == "control":
                        if not self._drain_control(control_read_fd):
                            selector.unregister(control_read_fd)
                            os.close(control_read_fd)
                    elif key.data == "pty":
                        if not self._drain_pty(master_fd, stdout_fd):
                            selector.unregister(master_fd)
                            os.close(master_fd)
                    elif key.data == "stdin":
                        if not self._forward_stdin(stdin_fd, master_fd):
                            selector.unregister(stdin_fd)

                self._flush_pending_post()

                if process.poll() is not None and master_fd not in selector.get_map():
                    break
        finally:
            selector.close()
            restore_terminal()
            atexit.unregister(restore_terminal)
            signal.signal(signal.SIGWINCH, previous_sigwinch)
            signal.signal(signal.SIGTERM, previous_sigterm)
            signal.signal(signal.SIGHUP, previous_sighup)
            for fd in (master_fd, control_read_fd):
                try:
                    os.close(fd)
                except OSError:
                    pass

        self._flush_pending_post()
        return process.wait()

    def _build_environment(self, control_fd: int) -> dict[str, str]:
        env = {
            key: value
            for key, value in os.environ.items()
            if not (
                key.startswith(CHILD_ENV_EXCLUDE_PREFIXES)
                and key not in CHILD_ENV_ALLOWLIST
            )
        }
        env["CYBER_SHELL_CONTROL_FD"] = str(control_fd)
        env["CYBER_SHELL_SESSION_ID"] = self._session_id
        env["CYBER_SHELL_STATE_DIR"] = str(self._config.state_dir)
        return env

    def _build_child_setup(self, slave_fd: int) -> Callable[[], None]:
        def child_setup() -> None:
            os.setsid()
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)

        return child_setup

    def _forward_stdin(self, stdin_fd: int, master_fd: int) -> bool:
        try:
            data = os.read(stdin_fd, 4096)
        except OSError:
            return False
        if not data:
            return False
        self._write_all(master_fd, data)
        return True

    def _drain_pty(self, master_fd: int, stdout_fd: int) -> bool:
        try:
            data = os.read(master_fd, 65536)
        except OSError:
            return False
        if not data:
            return False
        self._assembler.append_output(data)
        self._write_all(stdout_fd, data)
        return True

    def _drain_control(self, control_read_fd: int) -> bool:
        try:
            chunk = os.read(control_read_fd, 65536)
        except OSError:
            return False
        if not chunk:
            return False
        self._control_buffer.extend(chunk)
        while True:
            separator = self._control_buffer.find(b"\0")
            if separator < 0:
                break
            token = self._control_buffer[:separator].decode("utf-8", errors="replace")
            del self._control_buffer[: separator + 1]
            self._control_tokens.append(token)
            self._consume_control_messages()
        return True

    def _consume_control_messages(self) -> None:
        while self._control_tokens:
            event_type = self._control_tokens[0]
            if event_type == "PRE":
                if len(self._control_tokens) < 3:
                    return
                self._flush_pending_post()
                _, started_at, cmd = self._control_tokens[:3]
                del self._control_tokens[:3]
                self._assembler.start_command(started_at, cmd)
                continue
            if event_type == "POST":
                if len(self._control_tokens) < 4:
                    return
                _, finished_at, exit_code, cwd = self._control_tokens[:4]
                del self._control_tokens[:4]
                self._pending_post = (
                    finished_at,
                    _safe_int(exit_code),
                    cwd,
                )
                continue
            self._logger.warning("Unknown control token prefix: %s", event_type)
            self._control_tokens.pop(0)

    def _flush_pending_post(self) -> None:
        if self._pending_post is None:
            return
        finished_at, exit_code, cwd = self._pending_post
        self._pending_post = None
        event = self._assembler.finish_command(
            finished_at=finished_at,
            exit_code=exit_code,
            cwd=cwd,
        )
        if event is not None:
            self._emit_event(event)

    def _emit_event(self, event: ShellEvent) -> None:
        self._telemetry.enqueue(event)

    def _sync_window_size(self, terminal_fd: int, pty_fd: int) -> None:
        try:
            packed = fcntl.ioctl(terminal_fd, termios.TIOCGWINSZ, b"\0" * 8)
            rows, cols, x_pixels, y_pixels = struct.unpack("HHHH", packed)
            fcntl.ioctl(
                pty_fd,
                termios.TIOCSWINSZ,
                struct.pack("HHHH", rows, cols, x_pixels, y_pixels),
            )
        except OSError:
            return

    def _write_all(self, fd: int, payload: bytes) -> None:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            view = view[written:]


def _selector_priority(event) -> int:
    priorities = {"control": 0, "pty": 1, "stdin": 2}
    return priorities.get(event[0].data, 10)


def _safe_int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 1
