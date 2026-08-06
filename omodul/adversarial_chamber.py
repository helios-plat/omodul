"""omodul.adversarial_chamber — 红蓝对抗审判庭.

Genesis 锻造完 3O 算子 / Coprocessor 跑完网格搜索后, 结果不能直接交付 —
系统内部立刻拉起一场"闭门审判":

    蓝队 (Blue Team)   策略编写者: 解释这段策略为什么能赚钱.
    红队 (Red Team)    冷酷质疑者: 注入严格负面 Prompt, 唯一使命是挑刺/找漏洞/攻击.
    主脑 (Judge)       听取双方辩论后给出安全系数, 并对代码做最后一轮逻辑修正.

前置证据: 静态不变量扫描 (oprim._lookahead_scan) 提供"数学级法律"的客观证据,
红蓝双方都必须正面回应, LLM 可以犯错, 静态校验不放过任何一行污染代码.

双模式:
    - LLM 模式   config.llm_fn 注入 → 真实辩论 (caller 契约: async fn(messages=..., tools=..., max_tokens=...))
    - 确定性模式 llm_fn=None       → 静态证据 + 规则化辩护/质疑/裁决 (离线安全, 测试可复现)

输出: 《红蓝对抗审计报告》 markdown + 结构化 verdict (blocked/needs_review/approved)
      + safety_score_before → safety_score_after (如 60 → 95).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Callable

from pydantic import BaseModel

from omodul._base import BaseConfig, CostTracker, Trail, build_result, compute_fingerprint, extract_text, write_report
from oprim._lookahead_scan import scan_lookahead

# 确定性模式下的通用红队探测 (静态证据之外的"开放性质疑")
_GENERIC_RED_PROBES = [
    ("overfitting", "过拟合: 策略是否只对历史参数敏感? 是否做过样本外/滚动前推验证?"),
    ("survivorship", "幸存者偏差: 标的池是否包含已退市标的? 选择过程是否引入前视?"),
    ("costs", "交易成本: 换手率对应的手续费/滑点是否已被建模? 高频信号在 T+1 市场是否不可执行?"),
    ("regime", "市场状态: 策略是否只在单一牛/熊形态下有效? 极端行情(涨跌停/熔断)下是否暴露?"),
]


class AdversarialChamberConfig(BaseConfig):
    """红蓝对抗审判庭配置."""

    _omodul_name: ClassVar[str] = "adversarial_chamber"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"strategy_code"}
    _enabled_pillars: ClassVar[set[str]] = {"report", "cost", "decision_trail", "fingerprint"}

    llm_fn: Callable | None = None          # None → 确定性离线模式 (静态证据 + 规则辩论)
    red_team_rounds: int = 1                # 红队进攻轮数 (≥1)
    safety_threshold: float = 70.0          # 低于此分 → needs_review
    safety_floor: float = 0.0
    # 确定性评分权重
    violation_penalty: float = 30.0         # 每个硬违规扣分
    warning_penalty: float = 10.0           # 每个泄漏/风险扣分
    hardening_bonus: float = 15.0           # 蓝队加固加分 (无硬违规时)


class AdversarialChamberInput(BaseModel):
    """审判庭输入: 待审策略代码."""

    strategy_code: str
    strategy_name: str = "unnamed_strategy"
    caller: Any = None                      # 兼容 omodul 调用约定 (可空)
    context: str = ""                       # 策略背景 (市场/标的/周期)


# ---------------------------------------------------------------------------
# Prompt 构造 (机制, 与业务解耦)
# ---------------------------------------------------------------------------

def _build_blue_prompt(code: str, context: str, static: dict) -> str:
    return f"""你是蓝队: 量化策略的作者辩护人. 你的唯一职责是解释下面这段策略为什么能赚钱,
并针对红队可能发起的质疑提前加固.

策略名称: {context or '<unnamed>'}
静态不变量扫描结论 (数学级法律, 不可辩驳): verdict={static['verdict']},
violations={len(static['violations'])}, warnings={len(static['warnings'])}.

