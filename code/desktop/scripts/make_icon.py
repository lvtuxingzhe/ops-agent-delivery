"""生成桌面端正式图标：深蓝圆角底 + 白色 OA 字标。

运行：backend/.venv/Scripts/python.exe desktop/scripts/make_icon.py
产物：desktop/build/icon.png（1024）、desktop/build/icon.ico（16~256 多尺寸）
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
SIZE = 1024


def main() -> None:
    BUILD.mkdir(parents=True, exist_ok=True)
    gradient = Image.new("RGB", (SIZE, SIZE))
    top = (37, 99, 235)
    bottom = (7, 17, 32)
    for y in range(SIZE):
        ratio = y / (SIZE - 1)
        color = tuple(round(top[i] + (bottom[i] - top[i]) * ratio) for i in range(3))
        for x in range(SIZE):
            gradient.putpixel((x, y), color)

    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle((44, 44, SIZE - 44, SIZE - 44), radius=210, fill=255)

    icon = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    icon.paste(gradient, (0, 0), mask)

    draw = ImageDraw.Draw(icon)
    draw.rounded_rectangle((70, 70, SIZE - 70, SIZE - 70), radius=180, outline=(96, 165, 250, 120), width=2)

    font = ImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf", 400)
    draw.text((SIZE / 2, 455), "OA", font=font, fill=(255, 255, 255, 255), anchor="mm")

    small = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 64)
    draw.text((SIZE / 2, 745), "OPS AGENT", font=small, fill=(147, 197, 253, 255), anchor="mm")

    png = BUILD / "icon.png"
    icon.save(png)
    icon.save(BUILD / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"OK {png.stat().st_size} bytes -> {png}")
    print(f"OK {(BUILD / 'icon.ico').stat().st_size} bytes -> {BUILD / 'icon.ico'}")


if __name__ == "__main__":
    main()
