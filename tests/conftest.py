"""Temporary copies of public fixtures, never the user's saves."""

import shutil

import pytest

from tests.test_regressions import GAMES


@pytest.fixture
def world(tmp_path):
    save = tmp_path / "world"
    shutil.copytree(GAMES / "hollowreach", save)
    return save
