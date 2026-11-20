import asyncio
import os
import re
from pathlib import Path

from aiogram import F, Router
from aiogram.types import FSInputFile, Message

from bot.services.downloader import download_media

router = Router()

YOUTUBE_RE = re.compile(r"(https?://)?(www\.)?(youtube\.com|youtu\.be)/.+")
INSTAGRAM_RE = re.compile(r"(https?://)?(www\.)?instagram\.com/(reel|p|tv)/.+")


@router.message(F.text)
async def handle_text(message: Message):
    text = message.text.strip()

    if YOUTUBE_RE.match(text):
        mode = "youtube"
        status_text = "Скачиваю видео с YouTube..."
    elif INSTAGRAM_RE.match(text):
        mode = "instagram"
        status_text = "Скачиваю из Instagram..."
    else:
        mode = "music"
        status_text = f"Ищу трек «{text}»..."

    status_msg = await message.answer(status_text)
    file_path = None

    try:
        file_path, title = await asyncio.to_thread(download_media, text, mode)
        safe_name = f"{title[:50]}{Path(file_path).suffix}"
        media = FSInputFile(file_path, filename=safe_name)

        if mode == "music":
            await message.answer_audio(audio=media, title=title)
        else:
            await message.answer_video(video=media, caption=title[:200])

        await status_msg.delete()

    except Exception as e:
        await status_msg.edit_text(f"Ошибка загрузки: {e}")

    finally:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
