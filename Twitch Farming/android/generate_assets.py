from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

ROOT = Path(__file__).resolve().parent
size = 512
image = Image.new("RGBA", (size, size), (15, 15, 26, 255))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((36, 36, 476, 476), radius=110, fill=(124, 81, 224, 255))
draw.rounded_rectangle((148, 136, 365, 350), radius=45, fill=(255, 255, 255, 255))
draw.polygon([(270, 345), (322, 420), (322, 345)], fill=(255, 255, 255, 255))
draw.rectangle((196, 196, 220, 290), fill=(124, 81, 224, 255))
draw.rectangle((294, 196, 318, 290), fill=(124, 81, 224, 255))
image.save(ROOT / "icon.png")
presplash = Image.new("RGB", (480, 800), (15, 15, 26))
pd = ImageDraw.Draw(presplash)
pd.rounded_rectangle((155, 250, 325, 420), radius=42, fill=(124, 81, 224))
try:
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 34)
except OSError:
    font = ImageFont.load_default()
pd.text((65, 465), "TWITCH FARMING", font=font, fill=(245, 242, 255))
presplash.save(ROOT / "presplash.png")
