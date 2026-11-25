"""Route guard shared by the secrets, keys and provider endpoints."""

import functools
import inspect

from quart import jsonify

from apps.api.utils.tenant_scoping import get_current_tenant_id


def tenant_required(fn):
    """Reject requests whose verified token carries no tenant (403).

    Apply below ``login_required``/``require_scope`` so handlers can rely on a
    tenant-scoped service; the service layer also fails closed on its own.
    """

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        if not get_current_tenant_id():
            return jsonify({"error": "Tenant context required"}), 403
        if inspect.iscoroutinefunction(fn):
            return await fn(*args, **kwargs)
        return fn(*args, **kwargs)

    return wrapper
