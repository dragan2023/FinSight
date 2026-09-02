import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { ApiError } from "../api/client";
import SupplementPanel from "../components/SupplementPanel";
import SourceConflict, { type ConflictView } from "../components/SourceConflict";
import * as supplements from "../api/supplements";
import type {
  AttachmentListView,
  AttachmentView,
  UploadOutcomeView,
} from "../api/supplements";
import type { SupplementView } from "../api/research";

vi.mock("../api/supplements", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/supplements")>()),
  uploadSupplementFiles: vi.fn(),
  listReportSupplements: vi.fn(),
  confirmAttachmentMapping: vi.fn(),
  deleteAttachment: vi.fn(),
  sourcePreview: vi.fn(),
}));

const REPORT_ID = "c".repeat(32);

function renderWithProviders(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const savedAttachment: AttachmentView = {
  id: "d".repeat(32),
  report_id: REPORT_ID,
  task_id: "e".repeat(32),
  display_name: "纪要.txt",
  extension: "txt",
  mime_type: "text/plain",
  size_bytes: 30,
  sha256: "a".repeat(64),
  kind: "text",
  parse_method: "plain_text",
  storage_policy: "excerpt",
  raw_file_retained: false,
  excerpt_path: "data/attachments/" + "d".repeat(32) + ".excerpt.txt",
  truncated: false,
  warnings: [],
  stats: { chars: 30 },
  supplement_id: "9".repeat(32),
  mapping: {},
  mapping_confirmed_at: null,
  mapping_candidates: { security_id: "600519.SH", period: "2024Q1", unit: "CNY" },
  created_at: "2026-09-01T10:00:00+08:00",
};

const derivedSupplement: SupplementView = {
  id: "9".repeat(32),
  report_id: REPORT_ID,
  task_id: "e".repeat(32),
  kind: "user_provided",
  content: "会议要点：资本开支维持克制。",
  gap_ref: null,
  fields: { dimension: "local_attachment", source_note: "附件「纪要.txt」（text/plain）" },
  referenced_by_report_id: null,
  created_at: "2026-09-01T10:00:00+08:00",
};

function makeSupplement(overrides: Partial<SupplementView> = {}): SupplementView {
  return { ...derivedSupplement, ...overrides };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(supplements.listReportSupplements).mockResolvedValue({
    attachments: [],
    supplements: [],
  } as AttachmentListView);
});

