"""Dynamic node generator for connector manifests.

Generates BaseNode subclasses at runtime from connector manifests,
allowing new connectors to be added without writing Python code.

Ported from icestreams-worker; converted to Elder node contract.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Type

from apps.worker.streams.executor.node_registry import register_node
from apps.worker.streams.nodes.base import BaseNode

from .base import (
    ActionDefinition,
    ConnectorManifest,
    PortDefinition,
    TransformDefinition,
    TriggerDefinition,
)
from .executor import ConnectorActionExecutor

logger = logging.getLogger(__name__)


def _port_to_node_input(port: PortDefinition) -> dict[str, Any]:
    """Convert PortDefinition to node input dict."""
    return {
        "name": port.name,
        "description": port.description,
        "required": port.required,
        "data_type": port.type,
    }


def _port_to_node_output(port: PortDefinition) -> dict[str, Any]:
    """Convert PortDefinition to node output dict."""
    return {
        "name": port.name,
        "description": port.description,
        "data_type": port.type,
    }


def create_trigger_node(
    connector_id: str,
    trigger: TriggerDefinition,
    manifest: ConnectorManifest,
) -> type[BaseNode]:
    """Generate a trigger node class from manifest definition.

    Args:
        connector_id: Connector identifier.
        trigger: Trigger definition from manifest.
        manifest: The full connector manifest (for base_url).

    Returns:
        Generated BaseNode subclass.
    """
    # Pre-compute all values (use different names to avoid class scope issues)
    nt = f"trigger_{connector_id}_{trigger.id}"
    nm = f"{manifest.name}: {trigger.name}"
    desc = trigger.description
    conn_id = connector_id
    conn_name = manifest.name
    conn_color = manifest.color
    trig_icon = trigger.icon
    trig_schema = trigger.config_schema

    # Pre-compute outputs
    outs = [_port_to_node_output(p) for p in trigger.outputs]
    if not outs:
        outs = [{"name": "out", "description": "Trigger output", "data_type": "any"}]

    # Pre-compute config schema for validation
    req_fields = [f.field for f in trigger.config_schema if f.required]

    class GeneratedTriggerNode(BaseNode):
        """Dynamically generated trigger node."""

        node_type = nt
        name = nm
        description = desc
        category = "triggers"

        # Store connector and trigger info for UI
        connector_id_attr = conn_id
        connector_name_attr = conn_name
        connector_color_attr = conn_color
        trigger_icon = trig_icon
        config_schema_attr = trig_schema

        @classmethod
        def inputs(cls) -> list[dict[str, Any]]:
            """Triggers don't have inputs - they start workflows."""
            return []

        @classmethod
        def outputs(cls) -> list[dict[str, Any]]:
            return outs

        def validate_config(self, config: dict[str, Any]) -> list[str]:
            """Validate configuration."""
            errors = []
            for field_name in req_fields:
                if not config.get(field_name):
                    errors.append(f"Required field '{field_name}' is missing")
            return errors

        async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
            """Execute trigger node.

            For triggers, data comes from the trigger_data in context global_config.
            """
            # Get trigger data from global_config (set by webhook handler)
            trigger_data = self.context.get("global_config", {}).get("trigger_data", {})

            # Route data to appropriate output port
            node_id = self.context.get("node_id", "")
            output_data = {
                "out": {"data": trigger_data, "metadata": {}, "source_node_id": node_id}
            }

            # For event triggers with multiple outputs, route by event type if present
            if len(outs) > 1 and "event_type" in trigger_data:
                event_type = trigger_data.get("event_type", "other")
                output_data[event_type] = {
                    "data": trigger_data,
                    "metadata": {},
                    "source_node_id": node_id,
                }

            self.log_info(f"Trigger {trigger.name} fired")
            return output_data

    GeneratedTriggerNode.__name__ = (
        f"{manifest.name.replace(' ', '')}_{trigger.id}_Trigger"
    )
    return GeneratedTriggerNode


