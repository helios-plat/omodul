"""O1/O2/O3 omodul tests for assignment, neuro-symbolic, and sandbox lookahead."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock


class TestDeterministicOperateAssign:
    def test_completed_with_assign(self, tmp_path: Path):
        from omodul.deterministic_operate_assign import (
            Config,
            InputData,
            deterministic_operate_assign,
        )

        def assign_op(matrix, *, workers, tasks):
            return {
                "status": "success",
                "matches": [("A", "X"), ("B", "Y")],
                "total_cost": 5.0,
                "unassigned_workers": [],
                "unassigned_tasks": [],
            }

        cfg = Config(assign_op=assign_op, wfg_instance=None)
        inp = InputData(workers=["A", "B"], tasks=["X", "Y"], cost_matrix=[[1, 5], [5, 1]])
        result = deterministic_operate_assign(cfg, inp, tmp_path)
        assert result["status"] == "completed"

    def test_failed_no_raise(self, tmp_path: Path):
        from omodul.deterministic_operate_assign import (
            Config,
            InputData,
            deterministic_operate_assign,
        )

        def bad_assign(matrix, **kwargs):
            raise RuntimeError("scipy crash")

        cfg = Config(assign_op=bad_assign, wfg_instance=None)
        inp = InputData(workers=["A"], tasks=["T"], cost_matrix=[[1]])
        result = deterministic_operate_assign(cfg, inp, tmp_path)
        assert result["status"] == "failed"

    def test_pillars_present(self):
        from omodul.deterministic_operate_assign import Config
        assert Config._enabled_pillars == {"fingerprint", "decision_trail", "report"}


class TestNeuroSymbolicOrchestrate:
    def test_sat_verdict(self, tmp_path: Path):
        from omodul.neuro_symbolic_orchestrate import (
            Config,
            InputData,
            neuro_symbolic_orchestrate,
        )

        def z3_op(script):
            return {"verdict": "sat", "error": None}

        llm = MagicMock(return_value="All constraints are consistent.")
        cfg = Config(z3_check_op=z3_op, mus_shrink_sk=None, llm_caller=llm)
        inp = InputData(smt2_spec="(assert true)", constraints=[])
        result = neuro_symbolic_orchestrate(cfg, inp, tmp_path)
        assert result["status"] == "completed"

    def test_failed_no_raise(self, tmp_path: Path):
        from omodul.neuro_symbolic_orchestrate import (
            Config,
            InputData,
            neuro_symbolic_orchestrate,
        )

        def bad_z3(script):
            raise RuntimeError("Z3 crash")

        cfg = Config(z3_check_op=bad_z3, mus_shrink_sk=None, llm_caller=None)
        inp = InputData(smt2_spec="x")
        result = neuro_symbolic_orchestrate(cfg, inp, tmp_path)
        assert result["status"] == "failed"

    def test_pillars_present(self):
        from omodul.neuro_symbolic_orchestrate import Config
        assert "fingerprint" in Config._enabled_pillars
        assert "cost" in Config._enabled_pillars


class TestSandboxObserveLookahead:
    def test_safe_probe(self, tmp_path: Path):
        from omodul.sandbox_observe_lookahead import (
            Config,
            InputData,
            sandbox_observe_lookahead,
        )

        def probe_op(cmd, **kwargs):
            return {"status": "success", "stdout": "ok", "stderr": "", "exit_code": 0}

        cfg = Config(sandbox_op=probe_op, puct_search_sk=None, llm_caller=None)
        inp = InputData(probe_command="echo safe", state_id="test-1")
        result = sandbox_observe_lookahead(cfg, inp, tmp_path)
        assert result["status"] == "completed"

    def test_failed_no_raise(self, tmp_path: Path):
        from omodul.sandbox_observe_lookahead import (
            Config,
            InputData,
            sandbox_observe_lookahead,
        )

        def bad_probe(cmd, **kwargs):
            raise RuntimeError("sandbox crash")

        cfg = Config(sandbox_op=bad_probe, puct_search_sk=None, llm_caller=None)
        inp = InputData(probe_command="boom")
        result = sandbox_observe_lookahead(cfg, inp, tmp_path)
        assert result["status"] == "failed"

    def test_pillars_present(self):
        from omodul.sandbox_observe_lookahead import Config
        assert "fingerprint" in Config._enabled_pillars
        assert "cost" in Config._enabled_pillars
