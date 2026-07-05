"""Fixtures and shared helpers for the Fraimic test suite."""
from __future__ import annotations

import pytest
from PIL import Image

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/fraimic like a real HA install would."""
    yield


def write_test_image(tmp_path) -> str:
    """A small JPEG on disk, for tests exercising send_image/play_media."""
    path = tmp_path / "photo.jpg"
    Image.new("RGB", (400, 300), (10, 20, 30)).save(path, format="JPEG")
    return str(path)
