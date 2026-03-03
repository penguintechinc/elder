import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Edit, Trash2, ChevronRight, ChevronDown, Folder, FolderOpen, Box, Users, Copy, Plus } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import type { Organization, Entity } from '@/types'
import Button from '@/components/Button'
import Card, { CardHeader, CardContent } from '@/components/Card'
import Input from '@/components/Input'
import { NetworkGraph } from '@/components/NetworkGraph'
import CreateIdentityModal from '@/components/CreateIdentityModal'
import OnCallBadge from '@/components/OnCallBadge'

interface TreeNode {
  type: 'organization' | 'entity'
  id: number
  name: string
  data: Organization | Entity
  children: TreeNode[]
}

export default function OrganizationDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [expandedNodes, setExpandedNodes] = useState<Set<string>>(new Set())
  const [showMetadataModal, setShowMetadataModal] = useState(false)
  const [showCreateOrgModal, setShowCreateOrgModal] = useState(false)
  const [showCreateEntityModal, setShowCreateEntityModal] = useState(false)
  const [showEditModal, setShowEditModal] = useState(false)
  const [showCreateIdentityModal, setShowCreateIdentityModal] = useState(false)

  // Smooth scroll to section
  const scrollToSection = (sectionId: string) => {
    const element = document.getElementById(sectionId)
    if (element) {
      element.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }

  // Validate ID is a valid number
  const orgId = id ? parseInt(id) : NaN
  const isValidId = !isNaN(orgId) && orgId > 0

  const { data: organization, isLoading: orgLoading } = useQuery({
    queryKey: ['organization', id],
    queryFn: () => api.getOrganization(orgId),
    enabled: isValidId,
  })

  const { data: childOrgs } = useQuery({
    queryKey: ['organizations', { parent_id: id }],
    queryFn: () => api.getOrganizations({ parent_id: orgId }),
    enabled: isValidId,
  })

  const { data: entities } = useQuery({
    queryKey: ['entities', { organization_id: id }],
    queryFn: () => api.getEntities({ organization_id: orgId }),
    enabled: isValidId,
  })

  const { data: metadata } = useQuery({
    queryKey: ['organization-metadata', id],
    queryFn: () => api.getOrganizationMetadata(orgId),
    enabled: isValidId,
  })

  const { data: issues } = useQuery({
    queryKey: ['issues', { organization_id: id }],
    queryFn: () => api.getIssues({ organization_id: orgId }),
    enabled: isValidId,
  })

  const { data: projects } = useQuery({
    queryKey: ['projects', { organization_id: id }],
    queryFn: () => api.getProjects({ organization_id: orgId }),
    enabled: isValidId,
  })

  const { data: identities } = useQuery({
    queryKey: ['identities', { organization_id: id }],
    queryFn: () => api.getIdentities({ organization_id: orgId }),
    enabled: isValidId,
  })

  const { data: treeStats } = useQuery({
    queryKey: ['organization-tree-stats', id],
    queryFn: () => api.getOrganizationTreeStats(orgId),
    enabled: isValidId,
  })

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteOrganization(orgId),
    onSuccess: () => {
      toast.success('Organization deleted successfully')
      navigate('/organizations')
    },
    onError: () => {
      toast.error('Failed to delete organization')
    },
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

  const handleDelete = () => {
    if (window.confirm(`Are you sure you want to delete "${organization?.name}"?`)) {
      deleteMutation.mutate()
    }
  }

  const toggleNode = (nodeKey: string) => {
    const newExpanded = new Set(expandedNodes)
    if (newExpanded.has(nodeKey)) {
      newExpanded.delete(nodeKey)
    } else {
      newExpanded.add(nodeKey)
    }
    setExpandedNodes(newExpanded)
  }

  const buildTree = (): TreeNode[] => {
    const tree: TreeNode[] = []

    // Add child organizations
    if (childOrgs?.items) {
      for (const org of childOrgs.items) {
        tree.push({
          type: 'organization',
          id: org.id,
          name: org.name,
          data: org,
          children: [], // Would need recursive fetching for deeper trees
        })
      }
    }

    // Add entities
    if (entities?.items) {
      for (const entity of entities.items) {
        tree.push({
          type: 'entity',
          id: entity.id,
          name: entity.name,
          data: entity,
          children: [],
        })
      }
    }

    return tree
  }

  const renderTreeNode = (node: TreeNode, level: number = 0) => {
    const nodeKey = `${node.type}-${node.id}`
    const isExpanded = expandedNodes.has(nodeKey)
    const hasChildren = node.children.length > 0

    return (
      <div key={nodeKey} style={{ marginLeft: `${level * 24}px` }}>
        <div
          className="flex items-center gap-2 py-2 px-3 hover:bg-slate-800/50 rounded cursor-pointer group"
          onClick={() => {
            if (node.type === 'organization') {
              navigate(`/organizations/${node.id}`)
            } else {
              navigate(`/entities/${node.id}`)
            }
          }}
        >
          {hasChildren ? (
            <button
              onClick={(e) => {
                e.stopPropagation()
                toggleNode(nodeKey)
              }}
              className="p-0.5 hover:bg-slate-700 rounded"
            >
              {isExpanded ? (
                <ChevronDown className="w-4 h-4 text-slate-400" />
              ) : (
                <ChevronRight className="w-4 h-4 text-slate-400" />
              )}
            </button>
          ) : (
            <div className="w-5" />
          )}

          {node.type === 'organization' ? (
            isExpanded ? (
              <FolderOpen className="w-4 h-4 text-primary-400" />
            ) : (
              <Folder className="w-4 h-4 text-primary-400" />
            )
          ) : (
            <Box className="w-4 h-4 text-blue-400" />
          )}

          <span className="text-sm text-white group-hover:text-primary-400 transition-colors">
            {node.name}
          </span>

          {node.type === 'entity' && (
            <span className="text-xs text-slate-500 ml-2">
              {(node.data as Entity).entity_type}
            </span>
          )}
        </div>

        {isExpanded && hasChildren && (
          <div>
            {node.children.map((child) => renderTreeNode(child, level + 1))}
          </div>
        )}
      </div>
    )
  }

  // Relationship Graph Section Component
  const RelationshipGraphSection = ({ organizationId }: { organizationId: string }) => {
    const { data: graphData, isLoading } = useQuery({
      queryKey: ['organization-graph', organizationId],
      queryFn: () => api.getOrganizationGraph(parseInt(organizationId), 3),
      enabled: !!organizationId,
    })

    console.log('RelationshipGraphSection: organizationId:', organizationId);
    console.log('RelationshipGraphSection: isLoading:', isLoading);
    console.log('RelationshipGraphSection: graphData:', graphData);

    const handleNodeClick = (node: any) => {
      const nodeId = node.metadata?.id
      if (!nodeId) return

      if (node.type === 'organization') {
        navigate(`/organizations/${nodeId}`)
      } else {
        navigate(`/entities/${nodeId}`)
      }
    }

    if (isLoading) {
      console.log('RelationshipGraphSection: Rendering loading state');
      return (
        <div className="flex items-center justify-center h-[500px]">
          <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
        </div>
      )
    }

    if (!graphData) {
      console.log('RelationshipGraphSection: No graphData!');
      return (
        <div className="flex items-center justify-center h-[500px] bg-slate-50 rounded-lg border-2 border-dashed border-slate-300">
          <div className="text-center text-slate-500">
            <p className="text-lg font-medium">No graph data available</p>
            <p className="text-sm mt-2">Failed to load relationship data</p>
          </div>
        </div>
      )
    }

    if (graphData.nodes.length === 0) {
      console.log('RelationshipGraphSection: No nodes in graphData');
      return (
        <div className="flex items-center justify-center h-[500px] bg-slate-50 rounded-lg border-2 border-dashed border-slate-300">
          <div className="text-center text-slate-500">
            <p className="text-lg font-medium">No relationships to display</p>
            <p className="text-sm mt-2">Create entities and dependencies to see the graph</p>
          </div>
        </div>
      )
    }

    console.log('RelationshipGraphSection: Rendering NetworkGraph with', graphData.nodes.length, 'nodes and', graphData.edges.length, 'edges');
    return (
      <NetworkGraph
        nodes={graphData.nodes}
        edges={graphData.edges}
        height="500px"
        onNodeClick={handleNodeClick}
      />
    )
  }

  if (orgLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  if (!organization) {
    return (
      <div className="p-8">
        <Card>
          <CardContent className="text-center py-12">
            <p className="text-slate-400">Organization not found</p>
            <Button className="mt-4" onClick={() => navigate('/organizations')}>
              Back to Organizations
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  const tree = buildTree()

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate('/organizations')}
            className="p-2 hover:bg-slate-800 rounded-lg transition-colors"
          >
            <ArrowLeft className="w-5 h-5 text-slate-400" />
          </button>
          <div>
            <h1 className="text-3xl font-bold text-white">{organization.name}</h1>
            <p className="mt-1 text-slate-400">Organization Unit Details</p>
          </div>
        </div>
        <div className="flex gap-3">
          <Button
            variant="ghost"
            onClick={() => setShowEditModal(true)}
          >
            <Edit className="w-4 h-4 mr-2" />
            Edit
          </Button>
          <Button
            variant="ghost"
            onClick={handleDelete}
            className="text-red-500 hover:text-red-400 hover:bg-red-500/10"
          >
            <Trash2 className="w-4 h-4 mr-2" />
            Delete
          </Button>
        </div>
      </div>

      {/* On-Call Badge */}
      {isValidId && (
        <div className="mb-6">
          <OnCallBadge scopeType="organization" scopeId={orgId} />
        </div>
      )}

      {/* Overview */}
      <div className="mb-6">
        <Card>
          <CardHeader>
            <h2 className="text-xl font-semibold text-white">Overview</h2>
            <p className="text-sm text-slate-400 mt-1">
              Recursive view of all resources in this organization tree
            </p>
          </CardHeader>
          <CardContent>
            <div className="bg-slate-900 rounded-lg p-8 min-h-[400px] flex items-center justify-center">
              <div className="grid grid-cols-2 md:grid-cols-5 gap-8 w-full">
                {/* Sub-Organizations Bubble */}
                <div className="flex flex-col items-center">
                  <div
                    className="w-24 h-24 rounded-full bg-primary-500/20 border-2 border-primary-500 flex items-center justify-center cursor-pointer hover:bg-primary-500/30 transition-colors"
                    onClick={() => scrollToSection('hierarchy-section')}
                  >
                    <div className="text-center">
                      <Folder className="w-8 h-8 text-primary-400 mx-auto" />
                      <p className="text-2xl font-bold text-white mt-1">
                        {treeStats?.total_sub_organizations || 0}
                      </p>
                    </div>
                  </div>
                  <p className="text-sm text-slate-400 mt-2 text-center">Sub-Orgs</p>
                  <p className="text-xs text-slate-500 mt-0.5">(recursive)</p>
                </div>

                {/* Entities Bubble */}
                <div className="flex flex-col items-center">
                  <div
                    className="w-24 h-24 rounded-full bg-blue-500/20 border-2 border-blue-500 flex items-center justify-center cursor-pointer hover:bg-blue-500/30 transition-colors"
                    onClick={() => scrollToSection('hierarchy-section')}
                  >
                    <div className="text-center">
                      <Box className="w-8 h-8 text-blue-400 mx-auto" />
                      <p className="text-2xl font-bold text-white mt-1">
                        {treeStats?.total_entities || 0}
                      </p>
                    </div>
                  </div>
                  <p className="text-sm text-slate-400 mt-2 text-center">Entities</p>
                  <p className="text-xs text-slate-500 mt-0.5">(all levels)</p>
                </div>

                {/* Identities Bubble */}
                <div className="flex flex-col items-center">
                  <div
                    className="w-24 h-24 rounded-full bg-purple-500/20 border-2 border-purple-500 flex items-center justify-center cursor-pointer hover:bg-purple-500/30 transition-colors"
                    onClick={() => scrollToSection('identities-section')}
                  >
                    <div className="text-center">
                      <Users className="w-8 h-8 text-purple-400 mx-auto" />
                      <p className="text-2xl font-bold text-white mt-1">
                        {identities?.items?.length || 0}
                      </p>
                    </div>
                  </div>
                  <p className="text-sm text-slate-400 mt-2 text-center">Identities</p>
                  <p className="text-xs text-slate-500 mt-0.5">(this org)</p>
                </div>

                {/* Issues Bubble */}
                <div className="flex flex-col items-center">
                  <div
                    className="w-24 h-24 rounded-full bg-red-500/20 border-2 border-red-500 flex items-center justify-center cursor-pointer hover:bg-red-500/30 transition-colors"
                    onClick={() => scrollToSection('issues-section')}
                  >
                    <div className="text-center">
                      <svg className="w-8 h-8 text-red-400 mx-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                      </svg>
                      <p className="text-2xl font-bold text-white mt-1">
                        {treeStats?.total_issues || 0}
                      </p>
                    </div>
                  </div>
                  <p className="text-sm text-slate-400 mt-2 text-center">Issues</p>
                  <p className="text-xs text-slate-500 mt-0.5">({treeStats?.active_issues || 0} active)</p>
                </div>

                {/* Projects Bubble */}
                <div className="flex flex-col items-center">
                  <div
                    className="w-24 h-24 rounded-full bg-green-500/20 border-2 border-green-500 flex items-center justify-center cursor-pointer hover:bg-green-500/30 transition-colors"
                    onClick={() => scrollToSection('projects-section')}
                  >
                    <div className="text-center">
                      <svg className="w-8 h-8 text-green-400 mx-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
                      </svg>
                      <p className="text-2xl font-bold text-white mt-1">
                        {treeStats?.total_projects || 0}
                      </p>
                    </div>
                  </div>
                  <p className="text-sm text-slate-400 mt-2 text-center">Projects</p>
                  <p className="text-xs text-slate-500 mt-0.5">({treeStats?.active_projects || 0} active)</p>
                </div>
              </div>
            </div>

            {/* Connections Summary */}
            <div className="mt-6 grid grid-cols-2 md:grid-cols-4 gap-4">
              <div className="p-3 bg-slate-800/30 rounded-lg">
                <p className="text-xs text-slate-500">Total Resources</p>
                <p className="text-lg font-semibold text-white">
                  {(treeStats?.total_sub_organizations || 0) + (treeStats?.total_entities || 0) + (treeStats?.total_issues || 0) + (treeStats?.total_projects || 0)}
                </p>
              </div>
              <div className="p-3 bg-slate-800/30 rounded-lg">
                <p className="text-xs text-slate-500">Organizations in Tree</p>
                <p className="text-lg font-semibold text-white">
                  {treeStats?.organizations?.length || 1}
                </p>
              </div>
              <div className="p-3 bg-slate-800/30 rounded-lg">
                <p className="text-xs text-slate-500">Milestones</p>
                <p className="text-lg font-semibold text-white">
                  {treeStats?.total_milestones || 0}
                </p>
              </div>
              <div className="p-3 bg-slate-800/30 rounded-lg">
                <p className="text-xs text-slate-500">Tree Depth</p>
                <p className="text-lg font-semibold text-white">
                  {treeStats?.total_sub_organizations > 0 ? '2+' : '1'}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Main Info */}
        <div className="lg:col-span-2 space-y-6">
          {/* Organization Info Card */}
          <Card>
            <CardHeader>
              <h2 className="text-xl font-semibold text-white">Information</h2>
            </CardHeader>
            <CardContent>
              <dl className="grid grid-cols-1 gap-4">
                <div>
                  <dt className="text-sm font-medium text-slate-400">Name</dt>
                  <dd className="mt-1 text-sm text-white">{organization.name}</dd>
                </div>
                <div>
                  <dt className="text-sm font-medium text-slate-400">Type</dt>
                  <dd className="mt-1 text-sm text-white capitalize">
                    {organization.organization_type || 'organization'}
                  </dd>
                </div>
                {organization.description && (
                  <div>
                    <dt className="text-sm font-medium text-slate-400">Description</dt>
                    <dd className="mt-1 text-sm text-white">{organization.description}</dd>
                  </div>
                )}
                <div>
                  <dt className="text-sm font-medium text-slate-400">ID</dt>
                  <dd className="mt-1 text-sm text-white">{organization.id}</dd>
                </div>
                {organization.village_id && (
                  <div>
                    <dt className="text-sm font-medium text-slate-400">Village ID</dt>
                    <dd className="mt-1 flex items-center gap-2">
                      <a
                        href={`/id/${organization.village_id}`}
                        className="text-sm text-primary-400 hover:text-primary-300 font-mono"
                      >
                        {organization.village_id}
                      </a>
                      <button
                        onClick={() => {
                          navigator.clipboard.writeText(`${window.location.origin}/id/${organization.village_id}`)
                          toast.success('Village ID URL copied to clipboard')
                        }}
                        className="p-1 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition-colors"
                        title="Copy shareable link"
                      >
                        <Copy className="w-3.5 h-3.5" />
                      </button>
                    </dd>
                  </div>
                )}
                <div>
                  <dt className="text-sm font-medium text-slate-400">Created</dt>
                  <dd className="mt-1 text-sm text-white">
                    {new Date(organization.created_at).toLocaleString()}
                  </dd>
                </div>
                <div>
                  <dt className="text-sm font-medium text-slate-400">Last Updated</dt>
                  <dd className="mt-1 text-sm text-white">
                    {new Date(organization.updated_at).toLocaleString()}
                  </dd>
                </div>
              </dl>
            </CardContent>
          </Card>

          {/* Metadata Card */}
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <h2 className="text-xl font-semibold text-white">Metadata</h2>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setShowMetadataModal(true)}
                >
                  <Edit className="w-4 h-4 mr-2" />
                  Manage
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              {metadata?.metadata && Object.keys(metadata.metadata).length > 0 ? (
                <dl className="grid grid-cols-1 gap-4">
                  {Object.entries(metadata.metadata).map(([key, value]) => (
                    <div key={key}>
                      <dt className="text-sm font-medium text-slate-400">{key}</dt>
                      <dd className="mt-1 text-sm text-white">
                        {typeof value === 'object'
                          ? JSON.stringify(value)
                          : String(value)}
                      </dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <p className="text-sm text-slate-400">No metadata defined</p>
              )}
            </CardContent>
          </Card>

          {/* Issues Card */}
          <Card id="issues-section">
            <CardHeader>
              <div className="flex items-center justify-between">
                <h2 className="text-xl font-semibold text-white">Issues</h2>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => navigate(`/issues?organization_id=${id}`)}
                >
                  View All
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              {issues?.items && issues.items.length > 0 ? (
                <div className="space-y-3">
                  {issues.items.slice(0, 5).map((issue: any) => (
                    <div
                      key={issue.id}
                      className="p-3 bg-slate-800/30 rounded-lg hover:bg-slate-800/50 cursor-pointer transition-colors"
                      onClick={() => navigate(`/issues/${issue.id}`)}
                    >
                      <div className="flex items-start justify-between">
                        <div className="flex-1 min-w-0">
                          <h4 className="text-sm font-medium text-white truncate">
                            {issue.title}
                          </h4>
                          <div className="flex items-center gap-2 mt-1">
                            <span
                              className={`text-xs px-2 py-0.5 rounded ${
                                issue.status === 'open'
                                  ? 'bg-green-500/20 text-green-400'
                                  : issue.status === 'in_progress'
                                  ? 'bg-blue-500/20 text-blue-400'
                                  : 'bg-slate-500/20 text-slate-400'
                              }`}
                            >
                              {issue.status}
                            </span>
                            <span className="text-xs text-slate-500">
                              #{issue.id}
                            </span>
                          </div>
                        </div>
                      </div>
                    </div>
                  ))}
                  {issues.items.length > 5 && (
                    <p className="text-xs text-slate-500 text-center">
                      +{issues.items.length - 5} more
                    </p>
                  )}
                </div>
              ) : (
                <p className="text-sm text-slate-400">No issues</p>
              )}
            </CardContent>
          </Card>

          {/* Projects Card */}
          <Card id="projects-section">
            <CardHeader>
              <div className="flex items-center justify-between">
                <h2 className="text-xl font-semibold text-white">Projects</h2>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => navigate(`/projects?organization_id=${id}`)}
                >
                  View All
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              {projects?.items && projects.items.length > 0 ? (
                <div className="space-y-3">
                  {projects.items.slice(0, 5).map((project: any) => (
                    <div
                      key={project.id}
                      className="p-3 bg-slate-800/30 rounded-lg hover:bg-slate-800/50 cursor-pointer transition-colors"
                      onClick={() => navigate(`/projects/${project.id}`)}
                    >
                      <div className="flex items-start justify-between">
                        <div className="flex-1 min-w-0">
                          <h4 className="text-sm font-medium text-white truncate">
                            {project.name}
                          </h4>
                          <div className="flex items-center gap-2 mt-1">
                            <span
                              className={`text-xs px-2 py-0.5 rounded ${
                                project.status === 'active'
                                  ? 'bg-green-500/20 text-green-400'
                                  : project.status === 'planning'
                                  ? 'bg-blue-500/20 text-blue-400'
                                  : 'bg-slate-500/20 text-slate-400'
                              }`}
                            >
                              {project.status}
                            </span>
                          </div>
                        </div>
                      </div>
                    </div>
                  ))}
                  {projects.items.length > 5 && (
                    <p className="text-xs text-slate-500 text-center">
                      +{projects.items.length - 5} more
                    </p>
                  )}
                </div>
              ) : (
                <p className="text-sm text-slate-400">No projects</p>
              )}
            </CardContent>
          </Card>

          {/* Identities Card */}
          <Card id="identities-section">
            <CardHeader>
              <div className="flex items-center justify-between">
                <h2 className="text-xl font-semibold text-white">Identities</h2>
                <div className="flex gap-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setShowCreateIdentityModal(true)}
                  >
                    <Plus className="w-4 h-4 mr-1" />
                    Add
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => navigate(`/identities?organization_id=${id}`)}
                  >
                    View All
                  </Button>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              {identities?.items && identities.items.length > 0 ? (
                <div className="space-y-3">
                  {identities.items.slice(0, 5).map((identity: any) => (
                    <div
                      key={identity.id}
                      className="p-3 bg-slate-800/30 rounded-lg hover:bg-slate-800/50 cursor-pointer transition-colors"
                      onClick={() => navigate(`/identities/${identity.id}`)}
                    >
                      <div className="flex items-start justify-between">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <h4 className="text-sm font-medium text-white truncate">
                              {identity.username}
                            </h4>
                            {identity.portal_role && (
                              <span
                                className={`text-xs px-2 py-0.5 rounded ${
                                  identity.portal_role === 'admin'
                                    ? 'bg-red-500/20 text-red-400'
                                    : identity.portal_role === 'editor'
                                    ? 'bg-yellow-500/20 text-yellow-400'
                                    : 'bg-blue-500/20 text-blue-400'
                                }`}
                              >
                                {identity.portal_role}
                              </span>
                            )}
                          </div>
                          {identity.full_name && (
                            <p className="text-xs text-slate-400 mt-1">{identity.full_name}</p>
                          )}
                          {identity.email && (
                            <p className="text-xs text-slate-500 mt-0.5">{identity.email}</p>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                  {identities.items.length > 5 && (
                    <p className="text-xs text-slate-500 text-center">
                      +{identities.items.length - 5} more
                    </p>
                  )}
                </div>
              ) : (
                <p className="text-sm text-slate-400">No identities</p>
              )}
            </CardContent>
          </Card>

          {/* Relationship Graph */}
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-xl font-semibold text-white">Relationship Map</h2>
                  <p className="text-sm text-slate-400 mt-1">
                    Interactive graph showing connections up to 3 hops away
                  </p>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => navigate(`/relationships/${id}`)}
                >
                  Full Screen
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              <RelationshipGraphSection organizationId={id!} />
            </CardContent>
          </Card>
        </div>

        {/* Hierarchy Tree */}
        <div className="lg:col-span-1">
          <Card id="hierarchy-section">
            <CardHeader>
              <h2 className="text-xl font-semibold text-white">Hierarchy</h2>
            </CardHeader>
            <CardContent>
              {tree.length > 0 ? (
                <div className="space-y-1">
                  {tree.map((node) => renderTreeNode(node))}
                </div>
              ) : (
                <p className="text-sm text-slate-400">
                  No child organizations or entities
                </p>
              )}
              <div className="mt-6 pt-6 border-t border-slate-700">
                <p className="text-xs text-slate-500 mb-3">
                  {childOrgs?.items?.length || 0} sub-organization(s)
                  <br />
                  {entities?.items?.length || 0} entity/entities
                </p>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setShowCreateOrgModal(true)}
                  className="w-full justify-center"
                >
                  Add Sub-Organization
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setShowCreateEntityModal(true)}
                  className="w-full justify-center mt-2"
                >
                  Add Entity
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>

      {/* Create Sub-Organization Modal */}
      {showCreateOrgModal && (
        <CreateOrganizationModal
          parentId={orgId}
          onClose={() => setShowCreateOrgModal(false)}
          onSuccess={async () => {
            await queryClient.invalidateQueries({ queryKey: ['organizations', { parent_id: id }] })
            // Invalidate parent organization to update counts
            await queryClient.invalidateQueries({ queryKey: ['organization', id] })
            // Invalidate tree stats
            await queryClient.invalidateQueries({ queryKey: ['organization-tree-stats', id] })
            // Invalidate global organizations list
            await queryClient.invalidateQueries({ queryKey: ['organizations'] })
            setShowCreateOrgModal(false)
          }}
        />
      )}

      {/* Create Entity Modal */}
      {showCreateEntityModal && (
        <CreateEntityModal
          organizationId={orgId}
          onClose={() => setShowCreateEntityModal(false)}
          onSuccess={async () => {
            await queryClient.invalidateQueries({ queryKey: ['entities', { organization_id: id }] })
            // Invalidate parent organization to update counts
            await queryClient.invalidateQueries({ queryKey: ['organization', id] })
            // Invalidate tree stats
            await queryClient.invalidateQueries({ queryKey: ['organization-tree-stats', id] })
            // Invalidate global entities list
            await queryClient.invalidateQueries({ queryKey: ['entities'] })
            setShowCreateEntityModal(false)
          }}
        />
      )}

      {/* Metadata Modal */}
      {showMetadataModal && (
        <MetadataModal
          organizationId={orgId}
          onClose={() => setShowMetadataModal(false)}
          onSuccess={async () => {
            await queryClient.invalidateQueries({ queryKey: ['organization-metadata', id] })
            setShowMetadataModal(false)
          }}
        />
      )}

      {/* Edit Organization Modal */}
      {showEditModal && (
        <EditOrganizationModal
          organization={organization}
          onClose={() => setShowEditModal(false)}
          onSuccess={async () => {
            await queryClient.invalidateQueries({ queryKey: ['organization', id] })
            setShowEditModal(false)
          }}
        />
      )}

      {/* Create Identity Modal */}
      <CreateIdentityModal
        isOpen={showCreateIdentityModal}
        onClose={() => setShowCreateIdentityModal(false)}
        defaultTenantId={organization.tenant_id}
        defaultOrganizationId={orgId}
        defaultIsPortalUser={false}
        defaultPermissionScope="organization"
        invalidateQueryKeys={[
          ['identities', 'organization', orgId],
          ['identities'],
        ]}
      />
    </div>
  )
}

interface CreateOrganizationModalProps {
  parentId: number
  onClose: () => void
  onSuccess: () => void
}

function CreateOrganizationModal({ parentId, onClose, onSuccess }: CreateOrganizationModalProps) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')

  const createMutation = useMutation({
    mutationFn: (data: { name: string; description?: string; parent_id: number }) =>
      api.createOrganization(data),
    onSuccess: () => {
      toast.success('Organization created successfully')
      onSuccess()
    },
    onError: () => {
      toast.error('Failed to create organization')
    },
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    createMutation.mutate({
      name: name.trim(),
      description: description?.trim() || undefined,
      parent_id: parentId,
    })
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-md">
        <CardHeader>
          <h2 className="text-xl font-semibold text-white">Create Sub-Organization</h2>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <Input
              label="Name"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Enter organization name"
            />
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1.5">
                Description
              </label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Enter description (optional)"
                rows={3}
                className="block w-full px-4 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-slate-900 focus:ring-primary-500 focus:border-primary-500"
              />
            </div>
            <div className="flex justify-end gap-3 mt-6">
              <Button type="button" variant="ghost" onClick={onClose}>
                Cancel
              </Button>
              <Button type="submit" isLoading={createMutation.isPending}>
                Create
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}

interface CreateEntityModalProps {
  organizationId: number
  onClose: () => void
  onSuccess: () => void
}

function CreateEntityModal({ organizationId, onClose, onSuccess }: CreateEntityModalProps) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [entityType, setEntityType] = useState('compute')

  const ENTITY_TYPES = [
    { value: 'application', label: 'Application' },
    { value: 'service', label: 'Service' },
    { value: 'repository', label: 'Repository' },
    { value: 'datacenter', label: 'Datacenter' },
    { value: 'vpc', label: 'VPC' },
    { value: 'subnet', label: 'Subnet' },
    { value: 'compute', label: 'Compute' },
    { value: 'network', label: 'Network' },
    { value: 'storage', label: 'Storage' },
    { value: 'database', label: 'Database' },
    { value: 'user', label: 'User' },
    { value: 'security_issue', label: 'Security Issue' },
  ]

  const createMutation = useMutation({
    mutationFn: (data: any) => api.createEntity(data),
    onSuccess: () => {
      toast.success('Entity created successfully')
      onSuccess()
    },
    onError: () => {
      toast.error('Failed to create entity')
    },
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    createMutation.mutate({
      name: name.trim(),
      description: description?.trim() || undefined,
      entity_type: entityType,
      organization_id: organizationId,
    })
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-md">
        <CardHeader>
          <h2 className="text-xl font-semibold text-white">Create Entity</h2>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <Input
              label="Name"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Enter entity name"
            />
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1.5">
                Type
              </label>
              <select
                required
                value={entityType}
                onChange={(e) => setEntityType(e.target.value)}
                className="block w-full px-4 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-slate-900 focus:ring-primary-500 focus:border-primary-500"
              >
                {ENTITY_TYPES.map((type) => (
                  <option key={type.value} value={type.value}>
                    {type.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1.5">
                Description
              </label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Enter description (optional)"
                rows={3}
                className="block w-full px-4 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-slate-900 focus:ring-primary-500 focus:border-primary-500"
              />
            </div>
            <div className="flex justify-end gap-3 mt-6">
              <Button type="button" variant="ghost" onClick={onClose}>
                Cancel
              </Button>
              <Button type="submit" isLoading={createMutation.isPending}>
                Create
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}

interface MetadataModalProps {
  organizationId: number
  onClose: () => void
  onSuccess: () => void
}

function MetadataModal({ organizationId, onClose, onSuccess: _onSuccess }: MetadataModalProps) {
  const [key, setKey] = useState('')
  const [value, setValue] = useState('')
  const [fieldType, setFieldType] = useState('string')
  const [editingKey, setEditingKey] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const { data: metadataResponse } = useQuery({
    queryKey: ['organization-metadata', organizationId],
    queryFn: () => api.getOrganizationMetadata(organizationId),
  })

  const metadata = metadataResponse?.metadata || {}

  const createMutation = useMutation({
    mutationFn: (data: { key: string; field_type: string; value: any }) =>
      api.createOrganizationMetadata(organizationId, data),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['organization-metadata', organizationId] })
      toast.success('Metadata field created successfully')
      setKey('')
      setValue('')
      setFieldType('string')
    },
    onError: () => {
      toast.error('Failed to create metadata field')
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ key, value }: { key: string; value: any }) =>
      api.updateOrganizationMetadata(organizationId, key, { value }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['organization-metadata', organizationId] })
      toast.success('Metadata field updated successfully')
      setEditingKey(null)
      setValue('')
    },
    onError: () => {
      toast.error('Failed to update metadata field')
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (key: string) => api.deleteOrganizationMetadata(organizationId, key),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['organization-metadata', organizationId] })
      toast.success('Metadata field deleted successfully')
    },
    onError: () => {
      toast.error('Failed to delete metadata field')
    },
  })

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault()
    createMutation.mutate({ key, field_type: fieldType, value })
  }

  const handleUpdate = (key: string) => {
    updateMutation.mutate({ key, value })
  }

  const handleDelete = (key: string) => {
    if (window.confirm(`Are you sure you want to delete the "${key}" metadata field?`)) {
      deleteMutation.mutate(key)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <CardHeader>
          <h2 className="text-xl font-semibold text-white">Manage Metadata</h2>
        </CardHeader>
        <CardContent>
          {/* Add New Metadata Field */}
          <form onSubmit={handleCreate} className="space-y-4 mb-6 pb-6 border-b border-slate-700">
            <h3 className="text-sm font-medium text-slate-300">Add New Field</h3>
            <div className="grid grid-cols-3 gap-3">
              <Input
                label="Key"
                required
                value={key}
                onChange={(e) => setKey(e.target.value)}
                placeholder="field_name"
              />
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-1.5">
                  Type
                </label>
                <select
                  value={fieldType}
                  onChange={(e) => setFieldType(e.target.value)}
                  className="block w-full px-4 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-slate-900 focus:ring-primary-500 focus:border-primary-500"
                >
                  <option value="string">String</option>
                  <option value="number">Number</option>
                  <option value="boolean">Boolean</option>
                  <option value="date">Date</option>
                  <option value="json">JSON</option>
                </select>
              </div>
              <Input
                label="Value"
                required
                value={value}
                onChange={(e) => setValue(e.target.value)}
                placeholder="value"
              />
            </div>
            <Button type="submit" isLoading={createMutation.isPending}>
              Add Field
            </Button>
          </form>

          {/* Existing Metadata Fields */}
          <div className="space-y-3">
            <h3 className="text-sm font-medium text-slate-300">Existing Fields</h3>
            {Object.keys(metadata).length > 0 ? (
              <div className="space-y-2">
                {Object.entries(metadata).map(([metaKey, metaValue]) => (
                  <div
                    key={metaKey}
                    className="p-3 bg-slate-800/30 rounded-lg border border-slate-700"
                  >
                    {editingKey === metaKey ? (
                      <div className="flex items-center gap-2">
                        <Input
                          value={value}
                          onChange={(e) => setValue(e.target.value)}
                          placeholder="New value"
                          className="flex-1"
                        />
                        <Button
                          size="sm"
                          onClick={() => handleUpdate(metaKey)}
                          isLoading={updateMutation.isPending}
                        >
                          Save
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => {
                            setEditingKey(null)
                            setValue('')
                          }}
                        >
                          Cancel
                        </Button>
                      </div>
                    ) : (
                      <div className="flex items-center justify-between">
                        <div className="flex-1">
                          <dt className="text-sm font-medium text-slate-400">{metaKey}</dt>
                          <dd className="mt-1 text-sm text-white">
                            {typeof metaValue === 'object'
                              ? JSON.stringify(metaValue)
                              : String(metaValue)}
                          </dd>
                        </div>
                        <div className="flex gap-2">
                          <button
                            onClick={() => {
                              setEditingKey(metaKey)
                              setValue(String(metaValue))
                            }}
                            className="p-1.5 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition-colors"
                          >
                            <Edit className="w-4 h-4" />
                          </button>
                          <button
                            onClick={() => handleDelete(metaKey)}
                            className="p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-500/10 rounded transition-colors"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-slate-400">No metadata fields defined</p>
            )}
          </div>

          {/* Modal Actions */}
          <div className="flex justify-end gap-3 mt-6 pt-6 border-t border-slate-700">
            <Button type="button" variant="ghost" onClick={onClose}>
              Close
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

interface EditOrganizationModalProps {
  organization: Organization
  onClose: () => void
  onSuccess: () => void
}

function EditOrganizationModal({ organization, onClose, onSuccess }: EditOrganizationModalProps) {
  const [name, setName] = useState(organization.name)
  const [description, setDescription] = useState(organization.description || '')
  const [organizationType, setOrganizationType] = useState(organization.organization_type || 'organization')

  const ORGANIZATION_TYPES = [
    { value: 'department', label: 'Department' },
    { value: 'organization', label: 'Organization' },
    { value: 'team', label: 'Team' },
    { value: 'collection', label: 'Collection' },
    { value: 'other', label: 'Other' },
  ]

  const updateMutation = useMutation({
    mutationFn: (data: { name: string; description?: string; organization_type?: string }) =>
      api.updateOrganization(organization.id, data),
    onSuccess: () => {
      toast.success('Organization updated successfully')
      onSuccess()
    },
    onError: () => {
      toast.error('Failed to update organization')
    },
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    updateMutation.mutate({
      name: name.trim(),
      description: description?.trim() || undefined,
      organization_type: organizationType,
    })
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-md">
        <CardHeader>
          <h2 className="text-xl font-semibold text-white">Edit Organization</h2>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <Input
              label="Name"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Enter organization name"
            />
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1.5">
                Type
              </label>
              <select
                required
                value={organizationType}
                onChange={(e) => setOrganizationType(e.target.value)}
                className="block w-full px-4 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-slate-900 focus:ring-primary-500 focus:border-primary-500"
              >
                {ORGANIZATION_TYPES.map((type) => (
                  <option key={type.value} value={type.value}>
                    {type.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1.5">
                Description
              </label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Enter description (optional)"
                rows={3}
                className="block w-full px-4 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-slate-900 focus:ring-primary-500 focus:border-primary-500"
              />
            </div>
            <div className="flex justify-end gap-3 mt-6">
              <Button type="button" variant="ghost" onClick={onClose}>
                Cancel
              </Button>
              <Button type="submit" isLoading={updateMutation.isPending}>
                Save Changes
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
