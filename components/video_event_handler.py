"""视频事件处理器。

订阅 ON_MESSAGE_RECEIVED 事件，检测消息中的视频段，
将视频数据缓存到插件实例，并向 LLM 注入视频存在提示。
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
    """检测并缓存消息中的视频数据，供 analyze_video 工具使用。

    当消息的 unknown_segments 中包含 type="video" 的段时，
    将视频 base64 数据存入插件缓存，并在消息内容末尾注入提示文本，
    使 LLM 感知到当前消息含有视频。
    """

    handler_name = "video_cache_handler"
    handler_description = "检测并缓存消息中的视频数据，供 analyze_video 工具使用"
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
        """处理消息事件，提取并缓存视频数据。

        Args:
            event_name: 事件名称
            params: 事件参数，包含 message 字段

        Returns:
            tuple[EventDecision, dict]: 事件决策与（可能已修改的）参数
        """
        message = params.get("message")
        if message is None:
            return EventDecision.PASS, params

        unknown_segs: list[dict[str, Any]] = message.extra.get("unknown_segments") or []
        video_segs = [s for s in unknown_segs if s.get("type") == "video"]
        if not video_segs:
            return EventDecision.PASS, params

        video_data: dict[str, Any] = video_segs[0].get("data") or {}
        b64: str = video_data.get("base64", "")
        if not b64:
            return EventDecision.PASS, params

        stream_id: str = getattr(message, "stream_id", "")
        if not stream_id:
            return EventDecision.PASS, params

        # 用视频内容的 MD5 哈希作为缓存 key，同一视频无论谁发/引用/转发都命中同一 key
        import hashlib
        cache_key = hashlib.md5(b64.encode(), usedforsecurity=False).hexdigest()

        # 存入插件级缓存（LRU 逐出超出上限的最旧条目）
        cache: dict = self.plugin.video_cache  # type: ignore[attr-defined]
        from ..config import VideoAnalyzerConfig
        config: VideoAnalyzerConfig | None = self.plugin.config  # type: ignore[assignment]
        max_entries = config.general.max_cache_entries if config else 50

        if cache_key not in cache and len(cache) >= max_entries:
            # 逐出最早插入的条目
            oldest_key = next(iter(cache))
            del cache[oldest_key]
            logger.info(f"视频缓存已满，逐出最旧条目: {oldest_key[:8]}")

        cache[cache_key] = {
            "base64": b64,
            "filename": video_data.get("filename", "video.mp4"),
            "size_mb": video_data.get("size_mb", 0.0),
        }
        logger.info(
            f"视频已缓存 key={cache_key[:8]} "
            f"filename={video_data.get('filename', 'video.mp4')} "
            f"size={video_data.get('size_mb', 0):.1f}MB"
        )

        # 向消息内容注入提示，让 LLM 感知视频存在（用内容哈希 key 查找）
        hint = (
            f"\n[系统提示：当前消息包含一段视频（video_id: {cache_key}），"
            f"如需分析视频内容，请调用 analyze_video 工具，传入该 video_id]"
        )
        content = getattr(message, "content", "")
        if isinstance(content, str):
            message.content = content + hint
        elif isinstance(content, list):
            message.content = message.content + [hint]

        # format_message_line 优先使用 processed_plain_text，必须同步更新
        plain = getattr(message, "processed_plain_text", None) or ""
        message.processed_plain_text = plain + hint

        return EventDecision.SUCCESS, params
