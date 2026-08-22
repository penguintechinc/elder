# Contributing to Elder

Thank you for your interest in contributing to Elder! This document provides guidelines and instructions for contributing.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [License Agreement](#license-agreement)
- [Getting Started](#getting-started)
- [Development Workflow](#development-workflow)
- [Code Standards](#code-standards)
- [Pull Request Process](#pull-request-process)
- [Reporting Issues](#reporting-issues)

## Code of Conduct

By participating in this project, you agree to:

- Be respectful and constructive
- Welcome newcomers and help them learn
- Focus on what is best for the community
- Show empathy towards other community members

## License Agreement

Elder is licensed under a **Limited AGPL v3** license. By contributing, you agree that:

1. Your contributions will be licensed under the same license
2. You have the right to submit the contribution
3. You understand this is a Penguin Technologies Group LLC project
4. Certain contributions may be restricted based on license terms

**Please review the [LICENSE.md](LICENSE.md) before contributing.**

## Getting Started

### Prerequisites

Before you begin, ensure you have:

- Python 3.13+
- Node.js 18+
- Docker & Docker Compose
- Git
- Basic knowledge of Flask and React

### Setup Development Environment

```bash
# 1. Fork the repository on GitHub

# 2. Clone your fork
git clone https://github.com/YOUR_USERNAME/elder.git
cd elder

# 3. Add upstream remote
git remote add upstream https://github.com/penguintechinc/elder.git

# 4. Setup development environment
make setup

# 5. Start development services
make dev
```

### Running Tests

```bash
# Run all tests
make test

# Run specific test suite
pytest tests/unit/
pytest tests/integration/

# Run with coverage
pytest --cov=apps --cov-report=html
```

## Development Workflow

### 1. Create a Branch

Always create a new branch for your work:

```bash
# Update your fork
git checkout main
git pull upstream main

# Create feature branch
git checkout -b feature/your-feature-name

# Or for bug fixes
git checkout -b fix/bug-description
```

**Branch Naming Convention:**
- `feature/feature-name` - New features
- `fix/bug-description` - Bug fixes
- `docs/update-description` - Documentation updates
- `refactor/component-name` - Code refactoring
- `test/test-description` - Test additions/updates

### 2. Make Changes

- Write clean, readable code
- Follow project code style
- Add/update tests for your changes
- Update documentation as needed
- Keep commits focused and atomic

### 3. Commit Changes

We use **Conventional Commits** format:

```
<type>(<scope>): <subject>

<body>

<footer>
```

**Types:**
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting)
- `refactor`: Code refactoring
- `test`: Test additions/updates
- `chore`: Build process or auxiliary tool changes

**Examples:**

```bash
# Feature
git commit -m "feat(api): add endpoint for bulk entity creation

- Adds POST /api/v1/entities/bulk endpoint
- Supports batch creation of up to 1000 entities
- Includes validation and error handling
"

# Bug fix
git commit -m "fix(connector): resolve AWS credential timeout issue

Increases connection timeout from 5s to 30s to handle
slow network conditions.

Fixes #123
"

# Documentation
git commit -m "docs(api): update authentication examples

- Adds Bearer token examples
- Documents SAML/OAuth2 setup
- Clarifies rate limiting
"
```

### 4. Push Changes

```bash
# Push to your fork
git push origin feature/your-feature-name
```

### 5. Create Pull Request

1. Go to https://github.com/penguintechinc/elder
2. Click "New Pull Request"
3. Select your branch
4. Fill out the PR template
5. Request review

## Code Standards

### Python Code Style

**Follow PEP 8:**

```python
# Good
def calculate_total_cost(
    items: List[Item],
    discount_rate: float = 0.0
) -> Decimal:
    """Calculate total cost with optional discount.

    Args:
        items: List of items to calculate
        discount_rate: Discount rate (0.0-1.0)

    Returns:
        Total cost after discount
    """
    total = sum(item.price for item in items)
    return total * (1 - discount_rate)
```

**Use Type Hints:**

```python
from typing import List, Optional, Dict

def process_entities(
    entities: List[Entity],
    options: Optional[Dict[str, Any]] = None
) -> ProcessResult:
    ...
```

**Write Docstrings:**

```python
def create_dependency(source_id: int, target_id: int) -> Dependency:
    """Create a dependency between two entities.

    Args:
        source_id: Source entity ID
        target_id: Target entity ID

    Returns:
        Created Dependency object

    Raises:
        ValueError: If entities don't exist
        PermissionError: If user lacks permissions
    """
    ...
```

### JavaScript/TypeScript Code Style

**Use TypeScript:**

```typescript
// Good
interface EntityProps {
  id: number;
  name: string;
  type: EntityType;
}

const EntityCard: React.FC<EntityProps> = ({ id, name, type }) => {
  return (
    <div className="entity-card">
      <h2>{name}</h2>
      <span>{type}</span>
    </div>
  );
};
```

**Use Functional Components:**

```typescript
// Good - Functional component with hooks
const MyComponent: React.FC<Props> = () => {
  const [state, setState] = useState<State>();

  useEffect(() => {
    // Effect logic
  }, []);

  return <div>...</div>;
};
```

### Testing Standards

**Write Tests for New Features:**

```python
# tests/unit/test_entities.py
import pytest
from apps.api.models import Entity

def test_create_entity(db_session):
    """Test entity creation."""
    entity = Entity(
        name="test-entity",
        entity_type="compute",
        organization_id=1
    )
    db_session.add(entity)
    db_session.commit()

    assert entity.id is not None
    assert entity.name == "test-entity"

def test_entity_validation(db_session):
    """Test entity validation."""
    with pytest.raises(ValueError):
        Entity(name="", entity_type="compute")
```

**Aim for > 80% Coverage:**

```bash
pytest --cov=apps --cov-report=term-missing
```

### Documentation Standards

**Update README when:**
- Adding new features
- Changing setup process
- Modifying dependencies

**Update API docs when:**
- Adding/modifying endpoints
- Changing request/response format
- Adding authentication methods

**Write Clear Comments:**

```python
# Good comment
# Calculate weighted average considering null values
weighted_avg = sum(v * w for v, w in values if v is not None) / total_weight

# Bad comment
# Loop through values
for v in values:
    ...
```

## Pull Request Process

### Before Submitting

- [ ] Code follows project style guidelines
- [ ] All tests pass (`make test`)
- [ ] New tests added for new features
- [ ] Documentation updated
- [ ] Commit messages follow conventions
- [ ] Branch is up-to-date with main

### PR Template

```markdown
## Description
Brief description of changes

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## Testing
How was this tested?

## Checklist
- [ ] Tests pass
- [ ] Code follows style guide
- [ ] Documentation updated
- [ ] No breaking changes (or documented)
```

### Review Process

1. **Automated Checks**: CI/CD runs tests and linting
2. **Code Review**: Maintainer reviews code
3. **Revisions**: Address review comments
4. **Approval**: Maintainer approves PR
5. **Merge**: Squash and merge to main

### After Merge

- Delete your feature branch
- Pull latest main
- Your contribution will be in the next release!

## Reporting Issues

### Bug Reports

**Use this template:**

```markdown
**Describe the bug**
Clear description of the bug

**To Reproduce**
Steps to reproduce:
1. Go to '...'
2. Click on '...'
3. See error

**Expected behavior**
What you expected to happen

**Screenshots**
If applicable

**Environment:**
- OS: [e.g. Ubuntu 22.04]
- Python: [e.g. 3.13]
- Docker: [e.g. 24.0.7]
- Elder version: [e.g. 0.1.0]

**Additional context**
Any other relevant information
```

### Feature Requests

**Use this template:**

```markdown
**Is your feature request related to a problem?**
Clear description of the problem

**Describe the solution you'd like**
What you want to happen

**Describe alternatives you've considered**
Alternative solutions

**Additional context**
Any other relevant information
```

## Development Tips

### Quick Commands

```bash
# Format code
black apps/ web/src/
```

# Run linting
```bash
flake8 apps/
cd web && npm run lint
```

# Type checking
```bash
mypy apps/
cd web && npm run type-check
```

# Watch mode for tests
```bash
pytest-watch
```

### Debugging

```python
# Add breakpoint
import pdb; pdb.set_trace()

# Or use debugpy (VS Code)
import debugpy
debugpy.listen(5678)
debugpy.wait_for_client()
```

### Common Issues

**Import errors:**
```bash
# Ensure the virtual environment is activated (it lives at .venv/, not venv/)
source .venv/bin/activate

# Rebuild it from the hash-pinned lockfiles
rm -rf .venv && make setup-python
```

**`make lint` says ruff or mypy is missing:**
```bash
# Installs only the lint/type-check toolchain — no system build deps needed
make setup-lint
```

**`make setup-python` fails building python-ldap:**
Install the LDAP headers (`libldap2-dev libsasl2-dev` on Debian/Ubuntu), or use
`make setup-lint` if you only need to lint.

**Port conflicts:**
```bash
# Find process
lsof -i :5000

# Kill process
kill -9 <PID>
```

## Communication

- **GitHub Issues**: Bug reports and feature requests
- **GitHub Discussions**: Questions and general discussion
- **Pull Requests**: Code contributions

## Recognition

Contributors will be recognized in:
- Release notes
- Contributors list
- Annual contributor report

## Questions?

- Check [Development Guide](development/README.md)
- Search existing issues
- Ask in GitHub Discussions
- Review [Architecture Documentation](architecture/README.md)

## License

By contributing, you agree that your contributions will be licensed under the Limited AGPL v3 license.

---

Thank you for contributing to Elder! 🎉
