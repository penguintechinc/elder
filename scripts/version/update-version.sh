#!/bin/bash

# Version Management Script for Project Template
# Format: vMajor.Minor.Patch.EpochTimestamp

set -e

VERSION_FILE=".version"
VERSION_MD="VERSION.md"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Print colored output
print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if version file exists
if [ ! -f "$VERSION_FILE" ]; then
    print_info "Creating initial version file..."
    echo "1.0.0.$(date +%s)" > "$VERSION_FILE"
fi

# Read current version
current_version=$(cat "$VERSION_FILE")
# 4th field (build epoch) intentionally discarded here — new_build below
# always recomputes a fresh epoch timestamp rather than reusing the old one.
IFS='.' read -r major minor patch _ <<< "$current_version"

print_info "Current version: v$current_version"

# Get current epoch timestamp
new_build=$(date +%s)

# Parse command line arguments
case "${1:-build}" in
    "major")
        major=$((major + 1))
        minor=0
        patch=0
        print_info "Incrementing MAJOR version"
        ;;
    "minor")
        minor=$((minor + 1))
        patch=0
        print_info "Incrementing MINOR version"
        ;;
    "patch")
        patch=$((patch + 1))
        print_info "Incrementing PATCH version"
        ;;
    "build"|"")
        print_info "Updating BUILD timestamp only"
        ;;
    [0-9]*)
        # Custom version provided (e.g., ./update-version.sh 2 1 0)
        if [ $# -ge 3 ]; then
            major=$1
            minor=$2
            patch=$3
            print_info "Setting custom version: $major.$minor.$patch"
        else
            print_error "Custom version requires 3 arguments: major minor patch"
            print_info "Usage: $0 <major> <minor> <patch>"
            exit 1
        fi
        ;;
    "help"|"-h"|"--help")
        echo "Version Management Script"
        echo ""
        echo "Usage:"
        echo "  $0                    # Update build timestamp only"
        echo "  $0 build              # Update build timestamp only"
        echo "  $0 patch              # Increment patch version"
        echo "  $0 minor              # Increment minor version"
        echo "  $0 major              # Increment major version"
        echo "  $0 <maj> <min> <pat>  # Set specific version"
        echo "  $0 help               # Show this help"
        echo ""
        echo "Version format: vMajor.Minor.Patch.EpochTimestamp"
        echo "Example: v1.2.3.1647891234"
        exit 0
        ;;
    *)
        print_error "Invalid argument: $1"
        print_info "Use '$0 help' for usage information"
        exit 1
        ;;
esac

# Construct new version
new_version="$major.$minor.$patch.$new_build"

# Backup current version
cp "$VERSION_FILE" "$VERSION_FILE.backup"

# Write new version
echo "$new_version" > "$VERSION_FILE"

print_success "Version updated: v$current_version → v$new_version"

# Update VERSION.md if it exists
if [ -f "$VERSION_MD" ]; then
    print_info "Updating $VERSION_MD..."

    # Add entry to version history
    timestamp=$(date -u +"%Y-%m-%d %H:%M:%S UTC")

    # Create temporary file with new entry
    {
        echo "# Version History"
        echo ""
        echo "## v$new_version - $timestamp"
        echo ""
        if [ "$1" = "major" ]; then
            echo "### Major Release"
            echo "- Breaking changes or major new features"
        elif [ "$1" = "minor" ]; then
            echo "### Minor Release"
            echo "- New features and improvements"
        elif [ "$1" = "patch" ]; then
            echo "### Patch Release"
            echo "- Bug fixes and minor improvements"
        else
            echo "### Build Update"
            echo "- Development build with timestamp update"
        fi
        echo ""

        # Add existing content if VERSION.md exists and has content
        if [ -s "$VERSION_MD" ] && [ "$(head -n1 "$VERSION_MD")" = "# Version History" ]; then
            tail -n +3 "$VERSION_MD"
        else
            # Create initial content
            echo "## Version Format"
            echo ""
            echo "Versions follow the format: \`vMajor.Minor.Patch.EpochTimestamp\`"
            echo ""
            echo "- **Major**: Breaking changes, API changes, removed features"
            echo "- **Minor**: New features and significant functionality additions"
            echo "- **Patch**: Bug fixes, security patches, minor improvements"
            echo "- **Build**: Unix epoch timestamp for automatic chronological ordering"
            echo ""
            echo "## Automated Versioning"
            echo ""
            echo "Use the version update script:"
            echo ""
            echo "\`\`\`bash"
            echo "# Update build timestamp only"
            echo "./scripts/version/update-version.sh"
            echo ""
            echo "# Increment patch version"
            echo "./scripts/version/update-version.sh patch"
            echo ""
            echo "# Increment minor version"
            echo "./scripts/version/update-version.sh minor"
            echo ""
            echo "# Increment major version"
            echo "./scripts/version/update-version.sh major"
            echo ""
            echo "# Set specific version"
            echo "./scripts/version/update-version.sh 2 1 0"
            echo "\`\`\`"
            echo ""
        fi
    } > "$VERSION_MD.tmp"

    mv "$VERSION_MD.tmp" "$VERSION_MD"
    print_success "Updated $VERSION_MD with new version entry"
fi

# Update any version constants in code files
update_version_in_file() {
    local file=$1
    local pattern=$2

    if [ -f "$file" ]; then
        if sed -i.bak "$pattern" "$file" 2>/dev/null; then
            rm -f "$file.bak"
            print_info "Updated version in $file"
        fi
    fi
}

# Update Go files
update_version_in_file "apps/api/main.go" "s/version := \".*\"/version := \"$new_version\"/g"

# Update Python files
update_version_in_file "apps/web/app.py" "s/__version__ = \".*\"/__version__ = \"$new_version\"/g"

# Update package.json files
if [ -f "package.json" ]; then
    if command -v jq >/dev/null 2>&1; then
        jq ".version = \"$major.$minor.$patch\"" package.json > package.json.tmp && mv package.json.tmp package.json
        print_info "Updated version in package.json"
    else
        print_warning "jq not found, skipping package.json update"
    fi
fi

if [ -f "web/package.json" ]; then
    if command -v jq >/dev/null 2>&1; then
        jq ".version = \"$major.$minor.$patch\"" web/package.json > web/package.json.tmp && mv web/package.json.tmp web/package.json
        print_info "Updated version in web/package.json"
    fi
fi

# Git integration
if [ -d ".git" ]; then
    print_info "Git repository detected"

    # Check if there are any changes to commit
    if [ -n "$(git status --porcelain)" ]; then
        print_warning "There are uncommitted changes in the repository"
        print_info "Version files have been updated but not committed"
        print_info "To commit the version update:"
        echo "  git add .version VERSION.md package.json web/package.json"
        echo "  git commit -m \"chore: bump version to v$new_version\""

        if [ "$1" != "build" ]; then
            echo "  git tag v$major.$minor.$patch"
            echo "  git push origin main --tags"
        fi
    else
        print_info "Repository is clean, version update only affects version files"
    fi
fi

# Docker image tagging information
print_info "Docker image tags:"
echo "  Development: project-template:v$new_version"
echo "  Release: project-template:v$major.$minor.$patch"
echo "  Latest: project-template:latest"

# Summary
print_success "Version management completed!"
echo ""
echo "New version: v$new_version"
echo "Semantic version: v$major.$minor.$patch"
echo "Build timestamp: $new_build ($(date -d @$new_build 2>/dev/null || date -r $new_build 2>/dev/null || echo "timestamp"))"
echo ""

# Cleanup backup on success
rm -f "$VERSION_FILE.backup"
