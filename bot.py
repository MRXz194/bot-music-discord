# ============================================================
#  🎵 Discord Music Bot — Jockie Music Clone
#  Stack : discord.py 2.7 + yt-dlp + FFmpeg
# ============================================================
import os, asyncio, random, datetime, discord
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
    "socket_timeout": 10,
    "retries": 3,
    "extract_flat": "in_playlist",
    "extractor_args": {"youtube": {"skip": ["dash", "hls"], "player_client": ["android", "web"]}},
}

FFMPEG_OPTS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -probesize 10M",
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
#  GUILD STATE
# ─────────────────────────────────────────────────────────────
class GuildState:
    def __init__(self, guild):
        self.guild    = guild
        self.queue:   list[Song] = []
        self.history: list[Song] = []
        self.current: Song | None = None
        self.loop     = False
        self.volume   = 0.5
        self.voice:   discord.VoiceClient | None = None
        self._adv     = False   # advancing guard

    def playing(self): return self.voice is not None and self.voice.is_playing()
    def paused(self):  return self.voice is not None and self.voice.is_paused()

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
            timeout=30.0
        )
    except asyncio.TimeoutError:
        raise commands.CommandError("⏳ Tìm kiếm quá lâu! Thử dùng URL trực tiếp.")

    if not data: return []
    entries = data.get("entries", [data]) if "entries" in data else [data]
    return [e for e in entries if e]

async def refresh_song(song: Song):
    """Re-extract fresh stream URL and full metadata right before playback."""
    try:
        opts = dict(YTDL_OPTS)
        opts["extract_flat"] = False   # need full info for single URL
        opts["noplaylist"] = True      # CRITICAL: only extract the single track
        ytdl = yt_dlp.YoutubeDL(opts)
        data = await asyncio.wait_for(
            bot.loop.run_in_executor(None, lambda: ytdl.extract_info(song.webpage_url, download=False)),
            timeout=15.0
        )
        if data:
            song.url       = data.get("url", song.url)
            song.title     = data.get("title", song.title) or "Unknown"
            song.duration  = data.get("duration", song.duration)
            song.thumbnail = data.get("thumbnail", song.thumbnail)
            song.uploader  = data.get("uploader", song.uploader) or "Unknown"
    except Exception as e:
        print(f"[Refresh Error] {e}")

def make_source(url: str, volume: float) -> discord.PCMVolumeTransformer:
    return discord.PCMVolumeTransformer(
        discord.FFmpegPCMAudio(url, **FFMPEG_OPTS), volume=volume
    )

# ─────────────────────────────────────────────────────────────
#  ADVANCE QUEUE
# ─────────────────────────────────────────────────────────────
async def advance(guild: discord.Guild, ch: discord.TextChannel):
    state = gs(guild)
    if state._adv: return
    state._adv = True
    try:
        # Sync voice
        vc = state.voice or guild.voice_client
        if not vc or not vc.is_connected():
            state.current = None; state._adv = False; return
        state.voice = vc

        # Pick song
        if state.queue:
            song = state.queue.pop(0)
        elif state.loop and state.history:
            state.queue = list(state.history); state.history.clear()
            song = state.queue.pop(0)
            try: await ch.send(embed=discord.Embed(description="🔁 Loop – Phát lại từ đầu!", color=0x1DB954))
            except: pass
        else:
            state.current = None
            try: await ch.send(embed=discord.Embed(description="✅ Hết nhạc. Bot vẫn ngồi chờ ~ Thêm bài vào nào!", color=0x1DB954))
            except: pass
            state._adv = False; return

        # Refresh track metadata and URL
        await refresh_song(song)

        state.current = song
        if state.loop: state.history.append(song)

        def after(err):
            if err: print(f"[Player] {err}")
            state._adv = False
            asyncio.run_coroutine_threadsafe(advance(guild, ch), bot.loop)

        src = make_source(song.url, state.volume)
        if state.voice and state.voice.is_connected():
            state.voice.play(src, after=after)
            pos = len(state.history) if state.loop else 1
            tot = len(state.history)+len(state.queue) if state.loop else len(state.queue)+1
            try: await ch.send(embed=song.embed(pos, tot))
            except: pass
        else:
            state._adv = False
    except Exception as e:
        print(f"[Advance] {e}")
        state._adv = False
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
#  COMMANDS
# ─────────────────────────────────────────────────────────────