策略代码:
```python
{code}
```

请输出:
1. 核心盈利逻辑 (≤3 条, 每条 ≤50 字)
2. 针对静态发现的合规说明 (如有)
3. 你主动做的加固点"""


def _build_red_prompt(code: str, context: str, static: dict, blue_defense: str) -> str:
    probes = "\n".join(f"- {name}: {desc}" for name, desc in _GENERIC_RED_PROBES)
    static_evidence = "\n".join(
        f"- [静态证据 L{f['line']} {f['rule_id']}] {f['message']}"
        for f in (static["violations"] + static["warnings"])
    ) or "- 静态扫描未发现硬性违规 (仍需人工挑刺)"
    return f"""你是红队: 冷酷的量化策略质疑者. 你被注入了极其严格的负面 Prompt —
你的唯一使命就是挑刺、找漏洞、攻击下面这段代码. 不许夸奖, 不许留情.

策略代码:
```python
{code}
```

静态不变量扫描证据 (必须逐条正面回应, 不得回避):
{static_evidence}

蓝队辩护词:
{blue_defense or '(无)'}

开放性质疑清单 (逐条给出 存在/不存在 + 一句话理由):
{probes}

请输出 JSON:
{{"points": [{{"id": "R1", "severity": "high|medium|low", "title": "...", "detail": "...", "line": <int|None>}}],
 "summary": "整体攻击结论 (≤80字)"}}"""


def _build_judge_prompt(code: str, context: str, blue: str, red: str) -> str:
    return f"""你是主脑法官: 听取红蓝双方辩论后, 对策略代码做最后一轮逻辑修正并给出安全系数.

策略背景: {context or '<unnamed>'}

蓝队辩护:
{blue}

红队质疑:
{red}

