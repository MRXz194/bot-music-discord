"""
playlist_manager.py — Quản lý Playlist cá nhân cho Discord Music Bot
Lưu trữ bằng JSON file, không giới hạn số playlist/bài
"""
import json
import os
import random
import datetime
from pathlib import Path
from typing import Optional

PLAYLISTS_DIR = Path("playlists")
PLAYLISTS_DIR.mkdir(exist_ok=True)

MAX_PLAYLIST_NAME_LEN = 50
MAX_DESCRIPTION_LEN   = 200
SUPPORTED_SOURCES      = ("youtube", "soundcloud", "gdrive", "local", "url")


# ──────────────────────────────────────────────
#  HELPERS
# ──────────────────────────────────────────────

def _user_dir(user_id: int) -> Path:
    d = PLAYLISTS_DIR / str(user_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _playlist_path(user_id: int, name: str) -> Path:
    safe = _sanitize_name(name)
    return _user_dir(user_id) / f"{safe}.json"


def _sanitize_name(name: str) -> str:
    """Chuyển tên playlist thành filename-safe (giữ tiếng Việt, thay ký tự đặc biệt)."""
    import re
    # Bỏ ký tự không hợp lệ cho tên file, giữ chữ cái, số, dấu gạch ngang/dưới
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name).strip()
    safe = safe.replace(" ", "_")
    return safe[:MAX_PLAYLIST_NAME_LEN] or "playlist"


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


# ──────────────────────────────────────────────
#  READ / WRITE
# ──────────────────────────────────────────────

