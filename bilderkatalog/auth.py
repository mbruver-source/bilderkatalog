"""Anmeldung am Microsoft-Konto über MSAL (öffentlicher Client, nur Leserechte)."""

from __future__ import annotations

import threading

import msal

from . import config


class AuthError(RuntimeError):
    pass


class Auth:
    def __init__(self, client_id: str):
        self._lock = threading.Lock()
        self._cache = msal.SerializableTokenCache()
        cache_file = config.token_cache_path()
        if cache_file.exists():
            self._cache.deserialize(cache_file.read_text(encoding="utf-8"))
        self._app = msal.PublicClientApplication(
            client_id, authority=config.authority(), token_cache=self._cache
        )

    def _persist(self) -> None:
        if self._cache.has_state_changed:
            config.token_cache_path().write_text(self._cache.serialize(), encoding="utf-8")

    def account_name(self) -> str | None:
        accounts = self._app.get_accounts()
        return accounts[0].get("username") if accounts else None

    def token(self) -> str | None:
        """Gültiges Access-Token aus dem Cache (ggf. per Refresh-Token erneuert)."""
        with self._lock:
            accounts = self._app.get_accounts()
            if not accounts:
                return None
            result = self._app.acquire_token_silent(config.SCOPES, account=accounts[0])
            self._persist()
            return result.get("access_token") if result else None

    def login_interactive(self) -> str:
        """Öffnet den Browser für die Microsoft-Anmeldung. Blockiert bis zum Abschluss."""
        result = self._app.acquire_token_interactive(config.SCOPES, prompt="select_account")
        with self._lock:
            self._persist()
        if "access_token" not in result:
            raise AuthError(result.get("error_description") or result.get("error") or "Anmeldung fehlgeschlagen")
        return self.account_name() or ""

    def logout(self) -> None:
        with self._lock:
            for account in self._app.get_accounts():
                self._app.remove_account(account)
            self._persist()