def create_action_node(
    connector_id: str,
    action: ActionDefinition,
    manifest: ConnectorManifest,
) -> type[BaseNode]:
    """Generate an action node class from manifest definition.

    Args:
        connector_id: Connector identifier.
        action: Action definition from manifest.
        manifest: The full connector manifest (for base_url).

    Returns:
        Generated BaseNode subclass.
    """
    # Pre-compute all values
    nt = f"action_{connector_id}_{action.id}"
    nm = f"{manifest.name}: {action.name}"
    desc = action.description
    conn_id = connector_id
    conn_name = manifest.name
    conn_color = manifest.color
    act_icon = action.icon
    act_endpoint = action.endpoint
    act_method = action.method
    act_schema = action.config_schema
    act_body_template = action.request_body_template

    # Pre-compute inputs and outputs
    inps = [_port_to_node_input(p) for p in action.inputs]
    if not inps:
        inps = [{"name": "in", "description": "Input data", "required": True}]

    outs = [_port_to_node_output(p) for p in action.outputs]
    if not outs:
        outs = [{"name": "out", "description": "Action result", "data_type": "object"}]

    # Pre-compute config schema for validation
    req_fields = [f.field for f in action.config_schema if f.required]

    class GeneratedActionNode(BaseNode):
        """Dynamically generated action node."""

        node_type = nt
        name = nm
        description = desc
        category = "actions"

        # Store connector info
        connector_id_attr = conn_id
        connector_name_attr = conn_name
        connector_color_attr = conn_color
        action_icon = act_icon
        action_endpoint = act_endpoint
        action_method = act_method
        config_schema_attr = act_schema

        @classmethod
        def inputs(cls) -> list[dict[str, Any]]:
            return inps

        @classmethod
        def outputs(cls) -> list[dict[str, Any]]:
            return outs

        def validate_config(self, config: dict[str, Any]) -> list[str]:
            """Validate configuration."""
            errors = []
            for field_name in req_fields:
                if not config.get(field_name):
                    errors.append(f"Required field '{field_name}' is missing")
            return errors

        async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
            """Execute action by calling connector API via executor."""
            executor = ConnectorActionExecutor()
            config = self.context.get("config", {})
            global_config = self.context.get("global_config", {})
            node_id = self.context.get("node_id", "")

            try:
                result = await executor.execute_action(
                    connector_id=conn_id,
                    action_id=action.id,
                    endpoint=act_endpoint,
                    method=act_method,
                    request_body_template=act_body_template,
                    config_schema=act_schema,
                    config=config,
                    inputs=inputs,
                    variables=global_config,
                    base_url=manifest.default_url,
                )

                self.log_info(f"Action {action.name} completed")

                # Wrap result in the output port structure
                return {
                    "out": {"data": result, "metadata": {}, "source_node_id": node_id}
                }

            except Exception as e:
                self.log_error(f"Action {action.name} failed: {e}")
                raise

    GeneratedActionNode.__name__ = (
        f"{manifest.name.replace(' ', '')}_{action.id}_Action"
    )
    return GeneratedActionNode


