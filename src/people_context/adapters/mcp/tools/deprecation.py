"""Deprecation notices for MCP tools scheduled for removal in the next major version.

A deprecated tool keeps working unchanged: same parameters, same responses, same annotations. The notice reaches
two audiences. The model reads it at the front of the tool description, so it can choose the replacement before
calling; the operator reads it once per process on stderr, the first time a client calls the tool. The log line
names only the tool, never an argument, because arguments carry personal data.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable
from typing import ParamSpec, TypeVar

__all__ = ["DEPRECATION_ISSUE_URL", "deprecated_tool"]

#: The tracking issue every notice points to, so the plan and its prerequisites live in one place.
DEPRECATION_ISSUE_URL = "https://github.com/JinyangWang27/people-context/issues/234"

_LOGGER = logging.getLogger("people_context")

_P = ParamSpec("_P")
_R = TypeVar("_R")


def deprecated_tool(guidance: str) -> Callable[[Callable[_P, Awaitable[_R]]], Callable[_P, Awaitable[_R]]]:
    """Mark a tool deprecated: prefix its description and warn the operator on its first call.

    Apply it directly under ``@mcp.tool(...)`` so the SDK reads the prefixed description. ``guidance`` says which
    tool to use instead and when this one is still the right choice.
    """

    def decorate(tool: Callable[_P, Awaitable[_R]]) -> Callable[_P, Awaitable[_R]]:
        name = tool.__name__
        warned = False

        @functools.wraps(tool)
        async def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            nonlocal warned
            if not warned:
                warned = True
                _LOGGER.warning(
                    "MCP tool %s is deprecated and will be removed in 2.0; see %s",
                    name,
                    DEPRECATION_ISSUE_URL,
                )
            return await tool(*args, **kwargs)

        notice = f"Deprecated: removed in 2.0 ({DEPRECATION_ISSUE_URL}). {guidance}"
        wrapper.__doc__ = f"{notice}\n\n{tool.__doc__ or ''}".rstrip() + "\n"
        return wrapper

    return decorate
