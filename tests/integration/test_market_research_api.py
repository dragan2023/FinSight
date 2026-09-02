def test_market_research_calculations_are_explicit_input_and_local(client):
    relative = client.post("/api/sectors/relative-performance", json={
        "reference_id": "index", "series": [
            {"id": "index", "label": "指数", "price_basis": "index_price", "prices": {"2026-01-02": "100", "2026-01-03": "110"}},
            {"id": "stock", "label": "个股", "price_basis": "forward_adjusted", "prices": {"2026-01-02": "20", "2026-01-03": "21"}},
        ],
    })
    assert relative.status_code == 200
    assert relative.json()["return_differences"]["stock"] == "-0.05"
    overlap = client.post("/api/funds/visible-overlap", json={
        "left": {"fund_id": "A", "report_end": "2025-12-31", "weight_definition_confirmed": True, "holdings": [{"security_id": "600000", "market": "SH", "weight": "0.2"}]},
        "right": {"fund_id": "B", "report_end": "2025-12-31", "weight_definition_confirmed": True, "holdings": [{"security_id": "600000", "market": "SH", "weight": "0.1"}]},
    })
    assert overlap.status_code == 200
    assert overlap.json()["overlap_lower_bound"] == "0.1"
