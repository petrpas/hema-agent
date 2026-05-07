"""System-level configuration package (shared across all phases).

agent_config.json  — system/AI settings, committed, one section per module
"""

from shared.config.agent_config import (
    AgentConfig,
    RegAgentSystemConfig,
    Step,
    load_agent_config,
)

__all__ = [
    "AgentConfig",
    "RegAgentSystemConfig",
    "Step",
    "load_agent_config",
]
