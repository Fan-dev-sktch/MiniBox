"""MiniBox 处理引擎：解压 / 打包 / 转换。全部在本机完成，不联网。"""
from __future__ import annotations

import bz2
import gzip
import lzma
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import zipfile
from pathlib import Path

# ---------------------------------------------------------------- 外部工具探测

IS_WIN = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"
NO_WINDOW = 0x08000000 if IS_WIN else 0  # Windows 下不弹黑框


def _which(*names: str, extra: tuple[str, ...] = ()) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    for p in extra:
        if p and Path(p).exists():
            return p
    return None


def _find_ffmpeg() -> str | None:
    import sys
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    own = base / "bin" / ("ffmpeg.exe" if IS_WIN else "ffmpeg")  # 安装包自带的精简版
    if own.is_file():
        return str(own)
    p = _which("ffmpeg")
    if p:
        return p
    try:  # pip 包自带的 ffmpeg，免去单独安装
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _find_bsdtar() -> str | None:
    # Windows 10+ 自带的 tar.exe 和 macOS 的 tar 都是 bsdtar，能解 rar/7z/iso
    for cand in ("bsdtar", "tar"):
        p = shutil.which(cand)
        if not p:
            continue
        try:
            out = subprocess.run([p, "--version"], capture_output=True, text=True, timeout=5,
                                 creationflags=NO_WINDOW).stdout
            if "bsdtar" in out or "libarchive" in out:
                return p
        except Exception:
            pass
    return None


PF = os.environ.get("ProgramFiles", r"C:\Program Files")
PF86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")

