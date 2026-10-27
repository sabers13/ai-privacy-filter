"""Fixtures for the service tests; the shared stand-ins live in ``support``."""

from __future__ import annotations

import pytest
from support import FakeClock, Harness, first_occurrence_only


@pytest.fixture
def harness() -> Harness:
    return Harness.build()


@pytest.fixture
def blocked_harness() -> Harness:
    """Harness whose detector leaves a repeated value in the text."""
    return Harness.build(first_occurrence_only)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()
