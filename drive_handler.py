"""
drive_handler.py — Xử lý Google Drive links cho Discord Music Bot
Hỗ trợ: file đơn, folder, shared drive
Yêu cầu: link ở chế độ public ("Anyone with the link")
"""
import asyncio
import re
import os
import aiohttp
from pathlib import Path
from typing import Optional

# Regex patterns
_FILE_PATTERN   = re.compile(r"drive\.google\.com/file/d/([a-zA-Z0-9_-]+)")
_FOLDER_PATTERN = re.compile(r"drive\.google\.com/drive/folders/([a-zA-Z0-9_-]+)")
_OPEN_PATTERN   = re.compile(r"drive\.google\.com/open\?id=([a-zA-Z0-9_-]+)")

AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".wma", ".opus"}

TEMP_DIR = Path("temp_audio")
TEMP_DIR.mkdir(exist_ok=True)


# ──────────────────────────────────────────────
#  DETECT
# ──────────────────────────────────────────────

def is_drive_link(url: str) -> bool:
    """Check xem URL có phải Google Drive link không."""
    return "drive.google.com" in url


def extract_file_id(url: str) -> Optional[str]:
    """Lấy file ID từ GDrive link."""
    for pattern in (_FILE_PATTERN, _OPEN_PATTERN):
        m = pattern.search(url)
        if m:
            return m.group(1)
    return None


def extract_folder_id(url: str) -> Optional[str]:
    """Lấy folder ID từ GDrive link."""
    m = _FOLDER_PATTERN.search(url)
    return m.group(1) if m else None


def get_direct_download_url(file_id: str) -> str:
    """Convert GDrive file ID → direct download URL."""
    return f"https://drive.google.com/uc?export=download&id={file_id}&confirm=t"


def get_stream_url(file_id: str) -> str:
    """URL để stream trực tiếp qua yt-dlp."""
    return f"https://drive.google.com/file/d/{file_id}/view"


# ──────────────────────────────────────────────
#  EXTRACT
# ──────────────────────────────────────────────

async def extract_drive_file(url: str, ytdl_opts: dict, executor) -> Optional[dict]:
    """
    Extract thông tin 1 file từ Google Drive.
    Trả về track dict hoặc None nếu lỗi.
    """
    import yt_dlp

    file_id = extract_file_id(url)
    if not file_id:
        return None

    # Dùng yt-dlp để extract (yt-dlp hỗ trợ GDrive natively)
    opts = dict(ytdl_opts)
    opts["extract_flat"]  = False
    opts["noplaylist"]    = True

    ytdl = yt_dlp.YoutubeDL(opts)
    try:
        loop = asyncio.get_event_loop()
        data = await asyncio.wait_for(
            loop.run_in_executor(executor, lambda: ytdl.extract_info(url, download=False)),
            timeout=20.0
        )
        if not data:
            return None
        return {
            "title":       data.get("title", f"GDrive_{file_id[:8]}"),
            "url":         data.get("url", get_direct_download_url(file_id)),
            "webpage_url": url,
            "duration":    data.get("duration") or 0,
            "thumbnail":   data.get("thumbnail", ""),
            "uploader":    data.get("uploader", "Google Drive"),
            "source":      "gdrive",
        }
    except Exception as e:
        print(f"[DriveHandler] yt-dlp error: {e}")
        # Fallback: tạo track với direct URL
        return {
            "title":       f"GDrive File ({file_id[:8]}...)",
            "url":         get_direct_download_url(file_id),
            "webpage_url": url,
            "duration":    0,
            "thumbnail":   "",
            "uploader":    "Google Drive",
            "source":      "gdrive",
        }


