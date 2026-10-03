"""对每个样本文件 × 每个可转格式，走真实接口转换，并逐个校验输出。
用法: python matrix.py <server> <samples_dir> <library_dir>"""
import json, re, subprocess, sys, time, zipfile
from pathlib import Path
import requests
from PIL import Image
import pillow_heif; pillow_heif.register_heif_opener()
import fitz
B, SAMPLES, LIB = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
FF = sys.argv[4] if len(sys.argv) > 4 else "ffmpeg"
MARK = "MiniBox测试文字"  # 比较时去掉空白
ONLY = sys.argv[5].split(",") if len(sys.argv) > 5 and sys.argv[5] else None

def probe(p):
    out = subprocess.run([FF, "-hide_banner", "-i", str(p)], capture_output=True, text=True, errors="replace").stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", out)
    dur = int(m[1])*3600+int(m[2])*60+float(m[3]) if m else 0
    v = re.search(r"Stream #.*Video: (\w+)[^\n]*?, (\d{2,5})x(\d{2,5})", out)
    a = re.search(r"Stream #.*Audio: (\w+)", out)
    return dur, (v[1], int(v[2]), int(v[3])) if v and "attached pic" not in v[0] else None, a[1] if a else None

def frames_ok(p):  # 真正解码整段，确认没有坏帧
    r = subprocess.run([FF, "-hide_banner", "-v", "error", "-i", str(p), "-f", "null", "-"], capture_output=True, text=True, errors="replace")
    return r.returncode == 0 and not r.stderr.strip(), r.stderr.strip()[:200]

import html as _html
def norm(t):
    t = re.sub(r"\\u(-?\d+)\??", lambda m: chr(int(m[1]) % 65536), t)   # RTF \uNNNN
    t = re.sub(r"\\'([0-9a-f]{2})", "", t)
    t = re.sub(r"<[^>]+>", "", t); t = _html.unescape(t)
    return re.sub(r"\s+", "", t)
def text_of(p): return norm(_text_of(p))
def _text_of(p):
    e = p.suffix.lower()
    if e == ".rtf":  # RTF 里中文是代码页转义，交给 LibreOffice 读回纯文本
        return subprocess.run(["soffice","--headless","--cat",str(p)],capture_output=True).stdout.decode("utf-8","replace")
    if e in (".txt", ".csv", ".html", ".md"):
        return p.read_bytes().decode("utf-8", "replace")
    if e == ".pdf":
        with fitz.open(p) as d: return "".join(pg.get_text() for pg in d)
    if e in (".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp"):
        with zipfile.ZipFile(p) as z: return "".join(z.read(n).decode("utf-8","replace") for n in z.namelist() if n.endswith(".xml"))
    return ""

def check(src: Path, fmt: str, out: Path, opts):
    k = kind(src.name)
    if not out.exists(): return "输出不存在"
    if out.is_file() and out.stat().st_size == 0: return "输出为空"
    if out.is_dir():  # pdf → 图片文件夹
        imgs = sorted(out.iterdir())
        with fitz.open(src) as d: n = d.page_count
        if len(imgs) != n: return f"页数不符 {len(imgs)}/{n}"
        for i in imgs:
            with Image.open(i) as im: im.load()
            if i.suffix[1:] != fmt: return "图片格式不对"
        return None
    ext = out.suffix[1:].lower()
    if ext != fmt: return f"扩展名 {ext} ≠ {fmt}"
    if fmt in ("jpg","png","webp","avif","gif","bmp","tiff","ico") and k == "image":
        with Image.open(out) as im:
            im.load(); want = {"jpg":"JPEG","png":"PNG","webp":"WEBP","avif":"AVIF","gif":"GIF","bmp":"BMP","tiff":"TIFF","ico":"ICO"}[fmt]
            if im.format != want: return f"实际格式 {im.format}"
            ms = int(opts.get("max_side") or 0)
            if ms and max(im.size) > ms: return f"尺寸未缩小 {im.size}"
            if src.suffix.lower()==".gif" and fmt in ("gif","webp") and getattr(im,"n_frames",1) < 2: return "动图帧丢失"
        return None
    if fmt == "pdf" and k == "image":
        with fitz.open(out) as d:
            if d.page_count < 1: return "PDF 无页面"
        return None
    if k in ("video","audio") or (k=="image" and fmt in ("mp4","webm")):
        sd, sv, sa = probe(src); od, ov, oa = probe(out)
        audio_t = fmt in ("mp3","wav","flac","m4a","ogg","opus")
        if fmt == "gif":
            with Image.open(out) as im:
                if im.n_frames < 10: return f"GIF 帧数太少 {im.n_frames}"
                h = int(opts.get("height") or 480)
                if im.size[1] > h: return f"GIF 高度 {im.size[1]} > {h}"
            return None
        ok, err = frames_ok(out)
        if not ok: return "解码出错: " + err
        if sd and abs(od - sd) > 1.0: return f"时长 {od:.1f} ≠ 源 {sd:.1f}"
        if audio_t:
            if ov: return "音频文件里有视频流"
            if not oa: return "没有音频流"
        else:
            if not ov: return "没有视频流"
            if sa and not oa and src.suffix.lower()!=".gif": return "音轨丢失"
            h = int(opts.get("height") or 0)
            if h and ov[2] > h: return f"分辨率 {ov[2]}p > {h}p"
            if ov[1] % 2 or ov[2] % 2: return "宽高不是偶数（部分播放器打不开）"
        return None
    if k == "pdf" and fmt == "txt":
        return None if "MiniBox" in text_of(out) else "提取不到文字"
    if k in ("pdf","doc"):
        t = text_of(out)
        if fmt == "pdf":
            with fitz.open(out) as d:
                if d.page_count < 1: return "PDF 无页面"
        probe_txt = "第二张" if src.suffix.lower() in (".pptx",".ppt",".odp") and fmt=="pdf" else ("MiniBox" if k=="pdf" else MARK)
        if fmt in ("pdf","docx","odt","txt","html","xlsx","ods","csv","pptx","odp","rtf") and probe_txt not in t and MARK not in t:
            return "内容丢失（找不到测试文字）"
        return None
    return None

