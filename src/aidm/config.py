"""Global configuration for the AI Dungeon Master engine.

Initialized once at startup via :func:`init_config` and accessed anywhere via
:func:`get_config` (singleton). Config holds **infrastructure** settings only
(the LLM endpoint) plus runtime feature flags — never game state. It is the
source of truth, loaded from a single ``config.json`` at the working-directory
root: startup fails loudly if that file is missing or omits the LLM endpoint,
rather than silently defaulting to an endpoint that may not be running. Copy
``examples/config.example.json`` to ``config.json`` to get started.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aidm.const import CONFIG_FILENAME


class VertexProviderConfig(BaseModel):
    """Google Cloud Vertex AI settings for serving Claude models.

    Auth uses Application Default Credentials (``gcloud auth application-default
    login`` or ``GOOGLE_APPLICATION_CREDENTIALS``). Use ``region="global"`` for the
    cross-region endpoint, which is the most forgiving for model availability.
    """

    model_config = ConfigDict(frozen=True)

    type: Literal["vertex"]
    region: str = Field(min_length=1)
    project_id: str = Field(min_length=1)


class LLMConfig(BaseModel):
    """LLM provider/connection settings (from ``config.json``).

    Two shapes are supported. With no ``provider``, the model is served over an
    OpenAI-compatible endpoint and ``base_url`` is required (e.g. a local
    llama.cpp server). With a ``provider`` block, the model is served by that
    provider (e.g. Claude on Vertex), and ``base_url``/``api_key`` are unused.
    """

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True, extra="forbid")

    base_url: str | None = None
    model: str = Field(min_length=1)
    api_key: str = "-"
    timeout_s: float = Field(default=120.0, gt=0, allow_inf_nan=False)
    # Requested output ceiling; provider-specific reasoning accounting may differ.
    max_tokens: int = Field(default=8192, gt=0)
    provider: VertexProviderConfig | None = None

    @model_validator(mode="after")
    def _require_base_url_without_provider(self) -> LLMConfig:
        if self.provider is None and self.base_url is None:
            msg = "llm.base_url is required when no llm.provider is set."
            raise ValueError(msg)
        if self.base_url is not None:
            url = urlsplit(self.base_url)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                msg = "llm.base_url must be an HTTP(S) endpoint without credentials, query or fragment."
                raise ValueError(msg)
        return self


class FeatureFlags(BaseModel):
    """Runtime feature toggles (from the CLI / environment)."""

    model_config = ConfigDict(frozen=True)

    dev_mode: bool = False


class Config(BaseModel):
    """Composed engine configuration for a session."""

    model_config = ConfigDict(frozen=True)

    features: FeatureFlags
    llm: LLMConfig


class _ConfigHolder:
    instance: Config | None = None


def init_config(*, dev: bool = False, config_path: Path | None = None) -> Config:
    """Build the config singleton from a config file and runtime flags.

    Parameters
    ----------
    dev
        Whether the ``--dev`` CLI flag was passed. Dev mode is also enabled via
        ``AIDM_DEV=1`` in the environment.
    config_path
        Path to the config file. Defaults to ``./config.json``. The file is
        required: a missing file or one without a valid ``llm`` block raises.
    """
    features = FeatureFlags(dev_mode=dev or os.environ.get("AIDM_DEV") == "1")
    path = config_path if config_path is not None else Path(CONFIG_FILENAME)
    if not path.is_file():
        msg = f"Config file not found: {path}. Copy examples/config.example.json to {CONFIG_FILENAME} and set your LLM endpoint."
        raise FileNotFoundError(msg)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = "Config must be a JSON object."
        raise ValueError(msg)  # noqa: TRY004 (reported as a config validation error)
    llm = LLMConfig.model_validate(raw.get("llm", {}))
    config = Config(features=features, llm=llm)
    _ConfigHolder.instance = config
    return config


def get_config() -> Config:
    """Return the initialized config singleton."""
    if _ConfigHolder.instance is None:
        msg = "Config not initialized; call init_config() first."
        raise RuntimeError(msg)
    return _ConfigHolder.instance
