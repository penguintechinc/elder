"""REST API endpoints for geographic regions, locations, and geocoding."""

import asyncio
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any

from quart import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import login_required, require_scope
from apps.api.logging_config import log_error_and_respond
from apps.api.utils.village_id import mint_village_id

logger = logging.getLogger(__name__)

bp = Blueprint("geo", __name__, url_prefix="/api/v1/geo")

# Built-in fallback coordinates for common data center hubs and cities:
# (latitude, longitude, display_name, city, state, country)
_KNOWN_LOCATIONS: dict[str, tuple[float, float, str, str, str | None, str]] = {
    "dallas": (
        32.7767,
        -96.7970,
        "Dallas, TX, United States",
        "Dallas",
        "Texas",
        "United States",
    ),
    "ashburn": (
        39.0438,
        -77.4874,
        "Ashburn, VA, United States",
        "Ashburn",
        "Virginia",
        "United States",
    ),
    "virginia": (
        37.4316,
        -78.6569,
        "Virginia, United States",
        "Richmond",
        "Virginia",
        "United States",
    ),
    "new york": (
        40.7128,
        -74.0060,
        "New York, NY, United States",
        "New York",
        "New York",
        "United States",
    ),
    "london": (
        51.5072,
        -0.1276,
        "London, United Kingdom",
        "London",
        "England",
        "United Kingdom",
    ),
    "frankfurt": (
        50.1109,
        8.6821,
        "Frankfurt am Main, Germany",
        "Frankfurt am Main",
        "Hesse",
        "Germany",
    ),
    "singapore": (1.3521, 103.8198, "Singapore", "Singapore", None, "Singapore"),
    "tokyo": (35.6762, 139.6503, "Tokyo, Japan", "Tokyo", None, "Japan"),
    "chicago": (
        41.8781,
        -87.6298,
        "Chicago, IL, United States",
        "Chicago",
        "Illinois",
        "United States",
    ),
    "san francisco": (
        37.7749,
        -122.4194,
        "San Francisco, CA, United States",
        "San Francisco",
        "California",
        "United States",
    ),
    "seattle": (
        47.6062,
        -122.3321,
        "Seattle, WA, United States",
        "Seattle",
        "Washington",
        "United States",
    ),
    "amsterdam": (
        52.3676,
        4.9041,
        "Amsterdam, Netherlands",
        "Amsterdam",
        "North Holland",
        "Netherlands",
    ),
    "dublin": (53.3498, -6.2603, "Dublin, Ireland", "Dublin", None, "Ireland"),
    "sydney": (
        -33.8688,
        151.2093,
        "Sydney, NSW, Australia",
        "Sydney",
        "New South Wales",
        "Australia",
    ),
}

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_NOMINATIM_USER_AGENT = "Elder-CMDB/4.0"


def _get_tenant_id() -> str:
    """Resolve the caller's tenant_id string from JWT claims, defaulting to 'default'."""
    claims = getattr(g, "claims", {}) or {}
    tenant = claims.get("tenant")
    if tenant is not None and str(tenant).strip():
        return str(tenant).strip()
    return "default"


