import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Callable

import yt_dlp
from yt_dlp.networking.impersonate import ImpersonateTarget
from bot.config import COOKIES_FILE, DOWNLOAD_DIR, IG_COOKIES_FILE, MAX_FILE_SIZE_MB

logger = logging.getLogger(__name__)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


class MediaNotFoundError(Exception):
    """Понятная ошибка для пользователя, когда медиа не найдено или недоступно."""


def _build_bar(percent: float, length: int = 10) -> str:
    filled = int(length * percent / 100)
    return "█" * filled + "░" * (length - filled)


def _format_size_human(num_bytes: int | float | None) -> str | None:
    if not num_bytes or num_bytes <= 0:
        return None
    mb = num_bytes / (1024 * 1024)
    if mb >= 1024:
        return f"~{mb / 1024:.1f} ГБ"
    if mb >= 100:
        return f"~{mb:.0f} МБ"
    return f"~{mb:.1f} МБ"


def _estimate_format_bytes(fmt: dict, duration: float | None, is_audio: bool = False) -> int | None:
    size = fmt.get("filesize") or fmt.get("filesize_approx")
    if size and int(size) > 0:
        return int(size)

    if duration and duration > 0:
        if is_audio:
            abr = fmt.get("abr") or fmt.get("tbr")
            if abr and float(abr) > 0:
                return int(float(abr) * 1000 / 8 * duration)
        else:
            vbr = fmt.get("vbr") or fmt.get("tbr")
            if vbr and float(vbr) > 0:
                return int(float(vbr) * 1000 / 8 * duration)

    return None


