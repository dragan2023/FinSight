"""Safe local report export; never includes credentials, files or hidden reasoning."""
from __future__ import annotations
import csv, html, io, json, re, uuid
from pathlib import Path
from .reports import ReportStore

_FORMATS={"markdown":"md","html":"html","json":"json","csv":"csv","source_markdown":"md"}
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
        chunks=[f"# {_markdown_text(question)}",f"\n报告版本：{version}",f"\n生成日期：{_markdown_text(report.get('created_at'))}",f"\n研究范围：{_markdown_text(json.dumps(report.get('scope') or {}, ensure_ascii=False, sort_keys=True))}",f"\n覆盖程度：{_markdown_text(report.get('coverage'))}",f"\n输出完整性：{_markdown_text(report.get('output_integrity'))}","\n## 指标口径与来源"]
        for metric in report.get("metrics") or []:
            chunks.append(f"- {_markdown_text(metric.get('metric'))}: {_markdown_text(metric.get('value'))} {_markdown_text(metric.get('unit'))}；期间/口径：{_markdown_text(metric.get('basis'))}")
        chunks.append("\n## 数据缺口与局限")
        for gap in report.get("data_gaps") or []:
            chunks.append(f"- {_markdown_text(gap.get('dimension'))} / {_markdown_text(gap.get('period'))}: {_markdown_text(gap.get('impact'))}")
        chunks.extend(f"- {_markdown_text(item)}" for item in report.get("limitations") or [])
        for section in report.get("sections") or []:
            chunks.extend([f"\n## {_markdown_text(section.get('title'))}（{_markdown_text(section.get('status'))}）",_markdown_text(section.get("summary"))])
            for claim in section.get("claims") or []:
                chunks.append(f"- {_markdown_text(claim.get('text'))}")
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
        return "<!doctype html><html><head><meta charset='utf-8'><meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'\"><title>FinSight 报告</title><style>body{font:15px sans-serif;max-width:900px;margin:32px auto;white-space:pre-wrap;line-height:1.7}</style></head><body>"+body+"</body></html>"
    def _csv(self,report:dict)->str:
        out=io.StringIO();writer=csv.writer(out);writer.writerow(["metric","value","unit","basis","evidence_ids","data_date","coverage","output_integrity","note"])
        for metric in report.get("metrics") or []:writer.writerow([_csv_text(metric.get("metric")),_csv_text(metric.get("value")),_csv_text(metric.get("unit")),_csv_text(metric.get("basis")),_csv_text(",".join(metric.get("evidence_ids") or [])),_csv_text(report.get("created_at")),_csv_text(report.get("coverage")),_csv_text(report.get("output_integrity")),_csv_text(metric.get("note"))])
        return out.getvalue()