def _format_ts(value: Any) -> str | None:
    """Convert a datetime or timestamp value to an ISO 8601 string."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _serialize_region(row: Any) -> dict[str, Any]:
    """Serialize a geo_regions PyDAL row to a JSON-safe dict."""
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "village_id": getattr(row, "village_id", None),
        "code": row.code,
        "name": row.name,
        "provider": row.provider,
        "latitude": row.latitude,
        "longitude": row.longitude,
        "description": row.description,
        "is_active": bool(row.is_active) if row.is_active is not None else True,
        "created_at": _format_ts(getattr(row, "created_at", None)),
        "updated_at": _format_ts(getattr(row, "updated_at", None)),
    }


def _serialize_location(row: Any) -> dict[str, Any]:
    """Serialize a geo_locations PyDAL row to a JSON-safe dict."""
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "village_id": getattr(row, "village_id", None),
        "organization_id": row.organization_id,
        "region_code": row.region_code,
        "name": row.name,
        "city": row.city,
        "state": row.state,
        "country": row.country,
        "address": row.address,
        "latitude": row.latitude,
        "longitude": row.longitude,
        "description": row.description,
        "is_active": bool(row.is_active) if row.is_active is not None else True,
        "created_at": _format_ts(getattr(row, "created_at", None)),
        "updated_at": _format_ts(getattr(row, "updated_at", None)),
    }


def _parse_coord(value: Any, field_name: str, min_val: float, max_val: float) -> float:
    """Parse and range-validate a latitude or longitude value."""
    try:
        num = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a valid number") from exc
    if not (min_val <= num <= max_val):
        raise ValueError(f"{field_name} must be between {min_val} and {max_val}")
    return num


def _lookup_known_location(query: str) -> dict[str, Any] | None:
    """Check the built-in DC/city fallback dictionary for a matching location."""
    normalized = " ".join(query.lower().strip().split())
    if not normalized:
        return None

    # Exact match first, then comma-separated segments, then whole-phrase match.
    candidates = [normalized] + [
        part.strip() for part in normalized.split(",") if part.strip()
    ]
    for candidate in candidates:
        if candidate in _KNOWN_LOCATIONS:
            lat, lon, display_name, city, state, country = _KNOWN_LOCATIONS[candidate]
            return {
                "latitude": lat,
                "longitude": lon,
                "display_name": display_name,
                "city": city,
                "state": state,
                "country": country,
            }

    for key, (lat, lon, display_name, city, state, country) in _KNOWN_LOCATIONS.items():
        if re.search(rf"\b{re.escape(key)}\b", normalized):
            return {
                "latitude": lat,
                "longitude": lon,
                "display_name": display_name,
                "city": city,
                "state": state,
                "country": country,
            }
    return None


def _nominatim_geocode_sync(query: str) -> dict[str, Any] | None:
    """Query OpenStreetMap Nominatim synchronously with a short timeout."""
    params = urllib.parse.urlencode(
        {"format": "json", "limit": 1, "addressdetails": 1, "q": query}
    )
    url = f"{_NOMINATIM_URL}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": _NOMINATIM_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:  # noqa: S310 # nosec B310
            payload = json.loads(resp.read().decode("utf-8"))
    except (
        urllib.error.URLError,
        TimeoutError,
        ValueError,
        json.JSONDecodeError,
        OSError,
    ) as exc:
        logger.warning("Nominatim geocoding failed for %r: %s", query, exc)
        return None

    if not payload or not isinstance(payload, list):
        return None

    hit = payload[0]
    try:
        lat = float(hit["lat"])
        lon = float(hit["lon"])
    except (KeyError, TypeError, ValueError):
        return None

    addr = hit.get("address") or {}
    city = (
        addr.get("city")
        or addr.get("town")
        or addr.get("village")
        or addr.get("municipality")
    )
    return {
        "latitude": lat,
        "longitude": lon,
        "display_name": hit.get("display_name") or query,
        "city": city,
        "state": addr.get("state"),
        "country": addr.get("country"),
    }


async def _geocode_query(query: str) -> dict[str, Any] | None:
    """Resolve a human-readable location string to coordinates and address metadata."""
    cleaned = (query or "").strip()
    if not cleaned:
        return None

    known = _lookup_known_location(cleaned)
    if known is not None:
        return known

    return await asyncio.to_thread(_nominatim_geocode_sync, cleaned)


def _build_location_query(
    name: str | None,
    address: str | None,
    city: str | None,
    state: str | None,
    country: str | None,
) -> str:
    """Construct the best human-readable geocoding query from location fields."""
    parts = [
        p.strip()
        for p in (address, city, state, country)
        if isinstance(p, str) and p.strip()
    ]
    if parts:
        return ", ".join(parts)
    if isinstance(name, str) and name.strip():
        return name.strip()
    return ""


# ---------------------------------------------------------------------------
# Geocoding endpoint
# ---------------------------------------------------------------------------


@bp.route("/geocode", methods=["POST"])
@login_required
@require_scope("infrastructure:read")
async def geocode_location():
    """Geocode a human-readable location query into latitude/longitude."""
    try:
        data = await request.get_json()
        if not data or not isinstance(data, dict):
            return jsonify({"error": "Request body required"}), 400

        query = str(data.get("query") or "").strip()
        if not query:
            return jsonify({"error": "query is required"}), 400

        result = await _geocode_query(query)
        if result is None:
            return jsonify(
                {"error": f"Could not resolve coordinates for '{query}'"}
            ), 404

        return jsonify(result), 200
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to geocode location", 500)


# ---------------------------------------------------------------------------
# Geo Regions CRUD
# ---------------------------------------------------------------------------


@bp.route("/regions", methods=["GET"])
@login_required
@require_scope("infrastructure:read")
def list_regions():
    """List geographic regions for the current tenant."""
    try:
        db = current_app.db
        tenant_id = _get_tenant_id()
        rows = db(db.geo_regions.tenant_id == tenant_id).select(
            orderby=db.geo_regions.name
        )
        regions = [_serialize_region(row) for row in rows]
        return jsonify({"regions": regions, "total": len(regions)}), 200
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to list geo regions", 500)


@bp.route("/regions", methods=["POST"])
@login_required
@require_scope("infrastructure:write")
async def create_region():
    """Create a geographic region."""
    try:
        data = await request.get_json()
        if not data or not isinstance(data, dict):
            return jsonify({"error": "Request body required"}), 400

        code = str(data.get("code") or "").strip()
        name = str(data.get("name") or "").strip()
        if not code or not name:
            return jsonify({"error": "code and name are required"}), 400

        lat = (
            _parse_coord(data["latitude"], "latitude", -90.0, 90.0)
            if data.get("latitude") is not None
            else None
        )
        lon = (
            _parse_coord(data["longitude"], "longitude", -180.0, 180.0)
            if data.get("longitude") is not None
            else None
        )

        db = current_app.db
        tenant_id = _get_tenant_id()
        now = datetime.now(UTC)
        overwrite = bool(data.get("overwrite", False))

        existing = (
            db((db.geo_regions.code == code) & (db.geo_regions.tenant_id == tenant_id))
            .select()
            .first()
        )
        if existing:
            if not overwrite:
                return (
                    jsonify(
                        {
                            "error": f"Geo region '{code}' already exists in this tenant",
                            "code": "DUPLICATE_RESOURCE",
                            "existing_id": existing.id,
                        }
                    ),
                    409,
                )
            db(
                (db.geo_regions.id == existing.id)
                & (db.geo_regions.tenant_id == tenant_id)
            ).update(
                name=name,
                provider=data.get("provider"),
                latitude=lat,
                longitude=lon,
                description=data.get("description"),
                is_active=bool(data["is_active"]) if "is_active" in data else True,
                updated_at=now,
            )
            db.commit()
            row = (
                db(
                    (db.geo_regions.id == existing.id)
                    & (db.geo_regions.tenant_id == tenant_id)
                )
                .select()
                .first()
            )
            return jsonify(_serialize_region(row)), 200

        region_id = db.geo_regions.insert(
            tenant_id=tenant_id,
            village_id=mint_village_id(tenant_id),
            code=code,
            name=name,
            provider=data.get("provider"),
            latitude=lat,
            longitude=lon,
            description=data.get("description"),
            is_active=bool(data["is_active"]) if "is_active" in data else True,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        row = (
            db(
                (db.geo_regions.id == region_id)
                & (db.geo_regions.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        return jsonify(_serialize_region(row)), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to create geo region", 500)


@bp.route("/regions/<int:region_id>", methods=["PUT"])
@login_required
@require_scope("infrastructure:write")
async def update_region(region_id: int):
    """Update a geographic region scoped to the current tenant."""
    try:
        data = await request.get_json()
        if not data or not isinstance(data, dict):
            return jsonify({"error": "Request body required"}), 400

        db = current_app.db
        tenant_id = _get_tenant_id()
        row = (
            db(
                (db.geo_regions.id == region_id)
                & (db.geo_regions.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not row:
            return jsonify({"error": "Geo region not found"}), 404

        updates: dict[str, Any] = {"updated_at": datetime.now(UTC)}
        if "code" in data:
            code = str(data["code"] or "").strip()
            if not code:
                return jsonify({"error": "code cannot be empty"}), 400
            updates["code"] = code
        if "name" in data:
            name = str(data["name"] or "").strip()
            if not name:
                return jsonify({"error": "name cannot be empty"}), 400
            updates["name"] = name
        if "provider" in data:
            updates["provider"] = data["provider"]
        if "latitude" in data:
            updates["latitude"] = (
                _parse_coord(data["latitude"], "latitude", -90.0, 90.0)
                if data["latitude"] is not None
                else None
            )
        if "longitude" in data:
            updates["longitude"] = (
                _parse_coord(data["longitude"], "longitude", -180.0, 180.0)
                if data["longitude"] is not None
                else None
            )
        if "description" in data:
            updates["description"] = data["description"]
        if "is_active" in data:
            updates["is_active"] = bool(data["is_active"])

        db(
            (db.geo_regions.id == region_id) & (db.geo_regions.tenant_id == tenant_id)
        ).update(**updates)
        db.commit()

        updated = (
            db(
                (db.geo_regions.id == region_id)
                & (db.geo_regions.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        return jsonify(_serialize_region(updated)), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to update geo region", 500)


@bp.route("/regions/<int:region_id>", methods=["DELETE"])
@login_required
@require_scope("infrastructure:write")
def delete_region(region_id: int):
    """Delete a geographic region scoped to the current tenant."""
    try:
        db = current_app.db
        tenant_id = _get_tenant_id()
        deleted = db(
            (db.geo_regions.id == region_id) & (db.geo_regions.tenant_id == tenant_id)
        ).delete()
        if not deleted:
            return jsonify({"error": "Geo region not found"}), 404
        db.commit()
        return jsonify({"deleted": True, "id": region_id}), 200
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to delete geo region", 500)


# ---------------------------------------------------------------------------
# Geo Locations CRUD
# ---------------------------------------------------------------------------


@bp.route("/locations", methods=["GET"])
@login_required
@require_scope("infrastructure:read")
def list_locations():
    """List geographic locations for the current tenant."""
    try:
        db = current_app.db
        tenant_id = _get_tenant_id()
        rows = db(db.geo_locations.tenant_id == tenant_id).select(
            orderby=db.geo_locations.name
        )
        locations = [_serialize_location(row) for row in rows]
        return jsonify({"locations": locations, "total": len(locations)}), 200
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to list geo locations", 500)


@bp.route("/locations", methods=["POST"])
@login_required
@require_scope("infrastructure:write")
async def create_location():
    """Create a geographic location, auto-geocoding coordinates when omitted."""
    try:
        data = await request.get_json()
        if not data or not isinstance(data, dict):
            return jsonify({"error": "Request body required"}), 400

        name = str(data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name is required"}), 400

        db = current_app.db
        tenant_id = _get_tenant_id()
        overwrite = bool(data.get("overwrite", False))
        org_id = data.get("organization_id")
        dup_query = (db.geo_locations.name == name) & (
            db.geo_locations.tenant_id == tenant_id
        )
        if org_id is not None:
            dup_query &= db.geo_locations.organization_id == org_id
        else:
            dup_query &= db.geo_locations.organization_id == None  # noqa: E711
        existing = db(dup_query).select().first()
        if existing and not overwrite:
            scope_label = "organization" if org_id is not None else "tenant"
            return (
                jsonify(
                    {
                        "error": f"Geo location '{name}' already exists in this {scope_label}",
                        "code": "DUPLICATE_RESOURCE",
                        "existing_id": existing.id,
                    }
                ),
                409,
            )

        lat = (
            _parse_coord(data["latitude"], "latitude", -90.0, 90.0)
            if data.get("latitude") is not None
            else None
        )
        lon = (
            _parse_coord(data["longitude"], "longitude", -180.0, 180.0)
            if data.get("longitude") is not None
            else None
        )

        city = data.get("city")
        state = data.get("state")
        country = data.get("country")
        address = data.get("address")

        if lat is None or lon is None:
            query = _build_location_query(name, address, city, state, country)
            geocoded = await _geocode_query(query)
            if geocoded is None:
                return (
                    jsonify(
                        {
                            "error": (
                                "Unable to resolve coordinates automatically; "
                                "please provide latitude and longitude or a more specific city/address"
                            )
                        }
                    ),
                    400,
                )
            lat = geocoded["latitude"] if lat is None else lat
            lon = geocoded["longitude"] if lon is None else lon
            city = city or geocoded.get("city")
            state = state or geocoded.get("state")
            country = country or geocoded.get("country")

        now = datetime.now(UTC)

        if existing:
            db(
                (db.geo_locations.id == existing.id)
                & (db.geo_locations.tenant_id == tenant_id)
            ).update(
                organization_id=data.get("organization_id"),
                region_code=data.get("region_code"),
                city=city,
                state=state,
                country=country,
                address=address,
                latitude=lat,
                longitude=lon,
                description=data.get("description"),
                is_active=bool(data["is_active"]) if "is_active" in data else True,
                updated_at=now,
            )
            db.commit()
            row = (
                db(
                    (db.geo_locations.id == existing.id)
                    & (db.geo_locations.tenant_id == tenant_id)
                )
                .select()
                .first()
            )
            return jsonify(_serialize_location(row)), 200

        location_id = db.geo_locations.insert(
            tenant_id=tenant_id,
            village_id=mint_village_id(tenant_id),
            organization_id=data.get("organization_id"),
            region_code=data.get("region_code"),
            name=name,
            city=city,
            state=state,
            country=country,
            address=address,
            latitude=lat,
            longitude=lon,
            description=data.get("description"),
            is_active=bool(data["is_active"]) if "is_active" in data else True,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        row = (
            db(
                (db.geo_locations.id == location_id)
                & (db.geo_locations.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        return jsonify(_serialize_location(row)), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to create geo location", 500)


@bp.route("/locations/<int:location_id>", methods=["PUT"])
@login_required
@require_scope("infrastructure:write")
async def update_location(location_id: int):
    """Update a geographic location scoped to the current tenant."""
    try:
        data = await request.get_json()
        if not data or not isinstance(data, dict):
            return jsonify({"error": "Request body required"}), 400

        db = current_app.db
        tenant_id = _get_tenant_id()
        row = (
            db(
                (db.geo_locations.id == location_id)
                & (db.geo_locations.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not row:
            return jsonify({"error": "Geo location not found"}), 404

        updates: dict[str, Any] = {"updated_at": datetime.now(UTC)}
        if "name" in data:
            name = str(data["name"] or "").strip()
            if not name:
                return jsonify({"error": "name cannot be empty"}), 400
            updates["name"] = name
        for field in (
            "organization_id",
            "region_code",
            "city",
            "state",
            "country",
            "address",
            "description",
        ):
            if field in data:
                updates[field] = data[field]
        if "is_active" in data:
            updates["is_active"] = bool(data["is_active"])

        lat = (
            _parse_coord(data["latitude"], "latitude", -90.0, 90.0)
            if "latitude" in data and data["latitude"] is not None
            else (None if "latitude" in data else row.latitude)
        )
        lon = (
            _parse_coord(data["longitude"], "longitude", -180.0, 180.0)
            if "longitude" in data and data["longitude"] is not None
            else (None if "longitude" in data else row.longitude)
        )

        if lat is None or lon is None:
            query = _build_location_query(
                updates.get("name", row.name),
                updates.get("address", row.address),
                updates.get("city", row.city),
                updates.get("state", row.state),
                updates.get("country", row.country),
            )
            geocoded = await _geocode_query(query)
            if geocoded is None:
                return (
                    jsonify(
                        {
                            "error": (
                                "Unable to resolve coordinates automatically; "
                                "please provide latitude and longitude or a more specific city/address"
                            )
                        }
                    ),
                    400,
                )
            lat = geocoded["latitude"] if lat is None else lat
            lon = geocoded["longitude"] if lon is None else lon

        updates["latitude"] = lat
        updates["longitude"] = lon

        db(
            (db.geo_locations.id == location_id)
            & (db.geo_locations.tenant_id == tenant_id)
        ).update(**updates)
        db.commit()

        updated = (
            db(
                (db.geo_locations.id == location_id)
                & (db.geo_locations.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        return jsonify(_serialize_location(updated)), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to update geo location", 500)


@bp.route("/locations/<int:location_id>", methods=["DELETE"])
@login_required
@require_scope("infrastructure:write")
def delete_location(location_id: int):
    """Delete a geographic location scoped to the current tenant."""
    try:
        db = current_app.db
        tenant_id = _get_tenant_id()
        deleted = db(
            (db.geo_locations.id == location_id)
            & (db.geo_locations.tenant_id == tenant_id)
        ).delete()
        if not deleted:
            return jsonify({"error": "Geo location not found"}), 404
        db.commit()
        return jsonify({"deleted": True, "id": location_id}), 200
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to delete geo location", 500)
