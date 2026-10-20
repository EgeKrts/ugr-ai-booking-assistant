"""Pytest options: tests that call the real local model only run with --live."""
import pytest


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", default=False,
                     help="also run the tests marked 'live', which call the local Ollama model")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="needs the local model; run with --live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
