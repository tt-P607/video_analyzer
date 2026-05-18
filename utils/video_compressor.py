"""视频压缩工具。

使用 ffmpeg 将过大的视频压缩到目标体积以内，以满足 Gemini inline 上限。
"""

from __future__ import annotations

import asyncio
import base64
import tempfile
from pathlib import Path


async def compress_video_b64(b64_input: str, target_mb: float = 15.0) -> str:
    """将 base64 编码的视频压缩到目标大小以内。

    使用 ffmpeg CRF 模式压缩视频，适合快速降低体积。
    若 ffmpeg 不可用或压缩失败，抛出 RuntimeError。

    Args:
        b64_input: 输入视频的 base64 编码字符串
        target_mb: 目标文件大小上限（MB），默认 15 MB

    Returns:
        str: 压缩后的纯 base64 编码字符串

    Raises:
        RuntimeError: ffmpeg 执行失败或输出文件不存在
    """
    input_bytes = base64.b64decode(b64_input)

    loop = asyncio.get_running_loop()

    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = Path(tmpdir) / "input.mp4"
        output_path = Path(tmpdir) / "output.mp4"

        # 同步文件写入放入线程池，避免阻塞事件循环
        await loop.run_in_executor(None, input_path.write_bytes, input_bytes)

        async def _run_ffmpeg(crf: int) -> bytes | None:
            """执行 ffmpeg 压缩，返回输出字节；失败返回 None。"""
            if output_path.exists():
                output_path.unlink(missing_ok=True)
            cmd = [
                "ffmpeg",
                "-i", str(input_path),
                "-c:v", "libx264",
                "-crf", str(crf),
                "-c:a", "aac",
                "-b:a", "64k",
                "-movflags", "+faststart",
                "-y",
                str(output_path),
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0 or not output_path.exists():
                return None
            return await loop.run_in_executor(None, output_path.read_bytes)

        output_bytes = await _run_ffmpeg(28)
        if output_bytes is None:
            raise RuntimeError("ffmpeg 压缩失败，输出文件不存在或 ffmpeg 返回非零退出码")

        actual_mb = len(output_bytes) / (1024 * 1024)
        if actual_mb > target_mb:
            # 压缩后仍超过目标，尝试更激进的 CRF
            output_bytes_35 = await _run_ffmpeg(35)
            if output_bytes_35 is not None:
                output_bytes = output_bytes_35

        return base64.b64encode(output_bytes).decode("ascii")
