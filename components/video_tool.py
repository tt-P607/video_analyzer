"""视频分析工具，供 LLM 主动调用。

从插件共享缓存中取出视频数据，可选压缩后发给多模态模型，返回视频内容摘要。
"""

from __future__ import annotations

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


class AnalyzeVideoTool(BaseTool):
    """分析当前消息中的视频内容。

    当用户发送了视频消息，且 LLM 需要了解视频内容时调用此工具。
    工具会将视频发送给多模态模型（如 Gemini 2.5 Pro）进行分析，返回详细的文字描述。
    注意：仅在当前消息确实包含视频时才调用此工具。
    """

    tool_name = "analyze_video"
    tool_description = (
        "调用此工具后，你将『亲眼看到』视频的内容。"
        "工具返回的文字描述代表你实际观看到的画面，请将其视为你自己的视觉感知，"
        "以第一人称自然地融入回复中，不要暴露『我调用了工具』或『模型分析结果』等信息。"
        "仅在系统提示中明确标注当前消息含有视频（stream_id 已提供）时才调用此工具。"
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
        stream_id: Annotated[str, "视频的唯一标识（video_id），从系统提示中获取"],
    ) -> tuple[bool, str]:
        """执行视频分析。

        Args:
            stream_id: 视频的唯一标识（video_id），从系统提示获取

        Returns:
            tuple[bool, str]: (是否成功, 分析结果或错误信息)
        """
        cache: dict = self.plugin.video_cache  # type: ignore[attr-defined]
        analysis_cache: dict = self.plugin.analysis_cache  # type: ignore[attr-defined]
        entry: dict | None = cache.get(stream_id)
        if not entry:
            logger.info(f"analyze_video 未找到视频缓存，video_id={stream_id!r}，当前缓存 keys={list(cache.keys())}")
            return False, "未找到对应的视频数据，视频可能已过期或尚未收到。"

        # 描述去重：同一 video_id 已分析过则直接返回缓存结果，并附上"已看过"的语境提示
        if stream_id in analysis_cache:
            logger.info(f"analyze_video 命中描述缓存，video_id={stream_id[:16]}")
            cached = analysis_cache[stream_id]
            return True, f"[这段视频你之前已经看过，以下是你当时的印象]\n{cached}"

        b64: str = entry["base64"]
        size_mb: float = entry.get("size_mb", 0.0)

        from ..config import VideoAnalyzerConfig
        config: VideoAnalyzerConfig | None = self.plugin.config  # type: ignore[assignment]

        compress_threshold = config.general.compress_threshold_mb if config else 18.0
        default_prompt = (
            config.general.default_analysis_prompt
            if config
            else "请详细描述这段视频的内容，包括场景、人物、动作、对话等关键信息。"
        )

        # 超出阈值时尝试 ffmpeg 压缩
        if size_mb > compress_threshold:
            logger.info(f"视频大小 {size_mb:.1f}MB 超出阈值，尝试 ffmpeg 压缩")
            try:
                from ..utils.video_compressor import compress_video_b64
                b64 = await compress_video_b64(b64, target_mb=15.0)
                logger.info("视频压缩成功")
            except Exception as e:
                logger.warning(f"视频压缩失败，尝试直接发送: {e}")

        prompt = default_prompt

        try:
            model_set = get_model_set_by_task("video")
        except KeyError:
            return False, "未找到 video 任务模型，请检查 config/model.toml 中的 [model_tasks.video] 配置。"

        request = create_llm_request(model_set, request_name="video_analysis")
        request.add_payload(LLMPayload(ROLE.USER, [Video(b64), Text(prompt)]))

        try:
            response = await request.send(stream=False)
            result_text: str = response.message or ""
            # 存入描述缓存，避免重复消耗 token；同时持久化到磁盘
            if result_text:
                analysis_cache[stream_id] = result_text
                try:
                    from ..plugin import _ANALYSIS_CACHE_KEY
                    await json_store.save(_ANALYSIS_CACHE_KEY, dict(analysis_cache))
                except Exception as e:
                    logger.warning(f"视频分析缓存持久化失败: {e}")
                logger.info(f"视频描述已缓存 video_id={stream_id[:16]}")
            return True, result_text
        except Exception as e:
            logger.error(f"视频分析请求失败: {e}")
            return False, f"视频分析失败：{e}"
