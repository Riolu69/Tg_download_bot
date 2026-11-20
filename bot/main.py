import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer

from bot.config import BOT_TOKEN, LOCAL_API_URL, USE_LOCAL_API
from bot.handlers import media, start


async def main():
    logging.basicConfig(level=logging.INFO)

    if USE_LOCAL_API:
        session = AiohttpSession(
            api=TelegramAPIServer.from_base(LOCAL_API_URL, is_local=True),
            timeout=600,
        )
        bot = Bot(token=BOT_TOKEN, session=session)
    else:
        bot = Bot(token=BOT_TOKEN)

    dp = Dispatcher()
    dp.include_router(start.router)
    dp.include_router(media.router)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
