#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_DIR=$(cd "${SCRIPT_DIR}/.." && pwd)
PROFILE=${CXBA_HERMES_PROFILE:-cxba-production}
HOST=${CXBA_GATEWAY_HOST:-127.0.0.1}
PORT=${CXBA_GATEWAY_PORT:-9119}
SPRING_PORT=${CXBA_SPRING_PORT:-8290}
HERMES_BIN=${CXBA_HERMES_BIN:-"${PROJECT_DIR}/.venv/bin/hermes"}
PROFILE_HOME=${CXBA_HERMES_PROFILE_HOME:-"${HOME}/.hermes/profiles/${PROFILE}"}
PROFILE_SOURCE=${CXBA_HERMES_PROFILE_SOURCE:-"${PROJECT_DIR}/profiles/${PROFILE}"}
LOG_FILE=${CXBA_GATEWAY_LOG_FILE:-"${PROFILE_HOME}/logs/cxba-gateway-service.log"}
PID_FILE=${CXBA_GATEWAY_PID_FILE:-"${PROFILE_HOME}/cxba-gateway.pid"}
ENV_FILE=${CXBA_GATEWAY_ENV_FILE:-"${PROJECT_DIR}/config/hermes-gateway.env"}
TMUX_SESSION=${CXBA_GATEWAY_TMUX_SESSION:-cxba-hermes-gateway-9119}

quote_shell() {
	printf '%q' "$1"
}

init_config() {
	if [[ -e "${ENV_FILE}" ]]; then
		echo "Private runtime config already exists: ${ENV_FILE}"
		echo "No changes were made."
		return 0
	fi

	command -v openssl >/dev/null 2>&1 || {
		echo "openssl is required to generate runtime tokens." >&2
		return 1
	}

	local gateway_token spring_mcp_token case_storage_root
	gateway_token=${CXBA_GATEWAY_PRIVATE_TOKEN:-$(openssl rand -hex 32)}
	spring_mcp_token=${CXBA_SPRING_MCP_TOKEN:-$(openssl rand -hex 32)}
	case_storage_root=${CXBA_CASE_STORAGE_ROOT:-$(cd "${PROJECT_DIR}/../cxba-workbench" && pwd)/data/cxba}

	umask 077
	mkdir -p "$(dirname "${ENV_FILE}")"
	{
		printf 'export CXBA_GATEWAY_PRIVATE_TOKEN=%q\n' "${gateway_token}"
		printf 'export CXBA_HERMES_GATEWAY_PRIVATE_TOKEN=%q\n' "${gateway_token}"
		printf 'export CXBA_SPRING_MCP_TOKEN=%q\n' "${spring_mcp_token}"
		printf 'export CXBA_MCP_CONNECTION_TOKEN=%q\n' "${spring_mcp_token}"
		printf 'export CXBA_LOCAL_MODEL=%q\n' "${CXBA_LOCAL_MODEL:-qwen3.6-27b}"
		printf 'export CXBA_LOCAL_MODEL_BASE_URL=%q\n' "${CXBA_LOCAL_MODEL_BASE_URL:-https://llm-gz2xserodo4c3kj6.cn-beijing.maas.aliyuncs.com/compatible-mode/v1}"
		printf 'export CXBA_BAILIAN_API_KEY=%q\n' "${CXBA_BAILIAN_API_KEY:-}"
		printf 'export CXBA_CASE_STORAGE_ROOT=%q\n' "${case_storage_root}"
		printf 'export CXBA_KNOWLEDGE_VAULT_ROOT=%q\n' "${CXBA_KNOWLEDGE_VAULT_ROOT:-$(cd "${PROJECT_DIR}/../cxba-workbench" && pwd)/knowledge-vault}"
		printf 'export CXBA_SPRING_MCP_URL=%q\n' "${CXBA_SPRING_MCP_URL:-http://127.0.0.1:${SPRING_PORT}/mcp}"
		printf 'export CXBA_MCP_ENABLED=true\n'
		printf 'export CXBA_HERMES_GATEWAY_URL=%q\n' "ws://127.0.0.1:${PORT}/api/ws"
		printf 'export NO_PROXY=127.0.0.1,localhost\n'
		printf 'export no_proxy=127.0.0.1,localhost\n'
	} > "${ENV_FILE}"
	chmod 600 "${ENV_FILE}"

	echo "Private runtime config created: ${ENV_FILE} (mode 600)"
	echo "It contains the matching Hermes/Spring token variable names; token values were not printed."
}

