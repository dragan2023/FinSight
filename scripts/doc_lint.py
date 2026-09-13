"""文档口径巡检（方案 01 P0-5 / 决策 D15）。

F10 的教训是：治理文档引用的 11 条路径里 **9 条已不存在**，方法卡写 v1 而代码是 v2。
人工核对过一次，但会再次腐化——所以做成脚本，可纳入提交前的例行检查。

三项检查：

1. **路径**：文档里反引号包起来的仓库路径必须真实存在（允许"唯一后缀"简写，
   例如 `advisor/graph.py` 能唯一匹配到 `backend/app/advisor/graph.py`）；
2. **版本号**：策略方法卡声明的版本必须与 `strategy/registry.py` 一致；
3. **常量**：说明书里写的顾问超时值必须与 `advisor/graph.py` 的常量一致。

用法：

    .\\.venv\\Scripts\\python.exe scripts\\doc_lint.py          # 只报告
    .\\.venv\\Scripts\\python.exe scripts\\doc_lint.py --strict # 有问题则退出码 1
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 反引号内的代码片段。
CODE_SPAN = re.compile(r"`([^`\n]+)`")
#: 看起来像仓库内文件路径的片段（可带 :行号 或 :起-止 / 可带 ()）。
PATH_LIKE = re.compile(
    r"^((?:backend|frontend|scripts|tests|docs|data|\.cache)/[A-Za-z0-9_./@\-*]+?\."
    r"(?:py|ts|tsx|js|jsx|md|json|jsonl|toml|txt|yml|yaml|bat|ps1|lock|sql))"
    r"(?::(\d+)(?:-(\d+))?)?(?:\(\))?$")
#: 明确提到"已删除/不存在/历史上"的句子，允许引用不存在的路径。
#: `原写法` 用于"修正前后对照"表格的旧值列——它描述的是**已经被改掉的**路径。
HISTORICAL = ("已删除", "已移除", "不存在", "历史上", "已失准", "曾经", "旧路径", "原 ", "已废止")
#: 显式忽略区域：`<!-- doc-lint: ignore-start -->` 与 `<!-- doc-lint: ignore-end -->` 之间
#: 的行不做路径检查（用于"修正前后对照"表格这类必须引用旧路径的地方）。
IGNORE_START = "<!-- doc-lint: ignore-start -->"
IGNORE_END = "<!-- doc-lint: ignore-end -->"

#: 已知的、刻意保留的历史引用：路径 → 理由。
#: 只放"文档在讲它已不存在"的路径；F10 里那些**本该修正**的失准路径不放这里，
#: 否则白名单会把待修项一起吞掉（这正是这份脚本第一次跑就暴露的问题）。
ALLOWED_MISSING = {
    "backend/app/market_data/tushare_client.py": "V2 已删除该能力，文档在说明删除原因",
    "backend/app/web_research/tavily.py": "V2 已删除该能力",
    "backend/app/web_research/public_reader.py": "V2 已删除该能力（且爬虫属红线）",
    "backend/app/web_research/url_safety.py": "随 public_reader 一并移除",
    "docs/FinSight改造升级计划/改造升级计划_v2.md": "上位依据文档已被删除（F10 记录）",
}


def _glob_pattern_regex(pattern: str, *, shorthand: bool = False) -> re.Pattern:
    """把通配写法转成**不跨目录**的正则：`*` 只匹配一层内的任意字符，`?` 匹配单字符。

    `shorthand=True` 用于"唯一后缀简写"（如 `api/*.py`）：允许**任意前缀目录**
    （`(?:^|.*/)` 可以跨 `/`），但通配本身仍不跨目录。

    早先的实现只认"开头的 `*`"（`pattern.lstrip("*")` 后做 endswith），
    于是 `scripts/governance_record_*_change.py`（中间通配，用来指代一族脚本）
    会被误报成路径不存在——2026-09-14 写接力文档时真的被它拦下。
    这里改成真正的 glob，但**刻意让 `*` 不跨 `/`**：否则 `api/*.py` 会连
    `api/sub/x.py` 也算匹配，反而放过写错的路径。
    """
    escaped = re.escape(pattern).replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
    prefix = r"(?:^|.*/)" if shorthand else "^"
    return re.compile(prefix + escaped + "$")


def _resolve(token: str, files: list[str]) -> str:
    """返回 ok / ok_shorthand / missing。简写有多解时视为 ok（都真实存在）。"""
    target = token.rstrip("/")
    if (ROOT / target).exists():
        return "ok"
    if "*" in target or "?" in target:
        rooted = _glob_pattern_regex(target)
        shorthand = _glob_pattern_regex(target, shorthand=True)
        rooted_ok = any(rooted.match(item) for item in files)
        shorthand_ok = any(shorthand.match(item) for item in files)
        if rooted_ok:
            return "ok"
        return "ok_shorthand" if shorthand_ok else "missing"
    suffix = "/" + target
    matches = [item for item in files if item.endswith(suffix) or item == target]
    if matches:
        return "ok_shorthand"
    return "missing"


def check_paths(markdown_files: list[Path], files: list[str]) -> tuple[list[str], int, int]:
    violations: list[str] = []
    checked = shorthand = 0
    for path in markdown_files:
        in_ignore_region = False
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            # 显式区域标记：用于"修正前后对照"这类**必然**引用旧路径的表格。
            # 比关键词启发式可靠——关键词只出现在表头，控制不到每一行。
            if IGNORE_START in line:
                in_ignore_region = True
                continue
            if IGNORE_END in line:
                in_ignore_region = False
                continue
            if in_ignore_region or any(word in line for word in HISTORICAL):
                continue
            for token in CODE_SPAN.findall(line):
                token = token.strip()
                match = PATH_LIKE.match(token)
                if not match:
                    continue
                candidate = match.group(1)
                if candidate in ALLOWED_MISSING:
                    continue
                if candidate.startswith("data/") and not (ROOT / candidate).exists():
                    continue  # 本机数据文件，不进 git，不参与存在性检查
                checked += 1
                result = _resolve(candidate, files)
                if result == "ok_shorthand":
                    shorthand += 1
                elif result == "missing":
                    violations.append(f"{path.relative_to(ROOT)}:{number} 路径不存在：{candidate}")
    return violations, checked, shorthand


def check_versions() -> list[str]:
    """策略方法卡声明的版本必须与 registry 一致（P0-5 第 2、8 条）。"""
    sys.path.insert(0, str(ROOT))
    from backend.app.strategy.registry import list_specs

    versions = {spec.id: spec.version for spec in list_specs()}
    violations = []
    for path in (ROOT / "docs" / "策略知识").glob("*方法卡*.md"):
        text = path.read_text(encoding="utf-8")
        declared = set(re.findall(r"v(\d+)", path.name + " " + text[:400]))
        for strategy_id, version in versions.items():
            if strategy_id.split("_v")[0] in text and version.lstrip("v") not in declared:
                violations.append(
                    f"{path.relative_to(ROOT)} 提到 {strategy_id} 但未声明其当前版本 {version}")
    return violations


def check_timeouts() -> list[str]:
    """说明书里的顾问超时值必须与代码常量一致（P0-5 第 4 条）。"""
    from backend.app.advisor.graph import (ADVISOR_REQUEST_TIMEOUT_SECONDS,
                                           ADVISOR_RUN_TIMEOUT_SECONDS)

    manual = ROOT / "docs" / "FinSight使用说明书.md"
    if not manual.exists():
        return []
    violations = []
    for number, line in enumerate(manual.read_text(encoding="utf-8").splitlines(), 1):
        if "最多" not in line:
            continue
        numbers = [int(value) for value in re.findall(r"(\d+)\s*秒", line)]
        for value in numbers:
            if value not in (int(ADVISOR_REQUEST_TIMEOUT_SECONDS), int(ADVISOR_RUN_TIMEOUT_SECONDS)):
                violations.append(
                    f"{manual.relative_to(ROOT)}:{number} 写了 {value} 秒，"
                    f"代码常量为 {int(ADVISOR_REQUEST_TIMEOUT_SECONDS)}/"
                    f"{int(ADVISOR_RUN_TIMEOUT_SECONDS)}：{line.strip()[:80]}")
    return violations


def check_navigation() -> list[str]:
    """说明书的左侧导航清单必须与 `Layout/index.tsx` 的 NavLink **逐项同序**。

    加这一项的原因很直接：批次 7 新增了「治理与落盘」导航项，说明书里的
    「共 15 项」当场失准——而前两项检查（路径 / 版本 / 常量）都抓不到这种漂移。
    """
    layout = ROOT / "frontend" / "src" / "components" / "Layout" / "index.tsx"
    manual = ROOT / "docs" / "FinSight使用说明书.md"
    if not layout.exists() or not manual.exists():
        return []
    labels = [label.strip() for label in re.findall(
        r"aria-hidden\s*/>\s*([^<\n]+?)\s*</NavLink>",
        layout.read_text(encoding="utf-8"), re.S) if label.strip()]
    text = manual.read_text(encoding="utf-8")
    match = re.search(r"左侧导航从上到下依次是：(.+?)（共", text, re.S)
    if not match:
        return ["FinSight使用说明书.md 里找不到「左侧导航从上到下依次是：…（共 N 项」句式，"
                "无法核对导航清单"]
    documented = [item.strip() for item in
                  match.group(1).replace("\n", "").split("、") if item.strip()]
    if documented == labels:
        return []
    return [f"说明书导航清单与实际不一致：文档 {len(documented)} 项、代码 {len(labels)} 项；"
            f"文档={documented}；代码={labels}"]


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    parser = argparse.ArgumentParser(description="文档口径巡检")
    parser.add_argument("--strict", action="store_true", help="有违规时退出码 1")
    parser.add_argument("--limit", type=int, default=40, help="每类最多列出多少条")
    args = parser.parse_args()

    markdown = sorted((ROOT / "docs").rglob("*.md"))
    files = [str(item.relative_to(ROOT)).replace("\\", "/")
             for item in ROOT.rglob("*") if item.is_file()
             and ".git/" not in str(item) and "node_modules" not in str(item)]

    path_violations, checked, shorthand = check_paths(markdown, files)
    version_violations = check_versions()
    timeout_violations = check_timeouts()
    navigation_violations = check_navigation()

    print(f"扫描 {len(markdown)} 份 Markdown，检查 {checked} 条路径引用"
          f"（其中 {shorthand} 条为唯一后缀简写，已解析通过）")
    sections = (("路径", path_violations), ("版本号", version_violations),
                ("常量", timeout_violations), ("导航清单", navigation_violations))
    total = 0
    for label, items in sections:
        total += len(items)
        print(f"\n## {label}：{len(items)} 条")
        for item in items[:args.limit]:
            print("  -", item)
        if len(items) > args.limit:
            print(f"  ...（其余 {len(items) - args.limit} 条略）")
    print(f"\n合计 {total} 条违规")
    return 1 if (args.strict and total) else 0


if __name__ == "__main__":
    raise SystemExit(main())
