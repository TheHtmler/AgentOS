"""Private, content-addressed originals and extraction checkpoints for Ops imports."""

import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path

from agent_api.config import get_settings
from agent_api.knowledge.normalize import document_spec_from_payload
from agent_api.knowledge.types import DocumentSpec


def archive_bytes(data: bytes) -> str:
    digest = hashlib.sha256(data).hexdigest()
    root = get_settings().knowledge_source_root
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / digest
    try:
        with path.open("xb") as handle:
            handle.write(data)
        path.chmod(0o600)
    except FileExistsError:
        pass
    return digest


def source_path(digest: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("invalid source digest")
    path = get_settings().knowledge_source_root / digest
    if not path.is_file() or path.is_symlink():
        raise ValueError("原件未保存，请重新上传")
    return path


def save_checkpoint(spec: DocumentSpec) -> str:
    return archive_bytes(json.dumps(asdict(spec), ensure_ascii=False).encode())


def load_checkpoint(digest: str) -> DocumentSpec:
    payload = json.loads(source_path(digest).read_text())
    return document_spec_from_payload(payload)
