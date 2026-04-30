from __future__ import annotations

import logging

from aiohttp import web
from telegram import Update

import config
from bot import build_app

logger = logging.getLogger(__name__)


def _normalize_path(path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    return path


async def create_web_app() -> web.Application:
    tg_app = build_app()
    oauth = tg_app.bot_data["oauth"]

    webhook_path = _normalize_path(config.TELEGRAM_WEBHOOK_PATH)

    async def health(_: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def telegram_webhook(request: web.Request) -> web.Response:
        data = await request.json()
        update = Update.de_json(data, tg_app.bot)
        await tg_app.process_update(update)
        return web.Response(text="ok")

    app = web.Application()
    app.add_routes(
        [
            web.get("/healthz", health),
            web.get("/oauth2/callback", oauth.handle_callback),
            web.post(webhook_path, telegram_webhook),
        ]
    )

    async def on_startup(_: web.Application) -> None:
        await tg_app.initialize()
        await tg_app.start()

        if not config.BASE_URL:
            logger.warning("BASE_URL not set; not configuring Telegram webhook.")
            return

        webhook_url = config.BASE_URL.rstrip("/") + webhook_path
        await tg_app.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES)
        logger.info("Telegram webhook set to %s", webhook_url)

    async def on_cleanup(_: web.Application) -> None:
        await tg_app.stop()
        await tg_app.shutdown()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def main() -> None:
    logging.basicConfig(level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO))
    web.run_app(create_web_app(), host="0.0.0.0", port=config.PORT)


if __name__ == "__main__":
    main()

