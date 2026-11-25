import os
import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: runs > 20 s; skipped by make swtest, run by make swtest-all")
    config.addinivalue_line("markers", "nolint: presets engine state directly (no instruction writes it): no "
                                       "read-before-write lint (jspe_lint.py)")


@pytest.fixture(autouse=True)
def _nolint(request, monkeypatch):
    if request.node.get_closest_marker("nolint"):
        monkeypatch.setenv("JSPE_LINT", "off")
