#!/usr/bin/env bash
# Codespaces post-create — runs once when the container is built.
#
# Local Windows install does this in start.bat (kill existing → pull
# → activate venv → run). Codespaces version is simpler: no Qt, no
# venv (system python is fine), no kill (each container is fresh).

set -euo pipefail

echo "→ Installing Python deps from sentinel/requirements.txt"
# PySide6 is in requirements.txt. It installs but won't actually run
# without a display server — that's fine because we're using the
# Codespace for code/tests/PRs, not running the desktop app here.
pip install --quiet --user -r sentinel/requirements.txt

echo "→ Installing Claude Code globally via npm"
# Lets Peter run `claude` directly in the Codespace terminal so the
# Claude Code workflow lives entirely in the cloud. The npm package
# bundles the CLI; no separate auth setup beyond first login.
npm install -g @anthropic-ai/claude-code 2>/dev/null || {
    echo "  (claude-code install failed — non-fatal, can install later)"
}

echo "→ Installing google-genai for image generation tests"
# Already in requirements.txt but pin here for clarity — this is what
# sentinel/expression/generator.py uses for Imagen / gemini-flash-image.
pip install --quiet --user google-genai

echo "→ Configuring git (just safe defaults; user can override)"
git config --global pull.ff only
git config --global init.defaultBranch main

echo "✓ post-create done. Open a terminal and run \`claude\` to start."
