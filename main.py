"""AstrBot 插件: 宝可梦消消乐(Pokemon Shuffle) 数据查询。

用法:
  /皮卡丘            → 渲染皮卡丘卡片
  /pikachu           → 同上,无视大小写与空格
  /超级力量          → 渲染能力卡片
  /mega boost        → 同上
  /妙蛙              → 命中多个,返回"搜索到相关宝可梦/能力"列表
  /皮卡秋            → 模糊匹配,返回"您要查询的是否是: xxx"
"""

from __future__ import annotations

from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .icon_cache import IconCache
from .renderer import FontPool, RenderContext, render_ability, render_pokemon
from .search import Dataset, Entry, MatchResult


PLUGIN_DIR = Path(__file__).parent


@register(
    "astrbot_plugin_pokemon_shuffle",
    "githubcjx",
    "宝可梦消消乐(Pokemon Shuffle)数据查询插件",
    "0.1.0",
)
class PokemonShufflePlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config or {}
        self.prefix: str = self.config.get("command_prefix", "/")
        self.fuzzy_threshold: int = int(self.config.get("fuzzy_threshold", 50))
        self.max_list_items: int = int(self.config.get("max_list_items", 15))
        # 群聊白名单:字符串列表;空列表 = 所有群
        self.enabled_groups: set[str] = {str(g).strip() for g in (self.config.get("enabled_groups") or []) if str(g).strip()}
        self.respond_in_private: bool = bool(self.config.get("respond_in_private", True))

        self.dataset = Dataset(PLUGIN_DIR / "data")
        self.icons = IconCache(PLUGIN_DIR / "cache" / "icons")
        self.fonts = FontPool(PLUGIN_DIR / "fonts")
        self.render_ctx = RenderContext(
            fonts=self.fonts,
            icons=self.icons,
            out_dir=PLUGIN_DIR / "cache" / "cards",
        )
        logger.info(
            "[pokemon-shuffle] loaded %d pokemons, %d abilities, prefix=%r, "
            "enabled_groups=%s, respond_in_private=%s",
            len(self.dataset.pokemons),
            len(self.dataset.abilities),
            self.prefix,
            "ALL" if not self.enabled_groups else sorted(self.enabled_groups),
            self.respond_in_private,
        )

    # ---------- 作用域过滤 ----------
    def _is_scope_allowed(self, event: AstrMessageEvent) -> bool:
        """根据白名单 + 私聊开关判断是否处理本条消息。"""
        # 私聊
        try:
            is_private = bool(event.is_private_chat())
        except Exception:
            is_private = False
        if is_private:
            return self.respond_in_private
        # 群聊
        try:
            gid = str(event.get_group_id() or "")
        except Exception:
            gid = ""
        if not self.enabled_groups:
            return True   # 未配置 → 所有群都生效
        return gid in self.enabled_groups

    # 监听所有消息,自行解析前缀(因为指令是动态名称,@filter.command 不适用)
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        if not self._is_scope_allowed(event):
            return

        raw = (event.message_str or "").strip()
        if not raw:
            return
        if self.prefix and not raw.startswith(self.prefix):
            return
        query = raw[len(self.prefix):].strip()
        if not query:
            return

        # 避免吞掉别的指令: 如果第一段是常见保留词,跳过
        if query.split()[0].lower() in {"help", "menu", "插件", "重载"}:
            return

        try:
            result = self.dataset.search(
                query, fuzzy_threshold=self.fuzzy_threshold, max_items=self.max_list_items
            )
        except Exception as e:
            logger.exception("[pokemon-shuffle] search failed: %s", e)
            return

        async for r in self._respond(event, query, result):
            yield r

    # ---------- 响应分支 ----------
    async def _respond(self, event: AstrMessageEvent, query: str, result: MatchResult):
        if result.level == "exact":
            # 恰好命中一个
            entry = (result.pokemons or result.abilities)[0]
            try:
                if entry.kind == "pokemon":
                    ability_detail = self.dataset.ability_of(entry.raw.get("ability", ""))
                    path = await render_pokemon(self.render_ctx, entry, ability_detail)
                else:
                    path = await render_ability(self.render_ctx, entry)
                yield event.image_result(str(path))
            except Exception as e:
                logger.exception("[pokemon-shuffle] render failed: %s", e)
                yield event.plain_result(f"渲染失败: {e}")
                return

            # 命中后还有其它包含此关键词的同名条目 → 追加相关列表
            related_msg = _format_related(result)
            if related_msg:
                yield event.plain_result(related_msg)
            return

        if result.level == "contain":
            yield event.plain_result(_format_list(result, header_hit=True))
            return

        if result.level == "fuzzy":
            yield event.plain_result(_format_list(result, header_hit=False))
            return

        # none
        yield event.plain_result(f"未找到与 “{query}” 相关的宝可梦或能力。")

    async def terminate(self):
        pass


def _format_list(result: MatchResult, header_hit: bool) -> str:
    """两段式输出:
        搜索到相关宝可梦:
        皮卡丘
        皮卡丘 - 眨眼
        搜索到相关能力:
        电气连锁
    若 header_hit=False,改用"您要查询的是否是"的口吻。
    """
    if header_hit:
        pm_header = "搜索到相关宝可梦:"
        ab_header = "搜索到相关能力:"
    else:
        pm_header = "您要查询的是否是以下宝可梦:"
        ab_header = "您要查询的是否是以下能力:"

    parts: list[str] = []
    if result.pokemons:
        parts.append(pm_header)
        parts.extend(_unique_displays(result.pokemons))
    if result.abilities:
        if parts:
            parts.append("")  # 段间空行
        parts.append(ab_header)
        parts.extend(_unique_displays(result.abilities))
    return "\n".join(parts)


def _format_related(result: MatchResult) -> str:
    """精确命中后,把其余相关条目拼成一段提示文本。"""
    parts: list[str] = []
    if result.related_pokemons:
        parts.append("搜索到相关宝可梦:")
        parts.extend(_unique_displays(result.related_pokemons))
    if result.related_abilities:
        if parts:
            parts.append("")
        parts.append("搜索到相关能力:")
        parts.extend(_unique_displays(result.related_abilities))
    return "\n".join(parts)


def _unique_displays(entries: list[Entry]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for e in entries:
        if e.display in seen:
            continue
        seen.add(e.display)
        out.append(e.display)
    return out
