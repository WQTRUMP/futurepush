from futures_signal.metrics import basis, basis_bp, basis_change_label, basis_state, classify_price_oi, score_band


def test_basis_and_basis_bp():
    assert basis(3950, 3960) == -10
    assert round(basis_bp(3950, 3960), 2) == -25.25
    assert basis_state(-10) == "贴水"
    assert basis_change_label(3) == "贴水收窄/升水扩大"
    assert basis_change_label(-3) == "贴水扩大/升水收窄"


def test_price_oi_classification():
    assert classify_price_oi(1, 10) == "多头主动开仓"
    assert classify_price_oi(1, -10) == "空头平仓推动"
    assert classify_price_oi(-1, 10) == "空头主动加仓"
    assert classify_price_oi(-1, -10) == "多头止损/减仓"


def test_score_band():
    assert score_band(80) == "期现共振偏多"
    assert score_band(60) == "偏多但不强"
    assert score_band(40) == "中性震荡"
    assert score_band(20) == "偏空"
    assert score_band(19) == "明显空头"
