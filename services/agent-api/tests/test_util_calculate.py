from agent_api.tools.util.calculate import compute_calculate


def test_basic_arithmetic() -> None:
    out = compute_calculate("(2 + 3) * 4")
    assert out["ok"] is True
    assert out["result"] == 20
    assert out["result_type"] == "int"


def test_float_division_and_functions() -> None:
    out = compute_calculate("round(abs(-3.5) + min(1, 2), 1)")
    assert out["ok"] is True
    assert out["result"] == 4.5


def test_div_zero() -> None:
    out = compute_calculate("1 / 0")
    assert out["ok"] is False
    assert out["error_code"] == "div_zero"


def test_rejects_name_lookup() -> None:
    out = compute_calculate("__import__('os').system('id')")
    assert out["ok"] is False
    assert out["error_code"] in {"syntax", "forbidden"}


def test_rejects_too_long() -> None:
    out = compute_calculate("1+" * 200 + "1")
    assert out["ok"] is False
    assert out["error_code"] == "too_long"
