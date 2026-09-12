import { finNum } from "./tokens";

export interface LadderLevel {
  /** 板位，如 "1板"、"6+板" 或后端返回的 label。 */
  label: string;
  count: number;
  /** 该板位代表股（可选）。 */
  stocks?: Array<{ name: string; thscode?: string; sealed?: boolean | null }>;
}

/**
 * 连板梯队脉冲（实践 04/12 基型）：行 = 板位（高度向下递减），
 * 强度条编码家数，成员以 chip 展示；封板状态用 ● / ○ 标注。
 * 纯 DOM 实现，保证可访问性与测试可渲染。
 */
export default function LadderPulseMatrix({
  levels,
  emptyText,
}: {
  levels: LadderLevel[];
  emptyText?: string;
}) {
  const max = Math.max(0, ...levels.map((level) => level.count));
  if (!levels.length || max === 0) {
    return (
      <p className="fin-ladder fin-ladder--empty">
        {emptyText ?? "当次未取得可解析的连板梯队。"}
      </p>
    );
  }
  return (
    <div className="fin-ladder" role="table" aria-label="连板梯队脉冲">
      {levels.map((level) => (
        <div className="fin-ladder__row" role="row" key={level.label}>
          <span className="fin-ladder__level" role="cell">
            {level.label}
          </span>
          <span className="fin-ladder__track" role="cell">
            <i
              className="fin-ladder__fill"
              style={{
                width: `${Math.max(4, (level.count / max) * 100).toFixed(1)}%`,
              }}
            />
          </span>
          <span className="fin-ladder__count" role="cell">
            {finNum(level.count, 0)}
          </span>
          {level.stocks && level.stocks.length > 0 && (
            <span className="fin-ladder__chips" role="cell">
              {level.stocks.slice(0, 12).map((stock) => (
                <span className="fin-chip" key={stock.thscode ?? stock.name}>
                  {stock.sealed === true
                    ? "●"
                    : stock.sealed === false
                      ? "○"
                      : ""}{" "}
                  {stock.name}
                  {stock.sealed !== null && stock.sealed !== undefined && (
                    <small>{stock.sealed ? "次日封住" : "次日未封"}</small>
                  )}
                </span>
              ))}
              {level.stocks.length > 12 && (
                <span className="fin-chip">+{level.stocks.length - 12}</span>
              )}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}