请输出 JSON:
{{"verdict": "approved|needs_review|blocked",
 "safety_score": <0-100 整数>,
 "fixes": [{{"target": "代码位置/规则", "action": "修正动作"}}],
 "final_code": "<完整修正后的策略代码, 原样输出>"}}"""


# ---------------------------------------------------------------------------
# 确定性辩论 (离线模式)
# ---------------------------------------------------------------------------

def _deterministic_blue(code: str, static: dict) -> str:
    lines: list[str] = []
    if not static["violations"] and not static["warnings"]:
        lines.append("静态不变量扫描全绿 (pass): 未发现未来函数/数据泄漏/除零风险 — 这是本策略合规性的客观基石.")
    elif not static["violations"]:
        lines.append(f"静态扫描 verdict=review: 存在 {len(static['warnings'])} 处泄漏/风险告警, 蓝队承诺逐条加固.")
    else:
        lines.append(f"静态扫描 verdict=block: 存在 {len(static['violations'])} 处硬违规, 蓝队承认并给出修正方案.")
    low = code.lower()
    if any(k in low for k in ("stop_loss", "止损", "max_drawdown", "atr", "volatility")):
        lines.append("策略包含风险控制逻辑 (止损/波动率约束), 尾部风险有显式管理.")
    if any(k in low for k in ("volume", "vol", "成交")):
        lines.append("策略使用成交量过滤, 对流动性不足的标的/时段有天然回避.")
    if "shift(1)" in low or "shift( 1" in low:
        lines.append("滚动统计量已做 shift(1) 滞后, 决策向量不包含当前 bar 收盘后的信息.")
    return "\n".join(lines) or "蓝队: 核心盈利逻辑见代码注释 (静态扫描通过)."


def _deterministic_red(code: str, static: dict) -> str:
    points: list[str] = []
    for f in static["violations"] + static["warnings"]:
        points.append(
            f"- [静态证据 L{f['line']} {f['rule_id']} {f['severity']}] {f['message']}"
        )
    # 静态未覆盖的开放性探测 → "未发现, 但建议人工复核"
    for name, desc in _GENERIC_RED_PROBES:
        points.append(f"- [开放性质疑 {name}] {desc} → 静态无法判定, 建议样本外验证")
    return "\n".join(points)


def _deterministic_judge(code: str, static: dict, cfg: AdversarialChamberConfig) -> dict:
    n_v = len(static["violations"])
    n_w = len(static["warnings"])
    before = max(
        cfg.safety_floor,
        100.0 - cfg.violation_penalty * n_v - cfg.warning_penalty * n_w,
    )
    if n_v == 0:
        after = min(100.0, before + cfg.hardening_bonus)
        verdict = "approved" if after >= cfg.safety_threshold else "needs_review"
    else:
        after = before  # 硬违规未修复不加分
        verdict = "blocked"

    fixes: list[dict] = []
    for f in static["violations"]:
        fixes.append({"target": f"L{f['line']} ({f['rule_id']})", "action": f"必须修复: {f['message']}"})
    for f in static["warnings"]:
        fixes.append({"target": f"L{f['line']} ({f['rule_id']})", "action": f"建议加固: {f['message']}"})

    # 确定性修正: 在代码头部注入合规头 (硬违规仍需要人类/LLM 实际改写)
    header = (
        "# ===== 红蓝对抗审判庭修正头 =====\n"
        "# 以下位置存在静态不变量违规, 必须修复后才能进入回测/实盘:\n"
        + "\n".join(f"#   L{f['line']} {f['rule_id']}: {f['message']}" for f in static["violations"])
        + "\n# =================================\n"
    ) if static["violations"] else ""
    final_code = header + code

    return {
        "verdict": verdict,
        "safety_score_before": round(before, 1),
        "safety_score_after": round(after, 1),
        "fixes": fixes,
        "final_code": final_code,
        "rationale": (
            f"{len(static['violations'])} 处硬违规 / {len(static['warnings'])} 处告警; "
            f"安全系数 {before:.0f} → {after:.0f} (阈值 {cfg.safety_threshold:.0f})."
        ),
    }


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

async def adversarial_chamber(
    config: AdversarialChamberConfig,
    input_data: AdversarialChamberInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """对策略代码执行红蓝对抗审判, 输出审计报告.

    支柱: report + cost + decision_trail + fingerprint
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    trail = Trail()
    cost = CostTracker()
    code = input_data.strategy_code
    name = input_data.strategy_name

    if on_step:
        on_step({"type": "adversarial_chamber", "stage": "static_scan", "strategy": name})

    # 1. 指纹 + 静态证据 (数学级法律)
    fingerprint = compute_fingerprint({"strategy_code": code, "strategy_name": name})
    static = scan_lookahead(code, filename=f"{name}.py")
    trail.record(event="static_scan", verdict=static["verdict"], findings=len(static["findings"]))

    llm = config.llm_fn

    async def _ask(prompt: str, event: str) -> str:
        nonlocal cost
        trail.record(event=event, n_chars=len(prompt))
        resp = await llm(messages=[{"role": "user", "content": prompt}], max_tokens=4096)
        cost.add_from_response(resp, model=config.llm_model)
        return extract_text(resp)

    # 2. 蓝队辩护
    if llm is not None:
        blue_defense = await _ask(
            _build_blue_prompt(code, input_data.context, static), event="blue_team"
        )
    else:
        blue_defense = _deterministic_blue(code, static)
    trail.record(event="blue_team", chars=len(blue_defense))

    # 3. 红队质疑 (rounds 轮)
    red_attack = ""
    for rnd in range(max(1, config.red_team_rounds)):
        if llm is not None:
            red_attack = await _ask(
                _build_red_prompt(code, input_data.context, static, blue_defense),
                event=f"red_team_r{rnd + 1}",
            )
        else:
            red_attack = _deterministic_red(code, static)
        trail.record(event=f"red_team_r{rnd + 1}", chars=len(red_attack))

    # 4. 主脑裁决
    det_judge = _deterministic_judge(code, static, config)
    if llm is not None:
        judge_raw = await _ask(
            _build_judge_prompt(code, input_data.context, blue_defense, red_attack),
            event="judge",
        )
        parsed = _parse_judge_json(judge_raw)
        if parsed:
            # 归一化: 静态基分作 before, LLM 主脑分作 after
            judge = {
                "verdict": str(parsed.get("verdict", "needs_review")),
                "safety_score_before": det_judge["safety_score_before"],
                "safety_score_after": float(parsed.get("safety_score", det_judge["safety_score_after"])),
                "fixes": parsed.get("fixes", det_judge["fixes"]),
                "final_code": parsed.get("final_code") or code,
                "rationale": f"{det_judge['rationale']} | LLM 主脑裁决: {parsed.get('verdict')}",
            }
        else:
            judge = det_judge
    else:
        judge = det_judge
    trail.record(event="judge", verdict=judge.get("verdict"), score=judge.get("safety_score_after"))

    # 5. 审计报告 (markdown)
    report = _render_report(
        name=name,
        context=input_data.context,
        static=static,
        blue=blue_defense,
        red=red_attack,
        judge=judge,
        fingerprint=fingerprint,
    )
    report_path = write_report(report, output_dir=output_dir, name=f"adversarial_report_{name}")
    trail.write(output_dir, suffix=f"_{name}")

    status = judge.get("verdict", "needs_review")
    return build_result(
        status=status,
        fingerprint=fingerprint,
        trail=trail,
        trail_path=output_dir / f"decision_trail_{trail.run_id}_{name}.json",
        report_path=report_path,
        cost_usd=cost.total_usd,
        strategy_name=name,
        safety_score_before=judge.get("safety_score_before"),
        safety_score_after=judge.get("safety_score_after"),
        verdict=status,
        violations=len(static["violations"]),
        warnings=len(static["warnings"]),
        red_points=len(static["violations"]) + len(static["warnings"]),
        judge_fixes=judge.get("fixes", []),
        final_code=judge.get("final_code", code),
        rationale=judge.get("rationale", ""),
    )


