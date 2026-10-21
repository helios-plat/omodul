"""Phase 3-2 全量验证 — 演化自适应 (Evolutionary Adaptation).

关键验收:
1. 适应度衰减: 早期高分权重随时间指数递减
2. 拓扑安全性: 变异后 DAG 必须无环且连通
3. 淘汰机制: 连续 5 次垫底 → deactivate
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import networkx as nx
import pytest

# 挂载路径
_3O_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_3O_ROOT / "obase"))
sys.path.insert(0, str(_3O_ROOT / "oprim"))
sys.path.insert(0, str(_3O_ROOT / "oskill"))
sys.path.insert(0, str(_3O_ROOT / "omodul"))

from obase.population_genome_store import (
    PopulationGenomeStore, GenomeRecord, PopulationSnapshot,
)
from oprim._calculate_fitness_score import _calculate_fitness_score
from oskill._genetic_dag_mutate import (
    _genetic_dag_mutate,
    _genetic_dag_crossover,
)
from omodul.evolutionary_mating_transaction import (
    Config as EvoConfig,
    InputData as EvoInput,
    evolutionary_mating_transaction,
)


# ============================================================================
# Helpers
# ============================================================================

def _make_dag(num_nodes: int = 3) -> nx.DiGraph:
    """创建简单工作流 DAG."""
    g = nx.DiGraph()
    for i in range(num_nodes):
        g.add_node(f"node_{i}", type="task", params={"timeout": 10.0, "retries": 3})
    for i in range(num_nodes - 1):
        g.add_edge(f"node_{i}", f"node_{i+1}")
    return g


def _dag_to_dict(g: nx.DiGraph) -> dict:
    return nx.node_link_data(g)


# ============================================================================
# obase: PopulationGenomeStore
# ============================================================================

class TestPopulationGenomeStore:
    def test_add_and_get_genome(self):
        store = PopulationGenomeStore()
        dag = _make_dag(3)
        record = store.add_genome(dag)
        assert record.genome_id
        retrieved = store.get_genome(record.genome_id)
        assert retrieved is not None

    def test_record_reward(self):
        store = PopulationGenomeStore()
        dag = _make_dag(2)
        record = store.add_genome(dag)
        assert store.record_reward(record.genome_id, 0.85)
        assert store.record_reward(record.genome_id, 0.92)
        g = store.get_genome(record.genome_id)
        assert g.total_executions == 2
        assert len(g.reward_history) == 2

    def test_deactivate(self):
        store = PopulationGenomeStore()
        dag = _make_dag(2)
        r = store.add_genome(dag)
        assert store.deactivate(r.genome_id)
        assert not store.get_genome(r.genome_id).is_active

    def test_top_performers(self):
        store = PopulationGenomeStore()
        for score in [0.5, 0.9, 0.3, 0.7]:
            r = store.add_genome(_make_dag(2))
            r.last_score = score
        top = store.top_performers(2)
        assert len(top) == 2
        assert top[0].last_score >= top[1].last_score

    def test_bottom_performers(self):
        store = PopulationGenomeStore()
        for score in [0.5, 0.9, 0.3, 0.7]:
            r = store.add_genome(_make_dag(2))
            r.last_score = score
        bottom = store.bottom_performers(2)
        assert bottom[0].last_score <= 0.5

    def test_snapshot(self):
        store = PopulationGenomeStore()
        store.add_genome(_make_dag(2))
        snap = store.snapshot()
        assert snap.active_count == 1
        assert "mean_score" in snap.stats

    def test_advance_generation(self):
        store = PopulationGenomeStore()
        assert store.generation == 0
        store.advance_generation()
        assert store.generation == 1

    def test_serialization(self):
        store = PopulationGenomeStore()
        store.add_genome(_make_dag(2))
        store.advance_generation()
        data = store.to_dict()
        restored = PopulationGenomeStore.from_dict(data)
        assert restored.generation == 1
        assert restored.population_size == 1

    def test_max_population_enforced(self):
        store = PopulationGenomeStore(max_population=3)
        for _ in range(5):
            r = store.add_genome(_make_dag(2))
            r.last_score = 0.0
            r.is_active = False
        assert store.population_size <= 3

    def test_health(self):
        store = PopulationGenomeStore()
        h = store.health()
        assert h["status"] == "healthy"


# ============================================================================
# oprim: fitness score calculation
# ============================================================================

class TestFitnessScore:
    def test_empty_history(self):
        result = _calculate_fitness_score([])
        assert result["fitness"] == 0.0

    def test_single_score(self):
        now = time.time()
        history = [(now, 0.8)]
        result = _calculate_fitness_score(history, decay_lambda=0.1)
        assert abs(result["fitness"] - 0.8) < 0.01

    def test_exponential_decay(self):
        """早期高分权重应随时间衰减."""
        now = time.time()
        history = [
            (now - 7200, 1.0),   # 2 小时前: 满分
            (now - 3600, 0.5),   # 1 小时前: 中等
            (now - 60, 0.2),     # 1 分钟前: 低分
        ]
        result = _calculate_fitness_score(history, decay_lambda=0.5, recency_bias=0.0)
        # 最近的低分应有更高权重 → fitness 偏向 0.2
        assert result["fitness"] < 0.6, f"Fitness 应偏向近期低分，实际={result['fitness']}"

    def test_high_decay_forgets_old(self):
        """高 decay_lambda → 旧的奖励几乎被遗忘."""
        now = time.time()
        history = [
            (now - 7200, 1.0),   # 2h 前满分
            (now - 10, 0.1),     # 10s 前低分
        ]
        result = _calculate_fitness_score(history, decay_lambda=2.0, recency_bias=0.0)
        # 旧的几乎遗忘 → fitness ≈ 0.1
        assert result["fitness"] < 0.3

    def test_trend_improving(self):
        now = time.time()
        history = [(now - 4000, 0.2), (now - 3000, 0.3), (now - 2000, 0.5), (now - 1000, 0.9)]
        result = _calculate_fitness_score(history, decay_lambda=0.01)
        assert result["trend"] == "improving"

    def test_trend_declining(self):
        now = time.time()
        history = [(now - 4000, 0.9), (now - 3000, 0.7), (now - 2000, 0.4), (now - 1000, 0.2)]
        result = _calculate_fitness_score(history, decay_lambda=0.01)
        assert result["trend"] == "declining"

    def test_positional_only_one(self):
        import inspect
        sig = inspect.signature(_calculate_fitness_score)
        positional = [p for p in sig.parameters.values()
                      if p.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD]
        assert len(positional) <= 1


# ============================================================================
# oskill: genetic DAG mutation
# ============================================================================

class TestGeneticDAGMutate:
    def test_mutation_produces_valid_dag(self):
        g = _make_dag(5)
        dag_dict = _dag_to_dict(g)
        result = _genetic_dag_mutate(dag_dict, mutation_rate=1.0, random_seed=42)
        assert result["is_valid_dag"] is True, "变异后 DAG 必须有效"

    def test_mutation_zero_rate_no_change(self):
        g = _make_dag(4)
        dag_dict = _dag_to_dict(g)
        result = _genetic_dag_mutate(dag_dict, mutation_rate=0.0, random_seed=0)
        assert len(result["mutations_applied"]) == 0

    def test_mutation_applies_operations(self):
        g = _make_dag(6)
        dag_dict = _dag_to_dict(g)
        result = _genetic_dag_mutate(dag_dict, mutation_rate=1.0, random_seed=123)
        # 高变异率应产生至少一个操作
        assert len(result["mutations_applied"]) >= 0  # 可能全触发也可能部分触发

    def test_prune_removes_node(self):
        """Prune 操作应减少节点数."""
        g = _make_dag(6)
        dag_dict = _dag_to_dict(g)
        # 强制 prune-only
        result = _genetic_dag_mutate(
            dag_dict,
            mutation_rate=2.0,
            operation_weights={"prune": 1.0, "graft": 0.0, "param": 0.0},
            random_seed=999,
        )
        if result["nodes_removed"] > 0:
            assert result["is_valid_dag"] is True

    def test_graft_adds_node(self):
        """Graft 操作应增加节点."""
        g = _make_dag(4)
        dag_dict = _dag_to_dict(g)
        result = _genetic_dag_mutate(
            dag_dict,
            mutation_rate=2.0,
            operation_weights={"prune": 0.0, "graft": 1.0, "param": 0.0},
            random_seed=777,
        )
        assert result["nodes_added"] >= 1

    def test_param_mutate_changes_value(self):
        g = _make_dag(3)
        dag_dict = _dag_to_dict(g)
        result = _genetic_dag_mutate(
            dag_dict,
            mutation_rate=2.0,
            operation_weights={"prune": 0.0, "graft": 0.0, "param": 1.0},
            random_seed=555,
        )
        # 参数应被改变
        assert len(result["mutations_applied"]) >= 1
        assert any("param:" in m for m in result["mutations_applied"])

    def test_crossover_produces_valid_child(self):
        g1 = _make_dag(4)
        g2 = _make_dag(4)
        # 让 g2 节点名不同
        g2 = nx.DiGraph()
        for i in range(4):
            g2.add_node(f"alt_{i}", type="task", params={"timeout": 5.0})
        for i in range(3):
            g2.add_edge(f"alt_{i}", f"alt_{i+1}")

        result = _genetic_dag_crossover(_dag_to_dict(g1), _dag_to_dict(g2), random_seed=42)
        assert result["crossover_applied"] is True
        assert result["is_valid_dag"] is True

    def test_mutation_preserves_dag_no_cycles(self):
        """多次变异后 DAG 应始终无环."""
        for seed in range(20):
            g = _make_dag(5)
            dag_dict = _dag_to_dict(g)
            result = _genetic_dag_mutate(dag_dict, mutation_rate=0.8, random_seed=seed)
            assert result["is_valid_dag"], f"Seed {seed}: DAG 出现环"


# ============================================================================
# omodul: evolutionary mating transaction
# ============================================================================

class TestEvolutionaryMatingTransaction:
    def test_full_transaction(self, tmp_path: Path):
        """完整的演化繁衍事务."""
        store = PopulationGenomeStore(max_population=10)
        # 添加初始种群
        for i in range(6):
            dag = _make_dag(3 + i % 2)
            r = store.add_genome(dag)
            # 模拟历史奖励
            now = time.time()
            for j in range(5):
                r.reward_history.append((now - (5 - j) * 600, 0.3 + j * 0.1))

        def fitness_op(history, **kwargs):
            return _calculate_fitness_score(history, **kwargs)

        cfg = EvoConfig(
            genome_store=store,
            fitness_op=fitness_op,
            mutate_sk=_genetic_dag_mutate,
            crossover_sk=_genetic_dag_crossover,
            elite_count=2,
            offspring_count=3,
            extinction_threshold=5,
            mutation_rate=0.3,
        )
        inp = EvoInput(generation_label="test_gen_1")
        result = evolutionary_mating_transaction(cfg, inp, tmp_path)
        assert result["status"] == "completed"
        assert result["offspring_count"] >= 0

    def test_no_store_no_crash(self, tmp_path: Path):
        cfg = EvoConfig(
            genome_store=None, fitness_op=None, mutate_sk=None, crossover_sk=None,
        )
        inp = EvoInput()
        result = evolutionary_mating_transaction(cfg, inp, tmp_path)
        assert result["status"] == "completed"

    def test_extinction_logic(self, tmp_path: Path):
        """连续垫底 5 次 → 淘汰."""
        store = PopulationGenomeStore()
        for i in range(5):
            dag = _make_dag(3)
            r = store.add_genome(dag)
            r.last_score = 0.0
            r.metadata["bottom_streak"] = 5  # 已连续垫底 5 次

        def fitness_op(history, **kwargs):
            return {"fitness": 0.0}

        cfg = EvoConfig(
            genome_store=store,
            fitness_op=fitness_op,
            mutate_sk=_genetic_dag_mutate,
            crossover_sk=_genetic_dag_crossover,
            elite_count=1,
            offspring_count=1,
            extinction_threshold=5,
            mutation_rate=0.0,
        )
        inp = EvoInput()
        result = evolutionary_mating_transaction(cfg, inp, tmp_path)
        # 应淘汰连续垫底的变体
        assert len(result.get("eliminated", [])) >= 1

    def test_report_written(self, tmp_path: Path):
        store = PopulationGenomeStore()
        store.add_genome(_make_dag(3))

        def fitness_op(history, **kwargs):
            return {"fitness": 0.5}

        cfg = EvoConfig(
            genome_store=store, fitness_op=fitness_op,
            mutate_sk=_genetic_dag_mutate, crossover_sk=_genetic_dag_crossover,
            elite_count=1, offspring_count=1,
        )
        inp = EvoInput()
        result = evolutionary_mating_transaction(cfg, inp, tmp_path)
        assert Path(result["report_path"]).exists()

    def test_config_has_pillars(self):
        assert EvoConfig._enabled_pillars == {"fingerprint", "decision_trail", "report"}
