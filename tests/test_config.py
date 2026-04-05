from __future__ import annotations

import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from cyber_shell.config import (
    AppConfig,
    PERSISTED_ENV_KEYS,
    has_runtime_overrides,
    load_config,
    persist_config,
)


class ConfigTests(unittest.TestCase):
    def test_loads_simple_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    endpoint_url: "http://127.0.0.1:8080/api/terminal-events"
                    api_key: "secret"
                    burp_mcp_url: "http://127.0.0.1:3000"
                    timeout_ms: 1234
                    retry_max: 5
                    metadata:
                      role: "student"
                    """
                ).strip(),
                encoding="utf-8",
            )

            with _patched_clean_cyber_shell_env():
                config = load_config(config_path)

        self.assertEqual(
            config.endpoint_url,
            "http://127.0.0.1:8080/api/terminal-events",
        )
        self.assertEqual(config.api_key, "secret")
        self.assertEqual(config.burp_mcp_url, "http://127.0.0.1:3000")
        self.assertEqual(config.timeout_ms, 1234)
        self.assertEqual(config.retry_max, 5)
        self.assertEqual(config.metadata, {"role": "student"})

    def test_persist_config_writes_runtime_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config = AppConfig(
                endpoint_url="http://127.0.0.1:8080/api/terminal-events",
                api_key="replace-me",
                burp_mcp_url="http://127.0.0.1:3000",
                timeout_ms=3000,
                retry_max=3,
                retry_backoff_ms=1000,
                max_output_bytes=262144,
                queue_size=256,
                shell_path="/bin/bash",
                config_path=config_path,
                metadata={"hostname_group": "kali-lab"},
            )

            persist_config(config)
            written = config_path.read_text(encoding="utf-8")

        self.assertIn('endpoint_url: "http://127.0.0.1:8080/api/terminal-events"', written)
        self.assertIn('api_key: "replace-me"', written)
        self.assertIn('burp_mcp_url: "http://127.0.0.1:3000"', written)
        self.assertIn('shell_path: "/bin/bash"', written)
        self.assertIn('hostname_group: "kali-lab"', written)

    def test_has_runtime_overrides_detects_cli_or_env(self) -> None:
        with _patched_clean_cyber_shell_env():
            self.assertTrue(
                has_runtime_overrides({"endpoint_url": "http://127.0.0.1:8080"})
            )
            self.assertTrue(
                has_runtime_overrides({"burp_mcp_url": "http://127.0.0.1:3000"})
            )
            self.assertFalse(has_runtime_overrides({}))
            with patch.dict("os.environ", {"CYBER_SHELL_API_KEY": "secret"}):
                self.assertTrue(has_runtime_overrides({}))


def _patched_clean_cyber_shell_env():
    return patch.dict(
        "os.environ",
        {key: os.environ[key] for key in os.environ if key not in PERSISTED_ENV_KEYS},
        clear=True,
    )


if __name__ == "__main__":
    unittest.main()
