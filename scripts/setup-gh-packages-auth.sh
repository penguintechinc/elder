#!/bin/bash
# Helper script to set up GitHub CLI with package management permissions
#
# This will re-authenticate gh CLI with the necessary scopes to manage packages

set -euo pipefail

echo "================================================"
echo "GitHub CLI Package Management Setup"
echo "================================================"
echo ""
echo "This script will authenticate GitHub CLI with the following scopes:"
echo "  - read:packages   (read package metadata)"
echo "  - write:packages  (modify packages)"
echo "  - delete:packages (delete package versions)"
echo "  - repo            (access repositories)"
echo ""
echo "You'll be prompted to authenticate via web browser."
echo ""
read -p "Press Enter to continue..."

# Authenticate with required scopes
gh auth login \
  --scopes "read:packages,write:packages,delete:packages,repo,workflow" \
  --web

if [[ $? -eq 0 ]]; then
  echo ""
  echo "✅ Authentication successful!"
  echo ""
  echo "Verifying scopes..."
  gh auth status
  echo ""
  echo "You can now run the cleanup script:"
  echo "  ./scripts/cleanup-corrupted-manifests.sh --dry-run"
  echo "  ./scripts/cleanup-corrupted-manifests.sh"
else
  echo ""
  echo "❌ Authentication failed. Please try again."
  exit 1
fi
