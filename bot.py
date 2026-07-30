# ============================================================
#  🎵 Discord Music Bot v3.0 — Playlist Edition
#  Stack : discord.py 2.x + yt-dlp + FFmpeg
#  Features: Personal Playlists, GDrive, File Upload, UI/UX v2
# ============================================================
import os, asyncio, random, datetime, discord, re
from discord.ext import commands
from dotenv import load_dotenv
from pathlib import Path
import yt_dlp

import playlist_manager as pm
import drive_handler    as dh

load_dotenv()

# ─────────────────────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────────────────────
PREFIX = os.getenv("PREFIX", "!")

def get_prefix(bot, msg):
    base = [""] if not PREFIX else [PREFIX]
    return commands.when_mentioned_or(*base)(bot, msg)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix=get_prefix, intents=intents, help_command=None)

# ─────────────────────────────────────────────────────────────
#  YTDL / FFMPEG
# ─────────────────────────────────────────────────────────────
COOKIE_FILE = "/app/cookies.txt" if os.path.exists("/app/cookies.txt") \
              else ("cookies.txt" if os.path.exists("cookies.txt") else None)

YTDL_OPTS = {
    "format":              "bestaudio/best",
    "noplaylist":          False,
    "nocheckcertificate":  True,
    "ignoreerrors":        False,
    "quiet":               True,
    "no_warnings":         True,
    "default_search":      "ytsearch",
    "source_address":      "0.0.0.0",
    "socket_timeout":      15,
    "retries":             3,
    "extract_flat":        "in_playlist",
    "extractor_args":      {"youtube": {"skip": ["dash", "hls"], "player_client": ["ios", "android"]}},
}

if COOKIE_FILE:
    YTDL_OPTS["cookiefile"] = COOKIE_FILE
    print(f"🍪 Đã cấu hình Cookies từ file: {COOKIE_FILE}")

FFMPEG_OPTS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 10 "
                      "-probesize 10M -thread_queue_size 4096",
    "options": "-vn -sn -dn -b:a 128k",
}


def _find_ffmpeg():
    import shutil, subprocess, glob
    found = shutil.which("ffmpeg")
    if found: return found
    env_path = os.getenv("FFMPEG_PATH", "")
    if env_path and os.path.isfile(env_path): return env_path
    if os.name == 'nt':
        try:
            res = subprocess.run(["where.exe", "ffmpeg"], capture_output=True, text=True, timeout=3)
            if res.returncode == 0: return res.stdout.strip().splitlines()[0]
        except: pass
        common = [
            r"C:\Program Files\FFmpeg\bin\ffmpeg.exe",
            r"C:\ffmpeg\bin\ffmpeg.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe"),
            *glob.glob(os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\ffmpeg*\bin\ffmpeg.exe")),
        ]
        for p in common:
            if os.path.isfile(p): return p
    return "ffmpeg"


FFMPEG_EXE = _find_ffmpeg()
if FFMPEG_EXE != "ffmpeg":
    FFMPEG_OPTS["executable"] = FFMPEG_EXE
    print(f"   FFmpeg : {FFMPEG_EXE}")

# ─────────────────────────────────────────────────────────────
#  SONG CLASS
# ─────────────────────────────────────────────────────────────
class Song:
    def __init__(self, data: dict, requester: discord.Member):
        self.url         = data.get("url", "")
        self.webpage_url = data.get("webpage_url", data.get("url", ""))
        self.title       = data.get("title", "Unknown")
        self.duration    = data.get("duration") or 0
        self.thumbnail   = data.get("thumbnail", "")
        self.uploader    = data.get("uploader", "Unknown")
        self.requester   = requester
        self.source      = data.get("source", pm._detect_source(self.webpage_url or self.url))
        self.file_path   = data.get("file_path", "")

    @property
    def duration_fmt(self):
        if not self.duration: return "∞"
        return str(datetime.timedelta(seconds=int(self.duration)))

    @property
    def source_emoji(self):
        return pm.source_emoji(self.source)

    def embed(self, position=0, total=0, paused=False) -> discord.Embed:
        status = "⏸ Đang tạm dừng" if paused else "🎵 Đang phát"
        color  = 0xFFA500 if paused else 0x1DB954
        e = discord.Embed(
            title=status,
            description=f"**[{self.title}]({self.webpage_url or 'https://discord.com'})**",
            color=color,
        )
        if self.thumbnail:
            e.set_thumbnail(url=self.thumbnail)
        e.add_field(name="⏱ Thời lượng",    value=self.duration_fmt,      inline=True)
        e.add_field(name="👤 Uploader",      value=self.uploader[:50],     inline=True)
        e.add_field(name="🙋 Yêu cầu bởi",  value=self.requester.mention, inline=True)
        e.add_field(name="📡 Nguồn",
                    value=f"{self.source_emoji} {self.source.capitalize()}", inline=True)
        if total > 0:
            e.add_field(name="📋 Vị trí", value=f"{position}/{total}", inline=True)
        return e


# ─────────────────────────────────────────────────────────────
#  LOOP MODES
# ─────────────────────────────────────────────────────────────
LOOP_OFF      = 0
LOOP_PLAYLIST = 1
LOOP_SINGLE   = 2

# ─────────────────────────────────────────────────────────────
#  GUILD STATE
# ─────────────────────────────────────────────────────────────
class GuildState:
    def __init__(self, guild):
        self.guild        = guild
        self.queue:       list[Song] = []
        self.history:     list[Song] = []
        self.current:     Song | None = None
        self.loop         = LOOP_OFF
        self.volume       = 0.5
        self.voice:       discord.VoiceClient | None = None
        self._adv         = False
        self.play_count   = 0
        self.error_streak = 0

    def playing(self): return self.voice is not None and self.voice.is_playing()
    def paused(self):  return self.voice is not None and self.voice.is_paused()

    def loop_label(self):
        if self.loop == LOOP_PLAYLIST: return "🔁 Playlist"
        if self.loop == LOOP_SINGLE:   return "🔂 Single"
        return "❌ OFF"

    def reset(self):
        self.queue.clear(); self.history.clear()
        self.current = None; self._adv = False
        self.play_count = 0; self.error_streak = 0

_states: dict[int, GuildState] = {}
def gs(guild) -> GuildState:
    if guild.id not in _states: _states[guild.id] = GuildState(guild)
    return _states[guild.id]

# ─────────────────────────────────────────────────────────────
#  FETCH & AUDIO
# ─────────────────────────────────────────────────────────────
async def fetch_info(query: str) -> list[dict]:
    opts = dict(YTDL_OPTS)
    if not query.startswith("http") and "soundcloud" in query.lower():
        opts["default_search"] = "scsearch"
    ytdl = yt_dlp.YoutubeDL(opts)
    try:
        data = await asyncio.wait_for(
            bot.loop.run_in_executor(None, lambda: ytdl.extract_info(query, download=False)),
            timeout=60.0
        )
    except asyncio.TimeoutError:
        raise commands.CommandError("⏳ Tìm kiếm quá lâu! Thử dùng URL trực tiếp.")
    if not data: return []
    entries = data.get("entries", [data]) if "entries" in data else [data]
    return [e for e in entries if e]


async def refresh_song(song: Song) -> bool:
    """Re-extract fresh stream URL for a song. Returns True on success."""
    # Local file — không cần refresh
    if song.source == "local" or (song.url and song.url.startswith("local://")):
        return True
    try:
        opts = dict(YTDL_OPTS)
        opts["extract_flat"] = False
        opts["noplaylist"]   = True
        ytdl = yt_dlp.YoutubeDL(opts)
        data = await asyncio.wait_for(
            bot.loop.run_in_executor(
                None,
                lambda: ytdl.extract_info(song.webpage_url or song.url, download=False)
            ),
            timeout=20.0
        )
        if data and data.get("url"):
            song.url       = data.get("url", song.url)
            song.title     = data.get("title", song.title) or "Unknown"
            song.duration  = data.get("duration") or song.duration
            song.thumbnail = data.get("thumbnail", song.thumbnail)
            song.uploader  = data.get("uploader", song.uploader) or "Unknown"
            return True
        return False
    except Exception as e:
        print(f"[Refresh Error] {e}")
        return False


