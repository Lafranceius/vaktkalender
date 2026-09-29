"""Generate the pixel art in vaktkalender/static/ from the ASCII maps below.

    python3 scripts/make_pixel_art.py

Each character is one pixel; '.' is transparent. Filled shapes get a dark
outline automatically, which gives the sticker look.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "vaktkalender" / "static"

INK = "#1C293C"
PALETTE = {
    "K": INK,
    "R": "#E4002B",  # bull red
    "r": "#A8001F",  # bull shade
    "Y": "#FDC800",  # sun
    "y": "#FFE35C",  # sun highlight
    "B": "#1E3C96",  # can blue
    "b": "#3257C8",  # can blue highlight
    "S": "#C9CED6",  # can silver
    "s": "#EEF0F3",  # can silver highlight
    "W": "#FFFFFF",
    "H": "#FFF1CC",  # horn
    "N": "#8B5A2B",  # brush handle
    "P": "#FF5CA8",  # paint (recoloured live via CSS class "paint")
}

BULL = """
....................................
.RR................................H
RRR..............RRRRR............HH
..R...........RRRRRRRRRR.........HH.
..R.........RRRRRRRRRRRRRR......HH..
...R......RRRRRRRRRRRRRRRRRR...HH...
...R....RRRRRRRRRRRRRRRRRRRRRHHHR...
....RRRRRRRRRRRRRRRRRRRRRRRRrRRRRR..
....RRRRRRRRRRRRRRRRRRRRRRRRRrRRRRR.
....RRRRRRRRRRRRRRRRRRRRRRRRRRrKRRR.
....RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR
.....RRRRRRRRRRRRRRRRRRRRRR.RRRRRRRR
.....rRRRRRRRRRRRRRRRRRRRR...RRRRRR.
....rrRRRR.......rrRRRRRR.....RRR...
...rrRRR..........rrRRRRRR..........
..rr.RRR............rr.RRRRR........
.rr.RRR..............rr..RRRR.......
....RR....................RR........
"""

CAN = """
................
.....ssssss.....
....sSSSSSSs....
....KKKKKKKK....
....SsBBBBbS....
....SsBBBBbS....
....SsYYYYbS....
....RYYYYYYR....
....RRYYYYRR....
....BbSSSSsB....
....BbSSSSsB....
....BbSSSSsB....
....BbSSSSsB....
....KKKKKKKK....
.....ssssss.....
................
"""


PAINTER_BODY = """
..............................
..............................
..............................
..............................
..............................
.....ssssssssss...............
....sSSSSSSSSSSs..............
....KKKKKKKKKKKK..............
....BBBBBBSSSSSS..............
....BbBBBBSSSSsS..............
....BbWWBBSWWSsS..............
....BbWKBBSWKSsS..............
....BbBBBBSSSSsS..............
....BbKBBBSSSKsS..............
....BbBKKKKKKSsS..............
....BbBBBBSSSSsS..............
....BbBBBYYSSSsS..............
....RRBBYYYYSSRR..............
....RRRYYYYYYRRR..............
....SRRRYYYYRRRB..............
....SsSSSSBBBBbB..............
....SsSSSSBBBBbB..............
....SsSSSSBBBBbB..............
....SsSSSSBBBBbB..............
....SsSSSSBBBBbB..............
....KKKKKKKKKKKK..............
.....ssssssssss...............
......KK....KK................
......KK....KK................
.....RRR...RRR................
"""

LEFT_ARM = [(3, 16, "K"), (2, 17, "K"), (2, 18, "K"), (1, 19, "K"),
            (0, 20, "W"), (1, 20, "W"), (0, 21, "W"), (1, 21, "W")]
BRUSH_UP = [(16, 10, "K"), (17, 9, "K"), (18, 8, "K"),
            (19, 6, "W"), (20, 6, "W"), (19, 7, "W"), (20, 7, "W"),
            (21, 5, "N"), (22, 4, "N"), (23, 3, "N"), (24, 2, "S"), (25, 2, "S"),
            (25, 1, "P"), (26, 1, "P"), (27, 1, "P"), (26, 0, "P"), (27, 0, "P")]
BRUSH_DOWN = [(16, 14, "K"), (17, 14, "K"),
              (18, 13, "W"), (19, 13, "W"), (18, 14, "W"), (19, 14, "W"),
              (20, 14, "N"), (21, 14, "N"), (22, 14, "N"), (23, 14, "S"), (24, 14, "S"),
              (25, 13, "P"), (26, 13, "P"), (25, 14, "P"), (26, 14, "P"), (27, 14, "P"),
              (25, 15, "P"), (26, 15, "P"), (27, 17, "P")]


def parse(art: str) -> list[list[str]]:
    rows = [line for line in art.strip("\n").splitlines()]
    width = max(len(r) for r in rows)
    return [list(r.ljust(width, ".")) for r in rows]


def mirror(grid: list[list[str]]) -> list[list[str]]:
    return [list(reversed(row)) for row in grid]


def blank(width: int, height: int) -> list[list[str]]:
    return [["."] * width for _ in range(height)]


def paste(dst: list[list[str]], src: list[list[str]], x0: int, y0: int) -> None:
    for y, row in enumerate(src):
        for x, ch in enumerate(row):
            if ch != "." and 0 <= y0 + y < len(dst) and 0 <= x0 + x < len(dst[0]):
                dst[y0 + y][x0 + x] = ch


def disc(grid: list[list[str]], cx: float, cy: float, r: float) -> None:
    for y in range(len(grid)):
        for x in range(len(grid[0])):
            d2 = (x - cx) ** 2 + (y - cy) ** 2
            if d2 <= r * r:
                grid[y][x] = "Y"


def outline(grid: list[list[str]], pad: int = 1) -> list[list[str]]:
    h, w = len(grid), len(grid[0])
    out = blank(w + 2 * pad, h + 2 * pad)
    paste(out, grid, pad, pad)
    filled = {(x, y) for y, row in enumerate(out) for x, ch in enumerate(row) if ch != "."}
    for x, y in [(x, y) for x, y in filled if out[y][x] != "K"]:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= ny < len(out) and 0 <= nx < len(out[0]) and (nx, ny) not in filled:
                out[ny][nx] = "K"
    return out


def rects(grid: list[list[str]]) -> str:
    w = len(grid[0])
    out = []
    for y, row in enumerate(grid):
        x = 0
        while x < w:
            ch = row[x]
            if ch == ".":
                x += 1
                continue
            run = 1
            while x + run < w and row[x + run] == ch:
                run += 1
            cls = ' class="paint"' if ch == "P" else ""
            out.append(f'<rect x="{x}" y="{y}" width="{run}" height="1" fill="{PALETTE[ch]}"{cls}/>')
            x += run
    return "".join(out)


def to_svg(grid: list[list[str]], title: str) -> str:
    h, w = len(grid), len(grid[0])
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" shape-rendering="crispEdges" '
        f'role="img" aria-label="{title}"><title>{title}</title>' + rects(grid) + "</svg>\n"
    )


def to_png(grid: list[list[str]], scale: int, background: str | None = None, pad: int = 0) -> bytes:
    def rgb(hex_: str) -> tuple[int, int, int, int]:
        return int(hex_[1:3], 16), int(hex_[3:5], 16), int(hex_[5:7], 16), 255

    clear = rgb(background) if background else (0, 0, 0, 0)
    h, w = len(grid), len(grid[0])
    size_w, size_h = w * scale + 2 * pad, h * scale + 2 * pad
    raw = bytearray()
    for py in range(size_h):
        raw.append(0)
        for px in range(size_w):
            gx, gy = (px - pad) // scale, (py - pad) // scale
            inside = 0 <= gx < w and 0 <= gy < h and px >= pad and py >= pad
            ch = grid[gy][gx] if inside else "."
            raw.extend(rgb(PALETTE[ch]) if ch != "." else clear)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size_w, size_h, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")


def logo() -> list[list[str]]:
    """Two bulls charging at a sun. Each layer gets its own outline so they stay separate."""
    bull = outline(parse(BULL))
    bw, bh = len(bull[0]), len(bull)
    width, height = bw * 2 - 1, bh + 7
    sun = blank(width, height)
    disc(sun, (width - 1) / 2, 12.5, 12.5)
    grid = outline(sun, pad=0)
    paste(grid, bull, 0, height - bh)
    paste(grid, mirror(bull), width - bw, height - bh)
    return grid


def painter_frame(arm: list[tuple[int, int, str]]) -> list[list[str]]:
    grid = parse(PAINTER_BODY)
    for x, y, ch in LEFT_ARM + arm:
        grid[y][x] = ch
    return outline(grid)


def painter_svg() -> str:
    """Inline SVG (a Jinja partial) with two frames the CSS flips between while painting."""
    up, down = painter_frame(BRUSH_UP), painter_frame(BRUSH_DOWN)
    h, w = len(up), len(up[0])
    return (
        f'<svg class="painter__art" viewBox="0 0 {w} {h}" shape-rendering="crispEdges" aria-hidden="true">'
        f'<g class="f1">{rects(up)}</g><g class="f2">{rects(down)}</g></svg>\n'
    )


def main() -> None:
    art = logo()
    (STATIC / "bulls.svg").write_text(to_svg(art, "To okser som stanger mot en sol, i pikselkunst"))
    can = outline(parse(CAN), pad=0)
    (STATIC / "can.svg").write_text(to_svg(can, "Energidrikkboks i pikselkunst"))
    (STATIC / "favicon-32.png").write_bytes(to_png(can, 2))
    (STATIC / "apple-touch-icon.png").write_bytes(to_png(can, 10, background="#FDC800", pad=10))
    (STATIC.parent / "templates" / "_painter.svg").write_text(painter_svg())
    print("Wrote bulls.svg, can.svg, favicon-32.png, apple-touch-icon.png, templates/_painter.svg")


if __name__ == "__main__":
    main()
