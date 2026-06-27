"""Proxy configuration for YouTube transcript fetching.

Provides proxy support to work around YouTube's IP-based rate limiting
and blocking of cloud provider IPs.

Supports:
- Generic HTTP/HTTPS proxies
- Webshare rotating residential proxies
- Environment variable configuration
"""
# Created: 2025-12-30

import os
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from youtube_transcript_api import YouTubeTranscriptApi

logger = logging.getLogger(__name__)


@dataclass
class ProxySettings:
    """Proxy configuration for transcript fetching.

    Attributes:
        enabled: Whether to use proxy for transcript requests
        type: Proxy type - "generic" or "webshare"
        http_url: HTTP proxy URL (for generic type)
        https_url: HTTPS proxy URL (for generic type)
        webshare_username: Webshare account username
        webshare_password: Webshare account password
        webshare_locations: List of country codes to filter IPs (e.g., ["us", "de"])
    """
    enabled: bool = False
    type: str = "generic"  # "generic" or "webshare"

    # Generic proxy settings
    http_url: str = ""
    https_url: str = ""

    # Webshare settings
    webshare_username: str = ""
    webshare_password: str = ""
    webshare_locations: List[str] = field(default_factory=list)

    def __post_init__(self):
        """Load from environment variables if not set."""
        # Generic proxy from env
        if not self.http_url:
            self.http_url = os.environ.get("YANGER_PROXY_HTTP", "")
        if not self.https_url:
            self.https_url = os.environ.get("YANGER_PROXY_HTTPS",
                                            os.environ.get("YANGER_PROXY_URL", ""))

        # Webshare from env
        if not self.webshare_username:
            self.webshare_username = os.environ.get("YANGER_WEBSHARE_USER", "")
        if not self.webshare_password:
            self.webshare_password = os.environ.get("YANGER_WEBSHARE_PASS", "")

        # Auto-enable if env vars are set
        if not self.enabled:
            if self.https_url or self.http_url:
                self.enabled = True
                self.type = "generic"
            elif self.webshare_username and self.webshare_password:
                self.enabled = True
                self.type = "webshare"

    @classmethod
    def from_dict(cls, data: dict) -> "ProxySettings":
        """Create ProxySettings from dictionary."""
        return cls(
            enabled=data.get("enabled", False),
            type=data.get("type", "generic"),
            http_url=data.get("http_url", ""),
            https_url=data.get("https_url", ""),
            webshare_username=data.get("webshare_username", ""),
            webshare_password=data.get("webshare_password", ""),
            webshare_locations=data.get("webshare_locations", []),
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "enabled": self.enabled,
            "type": self.type,
            "http_url": self.http_url,
            "https_url": self.https_url,
            "webshare_username": self.webshare_username,
            "webshare_password": self.webshare_password,
            "webshare_locations": self.webshare_locations,
        }

    def is_configured(self) -> bool:
        """Check if proxy is properly configured."""
        if not self.enabled:
            return False

        if self.type == "webshare":
            return bool(self.webshare_username and self.webshare_password)
        else:  # generic
            return bool(self.http_url or self.https_url)

    def get_display_info(self) -> str:
        """Get human-readable proxy info (hides credentials)."""
        if not self.enabled:
            return "Proxy disabled"

        if self.type == "webshare":
            locations = ", ".join(self.webshare_locations) if self.webshare_locations else "all"
            return f"Webshare (user: {self.webshare_username[:4]}***, locations: {locations})"
        else:
            # Mask credentials in URL
            url = self.https_url or self.http_url
            if "@" in url:
                # Has credentials - mask them
                parts = url.split("@")
                host = parts[-1]
                return f"Generic proxy: ***@{host}"
            return f"Generic proxy: {url}"


class ProxyConfigBuilder:
    """Builds youtube-transcript-api proxy configuration objects."""

    @staticmethod
    def build(settings: ProxySettings) -> Optional[Any]:
        """Build a proxy config object for youtube-transcript-api.

        Args:
            settings: ProxySettings configuration

        Returns:
            Proxy config object or None if not configured
        """
        if not settings.enabled or not settings.is_configured():
            return None

        try:
            if settings.type == "webshare":
                return ProxyConfigBuilder._build_webshare(settings)
            else:
                return ProxyConfigBuilder._build_generic(settings)
        except ImportError as e:
            logger.error(f"Failed to import proxy config classes: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to build proxy config: {e}")
            return None

    @staticmethod
    def _build_webshare(settings: ProxySettings) -> Any:
        """Build Webshare proxy config."""
        from youtube_transcript_api.proxies import WebshareProxyConfig

        kwargs = {
            "proxy_username": settings.webshare_username,
            "proxy_password": settings.webshare_password,
        }

        if settings.webshare_locations:
            kwargs["filter_ip_locations"] = settings.webshare_locations

        logger.info(f"Using Webshare proxy (locations: {settings.webshare_locations or 'all'})")
        return WebshareProxyConfig(**kwargs)

    @staticmethod
    def _build_generic(settings: ProxySettings) -> Any:
        """Build generic proxy config."""
        from youtube_transcript_api.proxies import GenericProxyConfig

        logger.info(f"Using generic proxy: {settings.get_display_info()}")
        return GenericProxyConfig(
            http_url=settings.http_url or None,
            https_url=settings.https_url or None,
        )


def create_transcript_api(proxy_settings: Optional[ProxySettings] = None) -> "YouTubeTranscriptApi":
    """Create a YouTubeTranscriptApi instance with optional proxy support.

    Args:
        proxy_settings: Optional proxy configuration

    Returns:
        Configured YouTubeTranscriptApi instance
    """
    from youtube_transcript_api import YouTubeTranscriptApi

    if proxy_settings and proxy_settings.enabled:
        proxy_config = ProxyConfigBuilder.build(proxy_settings)
        if proxy_config:
            logger.info(f"Created transcript API with proxy: {proxy_settings.get_display_info()}")
            return YouTubeTranscriptApi(proxy_config=proxy_config)
        else:
            logger.warning("Proxy enabled but configuration invalid, using direct connection")

    return YouTubeTranscriptApi()


def test_proxy_connection(settings: ProxySettings, test_video_id: str = "dQw4w9WgXcQ") -> dict:
    """Test proxy connection by fetching a known video transcript.

    Args:
        settings: Proxy settings to test
        test_video_id: Video ID to test with (default: Rick Astley)

    Returns:
        Dict with test results
    """
    result = {
        "success": False,
        "proxy_used": settings.get_display_info(),
        "error": None,
        "transcript_length": 0,
    }

    try:
        api = create_transcript_api(settings)
        transcript = api.fetch(test_video_id)

        result["success"] = True
        result["transcript_length"] = len(transcript)
        logger.info(f"Proxy test successful: fetched {len(transcript)} segments")

    except Exception as e:
        result["error"] = f"{type(e).__name__}: {str(e)}"
        logger.error(f"Proxy test failed: {result['error']}")

    return result
