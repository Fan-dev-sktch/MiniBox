# MiniBox · 本地文件工具箱

免费、极简、**全部在你自己电脑上运行**的文件管理 + 解压 + 格式转换 + 音视频播放工具。
文件不上传任何服务器，没有账号、没有广告、没有次数限制。

## 下载即用（给普通用户）

到 [Releases](https://github.com/Fan-dev-sktch/MiniBox/releases) 下载最新版。

| 系统 | 下载 | 说明 |
|---|---|---|
| Windows 10/11 | `MiniBox-Setup-x.x.x.exe` | 双击安装（不需要管理员权限），桌面出现 MiniBox 图标 |
| Windows 免安装 | `MiniBox-windows-portable.zip` | 解压后双击 `MiniBox.exe`，设置也保存在这个文件夹里，可放 U 盘 |
| macOS | `MiniBox-macos-*.dmg` | 拖进「应用程序」；首次打开请右键 → 打开 |
| Linux | `MiniBox-linux-x64.tar.gz` | 解压后运行 `MiniBox/MiniBox` |

不需要装 Python，音视频转换和播放所需的 ffmpeg 已内置。打开后是一个独立窗口（借用系统自带的 Edge/Chrome 内核），关掉窗口程序就自动退出。

> Windows 首次运行时 SmartScreen 可能提示「已保护你的电脑」，点「更多信息 → 仍要运行」即可（开源小软件没有购买代码签名证书都会这样）。

![](https://img.shields.io/badge/license-MIT-green) ![](https://img.shields.io/badge/python-3.9+-blue)

## 能做什么

| 类别 | 功能 |
|---|---|
| 我的文件 | MiniBox 自己的独立空间（不扫描你电脑上的文件夹），固定分成 图片 / 视频 / 音乐 / 文档 / 压缩包 / 其他；拖进来的文件**自动按类型归档**，分类里可以再建自己的文件夹；搜索、排序、缩略图、重命名、移动、删除到回收站 |
| 从电脑导入 | 点「导入」：选位置（下载 / 桌面 / 文档 / 图片 / 视频 / 音乐 / 任意文件夹），再按**类型**和**时间**（今天 / 7 天内 / 30 天内 / 今年 / 自定义日期）筛选，勾掉不要的，一键复制进来并自动分类；已导入过的会自动跳过；可选「移入」（导入后从原位置移走）。只有你点导入时才会读取所选文件夹 |
| 格式转换页 | 单独一页：左边拖入文件，右边选格式，点「开始转换」；多种类型混在一起也行，每类分别选格式；结果自动存进「我的文件」对应分类 |
| 缩小体积 | 选「压到多大以内」（如 200 KB / 2 MB / 25 MB 或自定义）：照片自动调画质和尺寸；PDF 只压缩里面的图片，文字保持清晰可选；视频按目标大小两遍编码并自动选分辨率；音频算好码率转 MP3 |
| 剪辑 | 视频和音乐拖动两端选出一段保存；3 分钟以内的片段精确到每一帧，更长的用无损快剪，几秒完成 |
| PDF 页面 | 看着缩略图旋转、删除、拖动排序，另存新 PDF；或每页拆成单独的 PDF |
| 解压 | zip（自动修复 Windows 中文乱码）、7z、rar、tar / tar.gz / tgz / tar.bz2 / tar.xz、gz、bz2、xz、iso 等；支持密码；**智能解压**：包里只有一个文件夹就直接放出来，不会套两层 |
| 打包 | zip、7z（可加密，含文件名加密）、tar.gz |
| 图片 | JPG / PNG / WebP / AVIF / GIF / BMP / TIFF / ICO / PDF 互转，读取 iPhone 的 HEIC；可调画质、按长边缩小；**多张图片合成一个 PDF**；保留 GIF 动图 |
| 音视频 | MP4 / WebM / MKV / MOV / GIF、MP3 / WAV / FLAC / M4A / OGG / Opus；视频压缩（选画质 + 分辨率）、视频转 GIF、提取音频 |
| PDF | 每页导出为 PNG/JPG（可选清晰度）、提取文字、**合并多个 PDF**、转 Word（需 LibreOffice） |
| Office 文档 | Word / Excel / PPT / WPS / ODF / TXT / Markdown → PDF，以及 docx⇄odt、xlsx⇄ods⇄csv、pptx⇄odp（需 LibreOffice） |
| 音乐播放 | 底部常驻播放条，切换文件夹不中断；播放列表、列表循环 / 单曲循环 / 随机；显示专辑封面和歌名歌手；支持键盘媒体键 |
| 视频播放 | 文件夹连播、自动下一集、**记住每个视频看到哪里**、倍速 0.5–3×、自动加载同名字幕（srt/ass/vtt）和 mkv 内嵌字幕、全屏 |
| 万能格式 | mkv / avi / flv / wmv / HEVC / wma / ape 等浏览器本身放不了的格式，**自动边转码边播放**，可拖动进度 |

批量处理：多选后一次转换；任务在后台排队，可看进度、可取消，单个失败不影响其他。转换结果放在原文件旁边，**原文件不动**。

## 从源码运行（开发者）

需要 [Python 3.9+](https://www.python.org/downloads/)（Windows 安装时勾选 *Add Python to PATH*）。

| 系统 | 操作 |
|---|---|
| Windows | 双击 `start.bat` |
| macOS | 双击 `start.command`（第一次若提示无法打开：右键 → 打开） |
| Linux | 终端运行 `./start.sh` |

第一次会自动建立独立环境并安装依赖（约 1 分钟，只需一次），然后自动打开 MiniBox 窗口（<http://127.0.0.1:8765>）。

资料库默认放在用户目录下的 `MiniBox` 文件夹（便携版在程序旁的 `资料库` 文件夹），左下角「设置」里可以换到别的盘。

### 可选组件（装了就自动启用，左下角「本机能力」会亮绿灯）

| 组件 | 用途 | 说明 |
|---|---|---|
| ffmpeg | 音视频 | **已自动内置**（随依赖安装），无需操作 |
| [LibreOffice](https://www.libreoffice.org/download/) | Office 文档转换、PDF 转 Word | 免费，装好后重启 MiniBox |
| [7-Zip](https://www.7-zip.org/) | rar / iso 等更多格式、AES 加密 zip | Windows 10+ 和 macOS 自带的 tar 已能解 rar，一般不用装；Linux 建议 `sudo apt install 7zip` |

## Docker 部署（NAS / 服务器）

```bash
docker compose up -d --build
```

镜像里已包含 ffmpeg、LibreOffice、7-Zip 和中文字体。修改 `docker-compose.yml` 里的 `./files` 为你要管理的目录。
默认只监听本机 `127.0.0.1`；**MiniBox 没有登录功能**，如果要开放到局域网，请确保网络可信。

## 快捷键

| 键 | 作用 |
|---|---|
| 单击 / Ctrl(⌘)+单击 / Shift+单击 | 选择 / 多选 / 连选 |
| 双击、Enter | 打开文件夹或预览 |
| 空格 | 快速预览，← → 切换上一个/下一个 |
| F2 | 重命名 |
| Delete | 删除（进回收站） |
| Ctrl(⌘)+A | 全选 |
| Ctrl(⌘)+F | 搜索 |
| Backspace | 返回上一级 |
| 视频中：空格 / ← → / ↑ ↓ / F / N / [ ] / M | 暂停 / 快退快进 5 秒（Shift 30 秒）/ 音量 / 全屏 / 下一集 / 调速 / 静音 |

右键文件有完整菜单。

## 命令行参数

```
python app.py --port 8765 --host 127.0.0.1 --no-browser
```

环境变量：`MINIBOX_LIBRARY`（资料库目录）、`MINIBOX_DATA`（设置和缓存目录）。
设置保存在 `%APPDATA%\MiniBox`（Windows）、`~/Library/Application Support/MiniBox`（macOS）或 `~/.local/share/minibox`（Linux）；便携版保存在程序旁的 `data` 文件夹。

## 和现有开源项目的区别

做之前调研过 GitHub 上的同类项目，它们都很好，各有侧重：

- [VERT](https://github.com/VERT-sh/vert)：网页版格式转换，浏览器本地处理；不管理文件、不解压。
- [ConvertX](https://github.com/C4illin/ConvertX)：支持 1000+ 格式的自托管转换器；需 Docker，没有文件管理和解压。
- [File Converter](https://github.com/Tichau/FileConverter)：Windows 右键转换，非常顺手；仅 Windows。
- [PeaZip](https://github.com/peazip/PeaZip) / 7-Zip：解压最全的桌面软件；不做格式转换。
- [Cloudreve](https://github.com/cloudreve/cloudreve) / [可道云 kodbox](https://github.com/kalcaddle/kodbox)：功能强大的私有网盘，能在线解压和播放；但需要部署服务器、登录账号，不做格式转换。

MiniBox 的定位是把这几件事**放进同一个极简桌面软件**：管理文件 → 解压 → 转换 → 打包 → 播放，一站完成，装上就能用。

## 打包发布

推送一个版本标签，GitHub Actions 会自动打出 Windows 安装包、便携版、macOS dmg（Intel + Apple 芯片）和 Linux 包，并发布到 Releases：

```bash
git tag v1.1.0 && git push origin v1.1.0
```

本地手动打包：`pip install pyinstaller && pyinstaller packaging/minibox.spec`，Windows 安装包再运行 `makensis packaging/installer.nsi`。

## 目录结构

```
app.py          服务与接口（文件管理、任务队列、桌面窗口与生命周期）
engine.py       解压 / 打包 / 转换引擎
media.py        播放：媒体信息、封面、字幕、实时转码串流
packaging/      图标、PyInstaller 配置、NSIS 安装包脚本
.github/        自动打包流程
static/         界面（单个 HTML，无需构建）
start.*         一键启动脚本
Dockerfile      容器部署
```

## 许可

MIT。可以随意使用、修改、分发。
