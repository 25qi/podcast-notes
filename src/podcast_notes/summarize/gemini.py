"""用 Gemini 把一集的內容（逐字稿或音檔）寫成結構化筆記。

這一層只負責「給內容、拿回筆記」，不碰資料庫；寫入 notes、更新狀態是 pipeline/summarize.py 的事。

筆記格式（2026-09-28 Veky 選定「繁中詳細結構化」）對應 notes 表的欄位。
用結構化輸出（JSON schema）而不是請模型寫 markdown 再自己拆：欄位保證齊全、型別正確，
拆 markdown 遇到模型格式稍微不同就會壞。
"""

from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field

# 單次請求上限 10 分鐘（SDK 的 timeout 單位是毫秒）。
# 實測 Gemini 忙碌時曾經一個請求卡 30 分鐘才回，不設上限的話整個任務會被一個請求拖住。
_REQUEST_TIMEOUT_MS = 10 * 60 * 1000

# Gemini 忙碌（503）很常見：2026-09-27 試產 3 份筆記遇到 10 次 503。
# SDK 內建重試，這裡把間隔拉長：30 秒起跳、每次加倍、最多等 5 分鐘、總共試 6 次。
# 不重試 429：免費方案每天 20 次，429 幾乎都是當日額度用完，重試只是浪費時間（見 QuotaExhausted）。
_RETRY = types.HttpRetryOptions(
    attempts=6,
    initial_delay=30,
    max_delay=300,
    http_status_codes=[500, 502, 503, 504],
)

# 上傳的音檔要等 Gemini 處理完（state = ACTIVE）才能用
_FILE_POLL_SECONDS = 5
_FILE_READY_TIMEOUT_SECONDS = 600

_SYSTEM_INSTRUCTION = """\
你是 podcast 筆記助理。使用者會給你一集 podcast 的內容（英文逐字稿或音檔），請寫成繁體中文筆記。

規則：
- 只根據這一集節目的內容寫。節目裡沒講到的事，即使你知道，也不要寫（例如公司市占率、來賓過去經歷）。
- 逐字稿是 Spotify 自動產生的，可能有聽寫錯字、人名拼錯；段落標題可能是講者（Speaker 1）或章節名稱。
  人名、公司名請依上下文判斷正確寫法；不確定就照原文，不要猜。
- 專有名詞（公司、產品、技術名詞、人名）保留英文，第一次出現可以附中文。
- 金句必須是節目裡真的說過的話，original 照原文（可修正明顯的聽寫錯字），translation 翻成中文。
- 沒有來賓的集數（例如主持人自己聊）guest_background 回傳 null。
"""


class _Mention(BaseModel):
    name: str = Field(description="人物或公司名稱，保留英文")
    context: str = Field(description="在節目裡的角色，或被提到的原因（繁體中文，一句話）")


class _Quote(BaseModel):
    original: str = Field(description="節目中的英文原句")
    translation: str = Field(description="繁體中文翻譯")


class NoteContent(BaseModel):
    """筆記內容。欄位對應 notes 表。"""

    one_liner: str = Field(description="一句話總結這集在講什麼")
    guest_background: str | None = Field(
        description="來賓與背景：誰、什麼公司、為什麼值得聽。只寫節目裡提到的。沒有來賓回傳 null"
    )
    summary: str = Field(description="摘要，2～3 段，段落之間用空行分隔")
    key_points: list[str] = Field(description="6～8 個重點，每點 1～2 句，盡量帶具體數字或例子")
    people: list[_Mention] = Field(description="節目中提到的人物")
    companies: list[_Mention] = Field(description="節目中提到的公司與組織")
    quotes: list[_Quote] = Field(description="2～3 句金句")
    follow_up_questions: list[str] = Field(description="1～2 個值得延伸思考的問題")


@dataclass(frozen=True)
class Note:
    content: NoteContent
    model_name: str
    """實際執行的模型版本（例如 gemini-3.8-flash），不是設定裡的別名"""


class QuotaExhausted(Exception):
    """Gemini 回 429：免費方案當日額度（RPD）用完。不是這一集的錯，呼叫端應該停下來明天再跑。"""


class GeminiSummarizer:
    def __init__(self, api_key: str, model: str) -> None:
        self._model = model
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=_REQUEST_TIMEOUT_MS, retry_options=_RETRY),
        )

    def summarize_transcript(self, show: str, title: str, transcript: str) -> Note:
        prompt = f"節目：{show}\n集數標題：{title}\n\n以下是這一集的逐字稿：\n\n{transcript}"
        return self._generate([prompt])

    def summarize_audio(self, show: str, title: str, audio_url: str) -> Note:
        """音檔路線：下載 → 上傳到 Gemini Files API → 寫筆記 → 刪掉上傳的檔案。

        Gemini 不能直接吃外部網址的音檔（官方文件只有 Files API 這條路），所以要先下載再上傳。
        上傳的檔案 48 小時後會自動刪除，這裡用完就主動刪，不佔專案的 20 GB 空間。
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "episode"
            with httpx.stream("GET", audio_url, follow_redirects=True, timeout=120) as r:
                r.raise_for_status()
                # 音檔不一定是 mp3（也可能是 m4a/aac），照伺服器回報的類型告訴 Gemini
                mime_type = r.headers.get("content-type", "").split(";")[0].strip()
                if not mime_type.startswith("audio/"):
                    mime_type = "audio/mpeg"
                with path.open("wb") as f:
                    for chunk in r.iter_bytes():
                        f.write(chunk)
            uploaded = self._client.files.upload(
                file=path, config=types.UploadFileConfig(mime_type=mime_type)
            )

        try:
            uploaded = self._wait_until_active(uploaded)
            prompt = f"節目：{show}\n集數標題：{title}\n\n這一集的內容是附上的音檔。"
            part = types.Part.from_uri(file_uri=uploaded.uri, mime_type=uploaded.mime_type)
            return self._generate([part, prompt])
        finally:
            self._client.files.delete(name=uploaded.name)

    def _wait_until_active(self, file: types.File) -> types.File:
        deadline = time.monotonic() + _FILE_READY_TIMEOUT_SECONDS
        while file.state == types.FileState.PROCESSING:
            if time.monotonic() > deadline:
                raise TimeoutError(f"音檔上傳後 {_FILE_READY_TIMEOUT_SECONDS} 秒還沒處理完")
            time.sleep(_FILE_POLL_SECONDS)
            file = self._client.files.get(name=file.name)
        if file.state != types.FileState.ACTIVE:
            raise RuntimeError(f"音檔處理失敗，狀態：{file.state}")
        return file

    def _generate(self, contents: list) -> Note:
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=_SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    response_json_schema=NoteContent.model_json_schema(),
                ),
            )
        except errors.ClientError as e:
            if e.code == 429:
                raise QuotaExhausted(str(e)) from e
            raise
        # 用 pydantic 驗證一次：模型回傳的 JSON 缺欄位或型別不對，就在這裡擋下，不會寫進資料庫
        content = NoteContent.model_validate_json(response.text)
        return Note(content=content, model_name=response.model_version or self._model)
