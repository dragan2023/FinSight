"""Offline acceptance flows: all checks use TestClient's no-network fixture."""


def test_no_key_task_keeps_a_conditional_framework_and_new_task_kind(client):
    response = client.post("/api/research/tasks", json={
        "question": "数据缺失时仍给出取证框架", "type": "fund_quality",
        "client_request_id": "acceptance-fund-quality-001",
    })
    assert response.status_code == 202
    task = response.json()["task"]
    assert task["scope"] == {"type": "fund_quality"}


def test_market_calculation_rejects_nonpositive_common_price_instead_of_fabricating_return(client):
    response = client.post("/api/sectors/relative-performance", json={
        "reference_id": "index", "series": [
            {"id": "index", "label": "指数", "price_basis": "price", "prices": {"2026-01-02": "100", "2026-01-03": "0"}},
            {"id": "stock", "label": "股票", "price_basis": "forward", "prices": {"2026-01-02": "10", "2026-01-03": "11"}},
        ],
    })
    assert response.status_code == 422
    assert response.json()["error"]["category"] == "validation"
