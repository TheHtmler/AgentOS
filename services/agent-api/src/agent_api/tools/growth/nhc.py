"""NHC WS/T 423-2022 growth assessment via SD-table interpolation."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from agent_api.tools.growth.sd_table import SdRow, SdValues, value_to_z_sd_table

Sex = Literal["male", "female"]
IndicatorKey = Literal[
    "height-for-age",
    "weight-for-age",
    "bmi-for-age",
    "weight-for-length",
    "weight-for-height",
]

SOURCE_URL = "https://www.nhc.gov.cn/wjw/c100311/202211/923e7646561d4b88b72da9097d4da4d5.shtml"
STANDARD_ID = "nhc-wst-423-2022"
STANDARD_VERSION = "2022"
DAYS_PER_MONTH = 365.25 / 12.0

_SERVICE_ROOT = Path(__file__).resolve().parents[4]
_DATA_DIR = _SERVICE_ROOT / "seed" / "growth" / "nhc"

_INDICATOR_OUTPUT_LABELS: dict[IndicatorKey, str] = {
    "height-for-age": "length_height_for_age",
    "weight-for-age": "weight_for_age",
    "bmi-for-age": "bmi_for_age",
    "weight-for-length": "weight_for_length_height",
    "weight-for-height": "weight_for_length_height",
}


@dataclass(frozen=True, slots=True)
class NhcIndicatorResult:
    indicator: str
    z_score: float
    percentile: float


@dataclass(frozen=True, slots=True)
class NhcAssessment:
    standard: str
    source_url: str
    standard_version: str
    sex: Sex
    age_months: float
    height_cm: float | None
    weight_kg: float | None
    indicators: list[NhcIndicatorResult]
    warnings: list[str]
    errors: list[str]


def find_nearest_row(rows: list[SdRow], target_x: float) -> SdRow:
    if not rows:
        raise ValueError("Cannot select a row from an empty dataset")

    nearest = rows[0]
    nearest_distance = abs(nearest.x - target_x)
    for row in rows[1:]:
        distance = abs(row.x - target_x)
        if distance < nearest_distance:
            nearest = row
            nearest_distance = distance
    return nearest


def is_within_indicator_range(rows: list[SdRow], value: float) -> bool:
    if not rows:
        return False
    return rows[0].x <= value <= rows[-1].x


@lru_cache(maxsize=32)
def _load_indicator_rows(indicator: IndicatorKey, sex: Sex) -> tuple[SdRow, ...]:
    csv_path = _DATA_DIR / f"{indicator}-{sex}.csv"
    if not csv_path.is_file():
        raise FileNotFoundError(f"Missing NHC table: {csv_path.name}")

    rows: list[SdRow] = []
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for record in reader:
            rows.append(
                SdRow(
                    x=float(record["x"]),
                    sds=SdValues(
                        neg3=float(record["neg3"]),
                        neg2=float(record["neg2"]),
                        neg1=float(record["neg1"]),
                        median=float(record["median"]),
                        pos1=float(record["pos1"]),
                        pos2=float(record["pos2"]),
                        pos3=float(record["pos3"]),
                    ),
                )
            )

    rows.sort(key=lambda row: row.x)
    return tuple(rows)


def _pick_weight_by_size_indicator(age_months: float, height_cm: float, sex: Sex) -> IndicatorKey | None:
    preferred: tuple[IndicatorKey, ...] = (
        ("weight-for-length", "weight-for-height")
        if age_months < 24
        else ("weight-for-height", "weight-for-length")
    )
    for indicator in preferred:
        rows = list(_load_indicator_rows(indicator, sex))
        if is_within_indicator_range(rows, height_cm):
            return indicator
    return None


def _assess_indicator(
    *,
    indicator: IndicatorKey,
    sex: Sex,
    source_x: float,
    measurement: float,
) -> NhcIndicatorResult | None:
    rows = list(_load_indicator_rows(indicator, sex))
    if not is_within_indicator_range(rows, source_x):
        return None

    row = find_nearest_row(rows, source_x)
    z_score = value_to_z_sd_table(measurement, row)
    # Local import avoids circular dependency with tool.py.
    from agent_api.tools.growth.tool import z_to_percentile  # noqa: PLC0415

    return NhcIndicatorResult(
        indicator=_INDICATOR_OUTPUT_LABELS[indicator],
        z_score=round(z_score, 2),
        percentile=z_to_percentile(z_score),
    )


def assess_nhc(
    *,
    sex: Sex,
    age_months: float,
    height_cm: float | None = None,
    weight_kg: float | None = None,
) -> NhcAssessment:
    """Assess child growth against NHC WS/T 423-2022 (nearest age row + SD interpolation)."""

    warnings: list[str] = []
    errors: list[str] = []
    indicators: list[NhcIndicatorResult] = []

    if height_cm is not None:
        result = _assess_indicator(
            indicator="height-for-age",
            sex=sex,
            source_x=age_months,
            measurement=height_cm,
        )
        if result is None:
            errors.append("length_height_for_age is outside the supported age range for NHC WS/T 423-2022")
        else:
            indicators.append(result)

    if weight_kg is not None:
        result = _assess_indicator(
            indicator="weight-for-age",
            sex=sex,
            source_x=age_months,
            measurement=weight_kg,
        )
        if result is None:
            errors.append("weight_for_age is outside the supported age range for NHC WS/T 423-2022")
        else:
            indicators.append(result)

        if height_cm is not None:
            bmi = weight_kg / (height_cm / 100.0) ** 2
            bmi_result = _assess_indicator(
                indicator="bmi-for-age",
                sex=sex,
                source_x=age_months,
                measurement=bmi,
            )
            if bmi_result is None:
                errors.append("bmi_for_age is outside the supported age range for NHC WS/T 423-2022")
            else:
                indicators.append(bmi_result)

            size_indicator = _pick_weight_by_size_indicator(age_months, height_cm, sex)
            if size_indicator is None:
                warnings.append(
                    "weight_for_length_height unavailable: height outside NHC weight-for-length/height range"
                )
            else:
                size_result = _assess_indicator(
                    indicator=size_indicator,
                    sex=sex,
                    source_x=height_cm,
                    measurement=weight_kg,
                )
                if size_result is None:
                    warnings.append("weight_for_length_height could not be computed for this height")
                else:
                    indicators.append(size_result)

    return NhcAssessment(
        standard=STANDARD_ID,
        source_url=SOURCE_URL,
        standard_version=STANDARD_VERSION,
        sex=sex,
        age_months=round(age_months, 2),
        height_cm=height_cm,
        weight_kg=weight_kg,
        indicators=indicators,
        warnings=warnings,
        errors=errors,
    )