# ── PLAY ──────────────────────────────────────────────────────
@bot.command(name="play", aliases=["p"])
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
    if state.loop: state.history.extend(songs)

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
    await ctx.send(embed=state.current.embed(paused=state.paused()))


# ── QUEUE ──────────────────────────────────────────────────────
@bot.command(name="queue", aliases=["q", "list"])
async def cmd_queue(ctx):
    """Xem danh sách phát."""
    state = gs(ctx.guild)
    if not state.current and not state.queue:
        return await ctx.send(embed=discord.Embed(description="📭 Queue trống.", color=0xAAAAAA))

    lines = []
    if state.current:
        status = "⏸" if state.paused() else "🎵"
        lines.append(f"{status} **Đang phát:** [{state.current.title}]({state.current.webpage_url}) `[{state.current.duration_fmt}]`")

    for i, s in enumerate(state.queue[:15], 1):
        lines.append(f"`{i:>2}.` [{s.title}]({s.webpage_url}) `[{s.duration_fmt}]`")

    remaining = len(state.queue) - 15
    if remaining > 0:
        lines.append(f"\n_...và {remaining} bài nữa trong queue_")

    e = discord.Embed(
        title=f"📋 Queue — {len(state.queue)} bài đang chờ",
        description="\n".join(lines),
        color=0x1DB954,
    )
    e.set_footer(text=f"🔁 Loop: {'ON' if state.loop else 'OFF'}  |  🔊 Vol: {int(state.volume*100)}%")
    await ctx.send(embed=e)


# ── LOOP ──────────────────────────────────────────────────────
@bot.command(name="loop", aliases=["l", "repeat"])
async def cmd_loop(ctx):
    """Bật/tắt loop playlist."""
    state = gs(ctx.guild)
    state.loop = not state.loop
    state.history.clear()
    if state.loop:
        if state.current: state.history.append(state.current)
        state.history.extend(state.queue)
        desc = "🔁 Loop **BẬT** – Tự phát lại khi hết nhạc."
    else:
        desc = "➡️ Loop **TẮT**."
    await ctx.send(embed=discord.Embed(description=desc, color=0x1DB954))


# ── SHUFFLE ────────────────────────────────────────────────────
@bot.command(name="shuffle", aliases=["sh"])
async def cmd_shuffle(ctx):
    """Xáo trộn danh sách phát."""
    state = gs(ctx.guild)
    if not state.queue:
        return await ctx.send("❌ Queue trống.")
    random.shuffle(state.queue)
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


# ── REMOVE ────────────────────────────────────────────────────
@bot.command(name="remove", aliases=["rm", "del"])
async def cmd_remove(ctx, index: int):
    """Xóa bài khỏi queue theo số thứ tự. VD: remove 3"""
    state = gs(ctx.guild)
    if not 1 <= index <= len(state.queue):
        return await ctx.send(f"❌ Số thứ tự không hợp lệ (queue có {len(state.queue)} bài).")
    removed = state.queue.pop(index - 1)
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
        f"`{p}skip` / `{p}s` — Bỏ qua bài\n"
        f"`{p}stop` — Dừng & xóa queue\n"
        f"`{p}pause` — Tạm dừng\n"
        f"`{p}resume` / `{p}r` — Tiếp tục\n"
        f"`{p}np` — Xem bài đang phát"
    )
    e.add_field(name="📋 Queue", inline=False, value=
        f"`{p}queue` / `{p}q` — Xem danh sách phát\n"
        f"`{p}shuffle` / `{p}sh` — Xáo trộn queue\n"
        f"`{p}loop` / `{p}l` — Bật/tắt loop playlist\n"
        f"`{p}remove <số>` / `{p}rm` — Xóa 1 bài\n"
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
        "• Bật `loop` → tự phát lại khi hết nhạc"
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

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
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
