import uuid
import yt_dlp
from bot.config import COOKIES_FILE, DOWNLOAD_DIR, MAX_FILE_SIZE_MB


def download_media(query: str, mode: str) -> tuple[str, str]:
    """Синхронно скачивает медиа и возвращает (путь_к_файлу, название)."""
    file_id = uuid.uuid4().hex
    outtmpl = str(DOWNLOAD_DIR / f"{file_id}.%(ext)s")

    ydl_opts = {
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "max_filesize": MAX_FILE_SIZE_MB * 1024 * 1024,
    }

    if COOKIES_FILE:
        ydl_opts["cookiefile"] = COOKIES_FILE

    if mode == "music":
        ydl_opts.update({
            "format": "bestaudio/best",
            "default_search": "ytsearch1",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        })
    elif mode == "youtube":
        ydl_opts.update({
            "format": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "merge_output_format": "mp4",
        })
    elif mode == "instagram":
        ydl_opts.update({
            "format": "best[ext=mp4]/best",
        })

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(query, download=True)
        if "entries" in info:
            info = info["entries"][0]
        title = info.get("title", "media")

    downloaded_files = list(DOWNLOAD_DIR.glob(f"{file_id}.*"))
    if not downloaded_files:
        raise FileNotFoundError(
            f"Файл не найден после загрузки (возможно, превышен лимит {MAX_FILE_SIZE_MB} МБ)."
        )

    return str(downloaded_files[0]), title
