#!/bin/bash
# Run Alembic database migrations manually.
#
# Usage:
#   ./scripts/migrate.sh                  # upgrade head (default)
#   ./scripts/migrate.sh current          # show current revision
#   ./scripts/migrate.sh history          # show full migration chain
#   ./scripts/migrate.sh downgrade -1     # roll back one step
#
# Run this BEFORE starting the API after schema changes, or on a fresh DB
# when full migration history tracking is needed. For fresh databases,
# the API's startup create_all() handles table creation automatically.

set -euo pipefail
cd "$(dirname "$0")/.."

CMD="${*:-upgrade head}"
# shellcheck disable=SC2086
alembic $CMD
