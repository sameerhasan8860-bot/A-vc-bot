import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo
from aiohttp import web
import os

from miniapp import create_app

from bot import restore_accounts, router, shutdown_accounts
from config import Settings
from database import Database


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    settings = Settings.from_env()
    db = Database(settings.mongo_uri, settings.api_hash, settings.bot_token)
    await db.connect()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(router)

    commands = [
        ("start", "Open CHIMNEY control panel"),
        ("help", "Show all commands"),
        ("host", "Host your Telegram account"),
        ("join", "Connect source and target VCs"),
        ("level", "Set legacy output gain"),
        ("amp", "DSP power and amplification"),
        ("gain", "Set DSP gain 0-100"),
        ("bass", "Set bass EQ 0-100"),
        ("treble", "Set treble EQ 0-100"),
        ("echo", "Set echo 0-100"),
        ("distortion", "Set distortion 0-100"),
        ("dsp", "Show audio DSP settings"),
        ("leave", "Disconnect the VC bridge"),
        ("status", "Show live bridge status"),
        ("unhost", "Remove hosted account"),
        ("cancel", "Cancel account login"),
    ]
    await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in commands])

    app_runner = web.AppRunner(create_app(db))
    await app_runner.setup()
    site = web.TCPSite(app_runner, "0.0.0.0", int(os.getenv("MINI_APP_PORT", "8080")))
    await site.start()
    mini_app_url = os.getenv("MINI_APP_URL", "").strip()
    if mini_app_url.startswith("https://"):
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="VC Studio", web_app=WebAppInfo(url=mini_app_url))
        )

    try:
        await restore_accounts(db, settings)
        logging.getLogger("chimney").info("CHIMNEY VC BOT is online.")
        await dp.start_polling(bot, db=db, settings=settings)
    finally:
        await shutdown_accounts()
        await app_runner.cleanup()
        await bot.session.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
