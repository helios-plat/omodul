"""omodul.evolutionary_mating_transaction — 进化繁衍事务.

Pillars: fingerprint, decision_trail, report

签名: (config, input_data, output_dir, *, on_step=None)
失败绝对不 raise — 返回 status="failed".

定期触发的事务:
1. 拉取当前种群 → 计算适应度 → 末位淘汰
2. 基于高分变体进行遗传变异 → 生成子代
3. 将子代注入基因库等待下一轮调度
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, CostTracker, Trail, build_result, write_report


class Config(BaseConfig):
    """Evolutionary Mating Transaction 配置."""

    _omodul_name: ClassVar[str] = "evolutionary_mating_transaction"
    _omodul_version: ClassVar[str] = "1.1.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "report"}

    genome_store: Any = None           # obase.PopulationGenomeStore
    fitness_op: Any = None             # oprim._calculate_fitness_score
    mutate_sk: Any = None              # oskill._genetic_dag_mutate
    crossover_sk: Any = None           # oskill._genetic_dag_crossover

    # 演化参数
    elite_count: int = 2               # 保留精英数量
    offspring_count: int = 4           # 每代生成的子代数量
    extinction_threshold: int = 5      # 连续垫底次数淘汰阈值
    mutation_rate: float = 0.3


class InputData(BaseModel):
    """Evolutionary Mating Transaction 输入."""

    generation_label: str = "auto"

    model_config = {"arbitrary_types_allowed": True}


def evolutionary_mating_transaction(
    config: Config,
    input_data: InputData,
    output_dir: Path,
    *,
    on_step: Any = None,
) -> dict[str, Any]:
    """进化繁衍事务 — 定期触发种群演化.

    流程:
    1. 拉取当前种群 → 计算每个变体的适应度
    2. 末位淘汰：连续垫底超过阈值的变体被 deactivate
    3. 精英选择：Top N 作为父代
    4. 交叉+变异生成子代
    5. 子代注入基因库
    6. 推进世代

    Args:
        config: 配置
        input_data: 输入数据
        output_dir: 输出目录
        on_step: 步骤回调

    Returns:
        {"status": "completed"|"failed", ...}
    """
    cost = CostTracker()
    trail = Trail()
    decision_trail: list[dict[str, Any]] = []

    store = config.genome_store
    fitness_op = config.fitness_op
    mutate_sk = config.mutate_sk
    crossover_sk = config.crossover_sk

    try:
        # Phase 1: 适应度评估
        trail.record(event="fitness_evaluation")
        fitness_results: dict[str, dict[str, Any]] = {}

        if store is not None and fitness_op is not None:
            active = store.list_active()
            for genome in active:
                score_result = fitness_op(
                    genome.reward_history,
                    decay_lambda=0.1,
                    recency_bias=0.3,
                )
                fitness_results[genome.genome_id] = score_result
                # 更新最后评分
                genome.last_score = score_result["fitness"]
        else:
            # 无基因库 → 提前返回空结果
            return build_result(
                status="completed",
                error=None,
                cost_usd=0.0,
                summary="No genome store injected — skipping evolution.",
                pillars={
                    "fingerprint": {"generation": 0},
                    "decision_trail": decision_trail,
                    "report": {"skipped": True, "reason": "no genome store"},
                },
            )

        decision_trail.append({
            "step": "fitness_evaluated",
            "genomes_evaluated": len(fitness_results),
        })

        # Phase 2: 末位淘汰
        trail.record(event="extinction")
        eliminated: list[str] = []
        if store is not None and len(active) > config.elite_count:
            # 按评分排序
            ranked = sorted(active, key=lambda g: g.last_score)
            # 连续垫底检测
            for genome in ranked:
                bottom_streak = genome.metadata.get("bottom_streak", 0) + 1
                if genome.last_score <= 0.0:
                    genome.metadata["bottom_streak"] = bottom_streak
                else:
                    genome.metadata["bottom_streak"] = 0

                if genome.metadata.get("bottom_streak", 0) >= config.extinction_threshold:
                    store.deactivate(genome.genome_id)
                    eliminated.append(genome.genome_id)

        decision_trail.append({
            "step": "extinction",
            "eliminated": len(eliminated),
            "eliminated_ids": eliminated,
        })

        # Phase 3: 精英选择
        trail.record(event="elite_selection")
        elites: list[Any] = []
        if store is not None:
            elites = store.top_performers(n=max(2, config.elite_count))
            # 过滤掉已淘汰的
            elites = [g for g in elites if g.is_active]

        decision_trail.append({
            "step": "elite_selected",
            "elite_count": len(elites),
            "elite_ids": [g.genome_id for g in elites],
        })

        # Phase 4: 生成子代
        trail.record(event="offspring_generation")
        offspring_created = 0
        offspring_ids: list[str] = []

        if store is not None and mutate_sk is not None and len(elites) >= 2:
            import networkx as nx

            for _ in range(config.offspring_count):
                # 选择两个不同的父代
                import random
                if len(elites) >= 2:
                    p1, p2 = random.sample(elites, 2)
                else:
                    p1 = p2 = elites[0]

                # 获取父代 DAG
                dag1 = store.get_dag(p1.genome_id)
                dag2 = store.get_dag(p2.genome_id) if p2.genome_id != p1.genome_id else dag1

                if dag1 is None or dag2 is None:
                    continue

                # 交叉
                if crossover_sk is not None:
                    dag1_dict = nx.node_link_data(dag1)
                    dag2_dict = nx.node_link_data(dag2)
                    crossover_result = crossover_sk(dag1_dict, dag2_dict)
                    child_dict = crossover_result.get("child_dag", dag1_dict)
                else:
                    child_dict = nx.node_link_data(dag1)

                # 变异
                mutation_result = mutate_sk(
                    child_dict,
                    mutation_rate=config.mutation_rate,
                    random_seed=offspring_created,
                )
                mutated_dict = mutation_result.get("mutated_dag", child_dict)

                # 重建 DAG 并入库
                child_dag = nx.node_link_graph(mutated_dict)
                try:
                    child = store.add_genome(
                        child_dag,
                        parent_ids=[p1.genome_id, p2.genome_id],
                        metadata={
                            "generation": store.generation,
                            "mutations": mutation_result.get("mutations_applied", []),
                        },
                    )
                    offspring_ids.append(child.genome_id)
                    offspring_created += 1
                except Exception:
                    pass

        decision_trail.append({
            "step": "offspring_created",
            "count": offspring_created,
            "offspring_ids": offspring_ids,
        })

        # Phase 5: 推进世代
        if store is not None:
            store.advance_generation()

        # 生成报告
        snap = store.snapshot() if store is not None else None
        report_content = (
            f"# Evolutionary Mating Report\n\n"
            f"## Generation: {store.generation if store else 'N/A'}\n"
            f"## Population: {store.population_size if store else 0} "
            f"(active: {len(store.list_active()) if store else 0})\n\n"
            f"### Fitness Stats\n"
            f"- Mean: {snap.stats.get('mean_score', 0):.4f}\n"
            f"- Max: {snap.stats.get('max_score', 0):.4f}\n"
            f"- Min: {snap.stats.get('min_score', 0):.4f}\n\n"
            f"### Elite Genomes: {[g.genome_id[:8] for g in elites]}\n"
            f"### Eliminated: {eliminated}\n"
            f"### Offspring Created: {offspring_created}\n"
        )
        report_path = write_report(
            report_content, output_dir=output_dir,
            name=f"evolution_gen_{store.generation if store else 0}",
            fmt="markdown",
        )

        cost.add(in_tok=500, out_tok=300)
        trail.write(output_dir)

        return build_result(
            status="completed",
            error=None,
            cost_usd=cost.total_usd,
            report_path=str(report_path),
            trail=trail,
            trail_path=trail.write(output_dir),
            summary=f"Evolutionary mating: gen={store.generation if store else '?'}, "
                    f"offspring={offspring_created}, eliminated={len(eliminated)}",
            pillars={
                "fingerprint": {
                    "generation": store.generation if store else 0,
                    "population": store.population_size if store else 0,
                },
                "decision_trail": decision_trail,
                "report": {
                    "fitness_results": {
                        gid: fr["fitness"] for gid, fr in fitness_results.items()
                    },
                    "elites": [g.genome_id for g in elites],
                    "eliminated": eliminated,
                    "offspring_ids": offspring_ids,
                },
            },
            generation=store.generation if store else 0,
            offspring_count=offspring_created,
            eliminated=eliminated,
        )

    except Exception as e:
        return build_result(
            status="failed",
            error={"type": type(e).__name__, "message": str(e)},
            cost_usd=cost.total_usd,
            trail=trail,
            summary=f"Evolutionary mating failed: {e}",
            pillars={
                "decision_trail": decision_trail,
                "report": {"error": str(e)},
            },
        )
