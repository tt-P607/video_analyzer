"""通过 URL 抓取并分析视频或图文内容。

支持 B 站（bilibili.com、b23.tv）和抖音（douyin.com、v.douyin.com），
抖音同时支持视频和图文（图集）两种格式，标题与简介会一并传递给模型。
"""

from __future__ import annotations

import base64
import hashlib
import re
from typing import TYPE_CHECKING, Annotated

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.llm_api import create_llm_request, get_model_set_by_task
from src.core.components.base.tool import BaseTool
from src.kernel.llm import LLMPayload, Text, Video
from src.kernel.llm.roles import ROLE
from src.kernel.storage import json_store

if TYPE_CHECKING:
    from ..plugin import VideoAnalyzerPlugin

logger = get_logger("video_analyzer")

# yt-dlp 下载参数：只取最佳画质但限制文件大小
_URL_RE = re.compile(r"https?://[^\s\u3000\u300a\u300b\u3008\u3009\uff08\uff09（）【】「」]+")


def _extract_url(text: str) -> str:
    """从文本中提取第一个 HTTP(S) URL。

    支持抖音/快手/B站等平台的分享文本（如「复制链接打开抖音看看...」）。
    若文本本身就是纯 URL 则直接返回。

    Args:
        text: 包含 URL 的文本或纯 URL

    Returns:
        提取出的 URL，若无则返回原文本（交给 yt-dlp 处理）
    """
    text = text.strip()
    match = _URL_RE.search(text)
    if match:
        url = match.group(0).rstrip(".,;:!?")
        return url
    return text



