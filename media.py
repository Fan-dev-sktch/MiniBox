"""MiniBox 播放支持：媒体信息、封面、字幕、对浏览器放不了的格式实时转码串流。"""
from __future__ import annotations

import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path

import engine as E

# 浏览器（Edge/Chrome）能直接播的组合
DIRECT_CONTAINERS = {"mp4", "m4v", "mov", "webm", "mp3", "wav", "flac", "m4a", "aac", "ogg", "opus", "oga"}
DIRECT_VCODECS = {"h264", "vp8", "vp9", "av1"}
DIRECT_ACODECS = {"aac", "mp3", "opus", "vorbis", "flac", "pcm_s16le", "pcm_s24le", "pcm_f32le", "pcm_u8", "alac"}
TEXT_SUBS = {"subrip", "ass", "ssa", "mov_text", "webvtt", "text"}
SUB_EXT = (".srt", ".vtt", ".ass", ".ssa")


@lru_cache(maxsize=512)
def _probe(path: str, mtime: float) -> dict:
    """解析 `ffmpeg -i` 输出（不依赖 ffprobe）。"""
    info = {"duration": 0.0, "vcodec": None, "acodec": None, "width": 0, "height": 0, "subs": [], "tags": {}}
    if not E.TOOLS["ffmpeg"]:
        return info
    try:
        out = subprocess.run([E.TOOLS["ffmpeg"], "-hide_banner", "-i", path], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=30, creationflags=E.NO_WINDOW).stderr
    except Exception:
        return info
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", out)
    if m:
        info["duration"] = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    sub_idx = 0
    for line in out.splitlines():
        s = re.search(r"Stream #0:(\d+)(?:\[\w+\])?(?:\((\w+)\))?: (Video|Audio|Subtitle): (\w+)", line)
        if s:
            kind, codec, lang = s[3], s[4], s[2] or ""
            if kind == "Video" and not info["vcodec"] and "attached pic" not in line:
                info["vcodec"] = codec
                d = re.search(r", (\d{2,5})x(\d{2,5})", line)
                if d:
                    info["width"], info["height"] = int(d[1]), int(d[2])
            elif kind == "Audio" and not info["acodec"]:
                info["acodec"] = codec
            elif kind == "Subtitle":
                if codec in TEXT_SUBS:
                    info["subs"].append({"index": sub_idx, "lang": lang, "codec": codec})
                sub_idx += 1
            continue
        t = re.match(r"\s{4}(title|artist|album|album_artist)\s*:\s*(.+)", line)
        if t and t[1] not in info["tags"]:
            info["tags"][t[1]] = t[2].strip()
    return info


def probe(p: Path) -> dict:
    return _probe(str(p), p.stat().st_mtime)


def media_info(p: Path) -> dict:
    pr = probe(p)
    ext = E.ext_of(p.name)
    is_video = E.kind_of(p.name) == "video" and bool(pr["vcodec"])
    direct = ext in DIRECT_CONTAINERS and (not pr["vcodec"] or pr["vcodec"] in DIRECT_VCODECS or not is_video) \
        and (not pr["acodec"] or pr["acodec"] in DIRECT_ACODECS)
    if ext == "mov" and pr["vcodec"] not in (None, "h264"):
        direct = False
    tags = dict(pr["tags"])
    tags.update(_mutagen_tags(p))
    return {
        "duration": pr["duration"], "video": is_video, "direct": direct or not E.TOOLS["ffmpeg"],
        "width": pr["width"], "height": pr["height"], "vcodec": pr["vcodec"], "acodec": pr["acodec"],
        "title": tags.get("title") or E.stem_of(p.name), "artist": tags.get("artist") or tags.get("album_artist") or "",
        "album": tags.get("album") or "", "subs": subtitle_list(p, pr),
        "cover": not is_video and cover(p) is not None,
    }


def _mutagen_tags(p: Path) -> dict:
    try:
        import mutagen

        f = mutagen.File(p, easy=True)
        if not f or not f.tags:
            return {}
        out = {}
        for k in ("title", "artist", "album"):
            v = f.tags.get(k)
            if v:
                out[k] = str(v[0]) if isinstance(v, list) else str(v)
        return out
    except Exception:
        return {}


def cover(p: Path) -> tuple[bytes, str] | None:
    """内嵌封面，或同目录 cover/folder.jpg。"""
    try:
        import mutagen

        f = mutagen.File(p)
        if f is not None:
            pics = getattr(f, "pictures", None)  # FLAC / OGG
            if pics:
                return pics[0].data, pics[0].mime or "image/jpeg"
            tags = f.tags or {}
            for k in list(getattr(tags, "keys", lambda: [])()):
                if str(k).startswith("APIC"):
                    a = tags[k]
                    return a.data, a.mime or "image/jpeg"
            covr = tags.get("covr") if hasattr(tags, "get") else None
            if covr:
                data = bytes(covr[0])
                return data, "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
    except Exception:
        pass
    for name in ("cover.jpg", "cover.png", "folder.jpg", "folder.png", "Cover.jpg", "Folder.jpg", "front.jpg"):
        c = p.parent / name
        if c.is_file():
            return c.read_bytes(), "image/png" if name.endswith("png") else "image/jpeg"
    return None


