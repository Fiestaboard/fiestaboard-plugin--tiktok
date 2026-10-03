"""Shared fixtures for the TikTok plugin tests.

``tiktok_api`` patches ``requests.get`` with a fake TikTok v2 API that answers
``GET /v2/user/info/`` with payloads shaped like the real ones (including the
``error`` object TikTok sends on every response). Tests change its state or
queue raw responses to simulate errors.

``get_oauth_token`` is patched on the plugin instance: the platform's OAuth
service is not under test here, only how the plugin uses what it returns.
No test touches the network. All names and values are invented.
"""

import json
from collections import deque
from pathlib import Path
from unittest.mock import patch

import pytest
from plugins.tiktok import TikTokPlugin

MANIFEST_PATH = Path(__file__).parent.parent / "manifest.json"
USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"
TEST_CLIENT_KEY = "test_client_key_abc123"
TEST_CLIENT_SECRET = "test_client_secret"
TEST_TOKEN = "test_access_token"


class FakeResponse:
    def __init__(self, body=None, status_code=200, headers=None):
        self._body = body
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


def ok_body(user):
    return {"data": {"user": user}, "error": {"code": "ok", "message": "", "log_id": "test_log"}}


def error_body(code, message="test"):
    return {"data": {}, "error": {"code": code, "message": message, "log_id": "test_log"}}


class FakeTikTok:
    """Answers requests.get the way TikTok's user info endpoint would."""

    def __init__(self):
        self.user = {
            "open_id": "test-open-id",
            "display_name": "Pixel Owl",
            "follower_count": 15600,
            "following_count": 321,
            "likes_count": 1234567,
            "video_count": 87,
        }
        self.queued = deque()
        self.calls = []

    def fail(self, response, times=1):
        self.queued.extend([response] * times)

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        assert url == USER_INFO_URL, f"Unexpected TikTok request: {url}"
        if self.queued:
            response = self.queued.popleft()
            if isinstance(response, Exception):
                raise response
            return response
        fields = (params or {}).get("fields", "").split(",")
        return FakeResponse(ok_body({k: v for k, v in self.user.items() if k in fields}))


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def manifest_data():
    with open(MANIFEST_PATH) as f:
        return json.load(f)


@pytest.fixture(autouse=True)
def clock():
    c = Clock()
    with patch("plugins.tiktok._monotonic", side_effect=c):
        yield c


@pytest.fixture
def tiktok_api():
    fake = FakeTikTok()
    with patch("plugins.tiktok.requests.get", side_effect=fake):
        yield fake


@pytest.fixture
def token():
    class Token:
        value = TEST_TOKEN

    return Token


@pytest.fixture
def make_plugin(manifest_data, token):
    def factory(**config):
        p = TikTokPlugin(manifest_data)
        p.config = {"enabled": True, "client_id": TEST_CLIENT_KEY, "client_secret": TEST_CLIENT_SECRET, **config}
        p.get_oauth_token = lambda: token.value
        return p

    return factory


@pytest.fixture
def plugin(make_plugin):
    return make_plugin()
