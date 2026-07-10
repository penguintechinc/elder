"""Log Action Node for Streams Workflow.

Provides structured logging with configurable log levels.
Ported from icestreams-worker.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

from ...executor.node_registry import register_node
from ..base import BaseNode

logger = logging.getLogger(__name__)


@register_node("action_log", "actions", "Log")
class LogAction(BaseNode):
    """Log messages with structured format including context and metadata."""

    node_type = "action_log"
    name = "Log"
    description = "Log messages with configurable level and structured format"
    category = "actions"

    @classmethod
    def inputs(cls) -> List[Dict[str, Any]]:
        """Define input ports for the log node."""
        return [
            {
                "name": "message",
                "description": "Message to log",
                "required": True,
                "data_type": "string",
            },
            {
                "name": "data",
                "description": "Additional data to include in log",
                "required": False,
                "data_type": "any",
            },
        ]

    @classmethod
    def outputs(cls) -> List[Dict[str, Any]]:
        """Define output ports for the log node."""
        return [
            {
                "name": "logged",
                "description": "Whether message was logged successfully",
                "data_type": "bool",
            },
        ]

    def _format_log_entry(self, level: str, message: str, data: Any) -> str:
        """Format log entry as JSON with context."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "message": message,
            "execution_id": self.context.get("execution_id"),
            "playbook_id": self.context.get("playbook_id"),
            "node_id": self.context.get("node_id"),
        }
        if data is not None:
            entry["data"] = data
        return json.dumps(entry)

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Execute log action."""
        start_time = time.perf_counter()

        message = inputs.get("message", "")
        data = inputs.get("data")

        level = self.get_config_value("level", "INFO").upper()
        include_data = self.get_config_value("includeData", True)

        try:
            if not include_data:
                data = None

            log_entry = self._format_log_entry(level, message, data)

            if level == "DEBUG":
                logger.debug(log_entry)
            elif level == "INFO":
                logger.info(log_entry)
            elif level == "WARNING":
                logger.warning(log_entry)
            elif level == "ERROR":
                logger.error(log_entry)
            else:
                logger.info(log_entry)

            print(log_entry, flush=True)

            return {
                "logged": {
                    "data": True,
                    "metadata": {},
                    "source_node_id": self.context.get("node_id", ""),
                }
            }

        except Exception as e:
            self.log_error(f"Log action failed: {e}")
            raise
