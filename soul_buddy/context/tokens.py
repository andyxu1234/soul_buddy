"""Heuristic token estimator (A23 / ADR-009).

Default strategy: no tiktoken dependency (it downloads a BPE vocab at runtime,
which breaks in the PyInstaller package). We estimate per Unicode codepoint
using the empirical rules below; the 25% compaction margin (A13) plus the
official `prompt_tokens` calibration in `context/usage.py` absorb the error.

经验换算（token / 字符）:
    中文汉字            1 字 ≈ 0.6–1.0 token   -> 1.0     （保守取上限）
    英文 / 数字         1 token ≈ 4 字符        -> 0.25
    代码 / JSON 标点    1 token ≈ 3 字符        -> 1/3     （符号切得碎）
    空格 / 换行 / 缩进  一样要算                -> 0.25
    emoji / 特殊符号    1 个 ≈ 1–3 token        -> 2.0
    其它非 ASCII        日文假名 / 西里尔 / 全角标点等 -> 1.0

单调保守：宁可略微高估（早触发压缩）也不要低估撞窗口。真实用量拿到
`prompt_tokens` 后由 `ContextUsageCalculator.calibrate()` 按比例校正。
tiktoken remains an optional enhancement enabled only after the P1.5 Spike proves
it loads in the packaged build.

图片（`estimate_image_tokens`）是另一条口径：视觉模型按**分辨率**计费，与
base64 / 文件字节数无关，详见该函数的 docstring。
"""
from __future__ import annotations

# Per-character weights; kept as named constants so tuning is a single place.
CJK_WEIGHT = 1.0            # 汉字: 0.6–1.0，保守取 1
EMOJI_WEIGHT = 2.0          # emoji / dingbat / 箭头: 1–3，取中值 2
SPACE_WEIGHT = 0.25         # 空白 / 换行 / 缩进
ASCII_ALNUM_WEIGHT = 0.25   # 英文单词 / 数字: ~4 字符/token
ASCII_SYMBOL_WEIGHT = 1 / 3  # 代码 / JSON 标点: ~3 字符/token
OTHER_WEIGHT = 1.0          # 其它非 ASCII

# --- 图片：按分辨率计费 ------------------------------------------------------
IMAGE_PIXELS_PER_TOKEN = 750    # Anthropic 口径 ≈ (w×h)/750
IMAGE_MIN_TOKENS = 85           # OpenAI 低细节下限
IMAGE_TOKEN_FALLBACK = 1_400    # 读不到尺寸时的兜底（≈1MP 截图的量级）
_IMAGE_HEAD_BYTES = 64 * 1024   # 只读图片头部，绝不把整张图读进内存

# 变体选择符 / 零宽连接符（emoji 组合序列里出现，按 emoji 一起计）。
_EMOJI_MODIFIERS = frozenset({0x200D, 0xFE0F})


def _is_cjk(cp: int) -> bool:
    """统一表意文字主区 + 扩展 A + 兼容表意文字（汉字）。"""
    return (0x4E00 <= cp <= 0x9FFF
            or 0x3400 <= cp <= 0x4DBF
            or 0xF900 <= cp <= 0xFAFF)


def _is_emoji(cp: int) -> bool:
    """常见 emoji / 杂项符号 / 箭头 / 变体选择符。"""
    return (0x1F000 <= cp <= 0x1FAFF      # emoji, pictographs, symbols
            or 0x2600 <= cp <= 0x27BF     # misc symbols + dingbats
            or 0x2B00 <= cp <= 0x2BFF     # misc symbols and arrows
            or 0x2190 <= cp <= 0x21FF     # arrows
            or cp in _EMOJI_MODIFIERS)


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    total = 0.0
    for ch in text:
        cp = ord(ch)
        if _is_cjk(cp):
            total += CJK_WEIGHT
        elif _is_emoji(cp):
            total += EMOJI_WEIGHT
        elif ch.isspace():
            total += SPACE_WEIGHT
        elif cp < 128:
            total += (ASCII_ALNUM_WEIGHT if ch.isalnum()
                      else ASCII_SYMBOL_WEIGHT)
        else:
            total += OTHER_WEIGHT
    return int(total) + 1


def estimate_image_tokens(ref: dict) -> int:
    """估算单个 image ref 的 token 数（视觉模型按**分辨率**计费）。

    buffer 里图片只是 ``{"type": "image", "path": ...}`` 的轻引用，base64 只在
    wire 阶段才展开，所以纯文本估算会把整张图算成 0。这里按尺寸补上：

        Anthropic ≈ (w × h) / 750 ; OpenAI 高细节 ≈ 85 + 170 × 512px 分块
    取 Anthropic 口径（偏大），符合本项目「宁可高估不撞窗口」的取向。

    尺寸优先取 ref 里的 ``width``/``height``，否则读文件头（纯 stdlib，不依赖
    Pillow —— 打包环境不引入新依赖）。读不到就回落 ``IMAGE_TOKEN_FALLBACK``。
    """
    w, h = _ref_dimensions(ref)
    if w > 0 and h > 0:
        return max(IMAGE_MIN_TOKENS, round(w * h / IMAGE_PIXELS_PER_TOKEN))
    return IMAGE_TOKEN_FALLBACK


