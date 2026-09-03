from __future__ import annotations

DEFAULT_NVENC_CQ = 18
DEFAULT_X264_CRF = 18


def select_automatic_video_codec(*, nvenc_available: bool) -> str:
    """Choose the production encoder from the probed runtime capability."""
    return "h264_nvenc" if nvenc_available else "libx264"


def build_video_encoding_args(
    video_codec: str,
    nvenc_preset: str = "p5",
    nvenc_cq: int = DEFAULT_NVENC_CQ,
    x264_crf: int = DEFAULT_X264_CRF,
) -> list[str]:
    if video_codec.endswith("_nvenc"):
        if not 0 <= nvenc_cq <= 51:
            raise ValueError("nvenc_cq must be between 0 and 51.")
        args = [
            "-preset", nvenc_preset,
            "-tune", "hq",
            "-rc", "vbr",
            "-cq", str(nvenc_cq),
            "-b:v", "0",
            "-multipass", "qres",
            "-spatial-aq", "1",
            "-temporal-aq", "1",
            "-aq-strength", "8",
        ]
        if video_codec == "h264_nvenc":
            args.extend(["-profile:v", "high"])
        return args
    if video_codec == "libx264":
        if not 0 <= x264_crf <= 51:
            raise ValueError("x264_crf must be between 0 and 51.")
        return ["-preset", "medium", "-crf", str(x264_crf), "-profile:v", "high"]
    return []
