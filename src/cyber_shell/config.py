from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_CONFIG_PATH = Path.home() / ".config" / "cyber-shell" / "config.yaml"
DEFAULT_STATE_DIR = Path.home() / ".local" / "state" / "cyber-shell"
PERSISTED_ENV_KEYS = {
    "CYBER_SHELL_CONFIG",
    "CYBER_SHELL_ENDPOINT_URL",
    "CYBER_SHELL_API_KEY",
    "CYBER_SHELL_BURP_MCP_URL",
    "CYBER_SHELL_CHAT_TIMEOUT_MS",
    "CYBER_SHELL_TIMEOUT_MS",
    "CYBER_SHELL_RETRY_MAX",
    "CYBER_SHELL_RETRY_BACKOFF_MS",
    "CYBER_SHELL_MAX_OUTPUT_BYTES",
    "CYBER_SHELL_QUEUE_SIZE",
    "CYBER_SHELL_SHELL_PATH",
    "CYBER_SHELL_HOSTNAME",
    "CYBER_SHELL_STATE_DIR",
}


@dataclass(slots=True)
class AppConfig:
    endpoint_url: str | None = None
    api_key: str | None = None
    burp_mcp_url: str = "http://127.0.0.1:3000"
    debug: bool = False
    chat_timeout_ms: int = 60000
    timeout_ms: int = 3000
    retry_max: int = 3
    retry_backoff_ms: int = 1000
    max_output_bytes: int = 262144
    queue_size: int = 256
    shell_path: str = "/bin/bash"
    state_dir: Path = DEFAULT_STATE_DIR
    config_path: Path = DEFAULT_CONFIG_PATH
    hostname: str = field(default_factory=socket.gethostname)
    metadata: dict[str, str] = field(default_factory=dict)

    def ensure_state_dir(self) -> Path:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        return self.state_dir


def load_config(path: str | Path | None = None) -> AppConfig:
    config_path = Path(
        path
        or os.environ.get("CYBER_SHELL_CONFIG")
        or DEFAULT_CONFIG_PATH
    ).expanduser()
    data: dict[str, object] = {}
    if config_path.exists():
        data = _parse_simple_yaml(config_path.read_text(encoding="utf-8"))

    config = AppConfig(
        endpoint_url=_env_or_data("CYBER_SHELL_ENDPOINT_URL", data, "endpoint_url"),
        api_key=_env_or_data("CYBER_SHELL_API_KEY", data, "api_key"),
        burp_mcp_url=str(
            _env_or_data("CYBER_SHELL_BURP_MCP_URL", data, "burp_mcp_url")
            or "http://127.0.0.1:3000"
        ),
        debug=_coerce_bool(
            _env_or_data("CYBER_SHELL_DEBUG", data, "debug"),
            False,
        ),
        chat_timeout_ms=_coerce_int(
            _env_or_data("CYBER_SHELL_CHAT_TIMEOUT_MS", data, "chat_timeout_ms"),
            60000,
        ),
        timeout_ms=_coerce_int(
            _env_or_data("CYBER_SHELL_TIMEOUT_MS", data, "timeout_ms"), 3000
        ),
        retry_max=_coerce_int(
            _env_or_data("CYBER_SHELL_RETRY_MAX", data, "retry_max"), 3
        ),
        retry_backoff_ms=_coerce_int(
            _env_or_data("CYBER_SHELL_RETRY_BACKOFF_MS", data, "retry_backoff_ms"),
            1000,
        ),
        max_output_bytes=_coerce_int(
            _env_or_data("CYBER_SHELL_MAX_OUTPUT_BYTES", data, "max_output_bytes"),
            262144,
        ),
        queue_size=_coerce_int(
            _env_or_data("CYBER_SHELL_QUEUE_SIZE", data, "queue_size"), 256
        ),
        shell_path=str(
            _env_or_data("CYBER_SHELL_SHELL_PATH", data, "shell_path") or "/bin/bash"
        ),
        state_dir=Path(
            _env_or_data("CYBER_SHELL_STATE_DIR", data, "state_dir")
            or DEFAULT_STATE_DIR
        ).expanduser(),
        config_path=config_path,
        hostname=str(
            _env_or_data("CYBER_SHELL_HOSTNAME", data, "hostname")
            or socket.gethostname()
        ),
        metadata=_coerce_metadata(data.get("metadata")),
    )
    config.ensure_state_dir()
    return config


