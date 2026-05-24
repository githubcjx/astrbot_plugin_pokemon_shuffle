# astrbot_plugin_pokemon_shuffle

宝可梦消消乐(Pokemon Shuffle)数据查询插件,适用于 [AstrBot](https://docs.astrbot.app/)。

数据来源:[githubcjx/Pokemon-Shuffle-Data](https://github.com/githubcjx/Pokemon-Shuffle-Data)。

## 功能

- `/皮卡丘`、`/pikachu` —— 查询宝可梦,返回信息卡片(图片)
- `/超级力量`、`/mega boost` —— 查询能力,返回详情卡片(图片)
- 无视大小写、空格、常见标点
- 同名/多形态命中 → 返回相关列表(分宝可梦、能力两段)
- 完全没命中 → rapidfuzz 模糊匹配(默认相似度 ≥ 50%),返回"您要查询的是否是: xxx"建议

## 安装

把整个 `astrbot_plugin_pokemon_shuffle/` 目录放到 AstrBot 的 `data/plugins/` 下,然后:

```bash
pip install -r requirements.txt
```

或者通过 AstrBot WebUI 的插件市场重载。

## 中文字体

渲染卡片需要 CJK 字体。插件按顺序查找:

1. 插件 `fonts/` 目录下的任意 `.ttf` / `.ttc` / `.otf`(**推荐**:放一个 NotoSansCJK-Regular.ttc 进去)
2. Windows: `C:/Windows/Fonts/msyh.ttc`、`simhei.ttf`
3. macOS: `/System/Library/Fonts/PingFang.ttc`
4. Linux: `/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc` 等

如果都找不到,文字会用 Pillow 默认字体(无中文,会乱码)。

## 配置

在 AstrBot WebUI 的插件配置面板里可调:

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `command_prefix` | `/` | 触发前缀。改成 `.` 即 `.皮卡丘` |
| `fuzzy_threshold` | `50` | 模糊匹配相似度阈值(0-100) |
| `max_list_items` | `15` | 每段列表最多返回多少条 |

## 示例响应

输入 `/暗影冲击` → 返回能力卡片图(发动率/升级效果/能力描述/拥有宝可梦)

输入 `/妙蛙` → 命中多个,返回:

```
搜索到相关宝可梦:
妙蛙种子
妙蛙种子 - 眨眼
妙蛙草
妙蛙花
```

输入 `/皮卡秋`(错别字) → 返回:

```
您要查询的是否是以下宝可梦:
皮卡丘
皮卡丘 - 眨眼
...
```

## 缓存

- `cache/icons/` —— 52poke wiki 图标,首次访问后永久缓存(md5 文件名)
- `cache/cards/` —— 渲染好的卡片 PNG

可以随时清空,会自动重新生成。
