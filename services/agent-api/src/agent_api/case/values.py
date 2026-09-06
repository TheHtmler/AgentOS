"""Deterministic equivalence for Case slots, shared by writes and pending reads."""

import re
from decimal import ROUND_HALF_UP, Decimal

_NUMBER_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)(?![\d.])")
_APPROXIMATE_RE = re.compile(r"约|大概|左右|[~≈]|\b(?:about|approximately|approx)\b", re.I)


def _canonical_value(key: str | None, content: str) -> str:
    normalized = " ".join(content.split()).casefold()
    number = _NUMBER_RE.search(normalized)
    if key in {"height_cm", "weight_kg", "age_months"} and number is not None:
        return f"{key}:{Decimal(number.group(1)).normalize()}"
    if key == "sex":
        if "女" in normalized or "female" in normalized:
            return "sex:female"
        if "男" in normalized or "male" in normalized:
            return "sex:male"
    if key == "date_of_birth":
        digits = "".join(char for char in normalized if char.isdigit())
        if len(digits) == 8:
            return f"date_of_birth:{digits}"
    return normalized


def case_slot_values_match(key: str | None, existing: str, candidate: str) -> bool:
    """Treat an explicitly rounded age as a recap, not a new measurement.

    Approximation applies only at the stated age precision. Exact changes and
    measurements such as height/weight never receive this rounding tolerance.
    """

    if _canonical_value(key, existing) == _canonical_value(key, candidate):
        return True
    if key != "age_months":
        return False
    old_numbers = _NUMBER_RE.findall(existing)
    new_numbers = _NUMBER_RE.findall(candidate)
    if len(old_numbers) != 1 or len(new_numbers) != 1:
        return False
    old_value, new_value = Decimal(old_numbers[0]), Decimal(new_numbers[0])
    for text, approximate, precise in (
        (existing, old_value, new_value),
        (candidate, new_value, old_value),
    ):
        if (
            _APPROXIMATE_RE.search(text)
            and abs(precise - approximate) < Decimal("0.5")
            and precise.quantize(approximate, rounding=ROUND_HALF_UP) == approximate
        ):
            return True
    return False
