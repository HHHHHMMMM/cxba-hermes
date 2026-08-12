#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_DIR=$(cd "${SCRIPT_DIR}/.." && pwd)
PROFILE=${CXBA_HERMES_PROFILE:-cxba-production}
ENV_FILE=${CXBA_GATEWAY_ENV_FILE:-"${PROJECT_DIR}/config/hermes-gateway.env"}
SOURCE_CONFIG=${CXBA_HERMES_SOURCE_CONFIG:-"${PROJECT_DIR}/profiles/${PROFILE}/config.yaml"}
RUNTIME_CONFIG=${CXBA_HERMES_RUNTIME_CONFIG:-"${HOME}/.hermes/profiles/${PROFILE}/config.yaml"}
GATEWAY_SCRIPT=${CXBA_HERMES_GATEWAY_SCRIPT:-"${SCRIPT_DIR}/cxba-gateway.sh"}
PYTHON_BIN=${CXBA_MODEL_SYNC_PYTHON:-"${PROJECT_DIR}/.venv/bin/python"}

usage() {
	echo "Usage: $0 {sync|status}"
	echo "  sync    Detect the configured model from /v1/models, update Hermes, and restart Gateway."
	echo "  status  Compare the served model with the current Hermes configuration without changing anything."
}

require_file() {
	[[ -f "$1" ]] || { echo "Required file not found: $1" >&2; exit 1; }
}

load_endpoint() {
	require_file "${ENV_FILE}"
	# shellcheck disable=SC1090
	set -a
	. "${ENV_FILE}"
	set +a
	: "${CXBA_LOCAL_MODEL_BASE_URL:?CXBA_LOCAL_MODEL_BASE_URL is required in ${ENV_FILE}}"
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

detect_model() {
	local response parsed api_key
	local -a curl_args=(--connect-timeout 3 --max-time 12 -fsS)
	api_key=$(model_api_key)
	if [[ -n "${api_key}" ]]; then
		curl_args+=(-H "Authorization: Bearer ${api_key}")
	fi
	response=$(curl "${curl_args[@]}" "${CXBA_LOCAL_MODEL_BASE_URL%/}/models") || {
		echo "Model service unavailable: ${CXBA_LOCAL_MODEL_BASE_URL}" >&2
		exit 1
	}
	parsed=$(printf '%s' "${response}" | CXBA_LOCAL_MODEL="${CXBA_LOCAL_MODEL:-}" CXBA_LOCAL_MODEL_CONTEXT="${CXBA_LOCAL_MODEL_CONTEXT:-}" "${PYTHON_BIN}" -c '
import json, sys
import os

payload = json.load(sys.stdin)
models = payload.get("data")
if not isinstance(models, list) or not models:
    raise SystemExit("Expected a non-empty model list from /v1/models")

rows = [model for model in models if isinstance(model, dict)]
target = os.environ.get("CXBA_LOCAL_MODEL", "").strip()
if target:
    matches = [row for row in rows if str(row.get("id") or "").strip() == target]
    if not matches:
        available = ", ".join(
            str(row.get("id") or "").strip()
            for row in rows[:20]
            if str(row.get("id") or "").strip()
        )
        suffix = f"; first models: {available}" if available else ""
        raise SystemExit(f"Configured model not found in /v1/models: {target}{suffix}")
    row = matches[0]
elif len(rows) == 1:
    row = rows[0]
else:
    raise SystemExit("Set CXBA_LOCAL_MODEL when /v1/models returns multiple models")

model_id = str(row.get("id") or "").strip()
meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}

def positive_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if parsed > 0 else None
    return None

context = None
for value in (
    meta.get("n_ctx"),
    meta.get("context_length"),
    meta.get("context_window"),
    meta.get("max_context_length"),
    meta.get("max_model_len"),
    row.get("context_length"),
    row.get("context_window"),
    row.get("max_context_length"),
    row.get("max_model_len"),
):
    context = positive_int(value)
    if context is not None:
        break

if not model_id:
	raise SystemExit("Loaded model has no id")
if context is None:
    configured_context = positive_int(os.environ.get("CXBA_LOCAL_MODEL_CONTEXT"))
    if configured_context is not None:
        context = configured_context
    elif "qwen3.6-27b" in model_id.lower():
        context = 262144
    else:
        context = 65536
print(model_id + "\t" + str(context))
')
	IFS=$'\t' read -r DETECTED_MODEL DETECTED_CONTEXT <<< "${parsed}"
	if (( DETECTED_CONTEXT < 64000 )); then
		echo "Hermes requires at least 64000 context tokens; backend reports ${DETECTED_CONTEXT}." >&2
		exit 1
	fi
}

configured_model() {
	sed -n 's/^export CXBA_LOCAL_MODEL=//p' "${ENV_FILE}" | tail -1
}

configured_context() {
	sed -n 's/^[[:space:]]*context_length:[[:space:]]*//p' "${RUNTIME_CONFIG}" | head -1
}

show_status() {
	local current_model current_context result=0
	current_model=$(configured_model)
	current_context=$(configured_context)
	echo "Backend model:  ${DETECTED_MODEL}"
	echo "Backend context: ${DETECTED_CONTEXT}"
	echo "Hermes model:   ${current_model:-<missing>}"
	echo "Hermes context: ${current_context:-<missing>}"
	if [[ "${current_model}" != "${DETECTED_MODEL}" || "${current_context}" != "${DETECTED_CONTEXT}" ]]; then
		echo "Status: out of sync"
		result=1
	else
		echo "Status: synchronized"
	fi
	return "${result}"
}

update_config() {
	require_file "${SOURCE_CONFIG}"
	require_file "${RUNTIME_CONFIG}"
	"${PYTHON_BIN}" - "${ENV_FILE}" "${SOURCE_CONFIG}" "${RUNTIME_CONFIG}" \
		"${DETECTED_MODEL}" "${DETECTED_CONTEXT}" <<'PY'
import os
import re
import sys
from pathlib import Path

env_path, source_path, runtime_path, model_id, context = sys.argv[1:]

def replace(path_value, pattern, replacement, label):
    path = Path(path_value)
    original = path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, original, count=1, flags=re.MULTILINE)
    if count != 1:
        raise SystemExit(f"Expected exactly one {label} entry in {path}")
    if updated == original:
        return
    mode = path.stat().st_mode & 0o777
    temporary = path.with_name(path.name + ".tmp-model-sync")
    temporary.write_text(updated, encoding="utf-8")
    os.chmod(temporary, mode)
    os.replace(temporary, path)

replace(
    env_path,
    r"^export CXBA_LOCAL_MODEL=.*$",
    "export CXBA_LOCAL_MODEL=" + model_id,
    "CXBA_LOCAL_MODEL",
)
for config_path in (source_path, runtime_path):
    replace(
        config_path,
        r"^(\s*context_length:\s*).*$",
        r"\g<1>" + context,
        "context_length",
    )
PY
}

main() {
	local action=${1:-sync}
	[[ -x "${PYTHON_BIN}" ]] || { echo "Python executable not found: ${PYTHON_BIN}" >&2; exit 1; }
	load_endpoint
	detect_model
	case "${action}" in
		status)
			show_status
			;;
		sync)
			echo "Detected backend model: ${DETECTED_MODEL} (context=${DETECTED_CONTEXT})"
			update_config
			"${GATEWAY_SCRIPT}" restart
			"${GATEWAY_SCRIPT}" status
			echo "Hermes default model synchronized. Existing sessions retain their recorded model; create a new Session."
			;;
		*)
			usage >&2
			exit 2
			;;
	esac
}

main "$@"
