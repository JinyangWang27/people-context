#!/usr/bin/env bash
# Build the native-UV people-context MCPB Desktop bundle.
#
# Uses an exact, reviewed MCPB CLI version (never a floating latest) to validate
# the manifest, pack the bundle, and print the archive contents for inspection.
#
# Usage: mcpb/build.sh [OUTPUT_DIR]   (default: mcpb/dist)
set -euo pipefail

# Exact reviewed MCPB CLI release. Bumping it is a reviewed change here and in
# .github/workflows/mcpb-validate.yml and .github/workflows/release.yml.
MCPB_CLI_VERSION="2.1.2"

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out_dir="${1:-"${here}/dist"}"
mkdir -p "${out_dir}"
output="${out_dir}/people-context.mcpb"

mcpb() {
  npx --yes "@anthropic-ai/mcpb@${MCPB_CLI_VERSION}" "$@"
}

echo "==> Validating manifest with @anthropic-ai/mcpb@${MCPB_CLI_VERSION}"
mcpb validate "${here}/manifest.json"

echo "==> Packing ${output}"
mcpb pack "${here}" "${output}"

echo "==> Archive contents (inspect before attaching to a release)"
unzip -l "${output}"
