from agent_api.emergency import detect_emergency_signal


def test_detects_high_specificity_red_flags() -> None:
    assert detect_emergency_signal("孩子突然抽搐了怎么办") == "seizure"
    assert detect_emergency_signal("宝宝叫不醒，怎么都没反应") == "consciousness"
    assert detect_emergency_signal("呼吸困难，嘴唇发紫") in {"breathing", "circulation"}
    assert detect_emergency_signal("一直呕吐还没什么精神") == "gi_persistent"
    assert detect_emergency_signal("孩子反应差，比平时安静很多") == "metabolic_specific"


def test_ignores_common_low_specificity_symptoms() -> None:
    assert detect_emergency_signal("孩子有点咳嗽") is None
    assert detect_emergency_signal("发热到38度，精神还好") is None
    assert detect_emergency_signal("今天体重记录一下，15.2kg") is None
    assert detect_emergency_signal("") is None


def test_does_not_require_word_boundary_around_cjk() -> None:
    # No spaces in Chinese — patterns must fire even glued to surrounding text.
    assert detect_emergency_signal("宝宝今天突然抽搐，然后就昏迷了") is not None