listener_pid() {
	lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null | head -1 || true
}

process_command() {
	ps -p "$1" -o command= 2>/dev/null || true
}

is_cxba_gateway_process() {
	local pid=$1
	local command
	command=$(process_command "${pid}")
	[[ ( "${command}" == *"${HERMES_BIN}"* \
		|| "${command}" == *"${PROJECT_DIR}/.venv/bin/python ./.venv/bin/hermes"* \
		|| "${command}" == *"${PROJECT_DIR}/.venv/bin/python ${HERMES_BIN}"* ) \
		&& "${command}" == *"serve"* && "${command}" == *"--port ${PORT}"* ]]
}

process_env_value() {
	local pid=$1
	local key=$2
	ps eww -p "${pid}" 2>/dev/null \
		| tr ' ' '\n' \
		| awk -F= -v wanted="${key}" '$1 == wanted { sub(/^[^=]*=/, ""); print; exit }'
}

load_private_env_file() {
	if [[ -f "${ENV_FILE}" ]]; then
		# shellcheck disable=SC1090
		set -a
		. "${ENV_FILE}"
		set +a
	fi
}

inherit_gateway_environment() {
	local pid=$1
	local key value
	for key in \
		CXBA_GATEWAY_PRIVATE_TOKEN \
		CXBA_SPRING_MCP_TOKEN \
		CXBA_LOCAL_MODEL \
		CXBA_LOCAL_MODEL_BASE_URL \
		CXBA_BAILIAN_API_KEY \
		CXBA_SILICONFLOW_API_KEY \
		CXBA_CASE_STORAGE_ROOT \
		CXBA_KNOWLEDGE_VAULT_ROOT \
		CXBA_SPRING_MCP_URL
	do
		if [[ -z "${!key:-}" ]]; then
			value=$(process_env_value "${pid}" "${key}")
			if [[ -n "${value}" ]]; then
				export "${key}=${value}"
			fi
		fi
	done
}

inherit_spring_tokens() {
	local spring_pid value
	spring_pid=$(lsof -tiTCP:"${SPRING_PORT}" -sTCP:LISTEN 2>/dev/null | head -1 || true)
	[[ -n "${spring_pid}" ]] || return 0

	if [[ -z "${CXBA_GATEWAY_PRIVATE_TOKEN:-}" ]]; then
		value=$(process_env_value "${spring_pid}" CXBA_HERMES_GATEWAY_PRIVATE_TOKEN)
		[[ -z "${value}" ]] || export CXBA_GATEWAY_PRIVATE_TOKEN="${value}"
	fi
	if [[ -z "${CXBA_SPRING_MCP_TOKEN:-}" ]]; then
		value=$(process_env_value "${spring_pid}" CXBA_MCP_CONNECTION_TOKEN)
		[[ -z "${value}" ]] || export CXBA_SPRING_MCP_TOKEN="${value}"
	fi
}

