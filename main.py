import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

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
        ("level", "Set outgoing audio gain"),
        ("leave", "Disconnect the VC bridge"),
        ("status", "Show live bridge status"),
        ("unhost", "Remove hosted account"),
        ("cancel", "Cancel account login"),
    ]
    await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in commands])

    try:
        await restore_accounts(db, settings)
        logging.getLogger("chimney").info("CHIMNEY VC BOT is online.")
        await dp.start_polling(bot, db=db, settings=settings)
    finally:
        await shutdown_accounts()
        await bot.session.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
