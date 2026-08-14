import httpx

from agent_api.config import Settings
from agent_api.knowledge.ocr_client import ocr_image_bytes
from agent_api.knowledge.pdf_extract import extract_pdf_text

_IMAGE_MIME_TYPES = frozenset(
    {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/webp",
    }
)


async def extract_upload_text(
    *,
    data: bytes,
    filename: str,
    mime_type: str,
    client: httpx.AsyncClient,
    settings: Settings,
) -> tuple[str, dict[str, int]]:
    """Extract plain text from an uploaded image or PDF."""

    normalized_mime = mime_type.strip().lower()

    if normalized_mime == "application/pdf":
        text, text_layer_pages, ocr_pages = await extract_pdf_text(
            data,
            client=client,
            settings=settings,
        )
        return text, {"ocr_pages": ocr_pages, "text_layer_pages": text_layer_pages}

    if normalized_mime in _IMAGE_MIME_TYPES:
        text = await ocr_image_bytes(data, client=client, settings=settings)
        return text, {"ocr_pages": 1, "text_layer_pages": 0}

    raise ValueError(f"不支持的上传类型：{mime_type or filename}")
