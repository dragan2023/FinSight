/** 图表数据侧的纯函数工具（无 React、无 echarts 依赖）。 */

/** 宽松转数字：字符串/null/undefined -> number|null（不抛错）。 */
export const toNum = (value: unknown): number | null => {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

/** 简单移动平均：对含缺口的序列按窗口计算，窗口内任一缺口则该点为 null。 */
export function sma(
  values: Array<number | null>,
  window: number,
): Array<number | null> {
  const output: Array<number | null> = [];
  let sum = 0;
  let valid = 0;
  for (let index = 0; index < values.length; index += 1) {
    const value = values[index];
    if (value === null) {
      valid = 0;
      sum = 0;
      output.push(null);
      continue;
    }
    sum += value;
    valid += 1;
    if (valid >= window) {
      output.push(sum / window);
      sum -= values[index - window + 1] as number;
      valid -= 1;
    } else {
      output.push(null);
    }
  }
  return output;
}
