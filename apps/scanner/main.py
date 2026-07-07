"""Elder Local Scanner Service.

Polls the Elder API for pending scan jobs and executes them.
"""

# flake8: noqa: E501


import asyncio
import datetime
import logging
import os
import sys
from typing import Any, Dict, Optional

import httpx
from croniter import croniter
from scanners.banner import BannerScanner
from scanners.http_screenshot import HTTPScreenshotScanner
from scanners.network import NetworkScanner
from scanners.sbom_scanner import SBOMScanner

from shared.observability import init_telemetry

# Configuration from environment
POLL_INTERVAL = int(os.getenv("SCANNER_POLL_INTERVAL", "300"))
API_URL = os.getenv("ELDER_API_URL", "http://api:5000")
API_TOKEN = os.getenv("ELDER_API_TOKEN", "")
SCREENSHOT_DIR = os.getenv("SCREENSHOT_DIR", "/app/screenshots")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
NVD_SYNC_INTERVAL_HOURS = int(
    os.getenv("NVD_SYNC_INTERVAL_HOURS", "24")
)  # Run NVD sync once per day

# Initialize OpenTelemetry (Phase 0.5)
otel = init_telemetry("elder-scanner")

# Setup logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper()),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("scanner")


class ScannerService:
    """Main scanner service that polls for and executes scan jobs."""

    def __init__(self):
        self.api_url = API_URL.rstrip("/")
        self.headers = {
            "Content-Type": "application/json",
        }
        if API_TOKEN:
            self.headers["Authorization"] = f"Bearer {API_TOKEN}"

        # Initialize scanners
        self.scanners = {
            "network": NetworkScanner(),
            "http_screenshot": HTTPScreenshotScanner(screenshot_dir=SCREENSHOT_DIR),
            "banner": BannerScanner(),
            "sbom": SBOMScanner(),
        }

        # Ensure screenshot directory exists
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)

        # Track last NVD sync time
        self.last_nvd_sync: Optional[datetime.datetime] = None

    async def get_pending_jobs(self) -> list:
        """Fetch pending scan jobs from the API."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self.api_url}/api/v1/discovery/jobs/pending",
                    headers=self.headers,
                )
                if response.status_code == 200:
                    data = response.json()
                    return data.get("jobs", [])
                else:
                    logger.error(
                        f"Failed to fetch pending jobs: {response.status_code}"
                    )
                    return []
        except Exception as e:
            logger.error(f"Error fetching pending jobs: {e}")
            return []

    async def get_pending_sbom_scans(self) -> list:
        """Fetch pending SBOM scans from the API."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self.api_url}/api/v1/sbom/scans/pending",
                    headers=self.headers,
                )
                if response.status_code == 200:
                    return response.json()
                else:
                    logger.error(
                        f"Failed to fetch pending SBOM scans: {response.status_code}"
                    )
                    return []
        except Exception as e:
            logger.error(f"Error fetching pending SBOM scans: {e}")
            return []

    async def get_due_schedules(self) -> list:
        """Fetch due SBOM scan schedules from the API."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self.api_url}/api/v1/sbom/schedules/due",
                    headers=self.headers,
                )
                if response.status_code == 200:
                    return response.json()
                else:
                    logger.error(
                        f"Failed to fetch due schedules: {response.status_code}"
                    )
                    return []
        except Exception as e:
            logger.error(f"Error fetching due schedules: {e}")
            return []

    async def create_scheduled_scan(self, schedule: Dict[str, Any]) -> bool:
        """Create a scan job from a schedule and update the schedule."""
        schedule_id = schedule["id"]
        parent_type = schedule["parent_type"]
        parent_id = schedule["parent_id"]

        try:
            # Create scan job
            async with httpx.AsyncClient(timeout=30.0) as client:
                scan_payload = {
                    "parent_type": parent_type,
                    "parent_id": parent_id,
                    "scan_type": "git_clone",
                }
                response = await client.post(
                    f"{self.api_url}/api/v1/sbom/scans",
                    headers=self.headers,
                    json=scan_payload,
                )

                if response.status_code == 201:
                    logger.info(f"Created scan job for schedule {schedule_id}")

                    # Update schedule with last_run_at and calculate next_run_at
                    now = datetime.datetime.now(datetime.timezone.utc)
                    cron_expr = schedule.get("schedule_cron", "0 0 * * *")

                    try:
                        cron = croniter(cron_expr, now)
                        next_run = cron.get_next(datetime.datetime)
                    except Exception as e:
                        logger.error(f"Invalid cron expression '{cron_expr}': {e}")
                        # Default to 24 hours from now
                        next_run = now + datetime.timedelta(days=1)

                    # Update schedule
                    update_payload = {
                        "last_run_at": now.isoformat(),
                        "next_run_at": next_run.isoformat(),
                    }

                    response = await client.put(
                        f"{self.api_url}/api/v1/sbom/schedules/{schedule_id}",
                        headers=self.headers,
                        json=update_payload,
                    )

                    if response.status_code == 200:
                        logger.info(
                            f"Updated schedule {schedule_id}, next run at {next_run.isoformat()}"
                        )
                        return True
                    else:
                        logger.error(
                            f"Failed to update schedule {schedule_id}: {response.status_code}"
                        )
                        return False
                else:
                    logger.error(
                        f"Failed to create scan for schedule {schedule_id}: {response.status_code}"
                    )
                    return False

        except Exception as e:
            logger.error(
                f"Error creating scheduled scan for schedule {schedule_id}: {e}"
            )
            return False

    async def mark_job_running(self, job_id: int) -> bool:
        """Mark a job as running."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.api_url}/api/v1/discovery/jobs/{job_id}/start",
                    headers=self.headers,
                )
                return response.status_code == 200
        except Exception as e:
            logger.error(f"Error marking job {job_id} as running: {e}")
            return False

    async def submit_results(
        self,
        job_id: int,
        success: bool,
        results: Dict[str, Any],
        error_message: Optional[str] = None,
    ) -> bool:
        """Submit scan results back to the API."""
        try:
            payload = {
                "success": success,
                "results": results,
                "error_message": error_message,
            }
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.api_url}/api/v1/discovery/jobs/{job_id}/complete",
                    headers=self.headers,
                    json=payload,
                )
                return response.status_code == 200
        except Exception as e:
            logger.error(f"Error submitting results for job {job_id}: {e}")
            return False

    async def execute_job(self, job: Dict[str, Any]) -> None:
        """Execute a single scan job."""
        job_id = job["id"]
        provider = job["provider"]
        config = job.get("config_json", {})

        logger.info(f"Executing job {job_id} (type: {provider})")

        # Mark job as running
        if not await self.mark_job_running(job_id):
            logger.error(f"Failed to mark job {job_id} as running")
            return

        # Get appropriate scanner
        scanner = self.scanners.get(provider)
        if not scanner:
            error_msg = f"Unknown scanner type: {provider}"
            logger.error(error_msg)
            await self.submit_results(job_id, False, {}, error_msg)
            return

        # Execute scan
        try:
            results = await scanner.scan(config)
            await self.submit_results(job_id, True, results)
            logger.info(f"Job {job_id} completed successfully")
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Job {job_id} failed: {error_msg}")
            await self.submit_results(job_id, False, {}, error_msg)

    async def execute_sbom_scan(self, scan: Dict[str, Any]) -> None:
        """Execute a single SBOM scan."""
        scan_id = scan["id"]
        repository_url = scan.get("repository_url")
        repository_branch = scan.get("repository_branch", "main")

        logger.info(f"Executing SBOM scan {scan_id} for {repository_url}")

        # Mark scan as running
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.api_url}/api/v1/sbom/scans/{scan_id}/start",
                    headers=self.headers,
                )
                if response.status_code != 200:
                    logger.error(f"Failed to mark scan {scan_id} as running")
                    return
        except Exception as e:
            logger.error(f"Error marking scan {scan_id} as running: {e}")
            return

        # Get SBOM scanner
        scanner = self.scanners.get("sbom")
        if not scanner:
            logger.error("SBOM scanner not available")
            return

        # Execute scan
        try:
            config = {
                "repository_url": repository_url,
                "repository_branch": repository_branch,
                "api_url": self.api_url,
                "scan_id": scan_id,
            }

            results = await scanner.scan(config)

            # Submit results
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.api_url}/api/v1/sbom/scans/{scan_id}/results",
                    headers=self.headers,
                    json=results,
                )

                if response.status_code == 200:
                    logger.info(f"SBOM scan {scan_id} completed successfully")
                else:
                    logger.error(
                        f"Failed to submit results for scan {scan_id}: {response.status_code}"
                    )

        except Exception as e:
            error_msg = str(e)
            logger.error(f"SBOM scan {scan_id} failed: {error_msg}")

            # Submit failure
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    await client.post(
                        f"{self.api_url}/api/v1/sbom/scans/{scan_id}/results",
                        headers=self.headers,
                        json={"success": False, "error_message": error_msg},
                    )
            except Exception as submit_error:
                logger.error(
                    f"Failed to submit error for scan {scan_id}: {submit_error}"
                )

    async def trigger_nvd_sync(self) -> bool:
        """Trigger NVD sync via API to enrich vulnerability CVSS data."""
        try:
            async with httpx.AsyncClient(
                timeout=300.0
            ) as client:  # Longer timeout for sync
                response = await client.post(
                    f"{self.api_url}/api/v1/vulnerabilities/nvd-sync",
                    headers=self.headers,
                    json={"max_vulns": 500},  # Process up to 500 CVEs per sync
                )
                if response.status_code == 202:
                    data = response.json()
                    stats = data.get("stats", {})
                    logger.info(
                        f"NVD sync completed: processed={stats.get('processed', 0)}, "
                        f"updated={stats.get('updated', 0)}, errors={stats.get('errors', 0)}"
                    )
                    return True
                else:
                    logger.error(f"NVD sync failed: {response.status_code}")
                    return False
        except Exception as e:
            logger.error(f"Error triggering NVD sync: {e}")
            return False

    def _should_run_nvd_sync(self) -> bool:
        """Check if NVD sync should run (once per configured interval)."""
        if self.last_nvd_sync is None:
            return True

        elapsed = datetime.datetime.now(datetime.timezone.utc) - self.last_nvd_sync
        return elapsed.total_seconds() >= (NVD_SYNC_INTERVAL_HOURS * 3600)

    async def run(self) -> None:
        """Main poll loop."""
        logger.info(f"Scanner service started. Polling every {POLL_INTERVAL}s")
        logger.info(f"API URL: {self.api_url}")
        logger.info(f"NVD sync interval: {NVD_SYNC_INTERVAL_HOURS} hours")

        while True:
            try:
                # Check for due schedules and create scan jobs
                due_schedules = await self.get_due_schedules()

                if due_schedules:
                    logger.info(f"Found {len(due_schedules)} due schedule(s)")
                    for schedule in due_schedules:
                        await self.create_scheduled_scan(schedule)
                else:
                    logger.debug("No due schedules")

                # Fetch pending discovery jobs
                jobs = await self.get_pending_jobs()

                if jobs:
                    logger.info(f"Found {len(jobs)} pending discovery job(s)")
                    for job in jobs:
                        await self.execute_job(job)
                else:
                    logger.debug("No pending discovery jobs")

                # Fetch pending SBOM scans
                sbom_scans = await self.get_pending_sbom_scans()

                if sbom_scans:
                    logger.info(f"Found {len(sbom_scans)} pending SBOM scan(s)")
                    for scan in sbom_scans:
                        await self.execute_sbom_scan(scan)
                else:
                    logger.debug("No pending SBOM scans")

                # Run NVD sync once per day (or configured interval)
                if self._should_run_nvd_sync():
                    logger.info("Running scheduled NVD sync...")
                    if await self.trigger_nvd_sync():
                        self.last_nvd_sync = datetime.datetime.now(
                            datetime.timezone.utc
                        )

            except Exception as e:
                logger.error(f"Error in poll loop: {e}")

            # Wait before next poll
            await asyncio.sleep(POLL_INTERVAL)


def main():
    """Entry point."""
    service = ScannerService()
    asyncio.run(service.run())


if __name__ == "__main__":
    main()
