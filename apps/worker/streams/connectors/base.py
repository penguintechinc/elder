"""Connector manifest dataclasses and base connector for workflow connectors.

Ported from icestreams-worker.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict

import yaml

logger = logging.getLogger(__name__)


class AuthType(str, Enum):
    """Supported authentication types for connectors."""

    NONE = "none"
    API_KEY = "api_key"
    OAUTH = "oauth"
    JWT = "jwt"


@dataclass(slots=True, frozen=True)
class AuthMethod:
    """Authentication method configuration."""

    type: AuthType
    header: str = ""
    env_var: str = ""
    token_prefix: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AuthMethod:
        """Create AuthMethod from dictionary."""
        return cls(
            type=AuthType(data.get("type", "none")),
            header=data.get("header", ""),
            env_var=data.get("env_var", ""),
            token_prefix=data.get("token_prefix", ""),
        )


@dataclass(slots=True, frozen=True)
class ConfigField:
    """Configuration field schema for UI rendering."""

    field: str
    type: str
    label: str
    placeholder: str = ""
    options: tuple = ()
    required: bool = False
    default: Any = None
    supports_variables: bool = False
    description: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ConfigField:
        """Create ConfigField from dictionary."""
        options = data.get("options", [])
        return cls(
            field=data["field"],
            type=data.get("type", "string"),
            label=data.get("label", data["field"]),
            placeholder=data.get("placeholder", ""),
            options=tuple(options) if options else (),
            required=data.get("required", False),
            default=data.get("default"),
            supports_variables=data.get("supports_variables", False),
            description=data.get("description", ""),
        )


@dataclass(slots=True, frozen=True)
class PortDefinition:
    """Input/output port definition for workflow nodes."""

    name: str
    type: str = "any"
    description: str = ""
    required: bool = True

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PortDefinition:
        """Create PortDefinition from dictionary."""
        return cls(
            name=data["name"],
            type=data.get("type", "any"),
            description=data.get("description", ""),
            required=data.get("required", True),
        )


@dataclass(slots=True, frozen=True)
class TriggerDefinition:
    """Trigger node definition from connector manifest."""

    id: str
    name: str
    description: str
    icon: str = ""
    webhook_path: str = ""
    outputs: tuple = ()
    config_schema: tuple = ()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TriggerDefinition:
        """Create TriggerDefinition from dictionary."""
        outputs = tuple(PortDefinition.from_dict(o) for o in data.get("outputs", []))
        config_schema = tuple(
            ConfigField.from_dict(f) for f in data.get("config_schema", [])
        )
        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            icon=data.get("icon", ""),
            webhook_path=data.get("webhook_path", ""),
            outputs=outputs,
            config_schema=config_schema,
        )


@dataclass(slots=True, frozen=True)
class ActionDefinition:
    """Action node definition from connector manifest."""

    id: str
    name: str
    description: str
    endpoint: str
    method: str = "POST"
    icon: str = ""
    inputs: tuple = ()
    outputs: tuple = ()
    config_schema: tuple = ()
    request_body_template: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ActionDefinition:
        """Create ActionDefinition from dictionary."""
        inputs = tuple(PortDefinition.from_dict(i) for i in data.get("inputs", []))
        outputs = tuple(PortDefinition.from_dict(o) for o in data.get("outputs", []))
        config_schema = tuple(
            ConfigField.from_dict(f) for f in data.get("config_schema", [])
        )
        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            endpoint=data.get("endpoint", ""),
            method=data.get("method", "POST"),
            icon=data.get("icon", ""),
            inputs=inputs,
            outputs=outputs,
            config_schema=config_schema,
            request_body_template=data.get("request_body_template", ""),
        )


@dataclass(slots=True, frozen=True)
class TransformDefinition:
    """Transform node definition from connector manifest."""

    id: str
    name: str
    description: str
    icon: str = ""
    endpoint: str = ""
    method: str = "GET"
    inputs: tuple = ()
    outputs: tuple = ()
    config_schema: tuple = ()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TransformDefinition:
        """Create TransformDefinition from dictionary."""
        inputs = tuple(PortDefinition.from_dict(i) for i in data.get("inputs", []))
        outputs = tuple(PortDefinition.from_dict(o) for o in data.get("outputs", []))
        config_schema = tuple(
            ConfigField.from_dict(f) for f in data.get("config_schema", [])
        )
        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            icon=data.get("icon", ""),
            endpoint=data.get("endpoint", ""),
            method=data.get("method", "GET"),
            inputs=inputs,
            outputs=outputs,
            config_schema=config_schema,
        )


@dataclass(slots=True)
class ConnectorManifest:
    """Parsed connector manifest containing all definitions."""

    id: str
    name: str
    description: str
    icon: str = ""
    color: str = "#6366F1"
    version: str = "1.0.0"
    vendor: str = "external"
    auth_methods: tuple = ()
    base_url_env: str = ""
    default_url: str = ""
    health_endpoint: str = "/health"
    triggers: tuple = ()
    actions: tuple = ()
    transforms: tuple = ()

    @classmethod
    def from_yaml(cls, yaml_path: str) -> ConnectorManifest:
        """Load connector manifest from YAML file."""
        with open(yaml_path, "r") as f:
            data = yaml.safe_load(f)

        connector = data.get("connector", data)

        auth_methods = tuple(
            AuthMethod.from_dict(m)
            for m in connector.get("auth", {}).get("methods", [])
        )

        connection = connector.get("connection", {})

        triggers = tuple(
            TriggerDefinition.from_dict(t) for t in connector.get("triggers", [])
        )

        actions = tuple(
            ActionDefinition.from_dict(a) for a in connector.get("actions", [])
        )

        transforms = tuple(
            TransformDefinition.from_dict(t) for t in connector.get("transforms", [])
        )

        return cls(
            id=connector["id"],
            name=connector["name"],
            description=connector.get("description", ""),
            icon=connector.get("icon", ""),
            color=connector.get("color", "#6366F1"),
            version=connector.get("version", "1.0.0"),
            vendor=connector.get("vendor", "external"),
            auth_methods=auth_methods,
            base_url_env=connection.get("base_url_env", ""),
            default_url=connection.get("default_url", ""),
            health_endpoint=connection.get("health_endpoint", "/health"),
            triggers=triggers,
            actions=actions,
            transforms=transforms,
        )
