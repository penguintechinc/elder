"""Delay Transform Node for Streams Workflow.

Adds a configurable delay to workflow execution using async sleep.
Ported from icestreams-worker.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

from ...executor.node_registry import register_node
from ..base import BaseNode

logger = logging.getLogger(__name__)


@register_node("transform_delay", "transforms", "Delay")
class DelayTransform(BaseNode):
    """Add a delay/pause to the workflow execution."""

    node_type = "transform_delay"
    name = "Delay"
    description = "Pause workflow execution for a specified duration"
    category = "transforms"

    @classmethod
    def inputs(cls) -> List[Dict[str, Any]]:
        """Define input ports for the delay node."""
        return [
            {
                "name": "in",
                "description": "Input data (passed through after delay)",
                "required": True,
                "data_type": "any",
            },
        ]

    @classmethod
    def outputs(cls) -> List[Dict[str, Any]]:
        """Define output ports for the delay node."""
        return [
            {
                "name": "out",
                "description": "Input data passed through",
                "data_type": "any",
            },
        ]

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the delay transform."""
        start_time = time.perf_counter()

        input_data = inputs.get("in")

        # Get delay duration from config
        delay_ms = self.get_config_value("delayMs")
        delay_seconds = self.get_config_value("delaySeconds")

        # Convert to seconds
        if delay_ms is not None:
            delay = float(delay_ms) / 1000.0
        elif delay_seconds is not None:
            delay = float(delay_seconds)
        else:
            delay = 0.0

        # Cap at 5 minutes for safety
        delay = min(delay, 300.0)

        if delay > 0:
            self.log_info(f"Delaying for {delay:.2f} seconds")
            started_at = datetime.now(timezone.utc).isoformat()

            await asyncio.sleep(delay)

            finished_at = datetime.now(timezone.utc).isoformat()
            self.log_info(f"Delay completed")
        else:
            started_at = finished_at = datetime.now(timezone.utc).isoformat()

        # Pass through input data with timing metadata
        if isinstance(input_data, dict):
            result = {
                **input_data,
                "_delay": {
                    "duration_seconds": delay,
                    "started_at": started_at,
                    "finished_at": finished_at,
                },
            }
        else:
            result = input_data

        return {
            "out": {
                "data": result,
                "metadata": {},
                "source_node_id": self.context.get("node_id", ""),
            }
        }
