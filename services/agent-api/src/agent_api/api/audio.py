"""Authenticated, non-persistent speech-to-text proxy for chat input."""

from typing import Annotated, cast

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from agent_api.api.auth import get_current_user
from agent_api.config import Settings, get_settings
from agent_api.db.models import User

router = APIRouter(prefix="/v1/audio", tags=["audio"])

_ALLOWED_MIME_TYPES = frozenset(
    {
        "audio/webm",
        "audio/ogg",
        "audio/mpeg",
        "audio/mp4",
        "audio/wav",
        "audio/x-wav",
    }
)


class TranscriptionResponse(BaseModel):
    """The text returned to the browser; audio is intentionally not persisted."""

    text: str


def _asr_unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="Speech recognition is not configured")


async def transcribe_audio(
    *, data: bytes, filename: str, mime_type: str, settings: Settings
) -> str:
    """Forward a bounded audio blob to an OpenAI-compatible transcription endpoint."""

    if not settings.asr_enabled or not settings.resolved_asr_base_url or not settings.asr_model:
        raise _asr_unavailable()

    headers = {"Authorization": f"Bearer {settings.asr_api_key}"} if settings.asr_api_key else {}
    try:
        async with httpx.AsyncClient(timeout=settings.asr_timeout_seconds) as client:
            response = await client.post(
                f"{settings.resolved_asr_base_url}/audio/transcriptions",
                headers=headers,
                data={"model": settings.asr_model},
                files={"file": (filename, data, mime_type)},
            )
    except httpx.HTTPError as error:
        raise HTTPException(
            status_code=502, detail="Speech recognition service is unavailable"
        ) from error

    if response.is_error:
        raise HTTPException(status_code=502, detail="Speech recognition service rejected audio")

    try:
        payload = cast(dict[str, object], response.json())
    except ValueError as error:
        raise HTTPException(
            status_code=502, detail="Speech recognition service returned invalid data"
        ) from error

    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=502, detail="Speech recognition service returned no text")

    return text.strip()


@router.post("/transcriptions", response_model=TranscriptionResponse)
async def post_transcription(
    user: Annotated[User, Depends(get_current_user)],
    file: Annotated[UploadFile, File()],
) -> TranscriptionResponse:
    """Transcribe one browser recording without writing its bytes to storage."""

    del user  # Authentication is the only ownership boundary for transient audio.
    settings = get_settings()
    mime_type = (file.content_type or "").split(";", 1)[0].strip().lower()
    if mime_type not in _ALLOWED_MIME_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported audio format")

    data = await file.read(settings.asr_max_bytes + 1)
    if len(data) > settings.asr_max_bytes:
        raise HTTPException(status_code=400, detail=f"Audio exceeds {settings.asr_max_bytes} bytes")

    return TranscriptionResponse(
        text=await transcribe_audio(
            data=data,
            filename=file.filename or "recording.webm",
            mime_type=mime_type,
            settings=settings,
        )
    )