class FetchAndAnalyzeVideoTool(BaseTool):
    """从 URL 抓取并分析视频内容。

    支持 B 站、抖音、快手、微博、小红书、Twitter/X 等主流平台。
    当你想主动看一个视频，或者用户要求你看某个链接/分享卡片里的视频时调用此工具。
    """

    tool_name = "fetch_and_analyze_video"
    tool_description = (
        "当你想主动看一个视频或图文，或者用户要求你看某个链接/分享卡片时调用此工具。"
        "支持平台：B 站（bilibili.com、b23.tv）、抖音（douyin.com、v.douyin.com）。"
        "抖音支持视频和图文（图集）两种格式，标题与简介会一并分析。"
        "传入视频/图文的直接链接即可；若你收到的是分享文本，请先从中提取出 URL 再传入。"
        "工具会自动下载并分析内容，返回的文字描述代表你实际观看到的画面，"
        "请将其视为你自己的视觉感知，以第一人称自然地融入回复中，"
        "不要暴露『我调用了工具』或『模型分析结果』等信息。"
    )

    def __init__(self, plugin: "VideoAnalyzerPlugin") -> None:
        """初始化工具。

        Args:
            plugin: 宿主插件实例
        """
        super().__init__(plugin)
        self.plugin: "VideoAnalyzerPlugin" = plugin  # type: ignore[assignment]

    async def execute(
        self,
        url: Annotated[str, "视频/图文的直接链接（如 https://b23.tv/xxx 或 https://v.douyin.com/xxx 等）；若收到的是分享文本，请先提取其中的 URL 再传入"],
    ) -> tuple[bool, str]:
        """执行视频/图文抓取与分析。

        Args:
            url: 视频/图文链接（或含链接的分享文本，兜底正则提取）

        Returns:
            tuple[bool, str]: (是否成功, 分析结果或错误信息)
        """
        from ..utils.platform_parser import fetch_video_bytes, FetchResult
        from src.kernel.llm import Image

        analysis_cache: dict = self.plugin.analysis_cache  # type: ignore[attr-defined]

        actual_url = _extract_url(url)
        if actual_url != url:
            logger.info(f"从分享文本中提取 URL: {actual_url[:80]}")

        try:
            logger.info(f"fetch_and_analyze_video 开始下载: {actual_url[:80]}")
            result: FetchResult = await fetch_video_bytes(actual_url)
        except Exception as e:
            logger.error(f"视频/图文下载失败: {e}")
            return False, f"内容下载失败，可能是链接无效、平台不支持或网络超时：{e}"

        # 构建缓存 key（按媒体内容哈希）
        if result.is_gallery:
            cache_source = b"".join(result.image_bytes_list)
            media_desc = f"图文 {len(result.image_bytes_list)} 张图片"
        else:
            cache_source = result.video_bytes or b""
            size_mb = len(cache_source) / 1024 / 1024
            media_desc = f"视频 {size_mb:.1f}MB"

        logger.info(f"内容下载完成（{result.platform}），{media_desc}")

        cache_key = hashlib.md5(cache_source, usedforsecurity=False).hexdigest()

        if cache_key in analysis_cache:
            logger.info(f"命中描述缓存 cache_key={cache_key[:16]}")
            cached = analysis_cache[cache_key]
            return True, f"[这段内容你之前已经看过，以下是你当时的印象]\n{cached}"

        from ..config import VideoAnalyzerConfig
        config: VideoAnalyzerConfig | None = self.plugin.config  # type: ignore[assignment]

        if result.is_gallery:
            base_prompt = (
                config.general.default_gallery_prompt
                if config
                else "请对这组图文内容逐图描述，包括场景、人物、文字等关键信息。"
            )
        else:
            base_prompt = (
                config.general.default_analysis_prompt
                if config
                else "请详细描述这段视频的内容，包括场景、人物、动作、对话等关键信息。"
            )
        compress_threshold = config.general.compress_threshold_mb if config else 18.0

        # 拼接标题/简介到 prompt
        meta_parts: list[str] = []
        if result.title:
            meta_parts.append(f"标题：{result.title}")
        if result.description:
            meta_parts.append(f"简介：{result.description}")
        if meta_parts:
            full_prompt = base_prompt + "\n\n" + "\n".join(meta_parts)
        else:
            full_prompt = base_prompt

        try:
            model_set = get_model_set_by_task("video")
        except KeyError:
            return False, "未找到 video 任务模型，请检查 config/model.toml 中的 [model_tasks.video] 配置。"

        request = create_llm_request(model_set, request_name="fetch_video_analysis")

        if result.is_gallery:
            # 图文：用多张 Image payload
            image_contents: list = [
                Image(base64.b64encode(img).decode())
                for img in result.image_bytes_list
            ]
            image_contents.append(Text(full_prompt))
            request.add_payload(LLMPayload(ROLE.USER, image_contents))
        else:
            # 视频：压缩后用 Video payload
            b64 = base64.b64encode(result.video_bytes or b"").decode()
            video_size_mb = len(result.video_bytes or b"") / 1024 / 1024
            if video_size_mb > compress_threshold:
                logger.info(f"视频大小 {video_size_mb:.1f}MB 超出阈值，尝试 ffmpeg 压缩")
                try:
                    from ..utils.video_compressor import compress_video_b64
                    b64 = await compress_video_b64(b64, target_mb=15.0)
                except Exception as e:
                    logger.warning(f"视频压缩失败，尝试直接发送: {e}")
            request.add_payload(LLMPayload(ROLE.USER, [Video(b64), Text(full_prompt)]))

        logger.info(f"fetch_and_analyze_video 开始分析 cache_key={cache_key[:16]}")
        try:
            response = await request.send(stream=False)
            analysis: str = response.message or ""

            # 组装最终结果：标题/简介 + 分析内容
            output_parts: list[str] = []
            if result.title:
                output_parts.append(f"标题：{result.title}")
            if result.description:
                output_parts.append(f"简介：{result.description}")
            if output_parts:
                output_parts.append("")  # 空行分隔
            if analysis:
                output_parts.append(analysis)
            result_text = "\n".join(output_parts)

            if result_text:
                analysis_cache[cache_key] = result_text
                try:
                    from ..plugin import _ANALYSIS_CACHE_KEY
                    await json_store.save(_ANALYSIS_CACHE_KEY, dict(analysis_cache))
                except Exception as e:
                    logger.warning(f"分析缓存持久化失败: {e}")
                preview = result_text[:120].replace("\n", " ")
                logger.info(f"分析完成 cache_key={cache_key[:16]}，预览: {preview}...")
            else:
                logger.warning(f"分析返回空结果 cache_key={cache_key[:16]}")

            return True, result_text
        except Exception as e:
            logger.error(f"分析请求失败: {e}")
            return False, f"内容分析失败：{e}"
