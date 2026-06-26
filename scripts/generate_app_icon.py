from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
ICONSET = ASSETS / "app_icon.iconset"
PNG_PATH = ASSETS / "app_icon_1024.png"
ICNS_PATH = ASSETS / "app_icon.icns"
ICO_PATH = ASSETS / "app_icon.ico"


def draw_icon(size: int = 1024) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    scale = size / 1024

    def box(values: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        return tuple(round(v * scale) for v in values)

    def pts(values: list[tuple[int, int]]) -> list[tuple[int, int]]:
        return [(round(x * scale), round(y * scale)) for x, y in values]

    draw.rounded_rectangle(box((72, 72, 952, 952)), radius=round(210 * scale), fill=(10, 116, 77, 255))
    draw.rounded_rectangle(box((120, 130, 904, 892)), radius=round(150 * scale), fill=(16, 145, 92, 255))

    grid_color = (255, 255, 255, 34)
    for x in range(220, 820, 150):
        draw.line(pts([(x, 250), (x, 675)]), fill=grid_color, width=round(4 * scale))
    for y in range(280, 680, 100):
        draw.line(pts([(190, y), (770, y)]), fill=grid_color, width=round(4 * scale))

    axis_color = (213, 255, 229, 130)
    draw.line(pts([(190, 675), (780, 675)]), fill=axis_color, width=round(10 * scale))
    draw.line(pts([(190, 675), (190, 245)]), fill=axis_color, width=round(10 * scale))

    shadow = pts([(238, 604), (365, 520), (485, 555), (615, 400), (760, 302)])
    draw.line(shadow, fill=(2, 58, 42, 125), width=round(46 * scale), joint="curve")
    line = pts([(238, 585), (365, 501), (485, 536), (615, 381), (760, 283)])
    draw.line(line, fill=(231, 255, 91, 255), width=round(36 * scale), joint="curve")
    draw.line(line, fill=(255, 255, 255, 210), width=round(12 * scale), joint="curve")

    arrow = pts([(760, 283), (715, 285), (748, 235), (835, 242), (798, 322)])
    draw.polygon(arrow, fill=(231, 255, 91, 255))
    draw.line(pts([(760, 283), (835, 242)]), fill=(255, 255, 255, 220), width=round(10 * scale))

    lens_bounds = box((550, 520, 855, 825))
    draw.ellipse(lens_bounds, fill=(246, 255, 251, 42), outline=(255, 255, 255, 245), width=round(40 * scale))
    draw.ellipse(box((600, 570, 805, 775)), outline=(168, 255, 210, 150), width=round(10 * scale))
    draw.line(pts([(795, 790), (900, 895)]), fill=(255, 255, 255, 255), width=round(54 * scale))
    draw.line(pts([(804, 781), (909, 886)]), fill=(13, 90, 64, 120), width=round(18 * scale))

    return image


def save_iconset(source: Image.Image) -> None:
    if ICONSET.exists():
        shutil.rmtree(ICONSET)
    ICONSET.mkdir(parents=True)
    sizes = [
        (16, "icon_16x16.png"),
        (32, "icon_16x16@2x.png"),
        (32, "icon_32x32.png"),
        (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"),
        (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"),
        (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"),
        (1024, "icon_512x512@2x.png"),
    ]
    for pixels, name in sizes:
        source.resize((pixels, pixels), Image.Resampling.LANCZOS).save(ICONSET / name)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    source = draw_icon()
    source.save(PNG_PATH)
    source.save(ICO_PATH, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    save_iconset(source)
    subprocess.run(["iconutil", "-c", "icns", str(ICONSET), "-o", str(ICNS_PATH)], check=True)
    print(ICNS_PATH)
    print(ICO_PATH)


if __name__ == "__main__":
    main()
