"""Shared test fixtures.

The whole suite runs against the deterministic mock provider by default, so
`make test` works on a machine with no models, no GPU and no network. Tests that
genuinely need a real backend are marked (`ollama`, `docker`) and excluded from
the default run.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from workbench.core.clock import FrozenClock, SystemClock, set_clock
from workbench.core.ids import set_deterministic
from workbench.providers.mock import MockProvider
from workbench.providers.registry import ModelRegistry

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _deterministic() -> Iterator[None]:
    """Freeze time and identifiers for every test.

    Golden-file comparisons and artifact digests are only stable if nothing in
    the system reaches for the wall clock or a random number.
    """
    set_clock(FrozenClock())
    set_deterministic(True)
    yield
    set_clock(SystemClock())
    set_deterministic(False)


@pytest.fixture
def mock_provider() -> MockProvider:
    """A fresh mock provider loaded with the checked-in fixtures."""
    return MockProvider(fixtures_path=FIXTURES / "mock_responses.yaml", record_misses=False)


@pytest.fixture
def manifest_path() -> Path:
    return REPO_ROOT / "config" / "models.yaml"


@pytest.fixture
def router_config_path() -> Path:
    return REPO_ROOT / "config" / "router.yaml"


@pytest.fixture
def rbac_config_path() -> Path:
    return REPO_ROOT / "config" / "rbac.yaml"


@pytest.fixture
def ingest_config_path() -> Path:
    return REPO_ROOT / "config" / "ingest.yaml"


@pytest.fixture
def registry(manifest_path: Path) -> ModelRegistry:
    """The real manifest, so tests catch a manifest that stops parsing."""
    return ModelRegistry(manifest_path, fixtures_path=FIXTURES / "mock_responses.yaml")


def pytest_configure(config: pytest.Config) -> None:
    # Never let a test accidentally hit a real backend.
    os.environ.setdefault("WORKBENCH_PROVIDER", "mock")
    os.environ.setdefault("WORKBENCH_DETERMINISTIC", "1")


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip backend-dependent tests unless explicitly opted into."""
    if not os.environ.get("OLLAMA_E2E"):
        skip_ollama = pytest.mark.skip(reason="set OLLAMA_E2E=1 to run against real models")
        for item in items:
            if "ollama" in item.keywords:
                item.add_marker(skip_ollama)
