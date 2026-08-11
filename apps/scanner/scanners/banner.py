"""Banner grabber for collecting service banners from open ports."""

# flake8: noqa: E501

import asyncio
import logging
from typing import Any, Dict, Optional

from .base import BaseScanner

logger = logging.getLogger("scanner.banner")

# Common service ports and their probe strings
SERVICE_PROBES = {
    21: ("FTP", b"\r\n"),  # FTP
    22: ("SSH", b""),  # SSH (responds immediately)
    23: ("Telnet", b"\r\n"),  # Telnet
    25: ("SMTP", b"EHLO scanner\r\n"),  # SMTP
    80: ("HTTP", b"HEAD / HTTP/1.0\r\n\r\n"),  # HTTP
    110: ("POP3", b""),  # POP3
    143: ("IMAP", b""),  # IMAP
    443: ("HTTPS", b""),  # HTTPS (may not work without SSL)
    465: ("SMTPS", b""),  # SMTPS
    587: ("SMTP-Submission", b"EHLO scanner\r\n"),
    993: ("IMAPS", b""),  # IMAPS
    995: ("POP3S", b""),  # POP3S
    3306: ("MySQL", b""),  # MySQL
    5432: ("PostgreSQL", b""),  # PostgreSQL
    6379: ("Redis", b"PING\r\n"),  # Redis
    27017: ("MongoDB", b""),  # MongoDB
}


class BannerScanner(BaseScanner):
    """Banner grabber using socket connections."""

    async def scan(self, config: dict[str, Any]) -> dict[str, Any]:
        """Grab service banners from specified targets and ports.

        Config schema:
        {
            "targets": ["192.168.1.1", "10.0.0.1"],
            "ports": [22, 80, 443, 3306],     # Specific ports to check
            "timeout": 5,                     # Connection timeout in seconds
            "max_concurrent": 50              # Max concurrent connections
        }

        Returns:
            {
                "banners": [
                    {
                        "ip": "192.168.1.1",
                        "port": 22,
                        "service": "SSH",
                        "banner": "SSH-2.0-OpenSSH_8.2p1 Ubuntu-4ubuntu0.5",
                        "success": true
                    }
                ],
                "scan_stats": {
                    "total_targets": 2,
                    "total_ports": 4,
                    "banners_grabbed": 5
                }
            }
        """
        self.validate_config(config, ["targets", "ports"])

        targets = config["targets"]
        ports = config["ports"]
        timeout = config.get("timeout", 5)
        max_concurrent = config.get("max_concurrent", 50)

        # Build list of (target, port) tuples to scan
        scan_tasks = []
        for target in targets:
            for port in ports:
                scan_tasks.append((target, port))

        # Create semaphore for concurrency limiting
        semaphore = asyncio.Semaphore(max_concurrent)

        # Execute all banner grabs
        results = await asyncio.gather(
            *[
                self._grab_banner_with_semaphore(semaphore, target, port, timeout)
                for target, port in scan_tasks
            ]
        )

        # Filter out None results
        banners = [r for r in results if r is not None]
        successful = len([b for b in banners if b.get("success")])

        return {
            "banners": banners,
            "scan_stats": {
                "total_targets": len(targets),
                "total_ports": len(ports),
                "banners_grabbed": successful,
            },
        }

    async def _grab_banner_with_semaphore(
        self,
        semaphore: asyncio.Semaphore,
        target: str,
        port: int,
        timeout: int,
    ) -> dict[str, Any] | None:
        """Grab banner with concurrency limiting."""
        async with semaphore:
            return await self._grab_banner(target, port, timeout)

    async def _grab_banner(
        self,
        target: str,
        port: int,
        timeout: int,
    ) -> dict[str, Any]:
        """Grab banner from a single target/port."""
        service_name, probe = SERVICE_PROBES.get(port, ("Unknown", b""))

        result = {
            "ip": target,
            "port": port,
            "service": service_name,
            "banner": "",
            "success": False,
            "error": None,
        }

        try:
            # Create connection
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(target, port),
                timeout=timeout,
            )

            # Send probe if needed
            if probe:
                writer.write(probe)
                await writer.drain()

            # Read banner
            try:
                banner_data = await asyncio.wait_for(
                    reader.read(1024),
                    timeout=timeout,
                )
                banner = banner_data.decode("utf-8", errors="ignore").strip()
                result["banner"] = banner
                result["success"] = True
                logger.debug(f"Banner grabbed from {target}:{port}: {banner[:50]}...")
            except TimeoutError:
                # Some services don't send banners until you send something
                result["error"] = "No banner received"

            writer.close()
            await writer.wait_closed()

        except TimeoutError:
            result["error"] = "Connection timeout"
        except ConnectionRefusedError:
            result["error"] = "Connection refused"
        except OSError as e:
            result["error"] = str(e)
        except Exception as e:
            result["error"] = str(e)
            logger.error(f"Error grabbing banner from {target}:{port}: {e}")

        return result
