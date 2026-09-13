"""The offline fixture has no developer fallback and an empty setup stays readable."""


def test_unconfigured_instance_never_claims_finance_key_or_uses_network(client):
    status = client.get("/api/system/status")
    assert status.status_code == 200
    assert status.json()["market_data"]["key_configured"] is False
    assert client.get("/api/library/reports").status_code == 200
