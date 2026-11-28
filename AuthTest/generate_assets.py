from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent


def font(size):
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ):
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def create_image(size, target, presplash=False):
    image = Image.new("RGB", (size, size), (15, 13, 25))
    draw = ImageDraw.Draw(image)
    margin = size // 12
    draw.rounded_rectangle(
        (margin, margin, size - margin, size - margin),
        radius=size // 5,
        fill=(85, 49, 151),
        outline=(143, 103, 215),
        width=max(2, size // 90),
    )
    label = "AUTH"
    text_font = font(size // (7 if presplash else 6))
    box = draw.textbbox((0, 0), label, font=text_font)
    x = (size - (box[2] - box[0])) // 2
    y = (size - (box[3] - box[1])) // 2 - box[1]
    draw.text((x, y), label, font=text_font, fill=(255, 255, 255))
    if presplash:
        subtitle_font = font(size // 22)
        subtitle = "TWITCH DEVICE FLOW DIAGNOSTICS"
        subtitle_box = draw.textbbox((0, 0), subtitle, font=subtitle_font)
        sx = (size - (subtitle_box[2] - subtitle_box[0])) // 2
        draw.text((sx, size * 0.68), subtitle, font=subtitle_font, fill=(205, 190, 235))
    image.save(target)


create_image(512, ROOT / "icon.png")
create_image(512, ROOT / "presplash.png", presplash=True)
