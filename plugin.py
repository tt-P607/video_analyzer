"""视频分析插件入口。

允许 LLM 主动调用 analyze_video 工具，通过多模态模型分析用户发送的视频内容。
视频数据由框架媒体管理器落盘入库，本插件仅记录视频元数据（video_id 等），
不重复存储视频二进制。
"""

from __future__ import annotations

from src.app.plugin_system.api.log_api import get_logger
from src.core.components.base import BasePlugin
from src.core.components.loader import register_plugin

from .components.video_event_handler import VideoEventHandler
from .components.video_tool import AnalyzeVideoTool
from .components.fetch_tool import FetchAndAnalyzeVideoTool
from .config import VideoAnalyzerConfig

logger = get_logger("video_analyzer")


@register_plugin
class VideoAnalyzerPlugin(BasePlugin):
    """视频分析插件。

    检测用户消息中的视频，将视频元数据（video_id 等）缓存在插件实例上，
    并通知 LLM 可调用 analyze_video 工具进行视频内容分析。
    视频二进制由框架媒体管理器落盘入库，不在此重复存储。
    """

    plugin_name = "video_analyzer"
    plugin_description = "允许 LLM 主动调用多模态模型分析用户发送的视频，返回视频内容摘要"

    configs: list[type] = [VideoAnalyzerConfig]
    dependent_components: list[str] = []

    def __init__(self, config: VideoAnalyzerConfig | None = None) -> None:
        """初始化插件。

        Args:
            config: 插件配置实例
        """
        super().__init__(config)
        # video_id -> {"video_id": str, "filename": str, "size_mb": float}
        # 仅记录元数据，供工具判断当前会话是否见过该视频（二进制在框架落盘）
        self.video_cache: dict[str, dict] = {}

    def get_components(self) -> list[type]:
        """返回插件提供的组件类列表。"""
        return [VideoEventHandler, AnalyzeVideoTool, FetchAndAnalyzeVideoTool]
