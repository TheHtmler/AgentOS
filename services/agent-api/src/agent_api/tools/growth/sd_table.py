"""SD-table piecewise linear interpolation (ported from groowooth sd-table.ts)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class SdValues:
    neg3: float
    neg2: float
    neg1: float
    median: float
    pos1: float
    pos2: float
    pos3: float


@dataclass(frozen=True, slots=True)
class SdRow:
    x: float
    sds: SdValues


def _to_points(row: SdRow) -> list[tuple[float, float]]:
    return [
        (-3.0, row.sds.neg3),
        (-2.0, row.sds.neg2),
        (-1.0, row.sds.neg1),
        (0.0, row.sds.median),
        (1.0, row.sds.pos1),
        (2.0, row.sds.pos2),
        (3.0, row.sds.pos3),
    ]


def value_to_z_sd_table(value: float, row: SdRow) -> float:
    """Convert a measurement to z-score via piecewise linear SD interpolation."""

    if not isinstance(value, (int, float)) or value != value:  # NaN check
        raise ValueError("value must be a finite number")

    points = _to_points(row)
    first_z, first_value = points[0]
    last_z, last_value = points[-1]

    if value <= first_value:
        return -3.0
    if value >= last_value:
        return 3.0

    for (lower_z, lower_value), (upper_z, upper_value) in zip(points, points[1:]):
        if lower_value <= value <= upper_value:
            if upper_value == lower_value:
                return lower_z
            return lower_z + (value - lower_value) * (upper_z - lower_z) / (
                upper_value - lower_value
            )

    raise ValueError("value does not fit within the SD table ordering")


def sd_values_from_mapping(raw: Mapping[str, float]) -> SdValues:
    return SdValues(
        neg3=float(raw["neg3"]),
        neg2=float(raw["neg2"]),
        neg1=float(raw["neg1"]),
        median=float(raw["median"]),
        pos1=float(raw["pos1"]),
        pos2=float(raw["pos2"]),
        pos3=float(raw["pos3"]),
    )