TOOLS = {
    "ffmpeg": _find_ffmpeg(),
    "soffice": _which("soffice", "libreoffice", extra=(
        rf"{PF}\LibreOffice\program\soffice.exe", rf"{PF86}\LibreOffice\program\soffice.exe",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice")),
    "7z": _which("7z", "7zz", "7za", extra=(rf"{PF}\7-Zip\7z.exe", rf"{PF86}\7-Zip\7z.exe")),
    "bsdtar": _find_bsdtar(),
    "unrar": _which("unrar", "unar"),
}

import importlib.util as _ilu

# 重量级库（HEIC 解码、PDF、7z）只在第一次用到时才加载，启动更快
HEIF = _ilu.find_spec("pillow_heif") is not None
PDF = _ilu.find_spec("pypdfium2") is not None
PY7Z = _ilu.find_spec("py7zr") is not None
_heif_ready = False


def ensure_heif() -> None:
    global _heif_ready
    if HEIF and not _heif_ready:
        _heif_ready = True  # 只尝试一次；HEIC 组件坏了也不影响其他图片
        try:
            import pillow_heif

            pillow_heif.register_heif_opener()
        except Exception:  # noqa: BLE001
            pass


from PIL import Image, ImageOps, features  # noqa: E402

AVIF = bool(features.check("avif")) if hasattr(features, "check") else False


class Cancelled(Exception):
    pass


class UserError(Exception):
    """给用户看的错误信息。"""


# ---------------------------------------------------------------- 格式表

IMAGE_IN = {"jpg", "jpeg", "png", "webp", "gif", "bmp", "tif", "tiff", "ico", "heic", "heif", "avif"}
VIDEO_IN = {"mp4", "mkv", "mov", "avi", "webm", "flv", "wmv", "m4v", "ts", "3gp", "mpg", "mpeg"}
AUDIO_IN = {"mp3", "wav", "flac", "aac", "m4a", "ogg", "opus", "wma", "aiff", "aif", "amr"}
WORD_IN = {"doc", "docx", "odt", "rtf", "txt", "md", "wps"}
SHEET_IN = {"xls", "xlsx", "ods", "csv", "et"}
SLIDE_IN = {"ppt", "pptx", "odp", "dps"}
ARCHIVE_EXT = ("tar.gz", "tar.bz2", "tar.xz", "tar.zst", "tgz", "tbz2", "txz", "tar", "zip", "7z", "rar",
               "gz", "bz2", "xz", "iso", "cab", "jar", "apk", "zst")


def ext_of(name: str) -> str:
    low = name.lower()
    for multi in ("tar.gz", "tar.bz2", "tar.xz", "tar.zst"):
        if low.endswith("." + multi):
            return multi
    return low.rsplit(".", 1)[-1] if "." in low else ""


def kind_of(name: str, is_dir: bool = False) -> str:
    if is_dir:
        return "dir"
    e = ext_of(name)
    if e in IMAGE_IN:
        return "image"
    if e in VIDEO_IN:
        return "video"
    if e in AUDIO_IN:
        return "audio"
    if e == "pdf":
        return "pdf"
    if e in WORD_IN | SHEET_IN | SLIDE_IN:
        return "doc"
    if e in ARCHIVE_EXT:
        return "archive"
    return "other"


def targets_for(name: str) -> list[str]:
    """某个文件能转成哪些格式（按本机可用工具过滤）。"""
    e = ext_of(name)
    ff, lo = bool(TOOLS["ffmpeg"]), bool(TOOLS["soffice"])
    out: list[str] = []
    if e in IMAGE_IN:
        out = ["jpg", "png", "webp"] + (["avif"] if AVIF else []) + ["gif", "bmp", "tiff", "ico", "pdf"]
        if e == "gif" and ff:
            out += ["mp4", "webm"]
    elif e in VIDEO_IN and ff:
        out = ["mp4", "webm", "mkv", "mov", "gif", "mp3", "wav", "m4a"]
    elif e in AUDIO_IN and ff:
        out = ["mp3", "wav", "flac", "m4a", "ogg", "opus"]
    elif e == "pdf" and PDF:
        out = ["png", "jpg", "txt"]
        if lo:
            out.append("docx")
    elif e in WORD_IN and lo:
        out = ["pdf", "docx", "odt", "rtf", "txt", "html"]
    elif e in SHEET_IN and lo:
        out = ["pdf", "xlsx", "ods", "csv"]
    elif e in SLIDE_IN and lo:
        out = ["pdf", "pptx", "odp"]
    norm = {"jpeg": "jpg", "tif": "tiff"}.get(e, e)
    # 同格式也保留（图片/音视频 = 压缩重编码）
    if norm in out and kind_of(name) not in ("image", "video", "audio"):
        out.remove(norm)
    return out


def is_archive(name: str) -> bool:
    return kind_of(name) == "archive"


# ---------------------------------------------------------------- 工具函数

def unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    stem, suf = p.name, ""
    e = ext_of(p.name)
    if e and not p.is_dir():
        suf = "." + e
        stem = p.name[: -len(suf)]
    i = 1
    while True:
        cand = p.with_name(f"{stem} ({i}){suf}")
        if not cand.exists():
            return cand
        i += 1


def stem_of(name: str) -> str:
    e = ext_of(name)
    return name[: -(len(e) + 1)] if e else name


def safe_join(base: Path, member: str) -> Path | None:
    member = member.replace("\\", "/").lstrip("/")
    if re.match(r"^[a-zA-Z]:", member):
        member = member[2:].lstrip("/")
    parts = [p for p in member.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts) or not parts:
        return None
    target = base.joinpath(*parts)
    try:
        target.resolve().relative_to(base.resolve())
    except ValueError:
        return None
    return target


def run(cmd: list[str], job=None, on_line=None, cwd=None, timeout=None) -> str:
    """运行外部命令；支持取消；可逐行回调 stdout。"""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=cwd,
                            text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    if job is not None:
        job.proc = proc
    tail: list[str] = []
    try:
        assert proc.stdout
        for line in proc.stdout:
            if job is not None and job.cancelled:
                proc.kill()
                raise Cancelled()
            tail.append(line)
            if len(tail) > 40:
                tail.pop(0)
            if on_line:
                on_line(line)
        proc.wait(timeout=timeout)
    finally:
        if job is not None:
            job.proc = None
    if job is not None and job.cancelled:
        raise Cancelled()
    if proc.returncode != 0:
        lines = [x.strip() for x in tail if x.strip()]
        key = next((x for x in reversed(lines) if "rror" in x or "nvalid" in x), lines[-1] if lines else "")
        if "Invalid data" in key or "moov atom not found" in "".join(lines):
            raise UserError("无法读取这个文件，可能已损坏")
        if "No space left" in "".join(lines):
            raise UserError("磁盘空间不足")
        raise UserError(f"转换失败：{key[-160:]}")
    return "".join(tail)


# ---------------------------------------------------------------- 解压

def _fix_zip_name(info: zipfile.ZipInfo) -> str:
    name = info.filename
    if info.flag_bits & 0x800:
        return name
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        return name
    for enc in ("utf-8", "gbk", "big5", "shift_jis"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return name


def _extract_zip(src: Path, dest: Path, password: str | None, job) -> None:
    with zipfile.ZipFile(src) as zf:
        if password:
            zf.setpassword(password.encode("utf-8"))
        infos = zf.infolist()
        total = sum(i.file_size for i in infos) or 1
        done = 0
        for info in infos:
            if job and job.cancelled:
                raise Cancelled()
            target = safe_join(dest, _fix_zip_name(info))
            if target is None:
                continue
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with zf.open(info) as fsrc, open(target, "wb") as fdst:
                    shutil.copyfileobj(fsrc, fdst, 1 << 20)
            except RuntimeError as e:
                if "password" in str(e).lower() or "encrypted" in str(e).lower():
                    raise UserError("这个压缩包有密码，请填写正确的密码") from None
                raise
            except NotImplementedError:
                raise UserError("这个 zip 用了不支持的加密/压缩方式（如 AES），装个 7-Zip 后可解") from None
            done += info.file_size
            if job:
                job.sub(done / total)


def _extract_tar(src: Path, dest: Path, job) -> None:
    with tarfile.open(src) as tf:
        if hasattr(tarfile, "data_filter"):
            tf.extractall(dest, filter="data")
        else:
            members = [m for m in tf.getmembers() if safe_join(dest, m.name) and not (m.issym() or m.islnk())]
            tf.extractall(dest, members=members)


def _extract_single(src: Path, dest: Path, opener) -> None:
    out = dest / stem_of(src.name)
    with opener(src, "rb") as fsrc, open(out, "wb") as fdst:
        shutil.copyfileobj(fsrc, fdst, 1 << 20)


def _extract_7z_py(src: Path, dest: Path, password: str | None) -> None:
    import py7zr

    try:
        with py7zr.SevenZipFile(src, mode="r", password=password or None) as z:
            z.extractall(path=dest)
    except py7zr.exceptions.PasswordRequired:
        raise UserError("这个压缩包有密码，请填写密码") from None
    except Exception as e:
        if "password" in str(e).lower() or "crc" in str(e).lower():
            raise UserError("密码不对，或压缩包已损坏") from None
        raise


def _extract_external(src: Path, dest: Path, password: str | None, job) -> None:
    if TOOLS["7z"]:
        cmd = [TOOLS["7z"], "x", "-y", f"-o{dest}", f"-p{password or ''}", str(src)]
        run(cmd, job)
        return
    if TOOLS["bsdtar"] and not password:
        run([TOOLS["bsdtar"], "-xf", str(src), "-C", str(dest)], job)
        return
    if ext_of(src.name) == "rar":
        try:
            import rarfile

            with rarfile.RarFile(src) as rf:
                if password:
                    rf.setpassword(password)
                rf.extractall(dest)
            return
        except ImportError:
            pass
        except Exception as e:
            raise UserError(f"RAR 解压失败：{e}") from None
    raise UserError("解这种格式需要 7-Zip（Windows/Mac/Linux 都免费），装好后重启即可")


def extract(src: Path, password: str | None = None, job=None) -> Path:
    """智能解压：压缩包里只有一个顶层文件夹时直接放出，否则放进同名文件夹。"""
    parent = src.parent
    tmp = Path(tempfile.mkdtemp(prefix=".minibox-", dir=parent))
    try:
        e = ext_of(src.name)
        if e in ("zip", "jar", "apk") or (e not in ARCHIVE_EXT and zipfile.is_zipfile(src)):
            _extract_zip(src, tmp, password, job)
        elif e in ("tar", "tgz", "tbz2", "txz", "tar.gz", "tar.bz2", "tar.xz"):
            _extract_tar(src, tmp, job)
        elif e == "gz":
            _extract_single(src, tmp, gzip.open)
        elif e == "bz2":
            _extract_single(src, tmp, bz2.open)
        elif e == "xz":
            _extract_single(src, tmp, lzma.open)
        elif e == "7z" and PY7Z and not TOOLS["7z"]:
            _extract_7z_py(src, tmp, password)
        else:
            _extract_external(src, tmp, password, job)

        items = list(tmp.iterdir())
        if not items:
            raise UserError("压缩包是空的")
        if len(items) == 1:
            final = unique_path(parent / items[0].name)
            shutil.move(str(items[0]), str(final))
            return final
        final = unique_path(parent / stem_of(src.name))
        shutil.move(str(tmp), str(final))
        return final
    except zipfile.BadZipFile:
        raise UserError("不是有效的 zip 文件，或文件已损坏") from None
    except tarfile.TarError as e:
        raise UserError(f"tar 文件读取失败：{e}") from None
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 打包

def _walk(paths: list[Path]):
    """生成 (绝对路径, 包内路径)。"""
    for p in paths:
        if p.is_dir():
            yield p, p.name + "/"
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if not d.startswith(".minibox-")]
                r = Path(root)
                for d in dirs:
                    yield r / d, (r / d).relative_to(p.parent).as_posix() + "/"
                for f in files:
                    yield r / f, (r / f).relative_to(p.parent).as_posix()
        else:
            yield p, p.name


def compress(paths: list[Path], fmt: str, out_name: str | None, password: str | None, job=None) -> Path:
    parent = paths[0].parent
    if not out_name:
        out_name = stem_of(paths[0].name) if len(paths) == 1 else (parent.name or "压缩包")
    out_name = re.sub(r'[\\/:*?"<>|]', "_", out_name).strip() or "压缩包"
    suffix = {"zip": ".zip", "7z": ".7z", "tar.gz": ".tar.gz"}[fmt]
    out = unique_path(parent / (out_name + suffix))
    entries = list(_walk(paths))
    total = sum(a.stat().st_size for a, n in entries if not n.endswith("/")) or 1
    done = 0
    try:
        if fmt == "zip":
            if password:
                raise UserError("zip 加密不安全，要加密请选 7z 格式")
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                for a, n in entries:
                    if job and job.cancelled:
                        raise Cancelled()
                    zf.write(a, n)
                    if not n.endswith("/"):
                        done += a.stat().st_size
                        if job:
                            job.sub(done / total)
        elif fmt == "7z":
            import py7zr

            kw = {"password": password, "header_encryption": True} if password else {}
            with py7zr.SevenZipFile(out, "w", **kw) as z:
                for a, n in entries:
                    if job and job.cancelled:
                        raise Cancelled()
                    if n.endswith("/"):
                        continue
                    z.write(a, n)
                    done += a.stat().st_size
                    if job:
                        job.sub(done / total)
        else:
            with tarfile.open(out, "w:gz") as tf:
                for p in paths:
                    if job and job.cancelled:
                        raise Cancelled()
                    tf.add(p, arcname=p.name)
    except BaseException:
        out.unlink(missing_ok=True)
        raise
    return out


# ---------------------------------------------------------------- 转换：图片

def _flatten(img: Image.Image, bg=(255, 255, 255)) -> Image.Image:
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        base = Image.new("RGB", img.size, bg)
        base.paste(img, mask=img.split()[-1])
        return base
    return img.convert("RGB")


def _open_image(src: Path, max_side: int | None) -> Image.Image:
    ensure_heif()
    img = Image.open(src)
    img = ImageOps.exif_transpose(img) or img
    if max_side and max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.LANCZOS)
    return img


def convert_image(src: Path, out: Path, fmt: str, quality: int, max_side: int | None) -> None:
    try:
        _convert_image(src, out, fmt, quality, max_side)
    except UserError:
        raise
    except (OSError, SyntaxError, ValueError) as e:
        out.unlink(missing_ok=True)
        if "cannot identify" in str(e) or "truncated" in str(e) or isinstance(e, SyntaxError):
            raise UserError("无法读取这张图片，文件可能已损坏") from None
        raise


def _convert_image(src: Path, out: Path, fmt: str, quality: int, max_side: int | None) -> None:
    ensure_heif()
    with Image.open(src) as raw:
        animated = getattr(raw, "is_animated", False) and fmt in ("gif", "webp")
        if animated:
            frames, durations = [], []
            for i in range(raw.n_frames):
                raw.seek(i)
                f = raw.convert("RGBA")
                if max_side and max(f.size) > max_side:
                    f.thumbnail((max_side, max_side), Image.LANCZOS)
                frames.append(f)
                durations.append(raw.info.get("duration", 100))
            kw = {"save_all": True, "append_images": frames[1:], "duration": durations, "loop": 0}
            if fmt == "webp":
                kw["quality"] = quality
            frames[0].save(out, "GIF" if fmt == "gif" else "WEBP", **kw)
            return
    img = _open_image(src, max_side)
    if fmt == "jpg":
        _flatten(img).save(out, "JPEG", quality=quality, optimize=True, progressive=True)
    elif fmt == "png":
        (img if img.mode in ("RGB", "RGBA", "L", "LA", "P") else img.convert("RGBA")).save(out, "PNG", optimize=True)
    elif fmt == "webp":
        img.convert("RGBA" if "A" in img.getbands() else "RGB").save(out, "WEBP", quality=quality, method=5)
    elif fmt == "avif":
        img.convert("RGBA" if "A" in img.getbands() else "RGB").save(out, "AVIF", quality=quality)
    elif fmt == "gif":
        img.convert("RGBA").save(out, "GIF")
    elif fmt == "bmp":
        img.convert("RGBA" if "A" in img.getbands() else "RGB").save(out, "BMP")
    elif fmt == "tiff":
        img.save(out, "TIFF", compression="tiff_lzw")
    elif fmt == "ico":
        im = img.convert("RGBA")
        side = max(im.size)
        sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        sq.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
        if side < 256:  # 图标至少要 256×256，小图放大（否则会生成空图标）
            sq = sq.resize((256, 256), Image.LANCZOS)
        elif side > 256:
            sq = sq.resize((256, 256), Image.LANCZOS)
        sizes = [16, 24, 32, 48, 64, 128, 256]
        sq.save(out, "ICO", sizes=[(s, s) for s in sizes])
    elif fmt == "pdf":
        _flatten(img).save(out, "PDF", resolution=150)
    else:
        raise UserError(f"不支持转成 {fmt}")


def images_to_pdf(srcs: list[Path], out: Path, max_side: int | None, job=None) -> None:
    pages = []
    for i, s in enumerate(srcs):
        if job and job.cancelled:
            raise Cancelled()
        pages.append(_flatten(_open_image(s, max_side)))
        if job:
            job.sub((i + 1) / len(srcs))
    pages[0].save(out, "PDF", save_all=True, append_images=pages[1:], resolution=150)


# ---------------------------------------------------------------- 转换：PDF

def _open_pdf(src: Path):
    import pypdfium2 as pdfium

    try:
        return pdfium.PdfDocument(str(src))
    except pdfium.PdfiumError as e:
        if "password" in str(e).lower():
            raise UserError("这个 PDF 有密码保护，无法转换") from None
        raise UserError("无法打开这个 PDF，文件可能已损坏") from None


def pdf_page_count(src: Path) -> int:
    doc = _open_pdf(src)
    try:
        return len(doc)
    finally:
        doc.close()


def pdf_to_images(src: Path, fmt: str, dpi: int, job=None) -> Path:
    doc = _open_pdf(src)
    try:
        n = len(doc)
        folder = unique_path(src.parent / f"{stem_of(src.name)}_图片")
        folder.mkdir()
        width = len(str(n))
        for i in range(n):
            if job and job.cancelled:
                raise Cancelled()
            page = doc[i]
            img = page.render(scale=dpi / 72).to_pil().convert("RGB")
            target = folder / f"{stem_of(src.name)}_{str(i + 1).zfill(width)}.{fmt}"
            if fmt == "png":
                img.save(target, "PNG", optimize=False)
            else:
                img.save(target, "JPEG", quality=90)
            page.close()
            if job:
                job.sub((i + 1) / n)
        return folder
    finally:
        doc.close()


def pdf_to_text(src: Path, out: Path) -> None:
    doc = _open_pdf(src)
    try:
        with open(out, "w", encoding="utf-8") as fo:
            for i in range(len(doc)):
                page = doc[i]
                tp = page.get_textpage()
                if i:
                    fo.write("\n\n")
                fo.write(tp.get_text_range().replace("\r\n", "\n"))
                tp.close()
                page.close()
    finally:
        doc.close()


def merge_pdfs(srcs: list[Path], out: Path, job=None) -> None:
    import pypdfium2 as pdfium

    res = pdfium.PdfDocument.new()
    try:
        for i, s in enumerate(srcs):
            if job and job.cancelled:
                raise Cancelled()
            d = _open_pdf(s)
            res.import_pages(d)
            d.close()
            if job:
                job.sub((i + 1) / len(srcs))
        res.save(str(out))
    finally:
        res.close()


# ---------------------------------------------------------------- 转换：音视频

QUALITY = {  # 高 / 中 / 小
    "high": {"crf": 20, "vp9": 28, "ab": "256k"},
    "medium": {"crf": 24, "vp9": 33, "ab": "192k"},
    "small": {"crf": 29, "vp9": 38, "ab": "128k"},
}


def _streams(src: Path) -> dict:
    out = {"readable": False, "video": False, "audio": False, "duration": 0.0}
    try:
        p = subprocess.run([TOOLS["ffmpeg"], "-hide_banner", "-i", str(src)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30, creationflags=NO_WINDOW)
    except Exception:
        return out
    err = p.stderr
    out["readable"] = "Invalid data found" not in err and "Stream #" in err
    v = re.search(r"Stream #.*Video: (\w+)(?!.*attached pic)", err)
    a = re.search(r"Stream #.*Audio: (\w+)", err)
    out["video"], out["vcodec"] = bool(v), (v[1] if v else None)
    out["audio"], out["acodec"] = bool(a), (a[1] if a else None)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if m:
        out["duration"] = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    return out


def _duration(src: Path) -> float:
    try:
        p = subprocess.run([TOOLS["ffmpeg"], "-hide_banner", "-i", str(src)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30, creationflags=NO_WINDOW)
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", p.stderr)
        if m:
            return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    except Exception:
        pass
    return 0.0


def E_ext(p: Path) -> str:
    return ext_of(p.name).replace("jpeg", "jpg")


def convert_media(src: Path, out: Path, fmt: str, quality: str, height: int | None, job=None) -> None:
    if not TOOLS["ffmpeg"]:
        raise UserError("缺少 ffmpeg")
    info = _streams(src)
    if not info["readable"]:
        raise UserError("无法读取这个文件，可能已损坏或不是有效的音视频")
    if fmt in ("mp3", "wav", "flac", "m4a", "ogg", "opus") and not info["audio"]:
        raise UserError("这个视频没有声音，无法转成音频")
    if fmt in ("mp4", "webm", "mkv", "mov", "gif") and not info["video"]:
        raise UserError("这个文件没有画面，无法转成视频")
    q = QUALITY.get(quality, QUALITY["medium"])
    # 宽高必须是偶数，否则 H.264 编码器会报错
    vf = [f"scale=-2:'trunc(min({height},ih)/2)*2'" if height else "scale=trunc(iw/2)*2:trunc(ih/2)*2"]
    a: list[str]
    remux = (fmt in ("mp4", "mov", "mkv") and E_ext(src) != fmt and not height and quality == "medium"
             and info.get("vcodec") in ("h264", "hevc") and E_ext(src) not in ("gif",))
    if remux:  # 只换封装、不重新压缩：几乎瞬间完成，画质无损
        a = ["-map", "0:v:0", "-map", "0:a?", "-c:v", "copy"]
        if info.get("vcodec") == "hevc" and fmt != "mkv":
            a += ["-tag:v", "hvc1"]
        a += ["-c:a", "copy"] if fmt == "mkv" or info.get("acodec") in ("aac", "mp3") else ["-c:a", "aac", "-b:a", "192k"]
        if fmt != "mkv":
            a += ["-movflags", "+faststart"]
        vf = []
    elif fmt in ("mp4", "mov", "mkv"):
        a = ["-c:v", "libx264", "-preset", "medium", "-crf", str(q["crf"]), "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "160k"]
        if src.suffix.lower() == ".gif":
            a += ["-an"]
        if fmt != "mkv":
            a += ["-movflags", "+faststart"]
    elif fmt == "webm":
        a = ["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", str(q["vp9"]), "-deadline", "realtime", "-cpu-used", "8",
             "-row-mt", "1", "-tile-columns", "2", "-c:a", "libopus", "-b:a", "128k"]
    elif fmt == "gif":
        h = height or 480
        vf = [f"fps=12,scale=-2:'min({h},ih)':flags=lanczos,split[s0][s1];[s0]palettegen=stats_mode=diff[p];"
              f"[s1][p]paletteuse=dither=bayer:bayer_scale=4"]
        a = ["-loop", "0"]
    elif fmt == "mp3":
        a = ["-vn", "-c:a", "libmp3lame", "-b:a", q["ab"]]
    elif fmt == "wav":
        a = ["-vn", "-c:a", "pcm_s16le"]
    elif fmt == "flac":
        a = ["-vn", "-c:a", "flac"]
    elif fmt == "m4a":
        a = ["-vn", "-c:a", "aac", "-b:a", q["ab"]]
    elif fmt == "ogg":
        a = ["-vn", "-c:a", "libvorbis", "-q:a", {"high": "7", "medium": "5", "small": "3"}.get(quality, "5")]
    elif fmt == "opus":
        a = ["-vn", "-c:a", "libopus", "-b:a", {"high": "160k", "medium": "128k", "small": "96k"}.get(quality, "128k")]
    else:
        raise UserError(f"不支持转成 {fmt}")
    if fmt in ("mp3", "wav", "flac", "m4a", "ogg", "opus"):
        vf = []
    total = info["duration"]
    cmd = [TOOLS["ffmpeg"], "-hide_banner", "-nostdin", "-y", "-i", str(src)]
    if vf:
        cmd += ["-vf", ",".join(vf)]
    cmd += a + ["-progress", "pipe:1", "-nostats", str(out)]

    def on_line(line: str):
        if job and total and line.startswith("out_time_us="):
            try:
                job.sub(min(int(line.split("=")[1]) / 1e6 / total, 0.99))
            except ValueError:
                pass

    try:
        run(cmd, job, on_line)
    except BaseException:
        out.unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------- 转换：Office 文档

_LO_LOCK = threading.Lock()
LO_FILTER = {"txt": "txt:Text (encoded):UTF8", "csv": "csv:Text - txt - csv (StarCalc):44,34,76",
             "html": "html", "docx": "docx", "pdf": "pdf"}


def convert_office(src: Path, out: Path, fmt: str, job=None) -> None:
    if not TOOLS["soffice"]:
        raise UserError("文档转换需要免费的 LibreOffice，装好后重启 MiniBox 即可")
    profile = Path(tempfile.gettempdir()) / "minibox-lo-profile"
    with tempfile.TemporaryDirectory(prefix="minibox-") as td, _LO_LOCK:
        cmd = [TOOLS["soffice"], f"-env:UserInstallation={profile.as_uri()}", "--headless", "--norestore"]
        if ext_of(src.name) == "pdf":
            if PDF:
                _open_pdf(src).close()  # 有密码或已损坏时给出明确提示
            cmd.append("--infilter=writer_pdf_import")
        cmd += ["--convert-to", LO_FILTER.get(fmt, fmt), "--outdir", td, str(src)]
        run(cmd, job, timeout=600)
        produced = list(Path(td).glob("*." + fmt))
        if not produced:
            raise UserError("文档转换失败，文件可能已损坏或有密码保护")
        shutil.move(str(produced[0]), str(out))


# ================================================================ 缩小体积 / 剪辑 / PDF 页面

def human(n: float) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.0f} {u}" if u in ("B", "KB") or n >= 100 else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} GB"


class Note(str):
    """附带一句说明的输出：(路径, 说明)。"""


def _shrunk_name(src: Path, ext: str) -> Path:
    return unique_path(src.with_name(f"{stem_of(src.name)}_压缩.{ext}"))


def shrink(src: Path, target: int, job=None):
    """把文件压到 target 字节以内。返回输出路径，或 (路径, 说明)。"""
    if target < 1024:
        raise UserError("目标大小太小了")
    size = src.stat().st_size
    if size <= target:
        raise UserError(f"本来就只有 {human(size)}，已经比目标小，不用压缩")
    k = kind_of(src.name)
    if k == "image":
        return _shrink_image(src, target)
    if k == "pdf":
        return _shrink_pdf(src, target, job)
    if k in ("video", "audio"):
        return _shrink_media(src, target, job)
    raise UserError("这种文件不能缩小体积")


def _shrink_image(src: Path, target: int):
    import io
    ensure_heif()
    try:
        raw = Image.open(src)
    except (OSError, SyntaxError, ValueError):
        raise UserError("无法读取这张图片，文件可能已损坏") from None
    if getattr(raw, "is_animated", False):
        raise UserError("动图暂时不能缩小体积，可以先转成 MP4")
    img = ImageOps.exif_transpose(raw) or raw
    alpha = "A" in img.getbands() or (img.mode == "P" and "transparency" in img.info)
    if alpha and img.convert("RGBA").getextrema()[3][0] == 255:
        alpha = False  # 有透明通道但其实全不透明
    if alpha:  # 透明图片：保留 PNG，减色 + 缩小
        im = img.convert("RGBA")

        def enc(x):
            b = io.BytesIO()
            x.quantize(256, method=Image.FASTOCTREE).save(b, "PNG", optimize=True)
            return b.getvalue()
        data = enc(im)
        for _ in range(20):
            if len(data) <= target:
                break
            f = max(0.5, min(0.95, (target / len(data)) ** 0.5 * 0.95))
            im = im.resize((max(1, int(im.width * f)), max(1, int(im.height * f))), Image.LANCZOS)
            data = enc(im)
        out = _shrunk_name(src, "png")
    else:
        im = _flatten(img)

        def enc(x, q):
            b = io.BytesIO()
            x.save(b, "JPEG", quality=q, optimize=True, progressive=True, subsampling=0 if q >= 90 else 2)
            return b.getvalue()
        data = None
        for _ in range(20):
            low = enc(im, 30)
            if len(low) > target:  # 最低画质也放不下：先缩小尺寸
                f = max(0.5, min(0.95, (target / len(low)) ** 0.5 * 0.97))
                im = im.resize((max(1, int(im.width * f)), max(1, int(im.height * f))), Image.LANCZOS)
                continue
            lo, hi, data = 30, 92, low
            while lo <= hi:  # 二分找能放下的最高画质
                mid = (lo + hi) // 2
                d = enc(im, mid)
                if len(d) <= target:
                    data, lo = d, mid + 1
                else:
                    hi = mid - 1
            break
        if data is None or len(data) > target:
            raise UserError("压不到这么小，换个大一点的目标试试")
        out = _shrunk_name(src, "jpg")
    out.write_bytes(data)
    return out


_PDF_LEVELS = [(200, 80), (150, 72), (120, 62), (100, 55), (80, 48), (60, 40), (45, 35)]


def _pdf_recompress(src: Path, out: Path, dpi: int, q: int, job=None) -> int:
    import io
    import pypdfium2 as pdfium
    import pypdfium2.raw as R
    doc = _open_pdf(src)
    try:
        for page in doc:
            if job and job.cancelled:
                raise Cancelled()
            changed = False
            for obj in list(page.get_objects(filter=[R.FPDF_PAGEOBJ_IMAGE], max_depth=1)):
                try:
                    raw_len = R.FPDFImageObj_GetImageDataRaw(obj.raw, None, 0)
                    pil = obj.get_bitmap(render=False).to_pil()
                except Exception:  # noqa: BLE001
                    continue
                if pil.mode in ("1", "RGBA", "LA", "P", "PA") or min(pil.size) < 48 or raw_len < 20000:
                    continue
                l, b, r, t = (obj.get_bounds if hasattr(obj, "get_bounds") else obj.get_pos)()
                w_in, h_in = max(r - l, 1) / 72, max(t - b, 1) / 72
                w, h = pil.size
                f = min(1.0, dpi * w_in / w, dpi * h_in / h)
                if f < 0.98:
                    pil = pil.resize((max(1, int(w * f)), max(1, int(h * f))), Image.LANCZOS)
                if pil.mode not in ("RGB", "L"):
                    pil = pil.convert("RGB")
                buf = io.BytesIO()
                pil.save(buf, "JPEG", quality=q, optimize=True)
                if buf.tell() >= raw_len * 0.9:
                    continue
                buf.seek(0)
                obj.load_jpeg(buf, pages=[page], inline=False, autoclose=False)
                changed = True
            if changed:
                page.gen_content()
        doc.save(str(out))
    finally:
        doc.close()
    return out.stat().st_size


def _shrink_pdf(src: Path, target: int, job=None):
    if not PDF:
        raise UserError("缺少 PDF 组件")
    out = _shrunk_name(src, "pdf")
    tmp = out.with_name(out.name + ".part")
    best = None
    try:
        for i, (dpi, q) in enumerate(_PDF_LEVELS):
            n = _pdf_recompress(src, tmp, dpi, q, job)
            if job:
                job.sub((i + 1) / len(_PDF_LEVELS))
            if best is None or n < best:
                best = n
                shutil.copyfile(tmp, out)
            if n <= target:
                break
    except BaseException:
        out.unlink(missing_ok=True)
        raise
    finally:
        tmp.unlink(missing_ok=True)
    orig = src.stat().st_size
    if best is None or best >= orig * 0.95:
        out.unlink(missing_ok=True)
        raise UserError("这个 PDF 主要是文字，或者已经压缩过，没法再变小了")
    if best > target:
        return out, Note(f"最小只能压到 {human(best)}（原来 {human(orig)}）")
    return out


def _shrink_media(src: Path, target: int, job=None):
    if not TOOLS["ffmpeg"]:
        raise UserError("缺少 ffmpeg")
    info = _streams(src)
    if not info["readable"]:
        raise UserError("无法读取这个文件，可能已损坏或不是有效的音视频")
    dur = info["duration"]
    if dur <= 0:
        raise UserError("读不出这个文件的时长，无法按大小压缩")
    budget = target * 8 / dur * 0.96 / 1000  # 可用的总码率 kbps（留一点给封装）
    total_ref = [dur]

    def prog(base, span):
        def on_line(line: str):
            if job and line.startswith("out_time_us="):
                try:
                    job.sub(min(base + span * int(line.split("=")[1]) / 1e6 / total_ref[0], 0.99))
                except ValueError:
                    pass
        return on_line

    if not info["video"]:  # 音频：算出码率直接编码
        if not info["audio"]:
            raise UserError("这个文件里没有声音也没有画面")
        if budget < 24:
            raise UserError(f"目标太小：这段音频至少要 {human(24000 * dur / 8 / 0.96)}")
        kb = int(min(budget, 320))
        ext = "m4a" if E_ext(src) in ("m4a", "aac") else "mp3"
        out = _shrunk_name(src, ext)
        codec = ["-c:a", "aac"] if ext == "m4a" else ["-c:a", "libmp3lame"]
        cmd = [TOOLS["ffmpeg"], "-hide_banner", "-nostdin", "-y", "-i", str(src), "-vn", *codec, "-b:a", f"{kb}k",
               "-progress", "pipe:1", "-nostats", str(out)]
        try:
            run(cmd, job, prog(0, 1))
        except BaseException:
            out.unlink(missing_ok=True)
            raise
        return out

    has_a = info["audio"]
    ab = (128 if budget > 1500 else 96 if budget > 600 else 64) if has_a else 0
    vb = budget - ab
    if vb < 70:
        need = (70 + (64 if has_a else 0)) * 1000 * dur / 8 / 0.96
        raise UserError(f"目标太小：这个视频至少要 {human(need)}")
    out = _shrunk_name(src, "mp4")
    td = tempfile.mkdtemp(prefix="minibox-2pass-")
    log = os.path.join(td, "pass")

    def encode(vb: float):
        h = 1080 if vb >= 2500 else 720 if vb >= 1200 else 540 if vb >= 650 else 480 if vb >= 350 else 360
        vf = ["-vf", f"scale=-2:'trunc(min({h},ih)/2)*2'"]
        base = [TOOLS["ffmpeg"], "-hide_banner", "-nostdin", "-y", "-i", str(src), "-map", "0:v:0", *vf,
                "-c:v", "libx264", "-preset", "medium", "-b:v", f"{int(vb)}k", "-pix_fmt", "yuv420p",
                "-passlogfile", log]
        run(base + ["-pass", "1", "-an", "-f", "mp4", "-progress", "pipe:1", "-nostats", os.devnull], job, prog(0, 0.4))
        aud = ["-map", "0:a:0?", "-c:a", "aac", "-b:a", f"{ab}k"] if has_a else ["-an"]
        run(base + ["-pass", "2", *aud, "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(out)],
            job, prog(0.4, 0.6))
        return out.stat().st_size

    try:
        n = encode(vb)
        if n > target:  # 超了一点：按比例再压一次
            n = encode(vb * target / n * 0.95)
        if n > target:
            return out, Note(f"压到了 {human(n)}，比目标稍大")
        return out
    except BaseException:
        out.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(td, ignore_errors=True)


def trim(src: Path, start: float, end: float, job=None):
    """剪出 start~end 这一段（秒）。短片段精确剪切，长片段无损快剪。"""
    if not TOOLS["ffmpeg"]:
        raise UserError("缺少 ffmpeg")
    info = _streams(src)
    if not info["readable"]:
        raise UserError("无法读取这个文件，可能已损坏或不是有效的音视频")
    dur = info["duration"] or end
    start, end = max(0.0, float(start)), min(float(end), dur) if dur else float(end)
    if end - start < 0.2:
        raise UserError("剪出来的片段太短了")
    length = end - start
    ext = E_ext(src)
    video = info["video"]
    exact = video and length <= 180
    if video and exact and ext not in ("mp4", "mov", "mkv", "webm"):
        ext = "mp4"
    out = unique_path(src.with_name(f"{stem_of(src.name)}_剪辑.{ext}"))
    cmd = [TOOLS["ffmpeg"], "-hide_banner", "-nostdin", "-y"]
    lossless_audio = not video and ext in ("flac", "wav", "aiff", "aif")
    if exact:  # 重新编码：先快速跳到附近，再逐帧精确定位
        pre = min(start, 15.0)
        if start - pre > 0:
            cmd += ["-ss", f"{start - pre:.3f}"]
        cmd += ["-i", str(src), "-ss", f"{pre:.3f}", "-t", f"{length:.3f}", "-map", "0:v:0", "-map", "0:a?"]
        if ext == "webm":
            cmd += ["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "30", "-deadline", "realtime", "-cpu-used", "8",
                    "-row-mt", "1", "-c:a", "libopus", "-b:a", "160k"]
        else:
            cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k"]
            if ext != "mkv":
                cmd += ["-movflags", "+faststart"]
    elif lossless_audio:  # 无损音频重新编码也是无损的，而且时长精确
        codec = {"flac": "flac", "wav": "pcm_s16le", "aiff": "pcm_s16be", "aif": "pcm_s16be"}[ext]
        cmd += ["-ss", f"{start:.3f}", "-i", str(src), "-t", f"{length:.3f}", "-map", "0:a:0", "-c:a", codec]
    else:  # 不重新编码：几秒完成，画质无损（视频开头会对齐到最近的关键帧）
        cmd += ["-ss", f"{start:.3f}", "-i", str(src), "-t", f"{length:.3f}", "-map", "0", "-c", "copy",
                "-avoid_negative_ts", "make_zero"]
        if ext in ("mp4", "mov", "m4a"):
            cmd += ["-movflags", "+faststart"]
    cmd += ["-progress", "pipe:1", "-nostats", str(out)]

    def on_line(line: str):
        if job and line.startswith("out_time_us="):
            try:
                job.sub(min(int(line.split("=")[1]) / 1e6 / length, 0.99))
            except ValueError:
                pass
    try:
        run(cmd, job, on_line)
    except BaseException:
        out.unlink(missing_ok=True)
        raise
    if not out.exists() or out.stat().st_size == 0 or (video and not _streams(out)["video"]):
        out.unlink(missing_ok=True)
        raise UserError("剪辑失败，换个时间段试试")
    if video and not exact:
        return out, Note("片段较长，用了无损快剪，开头可能会早一两秒")
    return out


def pdf_thumb(src: Path, index: int, width: int = 240) -> bytes:
    import io
    doc = _open_pdf(src)
    try:
        if not 0 <= index < len(doc):
            raise UserError("没有这一页")
        page = doc[index]
        w = page.get_width()
        img = page.render(scale=max(0.05, width / max(w, 1))).to_pil().convert("RGB")
        page.close()
        b = io.BytesIO()
        img.save(b, "JPEG", quality=78)
        return b.getvalue()
    finally:
        doc.close()


def pdf_pages(src: Path, pages: list, mode: str = "save", job=None) -> Path:
    """pages: [[原页码(0 起), 旋转角度], ...]。mode=save 存成一个新 PDF；split 每页一个 PDF。"""
    import pypdfium2 as pdfium
    doc = _open_pdf(src)
    try:
        n = len(doc)
        items = [(int(i), int(r) % 360) for i, r in pages if 0 <= int(i) < n]
        if not items:
            raise UserError("没有选中任何页")

        def build(sel, dest: Path):
            new = pdfium.PdfDocument.new()
            try:
                new.import_pages(doc, [i for i, _ in sel])
                for k, (_, rot) in enumerate(sel):
                    if rot:
                        p = new[k]
                        p.set_rotation((p.get_rotation() + rot) % 360)
                        p.close()
                new.save(str(dest))
            finally:
                new.close()

        stem = stem_of(src.name)
        if mode == "split":
            folder = unique_path(src.parent / f"{stem}_拆分")
            folder.mkdir()
            width = len(str(n))
            for k, it in enumerate(items):
                if job and job.cancelled:
                    raise Cancelled()
                build([it], folder / f"{stem}_第{str(it[0] + 1).zfill(width)}页.pdf")
                if job:
                    job.sub((k + 1) / len(items))
            return folder
        out = unique_path(src.with_name(f"{stem}_整理.pdf"))
        build(items, out)
        return out
    finally:
        doc.close()
