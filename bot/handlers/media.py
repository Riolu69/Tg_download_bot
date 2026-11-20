import asyncio
import os
import re
import uuid
from pathlib import Path

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.services.downloader import download_media

router = Router()

YOUTUBE_RE = re.compile(r"(https?://)?(www\.)?(youtube\.com|youtu\.be)/.+")
INSTAGRAM_RE = re.compile(r"(https?://)?(www\.)?instagram\.com/(reel|p|tv)/.+")

# Временное хранилище ссылок (callback_data в Telegram ограничена 64 байтами)
URL_CACHE: dict[str, str] = {}


def build_quality_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎬 1080p", callback_data=f"yt:{token}:1080"),
                InlineKeyboardButton(text="🎬 720p", callback_data=f"yt:{token}:720"),
            ],
            [
                InlineKeyboardButton(text="🎬 480p", callback_data=f"yt:{token}:480"),
                InlineKeyboardButton(text="🎬 360p", callback_data=f"yt:{token}:360"),
            ],
            [
                InlineKeyboardButton(text="🎵 Только аудио (MP3)", callback_data=f"yt:{token}:audio"),
            ],
        ]
    )


async def _download_and_send(
    target_msg: Message,
    status_msg: Message,
    query: str,
    mode: str,
    quality: str = "1080",
):
    file_path = None
    try:
        file_path, title = await asyncio.to_thread(download_media, query, mode, quality)
        safe_name = f"{title[:50]}{Path(file_path).suffix}"
        media = FSInputFile(file_path, filename=safe_name)

        if mode == "music" or (mode == "youtube" and quality == "audio"):
            await target_msg.answer_audio(audio=media, title=title)
        else:
            await target_msg.answer_video(video=media, caption=title[:200])

        await status_msg.delete()

    except Exception as e:
        await status_msg.edit_text(f"Ошибка загрузки: {e}")

    finally:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)


@router.message(F.text)
async def handle_text(message: Message):
    text = message.text.strip()

    if YOUTUBE_RE.match(text):
        token = uuid.uuid4().hex[:8]
        if len(URL_CACHE) > 200:
            URL_CACHE.pop(next(iter(URL_CACHE)))
        URL_CACHE[token] = text

        await message.answer(
            "Выберите качество загрузки:",
            reply_markup=build_quality_keyboard(token),
        )
        return

    if INSTAGRAM_RE.match(text):
        mode = "instagram"
        status_text = "Скачиваю из Instagram..."
    else:
        mode = "music"
        status_text = f"Ищу трек «{text}»..."

    status_msg = await message.answer(status_text)
    await _download_and_send(message, status_msg, text, mode)


@router.callback_query(F.data.startswith("yt:"))
async def handle_youtube_quality(callback: CallbackQuery):
    await callback.answer()

    _, token, quality = callback.data.split(":", maxsplit=2)
    url = URL_CACHE.get(token)

    if not url:
        await callback.message.edit_text("Ссылка устарела, отправьте её заново.")
        return

    label = "аудио (MP3)" if quality == "audio" else f"видео ({quality}p)"
    await callback.message.edit_text(f"Скачиваю {label}...", reply_markup=None)

    await _download_and_send(callback.message, callback.message, url, "youtube", quality)
