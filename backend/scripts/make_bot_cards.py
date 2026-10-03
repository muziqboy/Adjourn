"""Render the meeting bot's camera cards (1280x720 JPEG, what Recall's output_video wants):

    app/assets/bot_listening.jpg   shown while Adjourn listens
    app/assets/bot_hand.jpg        "raised hand": Adjourn has an answer

Run once after changing the design: uv run python scripts/make_bot_cards.py
(Pillow is a dev dependency; the backend only reads the committed JPEGs.)
macOS fonts; rendered at 2x and downscaled, as Recall recommends.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1] / "app" / "assets"
BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
REGULAR = "/System/Library/Fonts/Supplemental/Arial.ttf"
EMOJI = "/System/Library/Fonts/Apple Color Emoji.ttc"
W, H = 2560, 1440


def emoji(char: str, size: int) -> Image.Image:
    font = ImageFont.truetype(EMOJI, 160)  # Apple's emoji font only renders at its bitmap size
    img = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((0, 0), char, font=font, embedded_color=True)
    return img.crop(img.getbbox()).resize((size, size), Image.LANCZOS)


def card(name: str, bg: str, accent: str, icon: str, title: str, subtitle: str) -> None:
    img = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)
    mark = emoji(icon, 360)
    img.paste(mark, ((W - 360) // 2, 260), mark)
    for text, font, y, color in (
        (title, ImageFont.truetype(BOLD, 150), 720, "white"),
        (subtitle, ImageFont.truetype(REGULAR, 90), 940, accent),
        ("Adjourn", ImageFont.truetype(BOLD, 70), 1220, "#9aa0a6"),
    ):
        width = draw.textlength(text, font=font)
        draw.text(((W - width) / 2, y), text, font=font, fill=color)
    img.resize((1280, 720), Image.LANCZOS).save(OUT / name, "JPEG", quality=90)
    print("wrote", OUT / name)


card("bot_listening.jpg", "#1f2124", "#81c995", "\N{EAR}", "Listening", "I'll raise my hand when I can help")
card("bot_hand.jpg", "#3d2f00", "#fdd663", "\N{RAISED HAND}", "I have an answer",
     "Say “Go ahead, Adjourn”")
