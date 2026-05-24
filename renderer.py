"""Pillow 卡片渲染器。

两种卡片:
  - 宝可梦信息卡 (render_pokemon)
  - 能力详情卡   (render_ability)

字体加载顺序:
  1) 插件内 fonts/ 目录下的字体 (NotoSansCJK-Regular.otf / SourceHanSansSC-Regular.otf 等)
  2) 系统常见中文字体 (msyh.ttc / simhei.ttf / PingFang.ttc / Noto Sans CJK)
  3) Pillow 默认 (无中文,会乱码,仅兜底)
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont

from .icon_cache import IconCache
from .search import Entry


# ---------- 字体 ----------
_FONT_CANDIDATES = [
    # 插件 fonts 目录会在运行时被前置注入
    "NotoSansCJK-Regular.ttc",
    "NotoSansCJKsc-Regular.otf",
    "SourceHanSansSC-Regular.otf",
    # Windows
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
    # Linux
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
        # 优先扫插件内 fonts/
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

    def get(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
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


# ---------- 颜色 / 主题 ----------
BG = (250, 251, 253)
CARD_BG = (255, 255, 255)
TEXT = (33, 37, 41)
MUTED = (108, 117, 125)
ACCENT = (255, 158, 27)   # 宝可梦能力增强经验里的金色圆点感
DIVIDER = (230, 232, 236)

TYPE_COLORS = {
    "普通": (168, 168, 120), "格斗": (192, 48, 40),  "飞行": (168, 144, 240),
    "毒":   (160, 64, 160),  "地面": (224, 192, 104), "岩石": (184, 160, 56),
    "虫":   (168, 184, 32),  "幽灵": (112, 88, 152),  "钢":   (184, 184, 208),
    "火":   (240, 128, 48),  "水":   (104, 144, 240), "草":   (120, 200, 80),
    "电":   (248, 208, 48),  "超能": (248, 88, 136),  "冰":   (152, 216, 216),
    "龙":   (112, 56, 248),  "恶":   (112, 88, 72),   "妖精": (238, 153, 172),
}


# ---------- 渲染器 ----------
@dataclass
class RenderContext:
    fonts: FontPool
    icons: IconCache
    out_dir: Path


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """按字符宽度软换行,中文按字逐个,英文尽量按词。"""
    lines: list[str] = []
    for raw_line in text.split("\n"):
        if not raw_line:
            lines.append("")
            continue
        buf = ""
        for ch in raw_line:
            test = buf + ch
            w = font.getbbox(test)[2]
            if w > max_width and buf:
                lines.append(buf)
                buf = ch
            else:
                buf = test
        if buf:
            lines.append(buf)
    return lines


def _draw_text(draw: ImageDraw.ImageDraw, xy, text, font, fill=TEXT):
    draw.text(xy, text, font=font, fill=fill)


def _text_h(font: ImageFont.FreeTypeFont) -> int:
    bbox = font.getbbox("中Aj")
    return bbox[3] - bbox[1] + 4


def _save_png(img: Image.Image, out_dir: Path, key: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    name = hashlib.md5(key.encode("utf-8")).hexdigest() + ".png"
    path = out_dir / name
    img.save(path, "PNG")
    return path


def _paste_icon(canvas: Image.Image, icon_path: Path | None, xy: tuple[int, int], size: int):
    if icon_path is None or not icon_path.exists():
        return
    try:
        with Image.open(icon_path) as im:
            im = im.convert("RGBA")
            im.thumbnail((size, size), Image.LANCZOS)
            canvas.paste(im, xy, im)
    except Exception:
        return


# ---------- 宝可梦卡片 ----------
def _join_multi(text: str | None, sep: str = " / ") -> str:
    """数据里用 \n 分隔的多值字段(如 elseAbility / position) → 单行展示,
    渲染时再根据可用宽度自动换行。"""
    if not text:
        return "—"
    parts = [p.strip() for p in text.split("\n") if p.strip()]
    return sep.join(parts) if parts else "—"


async def render_pokemon(ctx: RenderContext, entry: Entry, ability_detail: dict | None) -> Path:
    rec = entry.raw
    fonts = ctx.fonts
    f_title = fonts.get(34)
    f_sub = fonts.get(18)
    f_label = fonts.get(20)
    f_val = fonts.get(20)
    f_small = fonts.get(18)

    width = 720
    pad = 24
    icon_size = 96
    content_w = width - pad * 2

    # 两栏布局:左栏紧凑数值,右栏长文本字段
    base = rec.get("baseAtk", "—") or "—"
    lv10 = rec.get("lv10Atk", "—") or "—"
    lvmx = rec.get("LvMaxAtk", "—") or "—"
    atk_combined = f"{base} → {lv10} → {lvmx}"

    left_fields: list[tuple[str, str]] = [
        ("属性", rec.get("type", "—") or "—"),
        ("糖果", rec.get("sugar", "—") or "—"),
        ("攻击力", atk_combined),
        ("主能力", rec.get("ability", "—") or "—"),
    ]
    right_fields: list[tuple[str, str]] = [
        ("转换后能力", _join_multi(rec.get("elseAbility"))),
        ("掉落", rec.get("drop", "—") or "—"),
    ]
    pos_text = _join_multi(rec.get("position"))

    # ---- 列宽 ----
    left_label_x = pad + 8
    left_val_x = pad + 100
    left_val_w = 230   # 左值可用宽度
    right_label_x = pad + 360
    right_val_x = pad + 480
    right_val_w = width - pad - right_val_x - 8

    line_h = _text_h(f_val) + 6
    small_lh = _text_h(f_small)

    # 预先 wrap 左右栏每个字段,得到每行行数,栏总高 = sum(rows)*small_lh
    def wrap_rows(fields: list[tuple[str, str]], val_w: int) -> list[list[str]]:
        return [_wrap_text(v, f_small, val_w) for _, v in fields]

    left_wrapped = wrap_rows(left_fields, left_val_w)
    right_wrapped = wrap_rows(right_fields, right_val_w)

    def block_h(wrapped: list[list[str]]) -> int:
        h = 0
        for lines in wrapped:
            h += max(1, len(lines)) * small_lh + 8
        return h

    left_h = block_h(left_wrapped)
    right_h = block_h(right_wrapped)
    body_h = max(left_h, right_h) + 8

    # 出现位置(全宽行)
    pos_label_w = f_label.getbbox("出现位置")[2] - f_label.getbbox("出现位置")[0]
    pos_val_x = left_label_x + pos_label_w + 24
    pos_val_w = width - pad - pos_val_x - 8
    pos_lines = _wrap_text(pos_text, f_small, pos_val_w)
    pos_h = max(line_h, small_lh * len(pos_lines)) + 12

    # 能力简介
    ability_desc_lines: list[str] = []
    if ability_detail:
        desc = (ability_detail.get("abilityEffect", {}) or {}).get("effect", "") or ""
        if desc:
            ability_desc_lines = _wrap_text(f"【{rec.get('ability','')}】 {desc}", f_small, content_w)
    ability_block_h = small_lh * len(ability_desc_lines) + (16 if ability_desc_lines else 0)

    header_h = max(icon_size, 80) + pad
    height = pad + header_h + body_h + 18 + pos_h + ability_block_h + pad

    # ---- 画布 ----
    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((12, 12, width - 12, height - 12), radius=16, fill=CARD_BG)

    # ---- 头部 ----
    icon_url = rec.get("imgUrl", "")
    icon_path = await ctx.icons.get(icon_url) if icon_url else None
    _paste_icon(img, icon_path, (pad + 4, pad + 4), icon_size)

    name_x = pad + icon_size + 20
    _draw_text(draw, (name_x, pad), rec.get("name", "—"), f_title)
    _draw_text(draw, (name_x, pad + 42), f"No.{rec.get('no1', '—')}  {rec.get('nameEn','')}", f_sub, fill=MUTED)

    type_name = (rec.get("type") or "").split("/")[0].strip()
    color = TYPE_COLORS.get(type_name, (120, 120, 120))
    badge_text = rec.get("type", "—") or "—"
    bb = f_sub.getbbox(badge_text)
    bw, bh = bb[2] - bb[0] + 20, bb[3] - bb[1] + 10
    bx, by = name_x, pad + 72
    draw.rounded_rectangle((bx, by, bx + bw, by + bh), radius=10, fill=color)
    _draw_text(draw, (bx + 10, by + 4), badge_text, f_sub, fill=(255, 255, 255))

    y = pad + header_h + 4
    draw.line((pad, y, width - pad, y), fill=DIVIDER, width=1)
    y += 10

    # ---- 左/右两栏 ----
    def draw_column(fields, wrapped, label_x, val_x, y_start):
        cy = y_start
        for (label, _), lines in zip(fields, wrapped):
            _draw_text(draw, (label_x, cy), label, f_label, fill=MUTED)
            ly = cy
            for ln in lines or ["—"]:
                _draw_text(draw, (val_x, ly), ln, f_val if len(lines) == 1 else f_small)
                ly += small_lh if len(lines) > 1 else line_h
            cy += max(line_h, small_lh * max(1, len(lines))) + 4

    draw_column(left_fields, left_wrapped, left_label_x, left_val_x, y)
    draw_column(right_fields, right_wrapped, right_label_x, right_val_x, y)
    y += body_h

    # ---- 分隔 + 出现位置 (全宽) ----
    draw.line((pad, y, width - pad, y), fill=DIVIDER, width=1)
    y += 10
    _draw_text(draw, (left_label_x, y), "出现位置", f_label, fill=MUTED)
    ly = y
    for ln in pos_lines or ["—"]:
        _draw_text(draw, (pos_val_x, ly), ln, f_small)
        ly += small_lh
    y = max(ly, y + line_h) + 6

    # ---- 能力简介 ----
    if ability_desc_lines:
        draw.line((pad, y, width - pad, y), fill=DIVIDER, width=1)
        y += 8
        for ln in ability_desc_lines:
            _draw_text(draw, (left_label_x, y), ln, f_small, fill=TEXT)
            y += small_lh

    key = f"pokemon::{rec.get('no1')}::{rec.get('name')}::{rec.get('nameEn')}::v2"
    return _save_png(img, ctx.out_dir, key)


# ---------- 能力卡片 ----------
async def render_ability(ctx: RenderContext, entry: Entry) -> Path:
    rec = entry.raw
    fonts = ctx.fonts
    f_title = fonts.get(34)
    f_sub = fonts.get(20)
    f_label = fonts.get(20)
    f_val = fonts.get(20)
    f_small = fonts.get(18)

    width = 720
    pad = 24
    content_w = width - pad * 2
    line_h = _text_h(f_val) + 6

    name_cn = rec.get("ability", "") or ""
    name_en = rec.get("abilityEn", "") or ""

    fdl = (rec.get("fdl", "") or "").replace("\n\n", "  /  ").replace("\n", "  /  ")
    effect_lines = (rec.get("effect", "") or "").split("\n\n")
    exp_text = (rec.get("exp", "") or "").replace("\n\n", " / ").replace("\n", " / ")

    eff = rec.get("abilityEffect") or {}
    desc_text = eff.get("description") or "—"
    real_effect = eff.get("effect") or "—"

    init_pms = (rec.get("abilityPm") or {}).get("init") or []
    change_pms = (rec.get("abilityPm") or {}).get("change") or []

    # 预排版,估高度
    desc_lines = _wrap_text(desc_text, f_small, content_w - 200)
    effect_lines_wrapped: list[str] = []
    for ln in effect_lines:
        for w in _wrap_text(ln, f_small, content_w - 200):
            effect_lines_wrapped.append(w)
    real_lines = _wrap_text(real_effect, f_small, content_w - 200)

    # 高度估算
    h = pad + 60  # title
    h += line_h * 2  # fdl + exp
    h += line_h + _text_h(f_small) * len(effect_lines_wrapped) + 6
    h += line_h + _text_h(f_small) * len(desc_lines) + 6
    h += line_h + _text_h(f_small) * len(real_lines) + 6
    icon_row_h = 56
    h += line_h + icon_row_h + 8
    h += line_h + icon_row_h + 8
    h += pad

    img = Image.new("RGB", (width, h), BG)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((12, 12, width - 12, h - 12), radius=16, fill=CARD_BG)

    # 标题
    _draw_text(draw, (pad, pad), name_cn, f_title)
    if name_en:
        _draw_text(draw, (pad + f_title.getbbox(name_cn)[2] + 12, pad + 12), f"({name_en})", f_sub, fill=MUTED)

    y = pad + 60
    draw.line((pad, y, width - pad, y), fill=DIVIDER, width=1)
    y += 10

    label_x = pad + 8
    val_x = pad + 220   # 加宽标签栏,避免长标签(如"发动率(三/四/五消)")与值重叠

    def section_label(text: str, ycur: int) -> int:
        _draw_text(draw, (label_x, ycur), text, f_label, fill=MUTED)
        return ycur

    # 发动率
    section_label("发动率(三/四/五消)", y)
    _draw_text(draw, (val_x, y), fdl or "—", f_val)
    y += line_h

    # 能力增强经验
    section_label("能力增强经验", y)
    total = sum(int(s.strip()) for s in exp_text.replace("/", " ").split() if s.strip().isdigit())
    exp_show = exp_text + (f"   (总共: {total})" if total else "")
    _draw_text(draw, (val_x, y), exp_show or "—", f_val)
    y += line_h

    # 升级效果(多行)
    section_label("升级效果", y)
    cy = y
    for ln in effect_lines_wrapped:
        _draw_text(draw, (val_x, cy), ln, f_small)
        cy += _text_h(f_small)
    y = max(cy, y + line_h) + 6

    # 能力描述
    section_label("能力描述", y)
    cy = y
    for ln in desc_lines:
        _draw_text(draw, (val_x, cy), ln, f_small)
        cy += _text_h(f_small)
    y = max(cy, y + line_h) + 6

    # 能力效果
    section_label("能力效果", y)
    cy = y
    for ln in real_lines:
        _draw_text(draw, (val_x, cy), ln, f_small)
        cy += _text_h(f_small)
    y = max(cy, y + line_h) + 8

    draw.line((pad, y, width - pad, y), fill=DIVIDER, width=1)
    y += 10

    # 初期拥有的宝可梦
    _draw_text(draw, (label_x, y), "初期拥有的宝可梦", f_label, fill=MUTED)
    y2 = y + line_h - 2
    await _paste_pm_row(img, ctx, init_pms, (label_x, y2), icon_row_h)
    y = y2 + icon_row_h + 8

    # 转换后拥有的宝可梦
    _draw_text(draw, (label_x, y), "转换后拥有的宝可梦", f_label, fill=MUTED)
    y2 = y + line_h - 2
    await _paste_pm_row(img, ctx, change_pms, (label_x, y2), icon_row_h)

    key = f"ability::{name_cn}::{name_en}"
    return _save_png(img, ctx.out_dir, key)


async def _paste_pm_row(
    canvas: Image.Image,
    ctx: RenderContext,
    pms: Iterable[dict],
    origin: tuple[int, int],
    icon_size: int,
):
    """把一行宝可梦小图横向排列。"""
    pms = list(pms)
    if not pms:
        fonts = ctx.fonts
        f = fonts.get(18)
        _draw_text(ImageDraw.Draw(canvas), origin, "暂无", f, fill=MUTED)
        return
    urls = [p.get("src", "") for p in pms]
    paths = await ctx.icons.get_many(urls)
    x, y = origin
    gap = 6
    for p in paths:
        _paste_icon(canvas, p, (x, y), icon_size)
        x += icon_size + gap
        if x + icon_size > canvas.width - 24:
            x = origin[0]
            y += icon_size + gap