def get_video_size_hints(url: str, mode: str = "youtube") -> dict[str, str]:
    """Оценивает примерный размер для качеств 1080/720/480/360 и audio без скачивания файла."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "source_address": "0.0.0.0",
        "extractor_args": {
            "youtube": {
                "player_client": ["mweb"],
            }
        },
    }
    if COOKIES_FILE:
        ydl_opts["cookiefile"] = COOKIES_FILE

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False) or {}
    except Exception as exc:
        logger.debug("Failed to fetch size hints for %s: %s", url, exc)
        return {}

    if isinstance(info, dict) and "entries" in info and info["entries"]:
        info = info["entries"][0] or {}

    formats = info.get("formats") or []
    duration = info.get("duration")
    if not isinstance(formats, list) or not formats:
        return {}

    # Собираем аудиопотоки
    audio_items: list[tuple[float, int]] = []
    # Собираем видеопотоки: (height, is_avc1, has_audio, bitrate_or_size, est_bytes)
    video_items: list[tuple[int, int, int, float, int]] = []

    for fmt in formats:
        if not isinstance(fmt, dict):
            continue
        vcodec = fmt.get("vcodec") or "none"
        acodec = fmt.get("acodec") or "none"
        height = int(fmt.get("height") or 0)

        if vcodec == "none" and acodec != "none":
            est_a = _estimate_format_bytes(fmt, duration, is_audio=True)
            if est_a:
                abr = float(fmt.get("abr") or fmt.get("tbr") or 0)
                audio_items.append((abr, est_a))

        if vcodec != "none" and height > 0:
            est_v = _estimate_format_bytes(fmt, duration, is_audio=False)
            if est_v:
                is_avc = 1 if str(vcodec).startswith("avc1") else 0
                has_aud = 1 if acodec != "none" else 0
                br = float(fmt.get("vbr") or fmt.get("tbr") or est_v)
                video_items.append((height, is_avc, has_aud, br, est_v))

    hints: dict[str, str] = {}

    best_audio_bytes: int | None = None
    if audio_items:
        _, best_audio_bytes = max(audio_items, key=lambda item: item[0])
        if duration and float(duration) > 0:
            mp3_bytes = min(best_audio_bytes, int(float(duration) * 192 * 1000 / 8))
        else:
            mp3_bytes = best_audio_bytes
        aud_str = _format_size_human(mp3_bytes)
        if aud_str:
            hints["audio"] = aud_str

    for q in ("1080", "720", "480", "360"):
        max_h = int(q)
        # Предпочитаем точное совпадение высоты, иначе максимальную доступную <= max_h
        eligible = [item for item in video_items if item[0] <= max_h]
        if not eligible:
            continue

        # Выбираем лучший поток под конкретный потолок разрешения:
        # сначала avc1 и ближе к max_h, затем по битрейту/размеру
        best_v = max(
            eligible,
            key=lambda item: (
                item[0],          # height (ближе к max_h)
                item[1],          # avc1 приоритет
                item[3],          # bitrate / size
            ),
        )
        _, _, has_aud, _, v_bytes = best_v
        total_bytes = v_bytes + (best_audio_bytes if (not has_aud and best_audio_bytes) else 0)
        v_str = _format_size_human(total_bytes)
        if v_str:
            hints[q] = v_str

    return hints


def _guess_image_ext(url: str, content_type: str = "") -> str:
    clean_url = url.split("?", maxsplit=1)[0].lower()
    for ext in IMAGE_EXTS:
        if clean_url.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    ct = content_type.lower()
    if "png" in ct:
        return ".png"
    if "webp" in ct:
        return ".webp"
    return ".jpg"


def _cookie_header_from_file(cookiefile: str | None) -> str | None:
    """Собирает заголовок Cookie из Netscape cookies.txt для прямых запросов к CDN."""
    if not cookiefile or not os.path.isfile(cookiefile):
        return None
    pairs: list[str] = []
    try:
        with open(cookiefile, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 7:
                    name, value = parts[5], parts[6]
                    if name:
                        pairs.append(f"{name}={value}")
    except Exception as exc:
        logger.warning("Failed to parse cookies from %s: %s", cookiefile, exc)
        return None
    return "; ".join(pairs) if pairs else None


def _download_image_file(
    url: str,
    dest_stem: str,
    impersonate: bool = False,
    cookiefile: str | None = None,
) -> Path | None:
    if not url or not url.startswith(("http://", "https://")):
        return None
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    }
    cookie_hdr = _cookie_header_from_file(cookiefile)
    if cookie_hdr:
        headers["Cookie"] = cookie_hdr

    try:
        if impersonate:
            from curl_cffi import requests as cffi_requests

            resp = cffi_requests.get(
                url,
                headers=headers,
                impersonate="chrome",
                timeout=20,
                allow_redirects=True,
            )
            if resp.status_code >= 400 or not resp.content:
                return None
            ext = _guess_image_ext(url, resp.headers.get("content-type", ""))
            out_path = DOWNLOAD_DIR / f"{dest_stem}{ext}"
            out_path.write_bytes(resp.content)
            return out_path

        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
            if not data:
                return None
            ext = _guess_image_ext(url, resp.headers.get("content-type", ""))
            out_path = DOWNLOAD_DIR / f"{dest_stem}{ext}"
            out_path.write_bytes(data)
            return out_path
    except Exception as exc:
        logger.debug("Failed to download image %s: %s", url, exc)
        return None


def _download_via_gallery_dl(url: str, file_id: str, cookiefile: str | None = None) -> list[str]:
    """Скачивает фото/карусель через gallery-dl во временную папку и возвращает список путей."""
    temp_dir = DOWNLOAD_DIR / f"{file_id}_gdl"
    temp_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "gallery_dl",
        "--no-mtime",
        "-d",
        str(temp_dir),
    ]
    if cookiefile and os.path.isfile(cookiefile):
        cmd.extend(["--cookies", cookiefile])
    cmd.append(url)

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            logger.warning(
                "gallery-dl exited with code %s for %s: %s",
                proc.returncode,
                url,
                (proc.stderr or proc.stdout or "").strip(),
            )
    except Exception as exc:
        logger.warning("gallery-dl failed for %s: %s", url, exc)
        shutil.rmtree(temp_dir, ignore_errors=True)
        return []

    matched = sorted(
        f for f in temp_dir.rglob("*")
        if f.is_file() and f.stat().st_size > 0
    )
    if not matched:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return []

    return [str(p) for p in matched[:10]]


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
    """Синхронно скачивает видео, фото/карусель или аудио и возвращает словарь с путями."""
    file_id = uuid.uuid4().hex
    outtmpl = str(DOWNLOAD_DIR / f"{file_id}.%(ext)s")
    last_update = 0.0
    use_impersonate = mode in ("instagram", "tiktok")
    active_cookies = IG_COOKIES_FILE if mode == "instagram" else COOKIES_FILE

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

    if active_cookies:
        ydl_opts["cookiefile"] = active_cookies

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
    elif mode in ("instagram", "tiktok", "vk_clip", "twitter"):
        ydl_opts.update({
            "format": "best[ext=mp4]/bestvideo[vcodec^=avc1]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
            "merge_output_format": "mp4",
        })
        if use_impersonate:
            ydl_opts["impersonate"] = ImpersonateTarget("chrome")

    info: dict = {}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            extracted = ydl.extract_info(query, download=True)
            info = (
                extracted["entries"][0]
                if isinstance(extracted, dict) and "entries" in extracted and extracted["entries"]
                else (extracted or {})
            )
    except yt_dlp.utils.DownloadError as err:
        msg = str(err)
        logger.warning("yt-dlp DownloadError for %s (mode=%s): %s", query, mode, msg)

        if mode in ("instagram", "twitter") and ("No video formats found" in msg or "gallery" in msg.lower()):
            photos = _download_via_gallery_dl(query, file_id, cookiefile=active_cookies)
            if photos:
                return {
                    "type": "photos",
                    "paths": photos,
                    "title": "Фото",
                }
            raise MediaNotFoundError(
                "Не удалось получить медиа по этой ссылке — возможно, пост недоступен или ссылка неверная."
            ) from err

        if any(k in msg for k in ("HTTP Error 404", "Not Found", "Private video", "unavailable")):
            raise MediaNotFoundError(
                "Файл не найден по этой ссылке — возможно, ссылка неверная или доступ к публикации ограничен."
            ) from err

        raise MediaNotFoundError(
            "Не удалось скачать файл по этой ссылке. Проверьте ссылку или попробуйте позже."
        ) from err

    all_files = list(DOWNLOAD_DIR.glob(f"{file_id}.*"))
    media_files = [f for f in all_files if f.suffix.lower() not in IMAGE_EXTS]
    thumb_files = [f for f in all_files if f.suffix.lower() == ".jpg"]

    if not media_files and mode in ("instagram", "twitter"):
        for f in thumb_files:
            f.unlink(missing_ok=True)
        photos = _download_via_gallery_dl(query, file_id, cookiefile=active_cookies)
        if photos:
            return {
                "type": "photos",
                "paths": photos,
                "title": info.get("title") or "Фото",
            }

    if not media_files:
        for f in all_files:
            f.unlink(missing_ok=True)
        raise MediaNotFoundError(
            f"Файл не найден по этой ссылке (возможно, ссылка неверная или превышен лимит {MAX_FILE_SIZE_MB} МБ)."
        )

    is_audio = mode == "music" or (mode in ("youtube", "vk_video") and quality == "audio")
    return {
        "type": "audio" if is_audio else "video",
        "path": str(media_files[0]),
        "thumb": str(thumb_files[0]) if thumb_files else None,
        "title": info.get("title", "media"),
        "width": info.get("width"),
        "height": info.get("height"),
        "duration": int(info["duration"]) if info.get("duration") else None,
    }
