"""Pillow 卡片渲染器 - Pokemon TCG 风格。

- render_pokemon: 宝可梦信息卡
- render_ability: 能力详情卡

设计:
  外框: 属性色圆角粗边 + 渐变内底
  顶部: 大字名 + 编号 + 属性 badge + 主能力 badge
  中部: 像素艺术大图窗(带白色窄框 + 阴影)
  下部: pill 形数值条 + 小节分隔 + 多行文本

字体加载顺序: 插件 fonts/ 目录 → 系统中文字体 → PIL 默认(兜底).
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .icon_cache import IconCache
from .search import Entry


# ---------- 字体 ----------
_FONT_CANDIDATES = [
    "NotoSansCJK-Regular.ttc", "NotoSansCJKsc-Regular.otf", "SourceHanSansSC-Regular.otf",
    "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]


class FontPool:
    def __init__(self, plugin_font_dir: Path):
        self.font_dir = plugin_font_dir
        self._path: str | None = None
        self._cache: dict[int, ImageFont.FreeTypeFont] = {}
        self._resolve_path()

    def _resolve_path(self) -> None:
        if self.font_dir.exists():
            for p in self.font_dir.iterdir():
                if p.suffix.lower() in (".ttc", ".ttf", ".otf"):
                    self._path = str(p)
                    return
        for cand in _FONT_CANDIDATES:
            if os.path.exists(cand):
                self._path = cand
                return
        self._path = None

    def get(self, size: int):
        if size in self._cache:
            return self._cache[size]
        if self._path:
            try:
                font = ImageFont.truetype(self._path, size)
            except Exception:
                font = ImageFont.load_default()
        else:
            font = ImageFont.load_default()
        self._cache[size] = font
        return font


# ---------- 颜色 ----------
TEXT = (38, 36, 32)
MUTED = (110, 105, 95)
WHITE = (255, 255, 255)
PANEL = (252, 250, 244)         # 米白卡心,像 TCG 卡面
PANEL_DARK = (244, 240, 230)
DIVIDER = (220, 214, 200)
GOLD = (210, 168, 60)
SHADOW = (0, 0, 0, 70)

TYPE_COLORS = {
    "普通": (168, 168, 120), "格斗": (192, 48, 40),  "飞行": (168, 144, 240),
    "毒":   (160, 64, 160),  "地面": (224, 192, 104), "岩石": (184, 160, 56),
    "虫":   (168, 184, 32),  "幽灵": (112, 88, 152),  "钢":   (170, 175, 200),
    "火":   (240, 128, 48),  "水":   (104, 144, 240), "草":   (120, 200, 80),
    "电":   (245, 200, 50),  "超能": (248, 88, 136),  "超能力": (248, 88, 136),
    "冰":   (152, 216, 216),
    "龙":   (112, 56, 248),  "恶":   (112, 88, 72),   "妖精": (238, 153, 172),
}
ABILITY_FRAME = (118, 92, 200)   # 能力卡用紫色框,区别于宝可梦卡

# 装饰小图标(运行时下载并缓存,与宝可梦图标共用 IconCache)
CANDY_ICON_URL = (
    "https://s1.52poke.com/wiki/thumb/3/39/"
    "Bag_%E6%9C%80%E5%A4%A7%E7%AD%89%E7%BA%A7%E6%8F%90%E5%8D%87_Sprite.png/"
    "32px-Bag_%E6%9C%80%E5%A4%A7%E7%AD%89%E7%BA%A7%E6%8F%90%E5%8D%87_Sprite.png"
)
ABILITY_EXP_ICON_URL = (
    "https://s1.52poke.com/wiki/thumb/9/9a/"
    "Bag_%E8%83%BD%E5%8A%9B%E5%A2%9E%E5%BC%BA_Sprite.png/"
    "32px-Bag_%E8%83%BD%E5%8A%9B%E5%A2%9E%E5%BC%BA_Sprite.png"
)


# ---------- 工具 ----------
@dataclass
class RenderContext:
    fonts: FontPool
    icons: IconCache
    out_dir: Path


def _wrap_text(text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    for raw_line in str(text).split("\n"):
        if not raw_line:
            lines.append("")
            continue
        buf = ""
        for ch in raw_line:
            test = buf + ch
            if font.getbbox(test)[2] > max_width and buf:
                lines.append(buf)
                buf = ch
            else:
                buf = test
        if buf:
            lines.append(buf)
    return lines


def _text_w(font, text: str) -> int:
    b = font.getbbox(text)
    return b[2] - b[0]


def _text_h(font) -> int:
    b = font.getbbox("中Aj")
    return b[3] - b[1] + 4


def _save_png(img: Image.Image, out_dir: Path, key: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / (hashlib.md5(key.encode("utf-8")).hexdigest() + ".png")
    img.save(p, "PNG")
    return p


def _lighten(c: tuple[int, int, int], amt: float = 0.5) -> tuple[int, int, int]:
    return tuple(int(c[i] * (1 - amt) + 255 * amt) for i in range(3))  # type: ignore


def _darken(c: tuple[int, int, int], amt: float = 0.3) -> tuple[int, int, int]:
    return tuple(int(c[i] * (1 - amt)) for i in range(3))  # type: ignore


def _gradient(size: tuple[int, int], top: tuple[int, int, int], bot: tuple[int, int, int]) -> Image.Image:
    """垂直线性渐变。"""
    w, h = size
    img = Image.new("RGB", (1, h), top)
    px = img.load()
    for y in range(h):
        t = y / max(1, h - 1)
        px[0, y] = (
            int(top[0] * (1 - t) + bot[0] * t),
            int(top[1] * (1 - t) + bot[1] * t),
            int(top[2] * (1 - t) + bot[2] * t),
        )
    return img.resize((w, h))


def _paste_icon_scaled(canvas: Image.Image, icon_path: Path | None, box: tuple[int, int, int, int]):
    """像素艺术放大: 不抗锯齿,保留 Pokemon Shuffle 像素感。"""
    x, y, w, h = box
    if icon_path is None or not icon_path.exists():
        return
    try:
        with Image.open(icon_path) as im:
            im = im.convert("RGBA")
            # 计算等比放大尺寸,贴在 box 中心
            iw, ih = im.size
            scale = min(w / iw, h / ih)
            tw, th = int(iw * scale), int(ih * scale)
            im = im.resize((tw, th), Image.NEAREST)
            cx, cy = x + (w - tw) // 2, y + (h - th) // 2
            canvas.paste(im, (cx, cy), im)
    except Exception:
        return


def _rounded_panel(canvas: Image.Image, box, radius: int, fill, border=None, border_w=0):
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=border, width=border_w)


def _drop_shadow(canvas: Image.Image, box, radius: int, blur: int = 6, opacity: int = 80):
    """在指定区域下方画一层柔和阴影。"""
    x0, y0, x1, y1 = box
    pad = blur * 2
    sh = Image.new("RGBA", (x1 - x0 + pad * 2, y1 - y0 + pad * 2), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    sd.rounded_rectangle(
        (pad, pad, x1 - x0 + pad, y1 - y0 + pad),
        radius=radius, fill=(0, 0, 0, opacity),
    )
    sh = sh.filter(ImageFilter.GaussianBlur(blur))
    canvas.paste(sh, (x0 - pad, y0 - pad + 3), sh)


def _draw_text(draw: ImageDraw.ImageDraw, xy, text, font, fill=TEXT, stroke=0, stroke_fill=WHITE):
    draw.text(xy, str(text), font=font, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)


def _pill(draw: ImageDraw.ImageDraw, xy, text, font, fill, text_fill=WHITE, pad_x=12, pad_y=4):
    """色块圆角药丸标签;返回 (x_end, y_end)。"""
    x, y = xy
    tw = _text_w(font, text)
    th = _text_h(font)
    w = tw + pad_x * 2
    h = th + pad_y * 2
    draw.rounded_rectangle((x, y, x + w, y + h), radius=h // 2, fill=fill)
    _draw_text(draw, (x + pad_x, y + pad_y - 1), text, font, fill=text_fill)
    return x + w, y + h


def _stat_pill(
    draw: ImageDraw.ImageDraw, xy, label, value, font_label, font_value, accent,
    canvas: Image.Image | None = None, icon_path: Path | None = None,
):
    """左半淡色 label,右半深色 value 的复合 pill。
    若提供 icon_path,会在 label 左侧贴一个小图标。"""
    x, y = xy
    pad_x = 14
    pad_y = 6
    lw = _text_w(font_label, label)
    vw = _text_w(font_value, value)
    th = max(_text_h(font_label), _text_h(font_value))
    icon_h = th + 2
    icon_w = icon_h if (icon_path and icon_path.exists()) else 0
    icon_gap = 6 if icon_w else 0

    label_block_w = icon_w + icon_gap + lw
    w_total = label_block_w + vw + pad_x * 3
    h = th + pad_y * 2
    # 背景
    draw.rounded_rectangle((x, y, x + w_total, y + h), radius=h // 2, fill=_lighten(accent, 0.78))
    # 右半色块
    right_w = vw + pad_x * 2
    draw.rounded_rectangle(
        (x + w_total - right_w, y, x + w_total, y + h),
        radius=h // 2, fill=accent,
    )
    # 图标
    if icon_w and canvas is not None:
        _paste_icon_scaled(
            canvas, icon_path,
            (x + pad_x, y + (h - icon_h) // 2, icon_w, icon_h),
        )
    # 文本
    _draw_text(draw, (x + pad_x + icon_w + icon_gap, y + pad_y - 1), label, font_label, fill=_darken(accent, 0.45))
    _draw_text(draw, (x + w_total - right_w + pad_x, y + pad_y - 1), value, font_value, fill=WHITE)
    return x + w_total, y + h


def _tag_pill(draw: ImageDraw.ImageDraw, xy, text, font, accent, pad_x=12, pad_y=5):
    """tag 风格小药丸: 浅色底 + 同色描边 + 深色文字。返回 (右 x, 底 y)。"""
    x, y = xy
    tw = _text_w(font, text)
    th = _text_h(font)
    w = tw + pad_x * 2
    h = th + pad_y * 2
    bg = _lighten(accent, 0.78)
    border = accent
    fg = _darken(accent, 0.45)
    draw.rounded_rectangle((x, y, x + w, y + h), radius=h // 2, fill=bg, outline=border, width=1)
    _draw_text(draw, (x + pad_x, y + pad_y - 1), text, font, fill=fg)
    return x + w, y + h


def _draw_tags_wrapped(
    draw: ImageDraw.ImageDraw, origin, items: list[str], font, accent,
    content_w: int, gap: int = 8,
) -> int:
    """把 items 依次画成 tag,自动换行。返回结束的 y(最后一行底部)。"""
    if not items:
        _draw_text(draw, origin, "—", font, fill=MUTED)
        return origin[1] + _text_h(font) + 6
    x, y = origin
    th = _text_h(font)
    h = th + 5 * 2
    for item in items:
        tw = _text_w(font, item)
        w = tw + 12 * 2
        if x != origin[0] and x + w > origin[0] + content_w:
            x = origin[0]
            y += h + gap
        _tag_pill(draw, (x, y), item, font, accent)
        x += w + gap
    return y + h


def _section_header(
    draw: ImageDraw.ImageDraw, xy, text, font, accent, content_w: int,
    canvas: Image.Image | None = None, icon_path: Path | None = None,
):
    """带左侧色条的小节标题,后接细横线。可选 icon 紧贴标题右侧。"""
    x, y = xy
    bar_h = _text_h(font) + 2
    draw.rounded_rectangle((x, y + 2, x + 5, y + bar_h - 2), radius=2, fill=accent)
    _draw_text(draw, (x + 14, y - 2), text, font, fill=_darken(accent, 0.4))
    if icon_path and icon_path.exists() and canvas is not None:
        text_w = _text_w(font, text)
        icon_h = _text_h(font) + 4
        icon_x = x + 14 + text_w + 8
        icon_y = y + (bar_h - icon_h) // 2 - 1
        _paste_icon_scaled(canvas, icon_path, (icon_x, icon_y, icon_h, icon_h))
    th_end = y + bar_h
    line_y = th_end + 2
    draw.line((x, line_y, x + content_w, line_y), fill=DIVIDER, width=1)
    return line_y + 8


def _key_value_row(draw: ImageDraw.ImageDraw, xy, label, value, f_label, f_val, val_x):
    x, y = xy
    _draw_text(draw, (x, y), label, f_label, fill=MUTED)
    _draw_text(draw, (val_x, y), value if value else "—", f_val)


def _join_multi(text: str | None, sep: str = " · ") -> str:
    if not text:
        return "—"
    parts = [p.strip() for p in str(text).split("\n") if p.strip()]
    return sep.join(parts) if parts else "—"


# ---------- 宝可梦卡片 ----------
async def render_pokemon(ctx: RenderContext, entry: Entry, ability_detail: dict | None) -> Path:
    rec = entry.raw
    fonts = ctx.fonts

    f_name = fonts.get(46)
    f_no = fonts.get(20)
    f_badge = fonts.get(18)
    f_section = fonts.get(20)
    f_label = fonts.get(18)
    f_val = fonts.get(20)
    f_small = fonts.get(18)

    width = 760
    pad = 20            # 外框到内 panel 的间距
    frame_w = 16        # 属性色粗边宽度
    inner_pad = 26      # panel 内边距
    art_h = 220         # 顶部图像窗口高度

    type_name = (rec.get("type") or "").split("/")[0].strip()
    accent = TYPE_COLORS.get(type_name, (150, 150, 150))

    # ---- 预算文本 ----
    name = rec.get("name", "—")
    name_en = rec.get("nameEn", "")
    no1 = rec.get("no1", "—")

    base = rec.get("baseAtk", "—") or "—"
    lv10 = rec.get("lv10Atk", "—") or "—"
    lvmx = rec.get("LvMaxAtk", "—") or "—"
    sugar = rec.get("sugar", "—") or "—"

    main_ab = (rec.get("ability") or "").strip()
    raw_else = (rec.get("elseAbility") or "").strip()
    # mega 宝可梦: 数据里用 elseAbility=="mega" 标记;
    # 此类卡片不展示"转换后能力"与"掉落"两块。
    is_mega = raw_else.lower() == "mega"
    else_ab_items = (
        [] if is_mega
        else [p.strip() for p in raw_else.split("\n") if p.strip()]
    )
    drop_text = rec.get("drop", "—") or "—"
    pos_text = _join_multi(rec.get("position"))

    # ---- 估算高度 ----
    panel_x0 = pad
    panel_x1 = width - pad
    panel_w = panel_x1 - panel_x0
    content_x = panel_x0 + inner_pad
    content_w = panel_w - inner_pad * 2

    th_small = _text_h(f_small)
    th_val = _text_h(f_val)
    th_tag = _text_h(f_label) + 5 * 2   # tag pill 整体高
    line_h = th_val + 6

    # 4 个 pill 自动布局所需高度 (2 行)
    pill_block_h = (th_val + 12) * 2 + 12

    # 主能力 tag (1 行)
    main_ab_block_h = th_tag if main_ab else line_h

    # 转换后能力 tags (估算行数)
    def _tags_rows(items, font, content_w, gap=8):
        if not items:
            return 1
        x, rows = 0, 1
        for it in items:
            w = _text_w(font, it) + 12 * 2
            if x != 0 and x + w > content_w:
                rows += 1
                x = 0
            x += w + gap
        return rows
    else_rows = _tags_rows(else_ab_items, f_label, content_w)
    else_block_h = th_tag * else_rows + 8 * (else_rows - 1)

    # 出现位置
    pos_lines = _wrap_text(pos_text, f_small, content_w)
    pos_block_h = max(line_h, th_small * len(pos_lines))

    drop_block_h = line_h

    sec_h = 38   # _section_header 实际占用
    height = (
        pad
        + 80   # 名字 + 编号
        + 14
        + art_h
        + 18
        + sec_h + pill_block_h + 10        # 数据
        + sec_h + main_ab_block_h + 10     # 主能力
        + (0 if is_mega else sec_h + else_block_h + 10)
        + sec_h + pos_block_h + 10         # 出现位置
        + (0 if is_mega else sec_h + drop_block_h + 10)
        + inner_pad
        + pad
    )

    # ---- 画布 + 外框渐变 ----
    img = _gradient((width, height), _lighten(accent, 0.15), _darken(accent, 0.15)).convert("RGBA")
    draw = ImageDraw.Draw(img)

    # 中央 panel 阴影 + 米白圆角面板
    panel_box = (panel_x0, pad, panel_x1, height - pad)
    _drop_shadow(img, panel_box, radius=24, blur=10, opacity=110)
    _rounded_panel(img, panel_box, radius=24, fill=PANEL)
    # 内边一道细金描边
    draw.rounded_rectangle(panel_box, radius=24, outline=_lighten(accent, 0.3), width=2)

    y = pad + inner_pad

    # ---- 头部: 名字 + No + 英文名 ----
    _draw_text(draw, (content_x, y), name, f_name, fill=TEXT)
    name_h = _text_h(f_name)
    no_text = f"No.{no1}  ·  {name_en}"
    _draw_text(draw, (content_x, y + name_h + 2), no_text, f_no, fill=MUTED)

    # 右上角仅保留属性 badge
    type_badge = rec.get("type", "—") or "—"
    bb = f_badge.getbbox(type_badge)
    bw = bb[2] - bb[0] + 22
    bh = bb[3] - bb[1] + 12
    bx = panel_x1 - inner_pad - bw
    by = y + 2
    draw.rounded_rectangle((bx, by, bx + bw, by + bh), radius=bh // 2, fill=accent)
    _draw_text(draw, (bx + 11, by + 5), type_badge, f_badge, fill=WHITE)

    y += 80

    # ---- 图像窗口 (TCG art window) ----
    art_box = (content_x, y, content_x + content_w, y + art_h)
    # 渐变底
    art_bg = _gradient(
        (content_w, art_h),
        _lighten(accent, 0.62),
        _lighten(accent, 0.3),
    )
    # 圆角遮罩
    mask = Image.new("L", (content_w, art_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, content_w, art_h), radius=16, fill=255)
    img.paste(art_bg, (content_x, y), mask)
    # 边框
    draw.rounded_rectangle(art_box, radius=16, outline=_darken(accent, 0.2), width=3)
    # 图标(像素艺术放大)
    icon_url = rec.get("imgUrl", "")
    icon_path = await ctx.icons.get(icon_url) if icon_url else None
    _paste_icon_scaled(img, icon_path, (content_x + 20, y + 12, content_w - 40, art_h - 24))

    y += art_h + 18

    # ---- 数值 pill 区 ----
    y = _section_header(draw, (content_x, y), "数据", f_section, accent, content_w)
    candy_icon = await ctx.icons.get(CANDY_ICON_URL)
    pill_data = [
        ("基础攻击", str(base), None),
        ("Lv.10 攻击", str(lv10), None),
        ("满级攻击", str(lvmx), None),
        ("糖果", str(sugar), candy_icon),
    ]
    px, py = content_x, y
    row_h = th_val + 14
    for label, value, icon in pill_data:
        pad_x = 14
        lw = _text_w(f_label, label)
        vw = _text_w(f_val, value)
        icon_w = (th_val + 2 + 6) if (icon and icon.exists()) else 0
        pill_w = icon_w + lw + vw + pad_x * 3
        if px + pill_w > content_x + content_w:
            px = content_x
            py += row_h + 8
        _stat_pill(draw, (px, py), label, value, f_label, f_val, accent,
                   canvas=img, icon_path=icon)
        px += pill_w + 10
    y = py + row_h + 14

    # ---- 主能力 (tag) ----
    y = _section_header(draw, (content_x, y), "主能力", f_section, accent, content_w)
    y = _draw_tags_wrapped(draw, (content_x, y), [main_ab] if main_ab else [], f_label, accent, content_w)
    y += 10

    # ---- 转换后能力 (多个 tag) - mega 不展示 ----
    if not is_mega:
        y = _section_header(draw, (content_x, y), "转换后能力", f_section, accent, content_w)
        y = _draw_tags_wrapped(draw, (content_x, y), else_ab_items, f_label, accent, content_w)
        y += 10

    # ---- 出现位置 ----
    y = _section_header(draw, (content_x, y), "出现位置", f_section, accent, content_w)
    for ln in pos_lines or ["—"]:
        _draw_text(draw, (content_x, y), ln, f_small, fill=TEXT)
        y += th_small
    y += 6

    # ---- 掉落 - mega 不展示 ----
    if not is_mega:
        y = _section_header(draw, (content_x, y), "掉落", f_section, accent, content_w)
        _draw_text(draw, (content_x, y), drop_text, f_val, fill=TEXT)
        y += line_h

    key = f"pokemon::v5::{rec.get('no1')}::{rec.get('name')}::{rec.get('nameEn')}"
    return _save_png(img.convert("RGB"), ctx.out_dir, key)


# ---------- 能力卡片 ----------
async def render_ability(ctx: RenderContext, entry: Entry) -> Path:
    rec = entry.raw
    fonts = ctx.fonts
    accent = ABILITY_FRAME

    f_name = fonts.get(42)
    f_sub = fonts.get(22)
    f_section = fonts.get(20)
    f_label = fonts.get(18)
    f_val = fonts.get(20)
    f_small = fonts.get(18)

    width = 760
    pad = 20
    inner_pad = 26
    icon_size = 50    # 拥有宝可梦小图

    panel_x0, panel_x1 = pad, width - pad
    panel_w = panel_x1 - panel_x0
    content_x = panel_x0 + inner_pad
    content_w = panel_w - inner_pad * 2

    name_cn = rec.get("ability", "") or ""
    name_en = rec.get("abilityEn", "") or ""

    fdl_raw = (rec.get("fdl", "") or "")
    fdl_parts = [p.strip() for p in fdl_raw.split("\n\n") if p.strip()]
    fdl_pretty = "  /  ".join(fdl_parts) if fdl_parts else "—"

    exp_raw = (rec.get("exp", "") or "")
    exp_parts = [p.strip() for p in exp_raw.split("\n\n") if p.strip()]
    total = sum(int(s) for s in exp_parts if s.isdigit())
    exp_pretty = " / ".join(exp_parts) + (f"    总共 {total}" if total else "")

    # 升级效果合并为一行(用 · 分隔);超长会由 _wrap_text 自动换行兜底
    effect_parts = [p.strip() for p in (rec.get("effect") or "").split("\n\n") if p.strip()]
    effect_one_line = "   ·   ".join(effect_parts) if effect_parts else "—"

    eff = rec.get("abilityEffect") or {}
    desc_text = eff.get("description") or "—"
    real_effect = eff.get("effect") or "—"

    init_pms = (rec.get("abilityPm") or {}).get("init") or []
    change_pms = (rec.get("abilityPm") or {}).get("change") or []

    th_small = _text_h(f_small)
    th_val = _text_h(f_val)
    line_h = th_val + 6

    desc_lines = _wrap_text(desc_text, f_small, content_w)
    real_lines = _wrap_text(real_effect, f_small, content_w)
    effect_lines_wrapped = _wrap_text(effect_one_line, f_small, content_w)

    # 拥有宝可梦行高(自动换行)
    def row_count(pms, gap=8):
        if not pms:
            return 1
        per_row = max(1, (content_w + gap) // (icon_size + gap))
        return (len(pms) + per_row - 1) // per_row

    init_rows = row_count(init_pms) if init_pms else 0
    change_rows = row_count(change_pms) if change_pms else 0
    pm_row_h = icon_size + 10
    empty_row_h = th_small + 6
    init_h = pm_row_h * init_rows if init_rows else empty_row_h
    change_h = pm_row_h * change_rows if change_rows else empty_row_h

    sec_h = 38   # _section_header 实际占用 (色条+文本+横线+8 padding)
    height = (
        pad
        + 70                                                # 名字
        + 18
        + sec_h + line_h + 6                                # 发动率
        + sec_h + line_h + 6                                # 经验
        + sec_h + (th_small * max(1, len(effect_lines_wrapped))) + 8
        + sec_h + (th_small * max(1, len(desc_lines))) + 8
        + sec_h + (th_small * max(1, len(real_lines))) + 8
        + sec_h + init_h + 8                                # 初期
        + sec_h + change_h + 8                              # 转换后
        + inner_pad + pad
    )

    img = _gradient((width, height), _lighten(accent, 0.18), _darken(accent, 0.12)).convert("RGBA")
    draw = ImageDraw.Draw(img)
    panel_box = (panel_x0, pad, panel_x1, height - pad)
    _drop_shadow(img, panel_box, radius=24, blur=10, opacity=110)
    _rounded_panel(img, panel_box, radius=24, fill=PANEL)
    draw.rounded_rectangle(panel_box, radius=24, outline=_lighten(accent, 0.25), width=2)

    y = pad + inner_pad

    # 标题
    _draw_text(draw, (content_x, y), name_cn, f_name, fill=TEXT)
    nw = _text_w(f_name, name_cn)
    if name_en:
        _draw_text(draw, (content_x + nw + 14, y + 14), f"({name_en})", f_sub, fill=MUTED)
    # 右上角 "能力" 标签
    badge = "能力卡"
    bb = f_sub.getbbox(badge)
    bw = bb[2] - bb[0] + 22
    bh = bb[3] - bb[1] + 12
    bx = panel_x1 - inner_pad - bw
    by = y + 6
    draw.rounded_rectangle((bx, by, bx + bw, by + bh), radius=bh // 2, fill=accent)
    _draw_text(draw, (bx + 11, by + 5), badge, f_sub, fill=WHITE)

    y += 70

    # 发动率
    y = _section_header(draw, (content_x, y), "发动率 (三 / 四 / 五消)", f_section, accent, content_w)
    _draw_text(draw, (content_x, y), fdl_pretty, f_val, fill=TEXT)
    y += line_h + 6

    # 能力增强经验 (section header 右侧带 sprite 图标)
    exp_icon = await ctx.icons.get(ABILITY_EXP_ICON_URL)
    y = _section_header(
        draw, (content_x, y), "能力增强经验", f_section, accent, content_w,
        canvas=img, icon_path=exp_icon,
    )
    _draw_text(draw, (content_x, y), exp_pretty or "—", f_val, fill=TEXT)
    y += line_h + 6

    # 升级效果(单行展示,溢出宽度才自动换行)
    y = _section_header(draw, (content_x, y), "升级效果", f_section, accent, content_w)
    for ln in effect_lines_wrapped:
        _draw_text(draw, (content_x, y), ln, f_small, fill=TEXT)
        y += th_small
    y += 8

    # 能力描述
    y = _section_header(draw, (content_x, y), "能力描述", f_section, accent, content_w)
    for ln in desc_lines:
        _draw_text(draw, (content_x, y), ln, f_small, fill=TEXT)
        y += th_small
    y += 8

    # 能力效果
    y = _section_header(draw, (content_x, y), "能力效果", f_section, accent, content_w)
    for ln in real_lines:
        _draw_text(draw, (content_x, y), ln, f_small, fill=TEXT)
        y += th_small
    y += 8

    # 初期拥有的宝可梦
    y = _section_header(draw, (content_x, y), "初期拥有的宝可梦", f_section, accent, content_w)
    await _paste_pm_row(img, ctx, init_pms, (content_x, y), icon_size, content_w)
    y += init_h + 8

    # 转换后拥有的宝可梦
    y = _section_header(draw, (content_x, y), "转换后拥有的宝可梦", f_section, accent, content_w)
    await _paste_pm_row(img, ctx, change_pms, (content_x, y), icon_size, content_w)

    key = f"ability::v4::{name_cn}::{name_en}"
    return _save_png(img.convert("RGB"), ctx.out_dir, key)


async def _paste_pm_row(
    canvas: Image.Image,
    ctx: RenderContext,
    pms: Iterable[dict],
    origin: tuple[int, int],
    icon_size: int,
    content_w: int,
    gap: int = 8,
):
    pms = list(pms)
    if not pms:
        _draw_text(ImageDraw.Draw(canvas), origin, "暂无", ctx.fonts.get(18), fill=MUTED)
        return
    urls = [p.get("src", "") for p in pms]
    paths = await ctx.icons.get_many(urls)
    x, y = origin
    x_max = origin[0] + content_w
    for p in paths:
        # 圆角白底 + 阴影
        box = (x, y, x + icon_size, y + icon_size)
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle(box, radius=10, fill=WHITE, outline=(220, 215, 200), width=1)
        _paste_icon_scaled(canvas, p, (x + 4, y + 4, icon_size - 8, icon_size - 8))
        x += icon_size + gap
        if x + icon_size > x_max:
            x = origin[0]
            y += icon_size + gap
