from backend.app.analysis.fund_quality import nav_quality
def test_short_nav_sample_does_not_annualize_or_volatilize():
    result=nav_quality([("1","1"),("2","1.1")],adjusted=False)
    assert result["annualized_return"] is None and result["volatility"] is None and "单位净值" in result["limitation"]
def test_adjusted_nav_with_enough_observations_has_volatility():
    result=nav_quality([(str(x),str(1+x/1000)) for x in range(61)],adjusted=True)
    assert result["volatility"] is not None
