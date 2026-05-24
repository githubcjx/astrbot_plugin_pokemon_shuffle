"""52poke wiki 图标的异步下载与本地缓存。

文件名使用 URL 的 md5,首次访问后永久缓存,后续命中本地磁盘。
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import aiohttp


class IconCache:
    def __init__(self, cache_dir: Path, timeout: float = 10.0):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._locks: dict[str, asyncio.Lock] = {}

    def _path_for(self, url: str) -> Path:
        ext = ".png"
        # 尽量保留原扩展名
        for cand in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
            if cand in url.lower():
                ext = cand
                break
        digest = hashlib.md5(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}{ext}"

    async def get(self, url: str) -> Path | None:
        """返回本地图片路径;失败时返回 None。"""
        if not url:
            return None
        path = self._path_for(url)
        if path.exists() and path.stat().st_size > 0:
            return path

        lock = self._locks.setdefault(url, asyncio.Lock())
        async with lock:
            if path.exists() and path.stat().st_size > 0:
                return path
            try:
                async with aiohttp.ClientSession(timeout=self.timeout) as session:
                    async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}) as resp:
                        if resp.status != 200:
                            return None
                        data = await resp.read()
                        path.write_bytes(data)
                        return path
            except Exception:
                return None

    async def get_many(self, urls: list[str]) -> list[Path | None]:
        return await asyncio.gather(*(self.get(u) for u in urls))
