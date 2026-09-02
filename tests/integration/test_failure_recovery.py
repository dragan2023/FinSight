"""Bad local inputs leave independent calculations and report reads available."""


def test_bad_fund_weight_is_rejected_without_breaking_local_report_library(client):
    malformed = client.post("/api/funds/visible-overlap", json={
        "left": {"fund_id": "a", "report_end": "2025-12-31", "weight_definition_confirmed": True,
                 "holdings": [{"security_id": "600000", "market": "SH", "weight": "not-a-number"}]},
        "right": {"fund_id": "b", "report_end": "2025-12-31", "weight_definition_confirmed": True,
                  "holdings": [{"security_id": "600000", "market": "SH", "weight": "0.1"}]},
    })
    assert malformed.status_code == 422
    assert client.get("/api/library/reports").status_code == 200
