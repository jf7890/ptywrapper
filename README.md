# Cyber Shell

`cyber-shell` is a local CLI wrapper for interactive Bash on Linux/Kali. It runs a real shell inside a PTY, preserves normal terminal behavior, captures command/output/exit code/cwd, and sends telemetry to a configurable HTTP endpoint in fail-open mode.

## Features

- Runs a real interactive Bash session inside a PTY.
- Relays stdin/stdout in raw terminal mode with resize support.
- Uses shell hooks over a dedicated control pipe instead of printing markers into the terminal.
- Captures one logical event per command with:
  - `cmd`
  - `output`
  - `exit_code`
  - `cwd`
  - `started_at`
  - `finished_at`
  - `is_interactive`
- Sends telemetry asynchronously with timeout, retry, and fail-open behavior.
- Truncates oversized command output with `max_output_bytes`.
- Includes a built-in mock endpoint and dashboard for local testing.
- Strips ANSI color/control sequences from telemetry output while keeping the local terminal unchanged.

## Install

Assume Python 3 and `pip` are already installed and working normally.

```bash
python3 -m pip install .
```

## Quick Start

The fastest test flow uses two terminals.

Terminal 1: start the mock endpoint

```bash
cyber-shell mock-endpoint --host 0.0.0.0 --port 8080 --api-key replace-me
```

Open the dashboard locally:

```text
http://127.0.0.1:8080/
```

If you are accessing it from another machine:

```text
http://<server-ip>:8080/
```

Check that the endpoint is alive:

```bash
curl -i http://127.0.0.1:8080/health
```

Terminal 2: start the wrapped shell and point it at the mock endpoint

```bash
cyber-shell --endpoint-url http://127.0.0.1:8080/api/terminal-events --api-key replace-me
```

Inside that wrapped shell, run a few commands:

```bash
whoami
pwd
ls -la
```

To confirm that you are inside the wrapped session:

```bash
echo $CYBER_SHELL_SESSION_ID
```

If it prints a value like `sess-...`, you are inside a `cyber-shell` session.

## Configuration

By default, `cyber-shell` reads:

```text
~/.config/cyber-shell/config.yaml
```

The config format is intentionally simple YAML with optional environment variable overrides.

Sample config:

```yaml
endpoint_url: "http://127.0.0.1:8080/api/terminal-events"
api_key: "replace-me"
timeout_ms: 3000
retry_max: 3
retry_backoff_ms: 1000
max_output_bytes: 262144
queue_size: 256
user_id: "student-01"
lab_id: "lab-web-02"
target_id: "target-a"
metadata:
  hostname_group: "kali-lab"
```

Print the default template:

```bash
cyber-shell print-default-config
```

Supported environment overrides:

- `CYBER_SHELL_CONFIG`
- `CYBER_SHELL_ENDPOINT_URL`
- `CYBER_SHELL_API_KEY`
- `CYBER_SHELL_TIMEOUT_MS`
- `CYBER_SHELL_RETRY_MAX`
- `CYBER_SHELL_RETRY_BACKOFF_MS`
- `CYBER_SHELL_MAX_OUTPUT_BYTES`
- `CYBER_SHELL_QUEUE_SIZE`
- `CYBER_SHELL_USER_ID`
- `CYBER_SHELL_LAB_ID`
- `CYBER_SHELL_TARGET_ID`
- `CYBER_SHELL_SHELL_PATH`

## Manual Endpoint Test

You can test the dashboard without starting the wrapped shell:

```bash
curl -i -X POST http://127.0.0.1:8080/api/terminal-events \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer replace-me" \
  -d '{"session_id":"s1","seq":1,"cmd":"whoami","cwd":"/home/kali","exit_code":0,"output":"kali","output_truncated":false,"started_at":"2026-03-21T10:00:00Z","finished_at":"2026-03-21T10:00:01Z","is_interactive":false,"hostname":"kali","shell":"bash","user_id":"student-01","lab_id":"lab-01","target_id":"t1","metadata":{}}'
```

## Notes

- This tool targets POSIX/Linux, with Kali as the primary environment.
- V1 does not semantically parse `vim`, `top`, `nano`, `less`, or `man`; it only preserves terminal behavior and finalizes the event when the prompt returns.
- Nested shells and remote shells are treated as opaque terminal streams.
- The wrapper does not capture raw keystrokes for the entire session.

## Dev Checks

```bash
python -m unittest discover -s tests -v
python -m compileall src tests
```
