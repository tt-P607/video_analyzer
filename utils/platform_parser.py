"""B 站与抖音视频/图文内容解析器。

支持：
- B 站（bilibili.com、b23.tv）：无 cookie 直链解析，最高 480P
- 抖音（douyin.com、v.douyin.com）：无 cookie 解析，支持视频和图文
"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
from dataclasses import dataclass, field
from typing import Optional

import aiohttp

from src.app.plugin_system.api.log_api import get_logger

logger = get_logger("video_analyzer")

# ---------------------------------------------------------------------------
# 结果结构
# ---------------------------------------------------------------------------


@dataclass
class FetchResult:
    """视频或图文的抓取结果。

    Attributes:
        platform: 来源平台名（bilibili / douyin）
        title: 内容标题
        description: 内容简介/正文描述
        video_bytes: 视频字节（视频类型时设置，图文时为 None）
        image_bytes_list: 图片字节列表（图文类型时设置，视频时为空列表）
    """

    platform: str
    title: str = ""
    description: str = ""
    video_bytes: bytes | None = None
    image_bytes_list: list[bytes] = field(default_factory=list)

    @property
    def is_gallery(self) -> bool:
        """是否为图文（图集）类型。"""
        return len(self.image_bytes_list) > 0


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

_MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 8.0.0; SM-G955U Build/R16NW) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/116.0.0.0 Mobile Safari/537.36"
)

_PC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# 抖音解析
# ---------------------------------------------------------------------------


async def _parse_douyin(session: aiohttp.ClientSession, url: str) -> dict:
    """解析抖音内容，返回解析结果字典。

    支持视频和图文两种类型，不需要 cookie，通过 iesdouyin.com 分享页面解析。

    Args:
        session: aiohttp ClientSession
        url: 抖音视频/图文链接（短链或长链均可）

    Returns:
        dict with keys:
          - type: "video" | "gallery"
          - url: 视频直链（type=video 时）
          - image_urls: 图片直链列表（type=gallery 时）
          - title: 内容标题/描述文字

    Raises:
        RuntimeError: 解析失败
    """
    headers = {
        "User-Agent": _MOBILE_UA,
        "Referer": "https://www.douyin.com/?is_from_mobile_home=1&recommend=1",
        "Accept-Encoding": "gzip, deflate",
    }

    # 展开短链
    if "v.douyin.com" in url:
        async with session.get(url, headers=headers, allow_redirects=True) as resp:
            url = str(resp.url)

    # 提取 video_id
    id_match = re.search(r"/video/(\d+)", url)
    if not id_match:
        id_match = re.search(r"/note/(\d+)", url)
    if not id_match:
        raise RuntimeError(f"无法从抖音链接提取内容 ID: {url}")

    item_id = id_match.group(1)
    is_note = "/note/" in url

    share_url = (
        f"https://www.iesdouyin.com/share/{'note' if is_note else 'video'}/{item_id}/"
    )

    async with session.get(share_url, headers=headers) as resp:
        html = await resp.text()

    # 从 window._ROUTER_DATA 提取内容信息
    start_flag = "window._ROUTER_DATA = "
    start_idx = html.find(start_flag)
    if start_idx == -1:
        raise RuntimeError("未找到 window._ROUTER_DATA，抖音页面结构可能已变更")

    brace_start = html.find("{", start_idx)
    i = brace_start
    stack: list[str] = []
    while i < len(html):
        if html[i] == "{":
            stack.append("{")
        elif html[i] == "}":
            stack.pop()
            if not stack:
                break
        i += 1
    json_str = html[brace_start : i + 1]

    try:
        json_data = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"抖音 _ROUTER_DATA JSON 解析失败: {e}") from e

    loader_data = json_data.get("loaderData", {})
    item_list: Optional[list] = None
    for v in loader_data.values():
        if isinstance(v, dict):
            res = v.get("videoInfoRes") or v.get("noteDetailRes")
            if res and res.get("item_list"):
                item_list = res["item_list"]
                break

    if not item_list:
        raise RuntimeError("抖音页面中未找到内容信息")

    item = item_list[0]
    title: str = item.get("desc", "").strip()

    # 图文（图集）类型
    raw_images: list = item.get("images") or []
    if raw_images:
        image_urls: list[str] = []
        for img in raw_images:
            url_list: list[str] = img.get("url_list") or []
            if url_list:
                image_urls.append(url_list[0])
        if not image_urls:
            raise RuntimeError("抖音图文内容中未能提取到图片链接")
        logger.info(f"抖音图文，共 {len(image_urls)} 张图片，标题: {title[:30]}")
        return {"type": "gallery", "image_urls": image_urls, "title": title}

    # 视频类型
    video_info = item.get("video", {})
    play_addr = video_info.get("play_addr", {})
    uri = play_addr.get("uri", "")

    if not uri:
        raise RuntimeError("抖音视频直链为空，内容可能已删除")

    if uri.startswith("https://"):
        video_url = uri
    elif uri.endswith(".mp3"):
        video_url = uri
    else:
        video_url = f"https://www.douyin.com/aweme/v1/play/?video_id={uri}"

    return {"type": "video", "url": video_url, "title": title}


# ---------------------------------------------------------------------------
# B站解析（无 cookie，最高 480P）
# ---------------------------------------------------------------------------


async def _parse_bilibili(session: aiohttp.ClientSession, url: str) -> dict:
    """解析 B 站视频，返回解析结果字典（无 cookie，最高 480P）。

    流程：
    1. 展开 b23.tv 短链
    2. 提取 BV 号和分 P 序号
    3. 调用 /x/web-interface/view 获取 cid、title、description
    4. 调用 /x/player/playurl?platform=html5&fnval=0 获取 durl 直链

    Args:
        session: aiohttp ClientSession
        url: B 站视频链接（长链/短链/BV 号均可）

    Returns:
        dict with keys: url, title, description

    Raises:
        RuntimeError: 解析失败
    """
    headers = {
        "User-Agent": _PC_UA,
        "Referer": "https://www.bilibili.com",
        "Origin": "https://www.bilibili.com",
    }

    # 展开 b23.tv 短链
    if "b23.tv" in url.lower():
        async with session.get(url, headers=headers, allow_redirects=True) as resp:
            url = str(resp.url)

    # 提取 BV 号
    bv_match = re.search(r"[Bb][Vv][0-9A-Za-z]{10,}", url)
    if not bv_match:
        raise RuntimeError(f"无法从 B 站链接提取 BV 号: {url}")
    bvid = bv_match.group(0)

    # 分 P 序号（URL 中 p=N 是 1-indexed）
    p_match = re.search(r"[?&]p=(\d+)", url)
    page_index = max(0, int(p_match.group(1)) - 1) if p_match else 0

    # 获取视频信息（含 cid、title、desc）
    view_api = "https://api.bilibili.com/x/web-interface/view"
    async with session.get(
        view_api, params={"bvid": bvid}, headers=headers,
        timeout=aiohttp.ClientTimeout(total=15),
    ) as resp:
        j = await resp.json(content_type=None)

    if j.get("code") != 0:
        raise RuntimeError(f"B 站视频信息获取失败: {j.get('message')}")

    data = j["data"]
    title: str = data.get("title", "").strip()
    description: str = data.get("desc", "").strip()
    pages = data.get("pages") or []
    if not pages:
        raise RuntimeError("B 站视频分 P 信息为空")

    page_index = min(page_index, len(pages) - 1)
    cid = pages[page_index]["cid"]

    # 获取播放地址（platform=html5 + fnval=0 返回单文件 durl）
    play_api = "https://api.bilibili.com/x/player/playurl"
    play_params = {
        "bvid": bvid,
        "cid": cid,
        "qn": 64,          # 720P，无 cookie 实际降至 480P
        "fnval": 0,        # 0 = 单文件 MP4/FLV
        "fnver": 0,
        "fourk": 1,
        "otype": "json",
        "platform": "html5",
        "high_quality": 1,
    }
    async with session.get(
        play_api, params=play_params, headers=headers,
        timeout=aiohttp.ClientTimeout(total=15),
    ) as resp:
        j = await resp.json(content_type=None)

    if j.get("code") != 0:
        raise RuntimeError(f"B 站播放地址获取失败: {j.get('message')}")

    durl = (j.get("data") or {}).get("durl") or []
    if not durl:
        raise RuntimeError("B 站播放地址为空，可能需要登录或大会员")

    video_url = durl[0].get("url") or ""
    if not video_url:
        raise RuntimeError("B 站视频直链为空")

    return {"url": video_url, "title": title, "description": description}


# ---------------------------------------------------------------------------
# yt-dlp 通用下载
# ---------------------------------------------------------------------------


async def _download_direct(url: str, headers: Optional[dict] = None) -> bytes:
    """直接 HTTP 下载视频直链，返回字节内容。

    Args:
        url: 视频直链 URL
        headers: 可选请求头

    Returns:
        视频文件字节数据

    Raises:
        RuntimeError: 下载失败
    """
    req_headers = headers or {"User-Agent": _MOBILE_UA, "Referer": url}
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=req_headers) as resp:
            if resp.status != 200:
                raise RuntimeError(f"直接下载失败，状态码: {resp.status}")
            data = await resp.read()
    return data


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------

_BILIBILI_DOMAINS = ("bilibili.com", "b23.tv")
_DOUYIN_DOMAINS = ("v.douyin.com", "douyin.com")


def is_platform_supported(url: str) -> bool:
    """判断 URL 是否来自已支持的平台（B 站或抖音）。

    Args:
        url: 视频/图文链接

    Returns:
        是否属于已支持平台
    """
    lower = url.lower()
    return any(d in lower for d in (*_BILIBILI_DOMAINS, *_DOUYIN_DOMAINS))


def _detect_format(data: bytes) -> str:
    """通过文件头魔数检测文件格式。

    Args:
        data: 文件字节数据（至少前 12 字节）

    Returns:
        格式字符串，如 'mp4', 'webp', 'gif', 'jpeg', 'png', 'unknown'
    """
    if len(data) < 12:
        return "unknown"
    if data[4:8] == b"ftyp" or data[4:8] in (b"moov", b"mdat"):
        return "mp4"
    if data[:3] == b"FLV":
        return "flv"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    return "unknown"


async def _ensure_mp4(data: bytes, source: str = "") -> bytes:
    """确保视频数据为 MP4 格式，非 MP4 时用 ffmpeg 转换。

    Args:
        data: 原始字节数据
        source: 来源描述（用于日志）

    Returns:
        MP4 格式的字节数据

    Raises:
        RuntimeError: ffmpeg 转换失败或输入为不可转换的格式（图片）
    """
    import asyncio
    import tempfile
    from pathlib import Path

    fmt = _detect_format(data)
    if fmt == "mp4":
        return data

    # 图片格式拒绝处理（不是视频）
    if fmt in ("jpeg", "png"):
        raise RuntimeError(f"下载的内容是图片（{fmt}），不是视频，无法分析")

    # FLV 或其他格式用 ffmpeg 转 MP4
    logger.info(f"检测到 {fmt} 格式{f'（来自 {source}）' if source else ''}，尝试 ffmpeg 转换为 MP4")

    with tempfile.TemporaryDirectory() as tmpdir:
        suffix_map = {"webp": ".webp", "gif": ".gif", "flv": ".flv"}
        suffix = suffix_map.get(fmt, ".bin")
        input_path = Path(tmpdir) / f"input{suffix}"
        output_path = Path(tmpdir) / "output.mp4"

        # 同步文件写入放入线程池，避免阻塞事件循环
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, input_path.write_bytes, data)

        cmd = [
            "ffmpeg", "-i", str(input_path),
            "-c:v", "libx264", "-crf", "28",
            "-c:a", "aac", "-b:a", "64k",
            "-movflags", "+faststart",
            "-y", str(output_path),
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0 or not output_path.exists():
            err = stderr.decode(errors="replace")[-300:] if stderr else ""
            raise RuntimeError(f"ffmpeg 格式转换失败: {err}")

        return await loop.run_in_executor(None, output_path.read_bytes)


async def fetch_video_bytes(url: str) -> FetchResult:
    """从 URL 获取视频或图文内容，返回统一结果对象。

    支持平台：
    - B 站（bilibili.com、b23.tv）：无 cookie 直链解析，最高 480P
    - 抖音（douyin.com、v.douyin.com）：无 cookie 解析，支持视频和图文

    Args:
        url: 视频/图文页面链接

    Returns:
        FetchResult 对象，包含平台、标题、简介及媒体内容

    Raises:
        RuntimeError: 不支持的平台或解析/下载失败
    """
    lower = url.lower()

    # B站
    if any(d in lower for d in _BILIBILI_DOMAINS):
        logger.info("使用 B 站专用解析器（无 cookie，最高 480P）")
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            info = await _parse_bilibili(session, url)
        logger.info(f"B 站直链解析成功，标题: {info['title'][:30]}")
        data = await _download_direct(
            info["url"],
            headers={
                "User-Agent": _PC_UA,
                "Referer": "https://www.bilibili.com/",
            },
        )
        data = await _ensure_mp4(data, "bilibili")
        return FetchResult(
            platform="bilibili",
            title=info["title"],
            description=info["description"],
            video_bytes=data,
        )

    # 抖音
    if any(d in lower for d in _DOUYIN_DOMAINS):
        logger.info("使用抖音专用解析器（无 cookie）")
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            info = await _parse_douyin(session, url)

        if info["type"] == "gallery":
            image_urls: list[str] = info["image_urls"][:16]
            logger.info(f"抖音图文，下载 {len(image_urls)} 张图片")
            tasks = [
                _download_direct(
                    img_url,
                    headers={"User-Agent": _MOBILE_UA, "Referer": "https://www.douyin.com/"},
                )
                for img_url in image_urls
            ]
            image_bytes_list = list(await asyncio.gather(*tasks))
            return FetchResult(
                platform="douyin",
                title=info["title"],
                image_bytes_list=image_bytes_list,
            )
        else:
            logger.info(f"抖音视频直链解析成功: {info['url'][:60]}")
            data = await _download_direct(
                info["url"],
                headers={
                    "User-Agent": _MOBILE_UA,
                    "Referer": "https://www.douyin.com/",
                },
            )
            data = await _ensure_mp4(data, "douyin")
            return FetchResult(
                platform="douyin",
                title=info["title"],
                video_bytes=data,
            )

    raise RuntimeError(f"不支持的平台，仅支持 B 站和抖音: {url}")
