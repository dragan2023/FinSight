"""Normalization for index catalogue, constituents and price rows."""

from __future__ import annotations

from .normalization import NormalizedField, NormalizedRecord, date_from_ms, to_decimal


def normalize_index_catalog(rows: list[dict], tag: str) -> list[NormalizedRecord]:
    records = []
    for row in rows or []:
        thscode = str(row.get("thscode") or "")
        if not thscode:
            continue
        fields = {name: NormalizedField(source_field=name, label=label, kind="text", unit="",
                                        raw_value=None if row.get(name) is None else str(row.get(name)),
                                        value=None if row.get(name) is None else str(row.get(name)))
                  for name, label in (("name", "指数名称"), ("tag", "分类"))}
        records.append(NormalizedRecord(thscode=thscode, period_key=f"catalog:{tag}:{thscode}", period_end=None,
                                        disclosed_at=None, basis="index_catalog", currency="CNY", fields=fields))
    return records


def normalize_index_constituents(rows: list[dict], index_thscode: str) -> list[NormalizedRecord]:
    """Keep a dated *current* constituent snapshot without promoting members.

    Constituents are evidence for an index.  They are deliberately not written
    as equity identities: a user must explicitly enter company research before
    a member becomes a reusable company target.
    """
    records: list[NormalizedRecord] = []
    seen: set[str] = set()
    for position, row in enumerate(rows or []):
        code = str(row.get("thscode") or row.get("stock_thscode") or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        name = row.get("name") or row.get("stock_name")
        fields = {
            "member_thscode": NormalizedField(source_field="thscode", label="成分代码", kind="text", unit="",
                                                raw_value=code, value=code),
            "member_name": NormalizedField(source_field="name", label="成分名称", kind="text", unit="",
                                             raw_value=None if name is None else str(name),
                                             value=None if name is None else str(name)),
        }
        records.append(NormalizedRecord(thscode=index_thscode, period_key=f"current-member:{position}:{code}",
                                        period_end=None, disclosed_at=None, basis="current_constituents",
                                        currency=None, fields=fields))
    return records


def normalize_index_snapshot(rows: list[dict]) -> list[NormalizedRecord]:
    records: list[NormalizedRecord] = []
    for row in rows or []:
        code = str(row.get("thscode") or "").strip()
        if not code:
            continue
        fields: dict[str, NormalizedField] = {}
        for name, label, kind in (
            ("last_price", "最新价", "price"), ("change_rate", "涨跌幅", "percent"),
            ("turnover", "成交额", "turnover"), ("volume", "成交量", "volume"),
        ):
            raw = row.get(name)
            value = to_decimal(raw)
            if name == "change_rate" and value is not None and abs(value) > 1:
                value /= 100
            fields[name] = NormalizedField(source_field=name, label=label, kind=kind, unit="CNY" if name in {"last_price", "turnover"} else "",
                                            raw_value=None if raw is None else str(raw),
                                            value=None if value is None else format(value, "f"))
        records.append(NormalizedRecord(thscode=code, period_key=f"snapshot:{code}", period_end=None,
                                        disclosed_at=None, basis="index_snapshot", currency="CNY", fields=fields))
    return records


def normalize_index_history(rows: list[dict], thscode: str) -> list[NormalizedRecord]:
    records = []
    seen = set()
    for row in rows or []:
        date = date_from_ms(row.get("date_ms"))
        if not date or date in seen:
            continue
        seen.add(date)
        fields = {}
        for name, label in (("open_price", "开盘价"), ("high_price", "最高价"), ("low_price", "最低价"), ("close_price", "收盘价"), ("turnover", "成交额")):
            raw = row.get(name)
            parsed = to_decimal(raw)
            fields[name] = NormalizedField(source_field=name, label=label, kind="price" if name != "turnover" else "turnover", unit="CNY",
                                            raw_value=None if raw is None else str(raw), value=None if parsed is None else format(parsed, "f"))
        records.append(NormalizedRecord(thscode=thscode, period_key=date, period_end=date, disclosed_at=None,
                                        basis="index_daily_bar", currency="CNY", fields=fields))
    return sorted(records, key=lambda item: item.period_key)
