import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Map as MapLibreMap,
  Marker as MapLibreMarker,
  NavigationControl,
  Popup as MapLibrePopup,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import {
  Crosshair,
  Globe,
  Map as MapIcon,
  MapPin,
  Plus,
  Search,
  Trash2,
  X,
} from "lucide-react";
import toast from "react-hot-toast";
import api from "@/lib/api";
import Button from "@/components/Button";
import Card, { CardHeader, CardContent } from "@/components/Card";
import SearchableSelect from "@/components/SearchableSelect";

// EOX Sentinel-2 cloudless satellite basemap — no API key required.
const SATELLITE_TILE_URL =
  "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2020_3857/default/g/{z}/{y}/{x}.jpg";
const SATELLITE_ATTRIBUTION =
  "&copy; EOX IT Services GmbH — Sentinel-2 cloudless";

// World view — not scoped to any single region/customer.
const DEFAULT_CENTER: [number, number] = [0, 20];
const DEFAULT_ZOOM = 1.4;

interface RegionInfo {
  lng: number;
  lat: number;
  label: string;
}

// Fallback coordinates for cloud entities/networks that only carry a region code
// with no precise metadata.location or defined geo_location.
const REGION_COORDS: Record<string, RegionInfo> = {
  // AWS
  "us-east-1": {
    lng: -77.4874,
    lat: 39.0438,
    label: "AWS US East (N. Virginia)",
  },
  "us-east-2": { lng: -82.9988, lat: 40.4173, label: "AWS US East (Ohio)" },
  "us-west-1": {
    lng: -121.8863,
    lat: 37.3382,
    label: "AWS US West (N. California)",
  },
  "us-west-2": { lng: -120.5542, lat: 43.8041, label: "AWS US West (Oregon)" },
  "eu-west-1": { lng: -8.2439, lat: 53.4129, label: "AWS EU (Ireland)" },
  "eu-central-1": { lng: 8.6821, lat: 50.1109, label: "AWS EU (Frankfurt)" },
  "ap-southeast-1": {
    lng: 103.8198,
    lat: 1.3521,
    label: "AWS Asia Pacific (Singapore)",
  },
  "ap-northeast-1": {
    lng: 139.6917,
    lat: 35.6895,
    label: "AWS Asia Pacific (Tokyo)",
  },
  // GCP
  "us-central1": {
    lng: -93.6091,
    lat: 41.5868,
    label: "GCP US Central (Iowa)",
  },
  "us-east1": {
    lng: -81.1637,
    lat: 33.8361,
    label: "GCP US East (S. Carolina)",
  },
  "europe-west1": {
    lng: 4.3517,
    lat: 50.8503,
    label: "GCP Europe West (Belgium)",
  },
  "europe-west3": {
    lng: 8.6821,
    lat: 50.1109,
    label: "GCP Europe West (Frankfurt)",
  },
  "asia-east1": { lng: 121.5654, lat: 25.033, label: "GCP Asia East (Taiwan)" },
  "asia-southeast1": {
    lng: 103.8198,
    lat: 1.3521,
    label: "GCP Asia Southeast (Singapore)",
  },
  // Azure
  eastus: { lng: -78.4767, lat: 37.4316, label: "Azure East US (Virginia)" },
  eastus2: { lng: -78.6569, lat: 37.5407, label: "Azure East US 2 (Virginia)" },
  westus2: {
    lng: -120.7401,
    lat: 47.7511,
    label: "Azure West US 2 (Washington)",
  },
  westeurope: {
    lng: 4.9041,
    lat: 52.3676,
    label: "Azure West Europe (Netherlands)",
  },
  uksouth: { lng: -0.1278, lat: 51.5074, label: "Azure UK South (London)" },
  southeastasia: {
    lng: 103.8198,
    lat: 1.3521,
    label: "Azure Southeast Asia (Singapore)",
  },
};

const PROVIDER_OPTIONS = [
  { value: "custom", label: "Custom" },
  { value: "on-prem", label: "On-Premises" },
  { value: "aws", label: "AWS" },
  { value: "gcp", label: "GCP" },
  { value: "azure", label: "Azure" },
];

interface EntityLocation {
  city?: string | null;
  state?: string | null;
  country?: string | null;
  latitude?: number | null;
  longitude?: number | null;
}

interface EntityMetadata {
  location?: EntityLocation | null;
}

interface GeoEntity {
  id: number;
  name: string;
  type: string;
  sub_type?: string | null;
  region?: string | null;
  location?: string | null;
  metadata?: EntityMetadata | null;
}

interface GeoNetwork {
  id: number;
  name: string;
  network_type: string;
  cidr?: string | null;
  region?: string | null;
  location?: string | null;
}

interface GeoRegion {
  id: number;
  code: string;
  name: string;
  provider?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  description?: string | null;
}

interface GeoLocation {
  id: number;
  name: string;
  region_code?: string | null;
  city?: string | null;
  state?: string | null;
  country?: string | null;
  address?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  description?: string | null;
}

interface ExactMarker {
  entity: GeoEntity;
  lng: number;
  lat: number;
  location: EntityLocation;
}

interface DefinedLocationMarker {
  location: GeoLocation;
  lng: number;
  lat: number;
  entityCount: number;
  networkCount: number;
}

interface RegionApproxItem {
  name: string;
  detail: string;
}

