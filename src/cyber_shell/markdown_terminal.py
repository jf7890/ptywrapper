from __future__ import annotations

import re
import sys


ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_ITALIC = "\033[3m"
ANSI_DIM = "\033[2m"
ANSI_CYAN = "\033[36m"
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_BLUE = "\033[34m"
ANSI_MAGENTA = "\033[35m"

INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")
ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
NUMBERED_RE = re.compile(r"^(\s*)(\d+)\.\s+")


class TerminalMarkdownRenderer:
    def __init__(self, stream=None) -> None:
        self._stream = stream or sys.stdout
        self._buffer = ""
        self._in_code_block = False

    def feed(self, text: str) -> None:
        if not text:
            return
        self._buffer += text
        while True:
            newline_index = self._buffer.find("\n")
            if newline_index < 0:
                break
            line = self._buffer[:newline_index]
            self._buffer = self._buffer[newline_index + 1 :]
            self._write_line(line)

    def finalize(self) -> None:
        if self._buffer:
            self._write_line(self._buffer)
            self._buffer = ""

    def _write_line(self, line: str) -> None:
        stripped = line.rstrip("\r")
        if stripped.startswith("```"):
            self._in_code_block = not self._in_code_block
            lang = stripped[3:].strip()
            label = f"```{lang}" if lang else "```"
            self._stream.write(f"{ANSI_DIM}{label}{ANSI_RESET}\n")
            self._stream.flush()
            return

        if self._in_code_block:
            self._stream.write(f"{ANSI_GREEN}{stripped}{ANSI_RESET}\n")
            self._stream.flush()
            return

        rendered = _render_inline(stripped)
        if stripped.startswith("# "):
            rendered = f"{ANSI_BOLD}{ANSI_BLUE}{rendered[2:]}{ANSI_RESET}"
        elif stripped.startswith("## "):
            rendered = f"{ANSI_BOLD}{ANSI_BLUE}{rendered[3:]}{ANSI_RESET}"
        elif stripped.startswith("### "):
            rendered = f"{ANSI_BOLD}{ANSI_BLUE}{rendered[4:]}{ANSI_RESET}"
        elif stripped.startswith(">"):
            rendered = f"{ANSI_DIM}{rendered}{ANSI_RESET}"
        elif re.match(r"^\s*[-*]\s+", stripped):
            rendered = re.sub(r"^(\s*)[-*]\s+", rf"\1{ANSI_YELLOW}•{ANSI_RESET} ", rendered, count=1)
        elif NUMBERED_RE.match(stripped):
            rendered = NUMBERED_RE.sub(rf"\1{ANSI_YELLOW}\2.{ANSI_RESET} ", rendered, count=1)
        elif re.match(r"^\s*[-=]{3,}\s*$", stripped):
            rendered = f"{ANSI_DIM}{'─' * min(max(len(stripped), 12), 72)}{ANSI_RESET}"

        self._stream.write(rendered + "\n")
        self._stream.flush()


def _render_inline(text: str) -> str:
    rendered = text
    rendered = LINK_RE.sub(
        lambda match: f"{ANSI_BLUE}{match.group(1)}{ANSI_RESET} {ANSI_DIM}<{match.group(2)}>{ANSI_RESET}",
        rendered,
    )
    rendered = INLINE_CODE_RE.sub(lambda match: f"{ANSI_CYAN}`{match.group(1)}`{ANSI_RESET}", rendered)
    rendered = BOLD_RE.sub(lambda match: f"{ANSI_BOLD}{match.group(1)}{ANSI_RESET}", rendered)
    rendered = ITALIC_RE.sub(lambda match: f"{ANSI_ITALIC}{match.group(1)}{ANSI_RESET}", rendered)
    return rendered
