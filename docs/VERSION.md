# Version History

## v3.0.1.1767630247 - 2026-01-05 16:24:07 UTC

### Patch Release
- Bug fixes and minor improvements

## v2.4.0.1764026838 - 2025-11-24 23:27:18 UTC

### Minor Release
- New features and improvements

## v2.0.1.1763587611 - 2025-11-19 21:26:51 UTC

### Patch Release
- Bug fixes and minor improvements

## v1.0.0.1737727200 - 2025-01-24 17:20:00 UTC

### Initial Release
- Complete project template with multi-language support
- Comprehensive license server integration
- Docker and Kubernetes deployment configurations
- GitHub Actions CI/CD pipeline
- Development tools and workflow setup

## Version Format

Versions follow the format: `vMajor.Minor.Patch.EpochTimestamp`

- **Major**: Breaking changes, API changes, removed features
- **Minor**: New features and significant functionality additions
- **Patch**: Bug fixes, security patches, minor improvements
- **Build**: Unix epoch timestamp for automatic chronological ordering

## Automated Versioning

Use the version update script:

```bash
# Update build timestamp only
./scripts/version/update-version.sh

# Increment patch version
./scripts/version/update-version.sh patch

# Increment minor version
./scripts/version/update-version.sh minor

# Increment major version
./scripts/version/update-version.sh major

# Set specific version
./scripts/version/update-version.sh 2 1 0
```

## Version Integration

Versions are automatically embedded in:

- Go applications (`-ldflags "-X main.version=..."`)
- Docker image tags
- API responses and health checks
- CI/CD pipeline artifacts
- Release documentation

## Release Process

### Development Builds
- Build number automatically updated with epoch timestamp
- Tagged as `project-name:v1.0.0.1737727200`
- Includes full version for traceability

### Production Releases
- Semantic versioning for public releases
- Tagged as `project-name:v1.0.0` and `project-name:latest`
- Git tags created for release versions
- Changelog automatically generated

### Breaking Changes (Major)
- API changes that break backward compatibility
- Removed features or endpoints
- Database schema changes requiring migration
- Configuration format changes

### New Features (Minor)
- New API endpoints or features
- Enhanced functionality
- New configuration options
- Performance improvements

### Bug Fixes (Patch)
- Security patches
- Bug fixes
- Minor documentation updates
- Dependency updates

## Version Validation

The version management system includes:

- Format validation (vMajor.Minor.Patch.EpochTimestamp)
- Automatic timestamp generation
- Git integration for tagging
- Docker image tagging
- CI/CD pipeline integration
- License server version reporting

## Rollback Support

For production deployments:

1. **Docker Image Rollback**: Use previous semantic version tag
2. **Database Rollback**: Maintain database migration compatibility
3. **Configuration Rollback**: Version-specific configuration management
4. **License Compatibility**: Ensure license server version compatibility

## Best Practices

### When to Update Versions

- **Build**: Every commit to main branch
- **Patch**: Bug fixes, security updates, minor improvements
- **Minor**: New features, API additions, significant enhancements
- **Major**: Breaking changes, API removals, major architectural changes

### Git Workflow

```bash
# For feature development
git checkout -b feature/new-feature
# ... make changes ...
./scripts/version/update-version.sh  # Update build number
git commit -am "feat: add new feature"

# For releases
git checkout main
git merge feature/new-feature
./scripts/version/update-version.sh minor  # New feature
git commit -am "chore: bump version to v1.1.0"
git tag v1.1.0
git push origin main --tags
```

### CI/CD Integration

The CI/CD pipeline automatically:

1. Validates version format
2. Builds Docker images with version tags
3. Runs tests with version validation
4. Deploys with proper version tracking
5. Creates releases for tagged versions

## Monitoring and Alerting

Version information is included in:

- Health check endpoints (`/health`)
- Metrics endpoints (`/metrics`)
- Application logs
- Error reports
- Performance monitoring
- License server keepalive reports

## Troubleshooting

### Common Issues

1. **Version Mismatch**: Ensure all services use compatible versions
2. **Build Number Conflicts**: Use epoch timestamps for uniqueness
3. **Git Tag Conflicts**: Clean up local tags before creating new ones
4. **Docker Tag Issues**: Verify registry permissions and tag format

### Recovery Procedures

```bash
# Restore from backup
cp .version.backup .version

# Reset to specific version
echo "1.0.0.$(date +%s)" > .version

# Validate version format
./scripts/version/update-version.sh help
```