prepare_environment() {
	load_private_env_file
	inherit_spring_tokens

	: "${CXBA_LOCAL_MODEL:=qwen3.6-27b}"
	: "${CXBA_LOCAL_MODEL_BASE_URL:=https://llm-gz2xserodo4c3kj6.cn-beijing.maas.aliyuncs.com/compatible-mode/v1}"
	: "${CXBA_CASE_STORAGE_ROOT:=$(cd "${PROJECT_DIR}/../cxba-workbench" && pwd)/data/cxba}"
	: "${CXBA_KNOWLEDGE_VAULT_ROOT:=$(cd "${PROJECT_DIR}/../cxba-workbench" && pwd)/knowledge-vault}"
	: "${CXBA_SPRING_MCP_URL:=http://127.0.0.1:${SPRING_PORT}/mcp}"
	export CXBA_LOCAL_MODEL CXBA_LOCAL_MODEL_BASE_URL CXBA_BAILIAN_API_KEY CXBA_SILICONFLOW_API_KEY CXBA_CASE_STORAGE_ROOT CXBA_KNOWLEDGE_VAULT_ROOT CXBA_SPRING_MCP_URL

	local missing=()
	[[ -n "${CXBA_GATEWAY_PRIVATE_TOKEN:-}" ]] || missing+=(CXBA_GATEWAY_PRIVATE_TOKEN)
	[[ -n "${CXBA_SPRING_MCP_TOKEN:-}" ]] || missing+=(CXBA_SPRING_MCP_TOKEN)
	if is_bailian_model_endpoint; then
		[[ -n "${CXBA_BAILIAN_API_KEY:-}" ]] || missing+=(CXBA_BAILIAN_API_KEY)
	elif [[ "${CXBA_LOCAL_MODEL_BASE_URL%/}" == "https://api.siliconflow.cn/v1" ]]; then
		[[ -n "${CXBA_SILICONFLOW_API_KEY:-}" ]] || missing+=(CXBA_SILICONFLOW_API_KEY)
	fi
	if (( ${#missing[@]} > 0 )); then
		echo "Missing required secret environment: ${missing[*]}" >&2
		echo "Export them first or place them in ${ENV_FILE} with file mode 600." >&2
		return 1
	fi
	export CXBA_GATEWAY_PRIVATE_TOKEN CXBA_SPRING_MCP_TOKEN
}

is_bailian_model_endpoint() {
	local base=${CXBA_LOCAL_MODEL_BASE_URL%/}
	[[ "${base}" == "https://dashscope.aliyuncs.com/compatible-mode/v1" || "${base}" == https://*.maas.aliyuncs.com/compatible-mode/v1 ]]
}

model_api_key() {
	if is_bailian_model_endpoint; then
		printf '%s' "${CXBA_BAILIAN_API_KEY:-}"
		return
	fi
	if [[ "${CXBA_LOCAL_MODEL_BASE_URL%/}" == "https://api.siliconflow.cn/v1" ]]; then
		printf '%s' "${CXBA_SILICONFLOW_API_KEY:-}"
		return
	fi
	printf '%s' "${CXBA_BAILIAN_API_KEY:-${CXBA_SILICONFLOW_API_KEY:-}}"
}

check_model() {
	local response api_key
	local -a curl_args=(--connect-timeout 3 --max-time 12 -fsS)
	api_key=$(model_api_key)
	if [[ -n "${api_key}" ]]; then
		curl_args+=(-H "Authorization: Bearer ${api_key}")
	fi
	response=$(curl "${curl_args[@]}" "${CXBA_LOCAL_MODEL_BASE_URL%/}/models") || {
		echo "Model service unavailable: ${CXBA_LOCAL_MODEL_BASE_URL}" >&2
		return 1
	}
	if [[ "${response}" != *"${CXBA_LOCAL_MODEL}"* ]]; then
		echo "Configured model not found: ${CXBA_LOCAL_MODEL}" >&2
		return 1
	fi
}

sync_managed_profile_skills() {
	local source_skills="${PROFILE_SOURCE}/skills"
	local target_skills="${PROFILE_HOME}/skills"
	[[ -d "${source_skills}" ]] || return 0
	mkdir -p "${target_skills}"
	cp -R "${source_skills}/." "${target_skills}/"
}

start_gateway() {
	local pid gateway_command
	pid=$(listener_pid)
	if [[ -n "${pid}" ]]; then
		if is_cxba_gateway_process "${pid}"; then
			echo "Hermes Gateway is already running (pid=${pid}, port=${PORT})."
			return 0
		fi
		echo "Port ${PORT} is occupied by another process (pid=${pid}); refusing to start." >&2
		return 1
	fi

	[[ -x "${HERMES_BIN}" ]] || { echo "Hermes executable not found: ${HERMES_BIN}" >&2; return 1; }
	command -v tmux >/dev/null 2>&1 || { echo "tmux is required to run Hermes Gateway persistently." >&2; return 1; }
	prepare_environment
	check_model
	sync_managed_profile_skills
	mkdir -p "$(dirname "${LOG_FILE}")" "$(dirname "${PID_FILE}")"

	if tmux has-session -t "${TMUX_SESSION}" 2>/dev/null; then
		echo "Stale tmux session exists without a listener: ${TMUX_SESSION}" >&2
		echo "Stop it explicitly before starting Hermes Gateway." >&2
		return 1
	fi
	# tmux only forwards a small allow-list from the calling shell. Keep values
	# loaded from the private file, then explicitly carry the non-secret defaults
	# prepared above so older config files also receive newly required roots.
	gateway_command="set -a; source $(quote_shell "${ENV_FILE}"); set +a; export CXBA_LOCAL_MODEL=$(quote_shell "${CXBA_LOCAL_MODEL}") CXBA_LOCAL_MODEL_BASE_URL=$(quote_shell "${CXBA_LOCAL_MODEL_BASE_URL}") CXBA_CASE_STORAGE_ROOT=$(quote_shell "${CXBA_CASE_STORAGE_ROOT}") CXBA_KNOWLEDGE_VAULT_ROOT=$(quote_shell "${CXBA_KNOWLEDGE_VAULT_ROOT}") CXBA_SPRING_MCP_URL=$(quote_shell "${CXBA_SPRING_MCP_URL}"); exec $(quote_shell "${HERMES_BIN}") -p $(quote_shell "${PROFILE}") serve --host $(quote_shell "${HOST}") --port $(quote_shell "${PORT}") --isolated --skip-build >> $(quote_shell "${LOG_FILE}") 2>&1"
	tmux new-session -d -s "${TMUX_SESSION}" -c "${PROJECT_DIR}" "/bin/bash -lc $(quote_shell "${gateway_command}")"

	local attempt
	for attempt in {1..30}; do
		pid=$(listener_pid)
		if [[ -n "${pid}" ]]; then
			echo "${pid}" > "${PID_FILE}"
			echo "Hermes Gateway started (pid=${pid}, port=${PORT})."
			return 0
		fi
		if ! tmux has-session -t "${TMUX_SESSION}" 2>/dev/null; then
			echo "Hermes Gateway exited during startup. Recent log:" >&2
			tail -n 30 "${LOG_FILE}" >&2 || true
			return 1
		fi
		sleep 1
	done

	echo "Hermes Gateway did not listen on port ${PORT} within 30 seconds." >&2
	return 1
}

stop_gateway() {
	local pid
	pid=$(listener_pid)
	if [[ -z "${pid}" ]]; then
		rm -f "${PID_FILE}"
		echo "Hermes Gateway is not running."
		return 0
	fi
	if ! is_cxba_gateway_process "${pid}"; then
		echo "Port ${PORT} is occupied by another process (pid=${pid}); refusing to stop it." >&2
		return 1
	fi

	inherit_gateway_environment "${pid}"
	kill "${pid}"
	local attempt
	for attempt in {1..20}; do
		if ! kill -0 "${pid}" 2>/dev/null; then
			tmux kill-session -t "${TMUX_SESSION}" 2>/dev/null || true
			rm -f "${PID_FILE}"
			echo "Hermes Gateway stopped (pid=${pid})."
			return 0
		fi
		sleep 0.5
	done
	echo "Hermes Gateway did not stop after SIGTERM; no force kill was performed." >&2
	return 1
}

show_status() {
	local pid
	pid=$(listener_pid)
	if [[ -z "${pid}" ]]; then
		echo "Hermes Gateway: stopped"
		return 1
	fi
	if ! is_cxba_gateway_process "${pid}"; then
		echo "Hermes Gateway: unknown process on port ${PORT} (pid=${pid})"
		return 1
	fi
	echo "Hermes Gateway: running (pid=${pid}, port=${PORT}, profile=${PROFILE})"
	inherit_gateway_environment "${pid}"
	if check_model; then
		echo "Model service: reachable (${CXBA_LOCAL_MODEL} at ${CXBA_LOCAL_MODEL_BASE_URL})"
	else
		echo "Model service: unavailable"
		return 1
	fi
}

usage() {
	echo "Usage: $0 {init-config|start|stop|restart|status|logs}"
}

case "${1:-}" in
	init-config)
		init_config
		;;
	start)
		start_gateway
		;;
	stop)
		stop_gateway
		;;
	restart)
		current_pid=$(listener_pid)
		[[ -z "${current_pid}" ]] || inherit_gateway_environment "${current_pid}"
		stop_gateway
		start_gateway
		;;
	status)
		show_status
		;;
	logs)
		tail -f "${LOG_FILE}"
		;;
	*)
		usage >&2
		exit 2
		;;
esac
