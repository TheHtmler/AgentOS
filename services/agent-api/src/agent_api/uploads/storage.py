import re
from pathlib import Path
from uuid import UUID


def resolve_stored_upload_path(*, root: Path, meta: dict[str, object] | None) -> Path | None:
    """Resolve ``meta['stored_path']`` under ``root``; reject path escapes."""

    if not isinstance(meta, dict):
        return None
    relative = meta.get("stored_path")
    if not isinstance(relative, str) or not relative.strip():
        return None
    resolved_root = root.resolve()
    path = (resolved_root / relative).resolve()
    try:
        path.relative_to(resolved_root)
    except ValueError:
        return None
    if not path.is_file():
        return None
    return path


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
