"""omodul.cognitive_diagnosis —— 通用结构化诊断的确定性内核（接 ADR-A23）。

3O 层级：omodul（可组合诊断）。标准入口 run_diagnosis 遵循 3O §5.2 契约
（config, input_data, output_dir → 标准 dict），启用 decision_trail 支柱
（诊断可审计——AII 可置信性的体现）；fingerprint/report/cost 暂未启用。
diagnose() 是纯计算核心，run_diagnosis 是符合 3O 的 omodul 包装。

首个实例：学生数学画像。从作答矩阵 + Q-matrix，用确定性统计模型推 latent 能力画像。
P1-b 实现两个核心内核：DINA（知识点掌握 + slip/guess）+ 能力值估计。

★ 死守 ADR-A23 两红线：
  1. 只 descriptive + diagnostic，不 predictive/prescriptive（不预测会考好、不开补课处方）
  2. 不输出主观/心理标签——给可核查统计量（高 slip 率），"粗心/焦虑"留人解读

可置信性的根：全部确定性统计（可重现、可独立检验），非 LLM 臆测。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import ClassVar

import numpy as np

from omodul._base_config import BaseConfig

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class CognitiveDiagnosisConfig(BaseConfig):
    """cognitive_diagnosis 配置."""

    _omodul_name: ClassVar[str] = "cognitive_diagnosis"
    _omodul_version: ClassVar[str] = "1.0.0"

    max_em_iters: int = 30


# 该 omodul 启用的四支柱子集（3O §5.3，显式声明）
_enabled_pillars: set[str] = {"decision_trail"}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class DiagnosisReport:
    """诊断报告：全部可核查统计量，无心理/主观标签（守 A23）。"""

    mastery: dict = field(default_factory=dict)  # {student_id: {skill_id: 0/1}} 知识点掌握
    ability: dict = field(default_factory=dict)  # {student_id: float} 能力值（连续）
    item_params: dict = field(default_factory=dict)  # {item_id: {slip, guess}}
    error_patterns: dict = field(
        default_factory=dict
    )  # {student_id: {execution_errors, knowledge_gap_errors}}
    skill_summary: dict = field(default_factory=dict)  # {skill_id: 掌握该知识点的学生比例}

    def summary(self) -> dict:
        return {
            "n_students": len(self.mastery),
            "n_skills": len(self.skill_summary),
            "n_items": len(self.item_params),
        }


# ---------------------------------------------------------------------------
# Pure computation — DINA + EM + ability estimation
# ---------------------------------------------------------------------------


def _ideal_response(alpha: np.ndarray, q_row: np.ndarray) -> int:
    """DINA 理想响应 η：学生掌握了该题所需全部知识点 → 1，否则 0（非补偿性 AND 门）。"""
    required = q_row == 1
    return int(np.all(alpha[required] == 1)) if required.any() else 1


def _all_patterns(n_skills: int) -> np.ndarray:
    """枚举全部 2^K 个掌握模式。冷启动 K 小（数学知识点分块），可枚举。"""
    from itertools import product

    return np.array(list(product([0, 1], repeat=n_skills)))


def estimate_item_params(
    R: np.ndarray, Q: np.ndarray, patterns: np.ndarray, alpha_idx: np.ndarray
) -> tuple:
    """给定当前每个学生的掌握模式，估计每题的 slip/guess（确定性边际估计）。

    slip_j = P(答错 | 掌握所需知识点) —— 掌握却错（"执行型错误"的客观刻画）
    guess_j = P(答对 | 未掌握所需知识点) —— 没掌握却对
    """
    n_items = R.shape[1]
    slip = np.zeros(n_items)
    guess = np.zeros(n_items)
    for j in range(n_items):
        eta = np.array([_ideal_response(patterns[alpha_idx[i]], Q[j]) for i in range(R.shape[0])])
        masters = eta == 1
        non_masters = eta == 0
        slip[j] = np.mean(R[masters, j] == 0) if masters.any() else 0.0
        guess[j] = np.mean(R[non_masters, j] == 1) if non_masters.any() else 0.0
    return slip, guess


def _response_prob(eta: int, slip: float, guess: float) -> float:
    """DINA 响应概率：P(对|α,j) = (1-s)^η · g^(1-η)。"""
    return ((1 - slip) ** eta) * (guess ** (1 - eta))


def fit_dina(R: np.ndarray, Q: np.ndarray, max_iter: int = 30, tol: float = 1e-4) -> tuple:
    """DINA 拟合（EM）：交替估计 item 参数(slip/guess) 与学生掌握模式。

    返回 (alpha_idx, patterns, slip, guess)：
      alpha_idx[i] = 学生 i 最可能的掌握模式在 patterns 中的下标。

    确定性：相同 (R,Q) → 相同结果（无随机初始化，用边际正确率初始化）。
    """
    n_students, n_items = R.shape
    n_skills = Q.shape[1]
    patterns = _all_patterns(n_skills)

    scores = R.sum(axis=1)
    order = np.argsort(scores)
    alpha_idx = np.zeros(n_students, dtype=int)
    n_pat = len(patterns)
    pat_mastery_count = patterns.sum(axis=1)
    pat_order = np.argsort(pat_mastery_count)
    for rank, i in enumerate(order):
        alpha_idx[i] = pat_order[min(rank * n_pat // max(n_students, 1), n_pat - 1)]

    prev_slip = None
    for _ in range(max_iter):
        slip, guess = estimate_item_params(R, Q, patterns, alpha_idx)
        slip = np.clip(slip, 0.05, 0.5)
        guess = np.clip(guess, 0.05, 0.5)
        new_idx = np.zeros(n_students, dtype=int)
        for i in range(n_students):
            best_ll, best_p = -np.inf, 0
            for p in range(n_pat):
                ll = 0.0
                for j in range(n_items):
                    eta = _ideal_response(patterns[p], Q[j])
                    prob = _response_prob(eta, slip[j], guess[j])
                    ll += np.log(prob if R[i, j] == 1 else (1 - prob))
                if ll > best_ll:
                    best_ll, best_p = ll, p
            new_idx[i] = best_p
        alpha_idx = new_idx
        if prev_slip is not None and np.max(np.abs(slip - prev_slip)) < tol:
            break
        prev_slip = slip
    return alpha_idx, patterns, slip, guess


def estimate_ability(R: np.ndarray) -> np.ndarray:
    """能力值（简化）：标准化总分作为连续能力代理（IRT θ 的零阶近似）。"""
    scores = R.sum(axis=1).astype(float)
    mean, std = scores.mean(), scores.std()
    return (scores - mean) / std if std > 0 else np.zeros_like(scores)


def diagnose(
    R: np.ndarray,
    Q: np.ndarray,
    *,
    student_ids=None,
    skill_ids=None,
    item_ids=None,
    slip_threshold: float = 0.2,
) -> DiagnosisReport:
    """通用结构化诊断（首个实例：学生数学画像）。

    输入：
        R: 作答矩阵 (n_students × n_items)，0/1
        Q: Q-matrix (n_items × n_skills)，0/1，题考查哪些知识点
    输出：DiagnosisReport，全部可核查统计量（守 A23：无心理标签）。
    """
    n_students, n_items = R.shape
    n_skills = Q.shape[1]
    student_ids = student_ids or [f"S{i}" for i in range(n_students)]
    skill_ids = skill_ids or [f"K{k}" for k in range(n_skills)]
    item_ids = item_ids or [f"Q{j}" for j in range(n_items)]

    alpha_idx, patterns, slip, guess = fit_dina(R, Q)
    ability = estimate_ability(R)

    report = DiagnosisReport()
    for i, sid in enumerate(student_ids):
        alpha = patterns[alpha_idx[i]]
        report.mastery[sid] = {skill_ids[k]: int(alpha[k]) for k in range(n_skills)}
        report.ability[sid] = float(ability[i])

    for j, iid in enumerate(item_ids):
        report.item_params[iid] = {"slip": float(slip[j]), "guess": float(guess[j])}

    for i, sid in enumerate(student_ids):
        alpha = patterns[alpha_idx[i]]
        exec_err, gap_err = 0, 0
        for j in range(n_items):
            if R[i, j] == 0:
                eta = _ideal_response(alpha, Q[j])
                if eta == 1:
                    exec_err += 1
                else:
                    gap_err += 1
        report.error_patterns[sid] = {
            "execution_errors": exec_err,
            "knowledge_gap_errors": gap_err,
        }

    for k, kid in enumerate(skill_ids):
        mastered = sum(report.mastery[sid][kid] for sid in student_ids)
        report.skill_summary[kid] = mastered / n_students if n_students else 0.0

    return report


# ---------------------------------------------------------------------------
# 标准 omodul 包装（3O §5.2 契约）
# ---------------------------------------------------------------------------


def run_diagnosis(
    config: CognitiveDiagnosisConfig,
    input_data: dict,
    output_dir: str | None = None,
    *,
    on_step: Callable | None = None,
) -> dict:
    """标准 omodul 签名（3O §5.2）。通用结构化诊断入口。

    config:     CognitiveDiagnosisConfig
    input_data: {"R": np.ndarray, "Q": np.ndarray,
                 "student_ids": [...]?, "skill_ids": [...]?, "item_ids": [...]?}
    output_dir: decision_trail.json 落盘目录（None 则不落盘，仍返回 trail）
    on_step:    每步回调（可选）

    返回（omodul 标准 dict）：
        findings:    DiagnosisReport（失败 None）
        status:      "completed" | "failed"
        error:       失败原因（成功 None）
        decision_trail: 诊断轨迹
        report_path / cost_usd: 未启用

    失败不 raise（3O §5.12）。守 A23：findings 只含可核查统计量。
    """
    if isinstance(config, dict):
        config = CognitiveDiagnosisConfig(**config) if config else CognitiveDiagnosisConfig()
    trail: list[dict] = []
    findings = None
    status = "failed"
    error = None

    def _emit(step_dict: dict) -> None:
        trail.append(step_dict)
        if on_step is not None:
            on_step(step_dict)

    try:
        R = np.asarray(input_data["R"], dtype=int)
        Q = np.asarray(input_data["Q"], dtype=int)
        n_students, n_items = R.shape
        n_skills = Q.shape[1]

        _emit({"step": "input", "n_students": n_students, "n_items": n_items, "n_skills": n_skills})

        warnings = []
        if n_students < 30:
            warnings.append(
                "low_sample: DINA 题参数估计需足够样本(建议≥30)，小样本下掌握状态与slip可能混淆"
            )
        if n_skills > 12:
            warnings.append("high_skill_count: 2^K 模式枚举开销大(K>12)")
        if warnings:
            _emit({"step": "identifiability_check", "warnings": warnings})

        _emit(
            {
                "step": "fit_dina",
                "model": "DINA-EM",
                "constraints": "slip,guess∈[0.05,0.5] 单调性正则",
            }
        )
        report = diagnose(
            R,
            Q,
            student_ids=input_data.get("student_ids"),
            skill_ids=input_data.get("skill_ids"),
            item_ids=input_data.get("item_ids"),
            slip_threshold=config.max_em_iters and 0.2,  # use default threshold
        )
        _emit(
            {
                "step": "diagnose_done",
                "skill_mastery_rate": report.skill_summary,
                "high_slip_items": [
                    iid for iid, p in report.item_params.items() if p["slip"] > 0.25
                ],
            }
        )
        findings = report
        status = "completed"

    except Exception as e:
        error = {"code": "ERR_DIAGNOSIS", "message": str(e)}
        _emit({"step": "abort", "error": error})

    decision_trail = {
        "omodul": "cognitive_diagnosis",
        "enabled_pillars": sorted(_enabled_pillars),
        "model": "DINA + ability",
        "status": status,
        "trail": trail,
        # 守 A23：轨迹只记客观统计量与方法，不记任何心理/主观判断
        "red_lines": ["no_psychological_labels", "descriptive_diagnostic_only"],
    }

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(output_dir, "decision_trail.json"), "w", encoding="utf-8") as f:
            json.dump(decision_trail, f, ensure_ascii=False, indent=2, default=str)

    return {
        "findings": findings,
        "status": status,
        "error": error,
        "decision_trail": decision_trail,
        "report_path": None,
        "cost_usd": 0.0,
    }
