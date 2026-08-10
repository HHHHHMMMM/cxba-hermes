#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd "$script_dir/../.." && pwd)"
image_name="${1:-cxba-hermes-sandbox:local}"

docker build \
  --file "$script_dir/Dockerfile" \
  --tag "$image_name" \
  "$repository_root"

printf 'Built %s\n' "$image_name"
