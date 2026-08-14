import re
from pathlib import Path
from uuid import UUID


def _safe_filename(filename: str) -> str:
    """Return a basename safe for local disk storage."""

    name = Path(filename or "upload").name.replace("\x00", "")
    sanitized = re.sub(r"[^\w.\- ]", "_", name, flags=re.UNICODE).strip(". ")
    return sanitized or "upload"


def store_upload(
    *,
    root: Path,
    owner_user_id: UUID,
    artifact_id: UUID,
    filename: str,
    data: bytes,
) -> Path:
    """Persist upload bytes under ``root/{owner}/{artifact_id}/{safe_filename}``."""

    target_dir = (root / str(owner_user_id) / str(artifact_id)).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    stored_path = (target_dir / _safe_filename(filename)).resolve()
    stored_path.write_bytes(data)
    return stored_path