def make_source(song: Song, volume: float) -> discord.PCMVolumeTransformer:
    url = song.url
    # Resolve local file path
    if song.source == "local" or url.startswith("local://"):
        url = dh.resolve_local_url(url)
        # Local files: không cần reconnect options
        local_opts = dict(FFMPEG_OPTS)
        local_opts["before_options"] = "-thread_queue_size 4096"
        return discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(url, **local_opts), volume=volume
        )
    return discord.PCMVolumeTransformer(
        discord.FFmpegPCMAudio(url, **FFMPEG_OPTS), volume=volume
    )

# ─────────────────────────────────────────────────────────────
#  PLAYER VIEW (Persistent Buttons — không timeout)
# ─────────────────────────────────────────────────────────────
class PlayerView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="⏸", style=discord.ButtonStyle.secondary, custom_id="v3_pause")
    async def btn_pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = gs(interaction.guild)
        if state.playing():
            state.voice.pause()
            button.label = "▶"
            button.style = discord.ButtonStyle.success
            await interaction.response.edit_message(view=self)
        elif state.paused():
            state.voice.resume()
            button.label = "⏸"
            button.style = discord.ButtonStyle.secondary
            await interaction.response.edit_message(view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="⏭", style=discord.ButtonStyle.primary, custom_id="v3_skip")
    async def btn_skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = gs(interaction.guild)
        if state.playing() or state.paused():
            state.voice.stop()
            await interaction.response.send_message(
                embed=discord.Embed(description="⏭️ Đã skip.", color=0x1DB954), delete_after=4
            )
        else:
            await interaction.response.defer()

    @discord.ui.button(label="⏹", style=discord.ButtonStyle.danger, custom_id="v3_stop")
    async def btn_stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = gs(interaction.guild)
        state.reset()
        if state.voice and state.voice.is_connected():
            state.voice.stop()
        await interaction.response.send_message(
            embed=discord.Embed(description="⏹️ Đã dừng và xóa queue.", color=0xFF6B6B), delete_after=5
        )

    @discord.ui.button(label="🔀", style=discord.ButtonStyle.secondary, custom_id="v3_shuffle")
    async def btn_shuffle(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = gs(interaction.guild)
        if state.queue:
            random.shuffle(state.queue)
            if state.loop == LOOP_PLAYLIST and state.history:
                random.shuffle(state.history)
            await interaction.response.send_message(
                embed=discord.Embed(description="🔀 Queue đã được xáo trộn!", color=0x1DB954), delete_after=4
            )
        else:
            await interaction.response.defer()

    @discord.ui.button(label="🔁", style=discord.ButtonStyle.secondary, custom_id="v3_loop")
    async def btn_loop(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = gs(interaction.guild)
        if state.loop == LOOP_OFF:
            state.loop = LOOP_PLAYLIST
            state.history.clear()
            if state.current: state.history.append(state.current)
            button.label = "🔁"
            button.style = discord.ButtonStyle.success
            desc = "🔁 Loop **Playlist** bật."
        elif state.loop == LOOP_PLAYLIST:
            state.loop = LOOP_SINGLE
            button.label = "🔂"
            button.style = discord.ButtonStyle.primary
            desc = "🔂 Loop **Single** bật."
        else:
            state.loop = LOOP_OFF
            state.history.clear()
            button.label = "🔁"
            button.style = discord.ButtonStyle.secondary
            desc = "➡️ Loop **tắt**."
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(
            embed=discord.Embed(description=desc, color=0x1DB954), ephemeral=True
        )


# ─────────────────────────────────────────────────────────────
#  QUEUE VIEW (Phân trang bằng buttons)
# ─────────────────────────────────────────────────────────────
class QueueView(discord.ui.View):
    PER_PAGE = 10

    def __init__(self, state: GuildState, page: int = 1):
        super().__init__(timeout=120)
        self.state    = state
        self.page     = page
        self._update_buttons()

    def _total_pages(self):
        return max(1, (len(self.state.queue) + self.PER_PAGE - 1) // self.PER_PAGE)

    def _update_buttons(self):
        total = self._total_pages()
        self.btn_prev.disabled = (self.page <= 1)
        self.btn_next.disabled = (self.page >= total)
        self.btn_page.label    = f"📄 {self.page}/{total}"

    def build_embed(self) -> discord.Embed:
        state = self.state
        lines = []
        if state.current:
            icon = "⏸" if state.paused() else "🎵"
            lines.append(
                f"{icon} **Đang phát:** [{state.current.title[:60]}]"
                f"({state.current.webpage_url or 'https://discord.com'}) "
                f"`[{state.current.duration_fmt}]` {state.current.source_emoji}"
            )

        start = (self.page - 1) * self.PER_PAGE
        end   = start + self.PER_PAGE
        for i, s in enumerate(state.queue[start:end], start + 1):
            link = s.webpage_url or "https://discord.com"
            lines.append(
                f"`{i:>3}.` [{s.title[:55]}]({link}) `[{s.duration_fmt}]` {s.source_emoji}"
            )

        if not state.queue:
            lines.append("_Queue trống — thêm bài bằng lệnh play_")

        total = pm.total_duration([{"duration": s.duration} for s in state.queue])
        e = discord.Embed(
            title=f"📋 Queue — {len(state.queue)} bài đang chờ",
            description="\n".join(lines),
            color=0x1DB954,
        )
        e.set_footer(
            text=f"Trang {self.page}/{self._total_pages()}  |  "
                 f"Loop: {state.loop_label()}  |  "
                 f"🔊 Vol: {int(state.volume*100)}%  |  "
                 f"⏱ Tổng: {pm.format_duration(total)}"
        )
        return e

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def btn_prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 1:
            self.page -= 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="📄 1/1", style=discord.ButtonStyle.secondary, disabled=True)
    async def btn_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def btn_next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page < self._total_pages():
            self.page += 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


# ─────────────────────────────────────────────────────────────
#  PLAYLIST DETAIL VIEW (phân trang có nút Load/Delete)
# ─────────────────────────────────────────────────────────────
class PlaylistDetailView(discord.ui.View):
    PER_PAGE = 10

    def __init__(self, pl_data: dict, author: discord.Member):
        super().__init__(timeout=180)
        self.pl_data  = pl_data
        self.author   = author
        self.page     = 1
        self._update_buttons()

    def _total_pages(self):
        return max(1, (len(self.pl_data["tracks"]) + self.PER_PAGE - 1) // self.PER_PAGE)

    def _update_buttons(self):
        total = self._total_pages()
        self.btn_prev.disabled = (self.page <= 1)
        self.btn_next.disabled = (self.page >= total)
        self.btn_page.label    = f"📄 {self.page}/{total}"
        # Chỉ owner mới thấy nút xóa (ẩn nếu không phải owner)
        self.btn_delete.disabled = (self.pl_data["owner_id"] != self.author.id)

    def build_embed(self) -> discord.Embed:
        pl    = self.pl_data
        tracks = pl["tracks"]
        lines = []
        start = (self.page - 1) * self.PER_PAGE
        end   = start + self.PER_PAGE

        for i, t in enumerate(tracks[start:end], start + 1):
            dur  = pm.format_duration(t.get("duration") or 0)
            src  = pm.source_emoji(t.get("source", "url"))
            link = t.get("webpage_url") or t.get("url") or "https://discord.com"
            lines.append(f"`{i:>3}.` [{t['title'][:55]}]({link}) `[{dur}]` {src}")

        if not tracks:
            lines.append("_Playlist trống — thêm bài bằng `pl add <tên> <url/file>`_")

        total_dur = pm.total_duration(tracks)
        e = discord.Embed(
            title=f"📂 Playlist: **{pl['name']}**",
            description="\n".join(lines) if lines else "_Trống_",
            color=0x5865F2,
        )
        if pl.get("description"):
            e.add_field(name="📝 Mô tả", value=pl["description"], inline=False)
        e.add_field(name="🎵 Số bài",   value=str(len(tracks)),              inline=True)
        e.add_field(name="⏱ Tổng TG",  value=pm.format_duration(total_dur), inline=True)
        e.add_field(name="👤 Chủ sở hữu", value=pl["owner_name"],           inline=True)
        e.set_footer(text=f"Trang {self.page}/{self._total_pages()} | Cập nhật: {pl.get('updated_at','')[:10]}")
        return e

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def btn_prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 1: self.page -= 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="📄 1/1", style=discord.ButtonStyle.secondary, disabled=True)
    async def btn_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def btn_next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page < self._total_pages(): self.page += 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="▶️ Load", style=discord.ButtonStyle.success)
    async def btn_load(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.author:
            return await interaction.response.send_message(
                "❌ Chỉ người dùng lệnh mới có thể load.", ephemeral=True
            )
        await interaction.response.defer()
        await _load_playlist_to_queue(interaction, self.pl_data, shuffle=False)

    @discord.ui.button(label="🔀 Shuffle Load", style=discord.ButtonStyle.primary)
    async def btn_shuffle_load(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.author:
            return await interaction.response.send_message(
                "❌ Chỉ người dùng lệnh mới có thể load.", ephemeral=True
            )
        await interaction.response.defer()
        await _load_playlist_to_queue(interaction, self.pl_data, shuffle=True)

    @discord.ui.button(label="🗑️ Xóa", style=discord.ButtonStyle.danger)
    async def btn_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.pl_data["owner_id"]:
            return await interaction.response.send_message(
                "❌ Chỉ chủ playlist mới có thể xóa.", ephemeral=True
            )
        view = ConfirmView(interaction.user)
        await interaction.response.send_message(
            embed=discord.Embed(
                description=f"⚠️ Xóa playlist **{self.pl_data['name']}**? ({len(self.pl_data['tracks'])} bài)",
                color=0xFF6B6B
            ),
            view=view, ephemeral=True
        )
        await view.wait()
        if view.confirmed:
            try:
                pm.delete_playlist(self.pl_data["owner_id"], self.pl_data["name"])
                await interaction.edit_original_response(
                    embed=discord.Embed(description="🗑️ Đã xóa playlist!", color=0xFF6B6B),
                    view=None
                )
            except pm.PlaylistError as e:
                await interaction.edit_original_response(
                    embed=discord.Embed(description=str(e), color=0xFF6B6B), view=None
                )


# ─────────────────────────────────────────────────────────────
#  PLAYLIST LIST VIEW (dropdown chọn playlist)
# ─────────────────────────────────────────────────────────────
class PlaylistListDropdown(discord.ui.Select):
    def __init__(self, playlists: list[dict], target_user_id: int, author: discord.Member):
        self.all_playlists = playlists
        self.target_user_id = target_user_id
        self.author = author

        options = []
        for pl in playlists[:25]:
            label = pl["name"][:80]
            desc  = f"🎵 {pl['track_count']} bài"
            if pl.get("description"):
                desc += f" | {pl['description'][:50]}"
            options.append(discord.SelectOption(label=label, value=pl["name"], description=desc))

        super().__init__(
            placeholder="Chọn playlist để xem chi tiết…",
            min_values=1, max_values=1, options=options
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user != self.author:
            return await interaction.response.send_message(
                "❌ Bạn không phải người dùng lệnh này.", ephemeral=True
            )
        name = self.values[0]
        try:
            pl_data = pm.get_playlist(self.target_user_id, name)
        except pm.PlaylistError as e:
            return await interaction.response.send_message(str(e), ephemeral=True)

        view  = PlaylistDetailView(pl_data, interaction.user)
        await interaction.response.edit_message(embed=view.build_embed(), view=view)


class PlaylistListView(discord.ui.View):
    def __init__(self, playlists: list[dict], target_user_id: int, author: discord.Member):
        super().__init__(timeout=180)
        self.add_item(PlaylistListDropdown(playlists, target_user_id, author))


# ─────────────────────────────────────────────────────────────
#  SEARCH DROPDOWN
# ─────────────────────────────────────────────────────────────
class SearchDropdown(discord.ui.Select):
    def __init__(self, entries, author: discord.Member):
        self.entries = entries
        self.author  = author
        options = []
        for i, e in enumerate(entries[:5]):
            title    = e.get("title", "Unknown")[:90]
            duration = str(datetime.timedelta(seconds=int(e.get("duration") or 0))) if e.get("duration") else "∞"
            uploader = (e.get("uploader") or "Unknown")[:40]
            options.append(discord.SelectOption(
                label=f"{i+1}. {title}",
                description=f"⏱ {duration} | 👤 {uploader}",
                value=str(i)
            ))
        super().__init__(placeholder="Chọn bài hát…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user != self.author:
            return await interaction.response.send_message(
                "❌ Bạn không phải người tìm kiếm!", ephemeral=True
            )
        idx   = int(self.values[0])
        entry = self.entries[idx]
        song  = Song(entry, self.author)
        state = gs(interaction.guild)
        state.queue.append(song)

        e = discord.Embed(
            description=f"✅ Đã thêm: **[{song.title}]({song.webpage_url or 'https://discord.com'})**",
            color=0x1DB954
        )
        e.add_field(name="⏱", value=song.duration_fmt)
        e.add_field(name="📋 Queue", value=f"{len(state.queue)} bài")
        await interaction.message.edit(content=None, embed=e, view=None)
        await interaction.response.defer()
        if not state.playing() and not state.paused():
            vc = interaction.guild.voice_client
            if vc and vc.is_connected():
                await advance(interaction.guild, interaction.channel)


class SearchViewUI(discord.ui.View):
    def __init__(self, entries, author: discord.Member):
        super().__init__(timeout=60)
        self.add_item(SearchDropdown(entries, author))


# ─────────────────────────────────────────────────────────────
#  CONFIRM VIEW (dùng cho xóa queue, xóa playlist...)
# ─────────────────────────────────────────────────────────────
class ConfirmView(discord.ui.View):
    def __init__(self, author: discord.Member):
        super().__init__(timeout=30)
        self.author    = author
        self.confirmed = False

    @discord.ui.button(label="✅ Xác nhận", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.author:
            return await interaction.response.send_message("❌ Không phải bạn.", ephemeral=True)
        self.confirmed = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="❌ Hủy", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.author:
            return await interaction.response.send_message("❌ Không phải bạn.", ephemeral=True)
        self.confirmed = False
        self.stop()
        await interaction.response.send_message(
            embed=discord.Embed(description="✅ Đã hủy.", color=0xAAAAAA), ephemeral=True
        )


# ─────────────────────────────────────────────────────────────
#  ADVANCE QUEUE (engine phát nhạc)
# ─────────────────────────────────────────────────────────────
async def advance(guild: discord.Guild, ch: discord.TextChannel):
    state = gs(guild)
    if state._adv: return
    state._adv = True
    try:
        vc = state.voice or guild.voice_client
        for _ in range(6):
            if vc and vc.is_connected(): break
            await asyncio.sleep(0.5)
        if not vc or not vc.is_connected():
            state.current = None; state._adv = False; return
        state.voice = vc

        # Chọn bài tiếp theo
        if state.loop == LOOP_SINGLE and state.current:
            song = state.current
        elif state.queue:
            song = state.queue.pop(0)
        elif state.loop == LOOP_PLAYLIST and state.history:
            state.queue = list(state.history); state.history.clear()
            song = state.queue.pop(0)
            try: await ch.send(embed=discord.Embed(description="🔁 Loop — Phát lại từ đầu!", color=0x1DB954))
            except: pass
        else:
            state.current = None
            try: await ch.send(embed=discord.Embed(description="✅ Hết nhạc. Thêm bài vào nào!", color=0x1DB954))
            except: pass
            state._adv = False; return

        # Refresh stream URL
        ok = await refresh_song(song)
        if not ok:
            state.error_streak += 1
            if state.error_streak >= 3:
                state.queue.insert(0, song)
                try: await ch.send(embed=discord.Embed(
                    description="❌ Lỗi 3 lần liên tiếp. Chờ 15 giây rồi thử lại (queue không mất).",
                    color=0xFF6B6B
                ))
                except: pass
                state._adv = False
                await asyncio.sleep(15)
                asyncio.ensure_future(advance(guild, ch))
                return
            else:
                try: await ch.send(embed=discord.Embed(
                    description=f"❌ Không tải được **{song.title[:50]}**, bỏ qua…",
                    color=0xFF6B6B
                ))
                except: pass
                state._adv = False
                await asyncio.sleep(2)
                asyncio.ensure_future(advance(guild, ch))
                return

        state.error_streak = 0
        state.current      = song
        state.play_count  += 1
        if state.loop == LOOP_PLAYLIST: state.history.append(song)

        pos = len(state.history) if state.loop == LOOP_PLAYLIST else state.play_count
        tot = (len(state.history) + len(state.queue)) if state.loop == LOOP_PLAYLIST \
              else (state.play_count - 1 + 1 + len(state.queue))

        def after(err):
            if err: print(f"[Player] {err}")
            state._adv = False
            asyncio.run_coroutine_threadsafe(advance(guild, ch), bot.loop)

        src = make_source(song, state.volume)
        if state.voice and state.voice.is_connected():
            state.voice.play(src, after=after)
            view = PlayerView()
            try: await ch.send(embed=song.embed(pos, tot), view=view)
            except: pass
        else:
            state._adv = False
    except Exception as e:
        print(f"[Advance] {e}")
        state._adv = False
        await asyncio.sleep(2)
        asyncio.ensure_future(advance(guild, ch))


# ─────────────────────────────────────────────────────────────
#  VOICE HELPER
# ─────────────────────────────────────────────────────────────
async def ensure_voice(ctx) -> discord.VoiceClient:
    if not ctx.author.voice:
        raise commands.CommandError("❌ Bạn chưa vào voice channel nào!")
    target = ctx.author.voice.channel
    state  = gs(ctx.guild)
    vc     = ctx.guild.voice_client
    if vc:
        if vc.is_connected():
            state.voice = vc
            if vc.channel != target: await vc.move_to(target)
            return state.voice
        else:
            try: await vc.disconnect(force=True)
            except: pass
            await asyncio.sleep(1)
    state.voice = await target.connect(timeout=20.0, reconnect=True, self_deaf=True)
    return state.voice


# ─────────────────────────────────────────────────────────────
#  HELPER: Load playlist → queue
# ─────────────────────────────────────────────────────────────
async def _load_playlist_to_queue(ctx_or_interaction, pl_data: dict, shuffle: bool = False):
    """Thêm toàn bộ playlist vào queue và bắt đầu phát."""
    tracks = list(pl_data["tracks"])
    if not tracks:
        msg = discord.Embed(description="❌ Playlist trống, không có bài nào.", color=0xFF6B6B)
        if isinstance(ctx_or_interaction, discord.Interaction):
            await ctx_or_interaction.followup.send(embed=msg)
        else:
            await ctx_or_interaction.send(embed=msg)
        return

    if shuffle:
        random.shuffle(tracks)

    # Xác định requester
    if isinstance(ctx_or_interaction, discord.Interaction):
        requester = ctx_or_interaction.user
        guild     = ctx_or_interaction.guild
        channel   = ctx_or_interaction.channel
    else:
        requester = ctx_or_interaction.author
        guild     = ctx_or_interaction.guild
        channel   = ctx_or_interaction.channel

    state = gs(guild)
    songs = [Song(t, requester) for t in tracks]
    state.queue.extend(songs)

    e = discord.Embed(
        description=f"✅ Đã load **{len(songs)} bài** từ playlist **{pl_data['name']}**"
                    f"{'(🔀 đã shuffle)' if shuffle else ''} vào queue.",
        color=0x1DB954
    )
    e.add_field(name="📋 Queue", value=f"{len(state.queue)} bài tổng cộng")
    e.add_field(name="👤 Chủ playlist", value=pl_data["owner_name"])
    if isinstance(ctx_or_interaction, discord.Interaction):
        await ctx_or_interaction.followup.send(embed=e)
    else:
        await ctx_or_interaction.send(embed=e)

    if not state.playing() and not state.paused():
        vc = guild.voice_client
        if vc and vc.is_connected():
            await advance(guild, channel)


# ─────────────────────────────────────────────────────────────
#  ═══════════════════════════════════════
#  MUSIC COMMANDS
#  ═══════════════════════════════════════
# ─────────────────────────────────────────────────────────────

@bot.command(name="search", aliases=["find"])
@commands.cooldown(1, 4, commands.BucketType.user)
async def cmd_search(ctx, *, query: str):
    """Tìm nhạc và chọn bằng dropdown."""
    await ensure_voice(ctx)
    msg = await ctx.send(embed=discord.Embed(
        description=f"🔍 Đang tìm 5 kết quả cho: `{query}`…", color=0x1DB954
    ))
    opts = dict(YTDL_OPTS); opts["extract_flat"] = True
    ytdl = yt_dlp.YoutubeDL(opts)
    try:
        data = await asyncio.wait_for(
            bot.loop.run_in_executor(None, lambda: ytdl.extract_info(f"ytsearch5:{query}", download=False)),
            timeout=15.0
        )
    except Exception as e:
        return await msg.edit(embed=discord.Embed(description=str(e), color=0xFF6B6B))
    if not data or "entries" not in data or not data["entries"]:
        return await msg.edit(embed=discord.Embed(description="❌ Không tìm thấy kết quả.", color=0xFF6B6B))
    view = SearchViewUI(data["entries"], ctx.author)
    await msg.edit(embed=discord.Embed(
        description=f"🎵 **Kết quả cho:** `{query}`\nChọn bài ở menu bên dưới:",
        color=0x1DB954
    ), view=view)


@bot.command(name="play", aliases=["p"])
@commands.cooldown(1, 3, commands.BucketType.user)
async def cmd_play(ctx, *, query: str):
    """Phát nhạc YouTube/SoundCloud (URL hoặc tên bài)."""
    await ensure_voice(ctx)
    msg = await ctx.send(embed=discord.Embed(description=f"🔍 Đang tìm: `{query}`…", color=0x1DB954))
    try:
        entries = await fetch_info(query)
    except commands.CommandError as e:
        return await msg.edit(embed=discord.Embed(description=str(e), color=0xFF6B6B))
    if not entries:
        return await msg.edit(embed=discord.Embed(description="❌ Không tìm thấy kết quả.", color=0xFF6B6B))

    songs = [Song(e, ctx.author) for e in entries]
    state = gs(ctx.guild)
    state.queue.extend(songs)

    if len(songs) == 1:
        s = songs[0]
        e = discord.Embed(description=f"✅ Đã thêm: **[{s.title}]({s.webpage_url or 'https://discord.com'})**", color=0x1DB954)
        e.add_field(name="⏱", value=s.duration_fmt)
        e.add_field(name="📋 Queue", value=f"{len(state.queue)} bài")
    else:
        e = discord.Embed(description=f"✅ Đã thêm **{len(songs)} bài** vào queue.", color=0x1DB954)
        e.add_field(name="📋 Queue", value=f"{len(state.queue)} bài tổng cộng")

    await msg.edit(embed=e)
    if not state.playing() and not state.paused():
        await advance(ctx.guild, ctx.channel)


@bot.command(name="playnext", aliases=["pn"])
@commands.cooldown(1, 3, commands.BucketType.user)
async def cmd_playnext(ctx, *, query: str):
    """Thêm bài vào đầu queue (phát ngay sau bài hiện tại)."""
    await ensure_voice(ctx)
    msg = await ctx.send(embed=discord.Embed(description=f"🔍 Đang tìm: `{query}`…", color=0x1DB954))
    try:
        entries = await fetch_info(query)
    except commands.CommandError as e:
        return await msg.edit(embed=discord.Embed(description=str(e), color=0xFF6B6B))
    if not entries:
        return await msg.edit(embed=discord.Embed(description="❌ Không tìm thấy kết quả.", color=0xFF6B6B))

    songs  = [Song(e, ctx.author) for e in entries]
    state  = gs(ctx.guild)
    for i, s in enumerate(songs): state.queue.insert(i, s)

    if len(songs) == 1:
        s = songs[0]
        e = discord.Embed(description=f"⏫ Đã thêm vào **đầu queue**: **[{s.title}]({s.webpage_url or 'https://discord.com'})**", color=0x1DB954)
    else:
        e = discord.Embed(description=f"⏫ Đã thêm **{len(songs)} bài** vào đầu queue.", color=0x1DB954)
    await msg.edit(embed=e)
    if not state.playing() and not state.paused():
        await advance(ctx.guild, ctx.channel)


@bot.command(name="skip", aliases=["s", "next"])
async def cmd_skip(ctx):
    """Bỏ qua bài đang phát."""
    state = gs(ctx.guild)
    if not state.playing() and not state.paused():
        return await ctx.send(embed=discord.Embed(description="❌ Không có bài nào đang phát.", color=0xFF6B6B))
    state.voice.stop()
    await ctx.send(embed=discord.Embed(description="⏭️ Đã skip.", color=0x1DB954))


@bot.command(name="stop")
async def cmd_stop(ctx):
    """Dừng nhạc và xóa toàn bộ queue."""
    state = gs(ctx.guild)
    if not state.current and not state.queue:
        return await ctx.send(embed=discord.Embed(description="❌ Không có gì đang chạy.", color=0xFF6B6B))
    view = ConfirmView(ctx.author)
    msg  = await ctx.send(embed=discord.Embed(
        description=f"⚠️ Dừng nhạc và xóa **{len(state.queue)} bài** trong queue?", color=0xFFA500
    ), view=view)
    await view.wait()
    if view.confirmed:
        state.reset()
        if state.voice and state.voice.is_connected():
            state.voice.stop()
        await msg.edit(
            embed=discord.Embed(description="⏹️ Đã dừng và xóa queue.", color=0xFF6B6B), view=None
        )
    else:
        await msg.edit(
            embed=discord.Embed(description="✅ Đã hủy.", color=0xAAAAAA), view=None
        )


@bot.command(name="pause")
async def cmd_pause(ctx):
    """Tạm dừng nhạc."""
    state = gs(ctx.guild)
    if state.playing():
        state.voice.pause()
        await ctx.send(embed=discord.Embed(description="⏸️ Đã tạm dừng.", color=0xFFA500))
    elif state.paused():
        await ctx.send(embed=discord.Embed(description="⚠️ Nhạc đã đang bị tạm dừng.", color=0xFFA500))
    else:
        await ctx.send(embed=discord.Embed(description="❌ Không có bài nào đang phát.", color=0xFF6B6B))


@bot.command(name="resume", aliases=["r", "unpause"])
async def cmd_resume(ctx):
    """Tiếp tục phát nhạc."""
    state = gs(ctx.guild)
    if state.paused():
        state.voice.resume()
        await ctx.send(embed=discord.Embed(description="▶️ Đã tiếp tục phát.", color=0x1DB954))
    elif state.playing():
        await ctx.send(embed=discord.Embed(description="⚠️ Nhạc đang phát bình thường rồi.", color=0xFFA500))
    else:
        await ctx.send(embed=discord.Embed(description="❌ Không có bài nào đang phát.", color=0xFF6B6B))


@bot.command(name="nowplaying", aliases=["np", "current"])
async def cmd_np(ctx):
    """Xem bài đang phát."""
    state = gs(ctx.guild)
    if not state.current:
        return await ctx.send(embed=discord.Embed(description="❌ Không có bài nào đang phát.", color=0xFF6B6B))
    view = PlayerView()
    pos  = len(state.history) if state.loop == LOOP_PLAYLIST else state.play_count
    tot  = (len(state.history) + len(state.queue)) if state.loop == LOOP_PLAYLIST \
           else (state.play_count - 1 + 1 + len(state.queue))
    await ctx.send(embed=state.current.embed(pos, tot, paused=state.paused()), view=view)


@bot.command(name="queue", aliases=["q", "list"])
async def cmd_queue(ctx, page: int = 1):
    """Xem danh sách phát (có nút ◀▶)."""
    state = gs(ctx.guild)
    if not state.current and not state.queue:
        return await ctx.send(embed=discord.Embed(description="📭 Queue trống.", color=0xAAAAAA))
    view = QueueView(state, page)
    await ctx.send(embed=view.build_embed(), view=view)


@bot.command(name="loop", aliases=["l", "repeat"])
async def cmd_loop(ctx):
    """Chuyển loop: OFF → Playlist → Single → OFF."""
    state = gs(ctx.guild)
    if state.loop == LOOP_OFF:
        state.loop = LOOP_PLAYLIST
        state.history.clear()
        if state.current: state.history.append(state.current)
        desc = "🔁 Loop **Playlist** – Tự phát lại khi hết nhạc."
    elif state.loop == LOOP_PLAYLIST:
        state.loop = LOOP_SINGLE
        desc = "🔂 Loop **Single** – Lặp lại bài đang phát."
    else:
        state.loop = LOOP_OFF
        state.history.clear()
        desc = "➡️ Loop **TẮT**."
    await ctx.send(embed=discord.Embed(description=desc, color=0x1DB954))


@bot.command(name="shuffle", aliases=["sh"])
async def cmd_shuffle(ctx):
    """Xáo trộn queue."""
    state = gs(ctx.guild)
    if not state.queue:
        return await ctx.send(embed=discord.Embed(description="❌ Queue trống.", color=0xFF6B6B))
    random.shuffle(state.queue)
    if state.loop == LOOP_PLAYLIST and state.history:
        random.shuffle(state.history)
    await ctx.send(embed=discord.Embed(description="🔀 Đã xáo trộn queue!", color=0x1DB954))


@bot.command(name="volume", aliases=["vol", "v"])
async def cmd_volume(ctx, vol: int):
    """Chỉnh âm lượng 0–100."""
    if not 0 <= vol <= 100:
        return await ctx.send(embed=discord.Embed(description="❌ Âm lượng phải từ 0 đến 100.", color=0xFF6B6B))
    state = gs(ctx.guild)
    state.volume = vol / 100
    if state.voice and state.voice.source:
        state.voice.source.volume = state.volume
    bar = "█" * (vol // 10) + "░" * (10 - vol // 10)
    await ctx.send(embed=discord.Embed(description=f"🔊 **{vol}%** `{bar}`", color=0x1DB954))


@bot.command(name="remove", aliases=["rm", "del"])
async def cmd_remove(ctx, *, arg: str):
    """Xóa bài khỏi queue. VD: remove 3 hoặc remove 3-10"""
    state = gs(ctx.guild)
    m = re.match(r"(\d+)\s*-\s*(\d+)", arg)
    if m:
        s, e_ = int(m.group(1)), int(m.group(2))
        if s > e_: s, e_ = e_, s
        if not (1 <= s <= len(state.queue)) or not (1 <= e_ <= len(state.queue)):
            return await ctx.send(embed=discord.Embed(
                description=f"❌ Khoảng không hợp lệ (queue có {len(state.queue)} bài).", color=0xFF6B6B
            ))
        removed = state.queue[s-1:e_]
        del state.queue[s-1:e_]
        if state.loop == LOOP_PLAYLIST:
            for song in removed:
                try: state.history.remove(song)
                except ValueError: pass
        await ctx.send(embed=discord.Embed(
            description=f"🗑️ Đã xóa **{len(removed)} bài** (#{s}–#{e_}).", color=0xFF6B6B
        ))
    else:
        try: index = int(arg)
        except ValueError:
            return await ctx.send(embed=discord.Embed(
                description="❌ Sai cú pháp. VD: `remove 3` hoặc `remove 3-10`", color=0xFF6B6B
            ))
        if not 1 <= index <= len(state.queue):
            return await ctx.send(embed=discord.Embed(
                description=f"❌ Số thứ tự không hợp lệ (queue có {len(state.queue)} bài).", color=0xFF6B6B
            ))
        removed = state.queue.pop(index - 1)
        if state.loop == LOOP_PLAYLIST:
            try: state.history.remove(removed)
            except ValueError: pass
        await ctx.send(embed=discord.Embed(description=f"🗑️ Đã xóa: **{removed.title}**", color=0xFF6B6B))


@bot.command(name="clear", aliases=["c", "clearqueue"])
async def cmd_clear(ctx):
    """Xóa toàn bộ hàng chờ (giữ bài đang phát)."""
    state = gs(ctx.guild)
    if not state.queue:
        return await ctx.send(embed=discord.Embed(description="📭 Queue trống rồi.", color=0xAAAAAA))
    view = ConfirmView(ctx.author)
    msg  = await ctx.send(embed=discord.Embed(
        description=f"⚠️ Xóa **{len(state.queue)} bài** khỏi queue?", color=0xFFA500
    ), view=view)
    await view.wait()
    if view.confirmed:
        count = len(state.queue)
        state.queue.clear(); state.history.clear()
        await msg.edit(embed=discord.Embed(
            description=f"🗑️ Đã xóa {count} bài khỏi hàng chờ.", color=0xFF6B6B
        ), view=None)
    else:
        await msg.edit(embed=discord.Embed(description="✅ Đã hủy.", color=0xAAAAAA), view=None)


@bot.command(name="join", aliases=["j"])
async def cmd_join(ctx):
    """Kéo bot vào kênh của bạn."""
    if not ctx.author.voice:
        return await ctx.send(embed=discord.Embed(description="❌ Bạn chưa vào voice channel nào!", color=0xFF6B6B))
    vc = await ensure_voice(ctx)
    await ctx.send(embed=discord.Embed(description=f"✅ Đã vào **{vc.channel.name}**", color=0x1DB954))


@bot.command(name="leave", aliases=["dc", "bye"])
async def cmd_leave(ctx):
    """Bot rời kênh voice."""
    state = gs(ctx.guild)
    if state.voice and state.voice.is_connected():
        state.reset()
        await state.voice.disconnect(force=True)
        state.voice = None
        await ctx.send(embed=discord.Embed(description="👋 Đã rời kênh. Tạm biệt!", color=0xFF6B6B))
    else:
        await ctx.send(embed=discord.Embed(description="❌ Bot chưa ở trong kênh nào.", color=0xFF6B6B))


# ─────────────────────────────────────────────────────────────
#  ═══════════════════════════════════════
#  PLAYLIST COMMANDS
#  ═══════════════════════════════════════
# ─────────────────────────────────────────────────────────────

@bot.group(name="playlist", aliases=["pl"], invoke_without_command=True)
async def cmd_playlist(ctx):
    """Quản lý playlist cá nhân. Gõ `pl help` để xem lệnh."""
    p = PREFIX or ""
    e = discord.Embed(title="📂 Playlist Commands", color=0x5865F2)
    e.add_field(name="📋 Quản lý", inline=False, value=
        f"`{p}pl create <tên> [mô tả]` — Tạo playlist mới\n"
        f"`{p}pl delete <tên>` — Xóa playlist\n"
        f"`{p}pl rename <tên_cũ> <tên_mới>` — Đổi tên\n"
        f"`{p}pl list [@user]` — Xem danh sách playlist\n"
        f"`{p}pl view <tên> [@user]` — Xem chi tiết playlist\n"
        f"`{p}pl clone <@user> <tên> [tên_mới]` — Clone playlist\n"
    )
    e.add_field(name="➕ Thêm bài", inline=False, value=
        f"`{p}pl add <tên> <URL/tên bài>` — Thêm từ YouTube/SoundCloud\n"
        f"`{p}pl add <tên>` + đính kèm file — Upload file nhạc local\n"
        f"`{p}pl add <tên> <drive_link>` — Thêm từ Google Drive\n"
        f"`{p}pl addcurrent <tên>` — Thêm bài đang phát\n"
        f"`{p}pl remove <tên> <số hoặc khoảng>` — Xóa bài\n"
    )
    e.add_field(name="▶️ Phát", inline=False, value=
        f"`{p}pl load <tên> [@user]` — Load playlist vào queue\n"
        f"`{p}pl shuffle <tên> [@user]` — Shuffle rồi load\n"
        f"`{p}savequeue <tên>` / `{p}saveq` — Lưu queue hiện tại\n"
    )
    e.set_footer(text="Playlist được lưu vĩnh viễn, không giới hạn số lượng!")
    await ctx.send(embed=e)


@cmd_playlist.command(name="create", aliases=["new", "c"])
async def pl_create(ctx, name: str, *, description: str = ""):
    """Tạo playlist mới. VD: pl create rock_hits Nhạc rock hay nhất"""
    try:
        pm.create_playlist(ctx.author.id, str(ctx.author), name, description)
        e = discord.Embed(
            title="✅ Tạo playlist thành công!",
            description=f"📂 **{name}**",
            color=0x1DB954
        )
        if description: e.add_field(name="📝 Mô tả", value=description)
        e.set_footer(text=f"Thêm bài bằng: {PREFIX}pl add {name} <URL>")
        await ctx.send(embed=e)
    except pm.PlaylistError as err:
        await ctx.send(embed=discord.Embed(description=str(err), color=0xFF6B6B))


@cmd_playlist.command(name="delete", aliases=["del", "d"])
async def pl_delete(ctx, *, name: str):
    """Xóa playlist. VD: pl delete rock_hits"""
    try:
        data = pm.get_playlist(ctx.author.id, name)
    except pm.PlaylistError as err:
        return await ctx.send(embed=discord.Embed(description=str(err), color=0xFF6B6B))

    view = ConfirmView(ctx.author)
    msg  = await ctx.send(embed=discord.Embed(
        description=f"⚠️ Xóa playlist **{name}** ({len(data['tracks'])} bài)?",
        color=0xFFA500
    ), view=view)
    await view.wait()
    if view.confirmed:
        try:
            pm.delete_playlist(ctx.author.id, name)
            await msg.edit(embed=discord.Embed(
                description=f"🗑️ Đã xóa playlist **{name}**.", color=0xFF6B6B
            ), view=None)
        except pm.PlaylistError as err:
            await msg.edit(embed=discord.Embed(description=str(err), color=0xFF6B6B), view=None)
    else:
        await msg.edit(embed=discord.Embed(description="✅ Đã hủy.", color=0xAAAAAA), view=None)


@cmd_playlist.command(name="rename", aliases=["rn"])
async def pl_rename(ctx, old_name: str, new_name: str):
    """Đổi tên playlist. VD: pl rename rock_hits best_rock"""
    try:
        pm.rename_playlist(ctx.author.id, old_name, new_name)
        await ctx.send(embed=discord.Embed(
            description=f"✅ Đã đổi tên **{old_name}** → **{new_name}**.", color=0x1DB954
        ))
    except pm.PlaylistError as err:
        await ctx.send(embed=discord.Embed(description=str(err), color=0xFF6B6B))


@cmd_playlist.command(name="list", aliases=["ls"])
async def pl_list(ctx, target: discord.Member = None):
    """Xem danh sách playlist (của mình hoặc người khác). VD: pl list @MRX"""
    user = target or ctx.author
    playlists = pm.list_playlists(user.id)

    if not playlists:
        msg = f"📭 **{user.display_name}** chưa có playlist nào."
        if user == ctx.author:
            msg += f"\nTạo ngay bằng `{PREFIX}pl create <tên>`!"
        return await ctx.send(embed=discord.Embed(description=msg, color=0xAAAAAA))

    # Embed overview
    e = discord.Embed(
        title=f"📂 Playlist của {user.display_name} ({len(playlists)} playlist)",
        color=0x5865F2
    )
    for pl in playlists[:10]:
        val = f"🎵 **{pl['track_count']}** bài"
        if pl.get("description"): val += f" | _{pl['description'][:60]}_"
        e.add_field(name=f"📂 {pl['name']}", value=val, inline=False)

    if len(playlists) > 10:
        e.set_footer(text=f"Hiển thị 10/{len(playlists)} — Chọn qua dropdown để xem chi tiết")

    if len(playlists) > 0:
        view = PlaylistListView(playlists, user.id, ctx.author)
        await ctx.send(embed=e, view=view)
    else:
        await ctx.send(embed=e)


@cmd_playlist.command(name="view", aliases=["show", "info"])
async def pl_view(ctx, name: str, target: discord.Member = None):
    """Xem chi tiết playlist. VD: pl view rock_hits hoặc pl view rock_hits @MRX"""
    user = target or ctx.author
    try:
        pl_data = pm.get_playlist(user.id, name)
    except pm.PlaylistError as err:
        return await ctx.send(embed=discord.Embed(description=str(err), color=0xFF6B6B))

    view  = PlaylistDetailView(pl_data, ctx.author)
    await ctx.send(embed=view.build_embed(), view=view)


@cmd_playlist.command(name="add", aliases=["a"])
@commands.cooldown(1, 3, commands.BucketType.user)
async def pl_add(ctx, name: str, *, query: str = ""):
    """
    Thêm bài vào playlist.
    - URL YouTube/SoundCloud: pl add rock https://youtube.com/...
    - Tên bài: pl add rock bohemian rhapsody
    - Google Drive: pl add rock https://drive.google.com/...
    - File upload: pl add rock (đính kèm file .mp3/.wav/...)
    """
    # Kiểm tra playlist tồn tại (của người dùng)
    try:
        pm.get_playlist(ctx.author.id, name)
    except pm.PlaylistError as err:
        return await ctx.send(embed=discord.Embed(description=str(err), color=0xFF6B6B))

    msg = await ctx.send(embed=discord.Embed(description="⏳ Đang xử lý…", color=0x5865F2))

    # Case 1: File attachment
    if ctx.message.attachments and not query:
        attachment = ctx.message.attachments[0]
        try:
            track = await dh.handle_discord_attachment(attachment, ctx.author.id)
        except ValueError as e:
            return await msg.edit(embed=discord.Embed(description=str(e), color=0xFF6B6B))
        pm.add_track(ctx.author.id, name, track)
        e = discord.Embed(title="✅ Đã thêm file vào playlist!", color=0x1DB954)
        e.add_field(name="🎵 Bài",      value=track["title"])
        e.add_field(name="📁 Nguồn",    value="Local Upload")
        e.add_field(name="📂 Playlist", value=name)
        return await msg.edit(embed=e)

    if not query:
        return await msg.edit(embed=discord.Embed(
            description="❌ Hãy cung cấp URL, tên bài hoặc đính kèm file nhạc.", color=0xFF6B6B
        ))

    # Case 2: Google Drive link
    if dh.is_drive_link(query):
        folder_id = dh.extract_folder_id(query)
        if folder_id:
            # Folder GDrive
            await msg.edit(embed=discord.Embed(description="☁️ Đang quét Google Drive folder…", color=0x5865F2))
            try:
                tracks = await dh.extract_drive_folder(query, YTDL_OPTS, None)
            except Exception as e:
                return await msg.edit(embed=discord.Embed(
                    description=f"❌ Lỗi Google Drive: {e}", color=0xFF6B6B
                ))
            if not tracks:
                return await msg.edit(embed=discord.Embed(
                    description="❌ Không tìm thấy file nhạc trong folder. Đảm bảo folder public.", color=0xFF6B6B
                ))
            pm.add_tracks(ctx.author.id, name, tracks)
            e = discord.Embed(title="✅ Đã thêm từ Google Drive Folder!", color=0x1DB954)
            e.add_field(name="☁️ Số file", value=str(len(tracks)))
            e.add_field(name="📂 Playlist", value=name)
            return await msg.edit(embed=e)
        else:
            # File đơn GDrive
            await msg.edit(embed=discord.Embed(description="☁️ Đang lấy thông tin Google Drive file…", color=0x5865F2))
            track = await dh.extract_drive_file(query, YTDL_OPTS, None)
            if not track:
                return await msg.edit(embed=discord.Embed(
                    description="❌ Không lấy được thông tin file. Đảm bảo link public.", color=0xFF6B6B
                ))
            pm.add_track(ctx.author.id, name, track)
            e = discord.Embed(title="✅ Đã thêm từ Google Drive!", color=0x1DB954)
            e.add_field(name="🎵 Bài",      value=track["title"])
            e.add_field(name="📂 Playlist", value=name)
            return await msg.edit(embed=e)

    # Case 3: YouTube/SoundCloud URL hoặc tên bài
    await msg.edit(embed=discord.Embed(description=f"🔍 Đang tìm: `{query}`…", color=0x5865F2))
    try:
        entries = await fetch_info(query)
    except commands.CommandError as e:
        return await msg.edit(embed=discord.Embed(description=str(e), color=0xFF6B6B))

    if not entries:
        return await msg.edit(embed=discord.Embed(description="❌ Không tìm thấy kết quả.", color=0xFF6B6B))

    tracks = [pm.build_track_from_ytdl(e) for e in entries]
    pm.add_tracks(ctx.author.id, name, tracks)

    if len(tracks) == 1:
        t = tracks[0]
        e = discord.Embed(title="✅ Đã thêm vào playlist!", color=0x1DB954)
        e.add_field(name="🎵 Bài",      value=t["title"][:60])
        e.add_field(name="⏱ Thời lượng", value=pm.format_duration(t.get("duration") or 0))
        e.add_field(name="📂 Playlist", value=name)
        e.add_field(name="📡 Nguồn",    value=f"{pm.source_emoji(t.get('source','url'))} {t.get('source','url').capitalize()}")
    else:
        total_pl = pm.get_playlist(ctx.author.id, name)
        e = discord.Embed(title="✅ Đã thêm nhiều bài vào playlist!", color=0x1DB954)
        e.add_field(name="➕ Số bài thêm",   value=str(len(tracks)))
        e.add_field(name="📂 Playlist",       value=name)
        e.add_field(name="🎵 Tổng bài",       value=str(len(total_pl["tracks"])))
    await msg.edit(embed=e)


@cmd_playlist.command(name="remove", aliases=["rm"])
async def pl_remove(ctx, name: str, *, arg: str):
    """Xóa bài khỏi playlist. VD: pl remove rock 3 hoặc pl remove rock 3-10"""
    m = re.match(r"(\d+)\s*-\s*(\d+)", arg)
    try:
        if m:
            s, e_ = int(m.group(1)), int(m.group(2))
            data, removed = pm.remove_track_range(ctx.author.id, name, s, e_)
            await ctx.send(embed=discord.Embed(
                description=f"🗑️ Đã xóa **{len(removed)} bài** (#{s}–#{e_}) khỏi **{name}**.",
                color=0xFF6B6B
            ))
        else:
            index = int(arg)
            data, removed = pm.remove_track(ctx.author.id, name, index)
            await ctx.send(embed=discord.Embed(
                description=f"🗑️ Đã xóa **{removed['title'][:60]}** khỏi **{name}**.",
                color=0xFF6B6B
            ))
    except pm.PlaylistError as err:
        await ctx.send(embed=discord.Embed(description=str(err), color=0xFF6B6B))
    except ValueError:
        await ctx.send(embed=discord.Embed(
            description="❌ Sai cú pháp. VD: `pl remove rock 3` hoặc `pl remove rock 3-10`",
            color=0xFF6B6B
        ))


@cmd_playlist.command(name="addcurrent", aliases=["ac", "addnp"])
async def pl_addcurrent(ctx, *, name: str):
    """Thêm bài đang phát vào playlist. VD: pl addcurrent my_faves"""
    state = gs(ctx.guild)
    if not state.current:
        return await ctx.send(embed=discord.Embed(description="❌ Không có bài nào đang phát.", color=0xFF6B6B))
    try:
        track = {
            "title":       state.current.title,
            "url":         state.current.url,
            "webpage_url": state.current.webpage_url,
            "duration":    state.current.duration,
            "thumbnail":   state.current.thumbnail,
            "uploader":    state.current.uploader,
            "source":      state.current.source,
        }
        pm.add_track(ctx.author.id, name, track)
        e = discord.Embed(title="✅ Đã lưu bài đang phát vào playlist!", color=0x1DB954)
        e.add_field(name="🎵 Bài",      value=state.current.title[:60])
        e.add_field(name="📂 Playlist", value=name)
        await ctx.send(embed=e)
    except pm.PlaylistError as err:
        await ctx.send(embed=discord.Embed(description=str(err), color=0xFF6B6B))


@cmd_playlist.command(name="load", aliases=["play", "start"])
async def pl_load(ctx, name: str, target: discord.Member = None):
    """Load playlist vào queue và bắt đầu phát. VD: pl load rock hoặc pl load rock @MRX"""
    await ensure_voice(ctx)
    user = target or ctx.author
    try:
        pl_data = pm.get_playlist(user.id, name)
    except pm.PlaylistError as err:
        return await ctx.send(embed=discord.Embed(description=str(err), color=0xFF6B6B))
    await _load_playlist_to_queue(ctx, pl_data, shuffle=False)


@cmd_playlist.command(name="shuffle", aliases=["sh", "random"])
async def pl_shuffle_load(ctx, name: str, target: discord.Member = None):
    """Shuffle playlist rồi load vào queue. VD: pl shuffle rock hoặc pl shuffle rock @MRX"""
    await ensure_voice(ctx)
    user = target or ctx.author
    try:
        pl_data = pm.get_playlist(user.id, name)
    except pm.PlaylistError as err:
        return await ctx.send(embed=discord.Embed(description=str(err), color=0xFF6B6B))
    await _load_playlist_to_queue(ctx, pl_data, shuffle=True)


@cmd_playlist.command(name="clone", aliases=["copy"])
async def pl_clone(ctx, target: discord.Member, name: str, new_name: str = None):
    """Clone playlist của người khác về mình. VD: pl clone @MRX rock hoặc pl clone @MRX rock my_rock"""
    try:
        data = pm.clone_playlist(
            from_user_id=target.id,
            from_name=name,
            to_user_id=ctx.author.id,
            to_owner_name=str(ctx.author),
            new_name=new_name,
        )
        e = discord.Embed(title="✅ Clone playlist thành công!", color=0x1DB954)
        e.add_field(name="📂 Playlist",   value=data["name"])
        e.add_field(name="🎵 Số bài",     value=str(len(data["tracks"])))
        e.add_field(name="👤 Clone từ",   value=target.mention)
        await ctx.send(embed=e)
    except pm.PlaylistError as err:
        await ctx.send(embed=discord.Embed(description=str(err), color=0xFF6B6B))


@cmd_playlist.command(name="shufflesave", aliases=["mix"])
async def pl_shuffle_inplace(ctx, *, name: str):
    """Xáo trộn thứ tự bài trong playlist (lưu vĩnh viễn). VD: pl shufflesave rock"""
    try:
        data = pm.shuffle_playlist(ctx.author.id, name)
        e = discord.Embed(title="🔀 Đã xáo trộn playlist!", color=0x1DB954)
        e.add_field(name="📂 Playlist", value=data["name"])
        e.add_field(name="🎵 Số bài",   value=str(len(data["tracks"])))
        await ctx.send(embed=e)
    except pm.PlaylistError as err:
        await ctx.send(embed=discord.Embed(description=str(err), color=0xFF6B6B))


# ─────────────────────────────────────────────────────────────
#  SAVEQUEUE (top-level command)
# ─────────────────────────────────────────────────────────────
@bot.command(name="savequeue", aliases=["saveq", "saveas"])
async def cmd_savequeue(ctx, *, name: str):
    """Lưu queue hiện tại thành playlist. VD: savequeue chill_mix"""
    state = gs(ctx.guild)
    songs = ([state.current] if state.current else []) + list(state.queue)
    if not songs:
        return await ctx.send(embed=discord.Embed(
            description="❌ Queue trống, không có gì để lưu.", color=0xFF6B6B
        ))
    try:
        data = pm.save_queue_as(ctx.author.id, str(ctx.author), name, songs)
        e = discord.Embed(title="💾 Đã lưu queue thành playlist!", color=0x1DB954)
        e.add_field(name="📂 Playlist", value=data["name"])
        e.add_field(name="🎵 Số bài",   value=str(len(data["tracks"])))
        e.set_footer(text=f"Dùng '{PREFIX}pl load {name}' để phát lại!")
        await ctx.send(embed=e)
    except pm.PlaylistError as err:
        await ctx.send(embed=discord.Embed(description=str(err), color=0xFF6B6B))


# ─────────────────────────────────────────────────────────────
#  HELP
# ─────────────────────────────────────────────────────────────
@bot.command(name="help", aliases=["h"])
async def cmd_help(ctx):
    """Xem toàn bộ lệnh."""
    p = PREFIX if PREFIX else ""
    e = discord.Embed(title="🎵 Music Bot v3.0 — Hướng dẫn sử dụng", color=0x1DB954)
    e.add_field(name="▶️ Phát nhạc", inline=False, value=
        f"`{p}play <tên/URL>` — Phát từ YouTube/SoundCloud\n"
        f"`{p}search <tên>` — Tìm và chọn bài bằng Dropdown\n"
        f"`{p}playnext <tên/URL>` — Thêm vào **đầu** queue\n"
        f"`{p}skip` / `{p}s` — Bỏ qua bài\n"
        f"`{p}stop` — Dừng & xóa queue\n"
        f"`{p}pause` — Tạm dừng\n"
        f"`{p}resume` / `{p}r` — Tiếp tục\n"
        f"`{p}np` — Xem bài đang phát"
    )
    e.add_field(name="📋 Queue", inline=False, value=
        f"`{p}queue` / `{p}q` — Xem danh sách (nút ◀▶ chuyển trang)\n"
        f"`{p}shuffle` / `{p}sh` — Xáo trộn queue\n"
        f"`{p}loop` / `{p}l` — Chuyển loop: OFF → Playlist → Single\n"
        f"`{p}remove <số>` / `{p}rm` — Xóa bài (VD: `rm 3-10`)\n"
        f"`{p}clear` — Xóa sạch hàng chờ (có xác nhận)"
    )
    e.add_field(name="📂 Playlist", inline=False, value=
        f"`{p}pl` — Xem tất cả lệnh playlist\n"
        f"`{p}pl create <tên>` — Tạo playlist\n"
        f"`{p}pl add <tên> <URL/file>` — Thêm bài (YT/SC/GDrive/local)\n"
        f"`{p}pl load <tên> [@user]` — Load playlist vào queue\n"
        f"`{p}pl list [@user]` — Xem danh sách playlist\n"
        f"`{p}savequeue <tên>` — Lưu queue thành playlist"
    )
    e.add_field(name="🔊 Điều chỉnh", inline=False, value=
        f"`{p}volume <0-100>` / `{p}vol` — Chỉnh âm lượng\n"
        f"`{p}join` — Kéo bot vào kênh\n"
        f"`{p}leave` / `{p}dc` — Đuổi bot ra khỏi kênh"
    )
    e.add_field(name="💡 Tính năng nổi bật", inline=False, value=
        "• 📂 **Playlist cá nhân** — lưu bài từ YouTube, SoundCloud, Google Drive, file local\n"
        "• 🔗 **Chia sẻ playlist** — load/clone playlist của bạn bè\n"
        "• 🎛️ **Nút bấm tương tác** — ⏸ ⏭ ⏹ 🔀 🔁 ngay trên tin nhắn\n"
        "• 📄 **Queue phân trang** — nút ◀▶ chuyển trang\n"
        "• 💾 **Lưu queue** — `savequeue <tên>` để lưu lại sau\n"
        "• 🤖 Bot **không tự rời kênh** (24/7)"
    )
    e.set_footer(text="Made with ❤️ by MRX | v3.0 Playlist Edition")
    await ctx.send(embed=e)


# ─────────────────────────────────────────────────────────────
#  EVENTS
# ─────────────────────────────────────────────────────────────
@bot.event
async def setup_hook():
    bot.add_view(PlayerView())  # Persistent view (buttons không bao giờ hết hạn)


@bot.event
async def on_ready():
    print(f"✅ {bot.user} online! Prefix: '{PREFIX}'")
    p = PREFIX or ""
    # Tạo thư mục cần thiết
    Path("playlists").mkdir(exist_ok=True)
    Path("temp_audio").mkdir(exist_ok=True)
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening, name=f"{p}play | {p}pl | Music Bot"
    ))


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if member.id != bot.user.id: return
    if before.channel is not None and after.channel is None:
        state = gs(member.guild)
        state.reset()
        state.voice = None
        print(f"[Voice] Bot bị disconnect khỏi {before.channel.name} — state đã reset.")
    elif before.channel is not None and after.channel is not None and before.channel != after.channel:
        state = gs(member.guild)
        if state.voice and state.playing():
            state.voice.pause()
            await asyncio.sleep(0.5)
            state.voice.resume()


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CommandOnCooldown):
        return await ctx.send(embed=discord.Embed(
            description=f"⏳ Chờ **{error.retry_after:.1f}s** trước khi dùng lại lệnh này.",
            color=0xFFA500
        ), delete_after=5)
    if isinstance(error, commands.MissingRequiredArgument):
        return await ctx.send(embed=discord.Embed(
            description=f"❌ Thiếu tham số! Gõ `{PREFIX}help` để xem hướng dẫn.",
            color=0xFF6B6B
        ))
    if isinstance(error, commands.BadArgument):
        return await ctx.send(embed=discord.Embed(
            description="❌ Tham số không hợp lệ.",
            color=0xFF6B6B
        ))
    if isinstance(error, commands.CommandError):
        return await ctx.send(embed=discord.Embed(description=str(error), color=0xFF6B6B))
    print(f"[Error] {ctx.command}: {error}")


# ─────────────────────────────────────────────────────────────
#  RUN
# ─────────────────────────────────────────────────────────────
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN: raise ValueError("Thiếu DISCORD_TOKEN trong .env!")
bot.run(TOKEN)
