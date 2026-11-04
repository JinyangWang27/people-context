"""A Python whose sqlite3 cannot load extensions gets the semantic refusal, not an AttributeError."""

from __future__ import annotations

from typing import Any, cast

import pytest

from people_context.adapters.sqlite.semantic import SqliteVecNotAvailableError, _load_sqlite_vec


def test_a_connection_without_extension_loading_is_refused_cleanly() -> None:
    pytest.importorskip("sqlite_vec")

    # The python.org macOS build omits `enable_load_extension`; the old `finally` called it anyway.
    with pytest.raises(SqliteVecNotAvailableError, match="cannot load extensions"):
        _load_sqlite_vec(cast(Any, object()))
