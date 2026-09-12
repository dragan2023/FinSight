"""Safe local report export; never includes credentials, files or hidden reasoning."""
from __future__ import annotations
import csv, html, io, json, re, uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from .reports import ReportStore

_FORMATS={"markdown":"md","html":"html","json":"json","csv":"csv","source_markdown":"md"}
_METRIC_LABELS={
    "revenue_yoy":"营收同比", "net_profit_yoy":"归母净利润同比", "gross_margin":"毛利率",
    "net_margin":"净利率", "debt_ratio":"资产负债率", "ocf_vs_net_profit":"经营现金流 / 净利润",
    "ocf_vs_parent_profit":"经营现金流 / 归母净利润", "range_change":"区间涨跌", "max_drawdown":"最大回撤",
    "revenue_y3":"三年营收变化", "net_profit_y3":"三年净利润变化", "pe_ttm":"市盈率（TTM）",
    "pb_mrq":"市净率（MRQ）", "dividend_yield":"股息率",
}
def _clean(value:str)->str:return re.sub(r"[^\w.-]+","_",value,flags=re.UNICODE).strip("._")[:80] or "report"
def _text(value:object)->str:
    text=str(value or "")
    return text.replace("\x00","").replace("\r","")
def _markdown_text(value: object) -> str:
    """Keep exported Markdown inert in renderers that allow embedded HTML."""
    return _text(value).replace("<", "&lt;").replace(">", "&gt;")
def _csv_text(value:object)->str:
    value=_text(value).lstrip()
    return "'"+value if value[:1] in ("=","+","-","@") else value
def _display_metric(metric:dict)->str:
    value=metric.get("value")
    if value is None:return "暂缺"
    try:number=Decimal(str(value))
    except (InvalidOperation,ValueError):return "暂缺"
    unit=metric.get("unit") or ""
    if unit=="ratio":return f"{number * 100:.1f}%"
    if unit=="CNY/share":return f"{number:.2f} 元/股"
    if unit=="CNY":
        absolute=abs(number)
        if absolute>=Decimal("100000000"):return f"{number / Decimal('100000000'):.2f} 亿元"
        if absolute>=Decimal("10000"):return f"{number / Decimal('10000'):.2f} 万元"
        return f"{number:.2f} 元"
    if unit in {"times","x"}:return f"{number:.2f} 倍"
    return f"{number:.2f}"+(f" {unit}" if unit else "")