def estimate_images_tokens(messages: list[dict]) -> int:
    """累加整个消息缓冲里所有 image ref 的 token（一条消息可挂多张图）。

    只数图片，不数文字；非视觉模型请由调用方判断后再决定是否计入
    （图片会被降级成一句提示，按整图计费会误触压缩/硬上限）。
    """
    total = 0
    for m in messages:
        if not isinstance(m, dict):
            continue
        content = m.get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if isinstance(b, dict) and b.get("type") == "image":
                total += estimate_image_tokens(b)
    return total


def estimate_messages(messages: list[dict]) -> int:
    """Estimate tokens across an Anthropic/OpenAI-shaped message buffer.

    注意：**不含图片**（图片 token 见 ``estimate_images_tokens``）。文本口径保持
    纯函数、不做 I/O，图片尺寸读取单独走一条路径。
    """
    try:
        import json

        return estimate_tokens(json.dumps(messages, ensure_ascii=False))
    except Exception:
        return estimate_tokens(
            " ".join(str(m.get("content", "")) for m in messages))


# --- image dimension probes (stdlib only) -------------------------------------
# 图片按分辨率计费，而 buffer 里只存了文件路径 —— 只能读文件头拿宽高。
# 只读前 64 KiB（PNG/GIF/BMP/WebP 的尺寸都在最前面，JPEG 需要扫到 SOF 段），
# 任何失败都返回 (0, 0)：估算绝不能因为一张坏图把 session 弄崩。

def _ref_dimensions(ref: dict) -> tuple[int, int]:
    w, h = ref.get("width"), ref.get("height")
    if isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:
        return w, h
    path = ref.get("path")
    return _image_dimensions(path) if isinstance(path, str) and path else (0, 0)


def _image_dimensions(path: str) -> tuple[int, int]:
    try:
        with open(path, "rb") as f:
            head = f.read(_IMAGE_HEAD_BYTES)
    except OSError:
        return 0, 0
    for probe in (_dims_png, _dims_jpeg, _dims_gif, _dims_bmp, _dims_webp):
        try:
            w, h = probe(head)
        except Exception:
            continue
        if w > 0 and h > 0:
            return w, h
    return 0, 0


def _dims_png(b: bytes) -> tuple[int, int]:
    if len(b) < 24 or b[:8] != b"\x89PNG\r\n\x1a\n":
        return 0, 0
    return int.from_bytes(b[16:20], "big"), int.from_bytes(b[20:24], "big")


def _dims_gif(b: bytes) -> tuple[int, int]:
    if len(b) < 10 or b[:6] not in (b"GIF87a", b"GIF89a"):
        return 0, 0
    return (int.from_bytes(b[6:8], "little"),
            int.from_bytes(b[8:10], "little"))


def _dims_bmp(b: bytes) -> tuple[int, int]:
    if len(b) < 26 or b[:2] != b"BM":
        return 0, 0
    return (abs(int.from_bytes(b[18:22], "little", signed=True)),
            abs(int.from_bytes(b[22:26], "little", signed=True)))


def _dims_webp(b: bytes) -> tuple[int, int]:
    if len(b) < 30 or b[:4] != b"RIFF" or b[8:12] != b"WEBP":
        return 0, 0
    fmt = b[12:16]
    if fmt == b"VP8X":                       # 扩展格式：3 字节宽高减一
        return (int.from_bytes(b[24:27], "little") + 1,
                int.from_bytes(b[27:30], "little") + 1)
    if fmt == b"VP8 ":                       # 有损：14 位宽高
        return (int.from_bytes(b[26:28], "little") & 0x3FFF,
                int.from_bytes(b[28:30], "little") & 0x3FFF)
    if fmt == b"VP8L" and len(b) >= 25:      # 无损：位打包，各 14 位
        bits = int.from_bytes(b[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    return 0, 0


def _dims_jpeg(b: bytes) -> tuple[int, int]:
    if len(b) < 4 or b[:2] != b"\xff\xd8":
        return 0, 0
    i, n = 2, len(b)
    while i + 9 < n:
        if b[i] != 0xFF:
            i += 1
            continue
        marker = b[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2                            # 无长度字段的标记（RSTn / SOI）
            continue
        seg_len = int.from_bytes(b[i + 2:i + 4], "big")
        if seg_len < 2:
            return 0, 0
        # SOF0–SOF3 / SOF5–SOF15（0xC4 DHT、0xC8、0xCC 不是 SOF）
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            return (int.from_bytes(b[i + 7:i + 9], "big"),
                    int.from_bytes(b[i + 5:i + 7], "big"))
        i += 2 + seg_len
    return 0, 0
