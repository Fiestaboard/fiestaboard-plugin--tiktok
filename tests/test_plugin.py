"""Tests for the TikTok plugin. All HTTP is mocked (see conftest.py)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from plugins import tiktok as mod
from plugins.tiktok import TikTokPlugin, board_safe, format_count

from .conftest import TEST_TOKEN, USER_INFO_URL, FakeResponse, error_body

ROOT = Path(__file__).parent.parent


# ----------------------------------------------------------------------
# Manifest
# ----------------------------------------------------------------------


class TestManifest:
    def test_identity(self, manifest_data):
        assert manifest_data["id"] == "tiktok"
        assert manifest_data["name"] == "TikTok"
        assert manifest_data["category"] in {"art", "data", "transit", "weather", "entertainment", "utility", "home"}
        assert manifest_data["fiestaboard_version"] == ">=9.9.0"

    def test_oauth_block(self, manifest_data):
        oauth = manifest_data["oauth"]
        assert oauth["flows"] == ["relay"]
        assert oauth["authorization_url"] == "https://www.tiktok.com/v2/auth/authorize/"
        assert oauth["token_url"] == "https://open.tiktokapis.com/v2/oauth/token/"
        assert oauth["scopes"] == ["user.info.basic", "user.info.stats"]
        assert oauth["scope_separator"] == ","
        assert oauth["client_id_param"] == "client_key"
        assert oauth["client_id_setting"] == "client_id"
        assert oauth["client_secret_setting"] == "client_secret"
        assert oauth["app_setup_url"].startswith("https://")

    def test_no_secret_or_shipped_client_in_manifest(self, manifest_data):
        oauth = manifest_data["oauth"]
        assert "client_secret" not in oauth
        assert "client_id" not in oauth

    def test_settings_hold_client_key_and_secret(self, manifest_data):
        props = manifest_data["settings_schema"]["properties"]
        assert props["client_id"]["type"] == "string"
        assert props["client_secret"]["ui:widget"] == "password"
        assert "required" not in manifest_data["settings_schema"]
        assert props["refresh_seconds"]["minimum"] == manifest_data["min_refresh_seconds"]

    def test_variables_have_groups_and_examples(self, manifest_data):
        variables = manifest_data["variables"]
        groups = set(variables["groups"])
        for name, spec in variables["simple"].items():
            assert spec["group"] in groups, name
            assert "example" in spec, name

    def test_previews_fit_board(self, manifest_data):
        sizes = {"flagship": (6, 22), "note": (3, 15)}
        for preview in manifest_data["previews"]:
            rows, cols = sizes[preview["device_type"]]
            assert len(preview["rows"]) == rows
            for row in preview["rows"]:
                assert len(row.replace("{63}", "#")) <= cols, row

    def test_teaser_fits(self, manifest_data):
        assert len(manifest_data["teaser"].replace("{63}", "#")) <= 22

    def test_data_keys_match_manifest(self, plugin, tiktok_api, manifest_data):
        result = plugin.fetch_data()
        assert set(result.data) == set(manifest_data["variables"]["simple"])

    def test_docs_exist(self):
        assert (ROOT / "README.md").is_file()
        assert (ROOT / "docs" / "SETUP.md").is_file()


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


class TestHelpers:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (0, "0"),
            (999, "999"),
            (1000, "1K"),
            (1234, "1.2K"),
            (15600, "16K"),
            (1234567, "1.2M"),
            (2_500_000_000, "2.5B"),
            (None, "0"),
            ("x", "0"),
            (-5, "0"),
        ],
    )
    def test_format_count(self, value, expected):
        assert format_count(value) == expected

    def test_board_safe(self):
        assert board_safe("Café Owl 🦉") == "CAFE OWL"
        assert board_safe("pixel_owl") == "PIXEL OWL"
        assert board_safe(None) == ""


# ----------------------------------------------------------------------
# Fetching
# ----------------------------------------------------------------------


class TestFetch:
    def test_success(self, plugin, tiktok_api):
        result = plugin.fetch_data()
        assert result.available, result.error
        d = result.data
        assert d["display_name"] == "PIXEL OWL"
        assert d["followers"] == 15600
        assert d["followers_short"] == "16K"
        assert d["following"] == 321
        assert d["following_short"] == "321"
        assert d["likes"] == 1234567
        assert d["likes_short"] == "1.2M"
        assert d["videos"] == 87
        assert d["videos_short"] == "87"
        assert d["followers_line"] == "FOLLOWERS 16K"
        assert d["likes_line"] == "LIKES 1.2M"
        assert d["summary"] == "16K FANS 1.2M LIKES"
        assert result.formatted_lines

    def test_request_shape(self, plugin, tiktok_api):
        plugin.fetch_data()
        call = tiktok_api.calls[0]
        assert call["url"] == USER_INFO_URL
        assert call["params"] == {"fields": "display_name,follower_count,following_count,likes_count,video_count"}
        assert call["headers"]["Authorization"] == f"Bearer {TEST_TOKEN}"
        assert call["timeout"]

    def test_missing_counts_become_empty(self, plugin, tiktok_api):
        tiktok_api.user = {"display_name": "Pixel Owl"}
        d = plugin.fetch_data().data
        assert d["followers"] == ""
        assert d["followers_short"] == ""
        assert d["followers_line"] == ""
        assert d["summary"] == ""

    def test_display_name_fallback(self, plugin, tiktok_api):
        tiktok_api.user["display_name"] = "🦉🦉"
        assert plugin.fetch_data().data["display_name"] == "TIKTOK"

    def test_snapshot_shared_between_boards(self, plugin, tiktok_api, clock):
        plugin.fetch_data()
        plugin.fetch_data()
        assert len(tiktok_api.calls) == 1
        clock.advance(mod.SNAPSHOT_TTL_SECONDS + 1)
        plugin.fetch_data()
        assert len(tiktok_api.calls) == 2

    def test_config_change_clears_snapshot(self, plugin, tiktok_api):
        plugin.fetch_data()
        old = dict(plugin.config)
        plugin.on_config_change(old, {**old, "refresh_seconds": 600})
        plugin.fetch_data()
        assert len(tiktok_api.calls) == 2


class TestNotConnected:
    def test_no_token(self, plugin, token, tiktok_api):
        token.value = None
        result = plugin.fetch_data()
        assert not result.available
        assert result.error == mod.NOT_CONNECTED_ERROR
        assert tiktok_api.calls == []

    def test_old_platform(self, manifest_data):
        p = TikTokPlugin(manifest_data)
        p.config = {"enabled": True}
        if hasattr(p, "get_oauth_token"):
            p.get_oauth_token = None
        result = p.fetch_data()
        assert not result.available
        assert result.error == mod.PLATFORM_TOO_OLD_ERROR


# ----------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------


class TestErrors:
    def test_invalid_token_401(self, plugin, tiktok_api):
        tiktok_api.fail(FakeResponse(error_body("access_token_invalid"), 401))
        result = plugin.fetch_data()
        assert not result.available
        assert result.error == mod.UNAUTHORIZED_ERROR

    def test_scope_not_authorized(self, plugin, tiktok_api):
        tiktok_api.fail(FakeResponse(error_body("scope_not_authorized"), 401))
        result = plugin.fetch_data()
        assert result.error == mod.SCOPE_ERROR

    def test_scope_permission_missed_400(self, plugin, tiktok_api):
        tiktok_api.fail(FakeResponse(error_body("scope_permission_missed"), 400))
        assert plugin.fetch_data().error == mod.SCOPE_ERROR

    def test_auth_cooldown_is_per_token(self, plugin, tiktok_api, token):
        tiktok_api.fail(FakeResponse(error_body("access_token_invalid"), 401))
        plugin.fetch_data()
        plugin.fetch_data()
        assert len(tiktok_api.calls) == 1
        token.value = "test_new_token"
        assert plugin.fetch_data().available
        assert len(tiktok_api.calls) == 2

    def test_rate_limit_backs_off(self, plugin, tiktok_api, clock):
        tiktok_api.fail(FakeResponse(error_body("rate_limit_exceeded"), 429))
        result = plugin.fetch_data()
        assert "rate limit" in result.error
        plugin.fetch_data()
        assert len(tiktok_api.calls) == 1
        clock.advance(mod.RATE_LIMIT_COOLDOWN_SECONDS + 1)
        assert plugin.fetch_data().available

    @pytest.mark.parametrize(
        "response",
        [
            FakeResponse(error_body("internal_error"), 500),
            requests.exceptions.Timeout(),
            requests.exceptions.ConnectionError(),
            FakeResponse(None, 200),
            FakeResponse(["not", "a", "dict"], 200),
        ],
    )
    def test_transient_errors_show_stale_data(self, plugin, tiktok_api, clock, response):
        assert plugin.fetch_data().available
        clock.advance(mod.SNAPSHOT_TTL_SECONDS + 1)
        tiktok_api.fail(response)
        result = plugin.fetch_data()
        assert result.available
        assert result.data["followers"] == 15600

    def test_transient_error_without_stale_data(self, plugin, tiktok_api):
        tiktok_api.fail(FakeResponse(error_body("internal_error"), 503))
        result = plugin.fetch_data()
        assert not result.available
        assert "HTTP 503" in result.error

    def test_stale_data_expires(self, plugin, tiktok_api, clock):
        plugin.fetch_data()
        clock.advance(mod.STALE_DATA_LIMIT_SECONDS + 1)
        tiktok_api.fail(requests.exceptions.Timeout())
        result = plugin.fetch_data()
        assert not result.available
        assert "Timed out" in result.error

    def test_error_object_on_200(self, plugin, tiktok_api):
        tiktok_api.fail(FakeResponse(error_body("invalid_params", "bad fields"), 200))
        result = plugin.fetch_data()
        assert not result.available
        assert "invalid_params" in result.error

    def test_other_4xx(self, plugin, tiktok_api):
        tiktok_api.fail(FakeResponse(error_body("invalid_params"), 400))
        result = plugin.fetch_data()
        assert not result.available
        assert "HTTP 400" in result.error

    def test_unexpected_exception_never_raises(self, plugin, tiktok_api, monkeypatch):
        monkeypatch.setattr(plugin, "_build_data", lambda snap: 1 / 0)
        result = plugin.fetch_data()
        assert not result.available


# ----------------------------------------------------------------------
# Config validation
# ----------------------------------------------------------------------


class TestValidateConfig:
    def test_valid(self, plugin):
        config = {"client_id": "abc123XYZ", "client_secret": "s3cret", "refresh_seconds": 900}
        assert plugin.validate_config(config) == []

    def test_empty_is_fine(self, plugin):
        assert plugin.validate_config({}) == []

    def test_bad_client_key(self, plugin):
        errors = plugin.validate_config({"client_id": "has spaces in it"})
        assert any("Client Key" in e for e in errors)

    def test_secret_without_key_is_fine(self, plugin):
        # The guided setup saves both together; never block on order.
        assert plugin.validate_config({"client_secret": "x"}) == []

    def test_refresh_too_low(self, plugin):
        assert plugin.validate_config({"refresh_seconds": 10})


# ----------------------------------------------------------------------
# Display
# ----------------------------------------------------------------------


class TestDisplay:
    def test_flagship_layout(self, plugin, tiktok_api):
        lines = plugin.fetch_data().formatted_lines
        assert len(lines) == 6
        assert lines[1] == "PIXEL OWL"
        assert "FOLLOWERS 16K" in lines
        assert all(len(line.replace("{63}", "#")) <= 22 for line in lines)

    def test_note_layout(self, plugin, tiktok_api):
        with plugin._bound_board(SimpleNamespace(cols=15, rows=3)):
            lines = plugin.fetch_data().formatted_lines
        assert len(lines) == 3
        assert all(len(line.replace("{63}", "#")) <= 15 for line in lines)

    def test_get_formatted_display(self, plugin, tiktok_api):
        assert plugin.get_formatted_display()[1] == "PIXEL OWL"

    def test_get_formatted_display_unavailable(self, plugin, token):
        token.value = None
        assert plugin.get_formatted_display() is None


def test_plugin_export():
    assert mod.Plugin is TikTokPlugin
    assert TikTokPlugin.plugin_id.fget(None) == "tiktok"


def test_readme_lists_every_variable(manifest_data):
    readme = (ROOT / "README.md").read_text()
    for name in manifest_data["variables"]["simple"]:
        assert f"{{{{tiktok.{name}}}}}" in readme, name


def test_manifest_is_valid_json():
    json.loads((ROOT / "manifest.json").read_text())