def _load_playlist(user_id: int, name: str) -> Optional[dict]:
    path = _playlist_path(user_id, name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_playlist(data: dict) -> None:
    user_id = data["owner_id"]
    name    = data["name"]
    path    = _playlist_path(user_id, name)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ──────────────────────────────────────────────
#  PUBLIC API
# ──────────────────────────────────────────────

class PlaylistError(Exception):
    """Exception có thể hiển thị trực tiếp cho user."""
    pass


def create_playlist(user_id: int, owner_name: str, name: str, description: str = "") -> dict:
    """Tạo playlist mới. Raise PlaylistError nếu đã tồn tại."""
    if len(name) > MAX_PLAYLIST_NAME_LEN:
        raise PlaylistError(f"❌ Tên playlist tối đa {MAX_PLAYLIST_NAME_LEN} ký tự.")
    if _playlist_path(user_id, name).exists():
        raise PlaylistError(f"❌ Playlist **{name}** đã tồn tại. Dùng tên khác hoặc xóa playlist cũ.")

    data = {
        "name":        name,
        "owner_id":    user_id,
        "owner_name":  owner_name,
        "created_at":  _now(),
        "updated_at":  _now(),
        "description": description[:MAX_DESCRIPTION_LEN],
        "tracks":      [],
    }
    _save_playlist(data)
    return data


def delete_playlist(user_id: int, name: str) -> dict:
    """Xóa playlist. Raise PlaylistError nếu không tồn tại."""
    path = _playlist_path(user_id, name)
    data = _load_playlist(user_id, name)
    if data is None:
        raise PlaylistError(f"❌ Không tìm thấy playlist **{name}**.")
    path.unlink()
    return data


def rename_playlist(user_id: int, old_name: str, new_name: str) -> dict:
    """Đổi tên playlist. Raise PlaylistError nếu lỗi."""
    data = _load_playlist(user_id, old_name)
    if data is None:
        raise PlaylistError(f"❌ Không tìm thấy playlist **{old_name}**.")
    if _playlist_path(user_id, new_name).exists():
        raise PlaylistError(f"❌ Playlist **{new_name}** đã tồn tại.")
    if len(new_name) > MAX_PLAYLIST_NAME_LEN:
        raise PlaylistError(f"❌ Tên tối đa {MAX_PLAYLIST_NAME_LEN} ký tự.")

    # Xóa file cũ, lưu file mới
    _playlist_path(user_id, old_name).unlink()
    data["name"]       = new_name
    data["updated_at"] = _now()
    _save_playlist(data)
    return data


def list_playlists(user_id: int) -> list[dict]:
    """Trả về list metadata (không kèm tracks) của user."""
    user_dir = PLAYLISTS_DIR / str(user_id)
    if not user_dir.exists():
        return []
    result = []
    for f in sorted(user_dir.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            result.append({
                "name":        d.get("name", f.stem),
                "description": d.get("description", ""),
                "track_count": len(d.get("tracks", [])),
                "created_at":  d.get("created_at", ""),
                "updated_at":  d.get("updated_at", ""),
            })
        except Exception:
            pass
    return result


def get_playlist(user_id: int, name: str) -> dict:
    """Lấy đầy đủ dữ liệu playlist. Raise PlaylistError nếu không tìm thấy."""
    data = _load_playlist(user_id, name)
    if data is None:
        raise PlaylistError(f"❌ Không tìm thấy playlist **{name}**.")
    return data


def add_track(user_id: int, name: str, track: dict) -> dict:
    """Thêm 1 track vào playlist. Trả về playlist đã cập nhật."""
    data = get_playlist(user_id, name)
    data["tracks"].append(track)
    data["updated_at"] = _now()
    _save_playlist(data)
    return data


def add_tracks(user_id: int, name: str, tracks: list[dict]) -> dict:
    """Thêm nhiều tracks cùng lúc."""
    data = get_playlist(user_id, name)
    data["tracks"].extend(tracks)
    data["updated_at"] = _now()
    _save_playlist(data)
    return data


def remove_track(user_id: int, name: str, index: int) -> tuple[dict, dict]:
    """
    Xóa track theo 1-based index.
    Trả về (playlist_data, removed_track).
    """
    data = get_playlist(user_id, name)
    tracks = data["tracks"]
    if not (1 <= index <= len(tracks)):
        raise PlaylistError(f"❌ Số thứ tự không hợp lệ (playlist có {len(tracks)} bài).")
    removed = tracks.pop(index - 1)
    data["updated_at"] = _now()
    _save_playlist(data)
    return data, removed


def remove_track_range(user_id: int, name: str, start: int, end: int) -> tuple[dict, list]:
    """
    Xóa khoảng track theo 1-based index (inclusive).
    Trả về (playlist_data, list_removed).
    """
    data = get_playlist(user_id, name)
    tracks = data["tracks"]
    n = len(tracks)
    if start > end:
        start, end = end, start
    if not (1 <= start <= n) or not (1 <= end <= n):
        raise PlaylistError(f"❌ Khoảng không hợp lệ (playlist có {n} bài).")
    removed = tracks[start - 1:end]
    del tracks[start - 1:end]
    data["updated_at"] = _now()
    _save_playlist(data)
    return data, removed


def shuffle_playlist(user_id: int, name: str) -> dict:
    """Xáo trộn thứ tự track trong playlist."""
    data = get_playlist(user_id, name)
    random.shuffle(data["tracks"])
    data["updated_at"] = _now()
    _save_playlist(data)
    return data


def clone_playlist(from_user_id: int, from_name: str,
                   to_user_id: int, to_owner_name: str,
                   new_name: Optional[str] = None) -> dict:
    """
    Clone playlist từ user khác về user hiện tại.
    Nếu new_name không cung cấp, dùng tên gốc.
    """
    source = get_playlist(from_user_id, from_name)
    target_name = new_name or source["name"]

    if _playlist_path(to_user_id, target_name).exists():
        raise PlaylistError(f"❌ Bạn đã có playlist **{target_name}**. Dùng tên khác.")

    new_data = {
        "name":        target_name,
        "owner_id":    to_user_id,
        "owner_name":  to_owner_name,
        "created_at":  _now(),
        "updated_at":  _now(),
        "description": f"(Clone từ {source['owner_name']}) " + source.get("description", ""),
        "tracks":      list(source["tracks"]),  # deep copy simple dicts
    }
    _save_playlist(new_data)
    return new_data


def save_queue_as(user_id: int, owner_name: str, name: str, songs: list) -> dict:
    """
    Lưu list Song objects thành playlist mới.
    songs: list of Song objects có attributes title, webpage_url, url, duration, thumbnail, uploader
    """
    if _playlist_path(user_id, name).exists():
        raise PlaylistError(f"❌ Playlist **{name}** đã tồn tại. Chọn tên khác hoặc xóa playlist cũ trước.")

    tracks = []
    for s in songs:
        tracks.append({
            "title":       s.title,
            "url":         s.url,
            "webpage_url": s.webpage_url,
            "duration":    s.duration,
            "thumbnail":   s.thumbnail,
            "uploader":    s.uploader,
            "source":      _detect_source(s.webpage_url or s.url),
        })

    data = {
        "name":        name,
        "owner_id":    user_id,
        "owner_name":  owner_name,
        "created_at":  _now(),
        "updated_at":  _now(),
        "description": f"Lưu từ queue lúc {_now()}",
        "tracks":      tracks,
    }
    _save_playlist(data)
    return data


def _detect_source(url: str) -> str:
    if not url:
        return "url"
    url_lower = url.lower()
    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "youtube"
    if "soundcloud.com" in url_lower:
        return "soundcloud"
    if "drive.google.com" in url_lower:
        return "gdrive"
    if url.startswith("local://") or url.startswith("/") or (len(url) > 1 and url[1] == ":"):
        return "local"
    return "url"


def build_track_from_ytdl(data: dict, source_override: Optional[str] = None) -> dict:
    """Convert yt-dlp info dict → track dict cho playlist."""
    url = data.get("url", "") or data.get("webpage_url", "")
    webpage_url = data.get("webpage_url", "") or url
    source = source_override or _detect_source(webpage_url)
    return {
        "title":       data.get("title", "Unknown"),
        "url":         url,
        "webpage_url": webpage_url,
        "duration":    data.get("duration") or 0,
        "thumbnail":   data.get("thumbnail", ""),
        "uploader":    data.get("uploader", "Unknown"),
        "source":      source,
    }


def build_local_track(file_path: str, title: str = "") -> dict:
    """Build track dict cho file local."""
    name = title or os.path.basename(file_path)
    return {
        "title":       name,
        "url":         f"local://{file_path}",
        "webpage_url": "",
        "duration":    0,
        "thumbnail":   "",
        "uploader":    "Local File",
        "source":      "local",
        "file_path":   file_path,
    }


def source_emoji(source: str) -> str:
    """Trả về emoji tương ứng với nguồn nhạc."""
    return {
        "youtube":    "🎵",
        "soundcloud": "🔊",
        "gdrive":     "☁️",
        "local":      "📁",
        "url":        "🌐",
    }.get(source, "🎵")


def total_duration(tracks: list[dict]) -> int:
    """Tổng thời lượng (giây)."""
    return sum(t.get("duration") or 0 for t in tracks)


def format_duration(seconds: int) -> str:
    if seconds <= 0:
        return "∞"
    import datetime as dt
    return str(dt.timedelta(seconds=seconds))
