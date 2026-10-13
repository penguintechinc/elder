import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Map as MapLibreMap,
  Marker as MapLibreMarker,
  NavigationControl,
  Popup as MapLibrePopup,
} from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { Map as MapIcon } from 'lucide-react'
import api from '@/lib/api'
import Card, { CardHeader, CardContent } from '@/components/Card'

// EOX Sentinel-2 cloudless satellite basemap — no API key required.
const SATELLITE_TILE_URL =
  'https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2020_3857/default/g/{z}/{y}/{x}.jpg'
const SATELLITE_ATTRIBUTION = '&copy; EOX IT Services GmbH — Sentinel-2 cloudless'

// World view — not scoped to any single region/customer.
const DEFAULT_CENTER: [number, number] = [0, 20]
const DEFAULT_ZOOM = 1.4

interface RegionInfo {
  lng: number
  lat: number
  label: string
}

// Fallback coordinates for cloud entities that only carry a region code
// (entity.region) with no precise metadata.location. Intentionally small —
// the primary path is exact entity.metadata.location coordinates.
const REGION_COORDS: Record<string, RegionInfo> = {
  // AWS
  'us-east-1': { lng: -77.4874, lat: 39.0438, label: 'AWS US East (N. Virginia)' },
  'us-east-2': { lng: -82.9988, lat: 40.4173, label: 'AWS US East (Ohio)' },
  'us-west-1': { lng: -121.8863, lat: 37.3382, label: 'AWS US West (N. California)' },
  'us-west-2': { lng: -120.5542, lat: 43.8041, label: 'AWS US West (Oregon)' },
  'eu-west-1': { lng: -8.2439, lat: 53.4129, label: 'AWS EU (Ireland)' },
  'eu-central-1': { lng: 8.6821, lat: 50.1109, label: 'AWS EU (Frankfurt)' },
  'ap-southeast-1': { lng: 103.8198, lat: 1.3521, label: 'AWS Asia Pacific (Singapore)' },
  'ap-northeast-1': { lng: 139.6917, lat: 35.6895, label: 'AWS Asia Pacific (Tokyo)' },
  // GCP
  'us-central1': { lng: -93.6091, lat: 41.5868, label: 'GCP US Central (Iowa)' },
  'us-east1': { lng: -81.1637, lat: 33.8361, label: 'GCP US East (S. Carolina)' },
  'europe-west1': { lng: 4.3517, lat: 50.8503, label: 'GCP Europe West (Belgium)' },
  'europe-west3': { lng: 8.6821, lat: 50.1109, label: 'GCP Europe West (Frankfurt)' },
  'asia-east1': { lng: 121.5654, lat: 25.033, label: 'GCP Asia East (Taiwan)' },
  'asia-southeast1': { lng: 103.8198, lat: 1.3521, label: 'GCP Asia Southeast (Singapore)' },
  // Azure
  eastus: { lng: -78.4767, lat: 37.4316, label: 'Azure East US (Virginia)' },
  eastus2: { lng: -78.6569, lat: 37.5407, label: 'Azure East US 2 (Virginia)' },
  westus2: { lng: -120.7401, lat: 47.7511, label: 'Azure West US 2 (Washington)' },
  westeurope: { lng: 4.9041, lat: 52.3676, label: 'Azure West Europe (Netherlands)' },
  uksouth: { lng: -0.1278, lat: 51.5074, label: 'Azure UK South (London)' },
  southeastasia: { lng: 103.8198, lat: 1.3521, label: 'Azure Southeast Asia (Singapore)' },
}

interface EntityLocation {
  city?: string | null
  state?: string | null
  country?: string | null
  latitude?: number | null
  longitude?: number | null
}

interface EntityMetadata {
  location?: EntityLocation | null
}

// Local, narrowed shape of what this page reads from GET /entities — the
// full Entity type lives in @/types but doesn't carry `region`/`metadata`.
interface GeoEntity {
  id: number
  name: string
  type: string
  sub_type?: string | null
  region?: string | null
  metadata?: EntityMetadata | null
}

interface EntitiesResponse {
  items: GeoEntity[]
  total: number
}

interface ExactMarker {
  entity: GeoEntity
  lng: number
  lat: number
  location: EntityLocation
}

