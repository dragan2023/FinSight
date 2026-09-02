"""Attachment upload store (plan 04 sections 2.5 / 2.6).

上传的原始文件默认按 reference_only 处理：解析后原始字节立即丢弃，只在
数据库保留元数据（显示名、大小、类型、SHA-256、解析统计），绝不在磁盘留存
原始文件。用户显式选择 excerpt 时，额外保留一段有上限的解析文本摘录
（data/attachments/<32-hex id>.excerpt.txt），供预览与再分析引用；full 策略
需要明确许可依据，首版不提供。原文件名只是显示字段，绝不拼接服务器路径。

解析文本写入既有 user_provided 补充存储（标注来源文件与解析方式）；保存
资料不代表同意外发，也绝不自动触发再分析。任何失败文件都给出结构化错误与
DataGap 候选，其他文件与任务本身不受影响。
"""

from __future__ import annotations

import base64
import hashlib
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field

from ..errors import AppError
from ..settings import Settings
from .attachment_parsers import (
    REMEDIES_BY_CODE,
    REASON_BY_CODE,
    AttachmentLimits,
    AttachmentParseError,
    ParsedAttachment,
    parse_attachment,
)
from .attachment_worker import (
    SUBPROCESS_TIMEOUT_GRACE_SECONDS,
    run_isolated_parse,
)
from .mapping_candidates import extract_mapping_candidates
from .evidence_models import DataGap, StoragePolicy
from .models import MAX_SUPPLEMENT_TEXT, ResearchSupplement, SupplementKind
from .supplements import SupplementStore

ATTACHMENT_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
RETENTION_CHOICES = ("reference_only", "excerpt")
STORAGE_POLICY_BY_RETENTION = {
    "reference_only": StoragePolicy.REFERENCE_ONLY,
    "excerpt": StoragePolicy.EXCERPT,
}
# 显示名只是展示字段：拒绝路径分隔符与穿越片段，绝不参与任何文件系统路径。
_MAX_DISPLAY_NAME = 200


def is_attachment_id(value: str) -> bool:
    """防遍历：附件标识必须是 32 位十六进制；磁盘路径只由该 ID 构成。"""
    return bool(ATTACHMENT_ID_PATTERN.match(value or ""))


def attachment_gap(display_name: str, code: str, message: str) -> DataGap:
    """把单文件失败转成 DataGap 候选；只随响应返回，不自动写入报告缺口清单。"""
    return DataGap(
        id=uuid.uuid4().hex,
        dimension="local_attachment",
        reason=REASON_BY_CODE.get(code, "unsupported"),
        impact=f"附件「{display_name}」未能转为可用材料：{message}",
        remedies=list(REMEDIES_BY_CODE.get(code, ["确认文件后重新上传。"])),
    )


class AttachmentEntry:
    """端点读出的单个文件：显示名、声明 MIME 与完整字节。"""

    __slots__ = ("display_name", "mime_type", "data")

    def __init__(self, display_name: str, mime_type: str, data: bytes) -> None:
        self.display_name = display_name
        self.mime_type = mime_type
        self.data = data


class AttachmentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    report_id: str
    task_id: str
    display_name: str
    extension: str
    mime_type: str
    size_bytes: int = Field(ge=0)
    sha256: str
    kind: str
    parse_method: str
    storage_policy: StoragePolicy
    # 首版恒为 False：原始文件从不落盘，excerpt 只存解析文本摘录。
    raw_file_retained: bool = False
    excerpt_path: str | None = None
    truncated: bool = False
    warnings: list[str] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)
    supplement_id: str | None = None
    # 批次5：用户确认的候选字段映射（标的/期间/单位/备注）；确认只保存。
    mapping: dict = Field(default_factory=dict)
    mapping_confirmed_at: str | None = None
    # 批次7：解析时确定性提取的候选映射值（仅预填表单；确认仍走 mapping）。
    mapping_candidates: dict = Field(default_factory=dict)
    created_at: str


class AttachmentFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str
    code: str
    message: str
    gap: DataGap | None = None


class AttachmentUploadOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    saved: list[AttachmentRecord] = Field(default_factory=list)
    failed: list[AttachmentFailure] = Field(default_factory=list)
    supplements: list[ResearchSupplement] = Field(default_factory=list)
    gaps: list[DataGap] = Field(default_factory=list)


class AttachmentPreview(BaseModel):
    """GET /api/supplements/{id}：元数据 + 解析文本摘录 + 关联补充状态。"""

    model_config = ConfigDict(extra="forbid")

    attachment: AttachmentRecord
    excerpt: str | None = None
    supplement: ResearchSupplement | None = None


class AttachmentReportView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attachments: list[AttachmentRecord] = Field(default_factory=list)
    supplements: list[ResearchSupplement] = Field(default_factory=list)


class AttachmentDeletion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    deleted: bool
    excerpt_removed: bool = False


class AttachmentStore:
    def __init__(self, repository, settings: Settings,
                 limits: AttachmentLimits | None = None, *,
                 clock: Callable[[], float] = time.monotonic,
                 parse_subprocess: bool | None = None) -> None:
        self._repository = repository
        self._limits = limits or AttachmentLimits()
        self._project_root = settings.project_root
        self._clock = clock
        # 生产默认子进程隔离；False 仅限测试显式降级（计划 §2.5 安全边界）。
        self._parse_subprocess = (settings.attachment_parse_subprocess
                                  if parse_subprocess is None else parse_subprocess)

    @property
    def limits(self) -> AttachmentLimits:
        return self._limits

    @property
    def attachments_dir(self) -> Path:
        return self._project_root / "data" / "attachments"

    def excerpt_path_for(self, attachment_id: str) -> Path:
        """磁盘路径只由 32-hex ID 构成；调用方必须先用 is_attachment_id 校验。"""
        return self.attachments_dir / f"{attachment_id}.excerpt.txt"

    def save_upload(self, *, report_id: str, retention: str,
                    entries: list[AttachmentEntry]) -> AttachmentUploadOutcome:
        if retention not in RETENTION_CHOICES:
            raise AppError("validation", "留存策略仅支持 reference_only 或 excerpt。", 422)
        stored = self._repository.get_report(report_id)
        if stored is None:
            raise AppError("not_found", "报告不存在，无法上传附件。", 404)
        if len(entries) > self._limits.max_files_per_request:
            raise AppError(
                "validation",
                f"一次最多上传 {self._limits.max_files_per_request} 个文件，请分批上传。", 422)
        outcome = AttachmentUploadOutcome()
        supplement_store = SupplementStore(self._repository)
        running_total = 0
        for entry in entries:
            record = self._process_entry(
                report_id=report_id, task_id=stored.task_id, retention=retention,
                entry=entry, running_total=running_total,
                supplement_store=supplement_store, outcome=outcome)
            if record is not None:
                running_total += record.size_bytes
        return outcome

    # -- 批次5：预览 / 字段映射确认 / 删除 / 报告清单（计划 04 §2.6） ----------
    def _get_checked(self, attachment_id: str) -> AttachmentRecord:
        if not is_attachment_id(attachment_id or ""):
            raise AppError("validation", "附件标识格式不正确。", 422)
        record = self._repository.get_attachment(attachment_id)
        if record is None:
            raise AppError("not_found", "附件资料不存在。", 404)
        return record

    def preview(self, attachment_id: str) -> AttachmentPreview:
        """预览解析结果：元数据 + 摘录文本；绝不提供原始文件内容（未留存）。"""
        record = self._get_checked(attachment_id)
        excerpt: str | None = None
        if record.excerpt_path:
            path = (self._project_root / record.excerpt_path).resolve()
            # 防御：摘录路径必须仍在附件目录内（ID 命名），异常行数据一律拒读。
            if self.attachments_dir.resolve() not in path.parents or path.name != f"{record.id}.excerpt.txt":
                raise AppError("not_found", "摘录文件不可用，请重新上传该附件。", 404)
            if path.exists():
                excerpt = path.read_text(encoding="utf-8")
        supplement = None
        if record.supplement_id:
            supplement = self._repository.get_supplement(record.supplement_id)
        return AttachmentPreview(attachment=record, excerpt=excerpt, supplement=supplement)

    def confirm_mapping(self, attachment_id: str, mapping: dict) -> AttachmentRecord:
        """确认候选字段映射（标的/期间/单位/备注）：只保存，不触发任何计算。"""
        record = self._get_checked(attachment_id)
        allowed = {"security_id", "period", "unit", "note"}
        clean = {key: str(value).strip() for key, value in mapping.items()
                 if key in allowed and str(value).strip()}
        if not clean:
            raise AppError("validation", "请至少确认一项映射（标的/期间/单位/备注）。", 422)
        confirmed_at = datetime.now(timezone.utc).isoformat()
        self._repository.update_attachment_mapping(record.id, clean, confirmed_at)
        return self._repository.get_attachment(record.id)

    def list_for_report(self, report_id: str) -> AttachmentReportView:
        if not is_attachment_id(report_id or ""):
            raise AppError("validation", "报告标识格式不正确。", 422)
        if self._repository.get_report(report_id) is None:
            raise AppError("not_found", "报告不存在。", 404)
        return AttachmentReportView(
            attachments=self._repository.list_attachments(report_id),
            supplements=self._repository.list_supplements(report_id))

    def delete(self, attachment_id: str) -> AttachmentDeletion:
        """删除未引用资料（附件行 + 摘录文件 + 派生补充）；已引用的按保留规则拒绝。"""
        record = self._get_checked(attachment_id)
        supplement = (self._repository.get_supplement(record.supplement_id)
                      if record.supplement_id else None)
        if supplement is not None and supplement.referenced_by_report_id:
            raise AppError(
                "request_conflict",
                "该附件资料已被再分析报告引用；为保证旧版本可复核，不删除已引用资料。", 409)
        excerpt_removed = False
        if record.excerpt_path:
            path = self._project_root / record.excerpt_path
            if path.exists():
                path.unlink()
                excerpt_removed = True
        if supplement is not None:
            self._repository.delete_supplement(supplement.id)
        self._repository.delete_attachment(record.id)
        return AttachmentDeletion(id=record.id, deleted=True, excerpt_removed=excerpt_removed)

    def _process_entry(self, *, report_id: str, task_id: str, retention: str,
                       entry: AttachmentEntry, running_total: int,
                       supplement_store, outcome: AttachmentUploadOutcome,
                       ) -> AttachmentRecord | None:
        display_name = (entry.display_name or "").strip()
        if (not display_name or len(display_name) > _MAX_DISPLAY_NAME
                or "/" in display_name or "\\" in display_name
                or "\x00" in display_name or ".." in display_name):
            safe_name = display_name[:80]
            self._fail(outcome, safe_name, "invalid_filename",
                       "文件名包含路径分隔符或非法字符，已拒绝；文件名仅作显示用途。",
                       "文件名包含路径分隔符或非法字符。")
            return None
        data = entry.data
        if len(data) == 0:
            self._fail(outcome, display_name, "empty_file", "文件内容为空。", "文件内容为空。")
            return None
        if len(data) > self._limits.max_file_bytes:
            self._fail(outcome, display_name, "too_large",
                       f"文件超过单文件 {self._limits.max_file_bytes} 字节上限。",
                       "文件超过单文件大小上限。")
            return None
        if running_total + len(data) > self._limits.max_total_bytes:
            self._fail(outcome, display_name, "total_too_large",
                       f"合计超过 {self._limits.max_total_bytes} 字节单次上传上限。",
                       "合计大小超过单次上传上限。")
            return None
        extension = display_name.rsplit(".", 1)[-1] if "." in display_name else ""
        try:
            if self._parse_subprocess:
                parsed = self._parse_isolated(data, extension=extension,
                                              mime_type=entry.mime_type)
            else:
                parsed = parse_attachment(data, extension=extension,
                                          mime_type=entry.mime_type,
                                          limits=self._limits, clock=self._clock)
        except AttachmentParseError as error:
            self._fail(outcome, display_name, error.code, error.message, error.message)
            return None
        return self._persist(report_id=report_id, task_id=task_id, retention=retention,
                             mime_type=entry.mime_type, display_name=display_name,
                             extension=extension, data=data, parsed=parsed,
                             supplement_store=supplement_store, outcome=outcome)

    @staticmethod
    def _fail(outcome: AttachmentUploadOutcome, display_name: str, code: str,
              message: str, impact: str) -> None:
        gap = attachment_gap(display_name, code, impact)
        outcome.failed.append(AttachmentFailure(
            display_name=display_name, code=code, message=message, gap=gap))
        outcome.gaps.append(gap)

    def _parse_isolated(self, data: bytes, *, extension: str, mime_type: str) -> ParsedAttachment:
        """把解析交给一次性子进程（Job Object 内存硬限制 + 环境白名单）。

        子进程永不持有加密密钥；崩溃/超时都转成结构化 AttachmentParseError，
        绝不影响其他文件或 Web 进程。
        """
        request = {
            "data_b64": base64.b64encode(bytes(data)).decode("ascii"),
            "extension": extension,
            "mime_type": mime_type,
            "limits": self._limits.model_dump(),
        }
        response = run_isolated_parse(
            request,
            timeout_seconds=self._limits.parse_time_budget_seconds
            + SUBPROCESS_TIMEOUT_GRACE_SECONDS)
        if response.get("ok"):
            return ParsedAttachment(**response["parsed"])
        raise AttachmentParseError(response.get("code", "parse_failed"),
                                   response.get("message", "附件解析失败。"))

    def _persist(self, *, report_id: str, task_id: str, retention: str,
                 mime_type: str, display_name: str, extension: str, data: bytes,
                 parsed: ParsedAttachment, supplement_store,
                 outcome: AttachmentUploadOutcome) -> AttachmentRecord:
        attachment_id = uuid.uuid4().hex
        excerpt_path: str | None = None
        if retention == "excerpt":
            self.attachments_dir.mkdir(parents=True, exist_ok=True)
            path = self.excerpt_path_for(attachment_id)
            path.write_text(parsed.text[:self._limits.excerpt_chars], encoding="utf-8")
            excerpt_path = str(path.relative_to(self._project_root))
        usable_text = parsed.text.strip()
        supplement: ResearchSupplement | None = None
        if usable_text:
            source_note = f"附件「{display_name}」（{parsed.kind}/{parsed.parse_method}）"
            if parsed.truncated:
                source_note += "，解析文本已截断"
            supplement = supplement_store.add(
                report_id=report_id, kind=SupplementKind.USER_PROVIDED,
                content=usable_text[:MAX_SUPPLEMENT_TEXT],
                fields={"dimension": "local_attachment", "source_note": source_note})
        record = AttachmentRecord(
            id=attachment_id, report_id=report_id, task_id=task_id,
            display_name=display_name, extension=extension.lower(),
            mime_type=(mime_type or "").split(";", 1)[0].strip().lower(),
            size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
            kind=parsed.kind, parse_method=parsed.parse_method,
            storage_policy=STORAGE_POLICY_BY_RETENTION[retention],
            raw_file_retained=False, excerpt_path=excerpt_path,
            truncated=parsed.truncated, warnings=list(parsed.warnings),
            stats=dict(parsed.stats),
            supplement_id=supplement.id if supplement else None,
            # 候选值在解析时确定性提取并存档，预览/清单随时可回显；
            # 只预填表单，确认必须经 PUT mapping（宁缺毋滥，见模块口径）。
            mapping_candidates=extract_mapping_candidates(
                parsed.text[:self._limits.max_parse_chars]).model_dump(),
            created_at=datetime.now(timezone.utc).isoformat())
        self._repository.insert_attachment(record)
        outcome.saved.append(record)
        if supplement is not None:
            outcome.supplements.append(supplement)
        return record
