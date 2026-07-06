"""Credential storage for cookie-web provider."""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class CredentialStore:
    """Store and retrieve browser credentials."""

    def __init__(self, store_path: Optional[str] = None):
        if store_path:
            self.store_path = Path(store_path)
        else:
            try:
                from hermes_constants import get_hermes_home
                base = get_hermes_home()
            except (ImportError, ModuleNotFoundError):
                base = Path.home() / ".cookie-web"
            self.store_path = base / "credentials.json"

    def _ensure_dir(self):
        """Ensure store directory exists."""
        self.store_path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict:
        """Load credentials from store."""
        if not self.store_path.exists():
            return {}

        try:
            with open(self.store_path, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load credentials: {e}")
            return {}

    def save(self, credentials: dict):
        """Save credentials to store."""
        self._ensure_dir()

        try:
            with open(self.store_path, "w") as f:
                json.dump(credentials, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save credentials: {e}")

    def get_provider_credentials(self, provider: str) -> Optional[dict]:
        """Get credentials for a specific provider."""
        all_creds = self.load()
        return all_creds.get(provider)

    def set_provider_credentials(self, provider: str, credentials: dict):
        """Set credentials for a specific provider."""
        all_creds = self.load()
        all_creds[provider] = credentials
        self.save(all_creds)

    def clear_provider(self, provider: str):
        """Clear credentials for a specific provider."""
        all_creds = self.load()
        if provider in all_creds:
            del all_creds[provider]
            self.save(all_creds)

    def clear_all(self):
        """Clear all credentials."""
        self.save({})
