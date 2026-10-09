"""The nightly ingest query catalog (``config/ingest-queries.yaml``).

A tiny, strictly validated schema. The catalog is data, not code: operators
edit the YAML to add, disable or re-weight queries, and the planner rotates the
enabled entries deterministically. Validation is deliberate and loud — a typo in
an ``id`` or an out-of-range ``limit`` fails at load, not silently at 02:30.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Environment variable that overrides the catalog location in the Job image.
ENV_CATALOG = "NIGHTLY_CATALOG"

#: ``<repo>/config/ingest-queries.yaml``. ``parents[5]`` walks
#: pipeline -> agentic_kg -> src -> core -> packages -> repo root, both in a
#: checkout and in the Job image (where ``src`` is copied to
#: ``/app/packages/core/src`` and ``config`` to ``/app/config``).
DEFAULT_CATALOG_PATH = (
    Path(__file__).resolve().parents[5] / "config" / "ingest-queries.yaml"
)


class QuerySpec(BaseModel):
    """One discoverable ingest query."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    query: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    limit: int = Field(ge=1, le=50)
    weight: float = Field(default=1.0, gt=0.0)
    enabled: bool = True


class QueryCatalog(BaseModel):
    """The whole catalog. Ids are unique and at least one query is enabled."""

    model_config = ConfigDict(extra="forbid")

    queries: list[QuerySpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate(self) -> "QueryCatalog":
        ids = [spec.id for spec in self.queries]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate query id(s): {', '.join(duplicates)}")
        if not any(spec.enabled for spec in self.queries):
            raise ValueError("at least one query must be enabled")
        return self

    @property
    def enabled(self) -> list[QuerySpec]:
        return [spec for spec in self.queries if spec.enabled]


def default_catalog_path() -> Path:
    """The catalog to load when no explicit path is given.

    ``NIGHTLY_CATALOG`` wins. Otherwise the package-relative default is used
    when it exists (a checkout, or the Job image where ``config/`` sits beside
    ``packages/``); when the package was installed non-editable (CI's
    ``pip install ./packages/core``), that path lands in site-packages, so fall
    back to ``./config/ingest-queries.yaml`` relative to the working directory.
    """
    override = os.environ.get(ENV_CATALOG)
    if override:
        return Path(override)
    if DEFAULT_CATALOG_PATH.is_file():
        return DEFAULT_CATALOG_PATH
    return Path.cwd() / "config" / "ingest-queries.yaml"


def load_catalog(path: str | Path | None = None) -> QueryCatalog:
    """Load and validate a catalog from YAML.

    Args:
        path: Explicit path, or ``None`` to use :func:`default_catalog_path`.

    Raises:
        FileNotFoundError: The file does not exist.
        ValueError: The YAML is malformed or fails schema validation.
    """
    resolved = Path(path) if path is not None else default_catalog_path()
    if not resolved.is_file():
        raise FileNotFoundError(f"ingest query catalog not found: {resolved}")
    try:
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"catalog {resolved} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"catalog {resolved} must be a mapping with a 'queries' key")
    return QueryCatalog.model_validate(raw)


__all__ = [
    "DEFAULT_CATALOG_PATH",
    "ENV_CATALOG",
    "QueryCatalog",
    "QuerySpec",
    "default_catalog_path",
    "load_catalog",
]