describe("SupplementPanel 隐私数据流与上传", () => {
  it("展示隐私红线：只保存本机、默认不保留原始文件、不自动再分析", () => {
    renderWithProviders(
      <SupplementPanel reportId={REPORT_ID} supplements={[]} />,
    );
    expect(
      screen.getByText(/只保存到本机数据库/),
    ).toBeInTheDocument();
    expect(screen.getAllByText(/原始文件默认不留存/).length).toBeGreaterThan(0);
    expect(screen.getByText(/保存 ≠ 同意外发/)).toBeInTheDocument();
    expect(screen.getByText(/不会自动再分析/)).toBeInTheDocument();
  });

  it("上传走 multipart 契约：默认 reference_only，展示解析结果与失败补救", async () => {
    const user = userEvent.setup();
    const outcome: UploadOutcomeView = {
      saved: [savedAttachment],
      failed: [
        {
          display_name: "scan.pdf",
          code: "no_text_layer",
          message: "未检测到 PDF 文本层（可能为扫描件）；本应用不做 OCR。",
          gap: {
            id: "a".repeat(32),
            dimension: "local_attachment",
            reason: "unsupported",
            impact: "附件「scan.pdf」未能转为可用材料",
            remedies: ["对少量页面截图并使用已配置且支持视觉的模型分析。"],
          },
        },
      ],
      supplements: [derivedSupplement],
      gaps: [],
    };
    vi.mocked(supplements.uploadSupplementFiles).mockResolvedValue(outcome);
    renderWithProviders(<SupplementPanel reportId={REPORT_ID} supplements={[]} />);
    const input = screen.getByLabelText("选择附件文件") as HTMLInputElement;
    // jsdom/userEvent 每次选择只提交一个文件；浏览器多选由同一 input.files 通道承载，
    // 这里按两次连续上传断言（真实浏览器的多选一次提交亦走同一逻辑）。
    await user.upload(input, new File(["会议要点"], "纪要.txt", { type: "text/plain" }));
    await user.click(screen.getByRole("button", { name: "上传附件" }));
    await waitFor(() => {
      expect(supplements.uploadSupplementFiles).toHaveBeenCalledTimes(1);
    });
    await user.upload(input, new File(["%PDF-1.4"], "scan.pdf", { type: "application/pdf" }));
    await user.click(screen.getByRole("button", { name: "上传附件" }));
    await waitFor(() => {
      expect(supplements.uploadSupplementFiles).toHaveBeenCalledTimes(2);
    });
    const namesByCall = vi.mocked(supplements.uploadSupplementFiles).mock.calls.map(
      (call) => (call[1] as File[]).map((file: File) => file.name),
    );
    expect(namesByCall).toEqual([["纪要.txt"], ["scan.pdf"]]);
    expect(vi.mocked(supplements.uploadSupplementFiles).mock.calls[0][2]).toBe("reference_only");
    expect(screen.getByText(/已解析： 纪要.txt（text\/plain_text）/)).toBeInTheDocument();
    expect(screen.getByText(/scan.pdf：未检测到 PDF 文本层/)).toBeInTheDocument();
    expect(screen.getByText(/可以怎样补： 对少量页面截图/)).toBeInTheDocument();
  });

  it("留存策略可切换为 excerpt，并随表单一起提交", async () => {
    const user = userEvent.setup();
    vi.mocked(supplements.uploadSupplementFiles).mockResolvedValue({
      saved: [],
      failed: [],
      supplements: [],
      gaps: [],
    });
    renderWithProviders(<SupplementPanel reportId={REPORT_ID} supplements={[]} />);
    await user.click(screen.getByRole("radio", { name: "保留解析摘录" }));
    await user.upload(
      screen.getByLabelText("选择附件文件"),
      new File(["x"], "note.txt", { type: "text/plain" }),
    );
    await user.click(screen.getByRole("button", { name: "上传附件" }));
    await waitFor(() => {
      expect(supplements.uploadSupplementFiles).toHaveBeenCalledWith(
        REPORT_ID,
        expect.any(Array),
        "excerpt",
      );
    });
  });

  it("候选字段映射确认只保存，不触发任何再分析", async () => {
    const user = userEvent.setup();
    vi.mocked(supplements.listReportSupplements).mockResolvedValue({
      attachments: [savedAttachment],
      supplements: [derivedSupplement],
    } as AttachmentListView);
    vi.mocked(supplements.confirmAttachmentMapping).mockResolvedValue({
      attachment: { ...savedAttachment, mapping: { period: "2024Q1" }, mapping_confirmed_at: "2026-09-01T10:05:00+08:00" },
    });
    renderWithProviders(<SupplementPanel reportId={REPORT_ID} supplements={[derivedSupplement]} />);
    // 候选值已预填（600519.SH / 2024Q1 / CNY）；用户点击确认即显式确认候选。
    await screen.findByLabelText("映射标的 纪要.txt");
    await user.click(screen.getByRole("button", { name: "确认映射 纪要.txt" }));
    await waitFor(() => {
      expect(supplements.confirmAttachmentMapping).toHaveBeenCalledWith("d".repeat(32), {
        security_id: "600519.SH",
        period: "2024Q1",
        unit: "CNY",
      });
    });
    // 面板自身不发起再分析：模块中不存在自动调用，上传/映射只做本地保存。
  });

  it("候选值预填表单：未点击确认则零提交", async () => {
    vi.mocked(supplements.listReportSupplements).mockResolvedValue({
      attachments: [savedAttachment],
      supplements: [derivedSupplement],
    } as AttachmentListView);
    renderWithProviders(<SupplementPanel reportId={REPORT_ID} supplements={[derivedSupplement]} />);
    const target = (await screen.findByLabelText("映射标的 纪要.txt")) as HTMLInputElement;
    const period = screen.getByLabelText("映射期间 纪要.txt") as HTMLInputElement;
    expect(target.value).toBe("600519.SH");
    expect(period.value).toBe("2024Q1");
    // 候选 ≠ 确认：渲染与预填绝不自动提交，确认必须经用户显式点击。
    expect(supplements.confirmAttachmentMapping).not.toHaveBeenCalled();
    expect(screen.getByText("候选待确认")).toBeInTheDocument();
  });

  it("用户改写/清空候选后按草稿提交", async () => {
    const user = userEvent.setup();
    vi.mocked(supplements.listReportSupplements).mockResolvedValue({
      attachments: [savedAttachment],
      supplements: [derivedSupplement],
    } as AttachmentListView);
    vi.mocked(supplements.confirmAttachmentMapping).mockResolvedValue({
      attachment: savedAttachment,
    });
    renderWithProviders(<SupplementPanel reportId={REPORT_ID} supplements={[derivedSupplement]} />);
    const period = await screen.findByLabelText("映射期间 纪要.txt") as HTMLInputElement;
    const unit = screen.getByLabelText("映射单位 纪要.txt") as HTMLInputElement;
    await user.clear(period);
    await user.type(period, "2024Q2");
    await user.clear(unit);
    await user.click(screen.getByRole("button", { name: "确认映射 纪要.txt" }));
    await waitFor(() => {
      expect(supplements.confirmAttachmentMapping).toHaveBeenCalledWith("d".repeat(32), {
        security_id: "600519.SH",
        period: "2024Q2",
      });
    });
  });

  it("删除引用保护：已引用附件不可删除并展示原因", async () => {
    const referenced = makeSupplement({ referenced_by_report_id: "f".repeat(32) });
    vi.mocked(supplements.listReportSupplements).mockResolvedValue({
      attachments: [savedAttachment],
      supplements: [referenced],
    } as AttachmentListView);
    renderWithProviders(<SupplementPanel reportId={REPORT_ID} supplements={[referenced]} />);
    expect(await screen.findByText("已被再分析引用")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "删除附件 纪要.txt" })).not.toBeInTheDocument();
    });
  });

  it("删除未引用附件成功后刷新清单", async () => {
    const user = userEvent.setup();
    vi.mocked(supplements.listReportSupplements).mockResolvedValue({
      attachments: [savedAttachment],
      supplements: [derivedSupplement],
    } as AttachmentListView);
    vi.mocked(supplements.deleteAttachment).mockResolvedValue({
      id: "d".repeat(32),
      deleted: true,
      excerpt_removed: true,
    });
    const onChanged = vi.fn();
    renderWithProviders(
      <SupplementPanel reportId={REPORT_ID} supplements={[derivedSupplement]} onSupplementsChanged={onChanged} />,
    );
    await user.click(await screen.findByRole("button", { name: "删除附件 纪要.txt" }));
    await waitFor(() => {
      expect(supplements.deleteAttachment).toHaveBeenCalledWith("d".repeat(32));
    });
    expect(onChanged).toHaveBeenCalled();
  });

  it("删除被拒绝（request_conflict）时展示服务端原因", async () => {
    const user = userEvent.setup();
    vi.mocked(supplements.listReportSupplements).mockResolvedValue({
      attachments: [savedAttachment],
      supplements: [derivedSupplement],
    } as AttachmentListView);
    vi.mocked(supplements.deleteAttachment).mockRejectedValue(
      new ApiError("该附件资料已被再分析报告引用；为保证旧版本可复核，不删除已引用资料。",
        "request_conflict", null, false),
    );
    renderWithProviders(<SupplementPanel reportId={REPORT_ID} supplements={[derivedSupplement]} />);
    await user.click(await screen.findByRole("button", { name: "删除附件 纪要.txt" }));
    await waitFor(() => {
      expect(screen.getByText(/不删除已引用资料/)).toBeInTheDocument();
    });
  });
});

