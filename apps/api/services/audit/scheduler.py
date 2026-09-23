"""Audit Log Retention Scheduler.

Automatically enforces each tenant's configured audit-log retention window
so GDPR Art. 5 (storage limitation) compliance does not depend on an admin
remembering to trigger the manual cleanup endpoint. Mirrors the background
scheduler pattern used by `apps.api.services.access_review.scheduler`
(dedicated daemon thread, started from `main.py`, stop-event driven loop)
rather than introducing a new scheduling mechanism.
"""

import logging
import os
import threading

from apps.api.services.audit.service import AuditService

logger = logging.getLogger(__name__)

_TRUE_VALUES = {"1", "true", "yes", "on"}


def _env_bool(name: str, default: bool) -> bool:
    """Parse a boolean env var, tolerant of common truthy spellings."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_VALUES


def _env_int(name: str, default: int) -> int:
    """Parse a positive-int env var, falling back to `default` on bad input."""
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


class AuditRetentionScheduler:
    """Background scheduler enforcing audit log retention windows.

    Runs on its own daemon thread outside any Quart request/app context, so
    it is handed a PyDAL `db` instance directly rather than relying on
    `current_app` (which `AuditService`'s request-context helpers use).
    """

    def __init__(self, db):
        """Initialize scheduler.

        Args:
            db: PyDAL database instance
        """
        self.db = db
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._check_interval = _env_int(
            "AUDIT_RETENTION_CHECK_INTERVAL_SECONDS", 86400
        )  # default: once per day

    @property
    def enabled(self) -> bool:
        """Whether automatic retention enforcement is turned on.

        Defaults ON with a bounded, sane check interval so the feature is
        safe by default; deployments can opt out via env var.
        """
        return _env_bool("AUDIT_RETENTION_AUTOENFORCE_ENABLED", True)

    def start(self) -> None:
        """Start the scheduler thread, unless auto-enforcement is disabled."""
        if not self.enabled:
            logger.info("audit_retention_autoenforce_disabled")
            return

        if self._thread and self._thread.is_alive():
            logger.warning("Audit retention scheduler already running")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info(
            f"audit_retention_scheduler_started check_interval_s={self._check_interval}"
        )

    def stop(self) -> None:
        """Stop the scheduler thread."""
        if not self._thread or not self._thread.is_alive():
            return

        self._stop_event.set()
        self._thread.join(timeout=5)
        logger.info("audit_retention_scheduler_stopped")

    def _run(self) -> None:
        """Main scheduler loop — runs one sweep per tick until stopped."""
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception as e:
                logger.error(
                    f"Error in audit retention scheduler loop: {e}", exc_info=True
                )

            self._stop_event.wait(self._check_interval)

    def run_once(self) -> dict:
        """Run a single retention sweep across every active tenant.

        Each tenant is purged independently (tenant-scoped query + its own
        configured/floored retention window) so isolation holds even if one
        tenant's purge fails. Only sanitized counts are logged — never
        record contents.

        Returns:
            Summary dict: tenants checked/purged and total rows deleted
        """
        tenant_ids = AuditService.get_active_tenant_ids(self.db)
        results = []

        for tenant_id in tenant_ids:
            try:
                result = AuditService.cleanup_old_logs(tenant_id, db=self.db)
            except Exception as e:
                logger.error(
                    f"audit_retention_purge_failed tenant_id={tenant_id} error={e}",
                    exc_info=True,
                )
                continue

            if "error" in result:
                logger.warning(
                    f"audit_retention_purge_skipped tenant_id={tenant_id} "
                    f"reason={result['error']}"
                )
                continue

            results.append(result)

            if result["deleted_count"]:
                logger.info(
                    "audit_retention_purged "
                    f"tenant_id={tenant_id} "
                    f"deleted_count={result['deleted_count']} "
                    f"retention_days={result['retention_days']}"
                )

        total_deleted = sum(r["deleted_count"] for r in results)
        tenants_purged = sum(1 for r in results if r["deleted_count"])

        logger.info(
            "audit_retention_sweep_complete "
            f"tenants_checked={len(tenant_ids)} "
            f"tenants_purged={tenants_purged} "
            f"total_deleted={total_deleted}"
        )

        return {
            "tenants_checked": len(tenant_ids),
            "tenants_purged": tenants_purged,
            "total_deleted": total_deleted,
            "results": results,
        }


# Global scheduler instance — mirrors access_review.scheduler's module-level
# singleton so main.py can init/stop it the same way.
_scheduler: AuditRetentionScheduler | None = None


def init_scheduler(db) -> None:
    """Initialize and start the global audit retention scheduler.

    Args:
        db: PyDAL database instance
    """
    global _scheduler

    if _scheduler is not None:
        logger.warning("Audit retention scheduler already initialized")
        return

    _scheduler = AuditRetentionScheduler(db)
    _scheduler.start()


def get_scheduler() -> AuditRetentionScheduler | None:
    """Get the global scheduler instance.

    Returns:
        Scheduler instance or None if not initialized
    """
    return _scheduler


def stop_scheduler() -> None:
    """Stop the global scheduler."""
    global _scheduler

    if _scheduler:
        _scheduler.stop()
        _scheduler = None
