"""A small but real line editor for the C2 interactive menu.

It is deliberately hand written rather than using :mod:`readline` because the
input arrives from an SSH channel as arbitrary bytes and we must keep full
control over ANSI output.  It supports the editing keys an operator expects:

* printable input, UTF-8 aware
* Enter, Backspace
* Ctrl+C (cancel line), Ctrl+D (EOF), Ctrl+L (clear screen)
* Ctrl+A / Ctrl+E (home/end), Ctrl+U / Ctrl+K (kill line), Ctrl+W (kill word)
* Left/Right/Home/End/Delete arrow keys
* Up/Down command history
* Tab completion (delegated to a callback)

The editor never blocks: :meth:`LineEditor.feed` takes a chunk of bytes and
returns the list of completed command lines found in it.
"""

from __future__ import annotations

import codecs
from typing import Callable

ESC = "\x1b"

# control characters
_CTRL_A = "\x01"
_CTRL_C = "\x03"
_CTRL_D = "\x04"
_CTRL_E = "\x05"
_CTRL_K = "\x0b"
_CTRL_L = "\x0c"
_CTRL_U = "\x15"
_CTRL_W = "\x17"
_BACKSPACE = "\x7f"
_BS = "\x08"
_CR = "\r"
_LF = "\n"
_TAB = "\t"

Completer = Callable[[str, int], list[str]]


class LineEditor:
    def __init__(self, prompt: str, emit: Callable[[str], None], completer: Completer | None = None):
        self.prompt = prompt
        self._emit = emit
        self._completer = completer
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.buffer: list[str] = []
        self.cursor = 0
        self.history: list[str] = []
        self._history_index: int | None = None
        self._saved_line: str = ""
        self.eof = False
        self.secret = False

        # escape parsing state
        self._esc: str | None = None

    # -- public api --------------------------------------------------------
    def set_prompt(self, prompt: str) -> None:
        self.prompt = prompt

    def reset(self) -> None:
        self.buffer = []
        self.cursor = 0
        self._history_index = None
        self._saved_line = ""
        self._esc = None

    def render_prompt(self) -> None:
        self._emit(self.prompt + self._visible_buffer())

    def feed(self, data: bytes) -> list[str]:
        text = self._decoder.decode(data)
        completed: list[str] = []
        for ch in text:
            self._feed_char(ch, completed)
        return completed

