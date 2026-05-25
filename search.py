"""数据加载与查询匹配模块。

提供四级匹配:
  1) exact   - 归一化后完全相等
  2) contain - 候选名包含用户输入(子串)
  3) fuzzy   - rapidfuzz 相似度 >= 阈值
  4) none    - 都没命中

宝可梦与能力同池搜索,但结果分两段返回。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from rapidfuzz import fuzz


# ---------- 归一化 ----------
# 注意: + 不能剥离,游戏里"能力+"表示升级版,与原能力是不同条目(如 "连击" vs "连击+")。
_NON_ALNUM_RE = re.compile(r"[\s　\-_\.()（）\[\]【】,，。:：;；'\"]+")


def normalize(text: str) -> str:
    """去掉空白/常见标点并转小写,用于匹配比较。保留 +/!/? 等承载语义的字符。"""
    if not text:
        return ""
    return _NON_ALNUM_RE.sub("", text).lower()


# ---------- 实体 ----------
@dataclass
class Entry:
    kind: str          # "pokemon" 或 "ability"
    display: str       # 展示名(原始,带空格/形态)
    raw: dict          # 原始 JSON 记录
    keys: list[str] = field(default_factory=list)   # 归一化后的可搜索键(中文名+英文名)


@dataclass
class MatchResult:
    level: str                    # "exact" | "contain" | "fuzzy" | "none"
    pokemons: list[Entry] = field(default_factory=list)
    abilities: list[Entry] = field(default_factory=list)
    # 当 level == "exact" 时,这里放"其它包含关键词的同类条目"(已排除精确命中本身),
    # 用于在卡片后追加"搜索到相关..."列表。
    related_pokemons: list[Entry] = field(default_factory=list)
    related_abilities: list[Entry] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.pokemons) + len(self.abilities)


# ---------- 数据集 ----------
class Dataset:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.pokemons: list[Entry] = []
        self.abilities: list[Entry] = []
        self._ability_by_name: dict[str, dict] = {}
        self.load()

    def load(self) -> None:
        with open(self.data_dir / "pokemon.json", "r", encoding="utf-8") as f:
            for rec in json.load(f):
                keys = [normalize(rec.get("name", "")), normalize(rec.get("nameEn", ""))]
                keys = [k for k in keys if k]
                self.pokemons.append(Entry("pokemon", rec.get("name", ""), rec, keys))

        with open(self.data_dir / "abilities.json", "r", encoding="utf-8") as f:
            for rec in json.load(f):
                keys = [normalize(rec.get("ability", "")), normalize(rec.get("abilityEn", ""))]
                keys = [k for k in keys if k]
                self.abilities.append(Entry("ability", rec.get("ability", ""), rec, keys))
                # 给宝可梦卡片反查能力详情用
                if rec.get("ability"):
                    self._ability_by_name[normalize(rec["ability"])] = rec

    def ability_of(self, name: str) -> dict | None:
        """根据能力中文名(原始)取详情,用于宝可梦卡片附带显示。"""
        return self._ability_by_name.get(normalize(name))

    # ---------- 按编号(no1) ----------
    def search_by_no1(self, query: str, max_items: int = 30) -> "MatchResult":
        """支持 "1" / "001" / "025" 等输入,3 位补零后比对 no1 字段。
        同一编号可能对应多个形态 → 返回多条时走 contain 列表分支。"""
        q = (query or "").strip().lstrip("0") or "0"
        if not q.isdigit():
            return MatchResult(level="none")
        padded = q.zfill(3)
        hits = [e for e in self.pokemons if (e.raw.get("no1") or "") == padded]
        if not hits:
            return MatchResult(level="none")
        if len(hits) == 1:
            return MatchResult(level="exact", pokemons=hits)
        return MatchResult(level="contain", pokemons=hits[:max_items])

    # ---------- 匹配 ----------
    def search(self, query: str, fuzzy_threshold: int = 50, max_items: int = 15) -> MatchResult:
        nq = normalize(query)
        if not nq:
            return MatchResult(level="none")

        # 1) exact
        exact_p = [e for e in self.pokemons if nq in e.keys]
        exact_a = [e for e in self.abilities if nq in e.keys]
        if len(exact_p) + len(exact_a) == 1:
            # 计算"其它包含此关键词的条目",排除精确命中本身,作为附加列表返回
            hit = (exact_p or exact_a)[0]
            related_p = [
                e for e in self.pokemons
                if e is not hit and any(nq in k for k in e.keys)
            ]
            related_a = [
                e for e in self.abilities
                if e is not hit and any(nq in k for k in e.keys)
            ]
            return MatchResult(
                level="exact",
                pokemons=exact_p,
                abilities=exact_a,
                related_pokemons=_dedup_by_display(related_p)[:max_items],
                related_abilities=_dedup_by_display(related_a)[:max_items],
            )

        # 多个精确同名(归一化后撞名,如英文名 "Pikachu" 多形态) → 走 contain 列表分支
        if len(exact_p) + len(exact_a) > 1:
            return MatchResult(level="contain", pokemons=exact_p[:max_items], abilities=exact_a[:max_items])

        # 2) contain
        contain_p = [e for e in self.pokemons if any(nq in k for k in e.keys)]
        contain_a = [e for e in self.abilities if any(nq in k for k in e.keys)]
        if contain_p or contain_a:
            return MatchResult(
                level="contain",
                pokemons=_dedup_by_display(contain_p)[:max_items],
                abilities=_dedup_by_display(contain_a)[:max_items],
            )

        # 3) fuzzy
        fuzzy_p = _fuzzy_top(self.pokemons, nq, fuzzy_threshold, max_items)
        fuzzy_a = _fuzzy_top(self.abilities, nq, fuzzy_threshold, max_items)
        if fuzzy_p or fuzzy_a:
            return MatchResult(level="fuzzy", pokemons=fuzzy_p, abilities=fuzzy_a)

        return MatchResult(level="none")


# ---------- helpers ----------
def _dedup_by_display(entries: Iterable[Entry]) -> list[Entry]:
    seen: set[str] = set()
    out: list[Entry] = []
    for e in entries:
        if e.display in seen:
            continue
        seen.add(e.display)
        out.append(e)
    return out


def _fuzzy_top(entries: list[Entry], nq: str, threshold: int, limit: int) -> list[Entry]:
    scored: list[tuple[int, Entry]] = []
    seen_display: set[str] = set()
    for e in entries:
        if not e.keys:
            continue
        score = max(fuzz.ratio(nq, k) for k in e.keys)
        if score >= threshold and e.display not in seen_display:
            seen_display.add(e.display)
            scored.append((score, e))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [e for _, e in scored[:limit]]
