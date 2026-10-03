"""生成应用图标：packaging/icon.ico + icon.png"""
from pathlib import Path
from PIL import Image, ImageDraw

S = 1024
im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(im)
d.rounded_rectangle((40, 40, S - 40, S - 40), radius=220, fill=(15, 118, 110, 255))          # 底
d.rounded_rectangle((230, 300, 794, 760), radius=70, fill=(255, 255, 255, 255))              # 盒身
d.rounded_rectangle((200, 250, 824, 390), radius=60, fill=(153, 246, 228, 255))              # 盒盖
d.rounded_rectangle((440, 450, 584, 500), radius=25, fill=(15, 118, 110, 255))               # 把手
here = Path(__file__).parent
im.resize((512, 512), Image.LANCZOS).save(here / "icon.png")
im.save(here / "icon.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
