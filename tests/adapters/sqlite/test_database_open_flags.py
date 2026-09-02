"""`O_NOFOLLOW` is POSIX-only, and referencing it directly breaks Windows outright.

This file carries no `win32` skip on purpose: the permission tests skip there, so a
bare `os.O_NOFOLLOW` would raise `AttributeError` for every filesystem-backed open on
Windows with nothing in the suite to notice.
"""

from __future__ import annotations

import os

from people_context.adapters.sqlite.db import _O_NOFOLLOW


def test_the_nofollow_flag_degrades_instead_of_raising() -> None:
    assert getattr(os, "O_NOFOLLOW", 0) == _O_NOFOLLOW


def test_the_flag_is_an_integer_the_open_can_always_combine() -> None:
    """`os.open` must receive a usable mask on every platform, present or not."""
    assert isinstance(_O_NOFOLLOW, int)
    combined = os.O_CREAT | os.O_EXCL | os.O_WRONLY | _O_NOFOLLOW
    assert combined & os.O_CREAT
    if not hasattr(os, "O_NOFOLLOW"):
        assert not _O_NOFOLLOW
