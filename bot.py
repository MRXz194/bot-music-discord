# ============================================================
#  🎵 Discord Music Bot — Jockie Music Clone (v2.0)
#  Stack : discord.py 2.7 + yt-dlp + FFmpeg
#  Changes: Bug fixes, stability, UX improvements
# ============================================================
import os, asyncio, random, datetime, discord, re
from discord.ext import commands
from dotenv import load_dotenv
import yt_dlp

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
YTDL_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": False,
    "nocheckcertificate": True,
    "ignoreerrors": False,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "socket_timeout": 15,
    "retries": 3,
    "extract_flat": "in_playlist",
    "extractor_args": {"youtube": {"skip": ["dash", "hls"], "player_client": ["android", "web"]}},
}

FFMPEG_OPTS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -probesize 10M -thread_queue_size 4096",
    "options": "-vn -b:a 128k",
}

def _find_ffmpeg():
    import shutil, subprocess, glob

    # 1. Check PATH (Works on Linux/Docker and Windows if added to PATH)
    found = shutil.which("ffmpeg")
    if found: return found

    # 2. Env override
    env_path = os.getenv("FFMPEG_PATH", "")
    if env_path and os.path.isfile(env_path): return env_path

    # 3. Windows-specific deep search
    if os.name == 'nt':
        try:
            res = subprocess.run(["where.exe","ffmpeg"], capture_output=True, text=True, timeout=3)
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
        self.url          = data.get("url", "")
        self.webpage_url  = data.get("webpage_url", data.get("url", ""))
        self.title        = data.get("title", "Unknown")
        self.duration     = data.get("duration", 0)
        self.thumbnail    = data.get("thumbnail", "")
        self.uploader     = data.get("uploader", "Unknown")
        self.requester    = requester

    @property
    def duration_fmt(self):
        if not self.duration: return "∞"
        return str(datetime.timedelta(seconds=int(self.duration)))

    def embed(self, position=0, total=0, paused=False) -> discord.Embed:
        status = "⏸ Paused" if paused else "🎵 Now Playing"
        e = discord.Embed(
            title=status,
            description=f"**[{self.title}]({self.webpage_url})**",
            color=0xFFA500 if paused else 0x1DB954,
        )
        if self.thumbnail: e.set_thumbnail(url=self.thumbnail)
        e.add_field(name="⏱ Thời lượng",  value=self.duration_fmt,      inline=True)
        e.add_field(name="👤 Uploader",    value=self.uploader,          inline=True)
        e.add_field(name="🙋 Yêu cầu bởi", value=self.requester.mention, inline=True)
        if total > 0:
            e.set_footer(text=f"Track {position}/{total} trong queue")
        return e

# ─────────────────────────────────────────────────────────────
#  LOOP MODE ENUM
# ─────────────────────────────────────────────────────────────
LOOP_OFF      = 0
LOOP_PLAYLIST = 1
LOOP_SINGLE   = 2

# ─────────────────────────────────────────────────────────────
#  GUILD STATE
# ─────────────────────────────────────────────────────────────
class GuildState:
    def __init__(self, guild):
        self.guild    = guild
        self.queue:   list[Song] = []
        self.history: list[Song] = []
        self.current: Song | None = None
        self.loop     = LOOP_OFF        # 0=off, 1=playlist, 2=single
        self.volume   = 0.5
        self.voice:   discord.VoiceClient | None = None
        self._adv     = False   # advancing guard

    def playing(self): return self.voice is not None and self.voice.is_playing()
    def paused(self):  return self.voice is not None and self.voice.is_paused()

    def loop_label(self):
        if self.loop == LOOP_PLAYLIST: return "🔁 Playlist"
        if self.loop == LOOP_SINGLE:   return "🔂 Single"
        return "OFF"

    def reset(self):
        self.queue.clear(); self.history.clear()
        self.current = None; self._adv = False

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
            timeout=60.0     # tăng lên 60s cho playlist lớn
        )
    except asyncio.TimeoutError:
        raise commands.CommandError("⏳ Tìm kiếm quá lâu! Thử dùng URL trực tiếp.")

    if not data: return []
    entries = data.get("entries", [data]) if "entries" in data else [data]
    return [e for e in entries if e]

