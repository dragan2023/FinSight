import pytest
from backend.app.analysis.relative_performance import RelativeSeries,breadth,relative_performance
def test_common_dates_and_return_difference_are_not_alpha():
    value=relative_performance([RelativeSeries(id="stock",label="股",price_basis="forward",prices={"1":"10","2":"12","3":None}),RelativeSeries(id="index",label="指",price_basis="price",prices={"1":"20","2":"21","3":"22"})],"index")
    assert value.common_dates==["1","2"] and value.normalized["stock"]["1"]=="100"
    assert value.return_differences["stock"]=="0.15" and "alpha" in value.limitation
def test_no_common_window_is_not_filled_and_breadth_has_denominator():
    result=relative_performance([RelativeSeries(id="a",label="a",price_basis="p",prices={"1":"1"}),RelativeSeries(id="b",label="b",price_basis="p",prices={"2":"1"})],"a")
    assert result.returns["a"] is None
    assert breadth([{"change":"1"},{"change":None},{"change":"0"}])["covered"]==2
