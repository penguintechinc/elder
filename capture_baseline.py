import importlib
import sys

from apps.api.models.base import Base

# Import all models
try:
    from apps.api.modules.access_reviews.models.access_review import *
    from apps.api.modules.access_reviews.models.resource_role import *
    from apps.api.modules.infrastructure.models.dependency import *
    from apps.api.modules.infrastructure.models.infrastructure import *
    from apps.api.modules.ipam.models import *
    from apps.api.modules.issues.models.issue import *
    from apps.api.modules.issues.models.metadata import *
    from apps.api.modules.issues.models.project import *
    from apps.api.modules.sbom.models.assets import *
    from apps.api.modules.services_oncall.models import *
except Exception as e:
    print(f"Import error: {e}", file=sys.stderr)
    sys.exit(1)

t = Base.metadata.tables
print("TABLE_COUNT", len(t))

import json

spec = {}
for name, tbl in t.items():
    for col in tbl.columns:
        if col.name in ("village_id", "tenant_id"):
            spec[f"{name}.{col.name}"] = {
                "type": str(col.type),
                "nullable": col.nullable,
                "unique": bool(col.unique),
                "index": bool(col.index),
                "fks": sorted(str(fk.target_fullname) for fk in col.foreign_keys),
            }

print("COLSPEC", json.dumps(spec, sort_keys=True))