def _parse_judge_json(raw: str) -> dict | None:
    """从 LLM 输出中提取 JSON (容忍 ```json 围栏)."""
    import json
    import re

    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "verdict" not in data:
        return None
    return data


def _render_report(
    *,
    name: str,
    context: str,
    static: dict,
    blue: str,
    red: str,
    judge: dict,
    fingerprint: str,
) -> str:
    """渲染《红蓝对抗审计报告》."""
    lines = [
        f"# 红蓝对抗审计报告 — {name}",
        "",
        f"- 指纹: `{fingerprint}`",
        f"- 背景: {context or '(未提供)'}",
        f"- 静态不变量 verdict: **{static['verdict']}** (硬违规 {len(static['violations'])} / 告警 {len(static['warnings'])})",
        "",
        "## 一、静态不变量证据 (数学级法律)",
        "",
    ]
    if static["findings"]:
        for f in static["findings"]:
            lines.append(f"- `L{f['line']}` [{f['rule_id']} {f['severity']}] {f['message']}")
    else:
        lines.append("- 未发现违规.")
    lines += ["", "## 二、蓝队辩护", "", blue, "", "## 三、红队质疑", "", red, "", "## 四、主脑裁决", ""]
    lines += [
        f"- 裁决: **{judge.get('verdict')}**",
        f"- 安全系数: **{judge.get('safety_score_before')} → {judge.get('safety_score_after')}**",
        f"- 裁决理由: {judge.get('rationale', '')}",
        "",
        "### 修正项",
        "",
    ]
    fixes = judge.get("fixes", [])
    if fixes:
        for fx in fixes:
            lines.append(f"- **{fx.get('target')}**: {fx.get('action')}")
    else:
        lines.append("- 无需修正.")
    lines += ["", "> 本报告由 omodul.adversarial_chamber 生成. 硬违规 (blocked) 不得进入回测/实盘."]
    return "\n".join(lines)


__all__ = ["AdversarialChamberConfig", "AdversarialChamberInput", "adversarial_chamber"]