def subtitle_list(p: Path, pr: dict | None = None) -> list[dict]:
    if E.kind_of(p.name) != "video":
        return []
    pr = pr or probe(p)
    stem = E.stem_of(p.name).lower()
    subs = []
    try:
        for c in sorted(p.parent.iterdir()):
            n = c.name.lower()
            if c.is_file() and n.endswith(SUB_EXT) and (n.startswith(stem + ".") or E.stem_of(n) == stem):
                label = c.name[len(stem):].strip(". ") or c.suffix[1:]
                subs.append({"id": f"file:{c.name}", "label": f"外挂 {label}"})
    except OSError:
        pass
    for s in pr["subs"]:
        subs.append({"id": f"embed:{s['index']}", "label": f"内嵌 {s['lang'] or s['index'] + 1}".strip()})
    return subs


def _srt_to_vtt(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    text = re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", text)
    return "WEBVTT\n\n" + text


def _read_text(f: Path) -> str:
    raw = f.read_bytes()
    for enc in ("utf-8-sig", "gb18030", "big5", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def subtitle_vtt(p: Path, sid: str) -> str:
    kind, _, ref = sid.partition(":")
    if kind == "file":
        f = p.parent / Path(ref).name
        if not f.is_file():
            raise E.UserError("字幕文件不存在")
        if f.suffix.lower() == ".vtt":
            return _read_text(f)
        if f.suffix.lower() == ".srt":
            return _srt_to_vtt(_read_text(f))
        src = ["-sub_charenc", "UTF-8", "-i", str(f)]
        # ass/ssa：先转成 UTF-8 再交给 ffmpeg
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=f.suffix, delete=False, encoding="utf-8") as tf:
            tf.write(_read_text(f))
        src = ["-i", tf.name]
        try:
            return _ff_vtt(src)
        finally:
            Path(tf.name).unlink(missing_ok=True)
    if kind == "embed":
        return _ff_vtt(["-i", str(p), "-map", f"0:s:{int(ref)}"])
    raise E.UserError("未知字幕")


def _ff_vtt(args: list[str]) -> str:
    r = subprocess.run([E.TOOLS["ffmpeg"], "-hide_banner", "-loglevel", "error", *args, "-f", "webvtt", "pipe:1"],
                       capture_output=True, timeout=120, creationflags=E.NO_WINDOW)
    if r.returncode != 0:
        raise E.UserError("字幕转换失败")
    return r.stdout.decode("utf-8", "replace")


def stream_cmd(p: Path, start: float, audio_only: bool) -> tuple[list[str], str]:
    """实时转码命令，返回 (命令, MIME)。"""
    pr = probe(p)
    ff = E.TOOLS["ffmpeg"]
    base = [ff, "-hide_banner", "-loglevel", "error", "-nostdin"]
    if start > 0:
        base += ["-ss", f"{start:.2f}"]
    base += ["-i", str(p)]
    if audio_only or not pr["vcodec"]:
        return base + ["-vn", "-c:a", "libmp3lame", "-b:a", "256k", "-f", "mp3", "pipe:1"], "audio/mpeg"
    v = ["-map", "0:v:0", "-map", "0:a:0?"]
    if os.environ.get("MINIBOX_STREAM") == "webm":  # 备用：不支持 H.264 的浏览器（也用于自动化测试）
        return base + v + ["-c:v", "libvpx", "-deadline", "realtime", "-cpu-used", "8", "-b:v", "2M",
                           "-c:a", "libopus", "-b:a", "128k", "-f", "webm", "pipe:1"], "video/webm"
    if pr["vcodec"] == "h264":
        v += ["-c:v", "copy"]
    else:
        v += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
              "-vf", "scale='min(1920,iw)':-2"]
    if pr["acodec"] in ("aac",):
        v += ["-c:a", "copy"]
    else:
        v += ["-c:a", "aac", "-b:a", "192k", "-ac", "2"]
    v += ["-movflags", "frag_keyframe+empty_moov+default_base_moof", "-f", "mp4", "pipe:1"]
    return base + v, "video/mp4"


def stream(p: Path, start: float, audio_only: bool):
    cmd, mime = stream_cmd(p, start, audio_only)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=E.NO_WINDOW)

    def gen():
        try:
            assert proc.stdout
            while True:
                chunk = proc.stdout.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                proc.kill()
            except Exception:
                pass

    return gen(), mime
