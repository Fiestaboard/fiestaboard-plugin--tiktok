"""TikTok plugin for FiestaBoard.

Shows the signed-in user's TikTok follower, following, like and video counts.

Sign-in uses FiestaBoard's platform OAuth with the relay flow (the manifest's
``oauth`` block). Each user brings their own TikTok developer app (Sandbox mode
is enough for your own account) and enters its Client key and Client secret.
TikTok calls the client ID ``client_key`` and wants comma-separated scopes,
hence ``client_id_param`` and ``scope_separator`` in the manifest (core 9.9.0).
The platform stores the tokens (access 24 hours, refresh 365 days) and
refreshes them; this plugin only calls ``self.get_oauth_token()``.

Endpoint (TikTok API v2):

* ``GET /v2/user/info/?fields=...``   display name and the four counts
"""

import logging
import re
import threading
import time
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

import requests
from src.board_chars import BoardChars
from src.plugins.base import PluginBase, PluginResult
from src.text_to_board import take_tiles

logger = logging.getLogger(__name__)

USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"
USER_FIELDS = "display_name,follower_count,following_count,likes_count,video_count"
USER_AGENT = "FiestaBoard TikTok Plugin (https://github.com/Fiestaboard/fiestaboard-plugin--tiktok)"
REQUEST_TIMEOUT_SECONDS = 10

# A Flagship and a Note each ask for data; one snapshot serves both.
SNAPSHOT_TTL_SECONDS = 60
TRANSIENT_COOLDOWN_SECONDS = 60
AUTH_COOLDOWN_SECONDS = 300
RATE_LIMIT_COOLDOWN_SECONDS = 300
# Counts change slowly, so older numbers are still worth showing in an outage.
STALE_DATA_LIMIT_SECONDS = 3600

DEFAULT_COLS = 22
DEFAULT_ROWS = 6
TILE_ACCENT = "{63}"  # red

NOT_CONNECTED_ERROR = "Not signed in to TikTok. Open this plugin's settings and sign in."
PLATFORM_TOO_OLD_ERROR = "This FiestaBoard version cannot sign in to TikTok. Update FiestaBoard to use this plugin."
UNAUTHORIZED_ERROR = "TikTok rejected the sign-in (401). Open this plugin's settings and press Reconnect."
SCOPE_ERROR = (
    "TikTok did not allow reading your stats. Add the user.info.stats scope to your TikTok app, then press Reconnect."
)

CLIENT_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
SCOPE_ERROR_CODES = {"scope_not_authorized", "scope_permission_missed"}


class TikTokError(Exception):
    """A TikTok request failed; the message is shown to the user."""

    def __init__(self, message: str, *, transient: bool, cooldown: float):
        super().__init__(message)
        self.transient = transient
        self.cooldown = cooldown


# ----------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------


def _monotonic() -> float:
    return time.monotonic()


def board_safe(text: Any) -> str:
    """Uppercase *text*, fold accents, ``_`` -> space, drop what the board cannot show."""
    folded = unicodedata.normalize("NFKD", str(text or "").replace("_", " "))
    kept = []
    for ch in folded.upper():
        if ch.isspace():
            kept.append(" ")
        elif BoardChars.get_char_code(ch) is not None:
            kept.append(ch)
    return re.sub(r" {2,}", " ", "".join(kept)).strip()


def format_count(value: Any) -> str:
    """``1234`` -> ``1.2K``, ``15600`` -> ``16K``, ``1234567`` -> ``1.2M``."""
    try:
        n = max(int(value), 0)
    except (TypeError, ValueError):
        return "0"
    for size, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if n >= size:
            scaled = n / size
            text = f"{scaled:.1f}".rstrip("0").rstrip(".") if scaled < 10 else str(round(scaled))
            return f"{text}{suffix}"
    return str(n)


def _count(value: Any) -> Any:
    """An int count, or ``""`` when TikTok left it out."""
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return ""


def fit(text: str, width: int) -> str:
    """Truncate *text* to *width* tiles (colour markers count as one)."""
    return take_tiles(text, width)[0]


# ----------------------------------------------------------------------
# Plugin
# ----------------------------------------------------------------------


