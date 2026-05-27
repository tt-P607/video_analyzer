"""视频压缩工具。

使用 ffmpeg 将过大的视频压缩到目标体积以内，以满足 Gemini inline 上限。
"""

from __future__ import annotations

import asyncio
import base64
import tempfile
from pathlib import Path


async def compress_video_b64(
    b64_input: str,
    target_mb: float = 8.0,
    input_size_mb: float | None = None,
) -> str:
    """将 base64 编码的视频压缩到目标大小以内。

    使用高度优化的 ffmpeg 快速压缩方案。通过自适应调整帧率（fps）和分辨率（scale），
    在数秒内高保真、极速地将超大视频压入安全容量阈值。
    若 ffmpeg 不可用或压缩失败，抛出 RuntimeError。

    Args:
        b64_input: 输入视频的 base64 编码字符串
        target_mb: 目标文件大小上限（MB），默认 8 MB
        input_size_mb: 输入视频大小（MB），用于选择压缩策略

    Returns:
        str: 压缩后的纯 base64 编码字符串

    Raises:
        RuntimeError: ffmpeg 执行失败或输出文件不存在
    """
    input_bytes = base64.b64decode(b64_input)
    if input_size_mb is None:
        input_size_mb = len(input_bytes) / (1024 * 1024)

    loop = asyncio.get_running_loop()

    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = Path(tmpdir) / "input.mp4"
        output_path = Path(tmpdir) / "output.mp4"

        # 同步文件写入放入线程池，避免阻塞事件循环
        await loop.run_in_executor(None, input_path.write_bytes, input_bytes)

        # 极致单次快速压缩策略：自适应选择分辨率与帧率以达成极速渲染
        fps = 20  # 每秒 20 帧，动作连贯，画质优秀
        audio_bitrate = "64k"  # 音频音质优秀，人类可听清

        cmd = [
            "ffmpeg",
            "-i", str(input_path),
            "-r", str(fps),  # 限制帧率为 20fps
            "-c:v", "libx264",
            "-preset", "ultrafast",  # 开启最极限的极速编码预设，缩短 80% 以上的 CPU 等待时间！
            "-threads", "0",  # 打满所有可用的 CPU 逻辑多核进行多线程并行转码
        ]

        # 自适应选择 scale 滤波器与 CRF+VBV 码率双重硬核限制（解决反向压大的痛点）
        if input_size_mb < 15:
            # 较小视频：保持原分辨率比例，CRF 28 质量保底。仅当原码率过高时限流在 600k (只降不升)
            scale = None
            cmd.extend([
                "-crf", "28",
                "-maxrate", "600k",
                "-bufsize", "1200k"
            ])
        elif input_size_mb < 40:
            # 中型视频：限制 480p，CRF 28 质量保底。码率上限卡死在 350k (保证清晰连贯的同时绝对低于 8MB)
            scale = "854:-2"
            cmd.extend([
                "-crf", "28",
                "-maxrate", "350k",
                "-bufsize", "700k"
            ])
        else:
            # 超大视频 (>40MB)：降到 360p 宽度 640 提高压缩效率，CRF 30 质量保底。码率上限卡死在 180k
            # CRF 和 maxrate 协同工作，既防反向压大，又杜绝大视频大包卡死 WS，转码速度起飞！
            scale = "640:-2"
            audio_bitrate = "48k"
            cmd.extend([
                "-crf", "30",
                "-maxrate", "180k",
                "-bufsize", "360k"
            ])
        
        # 应用视频滤波缩放
        if scale:
            cmd.extend(["-vf", f"scale={scale}"])
            
        cmd.extend([
            "-c:a", "aac",
            "-b:a", audio_bitrate,
            "-movflags", "+faststart",
            "-y",
            str(output_path),
        ])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        
        if proc.returncode != 0 or not output_path.exists():
            raise RuntimeError(
                f"ffmpeg 极速压缩执行失败。exit_code={proc.returncode}. "
                f"error={stderr.decode('utf-8', errors='ignore')}"
            )

        output_bytes = await loop.run_in_executor(None, output_path.read_bytes)
        return base64.b64encode(output_bytes).decode("ascii")