interface RegionApproxGroup {
  region: string;
  info: RegionInfo;
  items: RegionApproxItem[];
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function normalizeList<T>(data: unknown, key: string): T[] {
  if (Array.isArray(data)) return data as T[];
  if (data && typeof data === "object") {
    const record = data as Record<string, unknown>;
    if (Array.isArray(record.items)) return record.items as T[];
    if (Array.isArray(record[key])) return record[key] as T[];
  }
  return [];
}

function buildExactPopup(item: ExactMarker): HTMLDivElement {
  const container = document.createElement("div");
  container.className = "text-sm min-w-[180px] max-w-[260px]";

  const title = document.createElement("div");
  title.className = "font-semibold text-sky-400 mb-1";
  title.textContent = item.entity.name || "Unnamed entity";
  container.appendChild(title);

  const placeParts = [
    item.location.city,
    item.location.state,
    item.location.country,
  ].filter((part): part is string => Boolean(part && part.trim()));
  if (placeParts.length > 0) {
    const place = document.createElement("div");
    place.className = "text-slate-300";
    place.textContent = placeParts.join(", ");
    container.appendChild(place);
  }

  const typeLine = document.createElement("div");
  typeLine.className = "text-slate-400 text-xs mt-1";
  typeLine.textContent = item.entity.sub_type
    ? `${item.entity.type} / ${item.entity.sub_type}`
    : item.entity.type;
  container.appendChild(typeLine);

  return container;
}

function buildLocationPopup(item: DefinedLocationMarker): HTMLDivElement {
  const container = document.createElement("div");
  container.className = "text-sm min-w-[190px] max-w-[260px]";

  const title = document.createElement("div");
  title.className = "font-semibold text-emerald-400 mb-1";
  title.textContent = item.location.name;
  container.appendChild(title);

  const placeParts = [
    item.location.city,
    item.location.state,
    item.location.country,
  ].filter((part): part is string => Boolean(part && part.trim()));
  if (placeParts.length > 0) {
    const place = document.createElement("div");
    place.className = "text-slate-300";
    place.textContent = placeParts.join(", ");
    container.appendChild(place);
  }

  if (item.location.region_code) {
    const region = document.createElement("div");
    region.className = "text-slate-400 text-xs mt-0.5";
    region.textContent = `Region: ${item.location.region_code}`;
    container.appendChild(region);
  }

  const coords = document.createElement("div");
  coords.className = "text-slate-500 text-xs mt-0.5 font-mono";
  coords.textContent = `${item.lat.toFixed(4)}, ${item.lng.toFixed(4)}`;
  container.appendChild(coords);

  const counts = document.createElement("div");
  counts.className =
    "text-slate-300 text-xs mt-1.5 pt-1.5 border-t border-slate-700";
  counts.textContent = `${item.entityCount} entit${item.entityCount === 1 ? "y" : "ies"} · ${item.networkCount} network${item.networkCount === 1 ? "" : "s"}`;
  container.appendChild(counts);

  return container;
}

const MAX_LISTED_ITEMS_PER_REGION = 25;

function buildRegionPopup(group: RegionApproxGroup): HTMLDivElement {
  const container = document.createElement("div");
  container.className = "text-sm min-w-[180px] max-w-[260px]";

  const title = document.createElement("div");
  title.className = "font-semibold text-amber-400 mb-1";
  title.textContent = group.info.label;
  container.appendChild(title);

  const note = document.createElement("div");
  note.className = "text-amber-500/80 text-xs italic mb-1";
  note.textContent = "Approximate — region-level location only";
  container.appendChild(note);

  const summary = document.createElement("div");
  summary.className = "text-slate-300 mb-1";
  summary.textContent = `${group.items.length} resource${group.items.length === 1 ? "" : "s"}`;
  container.appendChild(summary);

  const list = document.createElement("ul");
  list.className = "space-y-0.5 max-h-40 overflow-y-auto";
  for (const item of group.items.slice(0, MAX_LISTED_ITEMS_PER_REGION)) {
    const li = document.createElement("li");
    li.className = "text-slate-200 truncate";
    li.textContent = item.detail ? `${item.name} (${item.detail})` : item.name;
    list.appendChild(li);
  }
  if (group.items.length > MAX_LISTED_ITEMS_PER_REGION) {
    const more = document.createElement("li");
    more.className = "text-slate-500 italic";
    more.textContent = `+ ${group.items.length - MAX_LISTED_ITEMS_PER_REGION} more`;
    list.appendChild(more);
  }
  container.appendChild(list);

  return container;
}

function getErrorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === "object") {
    const err = error as {
      response?: { data?: { error?: string; message?: string } };
      message?: string;
    };
    return (
      err.response?.data?.error ||
      err.response?.data?.message ||
      err.message ||
      fallback
    );
  }
  return fallback;
}

