#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_DIR=$(cd "${SCRIPT_DIR}/.." && pwd)
GATEWAY_SCRIPT="${PROJECT_DIR}/scripts/cxba-gateway.sh"
ENV_FILE=${CXBA_GATEWAY_ENV_FILE:-"${PROJECT_DIR}/config/hermes-gateway.env"}
PROFILE=${CXBA_HERMES_PROFILE:-cxba-production}
HERMES_BIN=${CXBA_HERMES_BIN:-"${PROJECT_DIR}/.venv/bin/hermes"}
SSH_USER=${CXBA_REMOTE_SSH_USER:-root}
IDENTITY_FILE=${CXBA_REMOTE_IDENTITY_FILE:-"${HOME}/.ssh/cxba_remote_llm_ed25519"}
LOCAL_PORT=${CXBA_REMOTE_LOCAL_PORT:-18080}
LAUNCH_LABEL=com.spdb.cxba.remote-llm-tunnel
LAUNCH_AGENT="${HOME}/Library/LaunchAgents/${LAUNCH_LABEL}.plist"
LOG_FILE="${HOME}/Library/Logs/cxba-remote-llm-tunnel.log"

usage() {
	cat <<'EOF'
Usage:
  cxba-remote-model.sh bootstrap <ssh-host> <ssh-port>
  cxba-remote-model.sh switch <ssh-host> <ssh-port> [remote-api-port] [model-id]
  cxba-remote-model.sh status

bootstrap prompts once for the new server password and installs the dedicated
public key. switch never accepts or stores a password.
EOF
}

require_command() {
	command -v "$1" >/dev/null 2>&1 || {
		echo "Required command not found: $1" >&2
		exit 1
	}
}

validate_host() {
	[[ "$1" =~ ^[A-Za-z0-9.-]+$ ]] || {
		echo "Invalid SSH host: $1" >&2
		exit 2
	}
}

validate_port() {
	[[ "$1" =~ ^[0-9]+$ ]] && (( "$1" >= 1 && "$1" <= 65535 )) || {
		echo "Invalid port: $1" >&2
		exit 2
	}
}

ensure_identity() {
	mkdir -p "$(dirname "${IDENTITY_FILE}")"
	if [[ ! -f "${IDENTITY_FILE}" ]]; then
		ssh-keygen -q -t ed25519 -f "${IDENTITY_FILE}" -N '' -C cxba-remote-llm
	fi
	chmod 600 "${IDENTITY_FILE}"
}

bootstrap_key() {
	local host=$1 port=$2 public_key
	validate_host "${host}"
	validate_port "${port}"
	ensure_identity
	public_key=$(<"${IDENTITY_FILE}.pub")
	echo "Enter the SSH password once to authorize the CXBA tunnel key."
	printf '%s\n' "${public_key}" | ssh \
		-o StrictHostKeyChecking=accept-new \
		-p "${port}" "${SSH_USER}@${host}" \
		'umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; key=$(cat); grep -qxF "$key" ~/.ssh/authorized_keys || printf "%s\n" "$key" >> ~/.ssh/authorized_keys'
	ssh -i "${IDENTITY_FILE}" -o BatchMode=yes \
		-o StrictHostKeyChecking=accept-new -p "${port}" \
		"${SSH_USER}@${host}" true
	echo "SSH key authorization verified for ${SSH_USER}@${host}:${port}."
}

remote_models() {
	local host=$1 ssh_port=$2 remote_api_port=$3
	ssh -i "${IDENTITY_FILE}" -o BatchMode=yes \
		-o StrictHostKeyChecking=accept-new -p "${ssh_port}" \
		"${SSH_USER}@${host}" \
		"curl -fsS --max-time 8 http://127.0.0.1:${remote_api_port}/v1/models"
}

select_model() {
	local payload=$1 requested=${2:-}
	PAYLOAD="${payload}" REQUESTED_MODEL="${requested}" python3 - <<'PY'
import json, os, sys

data = json.loads(os.environ["PAYLOAD"]).get("data") or []
ids = [str(item.get("id") or "").strip() for item in data]
ids = [item for item in ids if item]
requested = os.environ.get("REQUESTED_MODEL", "").strip()
if requested:
    if requested not in ids:
        raise SystemExit(f"Requested model not found. Available: {', '.join(ids)}")
    print(requested)
elif len(ids) == 1:
    print(ids[0])
else:
    raise SystemExit(f"Specify model-id. Available: {', '.join(ids) or '(none)'}")
PY
}

model_context_length() {
	local payload=$1 model=$2
	PAYLOAD="${payload}" SELECTED_MODEL="${model}" python3 - <<'PY'
import json, os

data = json.loads(os.environ["PAYLOAD"]).get("data") or []
selected = os.environ["SELECTED_MODEL"]
for item in data:
    if str(item.get("id") or "") == selected:
        value = int(item.get("max_model_len") or 0)
        print(value if value > 0 else 131072)
        break
else:
    raise SystemExit(f"Selected model disappeared: {selected}")
PY
}

xml_escape() {
	python3 -c 'import html,sys; print(html.escape(sys.argv[1], quote=True))' "$1"
}

