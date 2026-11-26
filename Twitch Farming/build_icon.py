from pathlib import Path

from PIL import Image, ImageDraw


root = Path(__file__).resolve().parent
assets = root / "assets"
assets.mkdir(parents=True, exist_ok=True)
size = 1024
purple = (145, 70, 255, 255)
white = (255, 255, 255, 255)
image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((64, 64, 960, 960), radius=220, fill=purple)
draw.polygon([(370, 301), (275, 391), (275, 715), (389, 715), (389, 805), (484, 715), (560, 715), (731, 553), (731, 301)], fill=white)
draw.polygon([(693, 535), (617, 607), (541, 607), (474, 670), (474, 607), (389, 607), (389, 337), (693, 337)], fill=purple)
draw.rectangle((598, 403, 636, 511), fill=purple)
draw.rectangle((493, 403, 532, 511), fill=purple)
sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
for name in ("app.ico", "twitch.ico"):
    image.save(assets / name, format="ICO", sizes=sizes)
