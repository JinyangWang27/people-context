"""Optional pinned Model2Vec embedding adapter and explicit cache download."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

MODEL_REPOSITORY = "minishlab/potion-multilingual-128M"
MODEL_REVISION = "73908c3438cf03b6a01bcb9611d62b23d0726f08"
MODEL_ID = f"{MODEL_REPOSITORY}@{MODEL_REVISION}"
MODEL_DIMENSION = 256
MODEL_URL = f"https://huggingface.co/{MODEL_REPOSITORY}/tree/{MODEL_REVISION}"
MODEL_DOWNLOAD_SIZE = "approximately 512 MB"


class SemanticPackageNotAvailableError(RuntimeError):
    """Raised when an optional semantic package is not installed."""


class SemanticModelNotAvailableError(RuntimeError):
    """Raised when the pinned model is not present in the local cache."""


class Model2VecEmbeddingProvider:
    """Adapt a locally loaded Model2Vec StaticModel to the embedding port."""

    def __init__(self, model: Any) -> None:
        self._model = model
        self._dimension = int(model.dim)
        if self._dimension != MODEL_DIMENSION:
            raise ValueError(f"expected {MODEL_DIMENSION}-dimension model, got {self._dimension}")

    @property
    def model_id(self) -> str:
        return MODEL_ID

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        embeddings = self._model.encode(texts, show_progress_bar=False)
        return [[float(value) for value in vector] for vector in embeddings]


def semantic_cache_dir() -> Path:
    """Resolve the Hugging Face hub cache while honoring its documented overrides."""
    if value := os.environ.get("HF_HUB_CACHE"):
        return Path(value).expanduser().resolve()
    if value := os.environ.get("HF_HOME"):
        return (Path(value).expanduser() / "hub").resolve()
    cache_home = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")).expanduser()
    return (cache_home / "huggingface" / "hub").resolve()


def download_embedding_provider() -> Model2VecEmbeddingProvider:
    """Download the pinned snapshot, then load it from its resolved local path."""
    try:
        from huggingface_hub import snapshot_download
        from model2vec import StaticModel
    except ImportError as exc:  # pragma: no cover - exercised in base-install subprocess proof
        raise SemanticPackageNotAvailableError("install the semantic optional dependencies") from exc

    model_path = snapshot_download(
        repo_id=MODEL_REPOSITORY,
        revision=MODEL_REVISION,
        cache_dir=semantic_cache_dir(),
        local_files_only=False,
    )
    return Model2VecEmbeddingProvider(StaticModel.from_pretrained(model_path, force_download=False))


def create_local_embedding_provider() -> Model2VecEmbeddingProvider:
    """Load only an already cached pinned snapshot; never make a network request."""
    try:
        from huggingface_hub import snapshot_download
        from model2vec import StaticModel
    except ImportError as exc:
        raise SemanticPackageNotAvailableError("install the semantic optional dependencies") from exc

    try:
        model_path = snapshot_download(
            repo_id=MODEL_REPOSITORY,
            revision=MODEL_REVISION,
            cache_dir=semantic_cache_dir(),
            local_files_only=True,
        )
    except Exception as exc:
        raise SemanticModelNotAvailableError("the pinned semantic model is not cached") from exc
    return Model2VecEmbeddingProvider(StaticModel.from_pretrained(model_path, force_download=False))