def create_transform_node(
    connector_id: str,
    transform: TransformDefinition,
    manifest: ConnectorManifest,
) -> type[BaseNode]:
    """Generate a transform node class from manifest definition.

    Args:
        connector_id: Connector identifier.
        transform: Transform definition from manifest.
        manifest: The full connector manifest (for base_url).

    Returns:
        Generated BaseNode subclass.
    """
    # Pre-compute all values
    nt = f"transform_{connector_id}_{transform.id}"
    nm = f"{manifest.name}: {transform.name}"
    desc = transform.description
    conn_id = connector_id
    conn_name = manifest.name
    conn_color = manifest.color
    trans_icon = transform.icon
    trans_endpoint = transform.endpoint
    trans_method = transform.method
    trans_schema = transform.config_schema

    # Pre-compute inputs and outputs
    inps = [_port_to_node_input(p) for p in transform.inputs]
    if not inps:
        inps = [{"name": "in", "description": "Input data", "required": True}]

    outs = [_port_to_node_output(p) for p in transform.outputs]
    if not outs:
        outs = [{"name": "out", "description": "Transform result", "data_type": "any"}]

    req_fields = [f.field for f in transform.config_schema if f.required]

    class GeneratedTransformNode(BaseNode):
        """Dynamically generated transform node."""

        node_type = nt
        name = nm
        description = desc
        category = "transforms"

        connector_id_attr = conn_id
        connector_name_attr = conn_name
        connector_color_attr = conn_color
        transform_icon = trans_icon
        transform_endpoint = trans_endpoint
        transform_method = trans_method
        config_schema_attr = trans_schema

        @classmethod
        def inputs(cls) -> list[dict[str, Any]]:
            return inps

        @classmethod
        def outputs(cls) -> list[dict[str, Any]]:
            return outs

        def validate_config(self, config: dict[str, Any]) -> list[str]:
            """Validate configuration."""
            errors = []
            for field_name in req_fields:
                if not config.get(field_name):
                    errors.append(f"Required field '{field_name}' is missing")
            return errors

        async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
            """Execute transform by calling connector API if endpoint exists."""
            config = self.context.get("config", {})
            global_config = self.context.get("global_config", {})
            node_id = self.context.get("node_id", "")

            try:
                if trans_endpoint:
                    # Call API for data lookup/transformation
                    executor = ConnectorActionExecutor()
                    result = await executor.execute_transform(
                        connector_id=conn_id,
                        transform_id=transform.id,
                        endpoint=trans_endpoint,
                        method=trans_method,
                        config_schema=trans_schema,
                        config=config,
                        inputs=inputs,
                        variables=global_config,
                        base_url=manifest.default_url,
                    )
                else:
                    # Pass-through transform (no API call)
                    result = inputs.get("in", {})

                self.log_info(f"Transform {transform.name} completed")

                # Wrap result in the output port structure
                return {
                    "out": {"data": result, "metadata": {}, "source_node_id": node_id}
                }

            except Exception as e:
                self.log_error(f"Transform {transform.name} failed: {e}")
                raise

    GeneratedTransformNode.__name__ = (
        f"{manifest.name.replace(' ', '')}_{transform.id}_Transform"
    )
    return GeneratedTransformNode


def generate_nodes_from_connector(manifest: ConnectorManifest) -> int:
    """Generate and register all nodes from a connector manifest.

    Args:
        manifest: Parsed connector manifest.

    Returns:
        Number of nodes generated.
    """
    generated = 0

    # Generate trigger nodes
    for trigger in manifest.triggers:
        try:
            node_class = create_trigger_node(
                connector_id=manifest.id,
                trigger=trigger,
                manifest=manifest,
            )
            register_node(
                node_type=node_class.node_type,
                category="triggers",
                display_name=node_class.name,
                description=node_class.description,
            )(node_class)
            generated += 1
            logger.debug(f"Generated trigger node: {node_class.node_type}")
        except Exception as e:
            logger.error(f"Failed to generate trigger {trigger.id}: {e}")

    # Generate action nodes
    for action in manifest.actions:
        try:
            node_class = create_action_node(
                connector_id=manifest.id,
                action=action,
                manifest=manifest,
            )
            register_node(
                node_type=node_class.node_type,
                category="actions",
                display_name=node_class.name,
                description=node_class.description,
            )(node_class)
            generated += 1
            logger.debug(f"Generated action node: {node_class.node_type}")
        except Exception as e:
            logger.error(f"Failed to generate action {action.id}: {e}")

    # Generate transform nodes
    for transform in manifest.transforms:
        try:
            node_class = create_transform_node(
                connector_id=manifest.id,
                transform=transform,
                manifest=manifest,
            )
            register_node(
                node_type=node_class.node_type,
                category="transforms",
                display_name=node_class.name,
                description=node_class.description,
            )(node_class)
            generated += 1
            logger.debug(f"Generated transform node: {node_class.node_type}")
        except Exception as e:
            logger.error(f"Failed to generate transform {transform.id}: {e}")

    logger.info(
        f"Generated {generated} nodes from connector {manifest.id}: "
        f"{len(manifest.triggers)} triggers, "
        f"{len(manifest.actions)} actions, "
        f"{len(manifest.transforms)} transforms"
    )

    return generated
