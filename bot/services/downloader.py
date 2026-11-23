import asyncio
import os
import re
import shutil
import uuid
from pathlib import Path

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)

from bot.config import DOWNLOAD_DIR
from bot.services.downloader import (
    MediaNotFoundError,
    download_media,
    get_video_size_hints,
    search_audio,
)

router = Router()

YOUTUBE_RE = re.compile(r"(https?://)?(www\.|m\.)?(youtube\.com|youtu\.be)/.+")
RUTUBE_RE = re.compile(r"(https?://)?(www\.|m\.)?rutube\.ru/(video|shorts|yappy)/.+")
INSTAGRAM_RE = re.compile(r"(https?://)?(www\.)?instagram\.com/(reel|p|tv)/.+")
TIKTOK_RE = re.compile(r"(https?://)?(www\.|vm\.|vt\.|m\.)?tiktok\.com/.+")
VK_CLIP_RE = re.compile(r"(https?://)?(www\.|m\.)?(vk\.com|vk\.ru|vkvideo\.ru)/clip-?\d+_\d+.*")
VK_VIDEO_RE = re.compile(r"(https?://)?(www\.|m\.)?(vk\.com|vk\.ru|vkvideo\.ru)/(video-?\d+_\d+|.*z=video-?\d+_\d+).*")
TWITTER_RE = re.compile(r"(https?://)?(www\.|m\.|mobile\.)?(twitter\.com|x\.com)/.+")

SHORT_VIDEO_MODES = {"instagram", "tiktok", "vk_clip", "twitter"}

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


def _label_with_size(base_label: str, size_hint: str | None) -> str:
    if not size_hint:
        return base_label
    return f"{base_label} ({size_hint})"


def build_quality_keyboard(
    mode: str,
    token: str,
    size_hints: dict[str, str] | None = None,
) -> InlineKeyboardMarkup:
    hints = size_hints or {}
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_label_with_size("🎬 1080p", hints.get("1080")),
                    callback_data=f"q:{mode}:{token}:1080",
                ),
                InlineKeyboardButton(
                    text=_label_with_size("🎬 720p", hints.get("720")),
                    callback_data=f"q:{mode}:{token}:720",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_label_with_size("🎬 480p", hints.get("480")),
                    callback_data=f"q:{mode}:{token}:480",
                ),
                InlineKeyboardButton(
                    text=_label_with_size("🎬 360p", hints.get("360")),
                    callback_data=f"q:{mode}:{token}:360",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_label_with_size("🎵 Только аудио (MP3)", hints.get("audio")),
                    callback_data=f"q:{mode}:{token}:audio",
                ),
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


def _cleanup_paths(
    file_path: str | None,
    thumb_path: str | None,
    photo_paths: list[str],
) -> None:
    """Удаляет скачанные файлы, превью и временные директории после отправки."""
    base_dir = Path(DOWNLOAD_DIR).resolve()
    dirs_to_remove: set[Path] = set()
    files_to_remove: set[Path] = set()

    for raw in (file_path, thumb_path, *photo_paths):
        if not raw:
            continue
        p = Path(raw).resolve()
        try:
            p.relative_to(base_dir)
        except ValueError:
            continue

        if p.parent != base_dir and "_gdl" in p.parent.name:
            dirs_to_remove.add(p.parent)
        else:
            files_to_remove.add(p)
            stem = p.stem.split(".", maxsplit=1)[0]
            if stem and p.parent == base_dir:
                for sibling in base_dir.glob(f"{stem}*"):
                    if sibling.is_file():
                        files_to_remove.add(sibling)
                    elif sibling.is_dir() and "_gdl" in sibling.name:
                        dirs_to_remove.add(sibling)

    for file_item in files_to_remove:
        try:
            file_item.unlink(missing_ok=True)
        except OSError:
            pass

    for dir_item in dirs_to_remove:
        try:
            shutil.rmtree(dir_item, ignore_errors=True)
        except OSError:
            pass


