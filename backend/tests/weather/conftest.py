import pytest
from weather_helpers import FakeHttp

from weather import client


@pytest.fixture
def fake_http(monkeypatch):
    """Replace requests.get inside weather.client; any real network call would be a bug."""
    http = FakeHttp()
    monkeypatch.setattr(client.requests, "get", http)
    return http
