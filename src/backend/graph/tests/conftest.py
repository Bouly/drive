"""Shared fixtures for the graph tests."""

from django.core.cache import cache

import pytest


@pytest.fixture(autouse=True)
def clean_cache():
    """
    Start every test with an empty cache.

    The cache is a real Redis shared by every run: a mending left scheduled by
    one test silences the scheduling of the next, which showed up as a test
    passing alone and failing in the suite.
    """
    cache.clear()
    yield
    cache.clear()
