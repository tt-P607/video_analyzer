"""视频分析工具，供 LLM 主动调用。

按 video_id 从框架媒体管理器读取落盘的视频文件，可选压缩后发给多模态模型，
返回视频内容摘要，并将描述写入框架描述缓存（已识别视频后续自动进上下文）。
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.llm_api import create_llm_request, get_model_set_by_task
from src.app.plugin_system.api.media_api import (
    get_media_info,
    save_description_cache,
    save_media_info,
)
from src.core.components.base.tool import BaseTool
from src.kernel.concurrency import get_task_manager
from src.kernel.concurrency.watchdog import get_watchdog
from src.kernel.llm import LLMPayload, Text, Video
from src.kernel.llm.roles import ROLE

if TYPE_CHECKING:
    from ..plugin import VideoAnalyzerPlugin

logger = get_logger("video_analyzer")


class AnalyzeVideoTool(BaseTool):
    """分析当前消息中的视频内容。

    当用户发送了视频消息，且 LLM 需要了解视频内容时调用此工具。
    工具会按 video_id 从框架媒体管理器读取视频，发送给多模态模型分析，
    返回详细的文字描述，并将描述写入框架描述缓存。
    注意：仅在当前消息确实包含视频时才调用此工具。
    """

    name = "analyze_video"
    description = (
        "调用此工具后，你将『亲眼看到』视频的内容。"
        "工具返回的文字描述代表你实际观看到的画面，请将其视为你自己的视觉感知，"
        "以第一人称自然地融入回复中，不要暴露『我调用了工具』或『模型分析结果』等信息。"
        "仅在系统提示中明确标注当前消息含有视频（video_id 已提供）时才调用此工具。"
        "注意：如果消息文本中该视频占位符已经直接带有描述文本"
        "（形如 [视频(video_id):描述]），说明这段视频你以前已经看过、内容已知，"
        "请直接基于那段描述回复，不要重复调用本工具。"
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
        video_id: Annotated[str, "视频的唯一标识（video_id），从系统提示中获取"],
    ) -> tuple[bool, str]:
        """执行视频分析。

        Args:
            video_id: 视频的唯一标识（video_id），从系统提示获取

        Returns:
            tuple[bool, str]: (是否成功, 分析结果或错误信息)
        """
        # 已识别过的视频：直接返回缓存描述，避免重复调用多模态模型
        info = await get_media_info(video_id)
        if info and info.get("description"):
            logger.info(f"analyze_video 命中描述缓存，video_id={video_id[:16]}")
            return True, f"[这段视频你之前已经看过，以下是你当时的印象]\n{info['description']}"

        # 从落盘路径读取视频文件（base64）。
        # get_media_info 已做 Images/Voices/Videos 三表回退，可拿到视频记录的 path；
        # 不依赖 get_media_file（其仅查 Images 表，读不到 Videos 表视频）。
        b64 = None
        if info and info.get("path"):
            media_path = Path(info["path"])
            try:
                if not media_path.is_absolute():
                    media_path = Path(".") / media_path
                if media_path.exists():
                    data = await asyncio.to_thread(media_path.read_bytes)
                    b64 = base64.b64encode(data).decode("ascii")
            except Exception as e:
                logger.warning(f"读取视频文件失败: {media_path}: {e}")
        if not b64:
            logger.info(f"analyze_video 未找到视频文件，video_id={video_id!r}")
            return False, "未找到对应的视频数据，视频可能已过期或尚未收到。"

        from ..config import VideoAnalyzerConfig
        config: VideoAnalyzerConfig | None = self.plugin.config  # type: ignore[assignment]

        compress_threshold = config.general.compress_threshold_mb if config else 8.0
        max_video_size = config.general.max_video_size_mb if config else 10.0
        heartbeat_interval = config.general.heartbeat_interval if config else 3.0
        compress_target = config.general.compress_target_mb if config else 8.0
        default_prompt = (
            config.general.default_analysis_prompt
            if config
            else "请详细描述这段视频的内容，包括场景、人物、动作、对话等关键信息。"
        )

        # 提前获取安全的 stream_id (使用 BaseTool 基类的 get_current_stream_id 方法)
        stream_id_for_heartbeat = self.get_current_stream_id()
        if not stream_id_for_heartbeat:
            # 兜底
            stream_id_for_heartbeat = getattr(self, "stream_id", "")
        stream_id_for_heartbeat = str(stream_id_for_heartbeat or "").strip()

        # 启动心跳任务守护（覆盖包括压缩、多模态请求的整个 execute 执行生命周期）
        watchdog = get_watchdog()
        task_manager = get_task_manager()
        heartbeat_stop = asyncio.Event()
        heartbeat_task = None

        def refresh_napcat_heartbeat():
            """强行刷新所有已加载 napcat 适配器的心跳计时器，避免大报文发送同步卡死时误判超时"""
            try:
                from src.app.plugin_system.api.adapter_api import get_adapter
                import time
                adapter = get_adapter("napcat_adapter:adapter:napcat_adapter")
                if adapter and hasattr(adapter, "meta_event_handler"):
                    handler = getattr(adapter, "meta_event_handler")
                    if handler:
                        handler.last_heart_beat = time.time()
                        logger.debug("已强行刷新 napcat_adapter 的心跳 last_heart_beat 计时")
            except Exception as e:
                logger.warning(f"刷新 napcat 适配器心跳状态失败: {e}")

        async def _send_heartbeat():
            """定期发送心跳,避免 WatchDog 超时"""
            if not stream_id_for_heartbeat:
                logger.warning("视频分析工具未获取到 stream_id,无法发送心跳")
                return
            logger.info(f"心跳任务已启动 stream_id={stream_id_for_heartbeat[:16]}, 间隔={heartbeat_interval}s")
            while not heartbeat_stop.is_set():
                try:
                    await asyncio.wait_for(heartbeat_stop.wait(), timeout=heartbeat_interval)
                    break
                except asyncio.TimeoutError:
                    try:
                        watchdog.feed_dog(stream_id_for_heartbeat)
                        refresh_napcat_heartbeat()
                        logger.debug(f"已发送心跳并主动维持适配器连线状态 stream_id={stream_id_for_heartbeat[:16]}")
                    except Exception as e:
                        logger.warning(f"心跳发送或适配器维护失败: {e}")
            logger.info(f"心跳任务已结束 stream_id={stream_id_for_heartbeat[:16]}")

        if stream_id_for_heartbeat:
            heartbeat_task = task_manager.create_task(
                _send_heartbeat(),
                name="video_analysis_heartbeat",
                daemon=True,
            )

        try:
            # 压缩视频（base64 体积约为原字节的 4/3，反推原字节数）
            size_mb = len(b64) * 3 / 4 / 1024 / 1024
            logger.info(f"视频大小检查: size_mb={size_mb:.1f}MB, compress_threshold={compress_threshold}MB")
            if size_mb > compress_threshold:
                logger.info(f"视频大小 {size_mb:.1f}MB 超出阈值，尝试 ffmpeg 压缩到 {compress_target}MB")
                try:
                    from ..utils.video_compressor import compress_video_b64
                    b64 = await compress_video_b64(b64, target_mb=compress_target, input_size_mb=size_mb)
                    compressed_size_mb = len(base64.b64decode(b64)) / 1024 / 1024
                    logger.info(f"视频压缩完成: {size_mb:.1f}MB -> {compressed_size_mb:.1f}MB")
                    size_mb = compressed_size_mb
                except Exception as e:
                    logger.error(f"视频压缩失败: {e}", exc_info=True)
            else:
                logger.info(f"视频大小 {size_mb:.1f}MB 未超出阈值 {compress_threshold}MB,跳过压缩")

            # 检查压缩后大小
            if size_mb > max_video_size:
                return False, (
                    f"视频过大({size_mb:.1f}MB)，超过安全限制({max_video_size}MB)，"
                    f"无法发送以避免 WebSocket 连接断开。\n"
                    f"建议：请尝试更短的视频片段或降低视频分辨率。"
                )

            try:
                model_set = get_model_set_by_task("video")
            except KeyError:
                return False, "未找到 video 任务模型，请检查 config/model.toml 中的 [model_tasks.video] 配置。"

            request = create_llm_request(model_set, request_name="video_analysis")
            request.add_payload(LLMPayload(ROLE.USER, [Video(b64), Text(default_prompt)]))

            # 使用流式响应接收结果,避免长时间阻塞
            response = await request.send(stream=True)
            result_parts: list[str] = []
            async for chunk in response:
                if chunk:
                    result_parts.append(chunk)

            result_text = "".join(result_parts)

            # 写入框架描述缓存（converter 命中后描述自动进上下文）
            # 并同步更新 Videos 业务表，供 get_media_info 判定已识别
            if result_text:
                await save_description_cache(video_id, "video", result_text)
                await save_media_info(
                    video_id,
                    "video",
                    description=result_text,
                    vlm_processed=True,
                )
                logger.info(f"视频描述已写入框架缓存 video_id={video_id[:16]}")
            return True, result_text
        except Exception as e:
            logger.error(f"视频分析请求失败: {e}")
            return False, f"视频分析失败：{e}"
        finally:
            # 停止心跳任务
            heartbeat_stop.set()
            if heartbeat_task and heartbeat_task.task:
                try:
                    await asyncio.wait_for(heartbeat_task.task, timeout=1.0)
                except (asyncio.TimeoutError, Exception):
                    pass
