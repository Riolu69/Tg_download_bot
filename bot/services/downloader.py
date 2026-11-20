import time
import uuid
from typing import Callable

import yt_dlp
from yt_dlp.networking.impersonate import ImpersonateTarget
from bot.config import COOKIES_FILE, DOWNLOAD_DIR, MAX_FILE_SIZE_MB

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _build_bar(percent: float, length: int = 10) -> str:
    filled = int(length * percent / 100)
    return "█" * filled + "░" * (length - filled)


def search_audio(query: str, limit: int = 5) -> list[dict]:
    """Быстро ищет треки на YouTube без скачивания и возвращает список словарей."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "source_address": "0.0.0.0",
    }
    if COOKIES_FILE:
        ydl_opts["cookiefile"] = COOKIES_FILE

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)

    results = []
    for entry in info.get("entries") or []:
        url = entry.get("url")
        if not url:
            continue
        if not url.startswith("http"):
            url = f"https://www.youtube.com/watch?v={url}"
        results.append({
            "title": entry.get("title") or "Без названия",
            "url": url,
            "duration": int(entry["duration"]) if entry.get("duration") else None,
        })
    return results


def download_media(
    query: str,
    mode: str,
    quality: str = "1080",
    progress_callback: Callable[[str], None] | None = None,
) -> dict:
    """Синхронно скачивает медиа, обложку и возвращает словарь с путями и метаданными."""
    file_id = uuid.uuid4().hex
    outtmpl = str(DOWNLOAD_DIR / f"{file_id}.%(ext)s")
    last_update = 0.0

    def progress_hook(d: dict):
        nonlocal last_update
        if not progress_callback:
            return

        status = d.get("status")
        now = time.monotonic()

        if status == "downloading":
            if now - last_update < 2.0:
                return
            last_update = now

            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            speed = d.get("speed") or 0
            speed_mb = speed / (1024 * 1024)

            if total > 0:
                pct = min(100.0, downloaded * 100 / total)
                bar = _build_bar(pct)
                progress_callback(f"[{bar}] {pct:.0f}% • {speed_mb:.1f} МБ/с")
            else:
                dl_mb = downloaded / (1024 * 1024)
                progress_callback(f"Скачано {dl_mb:.1f} МБ • {speed_mb:.1f} МБ/с")

        elif status == "finished":
            last_update = now
            progress_callback("Обработка и склейка файла...")

    ydl_opts = {
        "outtmpl": outtmpl,
        "source_address": "0.0.0.0",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "writethumbnail": True,
        "max_filesize": MAX_FILE_SIZE_MB * 1024 * 1024,
        "progress_hooks": [progress_hook],
        "extractor_args": {
            "youtube": {
                "player_client": ["mweb"],
            }
        },
        "postprocessors": [
            {"key": "FFmpegThumbnailsConvertor", "format": "jpg"},
        ],
    }

    if COOKIES_FILE:
        ydl_opts["cookiefile"] = COOKIES_FILE

    if mode == "music" or (mode in ("youtube", "vk_video") and quality == "audio"):
        ydl_opts["format"] = "bestaudio/best"
        ydl_opts["postprocessors"].insert(0, {
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        })
        if mode == "music" and not query.startswith("http"):
            ydl_opts["default_search"] = "ytsearch1"
    elif mode in ("youtube", "vk_video"):
        ydl_opts.update({
            "format": (
                f"bestvideo[height<={quality}][vcodec^=avc1]+bestaudio[ext=m4a]/"
                f"bestvideo[height<={quality}]+bestaudio/"
                f"best[height<={quality}]/best"
            ),
            "merge_output_format": "mp4",
        })
    elif mode in ("instagram", "tiktok", "vk_clip"):
        ydl_opts.update({
            "format": "best[ext=mp4]/bestvideo[vcodec^=avc1]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
            "merge_output_format": "mp4",
        })
        if mode == "tiktok":
            ydl_opts["impersonate"] = ImpersonateTarget("chrome")

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(query, download=True)
        if "entries" in info:
            info = info["entries"][0]

    all_files = list(DOWNLOAD_DIR.glob(f"{file_id}.*"))
    media_files = [f for f in all_files if f.suffix.lower() not in IMAGE_EXTS]
    thumb_files = [f for f in all_files if f.suffix.lower() == ".jpg"]

    if not media_files:
        for f in all_files:
            f.unlink(missing_ok=True)
        raise FileNotFoundError(
            f"Файл не найден после загрузки (возможно, превышен лимит {MAX_FILE_SIZE_MB} МБ)."
        )

    return {
        "path": str(media_files[0]),
        "thumb": str(thumb_files[0]) if thumb_files else None,
        "title": info.get("title", "media"),
        "width": info.get("width"),
        "height": info.get("height"),
        "duration": int(info["duration"]) if info.get("duration") else None,
    }
