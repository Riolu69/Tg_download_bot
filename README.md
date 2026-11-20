# Tg Downloader Bot

Telegram-бот для скачивания видео и аудио с YouTube, Reels/видео из Instagram и интерактивного поиска музыки в MP3. Оптимизирован для работы на Linux VPS с локальным Telegram Bot API (поддержка файлов до 2 ГБ).

## Возможности

- **YouTube** — выбор качества (`360p`, `480p`, `720p`, `1080p`) или извлечение аудио (`MP3 192 kbps`).
- **Потоковый просмотр** — передача `supports_streaming=True`, разрешения и длительности для воспроизведения видео прямо во время загрузки.
- **Instagram** — скачивание Reels, постов и видео по ссылке.
- **Поиск музыки** — по текстовому запросу бот выводит 5 вариантов с длительностью на выбор, а не скачивает первый попавшийся трек.
- **Прогресс-бар и обложки** — отображение процента загрузки и скорости в реальном времени, автоматическое прикрепление `.jpg`-превью к видео и аудио.
- **Обход блокировок YouTube на VPS** — используется клиент `mweb`, `deno` для JS-челленджей и локальный сервер PO Token (`bgutil-ytdlp-pot-provider`).

## Стек

- Python 3.10+ / [aiogram 3](https://docs.aiogram.dev/)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) + [bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider) (v2.0.2)
- `ffmpeg` — склейка потоков, конвертация в MP3 и обработка обложек
- `deno` — решение JS-челленджей YouTube
- Local Telegram Bot API Server — отправка файлов до 2 ГБ

## Структура проекта

```text
Tg_download_bot/
├── bot/
│   ├── config.py              # Конфигурация (токен, пути, лимиты, cookies)
│   ├── handlers/
│   │   └── media.py           # Обработка ссылок, поиска, кнопок и прогресс-бара
│   └── services/
│       └── downloader.py      # Обёртка над yt-dlp (поиск, скачивание, обложки)
├── cookies.txt                # Куки YouTube (не коммитить в репозиторий)
└── requirements.txt
```

## Установка на VPS (Ubuntu / Debian)

### 1. Системные зависимости

```bash
apt update && apt install -y python3-venv ffmpeg git nodejs npm curl unzip
```

Установите `deno` (нужен `yt-dlp` для решения JS-челленджей):
```bash
curl -fsSL [https://deno.land/install.sh](https://deno.land/install.sh) | sh
```

### 2. Клонирование и виртуальное окружение

```bash
git clone [https://github.com/Riolu69/Tg_download_bot.git](https://github.com/Riolu69/Tg_download_bot.git) /opt/Tg_download_bot
cd /opt/Tg_download_bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install bgutil-ytdlp-pot-provider==2.0.2
```

### 3. Настройка PO Token сервера (`bgutil`)

```bash
git clone --single-branch --branch 2.0.2 [https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git](https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git) /opt/bgutil-ytdlp-pot-provider
cd /opt/bgutil-ytdlp-pot-provider/server
npm ci
npx tsc
```

Сервер запускается на `http://127.0.0.1:4416` (рекомендуется оформить как `systemd`-сервис `bgutil-provider`). Проверка работы:
```bash
curl -s [http://127.0.0.1:4416/ping](http://127.0.0.1:4416/ping)
# {"server_uptime":...,"version":"2.0.2"}
```

### 4. Конфигурация и куки

1. Укажите токен бота, URL локального Bot API и лимит `MAX_FILE_SIZE_MB` в `bot/config.py` (или `.env`).
2. Положите экспортированный файл `cookies.txt` в `/opt/Tg_download_bot/cookies.txt` (добавлен в `.gitignore`).

### 5. Запуск и обновление

```bash
systemctl restart bgutil-provider
systemctl restart bot
```

Обновление из репозитория:
```bash
cd /opt/Tg_download_bot && git pull && systemctl restart bot
```

## Использование

- Отправьте ссылку на **YouTube** → выберите нужное разрешение или MP3.
- Отправьте ссылку на **Instagram** → бот сразу скачает видео.
- Отправьте **название трека или исполнителя** → выберите нужный вариант из 5 предложенных.