interface RegionApproxGroup {
  region: string
  info: RegionInfo
  entities: GeoEntity[]
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function buildExactPopup(item: ExactMarker): HTMLDivElement {
  const container = document.createElement('div')
  container.className = 'text-sm min-w-[180px] max-w-[260px]'

  const title = document.createElement('div')
  title.className = 'font-semibold text-amber-400 mb-1'
  title.textContent = item.entity.name || 'Unnamed entity'
  container.appendChild(title)

  const placeParts = [item.location.city, item.location.state, item.location.country].filter(
    (part): part is string => Boolean(part && part.trim())
  )
  if (placeParts.length > 0) {
    const place = document.createElement('div')
    place.className = 'text-slate-300'
    place.textContent = placeParts.join(', ')
    container.appendChild(place)
  }

  const typeLine = document.createElement('div')
  typeLine.className = 'text-slate-400 text-xs mt-1'
  typeLine.textContent = item.entity.sub_type
    ? `${item.entity.type} / ${item.entity.sub_type}`
    : item.entity.type
  container.appendChild(typeLine)

  return container
}

const MAX_LISTED_ENTITIES_PER_REGION = 25

function buildRegionPopup(group: RegionApproxGroup): HTMLDivElement {
  const container = document.createElement('div')
  container.className = 'text-sm min-w-[180px] max-w-[260px]'

  const title = document.createElement('div')
  title.className = 'font-semibold text-amber-400 mb-1'
  title.textContent = group.info.label
  container.appendChild(title)

  const note = document.createElement('div')
  note.className = 'text-amber-500/80 text-xs italic mb-1'
  note.textContent = 'Approximate — region-level location only'
  container.appendChild(note)

  const summary = document.createElement('div')
  summary.className = 'text-slate-300 mb-1'
  summary.textContent = `${group.entities.length} resource${group.entities.length === 1 ? '' : 's'}`
  container.appendChild(summary)

  const list = document.createElement('ul')
  list.className = 'space-y-0.5 max-h-40 overflow-y-auto'
  for (const entity of group.entities.slice(0, MAX_LISTED_ENTITIES_PER_REGION)) {
    const li = document.createElement('li')
    li.className = 'text-slate-200 truncate'
    li.textContent = entity.sub_type ? `${entity.name} (${entity.sub_type})` : entity.name
    list.appendChild(li)
  }
  if (group.entities.length > MAX_LISTED_ENTITIES_PER_REGION) {
    const more = document.createElement('li')
    more.className = 'text-slate-500 italic'
    more.textContent = `+ ${group.entities.length - MAX_LISTED_ENTITIES_PER_REGION} more`
    list.appendChild(more)
  }
  container.appendChild(list)

  return container
}

// Named MapPage (not Map) — a top-level `function Map()` here would shadow
// the global ES2015 Map class used below for regionGroupMap.
export default function MapPage() {
  const mapContainerRef = useRef<HTMLDivElement | null>(null)
  const mapInstanceRef = useRef<MapLibreMap | null>(null)
  const markersRef = useRef<MapLibreMarker[]>([])
  const [mapLoaded, setMapLoaded] = useState(false)

  // Primary data source: entities carrying an exact metadata.location.
  // metadata may be absent entirely until the backend metadata-exposure fix
  // deploys — every access below is optional-chained accordingly.
  const { data: entitiesData, isLoading } = useQuery<EntitiesResponse>({
    queryKey: ['map-entities'],
    queryFn: () => api.getEntities({ per_page: 1000 }),
  })

  const markerData = useMemo(() => {
    const exact: ExactMarker[] = []
    const regionGroupMap = new Map<string, RegionApproxGroup>()
    let skipped = 0

    for (const entity of entitiesData?.items ?? []) {
      const location = entity.metadata?.location
      const lat = location?.latitude
      const lng = location?.longitude

      if (isFiniteNumber(lat) && isFiniteNumber(lng)) {
        exact.push({ entity, lat, lng, location: location ?? {} })
        continue
      }

      const regionCode = entity.region?.trim().toLowerCase()
      const info = regionCode ? REGION_COORDS[regionCode] : undefined
      if (regionCode && info) {
        let group = regionGroupMap.get(regionCode)
        if (!group) {
          group = { region: regionCode, info, entities: [] }
          regionGroupMap.set(regionCode, group)
        }
        group.entities.push(entity)
        continue
      }

      skipped += 1
    }

    return { exact, regionGroups: Array.from(regionGroupMap.values()), skipped }
  }, [entitiesData])

  // Create the map instance once. The container div is always rendered
  // (loading/empty states overlay on top of it) so this ref is available
  // on first mount.
  useEffect(() => {
    if (!mapContainerRef.current || mapInstanceRef.current) return

    const map = new MapLibreMap({
      container: mapContainerRef.current,
      style: {
        version: 8,
        sources: {
          satellite: {
            type: 'raster',
            tiles: [SATELLITE_TILE_URL],
            tileSize: 256,
            attribution: SATELLITE_ATTRIBUTION,
          },
        },
        layers: [{ id: 'satellite', type: 'raster', source: 'satellite' }],
      },
      center: DEFAULT_CENTER,
      zoom: DEFAULT_ZOOM,
    })

    map.addControl(new NavigationControl(), 'top-right')
    map.on('load', () => setMapLoaded(true))
    mapInstanceRef.current = map

    return () => {
      markersRef.current.forEach((marker) => marker.remove())
      markersRef.current = []
      map.remove()
      mapInstanceRef.current = null
    }
  }, [])

  // Render markers whenever the resolved entity/region data changes.
  useEffect(() => {
    const map = mapInstanceRef.current
    if (!map || !mapLoaded) return

    markersRef.current.forEach((marker) => marker.remove())
    markersRef.current = []

    for (const item of markerData.exact) {
      const popup = new MapLibrePopup({ offset: 16, closeButton: true, maxWidth: '260px' }).setDOMContent(
        buildExactPopup(item)
      )
      const marker = new MapLibreMarker({ color: '#0ea5e9' })
        .setLngLat([item.lng, item.lat])
        .setPopup(popup)
        .addTo(map)
      markersRef.current.push(marker)
    }

    for (const group of markerData.regionGroups) {
      const popup = new MapLibrePopup({ offset: 16, closeButton: true, maxWidth: '260px' }).setDOMContent(
        buildRegionPopup(group)
      )
      const marker = new MapLibreMarker({ color: '#f59e0b' })
        .setLngLat([group.info.lng, group.info.lat])
        .setPopup(popup)
        .addTo(map)
      markersRef.current.push(marker)
    }

    console.log('[Map] Rendered markers', {
      located: markerData.exact.length,
      regionApprox: markerData.regionGroups.reduce((sum, g) => sum + g.entities.length, 0),
      skipped: markerData.skipped,
    })
  }, [markerData, mapLoaded])

  const regionApproxCount = markerData.regionGroups.reduce((sum, g) => sum + g.entities.length, 0)
  const hasMarkers = markerData.exact.length > 0 || regionApproxCount > 0

  return (
    <div className="p-8">
      <div className="mb-6">
        <h1 className="text-3xl font-bold text-white flex items-center gap-3">
          <MapIcon className="w-8 h-8" />
          Resource Map
        </h1>
        <p className="text-slate-400 mt-2">
          Geographic view of resources with a known location
        </p>
      </div>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-xl font-semibold text-white">Resource Locations</h2>
              <p className="text-sm text-slate-400 mt-1">
                {markerData.exact.length} located
                {regionApproxCount > 0 && <span> · {regionApproxCount} region-approximate</span>}
              </p>
            </div>
            <div className="flex items-center gap-4 text-xs text-slate-400">
              <span className="flex items-center gap-1.5">
                <span className="w-2.5 h-2.5 rounded-full bg-primary-500 inline-block" />
                Exact location
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

          {isLoading && (
            <div className="absolute inset-0 flex items-center justify-center bg-slate-900/60">
              <div className="w-12 h-12 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
            </div>
          )}

          {!isLoading && !hasMarkers && (
            <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
              <div className="bg-slate-900/90 border border-slate-700 rounded-lg px-6 py-4 text-center pointer-events-auto max-w-sm">
                <MapIcon className="w-10 h-10 mx-auto mb-2 text-slate-500" />
                <p className="text-slate-300 font-medium">No geolocated entities yet</p>
                <p className="text-slate-500 text-sm mt-1">
                  Entities need a metadata.location with latitude/longitude, or a recognized
                  cloud region, to appear here.
                </p>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