# [Bug 3 Fix] refresh_song trả về True/False
async def refresh_song(song: Song) -> bool:
    """Re-extract fresh stream URL. Returns True on success, False on failure."""
    try:
        opts = dict(YTDL_OPTS)
        opts["extract_flat"] = False
        opts["noplaylist"] = True
        ytdl = yt_dlp.YoutubeDL(opts)
        data = await asyncio.wait_for(
            bot.loop.run_in_executor(None, lambda: ytdl.extract_info(song.webpage_url, download=False)),
            timeout=15.0
        )
        if data and data.get("url"):
            song.url       = data.get("url", song.url)
            song.title     = data.get("title", song.title) or "Unknown"
            song.duration  = data.get("duration", song.duration)
            song.thumbnail = data.get("thumbnail", song.thumbnail)
            song.uploader  = data.get("uploader", song.uploader) or "Unknown"
            return True
        return False
    except Exception as e:
        print(f"[Refresh Error] {e}")
        return False

def make_source(url: str, volume: float) -> discord.PCMVolumeTransformer:
    return discord.PCMVolumeTransformer(
        discord.FFmpegPCMAudio(url, **FFMPEG_OPTS), volume=volume
    )

# ─────────────────────────────────────────────────────────────
#  PLAYER CONTROLS VIEW (UX 1 — Interactive Buttons)
# ─────────────────────────────────────────────────────────────
class PlayerView(discord.ui.View):
    def __init__(self, guild: discord.Guild, ch: discord.TextChannel):
        super().__init__(timeout=600)  # 10 phút timeout
        self.guild = guild
        self.ch = ch

    @discord.ui.button(label="⏸", style=discord.ButtonStyle.secondary)
    async def btn_pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = gs(self.guild)
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

    @discord.ui.button(label="⏭", style=discord.ButtonStyle.primary)
    async def btn_skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = gs(self.guild)
        if state.playing() or state.paused():
            state.voice.stop()
            await interaction.response.send_message(
                embed=discord.Embed(description="⏭️ Đã skip.", color=0x1DB954), delete_after=5
            )
        else:
            await interaction.response.defer()

    @discord.ui.button(label="⏹", style=discord.ButtonStyle.danger)
    async def btn_stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = gs(self.guild)
        state.reset()
        if state.voice and state.voice.is_connected():
            state.voice.stop()
        await interaction.response.send_message(
            embed=discord.Embed(description="⏹️ Đã dừng và xóa queue.", color=0xFF6B6B), delete_after=5
        )
        self.stop()

    @discord.ui.button(label="🔀", style=discord.ButtonStyle.secondary)
    async def btn_shuffle(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = gs(self.guild)
        if state.queue:
            random.shuffle(state.queue)
            if state.loop == LOOP_PLAYLIST and state.history:
                random.shuffle(state.history)
            await interaction.response.send_message(
                embed=discord.Embed(description="🔀 Đã xáo trộn queue!", color=0x1DB954), delete_after=5
            )
        else:
            await interaction.response.defer()

# ─────────────────────────────────────────────────────────────
#  ADVANCE QUEUE (Bug 1, 3 fixes)
# ─────────────────────────────────────────────────────────────
async def advance(guild: discord.Guild, ch: discord.TextChannel):
    state = gs(guild)
    if state._adv: return
    state._adv = True
    try:
        # Sync voice
        vc = state.voice or guild.voice_client
        
        # [Fix] Chờ reconnect tối đa 3 giây nếu bot vừa bị kéo sang kênh khác
        for _ in range(6):
            if vc and vc.is_connected(): break
            await asyncio.sleep(0.5)
            
        if not vc or not vc.is_connected():
            state.current = None; state._adv = False; return
        state.voice = vc

        # [UX 4] Loop single — replay current song
        if state.loop == LOOP_SINGLE and state.current:
            song = state.current
        # Pick song from queue
        elif state.queue:
            song = state.queue.pop(0)
        elif state.loop == LOOP_PLAYLIST and state.history:
            state.queue = list(state.history); state.history.clear()
            song = state.queue.pop(0)
            try: await ch.send(embed=discord.Embed(description="🔁 Loop – Phát lại từ đầu!", color=0x1DB954))
            except: pass
        else:
            state.current = None
            try: await ch.send(embed=discord.Embed(description="✅ Hết nhạc. Bot vẫn ngồi chờ ~ Thêm bài vào nào!", color=0x1DB954))
            except: pass
            state._adv = False; return

        # [Bug 3 Fix] Refresh track — skip on failure
        ok = await refresh_song(song)
        if not ok:
            try:
                await ch.send(embed=discord.Embed(
                    description=f"❌ Không thể tải **{song.title}**, tự bỏ qua...", color=0xFF6B6B
                ))
            except: pass
            state._adv = False
            await asyncio.sleep(2)  # tránh spam khi nhiều bài lỗi liên tiếp
            asyncio.ensure_future(advance(guild, ch))
            return

        state.current = song
        # [Bug 1 Fix] Chỉ ghi history tại đây — nguồn DUY NHẤT
        if state.loop == LOOP_PLAYLIST: state.history.append(song)

        def after(err):
            if err: print(f"[Player] {err}")
            state._adv = False
            asyncio.run_coroutine_threadsafe(advance(guild, ch), bot.loop)

        src = make_source(song.url, state.volume)
        if state.voice and state.voice.is_connected():
            state.voice.play(src, after=after)
            pos = len(state.history) if state.loop == LOOP_PLAYLIST else 1
            tot = len(state.history)+len(state.queue) if state.loop == LOOP_PLAYLIST else len(state.queue)+1
            view = PlayerView(guild, ch)
            try: await ch.send(embed=song.embed(pos, tot, paused=False), view=view)
            except: pass
        else:
            state._adv = False
    except Exception as e:
        print(f"[Advance] {e}")
        state._adv = False
        # [Bug 3 Fix] Delay trước khi retry tránh CPU spike
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
#  SEARCH CONTROLS VIEW (UX)
# ─────────────────────────────────────────────────────────────
class SearchDropdown(discord.ui.Select):
    def __init__(self, entries, author: discord.Member):
        self.entries = entries
        self.author = author
        options = []
        for i, e in enumerate(entries[:5]):
            title = e.get('title', 'Unknown')[:90] 
            duration = str(datetime.timedelta(seconds=int(e.get('duration', 0)))) if e.get('duration') else "∞"
            uploader = e.get('uploader', 'Unknown')[:40]
            options.append(discord.SelectOption(
                label=f"{i+1}. {title}",
                description=f"⏱ {duration} | 👤 {uploader}",
                value=str(i)
            ))
        super().__init__(placeholder="Nhấn vào đây để chọn bài hát...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user != self.author:
            return await interaction.response.send_message("❌ Bạn không phải người tìm kiếm!", ephemeral=True)
        
        idx = int(self.values[0])
        entry = self.entries[idx]
        song = Song(entry, self.author)
        
        state = gs(interaction.guild)
        state.queue.append(song)
        
        e = discord.Embed(description=f"✅ Đã thêm: **[{song.title}]({song.webpage_url})**", color=0x1DB954)
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
#  COMMANDS
# ─────────────────────────────────────────────────────────────

# ── SEARCH (Dropdown) ────────────────────────────────────────
@bot.command(name="search", aliases=["find"])
@commands.cooldown(1, 4, commands.BucketType.user)
async def cmd_search(ctx, *, query: str):
    """Tìm nhạc trên YouTube và chọn kết quả."""
    await ensure_voice(ctx)
    msg = await ctx.send(embed=discord.Embed(description=f"🔍 Đang tìm 5 kết quả tốt nhất cho: `{query}`…", color=0x1DB954))
    
    opts = dict(YTDL_OPTS)
    opts['extract_flat'] = True
    ytdl = yt_dlp.YoutubeDL(opts)
    
    try:
        data = await asyncio.wait_for(
            bot.loop.run_in_executor(None, lambda: ytdl.extract_info(f"ytsearch5:{query}", download=False)),
            timeout=15.0
        )
    except Exception as e:
        return await msg.edit(embed=discord.Embed(description=str(e), color=0xFF6B6B))

    if not data or 'entries' not in data or not data['entries']:
        return await msg.edit(embed=discord.Embed(description="❌ Không tìm thấy kết quả.", color=0xFF6B6B))

    entries = data['entries']
    view = SearchViewUI(entries, ctx.author)
    await msg.edit(embed=discord.Embed(description=f"🎵 **Kết quả cho:** `{query}`\nChọn 1 bài ở menu bên dưới:", color=0x1DB954), view=view)

# ── PLAY (UX 6 — Cooldown 3s) ────────────────────────────────
@bot.command(name="play", aliases=["p"])
@commands.cooldown(1, 3, commands.BucketType.user)
async def cmd_play(ctx, *, query: str):
    """Phát nhạc YouTube / SoundCloud (URL hoặc tên bài)."""
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
    # [Bug 1 Fix] KHÔNG thêm vào history ở đây — advance() sẽ tự ghi

    if len(songs) == 1:
        s = songs[0]
        e = discord.Embed(description=f"✅ Đã thêm: **[{s.title}]({s.webpage_url})**", color=0x1DB954)
        e.add_field(name="⏱", value=s.duration_fmt)
        e.add_field(name="📋 Queue", value=f"{len(state.queue)} bài")
    else:
        e = discord.Embed(description=f"✅ Đã thêm **{len(songs)} bài** vào queue.", color=0x1DB954)
        e.add_field(name="📋 Queue", value=f"{len(state.queue)} bài tổng cộng")

    await msg.edit(embed=e)
    if not state.playing() and not state.paused():
        await advance(ctx.guild, ctx.channel)


# ── PLAYNEXT (UX 5) ──────────────────────────────────────────
@bot.command(name="playnext", aliases=["pn"])
@commands.cooldown(1, 3, commands.BucketType.user)
async def cmd_playnext(ctx, *, query: str):
    """Thêm bài vào ĐẦU queue (phát ngay sau bài hiện tại)."""
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
    # Chèn vào ĐẦU queue
    for i, s in enumerate(songs):
        state.queue.insert(i, s)

    if len(songs) == 1:
        s = songs[0]
        e = discord.Embed(description=f"⏫ Đã thêm vào **đầu queue**: **[{s.title}]({s.webpage_url})**", color=0x1DB954)
    else:
        e = discord.Embed(description=f"⏫ Đã thêm **{len(songs)} bài** vào đầu queue.", color=0x1DB954)

    await msg.edit(embed=e)
    if not state.playing() and not state.paused():
        await advance(ctx.guild, ctx.channel)


# ── SKIP ──────────────────────────────────────────────────────
@bot.command(name="skip", aliases=["s", "next"])
async def cmd_skip(ctx):
    """Bỏ qua bài đang phát."""
    state = gs(ctx.guild)
    if not state.playing() and not state.paused():
        return await ctx.send("❌ Không có bài nào đang phát.")
    state.voice.stop()
    await ctx.send(embed=discord.Embed(description="⏭️ Đã skip.", color=0x1DB954))


# ── STOP ──────────────────────────────────────────────────────
@bot.command(name="stop")
async def cmd_stop(ctx):
    """Dừng nhạc và xóa toàn bộ queue."""
    state = gs(ctx.guild)
    state.reset()
    if state.voice and state.voice.is_connected():
        state.voice.stop()
    await ctx.send(embed=discord.Embed(description="⏹️ Đã dừng và xóa queue.", color=0xFF6B6B))


# ── PAUSE ──────────────────────────────────────────────────────
@bot.command(name="pause")
async def cmd_pause(ctx):
    """Tạm dừng nhạc."""
    state = gs(ctx.guild)
    if state.playing():
        state.voice.pause()
        await ctx.send(embed=discord.Embed(description="⏸️ Đã tạm dừng.", color=0xFFA500))
    elif state.paused():
        await ctx.send("⚠️ Nhạc đã đang bị tạm dừng rồi.")
    else:
        await ctx.send("❌ Không có bài nào đang phát.")


# ── RESUME ──────────────────────────────────────────────────────
@bot.command(name="resume", aliases=["r", "unpause"])
async def cmd_resume(ctx):
    """Tiếp tục phát nhạc."""
    state = gs(ctx.guild)
    if state.paused():
        state.voice.resume()
        await ctx.send(embed=discord.Embed(description="▶️ Đã tiếp tục phát.", color=0x1DB954))
    elif state.playing():
        await ctx.send("⚠️ Nhạc đang phát bình thường rồi.")
    else:
        await ctx.send("❌ Không có bài nào đang phát.")


# ── NOW PLAYING ────────────────────────────────────────────────
@bot.command(name="nowplaying", aliases=["np", "current"])
async def cmd_np(ctx):
    """Xem bài đang phát."""
    state = gs(ctx.guild)
    if not state.current:
        return await ctx.send(embed=discord.Embed(description="❌ Không có bài nào đang phát.", color=0xFF6B6B))
    view = PlayerView(ctx.guild, ctx.channel)
    await ctx.send(embed=state.current.embed(paused=state.paused()), view=view)


# ── QUEUE (UX 2 — Phân trang) ────────────────────────────────
@bot.command(name="queue", aliases=["q", "list"])
async def cmd_queue(ctx, page: int = 1):
    """Xem danh sách phát. VD: queue 2"""
    state = gs(ctx.guild)
    if not state.current and not state.queue:
        return await ctx.send(embed=discord.Embed(description="📭 Queue trống.", color=0xAAAAAA))

    per_page = 10
    total_pages = max(1, (len(state.queue) + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))

    lines = []
    if state.current:
        status = "⏸" if state.paused() else "🎵"
        lines.append(f"{status} **Đang phát:** [{state.current.title}]({state.current.webpage_url}) `[{state.current.duration_fmt}]`")

    start = (page - 1) * per_page
    end = start + per_page
    for i, s in enumerate(state.queue[start:end], start + 1):
        lines.append(f"`{i:>2}.` [{s.title}]({s.webpage_url}) `[{s.duration_fmt}]`")

    if not state.queue:
        lines.append("_Queue trống — thêm bài bằng `!play`_")

    e = discord.Embed(
        title=f"📋 Queue — {len(state.queue)} bài đang chờ",
        description="\n".join(lines),
        color=0x1DB954,
    )
    e.set_footer(text=f"Trang {page}/{total_pages}  |  Loop: {state.loop_label()}  |  🔊 Vol: {int(state.volume*100)}%")
    await ctx.send(embed=e)


# ── LOOP (UX 4 — Loop single + Bug 1 Fix) ────────────────────
@bot.command(name="loop", aliases=["l", "repeat"])
async def cmd_loop(ctx):
    """Bật/tắt loop: OFF → Playlist → Single → OFF."""
    state = gs(ctx.guild)

    # Cycle: OFF → PLAYLIST → SINGLE → OFF
    if state.loop == LOOP_OFF:
        state.loop = LOOP_PLAYLIST
        state.history.clear()
        # [Bug 1 Fix] Chỉ add current — advance() sẽ tự ghi các bài từ queue
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


# ── SHUFFLE (Bug 2 Fix) ──────────────────────────────────────
@bot.command(name="shuffle", aliases=["sh"])
async def cmd_shuffle(ctx):
    """Xáo trộn danh sách phát."""
    state = gs(ctx.guild)
    if not state.queue:
        return await ctx.send("❌ Queue trống.")
    random.shuffle(state.queue)
    # [Bug 2 Fix] Trộn cả history để vòng loop sau cũng trộn
    if state.loop == LOOP_PLAYLIST and state.history:
        random.shuffle(state.history)
    await ctx.send(embed=discord.Embed(description="🔀 Đã xáo trộn queue!", color=0x1DB954))


# ── VOLUME ────────────────────────────────────────────────────
@bot.command(name="volume", aliases=["vol", "v"])
async def cmd_volume(ctx, vol: int):
    """Chỉnh âm lượng 0–100. VD: volume 80"""
    if not 0 <= vol <= 100:
        return await ctx.send("❌ Âm lượng phải từ 0 đến 100.")
    state = gs(ctx.guild)
    state.volume = vol / 100
    if state.voice and state.voice.source:
        state.voice.source.volume = state.volume
    bar = "█" * (vol // 10) + "░" * (10 - vol // 10)
    await ctx.send(embed=discord.Embed(description=f"🔊 **{vol}%** `{bar}`", color=0x1DB954))


# ── REMOVE (UX 3 — Xóa khoảng + Bug 4 Fix) ──────────────────
@bot.command(name="remove", aliases=["rm", "del"])
async def cmd_remove(ctx, *, arg: str):
    """Xóa bài khỏi queue. VD: remove 3 hoặc remove 3-10"""
    state = gs(ctx.guild)

    # Parse "3-10" range or single number
    range_match = re.match(r"(\d+)\s*-\s*(\d+)", arg)
    if range_match:
        start_idx = int(range_match.group(1))
        end_idx = int(range_match.group(2))
        if start_idx > end_idx:
            start_idx, end_idx = end_idx, start_idx
        if not (1 <= start_idx <= len(state.queue)) or not (1 <= end_idx <= len(state.queue)):
            return await ctx.send(f"❌ Khoảng không hợp lệ (queue có {len(state.queue)} bài).")
        removed = state.queue[start_idx - 1 : end_idx]
        del state.queue[start_idx - 1 : end_idx]
        # [Bug 4 Fix] Xóa khỏi history khi đang loop
        if state.loop == LOOP_PLAYLIST:
            for s in removed:
                try: state.history.remove(s)
                except ValueError: pass
        await ctx.send(embed=discord.Embed(
            description=f"🗑️ Đã xóa **{len(removed)} bài** (#{start_idx}–#{end_idx}).", color=0xFF6B6B
        ))
    else:
        try:
            index = int(arg)
        except ValueError:
            return await ctx.send("❌ Sai cú pháp. VD: `!remove 3` hoặc `!remove 3-10`")
        if not 1 <= index <= len(state.queue):
            return await ctx.send(f"❌ Số thứ tự không hợp lệ (queue có {len(state.queue)} bài).")
        removed = state.queue.pop(index - 1)
        # [Bug 4 Fix] Xóa khỏi history khi đang loop
        if state.loop == LOOP_PLAYLIST:
            try: state.history.remove(removed)
            except ValueError: pass
        await ctx.send(embed=discord.Embed(description=f"🗑️ Đã xóa: **{removed.title}**", color=0xFF6B6B))


# ── CLEAR ────────────────────────────────────────────────────
@bot.command(name="clear", aliases=["c", "clearqueue"])
async def cmd_clear(ctx):
    """Xóa toàn bộ hàng chờ (giữ bài đang phát)."""
    state = gs(ctx.guild)
    count = len(state.queue)
    state.queue.clear(); state.history.clear()
    await ctx.send(embed=discord.Embed(description=f"🗑️ Đã xóa {count} bài khỏi hàng chờ.", color=0xFF6B6B))


# ── JOIN / LEAVE ──────────────────────────────────────────────
@bot.command(name="join", aliases=["j"])
async def cmd_join(ctx):
    """Kéo bot vào kênh của bạn."""
    if not ctx.author.voice:
        return await ctx.send("❌ Bạn chưa vào voice channel nào!")
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
        await ctx.send("❌ Bot chưa ở trong kênh nào.")


# ── HELP ────────────────────────────────────────────────────
@bot.command(name="help", aliases=["h"])
async def cmd_help(ctx):
    """Xem toàn bộ lệnh."""
    p = PREFIX if PREFIX else ""
    e = discord.Embed(title="🎵 Music Bot — Danh sách lệnh", color=0x1DB954)
    e.add_field(name="▶️ Phát nhạc", inline=False, value=
        f"`{p}play <tên/URL>` — Phát từ YouTube hoặc SoundCloud\n"
        f"`{p}search <tên>` — Tìm và chọn bài bằng Dropdown\n"
        f"`{p}playnext <tên/URL>` — Thêm vào **đầu** queue\n"
        f"`{p}skip` / `{p}s` — Bỏ qua bài\n"
        f"`{p}stop` — Dừng & xóa queue\n"
        f"`{p}pause` — Tạm dừng\n"
        f"`{p}resume` / `{p}r` — Tiếp tục\n"
        f"`{p}np` — Xem bài đang phát"
    )
    e.add_field(name="📋 Queue", inline=False, value=
        f"`{p}queue [trang]` / `{p}q` — Xem danh sách (VD: `{p}q 2`)\n"
        f"`{p}shuffle` / `{p}sh` — Xáo trộn queue\n"
        f"`{p}loop` / `{p}l` — Chuyển loop: OFF → Playlist → Single → OFF\n"
        f"`{p}remove <số>` / `{p}rm` — Xóa 1 bài (VD: `{p}rm 3-10`)\n"
        f"`{p}clear` — Xóa sạch hàng chờ (giữ bài đang phát)"
    )
    e.add_field(name="🔊 Điều chỉnh", inline=False, value=
        f"`{p}volume <0-100>` / `{p}vol` — Chỉnh âm lượng\n"
        f"`{p}join` — Kéo bot vào kênh\n"
        f"`{p}leave` / `{p}dc` — Đuổi bot ra khỏi kênh"
    )
    e.add_field(name="💡 Tính năng", inline=False, value=
        "• Bot **không tự rời kênh** (24/7)\n"
        "• Hỗ trợ **YouTube, SoundCloud, Playlist**\n"
        "• Bật `loop` → tự phát lại khi hết nhạc\n"
        "• Nút bấm **[⏸ ⏭ ⏹ 🔀]** trên tin nhắn Now Playing"
    )
    e.set_footer(text="Made with ❤️ by MRX")
    await ctx.send(embed=e)


# ─────────────────────────────────────────────────────────────
#  EVENTS
# ─────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ {bot.user} online! Prefix: '{PREFIX}'")
    p = PREFIX or ""
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening, name=f"{p}play | Music Bot"
    ))

# [Stability Fix] Dọn state khi bot bị kick khỏi voice
@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if member.id != bot.user.id:
        return
    # Bot bị disconnect (bởi admin hoặc mất kết nối)
    if before.channel is not None and after.channel is None:
        state = gs(member.guild)
        state.reset()
        state.voice = None
        print(f"[Voice] Bot bị disconnect khỏi {before.channel.name} — state đã reset.")
    # Bot bị kéo sang kênh khác
    elif before.channel is not None and after.channel is not None and before.channel != after.channel:
        state = gs(member.guild)
        if state.voice and state.playing():
            state.voice.pause()
            bot.loop.create_task(asyncio.sleep(0.5))
            state.voice.resume()

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CommandOnCooldown):
        return await ctx.send(f"⏳ Chờ {error.retry_after:.1f}s trước khi dùng lại lệnh này.", delete_after=5)
    if isinstance(error, commands.MissingRequiredArgument):
        return await ctx.send(f"❌ Thiếu tham số! Gõ `{PREFIX}help` để xem hướng dẫn.")
    if isinstance(error, commands.BadArgument):
        return await ctx.send("❌ Tham số không hợp lệ.")
    if isinstance(error, commands.CommandError):
        return await ctx.send(str(error))
    print(f"[Error] {ctx.command}: {error}")

# ─────────────────────────────────────────────────────────────
#  RUN
# ─────────────────────────────────────────────────────────────
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN: raise ValueError("Thiếu DISCORD_TOKEN trong .env!")
bot.run(TOKEN)