class ExportService:
    def __init__(self,repository,exports_dir:Path):self._repository=repository;self._dir=exports_dir
    def create(self,report_id:str,format:str)->dict:
        if format not in _FORMATS: raise ValueError("导出格式仅支持 markdown/html/json/csv。")
        stored=ReportStore(self._repository).get(report_id)
        if not stored: raise KeyError(report_id)
        export_id=uuid.uuid4().hex
        suffix="_核验材料" if format=="source_markdown" else ""
        filename=f"{_clean(stored.question)}_{report_id[:8]}{suffix}.{_FORMATS[format]}"; target=(self._dir / f"{export_id}_{filename}").resolve(); base=self._dir.resolve()
        if base not in target.parents: raise ValueError("导出路径无效。")
        self._dir.mkdir(parents=True,exist_ok=True)
        report=stored.report.model_dump(mode="json")
        if format=="json": target.write_text(json.dumps({"schema_version":"finsight.export.v1","report_id":stored.id,"version":stored.version,"report":report,"unverified_raw_sections":[item.model_dump(mode="json") for item in stored.raw_sections]},ensure_ascii=False,indent=2),encoding="utf-8")
        elif format=="markdown": target.write_text(self._markdown(stored.question,stored.version,report),encoding="utf-8")
        elif format=="source_markdown": target.write_text(self._source_markdown(stored),encoding="utf-8")
        elif format=="html": target.write_text(self._html(stored.question,stored.version,report),encoding="utf-8")
        else: target.write_text(self._csv(report),encoding="utf-8-sig",newline="")
        self._repository.insert_export(export_id,report_id,format,target.name)
        return {"id":export_id,"filename":filename,"format":format}
    def path(self,export_id:str)->Path|None:
        row=self._repository.get_export(export_id)
        if not row:return None
        path=(self._dir/row["filename"]).resolve()
        return path if self._dir.resolve() in path.parents and path.is_file() else None
    def _markdown(self,question:str,version:int,report:dict)->str:
        chunks=[f"# {_markdown_text(question)}", f"\n生成日期：{_markdown_text(report.get('created_at'))}", "\n## 关键数据"]
        for metric in report.get("metrics") or []:
            label=_METRIC_LABELS.get(metric.get("metric"), "其他财务指标")
            chunks.append(f"- {_markdown_text(label)}：{_markdown_text(_display_metric(metric))}")
        chunks.append("\n## 阅读提示")
        chunks.extend(f"- {_markdown_text(item)}" for item in report.get("limitations") or [])
        chunks.append("\n## 仍待核验的信息")
        for gap in report.get("data_gaps") or []:
            chunks.append(f"- {_markdown_text(gap.get('impact'))}")
        for section in report.get("sections") or []:
            chunks.extend([f"\n## {_markdown_text(section.get('title'))}",_markdown_text(section.get("summary"))])
            for claim in section.get("claims") or []:
                chunks.append(f"- {_markdown_text(claim.get('text'))}")
            for question in section.get("follow_up_questions") or []:
                chunks.append(f"- 待核验：{_markdown_text(question)}")
        return "\n".join(chunks)+"\n"
    def _source_markdown(self,stored)->str:
        """Download-only material for a human audit, kept out of the reading UI."""
        chunks=[f"# {_markdown_text(stored.question)} · 核验材料", "",
                f"报告版本：{stored.version}",
                "", "本文件用于人工核验，保留本机已保存的结构化记录以及追问时使用的搜索结果。",
                "不包含 API Key、隐藏推理、原始附件或未保存的网页全文。", "", "## 同花顺及其他结构化数据"]
        evidence_ids=stored.report.evidence_ids
        if not evidence_ids:
            chunks.append("（本报告没有已保存的结构化证据。）")
        for evidence_id in evidence_ids:
            found=self._repository.get_evidence(evidence_id)
            if not found:
                continue
            meta,records,gaps=found
            chunks.extend([f"\n### {meta.capability}",
                           f"- 来源：{_markdown_text(meta.provider)}", f"- 接口：{_markdown_text(meta.endpoint_path)}",
                           f"- 获取时间：{_markdown_text(meta.fetched_at.isoformat())}",
                           f"- 口径：{_markdown_text(meta.basis)}", f"- 内容校验哈希：`{_markdown_text(meta.content_hash)}`",
                           "\n#### 记录"])
            if records:
                for record in records:
                    chunks.append("```json\n"+json.dumps(record,ensure_ascii=False,indent=2)+"\n```")
            else:
                chunks.append("（该来源没有标准化记录。）")
            raw=self._repository.get_evidence_raw(evidence_id)
            chunks.append("\n#### 已保存的原始响应（已脱敏）")
            chunks.append("```json\n"+json.dumps(raw,ensure_ascii=False,indent=2)+"\n```" if raw is not None else "（此记录产生于原始响应保留功能启用前，只有结构化记录和哈希可供核对。）")
            if gaps:
                chunks.append("\n#### 来源缺口")
                chunks.extend(f"- {_markdown_text(gap.dimension)}：{_markdown_text(gap.impact)}" for gap in gaps)
        chunks.extend(["\n## 联网检索结果（如有）"])
        conversation=self._repository.get_conversation(stored.id)
        messages=self._repository.list_chat_messages(conversation.id,limit=100) if conversation else []
        web_sources=[source for message in messages for source in message.sources]
        if not web_sources:
            chunks.append("（本报告及其追问尚未保存联网检索结果。）")
        else:
            seen=set()
            for source in web_sources:
                key=source.get("url")
                if not key or key in seen:
                    continue
                seen.add(key)
                chunks.extend([f"\n### {_markdown_text(source.get('title'))}",
                               f"- 域名：{_markdown_text(source.get('domain'))}",
                               f"- 链接：{_markdown_text(key)}",
                               f"- 发布信息：{_markdown_text(source.get('published_at') or '未提供')}",
                               f"- 搜索摘要：{_markdown_text(source.get('snippet') or '未提供')}"])
        return "\n".join(chunks)+"\n"
    def _html(self,question:str,version:int,report:dict)->str:
        body=html.escape(self._markdown(question,version,report))
        charts=self._chart_snapshots(report)
        return "<!doctype html><html><head><meta charset='utf-8'><meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'\"><title>FinSight 报告</title><style>body{font:15px sans-serif;max-width:900px;margin:32px auto;line-height:1.7}pre{white-space:pre-wrap;font:inherit}.chart{border:1px solid #e1e6dc;border-radius:10px;padding:12px;margin:16px 0}.chart h2{font-size:15px;margin:0 0 8px;color:#246653}.chart svg{max-width:100%;height:auto}</style></head><body>"+charts+"<pre>"+body+"</pre></body></html>"
    def _chart_snapshots(self,report:dict)->str:
        """受限 SVG 静态快照：HTML 导出可离线阅读，绝不嵌入运行时脚本。"""
        metric_by_id={item.get("metric"):item for item in report.get("metrics") or []}
        figures=[]
        for chart in report.get("chart_specs") or []:
            refs=chart.get("metric_refs") or []
            values=[]
            for ref in refs:
                try:value=Decimal(str((metric_by_id.get(ref) or {}).get("value")))
                except (InvalidOperation,ValueError):continue
                if value.is_finite():values.append((str(ref),float(value)))
            if not values:continue
            maximum=max(max(abs(value) for _,value in values),1.0)
            width=max(360,80*len(values)); bars=[]
            for index,(name,value) in enumerate(values):
                height=round(abs(value)/maximum*120,1); x=42+index*76; y=150-height if value>=0 else 150
                color="#c9302c" if value>=0 else "#1f9d66"
                bars.append(f"<rect x='{x}' y='{y}' width='42' height='{height}' rx='3' fill='{color}'/><text x='{x+21}' y='180' text-anchor='middle' font-size='10' fill='#526159'>{html.escape(_METRIC_LABELS.get(name,name))}</text><text x='{x+21}' y='{max(14,y-5)}' text-anchor='middle' font-size='10' fill='#31403a'>{value:.3g}</text>")
            title=html.escape(_text(chart.get("title") or "图表快照"))
            figures.append(f"<section class='chart'><h2>{title}</h2><svg viewBox='0 0 {width} 196' role='img' aria-label='{title}'><line x1='28' y1='150' x2='{width-16}' y2='150' stroke='#b7c1b8'/>"+"".join(bars)+"</svg><p>静态图表快照，基于报告内已保存指标；不含运行时脚本。</p></section>")
        return "".join(figures)
    def _csv(self,report:dict)->str:
        out=io.StringIO();writer=csv.writer(out);writer.writerow(["metric","value","unit","basis","evidence_ids","data_date","coverage","output_integrity","note"])
        for metric in report.get("metrics") or []:writer.writerow([_csv_text(metric.get("metric")),_csv_text(metric.get("value")),_csv_text(metric.get("unit")),_csv_text(metric.get("basis")),_csv_text(",".join(metric.get("evidence_ids") or [])),_csv_text(report.get("created_at")),_csv_text(report.get("coverage")),_csv_text(report.get("output_integrity")),_csv_text(metric.get("note"))])
        return out.getvalue()
