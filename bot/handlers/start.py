from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "Отправь мне:\n"
        "• Ссылку на YouTube — скачаю видео\n"
        "• Ссылку на Instagram Reels/пост — скачаю видео\n"
        "• Название трека — найду и пришлю MP3"
    )
