from __future__ import annotations

import hmac
import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from tts_filter import delete_entry, load_config, normalize_for_tts, upsert_entry

load_dotenv()
CONFIG_PATH = Path(os.getenv("TTS_FILTER_CONFIG", Path(__file__).resolve().parents[1] / "tts_filter" / "dictionary.yml"))
BEARER_TOKEN = os.getenv("TTS_FILTER_BEARER_TOKEN")

app = FastAPI(title="tts-filter API", version="0.2.0")


class NormalizeRequest(BaseModel):
    text: str = Field(..., min_length=1)
    code_block_mode: Literal["skip", "literal", "ollama-summary"] = "ollama-summary"
    ollama_model: str = "qwen2.5:0.5b"


class NormalizeResponse(BaseModel):
    normalized: str


class DictionaryEntryRequest(BaseModel):
    category: Literal["acronyms", "terms", "extensions"]
    key: str = Field(..., min_length=1)
    value: str = Field(..., min_length=1)


class DictionaryDeleteRequest(BaseModel):
    category: Literal["acronyms", "terms", "extensions"]
    key: str = Field(..., min_length=1)


class DictionaryResponse(BaseModel):
    config: dict[str, object]


class MessageResponse(BaseModel):
    message: str


def require_bearer(authorization: str | None = Header(default=None)) -> None:
    if not BEARER_TOKEN:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="TTS_FILTER_BEARER_TOKEN is not set")
    expected = f"Bearer {BEARER_TOKEN}"
    # タイミング攻撃でトークンを推測されないよう定数時間で比較する
    if not hmac.compare_digest((authorization or "").encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token")


@app.get("/health", response_model=MessageResponse)
def health() -> MessageResponse:
    return MessageResponse(message="ok")


@app.post("/normalize", response_model=NormalizeResponse, dependencies=[Depends(require_bearer)])
def normalize(req: NormalizeRequest) -> NormalizeResponse:
    from tts_filter import TTSFilter

    tts_filter = TTSFilter.from_yaml(CONFIG_PATH)
    tts_filter.code_block_mode = req.code_block_mode
    tts_filter.ollama_model = req.ollama_model
    return NormalizeResponse(normalized=tts_filter.normalize(req.text))


@app.get("/dictionary", response_model=DictionaryResponse, dependencies=[Depends(require_bearer)])
def get_dictionary() -> DictionaryResponse:
    return DictionaryResponse(config=load_config(CONFIG_PATH))


@app.put("/dictionary", response_model=DictionaryResponse, dependencies=[Depends(require_bearer)])
def put_dictionary(req: DictionaryEntryRequest) -> DictionaryResponse:
    return DictionaryResponse(config=upsert_entry(req.category, req.key, req.value, CONFIG_PATH))


@app.patch("/dictionary", response_model=DictionaryResponse, dependencies=[Depends(require_bearer)])
def patch_dictionary(req: DictionaryEntryRequest) -> DictionaryResponse:
    return DictionaryResponse(config=upsert_entry(req.category, req.key, req.value, CONFIG_PATH))


@app.delete("/dictionary", response_model=DictionaryResponse, dependencies=[Depends(require_bearer)])
def remove_dictionary(req: DictionaryDeleteRequest) -> DictionaryResponse:
    return DictionaryResponse(config=delete_entry(req.category, req.key, CONFIG_PATH))