write_launch_agent() {
	local host=$1 ssh_port=$2 remote_api_port=$3
	local escaped_host escaped_identity escaped_log
	escaped_host=$(xml_escape "${SSH_USER}@${host}")
	escaped_identity=$(xml_escape "${IDENTITY_FILE}")
	escaped_log=$(xml_escape "${LOG_FILE}")
	mkdir -p "$(dirname "${LAUNCH_AGENT}")" "$(dirname "${LOG_FILE}")"
	cat > "${LAUNCH_AGENT}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>${LAUNCH_LABEL}</string>
<key>ProgramArguments</key><array>
<string>/usr/bin/ssh</string><string>-NT</string>
<string>-i</string><string>${escaped_identity}</string>
<string>-L</string><string>127.0.0.1:${LOCAL_PORT}:127.0.0.1:${remote_api_port}</string>
<string>-o</string><string>BatchMode=yes</string>
<string>-o</string><string>ExitOnForwardFailure=yes</string>
<string>-o</string><string>ServerAliveInterval=15</string>
<string>-o</string><string>ServerAliveCountMax=3</string>
<string>-o</string><string>StrictHostKeyChecking=yes</string>
<string>-p</string><string>${ssh_port}</string>
<string>${escaped_host}</string>
</array>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>ThrottleInterval</key><integer>10</integer>
<key>StandardOutPath</key><string>${escaped_log}</string>
<key>StandardErrorPath</key><string>${escaped_log}</string>
</dict></plist>
EOF
	plutil -lint "${LAUNCH_AGENT}" >/dev/null
	launchctl bootout "gui/$(id -u)/${LAUNCH_LABEL}" >/dev/null 2>&1 || true
	launchctl bootstrap "gui/$(id -u)" "${LAUNCH_AGENT}"
}

set_env_export() {
	local key=$1 value=$2
	KEY="${key}" VALUE="${value}" ENV_FILE_PATH="${ENV_FILE}" python3 - <<'PY'
import os, shlex
from pathlib import Path

path = Path(os.environ["ENV_FILE_PATH"])
key = os.environ["KEY"]
value = os.environ["VALUE"]
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
prefix = f"export {key}="
replacement = prefix + shlex.quote(value)
for index, line in enumerate(lines):
    if line.startswith(prefix):
        lines[index] = replacement
        break
else:
    lines.append(replacement)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
path.chmod(0o600)
PY
}

wait_local_model() {
	local model=$1 attempt response
	for attempt in {1..24}; do
		response=$(curl -fsS --max-time 3 "http://127.0.0.1:${LOCAL_PORT}/v1/models" 2>/dev/null || true)
		if [[ "${response}" == *"${model}"* ]]; then
			return 0
		fi
		sleep 1
	done
	echo "SSH tunnel did not expose model ${model} on local port ${LOCAL_PORT}." >&2
	return 1
}

switch_remote() {
	local host=$1 ssh_port=$2 remote_api_port=${3:-8080} requested_model=${4:-}
	local payload model context_length
	validate_host "${host}"
	validate_port "${ssh_port}"
	validate_port "${remote_api_port}"
	ensure_identity
	payload=$(remote_models "${host}" "${ssh_port}" "${remote_api_port}")
	model=$(select_model "${payload}" "${requested_model}")
	context_length=$(model_context_length "${payload}" "${model}")
	write_launch_agent "${host}" "${ssh_port}" "${remote_api_port}"
	wait_local_model "${model}"
	set_env_export CXBA_LOCAL_MODEL "${model}"
	set_env_export CXBA_LOCAL_MODEL_BASE_URL "http://127.0.0.1:${LOCAL_PORT}/v1"
	set_env_export CXBA_LOCAL_MODEL_CONTEXT_LENGTH "${context_length}"
	"${GATEWAY_SCRIPT}" stop
	"${HERMES_BIN}" profile update "${PROFILE}" --force-config -y
	"${GATEWAY_SCRIPT}" start
	"${GATEWAY_SCRIPT}" status
	echo "Remote model switch complete: ${model} (${context_length} context) via ${SSH_USER}@${host}:${ssh_port}."
}

show_status() {
	local model="" base_url="" context_length=""
	if [[ -f "${ENV_FILE}" ]]; then
		model=$(sed -n 's/^export CXBA_LOCAL_MODEL=//p' "${ENV_FILE}" | head -1)
		base_url=$(sed -n 's/^export CXBA_LOCAL_MODEL_BASE_URL=//p' "${ENV_FILE}" | head -1)
		context_length=$(sed -n 's/^export CXBA_LOCAL_MODEL_CONTEXT_LENGTH=//p' "${ENV_FILE}" | head -1)
	fi
	echo "Model: ${model:-not configured}"
	echo "Endpoint: ${base_url:-not configured}"
	echo "Context length: ${context_length:-not configured}"
	launchctl print "gui/$(id -u)/${LAUNCH_LABEL}" 2>/dev/null | awk '
		$1 == "state" && !seen_state { print "Tunnel state: " $3; seen_state=1 }
		$1 == "pid" && !seen_pid { print "Tunnel pid: " $3; seen_pid=1 }
		seen_state && seen_pid { exit }
	' || true
	"${GATEWAY_SCRIPT}" status
}

require_command ssh
require_command ssh-keygen
require_command curl
require_command python3

case "${1:-}" in
	bootstrap)
		[[ $# -eq 3 ]] || { usage >&2; exit 2; }
		bootstrap_key "$2" "$3"
		;;
	switch)
		[[ $# -ge 3 && $# -le 5 ]] || { usage >&2; exit 2; }
		switch_remote "$2" "$3" "${4:-8080}" "${5:-}"
		;;
	status)
		[[ $# -eq 1 ]] || { usage >&2; exit 2; }
		show_status
		;;
	*)
		usage >&2
		exit 2
		;;
esac
