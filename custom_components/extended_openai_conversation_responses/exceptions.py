"""The exceptions used by Extended OpenAI Conversation (Responses)."""

from homeassistant.exceptions import HomeAssistantError


class EntityNotFound(HomeAssistantError):
    """When referenced entity not found."""

    def __init__(self, entity_id: str) -> None:
        """Initialize error."""
        super().__init__(self, f"entity {entity_id} not found")
        self.entity_id = entity_id

    def __str__(self) -> str:
        """Return string representation."""
        return f"Unable to find entity {self.entity_id}"


class EntityNotExposed(HomeAssistantError):
    """When referenced entity not exposed."""

    def __init__(self, entity_id: str, reason: str = "exposure") -> None:
        """Initialize error."""
        super().__init__(self, f"entity {entity_id} not exposed")
        self.entity_id = entity_id
        self.reason = reason

    def __str__(self) -> str:
        """Return string representation."""
        if self.reason == "permission":
            return (
                "The Home Assistant user does not have permission to access this "
                "entity. Ask a Home Assistant administrator to review user permissions."
            )
        if self.reason == "policy":
            return (
                "Entity access is denied by the current Function Tool access policy. "
                "Review this assistant's Guest Mode and tool access settings."
            )
        return (
            f"Entity {self.entity_id} is not exposed to Assist. Review Settings > "
            "Voice assistants > Expose in Home Assistant."
        )


class CallServiceError(HomeAssistantError):
    """Error during service calling"""

    def __init__(self, domain: str, service: str, data: object) -> None:
        """Initialize error."""
        super().__init__(
            self,
            f"unable to call service {domain}.{service} with data {data}. One of 'entity_id', 'area_id', or 'device_id' is required",
        )
        self.domain = domain
        self.service = service
        self.data = data

    def __str__(self) -> str:
        """Return string representation."""
        return f"unable to call service {self.domain}.{self.service} with data {self.data}. One of 'entity_id', 'area_id', or 'device_id' is required"


class FunctionNotFound(HomeAssistantError):
    """When referenced function not found."""

    def __init__(self, function: str) -> None:
        """Initialize error."""
        super().__init__(self, f"function '{function}' does not exist")
        self.function = function

    def __str__(self) -> str:
        """Return string representation."""
        return f"function '{self.function}' does not exist"


class NativeNotFound(HomeAssistantError):
    """When native function not found."""

    def __init__(self, name: str) -> None:
        """Initialize error."""
        super().__init__(self, f"native function '{name}' does not exist")
        self.name = name

    def __str__(self) -> str:
        """Return string representation."""
        return f"native function '{self.name}' does not exist"


class FunctionLoadFailed(HomeAssistantError):
    """When function load failed."""

    def __init__(self) -> None:
        """Initialize error."""
        super().__init__(
            self,
            "Unable to assemble Function Tools. Enable Home Assistant debug logging "
            "for Extended OpenAI to investigate the internal failure.",
        )

    def __str__(self) -> str:
        """Return string representation."""
        return (
            "Unable to assemble Function Tools. Enable Home Assistant debug logging "
            "for Extended OpenAI to investigate the internal failure."
        )


class ParseArgumentsFailed(HomeAssistantError):
    """When parse arguments failed."""

    def __init__(self, arguments: str) -> None:
        """Initialize error."""
        super().__init__(
            self,
            "The provider returned malformed or unparseable tool-call arguments.",
        )
        self.arguments = arguments

    def __str__(self) -> str:
        """Return string representation."""
        return "The provider returned malformed or unparseable tool-call arguments."


class FunctionValidationInfrastructureError(HomeAssistantError):
    """When local Function Tool validation cannot complete safely."""


class TokenLengthExceededError(HomeAssistantError):
    """When openai return 'length' as 'finish_reason'."""

    def __init__(self, token: int) -> None:
        """Initialize error."""
        super().__init__(
            self,
            f"Maximum response length ({token} tokens) reached. Increase Maximum "
            "response length in this assistant's Extended OpenAI configuration, "
            "or ask for a shorter response.",
        )
        self.token = token

    def __str__(self) -> str:
        """Return string representation."""
        return (
            f"Maximum response length ({self.token} tokens) reached. Increase Maximum "
            "response length in this assistant's Extended OpenAI configuration, "
            "or ask for a shorter response."
        )


class InvalidFunction(HomeAssistantError):
    """When function validation failed."""

    def __init__(self, function_name: str) -> None:
        """Initialize error."""
        super().__init__(
            self,
            f"failed to validate function `{function_name}`",
        )
        self.function_name = function_name

    def __str__(self) -> str:
        """Return string representation."""
        return f"failed to validate function `{self.function_name}` ({self.__cause__})"