class TikTokPlugin(PluginBase):
    """Your TikTok follower, following, like and video counts."""

    def __init__(self, manifest: Dict[str, Any]):
        super().__init__(manifest)
        self._lock = threading.RLock()
        self._reset_state()

    def _reset_state(self) -> None:
        self._snapshot: Optional[Dict[str, Any]] = None
        self._snapshot_at = 0.0
        self._last_good: Optional[Dict[str, Any]] = None
        self._last_good_at = 0.0
        self._cooldown_until = 0.0
        self._cooldown_error: Optional[TikTokError] = None
        self._cooldown_token: Optional[str] = None

    @property
    def plugin_id(self) -> str:
        return "tiktok"

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def validate_config(self, config: Dict[str, Any]) -> List[str]:
        errors: List[str] = []
        client_key = str(config.get("client_id") or "").strip()
        if client_key and not CLIENT_KEY_RE.match(client_key):
            errors.append("Client Key should be the Client key from your TikTok app's page (letters and numbers)")
        errors.extend(self._validate_refresh_seconds(config))
        return errors

    def on_config_change(self, old_config: Dict[str, Any], new_config: Dict[str, Any]) -> None:
        super().on_config_change(old_config, new_config)
        with self._lock:
            self._reset_state()

    # ------------------------------------------------------------------
    # TikTok request
    # ------------------------------------------------------------------

    def _get_user(self, token: str) -> Dict[str, Any]:
        try:
            response = requests.get(
                USER_INFO_URL,
                params={"fields": USER_FIELDS},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                    "User-Agent": USER_AGENT,
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.exceptions.Timeout:
            raise TikTokError("Timed out contacting TikTok", transient=True, cooldown=TRANSIENT_COOLDOWN_SECONDS)
        except requests.exceptions.RequestException as exc:
            raise TikTokError(
                f"Could not reach TikTok: {type(exc).__name__}", transient=True, cooldown=TRANSIENT_COOLDOWN_SECONDS
            )

        try:
            body = response.json()
        except ValueError:
            body = None
        body = body if isinstance(body, dict) else {}
        error = body.get("error") if isinstance(body.get("error"), dict) else {}
        code = str(error.get("code") or "")
        status = response.status_code

        if code in SCOPE_ERROR_CODES:
            raise TikTokError(SCOPE_ERROR, transient=False, cooldown=AUTH_COOLDOWN_SECONDS)
        if status == 401:
            raise TikTokError(UNAUTHORIZED_ERROR, transient=False, cooldown=AUTH_COOLDOWN_SECONDS)
        if status == 429:
            raise TikTokError(
                f"TikTok rate limit reached; trying again in {RATE_LIMIT_COOLDOWN_SECONDS // 60} minutes",
                transient=True,
                cooldown=RATE_LIMIT_COOLDOWN_SECONDS,
            )
        if status >= 500:
            raise TikTokError(
                f"TikTok is having trouble (HTTP {status}); will retry",
                transient=True,
                cooldown=TRANSIENT_COOLDOWN_SECONDS,
            )
        if status != 200:
            raise TikTokError(
                f"Unexpected response from TikTok (HTTP {status})",
                transient=False,
                cooldown=TRANSIENT_COOLDOWN_SECONDS,
            )
        if not body:
            raise TikTokError("TikTok sent an unreadable answer", transient=True, cooldown=TRANSIENT_COOLDOWN_SECONDS)
        if code and code != "ok":
            raise TikTokError(f"TikTok error: {code}", transient=False, cooldown=TRANSIENT_COOLDOWN_SECONDS)
        user = (body.get("data") or {}).get("user")
        return user if isinstance(user, dict) else {}

    # ------------------------------------------------------------------
    # Snapshot, shared between boards
    # ------------------------------------------------------------------

    def _start_cooldown(self, exc: TikTokError, token: str) -> None:
        self._cooldown_until = max(self._cooldown_until, _monotonic() + exc.cooldown)
        self._cooldown_error = exc
        # A rejected token only blocks that token: reconnecting gives a new one.
        self._cooldown_token = None if exc.transient else token

    def _in_cooldown(self, token: str) -> bool:
        if self._cooldown_error is None or _monotonic() >= self._cooldown_until:
            return False
        return self._cooldown_token is None or self._cooldown_token == token

    def _load_snapshot(self, token: str) -> Dict[str, Any]:
        if self._snapshot is not None and _monotonic() - self._snapshot_at < SNAPSHOT_TTL_SECONDS:
            return self._snapshot
        if self._in_cooldown(token):
            raise self._cooldown_error
        try:
            user = self._get_user(token)
        except TikTokError as exc:
            self._start_cooldown(exc, token)
            raise
        self._cooldown_error = None
        self._snapshot = user
        self._snapshot_at = _monotonic()
        return user

    def _stale_snapshot(self) -> Optional[Dict[str, Any]]:
        if self._last_good is None or _monotonic() - self._last_good_at > STALE_DATA_LIMIT_SECONDS:
            return None
        return self._last_good

    # ------------------------------------------------------------------
    # fetch_data
    # ------------------------------------------------------------------

    def _board_size(self) -> Tuple[int, int]:
        board = self.board
        if board is None:
            return DEFAULT_COLS, DEFAULT_ROWS
        return board.cols, board.rows

    def fetch_data(self) -> PluginResult:
        try:
            get_token = getattr(self, "get_oauth_token", None)
            if get_token is None:
                return PluginResult(available=False, error=PLATFORM_TOO_OLD_ERROR)
            token = get_token()
            if not token:
                return PluginResult(available=False, error=NOT_CONNECTED_ERROR)

            cols, rows = self._board_size()
            with self._lock:
                try:
                    snapshot = self._load_snapshot(token)
                except TikTokError as exc:
                    stale = self._stale_snapshot()
                    if exc.transient and stale is not None:
                        logger.info("TikTok: %s; showing the last data", exc)
                        snapshot = stale
                    else:
                        logger.warning("TikTok: %s", exc)
                        return PluginResult(available=False, error=str(exc))
                else:
                    self._last_good = snapshot
                    self._last_good_at = _monotonic()

            data = self._build_data(snapshot)
            return PluginResult(available=True, data=data, formatted_lines=self._format_display(data, cols, rows))
        except Exception as exc:  # fetch_data must never raise
            logger.exception("Error fetching TikTok data")
            return PluginResult(available=False, error=str(exc))

    # ------------------------------------------------------------------
    # Variables
    # ------------------------------------------------------------------

    @staticmethod
    def _build_data(user: Dict[str, Any]) -> Dict[str, Any]:
        followers = _count(user.get("follower_count"))
        following = _count(user.get("following_count"))
        likes = _count(user.get("likes_count"))
        videos = _count(user.get("video_count"))

        def short(n: Any) -> str:
            return format_count(n) if n != "" else ""

        followers_short, likes_short = short(followers), short(likes)
        summary = f"{followers_short} FANS {likes_short} LIKES" if followers_short and likes_short else ""
        return {
            "display_name": board_safe(user.get("display_name")) or "TIKTOK",
            "followers": followers,
            "followers_short": followers_short,
            "following": following,
            "following_short": short(following),
            "likes": likes,
            "likes_short": likes_short,
            "videos": videos,
            "videos_short": short(videos),
            "followers_line": f"FOLLOWERS {followers_short}" if followers_short else "",
            "likes_line": f"LIKES {likes_short}" if likes_short else "",
            "summary": summary,
        }

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    @staticmethod
    def _format_display(data: Dict[str, Any], cols: int, rows: int) -> List[str]:
        """Fallback layout for a board *cols* x *rows* when there is no template."""
        lines = [
            f"{TILE_ACCENT} TIKTOK {TILE_ACCENT}" if rows > 3 else f"{TILE_ACCENT} {data['display_name']}",
        ]
        if rows > 3:
            lines.append(data["display_name"])
        lines += [
            data["followers_line"],
            data["likes_line"],
            f"VIDEOS {data['videos_short']}" if data["videos_short"] else "",
            f"FOLLOWING {data['following_short']}" if data["following_short"] else "",
        ]
        return [fit(line, cols) for line in lines[:rows]]

    def get_formatted_display(self) -> Optional[List[str]]:
        result = self.get_data(self.board)
        if not result.available or not result.data:
            return None
        cols, rows = self._board_size()
        return self._format_display(result.data, cols, rows)


# Export hook: the loader looks for a module-level `Plugin`.
Plugin = TikTokPlugin
