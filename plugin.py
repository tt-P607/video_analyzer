"""视频分析插件入口。

允许 LLM 主动调用 analyze_video 工具，通过多模态模型分析用户发送的视频内容。
"""

from __future__ import annotations

from src.app.plugin_system.api.log_api import get_logger
from src.core.components.base import BasePlugin
from src.core.components.loader import register_plugin
from src.kernel.storage import json_store

from .config import VideoAnalyzerConfig
from .components.video_event_handler import VideoEventHandler
from .components.video_tool import AnalyzeVideoTool
from .components.fetch_tool import FetchAndAnalyzeVideoTool

logger = get_logger("video_analyzer")

_ANALYSIS_CACHE_KEY = "video_analyzer_analysis_cache"


@register_plugin
class VideoAnalyzerPlugin(BasePlugin):
    """视频分析插件。

    检测用户消息中的视频，将视频数据缓存在插件实例上，
    并通知 LLM 可调用 analyze_video 工具进行视频内容分析。
    """

    plugin_name = "video_analyzer"
    plugin_description = "允许 LLM 主动调用多模态模型分析用户发送的视频，返回视频内容摘要"
    plugin_version = "0.1.0"

    configs: list[type] = [VideoAnalyzerConfig]
    dependent_components: list[str] = []

    def __init__(self, config: VideoAnalyzerConfig | None = None) -> None:
        """初始化插件，创建插件级共享视频缓存。

        Args:
            config: 插件配置实例
        """
        super().__init__(config)
        # video_md5 -> {"base64": str, "filename": str, "size_mb": float}（内存，重启清空）
        self.video_cache: dict[str, dict] = {}
        # video_md5 -> str（分析结果，持久化到磁盘）
        self.analysis_cache: dict[str, str] = {}

    async def on_plugin_loaded(self) -> None:
        """插件加载时从磁盘恢复分析结果缓存。"""
        try:
            data = await json_store.load(_ANALYSIS_CACHE_KEY)
            if isinstance(data, dict):
                self.analysis_cache = data
                logger.info(f"已从磁盘恢复视频分析缓存，共 {len(data)} 条")
        except Exception as e:
            logger.warning(f"加载视频分析缓存失败，将使用空缓存: {e}")

    def get_components(self) -> list[type]:
        """返回插件提供的组件类列表。"""
        return [VideoEventHandler, AnalyzeVideoTool, FetchAndAnalyzeVideoTool]
