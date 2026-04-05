from __future__ import annotations


def build_wrapper_rcfile() -> str:
    return r"""# cyber-shell rcfile
if [[ -f /etc/bash.bashrc ]]; then
  source /etc/bash.bashrc
fi

if [[ -f ~/.bashrc ]]; then
  source ~/.bashrc
fi

export HISTCONTROL=
export HISTIGNORE=
shopt -s cmdhist lithist
__cyber_shell_preexec_ready=0
__cyber_shell_command_active=0

__cyber_shell_write_control() {
  [[ -n "${CYBER_SHELL_CONTROL_FD:-}" ]] || return 0
  printf '%s\0' "$@" >&${CYBER_SHELL_CONTROL_FD}
}

__cyber_shell_now() {
  local __cyber_shell_now_value
  printf -v __cyber_shell_now_value '%(%Y-%m-%dT%H:%M:%SZ)T' -1
  printf '%s' "${__cyber_shell_now_value}"
}

__cyber_shell_history_line() {
  local hist_line
  hist_line="$(builtin history 1 2>/dev/null)" || hist_line=""
  if [[ "${hist_line}" =~ ^[[:space:]]*[0-9]+[[:space:]](.*)$ ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
  else
    printf '%s' "${hist_line}"
  fi
}

__cyber_shell_should_ignore_debug() {
  case "${BASH_COMMAND}" in
    __cyber_shell_debugtrap|\
    __cyber_shell_ensure_prompt_hooks|\
    __cyber_shell_array_contains|\
    __cyber_shell_prompt_begin|\
    __cyber_shell_prompt_end|\
    __cyber_shell_should_ignore_debug|\
    __cyber_shell_history_line|\
    __cyber_shell_write_control|\
    __cyber_shell_now)
      return 0
      ;;
  esac
  return 1
}

__cyber_shell_array_contains() {
  local needle="$1"
  shift
  local item
  for item in "$@"; do
    if [[ "${item}" == "${needle}" ]]; then
      return 0
    fi
  done
  return 1
}

__cyber_shell_ensure_prompt_hooks() {
  if declare -p PROMPT_COMMAND >/dev/null 2>&1; then
    if declare -p PROMPT_COMMAND 2>/dev/null | grep -q 'declare \-a'; then
      if ! __cyber_shell_array_contains "__cyber_shell_prompt_begin" "${PROMPT_COMMAND[@]}"; then
        PROMPT_COMMAND=("__cyber_shell_prompt_begin" "${PROMPT_COMMAND[@]}")
      fi
      if ! __cyber_shell_array_contains "__cyber_shell_prompt_end" "${PROMPT_COMMAND[@]}"; then
        PROMPT_COMMAND+=("__cyber_shell_prompt_end")
      fi
    else
      if [[ "${PROMPT_COMMAND}" != *"__cyber_shell_prompt_begin"* ]]; then
        PROMPT_COMMAND="__cyber_shell_prompt_begin;${PROMPT_COMMAND}"
      fi
      if [[ "${PROMPT_COMMAND}" != *"__cyber_shell_prompt_end"* ]]; then
        PROMPT_COMMAND="${PROMPT_COMMAND};__cyber_shell_prompt_end"
      fi
    fi
  else
    PROMPT_COMMAND="__cyber_shell_prompt_begin;__cyber_shell_prompt_end"
  fi
}

__cyber_shell_debugtrap() {
  local trap_status started_at current_cmd
  trap_status=$?
  if [[ "${__cyber_shell_preexec_ready:-0}" != 1 ]]; then
    return "${trap_status}"
  fi
  if __cyber_shell_should_ignore_debug; then
    return "${trap_status}"
  fi

  __cyber_shell_ensure_prompt_hooks
  __cyber_shell_preexec_ready=0
  __cyber_shell_command_active=1
  started_at="$(__cyber_shell_now)"
  current_cmd="$(__cyber_shell_history_line)"
  __cyber_shell_write_control PRE "${started_at}" "${current_cmd}"
  return "${trap_status}"
}

__cyber_shell_prompt_begin() {
  local exit_code finished_at
  exit_code=$?
  finished_at="$(__cyber_shell_now)"
  if [[ "${__cyber_shell_command_active:-0}" == 1 ]]; then
    __cyber_shell_write_control POST "${finished_at}" "${exit_code}" "${PWD}"
    __cyber_shell_command_active=0
  fi
  return "${exit_code}"
}

__cyber_shell_prompt_end() {
  __cyber_shell_preexec_ready=1
  return 0
}

__cyber_shell_append_prompt_command() {
  if declare -p PROMPT_COMMAND >/dev/null 2>&1; then
    if declare -p PROMPT_COMMAND 2>/dev/null | grep -q 'declare \-a'; then
      PROMPT_COMMAND=(
        "__cyber_shell_prompt_begin"
        "${PROMPT_COMMAND[@]}"
        "__cyber_shell_prompt_end"
      )
    elif [[ -n "${PROMPT_COMMAND}" ]]; then
      PROMPT_COMMAND="__cyber_shell_prompt_begin;${PROMPT_COMMAND};__cyber_shell_prompt_end"
    else
      PROMPT_COMMAND="__cyber_shell_prompt_begin;__cyber_shell_prompt_end"
    fi
  else
    PROMPT_COMMAND="__cyber_shell_prompt_begin;__cyber_shell_prompt_end"
  fi
}

ask() {
  if [[ -z "$*" ]]; then
    echo -e "\033[1;33mUsage: ask <your question>\033[0m"
    return 1
  fi
  cyber-shell ask "$*"
}

__cyber_shell_append_prompt_command
trap '__cyber_shell_debugtrap' DEBUG
"""
