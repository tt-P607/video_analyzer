"""视频分析插件配置定义。"""

from __future__ import annotations

from typing import ClassVar

from src.core.components.base.config import BaseConfig, Field, SectionBase, config_section


class VideoAnalyzerConfig(BaseConfig):
    """视频分析插件配置。"""

    config_name: ClassVar[str] = "config"
    config_description: ClassVar[str] = "视频分析插件配置"

    @config_section("general")
    class GeneralSection(SectionBase):
        """通用配置。"""

        cache_ttl_seconds: int = Field(
            default=600,
            description="视频缓存过期时间（秒），默认 10 分钟",
        )
        max_cache_entries: int = Field(
            default=50,
            description="最大缓存条数，超出后按 LRU 顺序逐出",
        )
        compress_threshold_mb: float = Field(
            default=8.0,
            description="超过此大小（MB）才触发 ffmpeg 压缩，默认 8 MB",
        )
        max_video_size_mb: float = Field(
            default=30.0,
            description="压缩后视频的最大允许大小（MB），超过此值将拒绝发送，默认 30 MB",
        )
        heartbeat_interval: float = Field(
            default=3.0,
            description="视频分析期间心跳发送间隔（秒），默认 3 秒",
        )
        compress_target_mb: float = Field(
            default=8.0,
            description="压缩目标大小（MB），ffmpeg 会尽量压缩到此大小，默认 8 MB",
        )
        default_analysis_prompt: str = Field(
            default=(
                "请对这段视频进行极为详尽的内容还原，按以下结构逐项输出，不得省略任何可观察到的信息：\n\n"
                "【基本信息】\n"
                "- 视频总时长（如可判断）\n"
                "- 视频类型：真实拍摄 / CG动画 / 三渲二 / 游戏录像 / 手绘 / 混合等\n"
                "- 画面比例（如 16:9 横屏、9:16 竖屏等）\n"
                "- 水印或来源标识（如有，请逐字转录）\n\n"
                "【画面内容·逐段描述】\n"
                "按时间段（精确到秒，格式：0:00-0:03）描述每个画面段落：\n"
                "- 场景/环境：地点特征（地标建筑、自然景观、室内装饰、光线色调等）\n"
                "- 人物/角色：外貌（发色、发型、肤色）、服饰（颜色、款式、配饰）、面部表情\n"
                "- 动作行为：手势、移动轨迹、互动细节\n"
                "- 画面特效：转场方式、滤镜、叠加动画等\n\n"
                "【文字与字幕】\n"
                "- 所有字幕、标题、对话框、水印、广告文字，请逐字转录，注明出现时间和位置\n\n"
                "【音频内容】\n"
                "- 背景音乐：曲风、节奏、情绪，如能识别曲名或歌手请注明\n"
                "- 歌词（如有）：按时间顺序逐句转录全部可听到的歌词，片段也要完整记录\n"
                "- 人声对话/旁白：逐字转录所有对话，标明说话人（如可辨认）\n"
                "- 音效：环境音、特效音描述\n\n"
                "【叙事与主题】\n"
                "- 核心主题（一句话）\n"
                "- 叙事结构：开头/发展/高潮/结尾\n"
                "- 整体情绪基调\n\n"
                "规则：严格基于直接观察，不推测创作者或拍摄目的。无法判断的项目请注明「无法判断」。"
            ),
            description="默认视频分析提示词",
        )
        default_gallery_prompt: str = Field(
            default=(
                "请对这组图文内容进行极为详尽的描述，按以下结构逐项输出：\n\n"
                "【基本信息】\n"
                "- 图片总数量\n"
                "- 整体风格：真实摄影 / 插画 / CG / 截图 / 混合等\n\n"
                "【逐图描述】\n"
                "对每张图片按编号（第1张、第2张……）描述：\n"
                "- 场景/环境：地点特征、光线色调、构图\n"
                "- 人物/角色：外貌（发色、发型、肤色）、服饰（颜色、款式）、表情\n"
                "- 物品/元素：画面中出现的主要物体、文字、logo\n"
                "- 情绪基调\n\n"
                "【文字内容】\n"
                "- 图片内所有可见文字（标题、正文、水印、标签等），请逐字转录\n\n"
                "【图文关联】\n"
                "- 这组图片的整体主题是什么\n"
                "- 各图之间的逻辑/叙事关系\n\n"
                "规则：严格基于直接观察，不推测创作者意图。无法判断的项目请注明「无法判断」。"
            ),
            description="默认图文（图集）分析提示词",
        )

    general: GeneralSection = Field(default_factory=GeneralSection)