def kind(n):
    e = n.lower().rsplit(".",1)[-1]
    for k, s in (("image","jpg jpeg png webp gif bmp tif tiff ico heic heif avif"),("video","mp4 mkv mov avi webm flv wmv m4v ts 3gp mpg mpeg"),
                 ("audio","mp3 wav flac aac m4a ogg opus wma aiff aif amr"),("pdf","pdf"),("doc","doc docx odt rtf txt md wps xls xlsx ods csv et ppt pptx odp dps")):
        if e in s.split(): return k
    return "other"

def lib_path(vp): return LIB / vp.split("/",1)[1]

def run(op, paths, **o):
    r = requests.post(B+"/api/job", json={"op":op,"paths":paths,"options":o}).json()
    if "id" not in r: return None, r.get("error")
    while True:
        j = [x for x in requests.get(B+"/api/jobs").json() if x["id"]==r["id"]][0]
        if j["status"] not in ("queued","running"): return j, None
        time.sleep(0.3)

batch = "q" + str(int(time.time()))
requests.post(B+"/api/mkdir", json={"path":"stage","name":batch})
results = []; fails = []
files = sorted(p for p in SAMPLES.iterdir() if p.is_file())
if ONLY: files = [f for f in files if kind(f.name) in ONLY]
for f in files:
    up = requests.post(B+"/api/upload", data={"path":f"stage/{batch}","rel":f.name}, files={"files":(f.name, f.open("rb"))}).json()
    vp = up["saved"][0]
    targets = requests.get(B+"/api/formats", params={"name":f.name}).json()[f.name]
    if not targets:
        results.append((f.name,"-","无可用格式")); continue
    for t in targets:
        opts = {"format": t}
        if kind(f.name)=="video" and t=="mp4": opts["height"] = 240   # 顺带测分辨率
        if kind(f.name)=="image" and t=="jpg": opts.update(max_side=320, quality=70)
        if t=="gif" and kind(f.name)=="video": opts["height"] = 240
        t0 = time.time(); j, err = run("convert", [vp], **opts)
        if err: res = "接口拒绝: " + err
        elif j["status"] != "done": res = "失败: " + "; ".join(j["errors"])
        else:
            outs = j["outputs"]
            try:
                res = check(f, t, lib_path(outs[0]), opts) if outs else "没有输出"
            except Exception as ex:
                res = f"校验异常: {type(ex).__name__}: {ex}"
        ok = res is None
        results.append((f.name, t, "OK" if ok else res, round(time.time()-t0,1)))
        if not ok: fails.append((f.name, t, res))
        print(("✓" if ok else "✗"), f.name, "→", t, "" if ok else res, flush=True)
# 组合功能
imgs = [x for x in files if x.suffix.lower() in (".jpg",".png",".webp")][:3]
vps = []
for f in imgs:
    vps.append(requests.post(B+"/api/upload", data={"path":f"stage/{batch}","rel":"m_"+f.name}, files={"files":(f.name, f.open("rb"))}).json()["saved"][0])
if vps:
    j,_ = run("convert", vps, format="pdf", merge=True)
    ok = j and j["status"]=="done" and fitz.open(lib_path(j["outputs"][0])).page_count == len(vps)
    print(("✓" if ok else "✗"), "多张图片合成一个 PDF"); (None if ok else fails.append(("合成PDF","pdf","页数不对")))
pdfs = [x for x in files if x.suffix.lower()==".pdf"]
if len(pdfs) >= 2:
    vps = [requests.post(B+"/api/upload", data={"path":f"stage/{batch}","rel":"m_"+f.name}, files={"files":(f.name, f.open("rb"))}).json()["saved"][0] for f in pdfs]
    j,_ = run("merge_pdf", vps)
    n = sum(fitz.open(p).page_count for p in pdfs)
    ok = j and j["status"]=="done" and fitz.open(lib_path(j["outputs"][0])).page_count == n
    print(("✓" if ok else "✗"), "合并 PDF"); (None if ok else fails.append(("合并PDF","pdf","页数不对")))
total = sum(1 for r in results if r[1] != "-")
print(f"\n共 {total} 项转换，失败 {len(fails)} 项")
for x in fails: print("  ✗", *x)
json.dump(results, open("results.json","w"), ensure_ascii=False, indent=1)
