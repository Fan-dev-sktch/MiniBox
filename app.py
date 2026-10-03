"""MiniBox —— 免费、极简、全本地的文件管理 + 解压 + 格式转换工具。

启动:  python app.py            (默认 http://127.0.0.1:8765 ，自动打开浏览器)
参数:  --port 8765  --host 127.0.0.1  --no-browser
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

import engine as E

VERSION = "1.7.0"
FROZEN = getattr(sys, "frozen", False)
RES_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))  # 打包后资源在临时目录
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent


def _data_dir() -> Path:
    if os.environ.get("MINIBOX_DATA"):
        d = Path(os.environ["MINIBOX_DATA"])
    elif (APP_DIR / "portable").exists():  # 便携模式：数据跟着程序走
        d = APP_DIR / "data"
    elif platform.system() == "Windows":
        d = Path(os.environ.get("APPDATA", Path.home())) / "MiniBox"
    elif platform.system() == "Darwin":
        d = Path.home() / "Library" / "Application Support" / "MiniBox"
    else:
        d = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "minibox"
    d.mkdir(parents=True, exist_ok=True)
    return d


DATA_DIR = _data_dir()
CONFIG_FILE = Path(os.environ.get("MINIBOX_CONFIG", DATA_DIR / "minibox.json"))
CACHE_DIR = Path(os.environ.get("MINIBOX_CACHE", DATA_DIR / "cache"))
IN_DOCKER = Path("/.dockerenv").exists()
DESKTOP = [False]

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = None
app.json.ensure_ascii = False

# ================================================================ 位置（根目录）

_cfg_lock = threading.Lock()


# ---- 资料库：MiniBox 自己的独立空间，按文件类型固定分类，不去扫描电脑上的文件夹
CATEGORIES = [  # (key, 名称)
    ("image", "图片"), ("video", "视频"), ("audio", "音乐"),
    ("doc", "文档"), ("archive", "压缩包"), ("other", "其他"),
]
CAT_NAME = dict(CATEGORIES)
KIND_TO_CAT = {"image": "image", "video": "video", "audio": "audio", "pdf": "doc", "doc": "doc",
               "archive": "archive", "other": "other"}


def _default_library() -> Path:
    if os.environ.get("MINIBOX_LIBRARY"):
        return Path(os.environ["MINIBOX_LIBRARY"])
    if (APP_DIR / "portable").exists():
        return APP_DIR / "资料库"
    return Path.home() / "MiniBox"


def load_cfg() -> dict:
    cfg = {"show_hidden": False, "library": str(_default_library())}
    if CONFIG_FILE.exists():
        try:
            old = json.loads(CONFIG_FILE.read_text("utf-8"))
            cfg["show_hidden"] = bool(old.get("show_hidden"))
            if old.get("library") and not os.environ.get("MINIBOX_LIBRARY"):
                cfg["library"] = old["library"]
        except Exception:
            pass
    return cfg


def save_cfg(cfg: dict) -> None:
    try:
        CONFIG_FILE.write_text(json.dumps({k: v for k, v in cfg.items() if k != "roots"},
                                          ensure_ascii=False, indent=2), "utf-8")
    except OSError:
        pass


CFG = load_cfg()
STAGE_DIR = DATA_DIR / "staging"  # 格式转换页拖进来的文件先放这里


def setup_library() -> None:
    lib = Path(CFG["library"])
    lib.mkdir(parents=True, exist_ok=True)
    for _, name in CATEGORIES:
        (lib / name).mkdir(exist_ok=True)
    STAGE_DIR.mkdir(parents=True, exist_ok=True)
    CFG["roots"] = [{"id": "lib", "name": "资料库", "path": str(lib)},
                    {"id": "stage", "name": "转换暂存", "path": str(STAGE_DIR)}]
    save_cfg(CFG)


def _clean_stage(max_age: float = 86400) -> None:
    now = time.time()
    for d in STAGE_DIR.iterdir() if STAGE_DIR.exists() else []:
        try:
            if now - d.stat().st_mtime > max_age:
                shutil.rmtree(d, ignore_errors=True) if d.is_dir() else d.unlink()
        except OSError:
            pass


setup_library()
_clean_stage()


def lib_dir() -> Path:
    return Path(CFG["library"]).resolve()


def category_dir(cat: str) -> Path:
    return lib_dir() / CAT_NAME[cat]


def category_of_path(p: Path) -> str | None:
    """p 位于资料库哪个分类里（不在资料库中返回 None）。"""
    try:
        rel = p.resolve().relative_to(lib_dir())
    except ValueError:
        return None
    if not rel.parts:
        return None
    for key, name in CATEGORIES:
        if rel.parts[0] == name:
            return key
    return None


def category_for(p: Path) -> str:
    if p.is_dir():  # 文件夹按里面最多的类型归类
        counts: dict[str, int] = {}
        for f in p.rglob("*"):
            if f.is_file():
                c = KIND_TO_CAT.get(E.kind_of(f.name), "other")
                counts[c] = counts.get(c, 0) + 1
        return max(counts, key=counts.get) if counts else "other"
    return KIND_TO_CAT.get(E.kind_of(p.name), "other")


def _copy(src, dst):
    """复制文件并保留修改时间（不用 CopyFile2，兼容性更好）。"""
    shutil.copyfile(src, dst)
    try:
        shutil.copystat(src, dst)
    except OSError:
        pass
    return dst


def route_to_library(p: Path) -> Path:
    """把生成的文件放进它所属的分类；已经在正确分类里就不动。
    子文件夹结构会保留：视频/旅行/a.mp4 转出的 a.mp3 → 音乐/旅行/a.mp3"""
    want = category_for(p)
    have = category_of_path(p)
    if have == want:
        return p
    sub = Path()
    if have:
        rel = p.resolve().relative_to(category_dir(have))
        sub = rel.parent
    dest_dir = category_dir(want) / sub
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = E.unique_path(dest_dir / p.name)
    shutil.move(str(p), str(dest))
    return dest


def roots() -> dict[str, dict]:
    return {r["id"]: r for r in CFG["roots"]}


def resolve(vpath: str, must_exist: bool = True) -> Path:
    """虚拟路径 'rootId/sub/file' -> 本地绝对路径（防目录穿越）。"""
    vpath = (vpath or "").replace("\\", "/").strip("/")
    rid, _, rel = vpath.partition("/")
    r = roots().get(rid)
    if not r:
        abort(400, "位置不存在")
    base = Path(r["path"]).resolve()
    target = (base / rel).resolve() if rel else base
    try:
        target.relative_to(base)
    except ValueError:
        abort(403, "不允许访问该路径")
    if must_exist and not target.exists():
        abort(404, "文件不存在")
    return target


def to_vpath(p: Path, prefer: str | None = None) -> str:
    p = p.resolve()
    best = None
    rs = CFG["roots"]
    if prefer and prefer in roots():
        rs = [roots()[prefer]] + [r for r in rs if r["id"] != prefer]
    for r in rs:
        base = Path(r["path"]).resolve()
        try:
            rel = p.relative_to(base)
        except ValueError:
            continue
        best = (r["id"], str(base), rel.as_posix())
        break
    if not best:
        return ""
    return best[0] + ("" if best[2] == "." else "/" + best[2])


def entry(p: Path, rid: str | None = None) -> dict:
    st = p.stat()
    is_dir = p.is_dir()
    return {
        "name": p.name, "path": to_vpath(p, rid), "dir": is_dir,
        "size": 0 if is_dir else st.st_size, "mtime": st.st_mtime,
        "kind": E.kind_of(p.name, is_dir), "ext": "" if is_dir else E.ext_of(p.name),
    }


def visible(name: str) -> bool:
    if name.startswith(".minibox-"):
        return False
    if name in ("desktop.ini", "Thumbs.db", ".DS_Store"):
        return False
    return CFG.get("show_hidden") or not name.startswith(".")


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(409)
def _err(e):
    return jsonify(error=getattr(e, "description", str(e))), e.code


@app.errorhandler(OSError)
def _oserr(e):
    return jsonify(error=f"系统错误：{e.strerror or e}"), 500


# ================================================================ 任务队列

class Job:
    def __init__(self, title: str, total: int):
        self.id = uuid.uuid4().hex[:10]
        self.title = title
        self.total = max(total, 1)
        self.done = 0
        self.frac = 0.0
        self.status = "queued"  # queued running done error cancelled
        self.errors: list[str] = []
        self.outputs: list[str] = []
        self.cancelled = False
        self.proc = None
        self.created = time.time()
        self.current = ""
        self.items: list[dict] = []  # 每个输入文件的结果
        self.notes: list[str] = []
        self.route = False           # 输出是否自动归入资料库分类
        self.rid = None

    def out(self, p: Path) -> str:
        if self.route and p.exists():
            try:
                p = route_to_library(p)
            except OSError:
                pass
        return to_vpath(p, "lib" if self.route else self.rid)

    def sub(self, f: float):
        self.frac = max(0.0, min(f, 1.0))

    def step(self):
        self.done += 1
        self.frac = 0.0

    def to_dict(self):
        prog = 1.0 if self.status == "done" else (self.done + self.frac) / self.total
        return {"id": self.id, "title": self.title, "status": self.status, "progress": round(prog, 4),
                "errors": self.errors, "notes": self.notes, "outputs": self.outputs, "current": self.current, "created": self.created,
                "items": self.items, "done": self.done, "total": self.total}


JOBS: dict[str, Job] = {}
RID = threading.local()  # 当前请求的位置 id，让输出路径留在同一个位置下
POOL = ThreadPoolExecutor(max_workers=max(2, min(4, (os.cpu_count() or 2) // 2)))


def submit(title: str, total: int, fn) -> Job:
    job = Job(title, total)
    job.rid = getattr(RID, "value", None)
    job.route = getattr(RID, "route", False)
    JOBS[job.id] = job

    def wrapper():
        if job.cancelled:
            job.status = "cancelled"
            return
        job.status = "running"
        try:
            fn(job)
            job.status = "cancelled" if job.cancelled else ("error" if job.errors and not job.outputs else "done")
        except E.Cancelled:
            job.status = "cancelled"
        except E.UserError as e:
            job.errors.append(str(e))
            job.status = "error"
        except Exception as e:  # noqa: BLE001
            job.errors.append(f"{type(e).__name__}: {e}")
            job.status = "error"

    POOL.submit(wrapper)
    return job


def each(job: Job, paths: list[Path], fn):
    """逐个处理，单个失败不影响其他。"""
    for p in paths:
        if job.cancelled:
            raise E.Cancelled()
        job.current = p.name
        item = {"name": p.name, "src": to_vpath(p), "outputs": [], "error": None}
        job.items.append(item)
        try:
            res = fn(p)
            if isinstance(res, tuple):  # (输出, 说明)
                res, note = res
                item["note"] = str(note)
                job.notes.append(f"{p.name}：{note}")
            for r in (res if isinstance(res, list) else [res]):
                if r:
                    vp = job.out(r)
                    job.outputs.append(vp)
                    item["outputs"].append(vp)
        except E.Cancelled:
            raise
        except E.UserError as e:
            job.errors.append(f"{p.name}：{e}")
            item["error"] = str(e)
        except Exception as e:  # noqa: BLE001
            job.errors.append(f"{p.name}：{type(e).__name__}: {e}")
            item["error"] = f"{type(e).__name__}: {e}"
        job.step()
    job.current = ""


# ================================================================ 页面 & 基础信息

@app.get("/")
def index():
    return send_from_directory(RES_DIR / "static", "index.html")


@app.get("/static/<path:name>")
def static_file(name):
    return send_from_directory(RES_DIR / "static", name)


@app.get("/api/info")
def info():
    return jsonify({
        "app": "minibox", "version": VERSION, "desktop": DESKTOP[0],
        "library": str(lib_dir()),
        "categories": [{"key": k, "name": n, "path": f"lib/{n}"} for k, n in CATEGORIES],
        "tools": {"ffmpeg": bool(E.TOOLS["ffmpeg"]), "libreoffice": bool(E.TOOLS["soffice"]),
                  "7z": bool(E.TOOLS["7z"]), "bsdtar": bool(E.TOOLS["bsdtar"]), "heic": E.HEIF,
                  "avif": E.AVIF, "pdf": E.PDF},
        "local": not IN_DOCKER, "show_hidden": bool(CFG.get("show_hidden")),
        "os": platform.system(),
    })


@app.get("/api/library/stats")
def library_stats():
    """每个分类里的文件数和总大小。"""
    out = {}
    for key, name in CATEGORIES:
        n, size = 0, 0
        for root, dirs, files in os.walk(lib_dir() / name):
            dirs[:] = [d for d in dirs if visible(d)]
            for f in files:
                if visible(f):
                    n += 1
                    try:
                        size += (Path(root) / f).stat().st_size
                    except OSError:
                        pass
        out[key] = {"count": n, "size": size}
    return jsonify(out)


@app.post("/api/settings")
def settings():
    data = request.get_json(force=True)
    with _cfg_lock:
        if "show_hidden" in data:
            CFG["show_hidden"] = bool(data["show_hidden"])
        if data.get("library"):
            path = Path(os.path.expanduser(str(data["library"]).strip().strip('"')))
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError:
                abort(400, "无法使用这个位置，请换一个文件夹")
            CFG["library"] = str(path.resolve())
            setup_library()
        save_cfg(CFG)
    return jsonify(ok=True, library=str(lib_dir()))


# ================================================================ 浏览

@app.get("/api/list")
def list_dir():
    vp = request.args.get("path", "")
    rid = vp.strip("/").split("/")[0]
    p = resolve(vp)
    if not p.is_dir():
        abort(400, "不是文件夹")
    items = []
    try:
        it = list(p.iterdir())
    except PermissionError:
        abort(403, "没有权限读取这个文件夹")
    for c in it:
        if not visible(c.name):
            continue
        try:
            items.append(entry(c, rid))
        except OSError:
            continue
    return jsonify(path=to_vpath(p, rid), items=items)


@app.get("/api/recent")
def recent():
    """「全部」：所有分类里的文件，最新的在前。"""
    files = []
    for root, dirs, names in os.walk(lib_dir()):
        dirs[:] = [d for d in dirs if visible(d)]
        for n in names:
            if visible(n):
                p = Path(root) / n
                try:
                    files.append((p.stat().st_mtime, p))
                except OSError:
                    pass
    files.sort(key=lambda x: -x[0])
    return jsonify(path="lib", items=[entry(p, "lib") for _, p in files[:500]], total=len(files))


@app.get("/api/search")
def search():
    rid = request.args.get("path", "").strip("/").split("/")[0]
    base = resolve(request.args.get("path", ""))
    q = request.args.get("q", "").strip().lower()
    if not q:
        return jsonify(items=[])
    out = []
    deadline = time.time() + 4
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if visible(d)]
        for n in dirs + files:
            if q in n.lower() and visible(n):
                try:
                    out.append(entry(Path(root) / n, rid))
                except OSError:
                    pass
                if len(out) >= 300:
                    return jsonify(items=out, truncated=True)
        if time.time() > deadline:
            return jsonify(items=out, truncated=True)
    return jsonify(items=out)


@app.get("/api/formats")
def formats():
    names = request.args.getlist("name")
    return jsonify({n: E.targets_for(n) for n in names})


# ================================================================ 文件读取

@app.get("/api/file")
def get_file():
    p = resolve(request.args.get("path", ""))
    dl = request.args.get("dl") == "1"
    if p.is_dir():
        if not dl:
            abort(400, "文件夹不能预览")
        tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        tmp.close()
        with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as zf:
            for a, n in E._walk([p]):
                zf.write(a, n)
        resp = send_file(tmp.name, as_attachment=True, download_name=p.name + ".zip")
        resp.call_on_close(lambda: os.unlink(tmp.name))
        return resp
    return send_file(p, as_attachment=dl, download_name=p.name, conditional=True)


@app.get("/api/thumb")
def thumb():
    p = resolve(request.args.get("path", ""))
    kind = E.kind_of(p.name)
    if kind not in ("image", "video"):
        abort(400)
    st = p.stat()
    key = hashlib.sha1(f"v2|{p}|{st.st_mtime}|{st.st_size}".encode()).hexdigest()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = CACHE_DIR / f"{key}.webp"
    if not cached.exists() and kind == "video":  # 视频取第 1 秒的画面
        if not E.TOOLS["ffmpeg"]:
            abort(415)
        frame = cached.with_suffix(".jpg")
        for ss in ("1", "0"):
            subprocess.run([E.TOOLS["ffmpeg"], "-hide_banner", "-loglevel", "error", "-y", "-ss", ss, "-i", str(p),
                            "-frames:v", "1", "-vf", "scale=480:-2", str(frame)], capture_output=True, timeout=30,
                           creationflags=E.NO_WINDOW)
            if frame.exists() and frame.stat().st_size:
                break
        if not frame.exists():
            abort(415)
        p = frame
    if not cached.exists():
        try:
            from PIL import Image, ImageOps

            E.ensure_heif()
            with Image.open(p) as im:
                im = ImageOps.exif_transpose(im) or im
                im.thumbnail((480, 480))
                im.convert("RGBA").save(cached, "WEBP", quality=75)
            if kind == "video":
                p.unlink(missing_ok=True)
        except Exception:
            abort(415)
    resp = send_file(cached, mimetype="image/webp")
    resp.headers["Cache-Control"] = "max-age=86400"
    return resp


@app.get("/api/text")
def text_preview():
    p = resolve(request.args.get("path", ""))
    raw = p.open("rb").read(200_000)
    for enc in ("utf-8", "gbk"):
        try:
            return jsonify(text=raw.decode(enc), truncated=p.stat().st_size > len(raw))
        except UnicodeDecodeError:
            continue
    return jsonify(text=raw.decode("utf-8", "replace"), truncated=True)


@app.get("/api/archive")
def archive_list():
    """压缩包内容预览（zip / tar / 7z）。"""
    p = resolve(request.args.get("path", ""))
    e = E.ext_of(p.name)
    names: list[tuple[str, int]] = []
    try:
        if zipfile.is_zipfile(p):
            with zipfile.ZipFile(p) as zf:
                names = [(E._fix_zip_name(i), i.file_size) for i in zf.infolist()]
        elif e in ("tar", "tgz", "tbz2", "txz", "tar.gz", "tar.bz2", "tar.xz"):
            import tarfile

            with tarfile.open(p) as tf:
                names = [(m.name + ("/" if m.isdir() else ""), m.size) for m in tf.getmembers()]
        elif e == "7z" and E.PY7Z:
            import py7zr

            with py7zr.SevenZipFile(p) as z:
                names = [(i.filename + ("/" if i.is_directory else ""), i.uncompressed or 0) for i in z.list()]
        else:
            return jsonify(items=None)
    except Exception as ex:  # noqa: BLE001
        msg = str(ex).lower()
        if "password" in msg or "encrypt" in msg:
            return jsonify(items=None, encrypted=True)
        return jsonify(items=None, error=str(ex))
    return jsonify(items=[{"name": n, "size": s} for n, s in names[:2000]], total=len(names))


# ================================================================ 文件管理

@app.post("/api/upload")
def upload():
    dest = resolve(request.form.get("path", ""))
    if not dest.is_dir():
        abort(400, "目标不是文件夹")
    files = request.files.getlist("files")
    rels = request.form.getlist("rel")
    saved = []
    placed: dict[str, int] = {}
    auto_sort = request.form.get("path", "").split("/")[0] == "lib"
    for i, f in enumerate(files):
        rel = rels[i] if i < len(rels) and rels[i] else f.filename
        base = dest
        if auto_sort:  # 资料库里：按类型放进对应分类（当前分类就是该类型则放当前文件夹）
            cat = KIND_TO_CAT.get(E.kind_of(f.filename or rel), "other")
            if category_of_path(dest) != cat:
                base = category_dir(cat)
            placed[CAT_NAME[cat]] = placed.get(CAT_NAME[cat], 0) + 1
        target = E.safe_join(base, rel or "")
        if target is None:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if "/" not in rel.replace("\\", "/").strip("/"):
            target = E.unique_path(target)
        f.save(target)
        saved.append(to_vpath(target, request.form.get("path", "").split("/")[0]))
    return jsonify(saved=saved, placed=placed)


@app.post("/api/mkdir")
def mkdir():
    data = request.get_json(force=True)
    parent = resolve(data.get("path", ""))
    if parent.resolve() == lib_dir():
        abort(400, "请先进入一个分类，再新建文件夹")
    name = _clean_name(data.get("name", "新建文件夹"))
    target = E.unique_path(parent / name)
    target.mkdir()
    return jsonify(path=to_vpath(target, data.get("path", "").split("/")[0]))


def _clean_name(name: str) -> str:
    name = str(name).strip()
    if not name or name in (".", "..") or any(c in name for c in '\\/:*?"<>|'):
        abort(400, '名称不能为空，也不能包含 \\ / : * ? " < > |')
    return name


@app.post("/api/rename")
def rename():
    data = request.get_json(force=True)
    p = resolve(data.get("path", ""))
    if _is_root(p):
        abort(400, "不能重命名位置根目录")
    new = p.with_name(_clean_name(data.get("name", "")))
    if new.exists() and new.name.lower() != p.name.lower():
        abort(409, "已经有同名文件了")
    p.rename(new)
    return jsonify(path=to_vpath(new, data.get("path", "").split("/")[0]))


def _is_root(p: Path) -> bool:
    """位置根目录和资料库的分类文件夹都不能改名、移动或删除。"""
    p = p.resolve()
    if any(Path(r["path"]).resolve() == p for r in CFG["roots"]):
        return True
    return p.parent == lib_dir() and p.name in CAT_NAME.values()


@app.post("/api/move")
def move():
    data = request.get_json(force=True)
    dest = resolve(data.get("dest", ""))
    copy = bool(data.get("copy"))
    if not dest.is_dir():
        abort(400, "目标不是文件夹")
    if dest.resolve() == lib_dir():
        abort(400, "请移动到某个分类里")
    moved = []
    for vp in data.get("paths", []):
        src = resolve(vp)
        if _is_root(src):
            continue
        if src.is_dir() and (dest == src or src in dest.parents):
            abort(400, "不能把文件夹移动到它自己里面")
        if src.parent == dest and not copy:
            continue
        target = E.unique_path(dest / src.name)
        if copy:
            shutil.copytree(src, target, copy_function=_copy) if src.is_dir() else _copy(src, target)
        else:
            shutil.move(str(src), str(target))
        moved.append(to_vpath(target, data.get("dest", "").split("/")[0]))
    return jsonify(paths=moved)


@app.post("/api/delete")
def delete():
    data = request.get_json(force=True)
    permanent = bool(data.get("permanent"))
    failed = []
    for vp in data.get("paths", []):
        p = resolve(vp)
        if _is_root(p):
            continue
        try:
            if permanent:
                (shutil.rmtree if p.is_dir() else os.unlink)(p)
            else:
                from send2trash import send2trash

                send2trash(str(p))
        except Exception as e:  # noqa: BLE001
            failed.append(f"{p.name}：{e}")
    if failed and not permanent:
        return jsonify(ok=False, need_permanent=True, errors=failed)
    return jsonify(ok=not failed, errors=failed)


@app.post("/api/reveal")
def reveal():
    p = resolve(request.get_json(force=True).get("path", ""))
    if IN_DOCKER:
        abort(400, "Docker 模式下无法打开系统文件夹")
    target = p if p.is_dir() else p.parent
    sysname = platform.system()
    if sysname == "Windows":
        if p.is_dir():
            os.startfile(str(p))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["explorer", "/select,", str(p)])
    elif sysname == "Darwin":
        subprocess.Popen(["open", "-R", str(p)] if p.is_file() else ["open", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(target)])
    return jsonify(ok=True)


# ================================================================ 从电脑导入（只在用户主动点「导入」时才读取所选文件夹）

IMPORT_SCANS: dict[str, dict] = {}
_SKIP_DIRS = {"appdata", "$recycle.bin", "system volume information", "windows", "program files",
              "program files (x86)", "programdata", "node_modules", "__pycache__", "site-packages",
              "$windows.~bt", "$windows.~ws", "recovery", "msocache", "perflogs"}


def _local_only():
    if IN_DOCKER or (request.remote_addr or "") not in ("127.0.0.1", "::1", "localhost"):
        abort(403, "只能在本机使用这个功能")


def _known_folders() -> list[tuple[str, Path]]:
    home = Path.home()
    names = [("下载", "Downloads"), ("桌面", "Desktop"), ("文档", "Documents"),
             ("图片", "Pictures"), ("视频", "Videos"), ("音乐", "Music")]
    found: dict[str, Path] = {}
    if platform.system() == "Windows":
        try:
            import ctypes
            from ctypes import wintypes
            import uuid as _uuid
            guids = {"下载": "374DE290-123F-4565-9164-39C4925E467B", "桌面": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
                     "文档": "FDD39AD0-238F-46AF-ADB4-6C85480369C7", "图片": "33E28130-4E1E-4676-835A-98395C3BC3BB",
                     "视频": "18989B1D-99B5-455B-841C-AB7C74E4DDFC", "音乐": "4BD8D571-6D19-48D3-BE97-422220080E43"}
            for label, g in guids.items():
                buf = ctypes.c_wchar_p()
                gid = (ctypes.c_byte * 16).from_buffer_copy(_uuid.UUID(g).bytes_le)
                if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(gid), 0, None, ctypes.byref(buf)) == 0:
                    found[label] = Path(buf.value)
                    ctypes.windll.ole32.CoTaskMemFree(buf)
        except Exception:  # noqa: BLE001
            pass
    elif platform.system() == "Linux":
        try:  # xdg 用户目录（中文系统里叫「下载」「桌面」等）
            keys = {"DOWNLOAD": "下载", "DESKTOP": "桌面", "DOCUMENTS": "文档", "PICTURES": "图片", "VIDEOS": "视频", "MUSIC": "音乐"}
            for line in (home / ".config/user-dirs.dirs").read_text(encoding="utf-8").splitlines():
                k, _, v = line.partition("=")
                k = k.strip().replace("XDG_", "").replace("_DIR", "")
                if k in keys:
                    found[keys[k]] = Path(v.strip().strip('"').replace("$HOME", str(home)))
        except OSError:
            pass
    out = []
    for label, en in names:
        p = found.get(label)
        if p is None:
            p = home / ("Movies" if en == "Videos" and platform.system() == "Darwin" else en)
        if p.is_dir() and p.resolve() != home.resolve():
            out.append((label, p))
    return out


def _drives() -> list[str]:
    if platform.system() != "Windows":
        return []
    return [f"{c}:\\" for c in "CDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.exists(f"{c}:\\")]


@app.get("/api/import/places")
def import_places():
    _local_only()
    return jsonify(places=[{"name": n, "path": str(p)} for n, p in _known_folders()],
                   home=str(Path.home()), drives=_drives())


@app.get("/api/import/dirs")
def import_dirs():
    """浏览电脑上的文件夹（只列子文件夹，用来挑选导入来源）。"""
    _local_only()
    raw = (request.args.get("path") or "").strip().strip('"')
    p = Path(os.path.expanduser(raw)) if raw else Path.home()
    if not p.is_dir():
        abort(400, "找不到这个文件夹")
    try:
        lib = lib_dir()
        subs = sorted((c.name for c in p.iterdir() if c.is_dir() and not c.name.startswith(('.', '$'))
                       and c.name.lower() not in _SKIP_DIRS and c.resolve() != lib), key=str.lower)
    except PermissionError:
        abort(403, "没有权限读取这个文件夹")
    parent = str(p.parent) if p.parent != p else ""
    return jsonify(path=str(p), parent=parent, dirs=subs[:500])


@app.post("/api/import/scan")
def import_scan():
    """列出文件夹里可以导入的文件（类型、时间的筛选在界面上即时完成）。"""
    _local_only()
    data = request.get_json(force=True)
    base = Path(os.path.expanduser(str(data.get("path", "")).strip().strip('"')))
    if not base.is_dir():
        abort(400, "找不到这个文件夹")
    base = base.resolve()
    lib = lib_dir()
    skip = {lib, DATA_DIR.resolve()}
    check = any(s.is_relative_to(base) for s in skip)  # MiniBox 自己的文件夹在里面时要跳过
    try:
        base.relative_to(lib)
        abort(400, "这是 MiniBox 自己的文件夹，里面的文件已经在了")
    except ValueError:
        pass
    deep = data.get("deep", True)
    items, seen, t0, cut = [], 0, time.time(), False
    for root, dirs, names in os.walk(base):
        rp = Path(root)
        dirs[:] = [d for d in dirs if not d.startswith((".", "$")) and d.lower() not in _SKIP_DIRS
                   and not (check and (rp / d).resolve() in skip)] if deep else []
        for n in names:
            seen += 1
            if n.startswith(".") or n in ("desktop.ini", "Thumbs.db") or n.endswith((".lnk", ".url", ".tmp", ".crdownload", ".part")):
                continue
            p = rp / n
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_size == 0:
                continue
            cat = KIND_TO_CAT.get(E.kind_of(n), "other")
            items.append([p.relative_to(base).as_posix(), st.st_size, int(st.st_mtime), cat])
            if len(items) >= 20000:
                cut = True
                break
        if cut or seen > 300000 or time.time() - t0 > 12:
            cut = True
            break
    items.sort(key=lambda x: -x[2])
    token = uuid.uuid4().hex[:12]
    IMPORT_SCANS.clear()  # 只保留最近一次
    IMPORT_SCANS[token] = {"base": str(base), "items": items}
    return jsonify(token=token, path=str(base), items=items, truncated=cut)


def _already_have(p: Path, size: int, mtime: int) -> bool:
    """同名（或「名字 (2).jpg」这种重名副本）且大小、修改时间一样，就当作已经导入过。"""
    import glob as _glob
    cands = [p] + [Path(x) for x in _glob.glob(_glob.escape(str(p.with_name(p.stem))) + " (*)" + _glob.escape(p.suffix))][:50]
    for c in cands:
        try:
            st = c.stat()
        except OSError:
            continue
        if st.st_size == size and abs(int(st.st_mtime) - mtime) <= 2:
            return True
    return False


@app.post("/api/import/run")
def import_run():
    _local_only()
    data = request.get_json(force=True)
    scan = IMPORT_SCANS.get(data.get("token", ""))
    if not scan:
        abort(400, "列表已过期，请重新选择")
    base = Path(scan["base"])
    picks = [scan["items"][i] for i in data.get("idx", []) if isinstance(i, int) and 0 <= i < len(scan["items"])]
    if not picks:
        abort(400, "没有要导入的文件")
    move = bool(data.get("move"))
    RID.value, RID.route = "lib", False

    def run(job: Job):
        skipped = 0
        for rel, size, mtime, cat in picks:
            if job.cancelled:
                raise E.Cancelled()
            src = base / rel
            job.current = src.name
            item = {"name": src.name, "src": "", "outputs": [], "error": None}
            job.items.append(item)
            try:
                dest_dir = category_dir(cat)
                dest_dir.mkdir(parents=True, exist_ok=True)
                same = dest_dir / src.name
                if _already_have(same, size, mtime):
                    skipped += 1  # 同名同大小同时间：已经导入过
                else:
                    dest = E.unique_path(same)
                    shutil.move(str(src), str(dest), copy_function=_copy) if move else _copy(src, dest)
                    vp = to_vpath(dest, "lib")
                    job.outputs.append(vp)
                    item["outputs"].append(vp)
            except OSError as e:
                msg = "文件不见了" if isinstance(e, FileNotFoundError) else (
                    "磁盘空间不足" if getattr(e, "errno", 0) == 28 else f"{e.strerror or e}")
                job.errors.append(f"{src.name}：{msg}")
                item["error"] = msg
            job.step()
        job.current = ""
        if skipped:
            job.title += f"（{skipped} 个已存在，已跳过）"

    job = submit(f"{'移入' if move else '导入'} {len(picks)} 个文件", len(picks), run)
    return jsonify(job.to_dict())


# ================================================================ 处理任务

@app.post("/api/job")
def new_job():
    data = request.get_json(force=True)
    op = data.get("op")
    paths = [resolve(v) for v in data.get("paths", [])]
    opt = data.get("options") or {}
    RID.value = (data.get("paths") or [""])[0].split("/")[0]
    RID.route = any(v.split("/")[0] in ("lib", "stage") for v in data.get("paths", []))
    if not paths:
        abort(400, "请先选择文件")

    if op == "extract":
        arcs = [p for p in paths if p.is_file()]
        pw = opt.get("password") or None
        job = submit(f"解压 {_label(arcs)}", len(arcs), lambda j: each(j, arcs, lambda p: E.extract(p, pw, j)))

    elif op == "compress":
        fmt = opt.get("format", "zip")
        if fmt not in ("zip", "7z", "tar.gz"):
            abort(400, "不支持的格式")
        parents = {p.parent for p in paths}
        if len(parents) > 1:
            abort(400, "请选择同一个文件夹里的文件")

        def run_c(j):
            j.current = _label(paths)
            j.outputs.append(j.out(E.compress(paths, fmt, opt.get("name"), opt.get("password") or None, j)))

        job = submit(f"打包 {_label(paths)} → {fmt}", 1, run_c)

    elif op == "convert":
        fmt = str(opt.get("format", "")).lower()
        quality = int(opt.get("quality", 85))
        max_side = int(opt.get("max_side") or 0) or None
        vq = opt.get("vquality", "medium")
        height = int(opt.get("height") or 0) or None
        dpi = int(opt.get("dpi") or 150)
        files = [p for p in paths if p.is_file()]
        bad = [p.name for p in files if fmt not in E.targets_for(p.name)]
        if bad:
            abort(400, f"这些文件不能转成 {fmt}：{'、'.join(bad[:5])}")

        if fmt == "pdf" and opt.get("merge") and len(files) > 1 and all(E.kind_of(p.name) == "image" for p in files):
            def run_m(j):
                base = opt.get("name") or f"{E.stem_of(files[0].name)}_等{len(files)}张"
                out = E.unique_path(files[0].parent / (_clean_name(base) + ".pdf"))
                E.images_to_pdf(files, out, max_side, j)
                vp = j.out(out)
                j.outputs.append(vp)
                j.items = [{"name": f.name, "src": to_vpath(f), "outputs": [vp], "error": None} for f in files]
            job = submit(f"{len(files)} 张图片合成 PDF", 1, run_m)
        else:
            def one(p: Path):
                k = E.kind_of(p.name)
                same = E.ext_of(p.name).replace("jpeg", "jpg").replace("tif", "tiff") == fmt
                out = E.unique_path(p.with_name(E.stem_of(p.name) + ("_压缩" if same else "") + "." + fmt))
                if k == "image" and fmt in ("mp4", "webm"):
                    E.convert_media(p, out, fmt, vq, height, job_ref[0])
                elif k == "image":
                    E.convert_image(p, out, fmt, quality, max_side)
                elif k in ("video", "audio"):
                    E.convert_media(p, out, fmt, vq, height, job_ref[0])
                elif k == "pdf" and fmt in ("png", "jpg"):
                    return E.pdf_to_images(p, fmt, dpi, job_ref[0])
                elif k == "pdf" and fmt == "txt":
                    E.pdf_to_text(p, out)
                else:
                    E.convert_office(p, out, fmt, job_ref[0])
                return out

            job_ref: list = [None]

            def run_v(j):
                job_ref[0] = j
                each(j, files, one)

            job = submit(f"{_label(files)} → {fmt.upper()}", len(files), run_v)

    elif op == "shrink":
        target = int(opt.get("target") or 0)
        files = [p for p in paths if p.is_file()]
        if target < 1024:
            abort(400, "请选择目标大小")
        tl = f"{target / 1e6:g} MB" if target >= 1e6 else f"{target / 1e3:g} KB"
        job = submit(f"缩小体积 {_label(files)} → {tl} 以内", len(files),
                     lambda j: each(j, files, lambda p: E.shrink(p, target, j)))

    elif op == "trim":
        f = paths[0]
        start, end = float(opt.get("start") or 0), float(opt.get("end") or 0)
        job = submit(f"剪辑 {f.name}", 1, lambda j: each(j, [f], lambda p: E.trim(p, start, end, j)))

    elif op == "pdf_pages":
        f = paths[0]
        mode = "split" if opt.get("mode") == "split" else "save"
        pages = opt.get("pages") or []
        job = submit(f"{'拆分' if mode == 'split' else '整理'} {f.name}", 1,
                     lambda j: each(j, [f], lambda p: E.pdf_pages(p, pages, mode, j)))

    elif op == "merge_pdf":
        pdfs = [p for p in paths if E.ext_of(p.name) == "pdf"]
        if len(pdfs) < 2:
            abort(400, "至少选 2 个 PDF")

        def run_mp(j):
            out = E.unique_path(pdfs[0].parent / (f"{E.stem_of(pdfs[0].name)}_合并.pdf"))
            E.merge_pdfs(pdfs, out, j)
            j.outputs.append(j.out(out))

        job = submit(f"合并 {len(pdfs)} 个 PDF", 1, run_mp)
    else:
        abort(400, "未知操作")
    return jsonify(job.to_dict())


def _label(paths: list[Path]) -> str:
    if not paths:
        return ""
    return paths[0].name if len(paths) == 1 else f"{paths[0].name} 等 {len(paths)} 个"


@app.get("/api/jobs")
def jobs():
    return jsonify([j.to_dict() for j in sorted(JOBS.values(), key=lambda j: -j.created)][:50])


@app.post("/api/jobs/<jid>/cancel")
def cancel(jid):
    j = JOBS.get(jid)
    if j:
        j.cancelled = True
        if j.proc:
            try:
                j.proc.kill()
            except Exception:
                pass
    return jsonify(ok=True)


@app.post("/api/jobs/clear")
def clear_jobs():
    for k in [k for k, j in JOBS.items() if j.status in ("done", "error", "cancelled")]:
        JOBS.pop(k, None)
    return jsonify(ok=True)


# ================================================================ 播放

import media as M  # noqa: E402
from flask import Response  # noqa: E402


@app.get("/api/media/info")
def media_info():
    p = resolve(request.args.get("path", ""))
    try:
        return jsonify(M.media_info(p))
    except Exception as e:  # noqa: BLE001
        return jsonify(duration=0, video=E.kind_of(p.name) == "video", direct=True, title=p.stem,
                       artist="", album="", subs=[], error=str(e))


@app.get("/api/pdf/info")
def pdf_info():
    p = resolve(request.args.get("path", ""))
    try:
        return jsonify(pages=E.pdf_page_count(p))
    except E.UserError as e:
        abort(400, str(e))


@app.get("/api/pdf/thumb")
def pdf_thumb():
    p = resolve(request.args.get("path", ""))
    try:
        data = E.pdf_thumb(p, int(request.args.get("i") or 0), min(int(request.args.get("w") or 240), 800))
    except E.UserError as e:
        abort(400, str(e))
    resp = Response(data, mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "max-age=3600"
    return resp


@app.get("/api/media/cover")
def media_cover():
    p = resolve(request.args.get("path", ""))
    c = M.cover(p)
    if not c:
        abort(404)
    resp = Response(c[0], mimetype=c[1])
    resp.headers["Cache-Control"] = "max-age=86400"
    return resp


@app.get("/api/media/sub")
def media_sub():
    p = resolve(request.args.get("path", ""))
    try:
        return Response(M.subtitle_vtt(p, request.args.get("id", "")), mimetype="text/vtt; charset=utf-8")
    except E.UserError as e:
        abort(400, str(e))


@app.get("/api/media/stream")
def media_stream():
    p = resolve(request.args.get("path", ""))
    if not E.TOOLS["ffmpeg"]:
        abort(400, "缺少 ffmpeg，无法转码播放")
    gen, mime = M.stream(p, float(request.args.get("t") or 0), request.args.get("audio") == "1")
    resp = Response(gen, mimetype=mime, direct_passthrough=True)
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ================================================================ 生命周期（桌面模式：窗口全关后自动退出）

_last_beat = [time.time()]
_ever_beat = [False]
IDLE_LIMIT = 150  # 后台窗口的定时器可能被浏览器降频到 1 次/分钟，所以给足余量


@app.post("/api/heartbeat")
def heartbeat():
    _last_beat[0] = time.time()
    _ever_beat[0] = True
    return jsonify(ok=True)


@app.post("/api/bye")
def bye():
    """某个窗口关闭：如果 8 秒内没有别的窗口心跳，就退出。"""
    if DESKTOP[0]:
        _last_beat[0] = min(_last_beat[0], time.time() - (IDLE_LIMIT - 8))
    return ("", 204)


@app.post("/api/quit")
def quit_app():
    threading.Timer(0.3, lambda: os._exit(0)).start()
    return jsonify(ok=True)


def _watchdog(idle: int):
    while True:
        time.sleep(5)
        busy = any(j.status in ("queued", "running") for j in JOBS.values())
        if busy:
            _last_beat[0] = time.time()
        limit = idle if _ever_beat[0] else 300  # 窗口一直没打开：5 分钟后退出
        if time.time() - _last_beat[0] > limit:
            os._exit(0)


def _open_window(url: str) -> None:
    """优先用 Edge / Chrome 的「应用模式」开一个独立窗口，没有就用默认浏览器。"""
    cands: list[str] = []
    sysname = platform.system()
    if sysname == "Windows":
        for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                cands += [rf"{base}\Microsoft\Edge\Application\msedge.exe",
                          rf"{base}\Google\Chrome\Application\chrome.exe"]
    elif sysname == "Darwin":
        cands += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    else:
        cands += [shutil.which(n) or "" for n in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge")]
    profile = DATA_DIR / "window"
    for exe in cands:
        if exe and Path(exe).exists():
            try:
                subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile}", "--window-size=1280,820",
                                  "--no-first-run", "--no-default-browser-check", "--autoplay-policy=no-user-gesture-required"],
                                 creationflags=E.NO_WINDOW)
                return
            except OSError:
                continue
    webbrowser.open(url)


def _already_running(port: int) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/info", timeout=1.5) as r:
            return json.loads(r.read()).get("app") == "minibox"
    except Exception:
        return False


def _port_free(host: str, port: int) -> bool:
    import socket

    with socket.socket() as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


# ================================================================ 启动

def main():
    ap = argparse.ArgumentParser(description="MiniBox 本地文件工具箱")
    ap.add_argument("--host", default=os.environ.get("MINIBOX_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("MINIBOX_PORT", 8765)))
    ap.add_argument("--no-browser", action="store_true", help="不自动打开窗口（服务器模式）")
    ap.add_argument("--browser", action="store_true", help="用默认浏览器打开，而不是独立窗口")
    a = ap.parse_args()
    desktop = not a.no_browser and not IN_DOCKER
    DESKTOP[0] = desktop

    if desktop and _already_running(a.port):  # 已经开着：再开一个窗口就行
        _open_window(f"http://127.0.0.1:{a.port}")
        return 0
    port = a.port
    if not _port_free(a.host, port):
        for cand in range(port + 1, port + 50):
            if _port_free(a.host, cand):
                port = cand
                break
    url = f"http://{'127.0.0.1' if a.host in ('0.0.0.0', '::') else a.host}:{port}"
    print(f"\n  MiniBox {VERSION} 已启动 →  {url}\n")
    opener = None
    if desktop:
        threading.Thread(target=_watchdog, args=(IDLE_LIMIT,), daemon=True).start()
        opener = (lambda: webbrowser.open(url)) if a.browser else (lambda: _open_window(url))
    try:
        from waitress import create_server

        server = create_server(app, host=a.host, port=port, threads=12, max_request_body_size=1 << 40)
        if opener:  # 端口一开好就立刻弹出窗口，不再固定等待
            threading.Thread(target=opener, daemon=True).start()
        server.run()
    except ImportError:
        if opener:
            threading.Timer(0.5, opener).start()
        app.run(host=a.host, port=port, threaded=True)
    return 0


if __name__ == "__main__":
    if FROZEN and sys.stdout is None:  # 无控制台的打包程序
        log = open(DATA_DIR / "minibox.log", "w", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
    for _s in (sys.stdout, sys.stderr):  # 中文输出不因控制台编码而崩溃
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    import multiprocessing

    multiprocessing.freeze_support()
    sys.exit(main())
