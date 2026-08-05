"""视频事件处理器。

订阅 ON_MESSAGE_RECEIVED 事件，检测消息中的视频段，将视频元数据
（video_id 等）缓存到插件实例，供 analyze_video 工具按 video_id 查询。
视频二进制由框架媒体管理器落盘入库，不在此重复存储。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.plugin_system.api.log_api import get_logger
from src.core.components.base import BaseEventHandler
from src.core.components.types import EventType
from src.kernel.event import EventDecision

if TYPE_CHECKING:
    from ..plugin import VideoAnalyzerPlugin

logger = get_logger("video_analyzer")


class VideoEventHandler(BaseEventHandler):
    """检测并缓存消息中的视频元数据，供 analyze_video 工具使用。

    视频段由 converter 解析后存放于 ``message.extra["media"]``（带 video_id），
    本处理器读取该字段并将视频元数据（video_id、filename、size_mb、url）
    存入插件缓存，供 analyze_video 工具按 video_id 查询。
    """

    name = "video_cache_handler"
    description = "检测并缓存消息中的视频数据，供 analyze_video 工具使用"
    weight = 50

    init_subscribe = [EventType.ON_MESSAGE_RECEIVED]

    def __init__(self, plugin: "VideoAnalyzerPlugin") -> None:
        """初始化事件处理器。

        Args:
            plugin: 宿主插件实例
        """
        super().__init__(plugin)
        self.plugin: "VideoAnalyzerPlugin" = plugin  # type: ignore[assignment]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """处理消息事件，提取并缓存视频元数据。

        Args:
            event_name: 事件名称
            params: 事件参数，包含 message 字段

        Returns:
            tuple[EventDecision, dict]: 事件决策与（可能已修改的）参数
        """
        message = params.get("message")
        if message is None:
            return EventDecision.PASS, params

        # 视频段由 converter 存入 message.extra["media"]（带 video_id）
        media_list: list[dict[str, Any]] = message.extra.get("media") or []
        video_segs = [m for m in media_list if m.get("type") == "video"]
        if not video_segs:
            return EventDecision.PASS, params

        video: dict[str, Any] = video_segs[0]
        video_id: str = video.get("video_id", "")
        if not video_id:
            return EventDecision.PASS, params

        # 缓存视频元数据（二进制由框架落盘，工具经 media_api.get_media_file 读取）
        cache: dict[str, dict] = self.plugin.video_cache  # type: ignore[attr-defined]
        cache[video_id] = {
            "video_id": video_id,
            "filename": video.get("filename", "video.mp4"),
            "size_mb": video.get("size_mb", 0.0),
            "url": video.get("url", ""),
        }
        logger.info(
            f"视频已缓存 video_id={video_id[:8]} "
            f"filename={video.get('filename', 'video.mp4')} "
            f"size={video.get('size_mb', 0):.1f}MB"
        )

        return EventDecision.SUCCESS, params
