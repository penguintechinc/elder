import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Map as MapIcon, Filter, RefreshCw } from 'lucide-react'
import type { Organization } from '@/types'
import api from '@/lib/api'
import Button from '@/components/Button'
import Card, { CardHeader, CardContent } from '@/components/Card'
// Input component not currently used
import { NetworkGraph } from '@/components/NetworkGraph'

interface MapNode {
  id: string
  label: string
  type: string
  resource_id: number
  resource_type: string
  organization_id?: number
  parent_id?: number
}

interface MapEdge {
  from: string
  to: string
  type: string
}

// Resource type options
// Cloud discovery types (networking_resource/data_store/service/software) must
// match apps/api/modules/infrastructure/routes/graph.py VALID_RESOURCE_TYPES
// exactly — they're sent verbatim in the resource_types query param.
const RESOURCE_TYPES = [
  { value: 'organization', label: 'Organizations', color: '#3498db' },
  { value: 'entity', label: 'Entities', color: '#e74c3c' },
  { value: 'identity', label: 'Identities', color: '#9b59b6' },
  { value: 'project', label: 'Projects', color: '#27ae60' },
  { value: 'milestone', label: 'Milestones', color: '#f39c12' },
  { value: 'issue', label: 'Issues', color: '#e67e22' },
  { value: 'networking_resource', label: 'Networking Resources', color: '#1abc9c' },
  { value: 'data_store', label: 'Data Stores', color: '#06b6d4' },
  { value: 'service', label: 'Services', color: '#a855f7' },
  { value: 'software', label: 'Software', color: '#eab308' },
]

// Entity subtype options
const ENTITY_TYPES = [
  { value: 'network', label: 'Network', color: '#f39c12' },
  { value: 'compute', label: 'Compute', color: '#e74c3c' },
  { value: 'storage', label: 'Storage', color: '#8e44ad' },
  { value: 'datacenter', label: 'Datacenter', color: '#2c3e50' },
  { value: 'vpc', label: 'VPC', color: '#2980b9' },
  { value: 'subnet', label: 'Subnet', color: '#1abc9c' },
  { value: 'security', label: 'Security', color: '#c0392b' },
  { value: 'user', label: 'User', color: '#9b59b6' },
  { value: 'application', label: 'Application', color: '#8b5cf6' },
  { value: 'service', label: 'Service', color: '#a855f7' },
  { value: 'database', label: 'Database', color: '#06b6d4' },
]

