"""Production-capable tool adapters for every supported provider.

Six production adapters for the canonical provider set, plus two
domain-specific adapters (Obsidian vault, Paperclip placeholder) for
non-LLM tool surfaces:

| Adapter           | Provider   | Stream | Auth | Capabilities (canonical)        |
|-------------------|-----------|--------|------|---------------------------------|
| OpenAIAdapter     | openai    | yes    | yes  | reasoning, planning, code_generation, vision, image_generation, video_generation, voice_generation |
| ClaudeAdapter     | anthropic | yes    | yes  | reasoning, planning, code_generation, vision |
| MiniMaxAdapter    | minimax   | yes    | yes  | reasoning, planning, code_generation, vision |
| GeminiAdapter     | gemini    | yes    | yes  | reasoning, planning, code_generation, vision, image_generation, video_generation |
| OllamaAdapter     | ollama    | yes    | no*  | reasoning, planning, code_generation, vision |
| MCPServerAdapter  | mcp       | yes    | no*  | code_generation, memory, retrieval, communication, desktop_automation, browser_automation, vision |
| ObsidianAdapter   | obsidian  | no     | no   | retrieval, memory |
| PaperclipAdapter  | paperclip | no     | yes  | (placeholder) |

`*` = optional auth via a configurable env var.

Every adapter implements the existing `ToolAdapter` Protocol. Adapters
read their configuration exclusively from `ConfigurationManager`
(their `from_configuration_manager(...)` alternative constructor is
additive alongside the original plain constructor) and emit the
canonical `tool_manager.provider.*` event vocabulary via the shared
`ProviderRecorder`.
"""
from hermes.modules.tool_manager.adapters.base import BaseToolAdapter
from hermes.modules.tool_manager.adapters.claude_adapter import (
    ANTHROPIC_API_KEY_ENV_VAR,
    AnthropicAuthenticationError,
    AnthropicProviderConfig,
    ClaudeAdapter,
    register_with_capability_registry as register_anthropic,
)
from hermes.modules.tool_manager.adapters.gemini_adapter import (
    GEMINI_API_KEY_ENV_VAR,
    GeminiAdapter,
    GeminiAuthenticationError,
    GeminiProviderConfig,
    register_with_capability_registry as register_gemini,
)
from hermes.modules.tool_manager.adapters.http_base import (
    CancellationToken,
    HTTPConnectionError,
    HTTPRequest,
    HTTPResponse,
    HTTPStatusError,
    HTTPTimeoutError,
    HTTPTransportError,
    HTTPCancelledError,
    StdlibHTTPTransport,
    Transport,
    make_authorization_header,
    safe_json_loads,
)
from hermes.modules.tool_manager.adapters.mcp_server_adapter import (
    MCPError,
    MCPProtocolError,
    MCPProviderConfig,
    MCPTransportError,
    MCPServerAdapter,
    register_with_capability_registry as register_mcp,
)
from hermes.modules.tool_manager.adapters.minimax_adapter import (
    MINIMAX_API_KEY_ENV_VAR,
    MiniMaxAdapter,
    MiniMaxAuthenticationError,
    MiniMaxProviderConfig,
    register_with_capability_registry as register_minimax,
)
from hermes.modules.tool_manager.adapters.obsidian_adapter import ObsidianAdapter
from hermes.modules.tool_manager.adapters.ollama_adapter import OllamaAdapter, OllamaProviderConfig
from hermes.modules.tool_manager.adapters.openai_adapter import (
    OPENAI_API_KEY_ENV_VAR,
    OpenAIAdapter,
    OpenAIAuthenticationError,
    OpenAIProviderConfig,
    register_with_capability_registry as register_openai,
)
from hermes.modules.tool_manager.adapters.paperclip_adapter import PaperclipAdapter
from hermes.modules.tool_manager.adapters.provider_config import (
    AnthropicProviderConfig,
    GeminiProviderConfig,
    MCPProviderConfig,
    MiniMaxProviderConfig,
    OllamaProviderConfig,
    OpenAIProviderConfig,
    ProviderConfigBase,
    TokenUsage,
    estimate_cost_usd,
    provider_config_for,
    provider_names,
    supported_capabilities,
)
from hermes.modules.tool_manager.adapters.provider_events import (
    PROVIDER_CANCELLED,
    PROVIDER_ESTIMATED_COST,
    PROVIDER_FAILED,
    PROVIDER_HEALTH_CHANGED,
    PROVIDER_LATENCY,
    PROVIDER_RETRY,
    PROVIDER_SELECTED,
    PROVIDER_SUCCEEDED,
    PROVIDER_TIMEOUT,
    PROVIDER_TOKEN_USAGE,
    ProviderEventLog,
    ProviderRecorder,
    Stopwatch,
)


__all__ = [
    # Base
    "BaseToolAdapter",
    # Adapters
    "OpenAIAdapter",
    "ClaudeAdapter",
    "MiniMaxAdapter",
    "GeminiAdapter",
    "OllamaAdapter",
    "MCPServerAdapter",
    "ObsidianAdapter",
    "PaperclipAdapter",
    # Auth errors
    "OpenAIAuthenticationError",
    "AnthropicAuthenticationError",
    "MiniMaxAuthenticationError",
    "GeminiAuthenticationError",
    # MCP errors
    "MCPError",
    "MCPTransportError",
    "MCPProtocolError",
    # Config
    "ProviderConfigBase",
    "OpenAIProviderConfig",
    "AnthropicProviderConfig",
    "MiniMaxProviderConfig",
    "GeminiProviderConfig",
    "OllamaProviderConfig",
    "MCPProviderConfig",
    "provider_config_for",
    "provider_names",
    "supported_capabilities",
    "TokenUsage",
    "estimate_cost_usd",
    # HTTP primitives
    "Transport",
    "StdlibHTTPTransport",
    "HTTPRequest",
    "HTTPResponse",
    "HTTPTransportError",
    "HTTPConnectionError",
    "HTTPTimeoutError",
    "HTTPCancelledError",
    "HTTPStatusError",
    "CancellationToken",
    "make_authorization_header",
    "safe_json_loads",
    # Constants
    "OPENAI_API_KEY_ENV_VAR",
    "ANTHROPIC_API_KEY_ENV_VAR",
    "MINIMAX_API_KEY_ENV_VAR",
    "GEMINI_API_KEY_ENV_VAR",
    # Observability
    "ProviderRecorder",
    "ProviderEventLog",
    "Stopwatch",
    "PROVIDER_SELECTED",
    "PROVIDER_SUCCEEDED",
    "PROVIDER_FAILED",
    "PROVIDER_TIMEOUT",
    "PROVIDER_RETRY",
    "PROVIDER_TOKEN_USAGE",
    "PROVIDER_LATENCY",
    "PROVIDER_ESTIMATED_COST",
    "PROVIDER_CANCELLED",
    "PROVIDER_HEALTH_CHANGED",
    # Registration helpers
    "register_openai",
    "register_anthropic",
    "register_minimax",
    "register_gemini",
    "register_mcp",
]
