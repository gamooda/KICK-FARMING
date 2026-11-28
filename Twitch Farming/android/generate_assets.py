from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
ICON_ROOT = ROOT / "icons"
ICON_ROOT.mkdir(parents=True, exist_ok=True)

PURPLE_START = (179, 107, 255)
PURPLE_END = (113, 54, 216)
DARK = (15, 15, 26)
EYE = (33, 19, 56)


def make_logo(size, include_eyes=True, background=False):
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    scale = size / 48
    points = [
        (6, 4), (44, 4), (44, 32), (30, 46),
        (21, 46), (21, 38), (10, 38), (6, 34)
    ]
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).polygon([(int(x * scale), int(y * scale)) for x, y in points], fill=255)
    gradient = Image.new("RGBA", (size, size))
    pixels = gradient.load()
    for y in range(size):
        for x in range(size):
            amount = min(1.0, max(0.0, (x + y) / max(1, size * 2)))
            pixels[x, y] = tuple(
                int(PURPLE_START[index] * (1 - amount) + PURPLE_END[index] * amount)
                for index in range(3)
            ) + (255,)
    gradient.putalpha(mask)
    image.alpha_composite(gradient)
    if include_eyes:
        draw = ImageDraw.Draw(image)
        eye_w = max(2, int(size * 5 / 48))
        eye_h = max(3, int(size * 13 / 48))
        for center_x in (19.5, 30.5):
            x = int(center_x * scale - eye_w / 2)
            y = int(19.5 * scale - eye_h / 2)
            draw.rounded_rectangle((x, y, x + eye_w, y + eye_h), radius=max(1, size // 48), fill=EYE + (255,))
    if background:
        result = Image.new("RGBA", (size, size), DARK + (255,))
        result.alpha_composite(image)
        return result
    return image


def draw_icon(name, color):
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    width = 5
    def line(points, joint="curve"):
        draw.line(points, fill=color, width=width, joint=joint)
    def rect(box, radius=5):
        draw.rounded_rectangle(box, radius=radius, outline=color, width=width)
    def circle(box):
        draw.ellipse(box, outline=color, width=width)
    if name == "channels":
        for box in ((8, 8, 28, 28), (36, 8, 56, 28), (8, 36, 28, 56), (36, 36, 56, 56)):
            rect(box, 4)
    elif name == "drops":
        line([(32, 6), (19, 24), (13, 36), (14, 46), (21, 54), (32, 57), (43, 54), (50, 46), (51, 36), (45, 24), (32, 6)])
        draw.arc((20, 33, 45, 53), 200, 320, fill=color, width=width - 1)
    elif name == "followers":
        circle((8, 8, 30, 30))
        circle((37, 12, 55, 30))
        line([(3, 55), (5, 45), (12, 37), (25, 35), (34, 41), (38, 51)])
        line([(39, 38), (50, 39), (58, 48), (60, 55)])
    elif name == "bets":
        line([(9, 8), (9, 55), (57, 55)])
        line([(15, 44), (27, 31), (37, 39), (53, 17)])
        line([(43, 17), (53, 17), (53, 27)])
    elif name == "events":
        rect((12, 7, 52, 57), 4)
        line([(21, 21), (43, 21)])
        line([(21, 32), (43, 32)])
        line([(21, 43), (38, 43)])
    elif name == "settings":
        circle((18, 18, 46, 46))
        circle((27, 27, 37, 37))
        for x1, y1, x2, y2 in ((32, 4, 32, 14), (32, 50, 32, 60), (4, 32, 14, 32), (50, 32, 60, 32), (12, 12, 19, 19), (45, 45, 52, 52), (45, 19, 52, 12), (12, 52, 19, 45)):
            line([(x1, y1), (x2, y2)])
    elif name == "login":
        line([(30, 9), (50, 9), (50, 55), (30, 55)])
        line([(7, 32), (38, 32)])
        line([(27, 20), (39, 32), (27, 44)])
    elif name == "logout":
        line([(34, 9), (14, 9), (14, 55), (34, 55)])
        line([(57, 32), (26, 32)])
        line([(37, 20), (25, 32), (37, 44)])
    elif name == "plus":
        circle((7, 7, 57, 57))
        line([(32, 18), (32, 46)])
        line([(18, 32), (46, 32)])
    elif name == "refresh":
        draw.arc((9, 8, 55, 54), 205, 40, fill=color, width=width)
        draw.polygon([(47, 7), (58, 9), (54, 21)], fill=color)
        draw.arc((9, 10, 55, 56), 25, 220, fill=color, width=width)
        draw.polygon([(17, 57), (6, 55), (10, 43)], fill=color)
    elif name == "play":
        draw.polygon([(19, 10), (53, 32), (19, 54)], outline=color, fill=(0, 0, 0, 0))
        line([(19, 10), (53, 32), (19, 54), (19, 10)])
    elif name == "stop":
        rect((13, 13, 51, 51), 5)
    elif name == "external":
        rect((8, 21, 43, 56), 4)
        line([(29, 8), (56, 8), (56, 35)])
        line([(56, 8), (31, 33)])
    elif name == "copy":
        rect((21, 9, 54, 45), 4)
        rect((9, 20, 42, 56), 4)
    elif name == "close":
        line([(15, 15), (49, 49)])
        line([(49, 15), (15, 49)])
    elif name == "trash":
        line([(10, 17), (54, 17)])
        line([(23, 10), (23, 6), (41, 6), (41, 10)])
        line([(17, 22), (21, 56), (43, 56), (47, 22)])
        line([(27, 28), (27, 48)])
        line([(37, 28), (37, 48)])
    elif name == "gift":
        rect((10, 25, 54, 55), 3)
        rect((7, 17, 57, 26), 3)
        line([(32, 17), (32, 55)])
        circle((15, 6, 31, 20))
        circle((33, 6, 49, 20))
    elif name == "check":
        line([(9, 33), (25, 48), (55, 14)])
    elif name == "power":
        draw.arc((10, 12, 54, 57), 40, 320, fill=color, width=width)
        line([(32, 5), (32, 31)])
    elif name == "save":
        rect((9, 7, 55, 57), 4)
        rect((19, 7, 45, 26), 2)
        rect((19, 36, 45, 55), 2)
    elif name == "github":
        circle((13, 10, 51, 49))
        line([(23, 47), (21, 57)])
        line([(41, 47), (43, 57)])
        line([(19, 22), (12, 15)])
        line([(45, 22), (52, 15)])
    return image


app_icon = make_logo(512, include_eyes=True, background=True)
app_icon.save(ROOT / "icon.png")
make_logo(256, include_eyes=False).save(ICON_ROOT / "logo_bg.png")

presplash = Image.new("RGB", (480, 800), DARK)
logo = app_icon.resize((180, 180), Image.Resampling.LANCZOS)
presplash.paste(logo.convert("RGB"), (150, 235))
draw = ImageDraw.Draw(presplash)
try:
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
except OSError:
    font = ImageFont.load_default()
draw.text((65, 455), "TWITCH FARMING", font=font, fill=(245, 242, 255))
presplash.save(ROOT / "presplash.png")

icons = (
    "channels", "drops", "followers", "bets", "events", "settings",
    "login", "logout", "plus", "refresh", "play", "stop", "external",
    "copy", "close", "trash", "gift", "check", "power", "save", "github"
)
for name in icons:
    draw_icon(name, (171, 165, 190, 255)).save(ICON_ROOT / (name + ".png"))
    draw_icon(name, (196, 160, 255, 255)).save(ICON_ROOT / (name + "_active.png"))
