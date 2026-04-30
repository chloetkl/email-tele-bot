from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Optional

from aiohttp import web
from google_auth_oauthlib.flow import Flow


@dataclass(frozen=True)
class OAuthRequest:
    telegram_user_id: int
    state: str


class OAuthServer:
    """
    Minimal local HTTP server to receive Google's OAuth2 redirect.

    This is designed for a single-process bot:
    - /login generates a state and authorization URL
    - Google redirects back to /oauth2/callback?state=...&code=...
    - we exchange code -> tokens, then call on_success(user_id, refresh_token)
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int,
        redirect_base: str,
        client_secrets_file: str,
        on_success: Callable[[int, str], Awaitable[None]],
        on_error: Callable[[int, str], Awaitable[None]],
    ):
        self._host = host
        self._port = port
        self._redirect_base = redirect_base.rstrip("/")
        self._client_secrets_file = client_secrets_file
        self._on_success = on_success
        self._on_error = on_error

        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

        self._pending: Dict[str, tuple[int, Flow]] = {}
        self._lock = asyncio.Lock()

    @property
    def callback_url(self) -> str:
        return f"{self._redirect_base}/oauth2/callback"

    async def ensure_started(self) -> None:
        if self._runner:
            return

        app = web.Application()
        app.add_routes([web.get("/oauth2/callback", self._handle_callback)])
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host=self._host, port=self._port)
        await self._site.start()

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
        self._runner = None
        self._site = None

    async def create_auth_url(self, *, telegram_user_id: int) -> str:
        await self.ensure_started()

        state = secrets.token_urlsafe(24)
        flow = Flow.from_client_secrets_file(
            self._client_secrets_file,
            scopes=["https://www.googleapis.com/auth/gmail.send"],
            state=state,
        )
        flow.redirect_uri = self.callback_url

        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )

        async with self._lock:
            self._pending[state] = (telegram_user_id, flow)

        return auth_url

    async def _handle_callback(self, request: web.Request) -> web.Response:
        state = request.query.get("state", "")
        code = request.query.get("code", "")
        error = request.query.get("error", "")

        async with self._lock:
            pending = self._pending.pop(state, None)

        if not pending:
            return web.Response(text="Login session not found. Return to Telegram and run /login again.", status=400)

        telegram_user_id, flow = pending

        if error:
            await self._on_error(telegram_user_id, f"Google returned error: {error}")
            return web.Response(text="Login cancelled/failed. Return to Telegram.", status=400)

        if not code:
            await self._on_error(telegram_user_id, "Missing authorization code.")
            return web.Response(text="Missing code. Return to Telegram and try /login again.", status=400)

        try:
            flow.fetch_token(code=code)
            creds = flow.credentials
            refresh_token = getattr(creds, "refresh_token", None)
            if not refresh_token:
                await self._on_error(
                    telegram_user_id,
                    "No refresh token returned. Try /login again (Google may have skipped consent).",
                )
                return web.Response(text="No refresh token returned. Return to Telegram and try again.", status=400)

            await self._on_success(telegram_user_id, refresh_token)
            return web.Response(text="Login successful. You can return to Telegram.", status=200)
        except Exception as e:
            await self._on_error(telegram_user_id, f"Token exchange failed: {type(e).__name__}: {e}")
            return web.Response(text="Token exchange failed. Return to Telegram and try again.", status=500)
