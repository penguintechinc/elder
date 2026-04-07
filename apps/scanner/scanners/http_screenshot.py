"""HTTP screenshot scanner using Playwright for headless browser captures."""

# flake8: noqa: E501


import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from .base import BaseScanner

logger = logging.getLogger("scanner.http_screenshot")


class HTTPScreenshotScanner(BaseScanner):
    """HTTP screenshot scanner using Playwright."""

    def __init__(self, screenshot_dir: str = "/app/screenshots"):
        self.screenshot_dir = screenshot_dir
        os.makedirs(screenshot_dir, exist_ok=True)

    async def scan(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Capture screenshots of web services.

        Config schema:
        {
            "targets": ["https://example.com", "http://192.168.1.1:8080"],
            "timeout": 30000,           # Page load timeout in ms, default: 30000
            "viewport_width": 1920,     # Default: 1920
            "viewport_height": 1080,    # Default: 1080
            "full_page": false,         # Capture full page, default: false
            "wait_for": "load",         # load, domcontentloaded, networkidle
            "ignore_https_errors": true # Default: true
        }

        Returns:
            {
                "screenshots": [
                    {
                        "url": "https://example.com",
                        "path": "/app/screenshots/abc123.png",
                        "title": "Example Domain",
                        "status_code": 200,
                        "success": true
                    }
                ],
                "scan_stats": {
                    "total_targets": 2,
                    "successful": 2,
                    "failed": 0
                }
            }
        """
        self.validate_config(config, ["targets"])

        targets = config["targets"]
        timeout = config.get("timeout", 30000)
        viewport_width = config.get("viewport_width", 1920)
        viewport_height = config.get("viewport_height", 1080)
        full_page = config.get("full_page", False)
        wait_for = config.get("wait_for", "load")
        ignore_https_errors = config.get("ignore_https_errors", True)

        # Import playwright here to avoid import errors when not installed
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise Exception(
                "Playwright not installed. Run: pip install playwright && playwright install chromium"
            )

        screenshots: List[Dict[str, Any]] = []
        successful = 0
        failed = 0

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )

            context = await browser.new_context(
                viewport={"width": viewport_width, "height": viewport_height},
                ignore_https_errors=ignore_https_errors,
            )

            for target in targets:
                result = await self._capture_screenshot(
                    context,
                    target,
                    timeout,
                    full_page,
                    wait_for,
                )
                screenshots.append(result)

                if result["success"]:
                    successful += 1
                else:
                    failed += 1

            await browser.close()

        return {
            "screenshots": screenshots,
            "scan_stats": {
                "total_targets": len(targets),
                "successful": successful,
                "failed": failed,
            },
        }

    async def _capture_screenshot(
        self,
        context,
        url: str,
        timeout: int,
        full_page: bool,
        wait_for: str,
    ) -> Dict[str, Any]:
        """Capture a screenshot of a single URL."""
        # Generate filename from URL hash
        url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"{url_hash}_{timestamp}.png"
        filepath = os.path.join(self.screenshot_dir, filename)

        result = {
            "url": url,
            "path": filepath,
            "title": "",
            "status_code": 0,
            "success": False,
            "error": None,
        }

        try:
            page = await context.new_page()

            # Navigate to URL
            response = await page.goto(
                url,
                timeout=timeout,
                wait_until=wait_for,
            )

            if response:
                result["status_code"] = response.status

            # Get page title
            result["title"] = await page.title()

            # Take screenshot
            await page.screenshot(path=filepath, full_page=full_page)

            result["success"] = True
            logger.info(f"Screenshot captured: {url} -> {filepath}")

            await page.close()

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"Failed to capture {url}: {e}")

        return result