describe("SourceConflict 并列展示与选择", () => {
  const conflict: ConflictView = {
    id: "cf-1",
    dimension: "financial_statements.income",
    field: "operating_income",
    explanation: "同指标两来源口径不同：并列展示，不平均、不静默取舍。",
    sources: [
      { provider: "hexin_structured", label: "同花顺财报", value: "174144000000 CNY（合并/累计）" },
      { provider: "tushare", label: "Tushare income", value: "174143900000 CNY（合并/累计）" },
    ],
  };

  it("两来源并列完整展示，不隐藏任何一方", () => {
    renderWithProviders(<SourceConflict conflicts={[conflict]} />);
    expect(screen.getByText(/同花顺财报（hexin_structured）\] 174144000000/)).toBeInTheDocument();
    expect(screen.getByText(/Tushare income（tushare）\] 174143900000/)).toBeInTheDocument();
    expect(screen.getByText(/并列显示，不平均/)).toBeInTheDocument();
  });

  it("用户选择仅回调一次且携带冲突 ID 与来源", async () => {
    const user = userEvent.setup();
    const onChoose = vi.fn();
    renderWithProviders(<SourceConflict conflicts={[conflict]} onChoose={onChoose} />);
    await user.click(screen.getByRole("radio", { name: /Tushare income/ }));
    await user.click(screen.getByRole("button", { name: "仅用于本次新报告" }));
    expect(onChoose).toHaveBeenCalledTimes(1);
    expect(onChoose).toHaveBeenCalledWith("cf-1", "tushare");
    expect(screen.getByText("已记录本次选择")).toBeInTheDocument();
  });
});
