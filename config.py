from __future__ import annotations

import os

from dotenv import load_dotenv


load_dotenv()


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


TELEGRAM_BOT_TOKEN: str = _require_env("TELEGRAM_BOT_TOKEN")
ENCRYPTION_KEY: str = _require_env("ENCRYPTION_KEY")

DATABASE_PATH: str = os.getenv("DATABASE_PATH", "bot.db")
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# Google OAuth / Gmail API
GOOGLE_CLIENT_SECRETS_FILE: str = os.getenv("GOOGLE_CLIENT_SECRETS_FILE", "client_secret.json")
OAUTH_REDIRECT_BASE: str = os.getenv("OAUTH_REDIRECT_BASE", "http://localhost:8085")

# Web server (Cloud Run)
PORT: int = int(os.getenv("PORT", "8080"))
BASE_URL: str = os.getenv("BASE_URL", "")  # e.g. https://<cloud-run-service-url>
TELEGRAM_WEBHOOK_PATH: str = os.getenv("TELEGRAM_WEBHOOK_PATH", "/telegram/webhook")
