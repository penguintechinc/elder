"""Network scanner using masscan for high-speed port scanning."""

# flake8: noqa: E501

import asyncio
import json
import logging
from typing import Any, Dict, List

from .base import BaseScanner

logger = logging.getLogger("scanner.network")


class NetworkScanner(BaseScanner):
    """Network port scanner using masscan."""

    async def scan(self, config: dict[str, Any]) -> dict[str, Any]:
        """Execute a network scan using masscan.

        Config schema:
        {
            "targets": ["192.168.1.0/24", "10.0.0.1"],  # Required
            "ports": "1-1024",                          # Required
            "scan_type": "tcp_syn",                     # Default: tcp_syn
            "rate": 1000,                               # Packets/sec, default: 1000
            "max_retries": 1,                           # Default: 1
            "wait": 10,                                 # Seconds to wait, default: 10
            "exclude": [],                              # IPs to exclude
            "extra_args": []                            # Additional masscan args
        }

        Returns:
            {
                "hosts": [
                    {
                        "ip": "192.168.1.1",
                        "ports": [
                            {"port": 22, "proto": "tcp", "state": "open"},
                            {"port": 80, "proto": "tcp", "state": "open"}
                        ]
                    }
                ],
                "scan_stats": {
                    "total_hosts": 10,
                    "hosts_up": 3,
                    "ports_scanned": 1024
                }
            }
        """
        self.validate_config(config, ["targets", "ports"])

        targets = config["targets"]
        ports = config["ports"]
        scan_type = config.get("scan_type", "tcp_syn")
        rate = config.get("rate", 1000)
        max_retries = config.get("max_retries", 1)
        wait = config.get("wait", 10)
        exclude = config.get("exclude", [])
        extra_args = config.get("extra_args", [])

        # Build masscan command
        cmd = [
            "masscan",
            "-p",
            str(ports),
            "--rate",
            str(rate),
            "--retries",
            str(max_retries),
            "--wait",
            str(wait),
            "-oJ",
            "-",  # JSON output to stdout
        ]

        # Add targets
        if isinstance(targets, list):
            cmd.extend(targets)
        else:
            cmd.append(targets)

        # Add scan type
        if scan_type == "tcp_syn":
            # Default masscan behavior
            pass
        elif scan_type == "tcp_connect":
            cmd.append("--banners")
        elif scan_type == "udp":
            cmd.append("-pU:" + str(ports))

        # Add exclusions
        if exclude:
            for exc in exclude:
                cmd.extend(["--exclude", exc])

        # Add extra args
        cmd.extend(extra_args)

        logger.info(f"Running masscan: {' '.join(cmd)}")

        # Execute masscan
        try:
            result = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await result.communicate()

            if result.returncode != 0:
                error_msg = stderr.decode() if stderr else "Unknown error"
                raise Exception(f"masscan failed: {error_msg}")

            # Parse JSON output
            output = stdout.decode().strip()
            if not output:
                return {
                    "hosts": [],
                    "scan_stats": {
                        "total_hosts": 0,
                        "hosts_up": 0,
                        "ports_scanned": 0,
                    },
                }

            # Masscan outputs JSON array with trailing comma issues
            # Clean it up
            if output.endswith(",\n]"):
                output = output[:-3] + "\n]"
            elif output.endswith(",]"):
                output = output[:-2] + "]"

            try:
                scan_results = json.loads(output)
            except json.JSONDecodeError:
                # Try to parse line by line
                scan_results = []
                for line in output.split("\n"):
                    line = line.strip().rstrip(",")
                    if line and line not in ["[", "]"]:
                        try:
                            scan_results.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue

            # Process results into our format
            hosts_dict: dict[str, list[dict[str, Any]]] = {}

            for entry in scan_results:
                if "ip" in entry and "ports" in entry:
                    ip = entry["ip"]
                    if ip not in hosts_dict:
                        hosts_dict[ip] = []

                    for port_info in entry["ports"]:
                        hosts_dict[ip].append(
                            {
                                "port": port_info.get("port"),
                                "proto": port_info.get("proto", "tcp"),
                                "state": port_info.get("status", "open"),
                                "service": port_info.get("service", {}).get("name", ""),
                            }
                        )

            # Convert to list format
            hosts = [
                {"ip": ip, "ports": ports_list} for ip, ports_list in hosts_dict.items()
            ]

            return {
                "hosts": hosts,
                "scan_stats": {
                    "total_hosts": len(hosts_dict),
                    "hosts_up": len(hosts_dict),
                    "ports_scanned": (
                        len(ports.split(","))
                        if "," in ports
                        else (
                            int(ports.split("-")[1]) - int(ports.split("-")[0]) + 1
                            if "-" in ports
                            else 1
                        )
                    ),
                },
            }

        except FileNotFoundError:
            raise Exception("masscan not found. Please install masscan.")
        except Exception as e:
            logger.error(f"Network scan failed: {e}")
            raise
