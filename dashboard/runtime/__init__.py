"""Runtime abstraction layer for AI agent execution."""
from .base import AgentRuntime
from .openclaw import OpenClawRuntime

_BACKENDS = {
    'openclaw': OpenClawRuntime,
}

def get_runtime(name='openclaw', **kwargs) -> AgentRuntime:
    cls = _BACKENDS.get(name)
    if cls is None:
        raise ValueError(f"Unknown runtime: {name}. Available: {list(_BACKENDS)}")
    return cls(**kwargs)

__all__ = ['AgentRuntime', 'OpenClawRuntime', 'get_runtime']
