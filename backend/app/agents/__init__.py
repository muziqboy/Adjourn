"""The agent registry. Each agent is one file in this folder exposing `AGENT` (see base.py).

To add an agent:
1. Copy issue.py (it shows every hook) to `<type>.py`, change the prompt, schema and checks.
2. Import it below and add it to `_ALL`.
3. Add its type to AGENTS in .env to let the intent pass create it.
4. Optional: a card face in frontend/src/agents/index.tsx (there is a generic default).

Registry order matters only in mock mode, where each agent's keyword matcher runs in turn.
"""

from ..core.config import settings
from ..core.contract import AgentInfo
from . import answer, email, issue, research, schedule
from .base import AgentSpec

_ALL: list[AgentSpec] = [answer.AGENT, research.AGENT, issue.AGENT, email.AGENT, schedule.AGENT]
REGISTRY: dict[str, AgentSpec] = {spec.type: spec for spec in _ALL}


def get(type_: str) -> AgentSpec | None:
    return REGISTRY.get(type_)


def enabled() -> list[AgentSpec]:
    """The agents the intent pass may create tasks for (AGENTS in .env), in registry order."""
    return [spec for spec in _ALL if spec.type in settings.agents]


def describe() -> list[dict]:
    """Card metadata for every registered agent, sent to the panel in each snapshot."""
    return [
        AgentInfo(type=s.type, label=s.label, approval=s.approval, approval_again=s.approval_again,
                  opens_link=s.opens_link()).model_dump()
        for s in _ALL
    ]