async def _download_and_send(
    target_msg: Message,
    status_msg: Message,
    query: str,
    mode: str,
    quality: str = "1080",
    header: str = "Скачиваю...",
):
    file_path: str | None = None
    thumb_path: str | None = None
    photo_paths: list[str] = []
    loop = asyncio.get_running_loop()

    def on_progress(progress_text: str):
        asyncio.run_coroutine_threadsafe(
            _safe_edit(status_msg, f"{header}\n{progress_text}"),
            loop,
        )

    try:
        meta = await asyncio.to_thread(download_media, query, mode, quality, on_progress)
        media_type = meta.get("type", "video")
        title = meta["title"]

        await _safe_edit(status_msg, f"{header}\nОтправляю в Telegram...")

        if media_type == "photos":
            photo_paths = meta.get("paths") or []
            if len(photo_paths) == 1:
                await target_msg.answer_photo(photo=FSInputFile(photo_paths[0]))
            elif photo_paths:
                group = [InputMediaPhoto(media=FSInputFile(p)) for p in photo_paths[:10]]
                await target_msg.answer_media_group(media=group)
        else:
            file_path = meta.get("path")
            thumb_path = meta.get("thumb")
            if not file_path:
                raise MediaNotFoundError("Файл не найден после загрузки.")
            safe_name = f"{title[:50]}{Path(file_path).suffix}"
            media = FSInputFile(file_path, filename=safe_name)
            thumb = FSInputFile(thumb_path) if thumb_path and os.path.exists(thumb_path) else None

            if media_type == "audio":
                await target_msg.answer_audio(
                    audio=media,
                    title=title,
                    duration=meta.get("duration"),
                    thumbnail=thumb,
                )
            else:
                await target_msg.answer_video(
                    video=media,
                    caption=None if mode in SHORT_VIDEO_MODES else title[:200],
                    width=meta.get("width"),
                    height=meta.get("height"),
                    duration=meta.get("duration"),
                    thumbnail=thumb,
                    supports_streaming=True,
                )

        await status_msg.delete()

    except MediaNotFoundError as e:
        await _safe_edit(
            status_msg,
            f"⚠️ Медиа не найдено\n{e}",
        )
    except Exception:
        await _safe_edit(
            status_msg,
            "⚠️ Не удалось загрузить файл\nСсылка неверная, файл не найден или сервис временно недоступен.",
        )

    finally:
        _cleanup_paths(file_path, thumb_path, photo_paths)


@router.message(F.text)
async def handle_text(message: Message):
    text = message.text.strip()

    if YOUTUBE_RE.match(text):
        token = _cache_url(text)
        wait_msg = await message.answer("Проверяю доступные качества и размер...")
        size_hints = await asyncio.to_thread(get_video_size_hints, text, "youtube")
        await wait_msg.edit_text(
            "Выберите качество загрузки:",
            reply_markup=build_quality_keyboard("youtube", token, size_hints),
        )
        return

    if RUTUBE_RE.match(text):
        token = _cache_url(text)
        await message.answer(
            "Выберите качество загрузки (Rutube):",
            reply_markup=build_quality_keyboard("rutube", token),
        )
        return

    if VK_VIDEO_RE.match(text):
        token = _cache_url(text)
        await message.answer(
            "Выберите качество загрузки (VK Видео):",
            reply_markup=build_quality_keyboard("vk_video", token),
        )
        return

    if INSTAGRAM_RE.match(text):
        header = "Скачиваю из Instagram..."
        status_msg = await message.answer(header)
        await _download_and_send(message, status_msg, text, "instagram", header=header)
        return

    if TIKTOK_RE.match(text):
        header = "Скачиваю из TikTok..."
        status_msg = await message.answer(header)
        await _download_and_send(message, status_msg, text, "tiktok", header=header)
        return

    if VK_CLIP_RE.match(text):
        header = "Скачиваю VK Клип..."
        status_msg = await message.answer(header)
        await _download_and_send(message, status_msg, text, "vk_clip", header=header)
        return

    if TWITTER_RE.match(text):
        header = "Скачиваю из Twitter (X)..."
        status_msg = await message.answer(header)
        await _download_and_send(message, status_msg, text, "twitter", header=header)
        return

    # Поиск музыки с выбором трека
    status_msg = await message.answer(f"Ищу треки по запросу «{text}»...")
    try:
        tracks = await asyncio.to_thread(search_audio, text, 5)
    except Exception:
        await _safe_edit(status_msg, "⚠️ Не удалось выполнить поиск. Попробуйте позже.")
        return

    if not tracks:
        await _safe_edit(status_msg, f"По запросу «{text}» ничего не найдено.")
        return

    await status_msg.edit_text(
        f"Результаты по запросу «{text}»:\nВыберите трек для скачивания:",
        reply_markup=build_audio_search_keyboard(tracks),
    )


@router.callback_query(F.data.startswith("q:"))
async def handle_video_quality(callback: CallbackQuery):
    await callback.answer()

    _, mode, token, quality = callback.data.split(":", maxsplit=3)
    url = URL_CACHE.get(token)

    if not url:
        await callback.message.edit_text("Ссылка устарела, отправьте её заново.")
        return

    label = "аудио (MP3)" if quality == "audio" else f"видео ({quality}p)"
    header = f"Скачиваю {label}..."
    await callback.message.edit_text(header, reply_markup=None)

    await _download_and_send(
        callback.message, callback.message, url, mode, quality, header=header
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