async def extract_drive_folder(folder_url: str, ytdl_opts: dict, executor) -> list[dict]:
    """
    List các audio files trong Google Drive folder.
    Dùng Google Drive API public (không cần API key cho folder public).
    Trả về list track dicts.
    """
    folder_id = extract_folder_id(folder_url)
    if not folder_id:
        return []

    # Thử dùng gdown để list folder
    try:
        import gdown
        tracks = []

        loop = asyncio.get_event_loop()

        def _list_folder():
            try:
                files = gdown.download_folder(
                    id=folder_id,
                    output=str(TEMP_DIR / "gdrive_scan"),
                    quiet=True,
                    use_cookies=False,
                    skip_download=True,  # Chỉ list, không download
                )
                return files or []
            except Exception as e:
                print(f"[DriveHandler gdown list] {e}")
                return []

        # gdown.download_folder với skip_download có thể không hoạt động tất cả versions
        # Fallback: parse Google Drive folder page
        file_infos = await _fetch_folder_listing(folder_id)
        return file_infos

    except ImportError:
        # gdown không cài, dùng HTTP fallback
        return await _fetch_folder_listing(folder_id)


async def _fetch_folder_listing(folder_id: str) -> list[dict]:
    """
    Lấy danh sách file trong Google Drive folder thông qua HTTP request.
    Chỉ hoạt động với folder public.
    """
    url = f"https://drive.google.com/drive/folders/{folder_id}"
    tracks = []

    try:
        async with aiohttp.ClientSession() as session:
            headers = {"User-Agent": "Mozilla/5.0 (compatible; MusicBot/1.0)"}
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return []
                html = await resp.text()

        # Tìm file IDs trong response HTML (pattern Google Drive dùng)
        file_ids = re.findall(r'"([a-zA-Z0-9_-]{28,})"', html)
        file_ids = list(dict.fromkeys(file_ids))  # deduplicate, giữ order

        # Build tracks từ file IDs (heuristic, chỉ lấy max 50 files)
        for fid in file_ids[:50]:
            # Kiểm tra xem có phải file audio bằng cách check metadata
            # Dùng direct URL
            tracks.append({
                "title":       f"GDrive File ({fid[:8]}...)",
                "url":         get_direct_download_url(fid),
                "webpage_url": get_stream_url(fid),
                "duration":    0,
                "thumbnail":   "",
                "uploader":    "Google Drive",
                "source":      "gdrive",
            })

    except Exception as e:
        print(f"[DriveHandler folder list] {e}")

    return tracks


# ──────────────────────────────────────────────
#  FILE UPLOAD HANDLER
# ──────────────────────────────────────────────

async def handle_discord_attachment(attachment, user_id: int) -> Optional[dict]:
    """
    Download Discord attachment (audio file) về server.
    Trả về track dict hoặc None nếu không hợp lệ.
    """
    filename = attachment.filename
    ext = Path(filename).suffix.lower()

    if ext not in AUDIO_EXTENSIONS:
        raise ValueError(
            f"❌ Định dạng không hỗ trợ `{ext}`. "
            f"Chỉ nhận: {', '.join(sorted(AUDIO_EXTENSIONS))}"
        )

    if attachment.size > 50 * 1024 * 1024:  # 50MB hard limit
        raise ValueError("❌ File quá lớn (tối đa 50MB).")

    # Tạo thư mục cho user
    user_audio_dir = TEMP_DIR / str(user_id)
    user_audio_dir.mkdir(parents=True, exist_ok=True)

    # Tên file: timestamp_original để tránh trùng
    import time
    ts = int(time.time())
    safe_filename = re.sub(r'[^\w\.-]', '_', filename)
    save_path = user_audio_dir / f"{ts}_{safe_filename}"

    # Download
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(attachment.url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    raise ValueError(f"❌ Không tải được file (HTTP {resp.status}).")
                content = await resp.read()

        save_path.write_bytes(content)
    except aiohttp.ClientError as e:
        raise ValueError(f"❌ Lỗi kết nối khi tải file: {e}")

    return {
        "title":       Path(filename).stem,  # Tên file không có extension
        "url":         f"local://{save_path.absolute()}",
        "webpage_url": "",
        "duration":    0,
        "thumbnail":   "",
        "uploader":    "Local Upload",
        "source":      "local",
        "file_path":   str(save_path.absolute()),
    }


def resolve_local_url(url: str) -> str:
    """
    Convert 'local:///path/to/file' → '/path/to/file' cho FFmpeg.
    """
    if url.startswith("local://"):
        return url[len("local://"):]
    return url
