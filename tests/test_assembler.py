from __future__ import annotations

import unittest

from cyber_shell.assembler import EventAssembler, is_interactive_command
from cyber_shell.config import AppConfig


class EventAssemblerTests(unittest.TestCase):
    def test_finish_command_builds_event(self) -> None:
        config = AppConfig(max_output_bytes=8)
        assembler = EventAssembler(config, "sess-123")

        assembler.start_command("2026-03-21T08:31:01Z", "nmap -sV 10.10.10.5")
        assembler.append_output(b"abcdef")
        assembler.append_output(b"ghijkl")
        event = assembler.finish_command(
            finished_at="2026-03-21T08:31:03Z",
            exit_code=127,
            cwd="/tmp",
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.seq, 1)
        self.assertEqual(event.cwd, "/tmp")
        self.assertEqual(event.exit_code, 127)
        self.assertEqual(event.output, "abcdefgh")
        self.assertTrue(event.output_truncated)

    def test_blank_command_is_ignored(self) -> None:
        assembler = EventAssembler(AppConfig(), "sess-123")
        assembler.start_command("2026-03-21T08:31:01Z", "   ")
        event = assembler.finish_command(
            finished_at="2026-03-21T08:31:03Z",
            exit_code=0,
            cwd="/tmp",
        )
        self.assertIsNone(event)


class InteractiveCommandTests(unittest.TestCase):
    def test_detects_wrapped_interactive_commands(self) -> None:
        self.assertTrue(is_interactive_command("sudo -u kali vim test.txt"))
        self.assertTrue(is_interactive_command("env TERM=xterm less /etc/passwd"))
        self.assertFalse(is_interactive_command("printf 'hello'"))

    def test_strips_ansi_sequences_from_output(self) -> None:
        assembler = EventAssembler(AppConfig(), "sess-123")
        assembler.start_command("2026-03-21T08:31:01Z", "ls")
        assembler.append_output(b"\x1b[01;34msrc\x1b[0m\r\n")
        event = assembler.finish_command(
            finished_at="2026-03-21T08:31:03Z",
            exit_code=0,
            cwd="/tmp",
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.output, "src\n")


if __name__ == "__main__":
    unittest.main()
