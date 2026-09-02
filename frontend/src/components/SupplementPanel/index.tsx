import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Input, Radio, Space, Tag, Typography } from "antd";
import { useState } from "react";
import {
  confirmAttachmentMapping,
  deleteAttachment,
  listReportSupplements,
  uploadSupplementFiles,
  type AttachmentRetention,
  type AttachmentView,
  type MappingInput,
  type UploadOutcomeView,
} from "../../api/supplements";
import { normalizeError } from "../../api/client";
import type { SupplementView } from "../../api/research";

/**
 * SupplementPanel (plan 04 sections 2.5/2.6): 上传本地资料、查看解析结果、
 * 确认候选字段映射、删除未引用资料。隐私红线写在界面里：资料只保存本机、
 * 原始文件默认不留存、保存 ≠ 同意外发、绝不自动再分析——再分析必须由用户
 * 显式勾选并发起。
 */
export default function SupplementPanel({
  reportId,
  supplements,
  onSupplementsChanged,
}: {
  reportId: string;
  supplements: SupplementView[];
  onSupplementsChanged?: (items: SupplementView[]) => void;
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [retention, setRetention] = useState<AttachmentRetention>("reference_only");
  const [uploading, setUploading] = useState(false);
  const [outcome, setOutcome] = useState<UploadOutcomeView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, MappingInput>>({});
  const [busyId, setBusyId] = useState<string | null>(null);
  const listQuery = useQuery({
    queryKey: ["supplement-attachments", reportId],
    queryFn: () => listReportSupplements(reportId),
  });

  const refresh = async () => {
    const { data } = await listQuery.refetch();
    onSupplementsChanged?.(data?.supplements ?? []);
  };

  const upload = async () => {
    // 以 input 元素上的实际文件列表为准（避免受状态时序影响）。
    const input = document.getElementById("supplement-file-input") as HTMLInputElement | null;
    const picked = [...(input?.files ?? [])];
    if (picked.length === 0) return;
    setUploading(true);
    setError(null);
    try {
      const result = await uploadSupplementFiles(reportId, picked, retention);
      setOutcome(result);
      setFiles([]);
      if (input) input.value = "";
      await refresh();
    } catch (failure: unknown) {
      setError(normalizeError(failure).message);
    } finally {
      setUploading(false);
    }
  };

  const confirmMapping = async (attachment: AttachmentView) => {
    // 候选值预填 + 用户草稿合并：显式清空的项不提交；点击「确认映射」即显式确认。
    const draft = drafts[attachment.id] ?? {};
    const merged: Record<string, string> = { ...(attachment.mapping_candidates ?? {}) };
    for (const [key, value] of Object.entries(draft)) {
      if (typeof value === "string" && value.length > 0) {
        merged[key] = value;
      } else {
        delete merged[key];
      }
    }
    setBusyId(attachment.id);
    setError(null);
    try {
      await confirmAttachmentMapping(attachment.id, merged);
      await refresh();
    } catch (failure: unknown) {
      setError(normalizeError(failure).message);
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (attachment: AttachmentView) => {
    setBusyId(attachment.id);
    setError(null);
    try {
      await deleteAttachment(attachment.id);
      await refresh();
    } catch (failure: unknown) {
      setError(normalizeError(failure).message);
    } finally {
      setBusyId(null);
    }
  };

  const referencedIds = new Set(
    supplements.filter((item) => item.referenced_by_report_id).map((item) => item.id),
  );
  const attachments = listQuery.data?.attachments ?? [];

  return (
    <Space orientation="vertical" style={{ width: "100%" }} size="small">
      <Alert
        type="info"
        showIcon
        title="资料只保存到本机数据库；原始文件默认不留存（仅解析文本入库）。保存 ≠ 同意外发，也不会自动再分析。"
      />
      {error && <Alert type="error" showIcon title={error} />}
      <Space size="small" wrap>
        <input
          id="supplement-file-input"
          type="file"
          multiple
          aria-label="选择附件文件"
          onChange={(event) => setFiles([...(event.target.files ?? [])])}
        />
        <Radio.Group
          value={retention}
          onChange={(event) => setRetention(event.target.value)}
        >
          <Radio value="reference_only">仅记录（不保留文件）</Radio>
          <Radio value="excerpt">保留解析摘录</Radio>
        </Radio.Group>
        <Button
          aria-label="上传附件"
          disabled={files.length === 0}
          loading={uploading}
          onClick={() => void upload()}
        >
          上传附件
        </Button>
      </Space>
      {outcome && (
        <Space orientation="vertical" style={{ width: "100%" }} size={2}>
          {outcome.saved.map((item) => (
            <Typography.Text key={item.id} type="success">
              {"已解析： " + item.display_name + "（" + item.kind + "/" + item.parse_method + "）"}
            </Typography.Text>
          ))}
          {outcome.failed.map((item) => (
            <Alert
              key={item.display_name + item.code}
              type="warning"
              showIcon
              title={item.display_name + "：" + item.message}
              description={
                item.gap && item.gap.remedies.length > 0
                  ? "可以怎样补： " + item.gap.remedies.join("；")
                  : undefined
              }
            />
          ))}
        </Space>
      )}
      {attachments.map((attachment) => {
        const mapped = attachment.mapping_confirmed_at != null;
        const referenced = attachment.supplement_id != null && referencedIds.has(attachment.supplement_id);
        return (
          <Space
            key={attachment.id}
            orientation="vertical"
            size={2}
            style={{ width: "100%" }}
            aria-label={"附件 " + attachment.display_name}
          >
            <Space size="small" wrap>
              <Typography.Text strong>{attachment.display_name}</Typography.Text>
              <Tag>{attachment.kind + "/" + attachment.parse_method}</Tag>
              <Tag color={attachment.storage_policy === "excerpt" ? "blue" : "default"}>
                {attachment.storage_policy === "excerpt" ? "保留摘录" : "仅记录"}
              </Tag>
              {mapped ? (
                <Tag color="green">
                  {"已确认映射" + (attachment.mapping["period"] ? "：" + attachment.mapping["period"] : "")}
                </Tag>
              ) : (
                <Tag color="orange">候选待确认</Tag>
              )}
              {referenced && <Tag color="purple">已被再分析引用</Tag>}
              {!referenced && (
                <Button
                  size="small"
                  aria-label={"删除附件 " + attachment.display_name}
                  loading={busyId === attachment.id}
                  onClick={() => void remove(attachment)}
                >
                  删除
                </Button>
              )}
            </Space>
            {!mapped && (
              <Space size="small" wrap aria-label={"映射 " + attachment.display_name}>
                <Input
                  size="small"
                  aria-label={"映射标的 " + attachment.display_name}
                  placeholder="标的 600519.SH"
                  style={{ width: 150 }}
                  value={drafts[attachment.id]?.security_id
                    ?? attachment.mapping_candidates?.security_id ?? ""}
                  onChange={(event) =>
                    setDrafts((prev) => ({
                      ...prev,
                      [attachment.id]: { ...prev[attachment.id], security_id: event.target.value },
                    }))
                  }
                />
                <Input
                  size="small"
                  aria-label={"映射期间 " + attachment.display_name}
                  placeholder="期间 2024Q1"
                  style={{ width: 130 }}
                  value={drafts[attachment.id]?.period
                    ?? attachment.mapping_candidates?.period ?? ""}
                  onChange={(event) =>
                    setDrafts((prev) => ({
                      ...prev,
                      [attachment.id]: { ...prev[attachment.id], period: event.target.value },
                    }))
                  }
                />
                <Input
                  size="small"
                  aria-label={"映射单位 " + attachment.display_name}
                  placeholder="单位 CNY"
                  style={{ width: 110 }}
                  value={drafts[attachment.id]?.unit
                    ?? attachment.mapping_candidates?.unit ?? ""}
                  onChange={(event) =>
                    setDrafts((prev) => ({
                      ...prev,
                      [attachment.id]: { ...prev[attachment.id], unit: event.target.value },
                    }))
                  }
                />
                <Button
                  size="small"
                  aria-label={"确认映射 " + attachment.display_name}
                  loading={busyId === attachment.id}
                  onClick={() => void confirmMapping(attachment)}
                >
                  确认映射
                </Button>
              </Space>
            )}
          </Space>
        );
      })}
      {attachments.length === 0 && (
        <Typography.Text type="secondary">
          还没有附件资料；上传的文件只在本机解析，原始文件默认不留存。
        </Typography.Text>
      )}
    </Space>
  );
}
