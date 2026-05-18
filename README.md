# Video Analyzer Plugin for Neo-MoFox

一个为 Neo-MoFox 聊天机器人框架设计的视频分析插件。该插件允许 LLM 主动通过多模态模型（如 Gemini 2.0 Pro/Flash, GPT-4o 等）分析用户发送的视频内容或网页链接中的视频/图文内容。

## 功能特性

- **消息视频分析**：实时监测聊天中的视频消息，LLM 可以通过 `analyze_video` 工具随时“观看”视频。
- **直链解析抓取**：支持 B 站（bilibili.com, b23.tv）和抖音（douyin.com, v.douyin.com）的链接自动解析、下载并分析。
- **多模态支持**：支持视频（MP4, FLV, WebP, GIF）和抖音图文（图集）分析。
- **自动压缩**：当视频文件过大时，插件会自动调用 `ffmpeg` 进行压缩以符合 LLM API 的大小限制。
- **智能缓存**：对已分析过的视频内容进行持久化缓存，避免重复调用消耗 Token。
- **提示词优化**：内置详尽的视频/图文分析提示词模板，确保模型输出高质量的内容摘要。

## 安装

作为 Neo-MoFox 插件安装，将此目录放置在 `plugins/video_analyzer` 即可。

### 依赖项

- **系统依赖**：`ffmpeg` (用于视频压缩和格式转换)
- **Python 依赖**：
  - `aiohttp`

## 配置说明

插件配置位于 `config/video_analyzer.toml` (加载后生成)：

- `cache_ttl_seconds`: 内存视频缓存的过期时间（默认 600 秒）。
- `compress_threshold_mb`: 触发压缩的文件大小阈值（默认 18 MB）。
- `default_analysis_prompt`: 发送给模型的视频分析指令。

## 工具使用

插件向 LLM 暴露以下工具：

1. **`analyze_video(stream_id)`**：分析当前会话中已经收到的视频。
2. **`fetch_and_analyze_video(url)`**：抓取并分析外部链接。

## 开源协议

AGPL-v3.0
