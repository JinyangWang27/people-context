"""Concrete agent runners: one offline, one that shells out to an agent CLI."""

from __future__ import annotations

from evals.harness.errors import EvalHarnessError
from evals.harness.ports import AgentRunner
from evals.harness.runners.command import CommandAgentRunner
from evals.harness.runners.stub import StubAgentRunner
from evals.harness.suite import CommandRunnerConfig, LoadedSuite, RunnerConfig, StubRunnerConfig

__all__ = ["CommandAgentRunner", "StubAgentRunner", "build_runner"]


def build_runner(config: RunnerConfig, loaded: LoadedSuite) -> AgentRunner:
    """Construct the runner a suite entry describes."""
    if isinstance(config, StubRunnerConfig):
        return StubAgentRunner.from_path(loaded.asset_path(config.transcripts), config.model_id)
    if isinstance(config, CommandRunnerConfig):
        return CommandAgentRunner(config)
    raise EvalHarnessError(f"unsupported runner kind: {type(config).__name__}")
