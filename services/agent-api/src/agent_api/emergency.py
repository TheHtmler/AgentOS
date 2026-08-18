"""Deterministic detection of pediatric red-flag symptoms in a user message.

Deliberately code-level and independent of model compliance: acute
decompensation is a documented high-risk scenario for this program's MMA/PA
population (see the "急性失代偿" knowledge chunk and docs/13's "高风险：急性
症状…→升级就医" requirement), and prompt-only guidance cannot guarantee the
escalation notice survives an 8B model's own answer. Patterns are deliberately
narrow — common, low-specificity symptoms (发热/咳嗽 alone) are excluded so a
routine cold does not trigger this on every turn.
"""

from __future__ import annotations

import re

EMERGENCY_NOTICE = (
    "检测到您描述的情况包含可能的紧急信号，建议尽快带孩子就医或联系急诊评估。"
    "以下内容仅作参考，不能替代现场医疗判断——"
)

_EMERGENCY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "consciousness",
        re.compile(r"叫不醒|昏迷|昏睡不醒|意识不清|意识改变|意识障碍"),
    ),
    ("seizure", re.compile(r"抽搐|惊厥|抽风")),
    (
        "breathing",
        re.compile(r"呼吸困难|呼吸急促|呼吸费力|呼吸深快|喘不过气|呼吸不畅"),
    ),
    ("circulation", re.compile(r"嘴唇发紫|口唇发绀|皮肤发紫|休克")),
    (
        "gi_persistent",
        re.compile(
            r"呕吐不止|持续呕吐|一直呕吐|反复呕吐|长时间拒食|拒食.{0,10}嗜睡|嗜睡.{0,10}拒食"
        ),
    ),
    ("metabolic_specific", re.compile(r"反应差|反应低下|精神明显变差|精神状态很差")),
)


def detect_emergency_signal(text: str) -> str | None:
    """Return the matched red-flag category, or ``None`` when nothing matches.

    Pure function, no I/O — safe to call on every incoming user message.
    """

    if not text:
        return None
    for category, pattern in _EMERGENCY_PATTERNS:
        if pattern.search(text):
            return category
    return None
