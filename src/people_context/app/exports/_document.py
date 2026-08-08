"""Canonical JSON text for the versioned documents this project declares as interfaces."""

from __future__ import annotations

import json

from pydantic import BaseModel


def render_json_document(document: BaseModel) -> str:
    """Render one versioned document as canonical JSON text ending in a newline.

    Separators and indentation are fixed and the model's own field order is preserved,
    so the same document always produces byte-identical output. The trailing newline is
    part of the rendered text, which is what lets stdout and a written file carry exactly
    the same bytes.
    """
    return json.dumps(document.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"
