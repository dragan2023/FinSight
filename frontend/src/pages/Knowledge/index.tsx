import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Drawer, Input, Popconfirm, Select, Space, Table, Tag, Typography, Upload } from "antd";
import { DeleteOutlined, ReloadOutlined, SearchOutlined, UploadOutlined } from "@ant-design/icons";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import ErrorNotice from "../../components/ErrorNotice";
import {
  deleteKbDocument,
  listKbChunks,
  listKbDocuments,
  rebuildKbDocument,
  searchKb,
  updateKbDocumentMetadata,
  uploadKbDocument,
  type KbDocument,
  type KbMetadata,
  type KbSearchHit,
} from "../../api/kb";

const EMBEDDING_STATE_LABEL: Record<string, string> = {
  full: "已全部向量化",
  partial: "部分向量化",
  none: "关键词索引",
};

function readFileAsArrayBuffer(file: File): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(reader.error ?? new Error("读取文件失败"));
    reader.readAsArrayBuffer(file);
  });
}

function formatBytes(size: number): string {
  if (size >= 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  if (size >= 1024) return `${(size / 1024).toFixed(0)} KB`;
  return `${size} B`;
}

function formatTime(value: string): string {
  return value.slice(0, 16).replace("T", " ");
}

function CitationLine({ hit }: { hit: KbSearchHit }) {
  const parts: string[] = [];
  if (hit.heading) parts.push(hit.heading);
  if (hit.page !== null) parts.push(`第 ${hit.page} 页`);
  if (hit.line_start !== null) {
    parts.push(hit.line_end !== null && hit.line_end !== hit.line_start
      ? `第 ${hit.line_start}–${hit.line_end} 行`
      : `第 ${hit.line_start} 行`);
  }
  return (
    <span className="kb-cite">
      {hit.filename}
      {parts.length > 0 && <span className="kb-cite-loc">（{parts.join(" · ")}）</span>}
    </span>
  );
}

export default function Knowledge() {
  const queryClient = useQueryClient();
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<KbSearchHit[] | null>(null);
  // 证据回溯：?document=<id>&page=<n> 时打开该文档的分块原文抽屉并高亮目标页。
  const [searchParams, setSearchParams] = useSearchParams();
  const focusDocumentId = searchParams.get("document");
  const focusPage = searchParams.get("page");

  const documentsQuery = useQuery({ queryKey: ["kb-documents"], queryFn: listKbDocuments });
  const chunksQuery = useQuery({
    queryKey: ["kb-chunks", focusDocumentId],
    queryFn: () => listKbChunks(focusDocumentId as string),
    enabled: focusDocumentId !== null,
  });
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["kb-documents"] });

  const uploadMutation = useMutation({
    mutationFn: async () => {
      if (!pendingFile) throw new Error("未选择文件");
      const buffer = await readFileAsArrayBuffer(pendingFile);
      return uploadKbDocument(pendingFile.name, new Uint8Array(buffer));
    },
    onSuccess: () => {
      setPendingFile(null);
      invalidate();
    },
  });
  const deleteMutation = useMutation({
    mutationFn: deleteKbDocument,
    onSuccess: invalidate,
  });
  const rebuildMutation = useMutation({
    mutationFn: rebuildKbDocument,
    onSuccess: invalidate,
  });
  const metadataMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: KbMetadata }) => updateKbDocumentMetadata(id, data),
    onSuccess: invalidate,
  });
  const searchMutation = useMutation({
    mutationFn: () => searchKb(query.trim(), 5),
    onSuccess: (data) => setHits(data.hits),
  });

  const notice = uploadMutation.error ?? deleteMutation.error ?? rebuildMutation.error ?? metadataMutation.error ?? searchMutation.error;
  const documents: KbDocument[] = documentsQuery.data?.documents ?? [];
  const embeddingHint = documents.some((row) => row.embedding_state !== "none")
    ? "已启用嵌入：检索时查询词会发往嵌入服务用于向量化。"
    : "未启用嵌入：检索在本地关键词索引上完成，文档内容零外发；可在模型与 API 配置页接入 Qwen 文本向量化。";

  return (
    <div className="page-stack">
      <header className="page-heading">
        <h1>知识库</h1>
        <p>私有知识库：上传本地文档（PDF/Markdown/TXT/CSV/XLSX），解析与分块只在本机进行；检索命中可给出文档名与原文定位。</p>
      </header>
      {notice && (
        <ErrorNotice
          error={notice}
          retry={() => {
            uploadMutation.reset();
            deleteMutation.reset();
            rebuildMutation.reset();
            searchMutation.reset();
          }}
        />
      )}

      <div className="section-heading">
        <h2>文档库</h2>
        <span>{embeddingHint}</span>
      </div>
      <Space wrap style={{ marginBottom: 16 }}>
        <Upload
          maxCount={1}
          showUploadList={false}
          beforeUpload={(file) => {
            setPendingFile(file);
            return false;
          }}
        >
          <Button icon={<UploadOutlined aria-hidden />}>选择文档</Button>
        </Upload>
        <Button
          type="primary"
          icon={<UploadOutlined aria-hidden />}
          disabled={!pendingFile}
          loading={uploadMutation.isPending}
          onClick={() => uploadMutation.mutate()}
        >
          {pendingFile ? `解析并入库：${pendingFile.name}` : "解析并入库"}
        </Button>
        {pendingFile && (
          <Typography.Text type="secondary">
            仅支持 PDF / Markdown / TXT / CSV / TSV / XLSX，上限 25 MB。
          </Typography.Text>
        )}
      </Space>
      <Table<KbDocument>
        size="small"
        rowKey="id"
        loading={documentsQuery.isLoading}
        dataSource={documents}
        locale={{ emptyText: "尚未上传文档；上传后即可在下方检索，Agent 也能引用。" }}
        columns={[
          {
            title: "文档名",
            dataIndex: "filename",
            key: "filename",
            render: (_: unknown, row: KbDocument) => (
              <Button type="link" size="small" style={{ padding: 0 }}
                onClick={() => setSearchParams({ document: row.id })}>
                {row.filename}
              </Button>
            ),
          },
          { title: "类型", dataIndex: "kind", key: "kind", width: 90 },
          {
            title: "知识角色", key: "knowledge_role", width: 130,
            render: (_: unknown, row: KbDocument) => <Select size="small" value={row.knowledge_role ?? "source"}
              aria-label={`${row.filename} 的知识角色`}
              onChange={(knowledge_role: KbMetadata["knowledge_role"]) => metadataMutation.mutate({ id: row.id, data: { knowledge_role, tags: row.tags ?? [], review_state: row.review_state ?? "unreviewed", effective_at: row.effective_at ?? null } })}
              options={[{ value: "source", label: "来源" }, { value: "strategy", label: "策略" }, { value: "hypothesis", label: "假设" }]} />,
          },
          {
            title: "审核", key: "review_state", width: 120,
            render: (_: unknown, row: KbDocument) => <Select size="small" value={row.review_state ?? "unreviewed"}
              aria-label={`${row.filename} 的审核状态`}
              onChange={(review_state: KbMetadata["review_state"]) => metadataMutation.mutate({ id: row.id, data: { knowledge_role: row.knowledge_role ?? "source", tags: row.tags ?? [], review_state, effective_at: row.effective_at ?? null } })}
              options={[{ value: "unreviewed", label: "未审核" }, { value: "approved", label: "已审核" }, { value: "retired", label: "已退役" }]} />,
          },
          {
            title: "标签（回车保存）", key: "tags", width: 180,
            render: (_: unknown, row: KbDocument) => <Input size="small" defaultValue={(row.tags ?? []).join(", ")}
              placeholder="固定词表：a-share、days_20、trend…"
              aria-label={`${row.filename} 的标签`}
              onPressEnter={(event) => {
                const tags = event.currentTarget.value.split(",").map((item) => item.trim()).filter(Boolean);
                metadataMutation.mutate({ id: row.id, data: { knowledge_role: row.knowledge_role ?? "source", tags, review_state: row.review_state ?? "unreviewed", effective_at: row.effective_at ?? null } });
              }} />,
          },
          {
            title: "生效日", key: "effective_at", width: 160,
            render: (_: unknown, row: KbDocument) => <Input size="small" type="date" defaultValue={row.effective_at ?? ""}
              aria-label={`${row.filename} 的生效日`}
              onChange={(event) => {
                const value = event.currentTarget.value.trim();
                // type=date 的值只会是完整 YYYY-MM-DD 或空串，可直接保存。
                if (value === (row.effective_at ?? "")) return;
                metadataMutation.mutate({ id: row.id, data: { knowledge_role: row.knowledge_role ?? "source", tags: row.tags ?? [], review_state: row.review_state ?? "unreviewed", effective_at: value || null } });
              }} />,
          },
          { title: "大小", dataIndex: "byte_size", key: "byte_size", width: 90, render: (value: number) => formatBytes(value) },
          { title: "分块", dataIndex: "chunk_count", key: "chunk_count", width: 80 },
          {
            title: "向量索引",
            key: "embedding",
            width: 130,
            render: (_: unknown, row: KbDocument) => EMBEDDING_STATE_LABEL[row.embedding_state] ?? row.embedding_state,
          },
          {
            title: "解析告警",
            dataIndex: "warnings",
            key: "warnings",
            render: (warnings: string[]) =>
              warnings.length > 0 ? <Typography.Text type="warning">{warnings[0]}</Typography.Text> : "—",
          },
          { title: "上传时间", dataIndex: "created_at", key: "created_at", width: 150, render: (value: string) => formatTime(value) },
          {
            title: "操作",
            key: "actions",
            width: 170,
            render: (_: unknown, row: KbDocument) => (
              <Space size="small">
                <Button
                  size="small"
                  icon={<ReloadOutlined aria-hidden />}
                  loading={rebuildMutation.isPending && rebuildMutation.variables === row.id}
                  onClick={() => rebuildMutation.mutate(row.id)}
                >
                  重建索引
                </Button>
                <Popconfirm title="删除该文档及其全部分块？" onConfirm={() => deleteMutation.mutate(row.id)}>
                  <Button size="small" danger icon={<DeleteOutlined aria-hidden />}>删除</Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />

      <div className="section-heading">
        <h2>检索预览</h2>
        <span>检索是主动操作：命中显示摘录、文档名与原文定位，可核验后再引用。</span>
      </div>
      <Space.Compact style={{ width: "100%", maxWidth: 640, marginBottom: 16 }}>
        <Input
          placeholder="输入检索词，例如：主营业务 或 回购计划"
          value={query}
          maxLength={200}
          onChange={(event) => setQuery(event.target.value)}
          onPressEnter={() => query.trim() && searchMutation.mutate()}
          aria-label="知识库检索词"
        />
        <Button
          type="primary"
          icon={<SearchOutlined aria-hidden />}
          disabled={!query.trim()}
          loading={searchMutation.isPending}
          onClick={() => searchMutation.mutate()}
        >
          检索
        </Button>
      </Space.Compact>
      {searchMutation.data?.warnings.map((warning) => (
        <Typography.Text key={warning} type="warning" style={{ display: "block", marginBottom: 8 }}>
          {warning}
        </Typography.Text>
      ))}
      {searchMutation.data && (
        <Typography.Paragraph type="secondary">{searchMutation.data.note}</Typography.Paragraph>
      )}
      <ul className="news-timeline">
        {(hits ?? []).map((hit) => (
          <li className="news-line" key={hit.chunk_id}>
            <span className="kb-hit-head">
              <CitationLine hit={hit} />
              <Tag>{hit.channels.includes("vector") && hit.channels.includes("fts") ? "向量+关键词" : hit.channels.includes("vector") ? "向量" : "关键词"}</Tag>
            </span>
            <span className="news-line-summary">{hit.excerpt}</span>
          </li>
        ))}
      </ul>

      <Drawer
        width={560}
        open={focusDocumentId !== null}
        onClose={() => setSearchParams({})}
        title={chunksQuery.data?.document.filename ?? "文档原文"}
      >
        {chunksQuery.data && focusPage && (
          <Typography.Paragraph type="secondary">
            顾问证据指向第 {focusPage} 页，已高亮对应段落。
          </Typography.Paragraph>
        )}
        <ul className="kb-chunk-list">
          {(chunksQuery.data?.chunks ?? []).map((chunk) => {
            const focused = focusPage !== null && chunk.page !== null && String(chunk.page) === focusPage;
            return (
              <li key={chunk.chunk_id} className={focused ? "kb-chunk focused" : "kb-chunk"}>
                <span className="kb-cite">
                  {chunk.heading || "无标题"}
                  {chunk.page !== null && <span className="kb-cite-loc">（第 {chunk.page} 页）</span>}
                  {focused && <Tag color="green">证据指向</Tag>}
                </span>
                <Typography.Paragraph style={{ marginBottom: 0 }}>{chunk.content}</Typography.Paragraph>
              </li>
            );
          })}
        </ul>
        {chunksQuery.data && chunksQuery.data.chunks.length === 0 && (
          <Typography.Text type="secondary">该文档暂无分块内容。</Typography.Text>
        )}
      </Drawer>
    </div>
  );
}
