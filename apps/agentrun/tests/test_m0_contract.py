"""M0 contract test:Run Directory Contract 原子写、events seq、result 校验、yaml_lite。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentrun.core import yaml_lite
from agentrun.core.contract import mark_done_if_valid, validate_result, write_result
from agentrun.core.events import append_event, next_seq
from agentrun.core.jsonio import read_json, write_json_atomic
from agentrun.core.rundir import run_paths
from agentrun.core.run import TASK, RunRequest, new_run_id


def _req(run_id: str) -> RunRequest:
    return RunRequest(run_type=TASK, run_id=run_id, provider_profile="codex-cli", provider="cli")


class ContractTest(unittest.TestCase):
    def test_atomic_json_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.json"
            write_json_atomic(p, {"a": 1, "中": "文"})
            self.assertEqual(read_json(p), {"a": 1, "中": "文"})

    def test_events_seq_monotonic(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            paths = run_paths(Path(d), "_default", TASK, "task-1").ensure()
            for _ in range(5):
                append_event(paths, "task-1", TASK, "status.changed", {"state": "running"})
            self.assertEqual(next_seq(paths), 6)
            from agentrun.core.jsonio import read_jsonl

            seqs = [r["seq"] for r in read_jsonl(paths.events_file)]
            self.assertEqual(seqs, [1, 2, 3, 4, 5])

    def test_result_invalid_outcome_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            paths = run_paths(Path(d), "_default", TASK, "task-2").ensure()
            with self.assertRaises(ValueError):
                write_result(paths, _req("task-2"), "bogus")

    def test_mark_done_missing_result(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            req = _req(new_run_id(TASK))
            paths = run_paths(Path(d), "_default", TASK, req.run_id).ensure()
            status = mark_done_if_valid(paths, req)
            self.assertEqual(status["state"], "failed")
            self.assertEqual(status["failure_reason"], "result_missing")

    def test_mark_done_valid_result(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            req = _req(new_run_id(TASK))
            paths = run_paths(Path(d), "_default", TASK, req.run_id).ensure()
            write_result(paths, req, "succeeded", summary="ok")
            ok, reason = validate_result(paths)
            self.assertTrue(ok)
            self.assertIsNone(reason)
            status = mark_done_if_valid(paths, req)
            self.assertEqual(status["state"], "done")

    def test_result_schema_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            req = _req(new_run_id(TASK))
            paths = run_paths(Path(d), "_default", TASK, req.run_id).ensure()
            write_json_atomic(
                paths.result_file,
                {
                    "schema_version": 1,
                    "run_id": req.run_id,
                    "outcome": "succeeded",
                    # 缺 summary / artifacts / errors / validation
                },
            )
            status = mark_done_if_valid(paths, req)
            self.assertEqual(status["state"], "failed")
            self.assertEqual(status["failure_reason"], "schema_invalid")

    def test_yaml_lite_subset(self) -> None:
        data = yaml_lite.loads(
            "runs_dir: runs\n"
            "max_concurrency: 2\n"
            "profiles:\n"
            "  - id: codex-cli\n"
            "    transport: cli\n"
            "    default_args: [exec, -p]\n"
            "    result_contract: required\n"
        )
        self.assertEqual(data["runs_dir"], "runs")
        self.assertEqual(data["max_concurrency"], 2)
        self.assertEqual(data["profiles"][0]["id"], "codex-cli")
        self.assertEqual(data["profiles"][0]["default_args"], ["exec", "-p"])


if __name__ == "__main__":
    unittest.main()
