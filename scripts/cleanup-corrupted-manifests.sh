#!/bin/bash
# Script to clean up corrupted Docker manifests from GitHub Container Registry
# This script removes packages with uppercase naming that conflict with lowercase versions
#
# Prerequisites:
# 1. GitHub CLI (gh) installed and authenticated
# 2. Personal Access Token with read:packages, write:packages, delete:packages scopes
#
# Usage:
#   gh auth login --scopes "read:packages,write:packages,delete:packages"
#   ./cleanup-corrupted-manifests.sh [--dry-run]

set -e

ORG="penguintechinc"
DRY_RUN=false

# Parse arguments
if [[ "$1" == "--dry-run" ]]; then
  DRY_RUN=true
  echo "🔍 DRY RUN MODE - No changes will be made"
  echo ""
fi

echo "================================================"
echo "GitHub Container Registry Cleanup Tool"
echo "Organization: $ORG"
echo "================================================"
echo ""

# Function to list package versions
list_package_versions() {
  local package_name=$1
  echo "📦 Checking package: $package_name"

  # Try to get package versions
  versions=$(gh api "/orgs/$ORG/packages/container/$package_name/versions" --jq '.[] | {id: .id, name: .name, tags: .metadata.container.tags}' 2>&1)

  if [[ $? -ne 0 ]]; then
    if echo "$versions" | grep -q "404"; then
      echo "   ⚠️  Package not found: $package_name"
      return 1
    else
      echo "   ❌ Error accessing package: $package_name"
      echo "$versions"
      return 1
    fi
  fi

  echo "$versions"
  return 0
}

# Function to delete package version
delete_version() {
  local package_name=$1
  local version_id=$2
  local version_tags=$3

  if [[ "$DRY_RUN" == true ]]; then
    echo "   [DRY RUN] Would delete version $version_id (tags: $version_tags)"
  else
    echo "   🗑️  Deleting version $version_id (tags: $version_tags)..."
    gh api -X DELETE "/orgs/$ORG/packages/container/$package_name/versions/$version_id"
    if [[ $? -eq 0 ]]; then
      echo "   ✅ Successfully deleted version $version_id"
    else
      echo "   ❌ Failed to delete version $version_id"
    fi
  fi
}

# Function to delete all versions of a package (for complete cleanup)
delete_all_versions() {
  local package_name=$1

  echo ""
  echo "🗑️  Deleting ALL versions of package: $package_name"

  # Get all version IDs
  version_ids=$(gh api "/orgs/$ORG/packages/container/$package_name/versions" --jq '.[].id' 2>&1)

  if [[ $? -ne 0 ]]; then
    echo "   ❌ Error getting versions for $package_name"
    return 1
  fi

  if [[ -z "$version_ids" ]]; then
    echo "   ℹ️  No versions found for $package_name"
    return 0
  fi

  # Delete each version
  while IFS= read -r version_id; do
    if [[ "$DRY_RUN" == true ]]; then
      echo "   [DRY RUN] Would delete version ID: $version_id"
    else
      echo "   🗑️  Deleting version ID: $version_id..."
      gh api -X DELETE "/orgs/$ORG/packages/container/$package_name/versions/$version_id" 2>&1
      if [[ $? -eq 0 ]]; then
        echo "   ✅ Deleted version $version_id"
      else
        echo "   ⚠️  Failed to delete version $version_id (may already be deleted)"
      fi
    fi
  done <<< "$version_ids"
}

# Check for packages with uppercase naming (these are corrupted)
echo "🔍 Step 1: Checking for corrupted packages with uppercase naming"
echo ""

# List of potential corrupted package names (with uppercase)
CORRUPTED_PACKAGES=(
  "Elder"
  "Elder-api"
  "Elder-web"
  "Elder-grpc"
  "Elder-envoy"
  "Elder-scanner"
  "Elder-worker"
)

for pkg in "${CORRUPTED_PACKAGES[@]}"; do
  if list_package_versions "$pkg" > /dev/null 2>&1; then
    echo "   ⚠️  Found corrupted package: $pkg"
    delete_all_versions "$pkg"
  fi
done

echo ""
echo "🔍 Step 2: Checking lowercase packages for untagged/corrupted versions"
echo ""

# List of correct lowercase package names
CORRECT_PACKAGES=(
  "elder"
  "elder-api"
  "elder-web"
  "elder-grpc"
  "elder-envoy"
  "elder-scanner"
  "elder-worker"
)

for pkg in "${CORRECT_PACKAGES[@]}"; do
  versions_output=$(list_package_versions "$pkg" 2>&1)

  if [[ $? -ne 0 ]]; then
    continue
  fi

  # Find untagged versions (these are often corrupted manifests)
  echo "$versions_output" | jq -r 'select(.tags == [] or .tags == null) | "UNTAGGED:\(.id)"' 2>/dev/null | while read -r line; do
    if [[ $line == UNTAGGED:* ]]; then
      version_id="${line#UNTAGGED:}"
      echo "   ⚠️  Found untagged version in $pkg: $version_id"
      delete_version "$pkg" "$version_id" "none"
    fi
  done
done

echo ""
echo "================================================"
echo "✅ Cleanup Complete!"
echo "================================================"
echo ""

if [[ "$DRY_RUN" == true ]]; then
  echo "This was a dry run. To actually delete packages, run:"
  echo "  $0"
else
  echo "Corrupted manifests have been removed."
  echo ""
  echo "Next steps:"
  echo "1. Push new code with lowercase image names"
  echo "2. Trigger a new build via GitHub Actions"
  echo "3. Verify images with:"
  echo "   docker pull --platform linux/amd64 ghcr.io/$ORG/elder-web:latest"
  echo "   docker pull --platform linux/arm64 ghcr.io/$ORG/elder-web:latest"
fi