export default function Map() {
  // Filter state
  const [selectedResourceTypes, setSelectedResourceTypes] = useState<string[]>(RESOURCE_TYPES.map(t => t.value))
  const [selectedEntityTypes, setSelectedEntityTypes] = useState<string[]>([])
  const [organizationId, setOrganizationId] = useState<string>('')
  const [includeHierarchical, setIncludeHierarchical] = useState(true)
  const [includeDependencies, setIncludeDependencies] = useState(true)
  const [limit, setLimit] = useState(500)
  const [showFilters, setShowFilters] = useState(true)

  // Build query params
  const queryParams = useMemo(() => ({
    resource_types: selectedResourceTypes.join(','),
    entity_types: selectedEntityTypes.join(','),
    organization_id: organizationId ? parseInt(organizationId) : undefined,
    include_hierarchical: includeHierarchical,
    include_dependencies: includeDependencies,
    limit,
  }), [selectedResourceTypes, selectedEntityTypes, organizationId, includeHierarchical, includeDependencies, limit])

  // Fetch map data
  const { data: mapData, isLoading, refetch } = useQuery({
    queryKey: ['map', queryParams],
    queryFn: () => api.getMap(queryParams),
  })

  // Fetch organizations for filter dropdown
  const { data: orgsData } = useQuery({
    queryKey: ['organizations-list'],
    queryFn: () => api.getOrganizations({ per_page: 1000 }),
  })

  // Transform API data to NetworkGraph format
  const graphData = useMemo(() => {
    if (!mapData) return { nodes: [], edges: [] }

    // Transform nodes - NetworkGraph expects id, label, type, metadata
    const nodes = mapData.nodes.map((node: MapNode) => ({
      id: node.id, // Already in format "type:id"
      label: node.label,
      type: node.type,
      metadata: {
        id: node.resource_id,
        resource_type: node.resource_type,
        organization_id: node.organization_id,
        parent_id: node.parent_id,
        ...node,
      },
    }))

    // Transform edges - NetworkGraph expects from, to, label
    const edges = mapData.edges.map((edge: MapEdge) => ({
      from: edge.from,
      to: edge.to,
      label: edge.type,
    }))

    return { nodes, edges }
  }, [mapData])

  // Toggle resource type selection
  const toggleResourceType = (value: string) => {
    setSelectedResourceTypes(prev =>
      prev.includes(value)
        ? prev.filter(t => t !== value)
        : [...prev, value]
    )
  }

  // Toggle entity type selection
  const toggleEntityType = (value: string) => {
    setSelectedEntityTypes(prev =>
      prev.includes(value)
        ? prev.filter(t => t !== value)
        : [...prev, value]
    )
  }

  interface GraphNodeWithMetadata {
    id: string
    label: string
    type: string
    metadata?: Record<string, unknown>
  }

  // Handle node click - could navigate to details
  const handleNodeClick = (node: GraphNodeWithMetadata) => {
    console.log('Node clicked:', node)
    // Could add navigation or modal here
  }

  return (
    <div className="p-8">
      {/* Header */}
      <div className="mb-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold text-white flex items-center gap-3">
              <MapIcon className="w-8 h-8" />
              Resource Map
            </h1>
            <p className="text-slate-400 mt-2">
              Interactive visualization of all resources and their relationships
            </p>
          </div>

          <div className="flex items-center gap-3">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setShowFilters(!showFilters)}
            >
              <Filter className="w-4 h-4 mr-2" />
              {showFilters ? 'Hide Filters' : 'Show Filters'}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => refetch()}
            >
              <RefreshCw className="w-4 h-4 mr-2" />
              Refresh
            </Button>
          </div>
        </div>
      </div>

      {/* Filters */}
      {showFilters && (
        <Card className="mb-6">
          <CardHeader>
            <h2 className="text-lg font-semibold text-white">Filters</h2>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {/* Organization Filter */}
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-2">
                  Organization Scope
                </label>
                <select
                  value={organizationId}
                  onChange={(e) => setOrganizationId(e.target.value)}
                  className="w-full px-3 py-2 bg-slate-800 text-white border border-slate-700 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-500"
                >
                  <option value="">All Organizations (Global)</option>
                  {orgsData?.items?.map((org: Organization) => (
                    <option key={org.id} value={org.id}>
                      {org.name}
                    </option>
                  ))}
                </select>
              </div>

              {/* Resource Types */}
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-2">
                  Resource Types
                </label>
                <div className="flex flex-wrap gap-2">
                  {RESOURCE_TYPES.map((type) => (
                    <button
                      key={type.value}
                      onClick={() => toggleResourceType(type.value)}
                      className={`px-3 py-1.5 text-sm rounded-lg border transition-colors ${
                        selectedResourceTypes.includes(type.value)
                          ? 'border-primary-500 bg-primary-500/20 text-primary-300'
                          : 'border-slate-600 bg-slate-800 text-slate-400 hover:border-slate-500'
                      }`}
                    >
                      <span
                        className="inline-block w-2 h-2 rounded-full mr-2"
                        style={{ backgroundColor: type.color }}
                      />
                      {type.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Entity Types (only show if entities selected) */}
              {selectedResourceTypes.includes('entity') && (
                <div>
                  <label className="block text-sm font-medium text-slate-300 mb-2">
                    Entity Types (leave empty for all)
                  </label>
                  <div className="flex flex-wrap gap-2">
                    {ENTITY_TYPES.map((type) => (
                      <button
                        key={type.value}
                        onClick={() => toggleEntityType(type.value)}
                        className={`px-3 py-1.5 text-sm rounded-lg border transition-colors ${
                          selectedEntityTypes.includes(type.value)
                            ? 'border-primary-500 bg-primary-500/20 text-primary-300'
                            : 'border-slate-600 bg-slate-800 text-slate-400 hover:border-slate-500'
                        }`}
                      >
                        <span
                          className="inline-block w-2 h-2 rounded-full mr-2"
                          style={{ backgroundColor: type.color }}
                        />
                        {type.label}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {/* Relationship Options */}
              <div className="flex flex-wrap gap-6">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={includeHierarchical}
                    onChange={(e) => setIncludeHierarchical(e.target.checked)}
                    className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-primary-500 focus:ring-primary-500"
                  />
                  <span className="text-sm text-slate-300">Include Hierarchical Links</span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={includeDependencies}
                    onChange={(e) => setIncludeDependencies(e.target.checked)}
                    className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-primary-500 focus:ring-primary-500"
                  />
                  <span className="text-sm text-slate-300">Include Dependencies</span>
                </label>
              </div>

              {/* Limit */}
              <div className="max-w-xs">
                <label className="block text-sm font-medium text-slate-300 mb-2">
                  Max Nodes: {limit}
                </label>
                <input
                  type="range"
                  min="50"
                  max="1000"
                  step="50"
                  value={limit}
                  onChange={(e) => setLimit(parseInt(e.target.value))}
                  className="w-full"
                />
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Graph Card */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-xl font-semibold text-white">Resource Graph</h2>
              <p className="text-sm text-slate-400 mt-1">
                {mapData?.stats?.node_count || 0} nodes, {mapData?.stats?.edge_count || 0} connections
                {mapData?.stats?.truncated && (
                  <span className="text-yellow-400 ml-2">(truncated to {limit} nodes)</span>
                )}
              </p>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="flex items-center justify-center h-[calc(100vh-400px)]">
              <div className="w-12 h-12 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
            </div>
          ) : !graphData || graphData.nodes.length === 0 ? (
            <div className="flex items-center justify-center h-[calc(100vh-400px)] bg-slate-800/50 rounded-lg border-2 border-dashed border-slate-600">
              <div className="text-center text-slate-400">
                <MapIcon className="w-16 h-16 mx-auto mb-4 opacity-50" />
                <p className="text-lg font-medium">No resources to display</p>
                <p className="text-sm mt-2">
                  Adjust filters or create resources to visualize relationships
                </p>
              </div>
            </div>
          ) : (
            <NetworkGraph
              nodes={graphData.nodes}
              edges={graphData.edges}
              height="calc(100vh - 400px)"
              onNodeClick={handleNodeClick}
            />
          )}
        </CardContent>
      </Card>

      {/* Legend */}
      <Card className="mt-6">
        <CardHeader>
          <h3 className="text-lg font-semibold text-white">Legend</h3>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {/* Resource Types */}
            <div>
              <h4 className="text-sm font-medium text-slate-400 mb-2">Resource Types</h4>
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
                {RESOURCE_TYPES.map((type) => (
                  <div key={type.value} className="flex items-center gap-2">
                    <div
                      className="w-4 h-4 rounded"
                      style={{ backgroundColor: type.color }}
                    />
                    <span className="text-sm text-slate-300">{type.label}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Entity Subtypes */}
            <div>
              <h4 className="text-sm font-medium text-slate-400 mb-2">Entity Subtypes</h4>
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
                {ENTITY_TYPES.map((type) => (
                  <div key={type.value} className="flex items-center gap-2">
                    <div
                      className="w-4 h-4 rounded"
                      style={{ backgroundColor: type.color }}
                    />
                    <span className="text-sm text-slate-300">{type.label}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Edge Types */}
            <div>
              <h4 className="text-sm font-medium text-slate-400 mb-2">Relationship Types</h4>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <div className="flex items-center gap-2">
                  <div className="w-8 h-0.5 bg-green-500" />
                  <span className="text-sm text-slate-300">Parent/Child</span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="w-8 h-0.5 bg-blue-500" />
                  <span className="text-sm text-slate-300">Contains</span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="w-8 h-0.5 bg-yellow-500" />
                  <span className="text-sm text-slate-300">Dependency</span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="w-8 h-0.5 bg-slate-500 border-dashed" style={{ borderTop: '2px dashed' }} />
                  <span className="text-sm text-slate-300">Related</span>
                </div>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
