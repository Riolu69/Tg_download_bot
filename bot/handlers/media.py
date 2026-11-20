import asyncio
import os
import re
import uuid
from pathlib import Path

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.services.downloader import download_media, search_audio

router = Router()

YOUTUBE_RE = re.compile(r"(https?://)?(www\.)?(youtube\.com|youtu\.be)/.+")
INSTAGRAM_RE = re.compile(r"(https?://)?(www\.)?instagram\.com/(reel|p|tv)/.+")

# Временное хранилище ссылок (callback_data ограничена 64 байтами)
URL_CACHE: dict[str, str] = {}


def _cache_url(url: str) -> str:
    token = uuid.uuid4().hex[:8]
    if len(URL_CACHE) > 300:
        URL_CACHE.pop(next(iter(URL_CACHE)))
    URL_CACHE[token] = url
    return token


def _format_duration(seconds: int | None) -> str:
    if not seconds:
        return ""
    mins, secs = divmod(seconds, 60)
    hours, mins = divmod(mins, 60)
    if hours:
        return f" ({hours}:{mins:02d}:{secs:02d})"
    return f" ({mins}:{secs:02d})"


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


def build_audio_search_keyboard(tracks: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for track in tracks:
        token = _cache_url(track["url"])
        dur = _format_duration(track.get("duration"))
        title = track["title"]
        if len(title) > 45:
            title = title[:42] + "..."
        rows.append([
            InlineKeyboardButton(text=f"🎵 {title}{dur}", callback_data=f"aud:{token}")
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _safe_edit(msg: Message, text: str):
    try:
        await msg.edit_text(text)
    except TelegramBadRequest:
        pass


async def _download_and_send(
    target_msg: Message,
    status_msg: Message,
    query: str,
    mode: str,
    quality: str = "1080",
    header: str = "Скачиваю...",
):
    file_path = None
    thumb_path = None
    loop = asyncio.get_running_loop()

    def on_progress(progress_text: str):
        asyncio.run_coroutine_threadsafe(
            _safe_edit(status_msg, f"{header}\n{progress_text}"),
            loop,
        )

    try:
        meta = await asyncio.to_thread(download_media, query, mode, quality, on_progress)
        file_path = meta["path"]
        thumb_path = meta.get("thumb")
        title = meta["title"]

        await _safe_edit(status_msg, f"{header}\nОтправляю в Telegram...")

        safe_name = f"{title[:50]}{Path(file_path).suffix}"
        media = FSInputFile(file_path, filename=safe_name)
        thumb = FSInputFile(thumb_path) if thumb_path and os.path.exists(thumb_path) else None

        if mode == "music" or (mode == "youtube" and quality == "audio"):
            await target_msg.answer_audio(
                audio=media,
                title=title,
                duration=meta.get("duration"),
                thumbnail=thumb,
            )
        else:
            await target_msg.answer_video(
                video=media,
                caption=title[:200],
                width=meta.get("width"),
                height=meta.get("height"),
                duration=meta.get("duration"),
                thumbnail=thumb,
                supports_streaming=True,
            )

        await status_msg.delete()

    except Exception as e:
        await _safe_edit(status_msg, f"Ошибка загрузки: {e}")

    finally:
        for path in (file_path, thumb_path):
            if path and os.path.exists(path):
                os.remove(path)


@router.message(F.text)
async def handle_text(message: Message):
    text = message.text.strip()

    if YOUTUBE_RE.match(text):
        token = _cache_url(text)
        await message.answer(
            "Выберите качество загрузки:",
            reply_markup=build_quality_keyboard(token),
        )
        return

    if INSTAGRAM_RE.match(text):
        status_msg = await message.answer("Скачиваю из Instagram...")
        await _download_and_send(
            message, status_msg, text, "instagram", header="Скачиваю из Instagram..."
        )
        return

    # Поиск музыки с выбором трека
    status_msg = await message.answer(f"Ищу треки по запросу «{text}»...")
    try:
        tracks = await asyncio.to_thread(search_audio, text, 5)
    except Exception as e:
        await _safe_edit(status_msg, f"Ошибка поиска: {e}")
        return

    if not tracks:
        await _safe_edit(status_msg, f"По запросу «{text}» ничего не найдено.")
        return

    await status_msg.edit_text(
        f"Результаты по запросу «{text}»:\nВыберите трек для скачивания:",
        reply_markup=build_audio_search_keyboard(tracks),
    )


@router.callback_query(F.data.startswith("yt:"))
async def handle_youtube_quality(callback: CallbackQuery):
    await callback.answer()

    _, token, quality = callback.data.split(":", maxsplit=2)
    url = URL_CACHE.get(token)

    if not url:
        await callback.message.edit_text("Ссылка устарела, отправьте её заново.")
        return

    label = "аудио (MP3)" if quality == "audio" else f"видео ({quality}p)"
    header = f"Скачиваю {label}..."
    await callback.message.edit_text(header, reply_markup=None)

    await _download_and_send(
        callback.message, callback.message, url, "youtube", quality, header=header
    )


@router.callback_query(F.data.startswith("aud:"))
async def handle_audio_select(callback: CallbackQuery):
    await callback.answer()

    _, token = callback.data.split(":", maxsplit=1)
    url = URL_CACHE.get(token)

    if not url:
        await callback.message.edit_text("Список устарел, отправьте запрос заново.")
        return

    header = "Скачиваю трек (MP3)..."
    await callback.message.edit_text(header, reply_markup=None)

    await _download_and_send(
        callback.message, callback.message, url, "youtube", "audio", header=header
    )