// Named MapPage (not Map) — a top-level `function Map()` here would shadow
// the global ES2015 Map class used below for regionGroupMap.
export default function MapPage() {
  const queryClient = useQueryClient();
  const formIdPrefix = useId();
  const mapContainerRef = useRef<HTMLDivElement | null>(null);
  const mapInstanceRef = useRef<MapLibreMap | null>(null);
  const markersRef = useRef<MapLibreMarker[]>([]);
  const [mapLoaded, setMapLoaded] = useState(false);

  // Management panel state
  const [isPanelOpen, setIsPanelOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<"locations" | "regions">(
    "locations",
  );
  const [pickTarget, setPickTarget] = useState<"location" | "region" | null>(
    null,
  );

  // Add Location form state
  const [locName, setLocName] = useState("");
  const [locRegionCode, setLocRegionCode] = useState("");
  const [locCity, setLocCity] = useState("");
  const [locState, setLocState] = useState("");
  const [locCountry, setLocCountry] = useState("");
  const [locAddress, setLocAddress] = useState("");
  const [locLat, setLocLat] = useState("");
  const [locLng, setLocLng] = useState("");
  const [locDescription, setLocDescription] = useState("");
  const [isGeocoding, setIsGeocoding] = useState(false);

  // Add Region form state
  const [regCode, setRegCode] = useState("");
  const [regName, setRegName] = useState("");
  const [regProvider, setRegProvider] = useState("custom");
  const [regLat, setRegLat] = useState("");
  const [regLng, setRegLng] = useState("");
  const [regDescription, setRegDescription] = useState("");

  const { data: entitiesData, isLoading: entitiesLoading } = useQuery<unknown>({
    queryKey: ["map-entities"],
    queryFn: () => api.getEntities({ per_page: 1000 }),
  });

  const { data: regionsData, isLoading: regionsLoading } = useQuery<unknown>({
    queryKey: ["geo-regions"],
    queryFn: () => api.getGeoRegions(),
  });

  const { data: locationsData, isLoading: locationsLoading } =
    useQuery<unknown>({
      queryKey: ["geo-locations"],
      queryFn: () => api.getGeoLocations(),
    });

  const { data: networksData, isLoading: networksLoading } = useQuery<unknown>({
    queryKey: ["map-networks"],
    queryFn: () => api.listNetworks({}),
  });

  const isLoading =
    entitiesLoading || regionsLoading || locationsLoading || networksLoading;

  const entities = useMemo(
    () => normalizeList<GeoEntity>(entitiesData, "entities"),
    [entitiesData],
  );
  const geoRegions = useMemo(
    () => normalizeList<GeoRegion>(regionsData, "regions"),
    [regionsData],
  );
  const geoLocations = useMemo(
    () => normalizeList<GeoLocation>(locationsData, "locations"),
    [locationsData],
  );
  const networks = useMemo(
    () => normalizeList<GeoNetwork>(networksData, "networks"),
    [networksData],
  );

  // Merge built-in REGION_COORDS with user-defined geo_regions
  const regionCoordsMap = useMemo(() => {
    const merged: Record<string, RegionInfo> = { ...REGION_COORDS };
    for (const reg of geoRegions) {
      const code = reg.code?.trim().toLowerCase();
      if (
        code &&
        isFiniteNumber(reg.latitude) &&
        isFiniteNumber(reg.longitude)
      ) {
        merged[code] = {
          lat: reg.latitude,
          lng: reg.longitude,
          label: reg.name ? `${reg.name} (${reg.code})` : reg.code,
        };
      }
    }
    return merged;
  }, [geoRegions]);

  const regionSelectOptions = useMemo(() => {
    const seen = new Set<string>();
    const opts: Array<{ value: string; label: string }> = [];
    for (const reg of geoRegions) {
      const code = reg.code?.trim();
      if (code && !seen.has(code.toLowerCase())) {
        seen.add(code.toLowerCase());
        opts.push({
          value: code,
          label: reg.name ? `${code} — ${reg.name}` : code,
        });
      }
    }
    for (const [code, info] of Object.entries(REGION_COORDS)) {
      if (!seen.has(code.toLowerCase())) {
        seen.add(code.toLowerCase());
        opts.push({ value: code, label: `${code} — ${info.label}` });
      }
    }
    return opts;
  }, [geoRegions]);

  const markerData = useMemo(() => {
    const exact: ExactMarker[] = [];
    const regionGroupMap = new Map<string, RegionApproxGroup>();
    const definedLocationsMap = new Map<number, DefinedLocationMarker>();
    const locationLookup = new Map<string, DefinedLocationMarker>();
    let skipped = 0;

    for (const loc of geoLocations) {
      if (!isFiniteNumber(loc.latitude) || !isFiniteNumber(loc.longitude))
        continue;
      const entry: DefinedLocationMarker = {
        location: loc,
        lat: loc.latitude,
        lng: loc.longitude,
        entityCount: 0,
        networkCount: 0,
      };
      definedLocationsMap.set(loc.id, entry);

      const nameKey = loc.name?.trim().toLowerCase();
      if (nameKey) locationLookup.set(nameKey, entry);

      const cityKey = loc.city?.trim().toLowerCase();
      if (cityKey && !locationLookup.has(cityKey)) {
        locationLookup.set(cityKey, entry);
      }

      if (loc.city?.trim() && loc.state?.trim()) {
        const cityStateKey = `${loc.city.trim().toLowerCase()}, ${loc.state.trim().toLowerCase()}`;
        if (!locationLookup.has(cityStateKey)) {
          locationLookup.set(cityStateKey, entry);
        }
      }
    }

    const matchGeoLocation = (
      raw?: string | null,
    ): DefinedLocationMarker | undefined => {
      const key = raw?.trim().toLowerCase();
      return key ? locationLookup.get(key) : undefined;
    };

    const addRegionItem = (
      regionRaw: string | null | undefined,
      item: RegionApproxItem,
    ): boolean => {
      const regionCode = regionRaw?.trim().toLowerCase();
      const info = regionCode ? regionCoordsMap[regionCode] : undefined;
      if (!regionCode || !info) return false;
      let group = regionGroupMap.get(regionCode);
      if (!group) {
        group = { region: regionCode, info, items: [] };
        regionGroupMap.set(regionCode, group);
      }
      group.items.push(item);
      return true;
    };

    for (const entity of entities) {
      const location = entity.metadata?.location;
      const lat = location?.latitude;
      const lng = location?.longitude;

      if (isFiniteNumber(lat) && isFiniteNumber(lng)) {
        exact.push({ entity, lat, lng, location: location ?? {} });
        continue;
      }

      const cityState =
        location?.city && location?.state
          ? `${location.city}, ${location.state}`
          : null;
      const matchedLoc =
        matchGeoLocation(entity.location) ||
        matchGeoLocation(cityState) ||
        matchGeoLocation(location?.city);
      if (matchedLoc) {
        matchedLoc.entityCount += 1;
        continue;
      }

      const detail = entity.sub_type
        ? `${entity.type} / ${entity.sub_type}`
        : entity.type;
      if (addRegionItem(entity.region, { name: entity.name, detail })) {
        continue;
      }

      skipped += 1;
    }

    for (const net of networks) {
      const matchedLoc = matchGeoLocation(net.location);
      if (matchedLoc) {
        matchedLoc.networkCount += 1;
        continue;
      }

      const detail = net.cidr
        ? `${net.network_type} · ${net.cidr}`
        : net.network_type;
      if (addRegionItem(net.region, { name: net.name, detail })) {
        continue;
      }

      skipped += 1;
    }

    return {
      exact,
      definedLocations: Array.from(definedLocationsMap.values()),
      regionGroups: Array.from(regionGroupMap.values()),
      skipped,
    };
  }, [entities, networks, geoLocations, regionCoordsMap]);

  // Mutations
  const createLocationMutation = useMutation({
    mutationFn: (payload: Parameters<typeof api.createGeoLocation>[0]) =>
      api.createGeoLocation(payload),
    onSuccess: () => {
      toast.success("Location created");
      queryClient.invalidateQueries({ queryKey: ["geo-locations"] });
      setLocName("");
      setLocRegionCode("");
      setLocCity("");
      setLocState("");
      setLocCountry("");
      setLocAddress("");
      setLocLat("");
      setLocLng("");
      setLocDescription("");
      setPickTarget(null);
    },
    onError: (err, payload) => {
      if (
        (err as { response?: { status?: number } })?.response?.status === 409
      ) {
        const msg = getErrorMessage(err, "A matching location already exists.");
        if (
          window.confirm(
            `${msg}\n\nDo you want to overwrite the existing location?`,
          )
        ) {
          createLocationMutation.mutate({ ...payload, overwrite: true });
        }
        return;
      }
      toast.error(getErrorMessage(err, "Failed to create location"));
    },
  });

  const deleteLocationMutation = useMutation({
    mutationFn: (id: number) => api.deleteGeoLocation(id),
    onSuccess: () => {
      toast.success("Location deleted");
      queryClient.invalidateQueries({ queryKey: ["geo-locations"] });
    },
    onError: (err) =>
      toast.error(getErrorMessage(err, "Failed to delete location")),
  });

  const createRegionMutation = useMutation({
    mutationFn: (payload: Parameters<typeof api.createGeoRegion>[0]) =>
      api.createGeoRegion(payload),
    onSuccess: () => {
      toast.success("Region created");
      queryClient.invalidateQueries({ queryKey: ["geo-regions"] });
      setRegCode("");
      setRegName("");
      setRegProvider("custom");
      setRegLat("");
      setRegLng("");
      setRegDescription("");
      setPickTarget(null);
    },
    onError: (err, payload) => {
      if (
        (err as { response?: { status?: number } })?.response?.status === 409
      ) {
        const msg = getErrorMessage(err, "A matching region already exists.");
        if (
          window.confirm(
            `${msg}\n\nDo you want to overwrite the existing region?`,
          )
        ) {
          createRegionMutation.mutate({ ...payload, overwrite: true });
        }
        return;
      }
      toast.error(getErrorMessage(err, "Failed to create region"));
    },
  });

  const deleteRegionMutation = useMutation({
    mutationFn: (id: number) => api.deleteGeoRegion(id),
    onSuccess: () => {
      toast.success("Region deleted");
      queryClient.invalidateQueries({ queryKey: ["geo-regions"] });
    },
    onError: (err) =>
      toast.error(getErrorMessage(err, "Failed to delete region")),
  });

  // Create the map instance once.
  useEffect(() => {
    if (!mapContainerRef.current || mapInstanceRef.current) return;

    const map = new MapLibreMap({
      container: mapContainerRef.current,
      style: {
        version: 8,
        sources: {
          satellite: {
            type: "raster",
            tiles: [SATELLITE_TILE_URL],
            tileSize: 256,
            attribution: SATELLITE_ATTRIBUTION,
          },
        },
        layers: [{ id: "satellite", type: "raster", source: "satellite" }],
      },
      center: DEFAULT_CENTER,
      zoom: DEFAULT_ZOOM,
    });

    map.addControl(new NavigationControl(), "top-right");
    map.on("load", () => setMapLoaded(true));
    mapInstanceRef.current = map;

    return () => {
      markersRef.current.forEach((marker) => marker.remove());
      markersRef.current = [];
      map.remove();
      mapInstanceRef.current = null;
    };
  }, []);

  // Handle "Pick on Map" click listener & cursor
  useEffect(() => {
    const map = mapInstanceRef.current;
    if (!map || !mapLoaded) return;

    const canvas = map.getCanvas();
    canvas.style.cursor = pickTarget ? "crosshair" : "";

    if (!pickTarget) return;

    const handleMapClick = (e: { lngLat: { lat: number; lng: number } }) => {
      const latStr = e.lngLat.lat.toFixed(6);
      const lngStr = e.lngLat.lng.toFixed(6);
      if (pickTarget === "location") {
        setLocLat(latStr);
        setLocLng(lngStr);
      } else {
        setRegLat(latStr);
        setRegLng(lngStr);
      }
      setPickTarget(null);
      toast.success(`Picked coordinates: ${latStr}, ${lngStr}`);
    };

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setPickTarget(null);
      }
    };

    map.on("click", handleMapClick);
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      map.off("click", handleMapClick);
      window.removeEventListener("keydown", handleKeyDown);
      canvas.style.cursor = "";
    };
  }, [pickTarget, mapLoaded]);

  // Render markers whenever resolved data changes.
  useEffect(() => {
    const map = mapInstanceRef.current;
    if (!map || !mapLoaded) return;

    markersRef.current.forEach((marker) => marker.remove());
    markersRef.current = [];

    for (const locMarker of markerData.definedLocations) {
      const popup = new MapLibrePopup({
        offset: 16,
        closeButton: true,
        maxWidth: "260px",
      }).setDOMContent(buildLocationPopup(locMarker));
      const marker = new MapLibreMarker({ color: "#10b981" })
        .setLngLat([locMarker.lng, locMarker.lat])
        .setPopup(popup)
        .addTo(map);
      markersRef.current.push(marker);
    }

    for (const item of markerData.exact) {
      const popup = new MapLibrePopup({
        offset: 16,
        closeButton: true,
        maxWidth: "260px",
      }).setDOMContent(buildExactPopup(item));
      const marker = new MapLibreMarker({ color: "#0ea5e9" })
        .setLngLat([item.lng, item.lat])
        .setPopup(popup)
        .addTo(map);
      markersRef.current.push(marker);
    }

    for (const group of markerData.regionGroups) {
      const popup = new MapLibrePopup({
        offset: 16,
        closeButton: true,
        maxWidth: "260px",
      }).setDOMContent(buildRegionPopup(group));
      const marker = new MapLibreMarker({ color: "#f59e0b" })
        .setLngLat([group.info.lng, group.info.lat])
        .setPopup(popup)
        .addTo(map);
      markersRef.current.push(marker);
    }
  }, [markerData, mapLoaded]);

  const handleResolveCoordinates = async () => {
    const query =
      locAddress.trim() ||
      [locCity.trim(), locState.trim(), locCountry.trim()]
        .filter(Boolean)
        .join(", ") ||
      locName.trim();
    if (!query) {
      toast.error(
        "Enter an address, city/state/country, or name to resolve coordinates",
      );
      return;
    }
    setIsGeocoding(true);
    try {
      const res = await api.geocodeLocation(query);
      const lat = res?.latitude ?? res?.lat;
      const lng = res?.longitude ?? res?.lng;
      if (isFiniteNumber(lat) && isFiniteNumber(lng)) {
        setLocLat(String(lat));
        setLocLng(String(lng));
        toast.success(
          `Resolved: ${Number(lat).toFixed(4)}, ${Number(lng).toFixed(4)}`,
        );
      } else {
        toast.error("Could not resolve coordinates for that location");
      }
    } catch (err) {
      toast.error(getErrorMessage(err, "Failed to geocode location"));
    } finally {
      setIsGeocoding(false);
    }
  };

  const handleCreateLocation = (e: FormEvent) => {
    e.preventDefault();
    if (!locName.trim()) {
      toast.error("Location name is required");
      return;
    }
    const parsedLat = locLat.trim() !== "" ? Number(locLat) : undefined;
    const parsedLng = locLng.trim() !== "" ? Number(locLng) : undefined;
    if (
      (parsedLat !== undefined && !Number.isFinite(parsedLat)) ||
      (parsedLng !== undefined && !Number.isFinite(parsedLng))
    ) {
      toast.error("Latitude and longitude must be valid numbers");
      return;
    }

    createLocationMutation.mutate({
      name: locName.trim(),
      region_code: locRegionCode.trim() || undefined,
      city: locCity.trim() || undefined,
      state: locState.trim() || undefined,
      country: locCountry.trim() || undefined,
      address: locAddress.trim() || undefined,
      latitude: parsedLat,
      longitude: parsedLng,
      description: locDescription.trim() || undefined,
    });
  };

  const handleCreateRegion = (e: FormEvent) => {
    e.preventDefault();
    if (!regCode.trim() || !regName.trim()) {
      toast.error("Region code and name are required");
      return;
    }
    const parsedLat = regLat.trim() !== "" ? Number(regLat) : undefined;
    const parsedLng = regLng.trim() !== "" ? Number(regLng) : undefined;
    if (
      (parsedLat !== undefined && !Number.isFinite(parsedLat)) ||
      (parsedLng !== undefined && !Number.isFinite(parsedLng))
    ) {
      toast.error("Latitude and longitude must be valid numbers");
      return;
    }

    createRegionMutation.mutate({
      code: regCode.trim(),
      name: regName.trim(),
      provider: regProvider || "custom",
      latitude: parsedLat,
      longitude: parsedLng,
      description: regDescription.trim() || undefined,
    });
  };

  const regionApproxCount = markerData.regionGroups.reduce(
    (sum, g) => sum + g.items.length,
    0,
  );
  const hasMarkers =
    markerData.definedLocations.length > 0 ||
    markerData.exact.length > 0 ||
    regionApproxCount > 0;

  return (
    <div className="p-8 relative">
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold text-white flex items-center gap-3">
            <MapIcon className="w-8 h-8" />
            Resource Map
          </h1>
          <p className="text-slate-400 mt-2">
            Geographic view of entities, networks, and defined locations
          </p>
        </div>

        <Button
          variant={isPanelOpen ? "secondary" : "primary"}
          onClick={() => {
            setIsPanelOpen((prev) => !prev);
            if (isPanelOpen) setPickTarget(null);
          }}
        >
          <MapPin className="w-4 h-4 mr-2" />
          Regions &amp; Locations
        </Button>
      </div>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-xl font-semibold text-white">
                Resource Locations
              </h2>
              <p className="text-sm text-slate-400 mt-1">
                {markerData.definedLocations.length} defined location
                {markerData.definedLocations.length === 1 ? "" : "s"}
                {markerData.exact.length > 0 && (
                  <span> · {markerData.exact.length} exact</span>
                )}
                {regionApproxCount > 0 && (
                  <span> · {regionApproxCount} region-approximate</span>
                )}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-4 text-xs text-slate-400">
              <span className="flex items-center gap-1.5">
                <span className="w-2.5 h-2.5 rounded-full bg-emerald-500 inline-block" />
                Defined location
              </span>
              <span className="flex items-center gap-1.5">
                <span className="w-2.5 h-2.5 rounded-full bg-sky-500 inline-block" />
                Exact entity location
              </span>
              <span className="flex items-center gap-1.5">
                <span className="w-2.5 h-2.5 rounded-full bg-amber-500 inline-block" />
                Region approximate
              </span>
            </div>
          </div>
        </CardHeader>
        <CardContent className="relative p-0">
          <div
            ref={mapContainerRef}
            className="w-full h-[calc(100vh-320px)] rounded-b-lg overflow-hidden"
          />

          {pickTarget && (
            <div className="absolute top-3 left-1/2 -translate-x-1/2 z-20 bg-slate-900/95 border border-emerald-500/70 text-emerald-300 px-4 py-2 rounded-lg shadow-lg flex items-center gap-3 text-sm">
              <Crosshair className="w-4 h-4 animate-pulse" />
              <span>
                Click anywhere on the map to pick coordinates (Esc to cancel)
              </span>
              <button
                type="button"
                onClick={() => setPickTarget(null)}
                className="text-slate-400 hover:text-white"
                aria-label="Cancel pick on map"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          )}

          {isLoading && (
            <div className="absolute inset-0 flex items-center justify-center bg-slate-900/60">
              <div className="w-12 h-12 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
            </div>
          )}

          {!isLoading && !hasMarkers && (
            <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
              <div className="bg-slate-900/90 border border-slate-700 rounded-lg px-6 py-4 text-center pointer-events-auto max-w-sm">
                <MapIcon className="w-10 h-10 mx-auto mb-2 text-slate-500" />
                <p className="text-slate-300 font-medium">
                  No geolocated resources yet
                </p>
                <p className="text-slate-500 text-sm mt-1">
                  Add a defined location via Regions &amp; Locations, or assign
                  a region or metadata.location to your entities and networks.
                </p>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {isPanelOpen && (
        <aside
          aria-label="Regions and Locations Manager"
          className="fixed top-20 right-6 bottom-6 w-full max-w-md z-30 bg-slate-900 border border-slate-700 rounded-xl shadow-2xl flex flex-col overflow-hidden"
        >
          <div className="px-5 py-4 border-b border-slate-800 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Globe className="w-5 h-5 text-emerald-400" />
              <h2 className="text-lg font-semibold text-white">
                Regions &amp; Locations
              </h2>
            </div>
            <button
              type="button"
              onClick={() => {
                setIsPanelOpen(false);
                setPickTarget(null);
              }}
              className="text-slate-400 hover:text-white p-1 rounded"
              aria-label="Close panel"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          <div className="flex border-b border-slate-800 bg-slate-950/40">
            <button
              type="button"
              onClick={() => {
                setActiveTab("locations");
                setPickTarget(null);
              }}
              className={`flex-1 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                activeTab === "locations"
                  ? "border-emerald-500 text-emerald-400"
                  : "border-transparent text-slate-400 hover:text-slate-200"
              }`}
            >
              Locations ({geoLocations.length})
            </button>
            <button
              type="button"
              onClick={() => {
                setActiveTab("regions");
                setPickTarget(null);
              }}
              className={`flex-1 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                activeTab === "regions"
                  ? "border-amber-500 text-amber-400"
                  : "border-transparent text-slate-400 hover:text-slate-200"
              }`}
            >
              Regions ({geoRegions.length})
            </button>
          </div>

          <div className="flex-1 overflow-y-auto p-5 space-y-6">
            {activeTab === "locations" ? (
              <>
                <form onSubmit={handleCreateLocation} className="space-y-3">
                  <h3 className="text-sm font-semibold text-slate-200">
                    Add Location
                  </h3>

                  <div>
                    <label
                      htmlFor={`${formIdPrefix}-loc-name`}
                      className="block text-xs font-medium text-slate-300 mb-1"
                    >
                      Name *
                    </label>
                    <input
                      id={`${formIdPrefix}-loc-name`}
                      type="text"
                      required
                      value={locName}
                      onChange={(e) => setLocName(e.target.value)}
                      placeholder="e.g. Dallas HQ, Austin DC-1"
                      className="w-full px-3 py-2 text-sm bg-slate-950 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:border-primary-500"
                    />
                  </div>

                  <div>
                    <span className="block text-xs font-medium text-slate-300 mb-1">
                      Region Code
                    </span>
                    <SearchableSelect
                      options={regionSelectOptions}
                      value={locRegionCode}
                      onChange={(val) => setLocRegionCode(String(val))}
                      placeholder="Select or type region code..."
                      allowCustom
                      allowClear
                      ariaLabel="Location region code"
                    />
                  </div>

                  <div className="grid grid-cols-3 gap-2">
                    <div>
                      <label
                        htmlFor={`${formIdPrefix}-loc-city`}
                        className="block text-xs font-medium text-slate-300 mb-1"
                      >
                        City
                      </label>
                      <input
                        id={`${formIdPrefix}-loc-city`}
                        type="text"
                        value={locCity}
                        onChange={(e) => setLocCity(e.target.value)}
                        placeholder="Dallas"
                        className="w-full px-2.5 py-1.5 text-sm bg-slate-950 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:border-primary-500"
                      />
                    </div>
                    <div>
                      <label
                        htmlFor={`${formIdPrefix}-loc-state`}
                        className="block text-xs font-medium text-slate-300 mb-1"
                      >
                        State
                      </label>
                      <input
                        id={`${formIdPrefix}-loc-state`}
                        type="text"
                        value={locState}
                        onChange={(e) => setLocState(e.target.value)}
                        placeholder="TX"
                        className="w-full px-2.5 py-1.5 text-sm bg-slate-950 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:border-primary-500"
                      />
                    </div>
                    <div>
                      <label
                        htmlFor={`${formIdPrefix}-loc-country`}
                        className="block text-xs font-medium text-slate-300 mb-1"
                      >
                        Country
                      </label>
                      <input
                        id={`${formIdPrefix}-loc-country`}
                        type="text"
                        value={locCountry}
                        onChange={(e) => setLocCountry(e.target.value)}
                        placeholder="US"
                        className="w-full px-2.5 py-1.5 text-sm bg-slate-950 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:border-primary-500"
                      />
                    </div>
                  </div>

                  <div>
                    <label
                      htmlFor={`${formIdPrefix}-loc-address`}
                      className="block text-xs font-medium text-slate-300 mb-1"
                    >
                      Address (optional)
                    </label>
                    <input
                      id={`${formIdPrefix}-loc-address`}
                      type="text"
                      value={locAddress}
                      onChange={(e) => setLocAddress(e.target.value)}
                      placeholder="1950 N Stemmons Fwy, Dallas, TX"
                      className="w-full px-3 py-1.5 text-sm bg-slate-950 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:border-primary-500"
                    />
                  </div>

                  <div className="space-y-2">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="text-xs font-medium text-slate-300">
                        Coordinates (optional — auto-resolved on save)
                      </span>
                      <div className="flex items-center gap-1.5">
                        <Button
                          type="button"
                          size="sm"
                          variant="secondary"
                          isLoading={isGeocoding}
                          onClick={handleResolveCoordinates}
                        >
                          <Search className="w-3.5 h-3.5 mr-1" />
                          Resolve
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          variant={
                            pickTarget === "location" ? "primary" : "secondary"
                          }
                          onClick={() =>
                            setPickTarget((prev) =>
                              prev === "location" ? null : "location",
                            )
                          }
                        >
                          <Crosshair className="w-3.5 h-3.5 mr-1" />
                          {pickTarget === "location"
                            ? "Picking..."
                            : "Pick on Map"}
                        </Button>
                      </div>
                    </div>

                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <label
                          htmlFor={`${formIdPrefix}-loc-lat`}
                          className="block text-xs text-slate-400 mb-1"
                        >
                          Latitude
                        </label>
                        <input
                          id={`${formIdPrefix}-loc-lat`}
                          type="number"
                          step="any"
                          value={locLat}
                          onChange={(e) => setLocLat(e.target.value)}
                          placeholder="Auto"
                          className="w-full px-2.5 py-1.5 text-sm bg-slate-950 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:border-primary-500"
                        />
                      </div>
                      <div>
                        <label
                          htmlFor={`${formIdPrefix}-loc-lng`}
                          className="block text-xs text-slate-400 mb-1"
                        >
                          Longitude
                        </label>
                        <input
                          id={`${formIdPrefix}-loc-lng`}
                          type="number"
                          step="any"
                          value={locLng}
                          onChange={(e) => setLocLng(e.target.value)}
                          placeholder="Auto"
                          className="w-full px-2.5 py-1.5 text-sm bg-slate-950 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:border-primary-500"
                        />
                      </div>
                    </div>
                  </div>

                  <div>
                    <label
                      htmlFor={`${formIdPrefix}-loc-desc`}
                      className="block text-xs font-medium text-slate-300 mb-1"
                    >
                      Description
                    </label>
                    <input
                      id={`${formIdPrefix}-loc-desc`}
                      type="text"
                      value={locDescription}
                      onChange={(e) => setLocDescription(e.target.value)}
                      placeholder="Primary colocation facility"
                      className="w-full px-3 py-1.5 text-sm bg-slate-950 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:border-primary-500"
                    />
                  </div>

                  <Button
                    type="submit"
                    className="w-full"
                    isLoading={createLocationMutation.isPending}
                  >
                    <Plus className="w-4 h-4 mr-1.5" />
                    Add Location
                  </Button>
                </form>

                <div className="border-t border-slate-800 pt-4 space-y-2">
                  <h3 className="text-sm font-semibold text-slate-200">
                    Defined Locations
                  </h3>
                  {geoLocations.length === 0 ? (
                    <p className="text-xs text-slate-500">
                      No custom locations defined yet.
                    </p>
                  ) : (
                    <ul className="space-y-2">
                      {geoLocations.map((loc) => {
                        const place = [loc.city, loc.state, loc.country]
                          .filter(Boolean)
                          .join(", ");
                        return (
                          <li
                            key={loc.id}
                            className="p-3 rounded-lg bg-slate-950/70 border border-slate-800 flex items-start justify-between gap-2"
                          >
                            <div className="min-w-0">
                              <div className="text-sm font-medium text-emerald-400 truncate">
                                {loc.name}
                              </div>
                              {place && (
                                <div className="text-xs text-slate-300 truncate">
                                  {place}
                                </div>
                              )}
                              <div className="text-xs text-slate-500 mt-0.5">
                                {loc.region_code ? `${loc.region_code} · ` : ""}
                                {isFiniteNumber(loc.latitude) &&
                                isFiniteNumber(loc.longitude)
                                  ? `${loc.latitude.toFixed(4)}, ${loc.longitude.toFixed(4)}`
                                  : "No coordinates"}
                              </div>
                            </div>
                            <button
                              type="button"
                              onClick={() =>
                                deleteLocationMutation.mutate(loc.id)
                              }
                              disabled={deleteLocationMutation.isPending}
                              className="text-slate-400 hover:text-red-400 p-1 rounded shrink-0"
                              aria-label={`Delete location ${loc.name}`}
                            >
                              <Trash2 className="w-4 h-4" />
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </div>
              </>
            ) : (
              <>
                <form onSubmit={handleCreateRegion} className="space-y-3">
                  <h3 className="text-sm font-semibold text-slate-200">
                    Add Custom Region
                  </h3>

                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label
                        htmlFor={`${formIdPrefix}-reg-code`}
                        className="block text-xs font-medium text-slate-300 mb-1"
                      >
                        Code *
                      </label>
                      <input
                        id={`${formIdPrefix}-reg-code`}
                        type="text"
                        required
                        value={regCode}
                        onChange={(e) => setRegCode(e.target.value)}
                        placeholder="dal2"
                        className="w-full px-3 py-2 text-sm bg-slate-950 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:border-primary-500"
                      />
                    </div>
                    <div>
                      <label
                        htmlFor={`${formIdPrefix}-reg-provider`}
                        className="block text-xs font-medium text-slate-300 mb-1"
                      >
                        Provider
                      </label>
                      <select
                        id={`${formIdPrefix}-reg-provider`}
                        value={regProvider}
                        onChange={(e) => setRegProvider(e.target.value)}
                        className="w-full px-2.5 py-2 text-sm bg-slate-950 border border-slate-700 rounded-lg text-white focus:outline-none focus:border-primary-500"
                      >
                        {PROVIDER_OPTIONS.map((opt) => (
                          <option key={opt.value} value={opt.value}>
                            {opt.label}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>

                  <div>
                    <label
                      htmlFor={`${formIdPrefix}-reg-name`}
                      className="block text-xs font-medium text-slate-300 mb-1"
                    >
                      Name *
                    </label>
                    <input
                      id={`${formIdPrefix}-reg-name`}
                      type="text"
                      required
                      value={regName}
                      onChange={(e) => setRegName(e.target.value)}
                      placeholder="Dallas Datacenter 2"
                      className="w-full px-3 py-2 text-sm bg-slate-950 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:border-primary-500"
                    />
                  </div>

                  <div className="space-y-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-medium text-slate-300">
                        Coordinates
                      </span>
                      <Button
                        type="button"
                        size="sm"
                        variant={
                          pickTarget === "region" ? "primary" : "secondary"
                        }
                        onClick={() =>
                          setPickTarget((prev) =>
                            prev === "region" ? null : "region",
                          )
                        }
                      >
                        <Crosshair className="w-3.5 h-3.5 mr-1" />
                        {pickTarget === "region" ? "Picking..." : "Pick on Map"}
                      </Button>
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <label
                          htmlFor={`${formIdPrefix}-reg-lat`}
                          className="block text-xs text-slate-400 mb-1"
                        >
                          Latitude
                        </label>
                        <input
                          id={`${formIdPrefix}-reg-lat`}
                          type="number"
                          step="any"
                          value={regLat}
                          onChange={(e) => setRegLat(e.target.value)}
                          placeholder="32.7767"
                          className="w-full px-2.5 py-1.5 text-sm bg-slate-950 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:border-primary-500"
                        />
                      </div>
                      <div>
                        <label
                          htmlFor={`${formIdPrefix}-reg-lng`}
                          className="block text-xs text-slate-400 mb-1"
                        >
                          Longitude
                        </label>
                        <input
                          id={`${formIdPrefix}-reg-lng`}
                          type="number"
                          step="any"
                          value={regLng}
                          onChange={(e) => setRegLng(e.target.value)}
                          placeholder="-96.7970"
                          className="w-full px-2.5 py-1.5 text-sm bg-slate-950 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:border-primary-500"
                        />
                      </div>
                    </div>
                  </div>

                  <div>
                    <label
                      htmlFor={`${formIdPrefix}-reg-desc`}
                      className="block text-xs font-medium text-slate-300 mb-1"
                    >
                      Description
                    </label>
                    <input
                      id={`${formIdPrefix}-reg-desc`}
                      type="text"
                      value={regDescription}
                      onChange={(e) => setRegDescription(e.target.value)}
                      placeholder="On-prem primary region"
                      className="w-full px-3 py-1.5 text-sm bg-slate-950 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:border-primary-500"
                    />
                  </div>

                  <Button
                    type="submit"
                    className="w-full"
                    isLoading={createRegionMutation.isPending}
                  >
                    <Plus className="w-4 h-4 mr-1.5" />
                    Add Region
                  </Button>
                </form>

                <div className="border-t border-slate-800 pt-4 space-y-2">
                  <h3 className="text-sm font-semibold text-slate-200">
                    Defined Custom Regions
                  </h3>
                  {geoRegions.length === 0 ? (
                    <p className="text-xs text-slate-500">
                      No custom regions defined yet.
                    </p>
                  ) : (
                    <ul className="space-y-2">
                      {geoRegions.map((reg) => (
                        <li
                          key={reg.id}
                          className="p-3 rounded-lg bg-slate-950/70 border border-slate-800 flex items-start justify-between gap-2"
                        >
                          <div className="min-w-0">
                            <div className="text-sm font-medium text-amber-400 truncate">
                              {reg.code} — {reg.name}
                            </div>
                            <div className="text-xs text-slate-400 mt-0.5">
                              {reg.provider || "custom"}
                              {isFiniteNumber(reg.latitude) &&
                              isFiniteNumber(reg.longitude)
                                ? ` · ${reg.latitude.toFixed(4)}, ${reg.longitude.toFixed(4)}`
                                : ""}
                            </div>
                          </div>
                          <button
                            type="button"
                            onClick={() => deleteRegionMutation.mutate(reg.id)}
                            disabled={deleteRegionMutation.isPending}
                            className="text-slate-400 hover:text-red-400 p-1 rounded shrink-0"
                            aria-label={`Delete region ${reg.code}`}
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </>
            )}
          </div>
        </aside>
      )}
    </div>
  );
}
