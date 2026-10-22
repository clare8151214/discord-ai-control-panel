"""Edge-TTS 文字轉語音層。

使用微軟 Edge 的免費雲端 TTS（edge-tts 套件），不佔本機 GPU。
之後若要換成本地模型（GPT-SoVITS 等），只需替換 synthesize() 的實作。
"""
from __future__ import annotations

import re

import edge_tts


DEFAULT_TTS_VOICE = "zh-TW-HsiaoChenNeural"

# 精選常用聲音；/tts voice 的自動完成選單使用，實際可用聲音以 Edge 完整清單為準。
RECOMMENDED_VOICES: dict[str, str] = {
    "zh-TW-HsiaoChenNeural": "台灣中文・女聲（預設）",
    "zh-TW-HsiaoYuNeural": "台灣中文・女聲（軟）",
    "zh-TW-YunJheNeural": "台灣中文・男聲",
    "zh-CN-XiaoxiaoNeural": "中國普通話・女聲",
    "zh-CN-YunxiNeural": "中國普通話・男聲",
    "ja-JP-NanamiNeural": "日語・女聲",
    "en-US-JennyNeural": "英語・女聲",
    "en-US-GuyNeural": "英語・男聲",
}

CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`([^`\n]*)`")
URL_RE = re.compile(r"https?://\S+")
DISCORD_EMOJI_RE = re.compile(r"<a?:\w+:\d+>")
MARKDOWN_MARKS_RE = re.compile(r"[*_~#>|]+")
# 常見表情符號區段；Edge TTS 有時會把 emoji 念成描述文字，直接移除。
EMOJI_RE = re.compile(
    "["
    "\U0001f000-\U0001faff"
    "\U00002600-\U000027bf"
    "\U0001f1e6-\U0001f1ff"
    "️‍"
    "]+"
)


DIALOGUE_RE = re.compile(
    r"「([^」]*)」|『([^』]*)』|“([^”]*)”|＂([^＂]*)＂|\"([^\"\n]*)\""
)
ACTION_RE = re.compile(r"\*[^*\n]+\*|（[^）\n]*）|\([^)\n]*\)")


class TTSError(Exception):
    """語音合成失敗。"""


def extract_dialogue(text: str) -> str:
    """只取出引號內的台詞（「」『』“”＂＂""），依原文順序以句號串接。

    回覆完全沒有引號台詞時回傳空字串，呼叫端應跳過朗讀。
    """
    segments = [
        group.strip()
        for match in DIALOGUE_RE.finditer(text)
        for group in match.groups()
        if group and group.strip()
    ]
    parts = []
    for segment in segments:
        if segment[-1] not in "。！？，；…～.!?,;~":
            segment += "。"
        parts.append(segment)
    return "".join(parts)


def strip_actions(text: str) -> str:
    """移除 *動作*、（旁白）等描述標記，保留其餘內容照唸。"""
    return ACTION_RE.sub("", text)


def clean_for_speech(text: str, max_chars: int) -> str:
    """把 Discord 回覆整理成適合朗讀的純文字，過長時在句尾截斷。"""
    text = CODE_BLOCK_RE.sub("，程式碼內容略過，", text)
    text = INLINE_CODE_RE.sub(r"\1", text)
    text = URL_RE.sub("", text)
    text = DISCORD_EMOJI_RE.sub("", text)
    text = EMOJI_RE.sub("", text)
    text = MARKDOWN_MARKS_RE.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text).strip()
    if len(text) <= max_chars:
        return text
    shortened = text[:max_chars]
    split_at = max(
        shortened.rfind("。"),
        shortened.rfind("！"),
        shortened.rfind("？"),
        shortened.rfind("\n"),
    )
    if split_at >= max_chars // 2:
        shortened = shortened[: split_at + 1]
    return f"{shortened.rstrip()}，以下省略，完整內容請看文字訊息。"


async def synthesize(text: str, voice: str = DEFAULT_TTS_VOICE) -> bytes:
    """把文字合成為 mp3 bytes。"""
    if not text.strip():
        raise TTSError("沒有可朗讀的文字內容")
    communicate = edge_tts.Communicate(text=text, voice=voice)
    audio = bytearray()
    try:
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                audio.extend(chunk["data"])
    except Exception as exc:  # edge-tts 會拋出多種網路／協定錯誤
        raise TTSError(f"Edge TTS 合成失敗：{exc}") from exc
    if not audio:
        raise TTSError("Edge TTS 沒有回傳任何音訊資料")
    return bytes(audio)


_VOICES_CACHE: list[dict] | None = None


async def resolve_voice(name: str) -> str | None:
    """驗證聲音名稱並回傳正式 ShortName；查無此聲音時回傳 None。

    若無法取得 Edge 完整聲音清單（例如暫時斷網），退回精選清單比對；
    仍找不到就原樣接受，讓實際合成時再回報錯誤。
    """
    global _VOICES_CACHE
    wanted = name.strip().lower()
    if not wanted:
        return None
    try:
        if _VOICES_CACHE is None:
            _VOICES_CACHE = await edge_tts.list_voices()
        for item in _VOICES_CACHE:
            short_name = str(item.get("ShortName", ""))
            if short_name.lower() == wanted:
                return short_name
        return None
    except Exception:
        for short_name in RECOMMENDED_VOICES:
            if short_name.lower() == wanted:
                return short_name
        return name.strip()
