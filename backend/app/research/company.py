"""Build a company-quality view only from already stored evidence."""
from __future__ import annotations
from ..analysis.company_quality import assess_company_quality
from ..analysis.company_comparison import ComparisonCompany, build_comparison


def _local_records(repository, thscode: str) -> tuple[list[dict], list[dict], list[dict], list[dict], list[str]]:
    """Read saved evidence only; never invokes MarketDataService or a network client."""
    income: list[dict] = []
    balance: list[dict] = []
    cashflow: list[dict] = []
    valuations: list[dict] = []
    evidence_ids: list[str] = []
    for meta in repository.list_evidence(thscode=thscode, limit=200):
        found = repository.get_evidence(meta.id)
        if not found:
            continue
        _, records, _ = found
        bucket = None
        if "income" in meta.endpoint_path:
            bucket = income
        elif "balance" in meta.endpoint_path:
            bucket = balance
        elif "cash-flow" in meta.endpoint_path or "cashflow" in meta.endpoint_path:
            bucket = cashflow
        elif "valuations" in meta.endpoint_path:
            bucket = valuations
        if bucket is not None:
            bucket.extend(records)
            evidence_ids.append(meta.id)
    return income, balance, cashflow, valuations, evidence_ids


def _latest_annual(records: list[dict]) -> dict | None:
    rows = [row for row in records if row.get("basis") == "annual_report"]
    return max(rows, key=lambda row: row.get("period_key") or "") if rows else None


def _field_value(record: dict | None, name: str) -> str | None:
    field = (record or {}).get("fields", {}).get(name) or {}
    return field.get("value")

def local_company_research(repository, thscode: str, industry: str | None = None) -> dict:
    income, balance, cashflow, _, evidence_ids = _local_records(repository, thscode)
    quality=assess_company_quality(income_records=income,balance_records=balance,cashflow_records=cashflow,industry=industry,evidence_ids=evidence_ids)
    return {"thscode":thscode,"evidence_ids":evidence_ids,"quality":quality.model_dump(mode="json"),"local_only":True}


def local_company_comparison(repository, thscodes: list[str], industries: dict[str, str] | None = None) -> dict:
    """Build a comparison from the explicit, already-confirmed group only."""
    industries = industries or {}
    companies: list[ComparisonCompany] = []
    for thscode in thscodes:
        identity = repository.get_identity(thscode)
        if identity is None:
            raise KeyError(thscode)
        income, balance, cashflow, valuations, evidence_ids = _local_records(repository, thscode)
        quality = assess_company_quality(income_records=income, balance_records=balance,
                                         cashflow_records=cashflow, industry=industries.get(thscode),
                                         evidence_ids=evidence_ids)
        values = {metric.metric: metric.value for dimension in quality.dimensions for metric in dimension.metrics}
        latest_income, latest_balance = _latest_annual(income), _latest_annual(balance)
        latest_valuation = valuations[0] if valuations else None
        values.update({
            "parent_holder_net_profit": _field_value(latest_income, "parent_holder_net_profit"),
            "net_profit": _field_value(latest_income, "net_profit"),
            "holder_equity_total": _field_value(latest_balance, "holder_equity_total"),
            "pe_ttm": _field_value(latest_valuation, "pe_ttm"),
            "pb_mrq": _field_value(latest_valuation, "pb_mrq"),
        })
        companies.append(ComparisonCompany(
            security_id=thscode, thscode=thscode, name=identity.name, industry=industries.get(thscode),
            period=(latest_income or {}).get("period_key"), currency=(latest_income or {}).get("currency"),
            unit="CNY", consolidation="consolidated", period_basis=(latest_income or {}).get("basis"),
            disclosed_at=(latest_income or {}).get("disclosed_at"), values=values))
    metrics = ["revenue_yoy", "net_profit_yoy", "gross_margin", "net_margin", "debt_ratio", "ocf_vs_parent_profit", "pe_ttm", "pb_mrq"]
    return build_comparison(companies, metrics).model_dump(mode="json")
