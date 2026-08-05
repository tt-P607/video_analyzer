# Video Analyzer Plugin for Neo-MoFox

一个为 Neo-MoFox 聊天机器人框架设计的视频分析插件。该插件允许 LLM 主动通过多模态模型（如 Gemini 2.0 Pro/Flash, GPT-4o 等）分析用户发送的视频内容或网页链接中的视频/图文内容。

## 功能特性

- **消息视频分析**：实时监测聊天中的视频消息，LLM 可以通过 `analyze_video` 工具随时“观看”视频。
- **直链解析抓取**：支持 B 站（bilibili.com, b23.tv）和抖音（douyin.com, v.douyin.com）的链接自动解析、下载并分析。
- **多模态支持**：支持视频（MP4, FLV, WebP, GIF）和抖音图文（图集）分析。
- **自动压缩**：当视频文件过大时，插件会自动调用 `ffmpeg` 进行压缩以符合 LLM API 的大小限制。
- **框架级缓存**：分析结果写入框架数据库（`VideoDescriptions` 表），已识别视频的同一内容再次出现时，描述会自动进入 LLM 上下文（`[视频(video_id):描述]`），避免重复消耗 Token。
- **按 media_id 引用**：基于框架的 `video_id`（SHA256）体系，LLM 可精确引用历史视频。
- **提示词优化**：内置详尽的视频/图文分析提示词模板，确保模型输出高质量的内容摘要。

## 安装

作为 Neo-MoFox 插件安装，将此目录放置在 `plugins/video_analyzer` 即可。

### 依赖项

- **系统依赖**：`ffmpeg` (用于视频压缩和格式转换)
- **Python 依赖**：
  - `aiohttp`
- **框架 API**：依赖 `media_api`（≥ 1.2.0），提供 `save_description_cache` / `get_media_file` 能力

## 配置说明

插件配置位于 `config/video_analyzer.toml` (加载后生成)：

- `cache_ttl_seconds`: 视频缓存过期时间（默认 600 秒）。
- `compress_threshold_mb`: 触发压缩的文件大小阈值（默认 8 MB）。
- `default_analysis_prompt`: 发送给模型的视频分析指令。
- `default_gallery_prompt`: 发送给模型的图文（图集）分析指令。

## 工具使用

插件向 LLM 暴露以下工具：

1. **`analyze_video(video_id)`**：分析当前消息中已经收到的视频，按 `video_id` 从框架媒体缓存读取视频文件。
2. **`fetch_and_analyze_video(url)`**：抓取并分析外部链接。

## 缓存机制

- 收到消息中的视频由框架自动落盘入库（`data/media_cache/videos/`），插件不重复存储二进制。
- `analyze_video` 分析完成后，描述写入框架 `VideoDescriptions` 缓存表与 `Videos` 业务表。
- 同一视频内容（相同 `video_id`）再次出现时，converter 命中缓存，描述直接进入上下文。
- 外部链接视频只保存描述（按内容哈希），不持久化视频文件。

## 开源协议

AGPL-v3.0