def default_config_text() -> str:
    return _serialize_config(
        AppConfig(
            endpoint_url="http://127.0.0.1:8080/api/terminal-events",
            api_key="replace-me",
            burp_mcp_url="http://127.0.0.1:3000",
            debug=False,
            chat_timeout_ms=60000,
            timeout_ms=3000,
            retry_max=3,
            retry_backoff_ms=1000,
            max_output_bytes=262144,
            queue_size=256,
            shell_path="/bin/bash",
            metadata={"hostname_group": "kali-lab"},
        )
    )


def persist_config(config: AppConfig) -> Path:
    config.config_path.parent.mkdir(parents=True, exist_ok=True)
    config.config_path.write_text(_serialize_config(config), encoding="utf-8")
    if os.name == "posix":
        os.chmod(config.config_path, 0o600)
    return config.config_path


def has_runtime_overrides(cli_args: dict[str, object] | None = None) -> bool:
    if cli_args:
        if cli_args.get("endpoint_url") or cli_args.get("api_key"):
            return True
    return any(key in os.environ for key in PERSISTED_ENV_KEYS)


def _env_or_data(env_name: str, data: dict[str, object], key: str) -> object:
    if env_name in os.environ:
        return os.environ[env_name]
    return data.get(key)


def _coerce_int(value: object, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        return int(str(value))
    except ValueError:
        return default


def _coerce_bool(value: object, default: bool) -> bool:
    if value in (None, ""):
        return default
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return default


def _coerce_metadata(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(inner) for key, inner in value.items()}


def _serialize_config(config: AppConfig) -> str:
    lines = [
        f'endpoint_url: {_yaml_string(config.endpoint_url)}'
        if config.endpoint_url is not None
        else "endpoint_url: null",
        f'api_key: {_yaml_string(config.api_key)}'
        if config.api_key is not None
        else "api_key: null",
        f'burp_mcp_url: {_yaml_string(config.burp_mcp_url)}',
        f"chat_timeout_ms: {config.chat_timeout_ms}",
        f"timeout_ms: {config.timeout_ms}",
        f"retry_max: {config.retry_max}",
        f"retry_backoff_ms: {config.retry_backoff_ms}",
        f"max_output_bytes: {config.max_output_bytes}",
        f"queue_size: {config.queue_size}",
        f'shell_path: {_yaml_string(config.shell_path)}',
    ]
    if config.metadata:
        lines.append("metadata:")
        for key, value in config.metadata.items():
            lines.append(f"  {key}: {_yaml_string(value)}")
    return "\n".join(lines) + "\n"


def _yaml_string(value: object) -> str:
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _parse_simple_yaml(text: str) -> dict[str, object]:
    result: dict[str, object] = {}
    stack: list[tuple[int, dict[str, object]]] = [(-1, result)]

    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue
        stripped = raw_line.lstrip()
        if stripped.startswith("#"):
            continue

        indent = len(raw_line) - len(stripped)
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()

        if ":" not in stripped:
            continue
        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        value = raw_value.strip()

        parent = stack[-1][1]
        if not value:
            child: dict[str, object] = {}
            parent[key] = child
            stack.append((indent, child))
            continue

        parent[key] = _parse_scalar(value)

    return result


def _parse_scalar(value: str) -> object:
    if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
        return value[1:-1]
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"null", "none"}:
        return None
    try:
        return int(value)
    except ValueError:
        return value
