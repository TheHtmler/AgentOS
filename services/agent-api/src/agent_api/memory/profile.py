"""Profile slot keys and human-readable rendering for always-on memory."""

from __future__ import annotations

from typing import Any, cast

PROFILE_KEYS: tuple[str, ...] = (
    "height_cm",
    "weight_kg",
    "sex",
    "date_of_birth",
    "age_months",
)

_PROFILE_KEY_SET = frozenset(PROFILE_KEYS)

_SEX_LABELS = {
    "m": "男",
    "male": "男",
    "boy": "男",
    "男": "男",
    "男孩": "男",
    "f": "女",
    "female": "女",
    "girl": "女",
    "女": "女",
    "女孩": "女",
}


def normalize_profile_value(key: str, value: object) -> tuple[str, list[str], str] | None:
    """Return ``(key, tags, content)`` for a valid profile slot, else None."""

    if key not in _PROFILE_KEY_SET or value is None:
        return None

    if key == "height_cm":
        number = _as_positive_float(value)
        if number is None:
            return None
        return key, ["身高"], f"身高 {number:g}cm"

    if key == "weight_kg":
        number = _as_positive_float(value)
        if number is None:
            return None
        return key, ["体重"], f"体重 {number:g}kg"

    if key == "sex":
        if not isinstance(value, str):
            return None
        label = _SEX_LABELS.get(value.strip().lower())
        if label is None:
            return None
        return key, ["性别"], f"性别 {label}"

    if key == "date_of_birth":
        if not isinstance(value, str) or not value.strip():
            return None
        return key, ["生日"], f"出生日期 {value.strip()}"

    if key == "age_months":
        number = _as_positive_float(value)
        if number is None:
            return None
        return key, ["月龄"], f"月龄 {number:g}"

    return None


def coerce_profile_dict(raw: object) -> dict[str, Any]:
    """Keep only known profile keys from model JSON."""

    if not isinstance(raw, dict):
        return {}
    profile: dict[str, Any] = {}
    for key_obj, value in cast(dict[object, object], raw).items():
        if isinstance(key_obj, str) and key_obj in _PROFILE_KEY_SET and value is not None:
            profile[key_obj] = value
    return profile


def _as_positive_float(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if number <= 0 or number != number:  # NaN
        return None
    return number
