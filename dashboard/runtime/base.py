"""Abstract base class for agent runtimes."""
from abc import ABC, abstractmethod
from typing import Optional


class AgentRuntime(ABC):
    """Interface for all agent execution backends.

    Implementations: OpenClawRuntime, OpenAIRuntime, AnthropicRuntime
    """

    @abstractmethod
    def run(self, agent_id: str, session_id: str, prompt: str,
            timeout: int = 120) -> str:
        """Send a prompt to an agent and return its response text.

        Args:
            agent_id: Fully-qualified agent identifier (e.g. "company-ceo")
            session_id: Session / conversation thread ID for memory continuity
            prompt: The instruction text to send
            timeout: Maximum seconds to wait for a response

        Returns:
            The agent's response text, or empty string on failure.
        """

    @abstractmethod
    def register(self, agent_id: str, workspace: str, soul_content: str = '') -> bool:
        """Register (or re-register) an agent with the runtime.

        Args:
            agent_id: Unique agent identifier
            workspace: Path to the agent's workspace directory
            soul_content: Optional system prompt / persona definition

        Returns:
            True on success, False on failure.
        """

    @abstractmethod
    def delete(self, agent_id: str) -> bool:
        """Remove an agent from the runtime.

        Returns:
            True if the agent was found and removed, False otherwise.
        """

    def name(self) -> str:
        """Human-readable name of this runtime."""
        return self.__class__.__name__
