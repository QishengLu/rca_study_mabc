"""mABC eval agent — multi-agent voting (ProcessScheduler + 6 experts).

This is the only framework with `multi_agent=True`. The legacy agent_runner.py
emits OpenAI-flat messages from the *main* ProcessScheduler thread; sub-agent
ReAct loops are tagged via custom keys (`_subagent_name`, `_subagent_task_id`)
that we lift into v3 SubAgentCall structure.

Also: mABC originally needed rcabench JSON column format. We patched the
loader (frameworks/mabc/data_adapter.py) to read ops-lite parquet instead.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sota_rca.legacy_agent import LegacySubprocessAgent, LegacyAgentConfig
from sota_rca.data.converters import (
    build_multi_agent_trajectory,
    openai_message_to_v3,
)

logger = logging.getLogger(__name__)

try:
    from rcabench_platform.v3.sdk.llm_eval.trajectory import (  # type: ignore
        Trajectory, AgentTrajectory, Turn, Message,
    )
except ImportError:
    from sota_rca.data.trajectory import (  # type: ignore
        Trajectory, AgentTrajectory, Turn, Message,
    )

_FW_DIR = Path(__file__).resolve().parent.parent.parent.parent  # frameworks/mabc


class MABCEvalAgent(LegacySubprocessAgent):
    @staticmethod
    def name() -> str:
        return "mabc"

    @classmethod
    def _default_config(cls) -> LegacyAgentConfig:
        return LegacyAgentConfig(
            framework_name="mabc",
            framework_dir=_FW_DIR,
            cmd=["uv", "run", "python", "agent_runner.py"],
            prompt_yaml=_FW_DIR / "src" / "mabc_rca" / "prompts" / "agents" / "langgraph" / "rca.yaml",
            timeout=3600,  # mABC is slow (33+ LLM calls, ReAct per expert)
            multi_agent=True,
        )

    def _build_trajectory(self, result_data: dict, system_prompt: str):
        """Multi-agent trajectory: split flat messages by _subagent_* tags."""
        flat = result_data.get("trajectory", [])
        sub_agents_data: dict[str, dict] = {}
        main_messages: list[dict] = []

        for msg in flat:
            sub_id = msg.get("_subagent_task_id")
            sub_name = msg.get("_subagent_name")
            if sub_id:
                sub = sub_agents_data.setdefault(sub_id, {
                    "task_id": sub_id,
                    "agent_name": sub_name or "Expert",
                    "instructions": "",
                    "messages": [],
                })
                # Clean the marker before storing
                clean = {k: v for k, v in msg.items() if not k.startswith("_subagent_")}
                sub["messages"].append(clean)
            else:
                main_messages.append(msg)

        if not sub_agents_data:
            # No sub-agent markers — fall back to single-agent wrap
            return super()._build_trajectory(result_data, system_prompt)

        return build_multi_agent_trajectory(
            main_agent_name="ProcessScheduler",
            main_messages=main_messages,
            sub_agents=list(sub_agents_data.values()),
            system_prompt=system_prompt,
        )
