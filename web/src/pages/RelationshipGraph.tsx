import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft } from 'lucide-react'
import api from '@/lib/api'
import Button from '@/components/Button'
import Card, { CardHeader, CardContent } from '@/components/Card'
import { NetworkGraph } from '@/components/NetworkGraph'

interface GraphNode {
  id: string
  label: string
  type: string
  metadata?: Record<string, unknown>
}

export default function RelationshipGraph() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [depth, setDepth] = useState(5)

  // Validate ID is a valid number
  const orgId = id ? parseInt(id) : NaN
  const isValidId = !isNaN(orgId) && orgId > 0

  const { data: organization } = useQuery({
    queryKey: ['organization', id],
    queryFn: () => api.getOrganization(orgId),
    enabled: isValidId,
  })

  const { data: graphData, isLoading } = useQuery({
    queryKey: ['organization-graph', id, depth],
    queryFn: () => api.getOrganizationGraph(orgId, depth),
    enabled: isValidId,
  })

  // Handle invalid ID
  if (!isValidId) {
    return (
      <div className="p-8">
        <Card>
          <CardContent className="text-center py-12">
            <p className="text-slate-400">Invalid organization ID</p>
            <Button className="mt-4" onClick={() => navigate('/organizations')}>
              Back to Organizations
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  const handleNodeClick = (node: GraphNode) => {
    const nodeId = node.metadata?.id
    if (!nodeId) return

    if (node.type === 'organization') {
      navigate(`/organizations/${nodeId}`)
    } else {
      navigate(`/entities/${nodeId}`)
    }
  }

  return (
    <div className="p-8">
      {/* Header */}
      <div className="mb-6">
        <div className="flex items-center gap-4 mb-4">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => navigate(`/organizations/${id}`)}
          >
            <ArrowLeft className="w-4 h-4 mr-2" />
            Back to Organization
          </Button>
        </div>

        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold text-white">
              {organization?.name || 'Loading...'} - Relationship Map
            </h1>
            <p className="text-slate-400 mt-2">
              Interactive graph visualization of organizational relationships
            </p>
          </div>

          <div className="flex items-center gap-3">
            <label className="text-sm text-slate-400">
              Depth:
            </label>
            <select
              value={depth}
              onChange={(e) => setDepth(parseInt(e.target.value))}
              className="px-3 py-2 bg-slate-800 text-white border border-slate-700 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-500"
            >
              <option value={1}>1 hop</option>
              <option value={2}>2 hops</option>
              <option value={3}>3 hops</option>
              <option value={4}>4 hops</option>
              <option value={5}>5 hops</option>
              <option value={7}>7 hops</option>
              <option value={10}>10 hops (max)</option>
            </select>
          </div>
        </div>
      </div>

      {/* Graph Card */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-xl font-semibold text-white">Full Relationship Graph</h2>
              <p className="text-sm text-slate-400 mt-1">
                {graphData?.nodes?.length || 0} nodes, {graphData?.edges?.length || 0} connections
              </p>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="flex items-center justify-center h-[calc(100vh-300px)]">
              <div className="w-12 h-12 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
            </div>
          ) : !graphData || graphData.nodes.length === 0 ? (
            <div className="flex items-center justify-center h-[calc(100vh-300px)] bg-slate-50 rounded-lg border-2 border-dashed border-slate-300">
              <div className="text-center text-slate-500">
                <p className="text-lg font-medium">No relationships to display</p>
                <p className="text-sm mt-2">
                  Create child organizations, entities, and dependencies to visualize relationships
                </p>
                <Button
                  className="mt-4"
                  onClick={() => navigate(`/organizations/${id}`)}
                >
                  Go to Organization Details
                </Button>
              </div>
            </div>
          ) : (
            <NetworkGraph
              nodes={graphData.nodes}
              edges={graphData.edges}
              height="calc(100vh - 300px)"
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
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
            <div className="flex items-center gap-2">
              <div className="w-4 h-4 rounded" style={{ backgroundColor: '#10b981' }}></div>
              <span className="text-sm text-slate-300">Organization</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-4 h-4 rounded" style={{ backgroundColor: '#8b5cf6' }}></div>
              <span className="text-sm text-slate-300">Application</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-4 h-4 rounded" style={{ backgroundColor: '#a855f7' }}></div>
              <span className="text-sm text-slate-300">Service</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-4 h-4 rounded" style={{ backgroundColor: '#7c3aed' }}></div>
              <span className="text-sm text-slate-300">Repository</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-4 h-4 rounded" style={{ backgroundColor: '#3b82f6' }}></div>
              <span className="text-sm text-slate-300">Datacenter</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-4 h-4 rounded" style={{ backgroundColor: '#6366f1' }}></div>
              <span className="text-sm text-slate-300">VPC/Subnet</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-4 h-4 rounded" style={{ backgroundColor: '#ec4899' }}></div>
              <span className="text-sm text-slate-300">Compute</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-4 h-4 rounded" style={{ backgroundColor: '#14b8a6' }}></div>
              <span className="text-sm text-slate-300">Network</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-4 h-4 rounded" style={{ backgroundColor: '#0ea5e9' }}></div>
              <span className="text-sm text-slate-300">Storage</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-4 h-4 rounded" style={{ backgroundColor: '#06b6d4' }}></div>
              <span className="text-sm text-slate-300">Database</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-4 h-4 rounded" style={{ backgroundColor: '#f59e0b' }}></div>
              <span className="text-sm text-slate-300">User</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-4 h-4 rounded" style={{ backgroundColor: '#ef4444' }}></div>
              <span className="text-sm text-slate-300">Security Issue</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-4 h-4 rounded" style={{ backgroundColor: '#64748b' }}></div>
              <span className="text-sm text-slate-300">Other</span>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
