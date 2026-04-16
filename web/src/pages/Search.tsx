import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Search as SearchIcon, Tag, Folder, Box, MessageSquare, User, X } from 'lucide-react'
import api from '@/lib/api'
import Card, { CardContent } from '@/components/Card'
import Input from '@/components/Input'

type ResourceType = 'all' | 'organization' | 'entity' | 'issue' | 'identity'

export default function Search() {
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()

  const [keyword, setKeyword] = useState(searchParams.get('q') || '')
  const [selectedLabels, setSelectedLabels] = useState<number[]>([])
  const [resourceType, setResourceType] = useState<ResourceType>('all')

  const { data: labels } = useQuery({
    queryKey: ['labels-all'],
    queryFn: () => api.getLabels({ per_page: 1000 }),
  })

  // Search all resource types
  const { data: orgResults, isLoading: orgLoading } = useQuery({
    queryKey: ['search-orgs', keyword, selectedLabels],
    queryFn: () => api.getOrganizations({ search: keyword }),
    enabled: (resourceType === 'all' || resourceType === 'organization') && keyword.length > 0,
  })

  const { data: entityResults, isLoading: entityLoading } = useQuery({
    queryKey: ['search-entities', keyword, selectedLabels],
    queryFn: () => api.getEntities({ search: keyword }),
    enabled: (resourceType === 'all' || resourceType === 'entity') && keyword.length > 0,
  })

  const { data: issueResults, isLoading: issueLoading } = useQuery({
    queryKey: ['search-issues', keyword, selectedLabels],
    queryFn: () => api.getIssues({ search: keyword }),
    enabled: (resourceType === 'all' || resourceType === 'issue') && keyword.length > 0,
  })

  const { data: identityResults, isLoading: identityLoading } = useQuery({
    queryKey: ['search-identities', keyword, selectedLabels],
    queryFn: () => api.getIdentities({ search: keyword }),
    enabled: (resourceType === 'all' || resourceType === 'identity') && keyword.length > 0,
  })

  const isLoading = orgLoading || entityLoading || issueLoading || identityLoading

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault()
    setSearchParams({ q: keyword })
  }

  const toggleLabel = (labelId: number) => {
    setSelectedLabels(prev =>
      prev.includes(labelId)
        ? prev.filter(id => id !== labelId)
        : [...prev, labelId]
    )
  }

  const getIcon = (type: string) => {
    switch (type) {
      case 'organization':
        return <Folder className="w-5 h-5 text-primary-400" />
      case 'entity':
        return <Box className="w-5 h-5 text-blue-400" />
      case 'issue':
        return <MessageSquare className="w-5 h-5 text-orange-400" />
      case 'identity':
        return <User className="w-5 h-5 text-green-400" />
      default:
        return <SearchIcon className="w-5 h-5 text-slate-400" />
    }
  }

  const getNavigateUrl = (type: string, id: number) => {
    switch (type) {
      case 'organization':
        return `/organizations/${id}`
      case 'entity':
        return `/entities/${id}`
      case 'issue':
        return `/issues/${id}`
      case 'identity':
        return `/identities/${id}`
      default:
        return '#'
    }
  }

  const totalResults = (orgResults?.items?.length || 0) +
                       (entityResults?.items?.length || 0) +
                       (issueResults?.items?.length || 0) +
                       (identityResults?.items?.length || 0)

  return (
    <div className="p-8">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-white mb-2">Search</h1>
        <p className="text-slate-400">
          Search across organizations, entities, issues, and identities
        </p>
      </div>

      {/* Search Form */}
      <form onSubmit={handleSearch} className="mb-6">
        <div className="relative">
          <SearchIcon className="absolute left-4 top-1/2 transform -translate-y-1/2 w-5 h-5 text-slate-400" />
          <Input
            type="text"
            placeholder="Search by keyword..."
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            className="pl-12 text-lg py-3"
            autoFocus
          />
        </div>
      </form>

      {/* Filters */}
      <div className="mb-6 space-y-4">
        {/* Resource Type Filter */}
        <div>
          <label className="block text-sm font-medium text-slate-300 mb-2">
            Filter by Type
          </label>
          <div className="flex flex-wrap gap-2">
            {(['all', 'organization', 'entity', 'issue', 'identity'] as ResourceType[]).map((type) => (
              <button
                key={type}
                type="button"
                onClick={() => setResourceType(type)}
                className={`px-3 py-1.5 text-sm rounded-lg transition-colors ${
                  resourceType === type
                    ? 'bg-primary-500 text-white'
                    : 'bg-slate-800 text-slate-300 hover:bg-slate-700'
                }`}
              >
                {type === 'all' ? 'All' : type.charAt(0).toUpperCase() + type.slice(1) + 's'}
              </button>
            ))}
          </div>
        </div>

        {/* Label Filter */}
        {labels?.items && labels.items.length > 0 && (
          <div>
            <label className="block text-sm font-medium text-slate-300 mb-2">
              Filter by Labels
            </label>
            <div className="flex flex-wrap gap-2">
              {labels.items.map((label: any) => (
                <button
                  key={label.id}
                  type="button"
                  onClick={() => toggleLabel(label.id)}
                  className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg transition-all ${
                    selectedLabels.includes(label.id)
                      ? 'ring-2 ring-white'
                      : 'opacity-70 hover:opacity-100'
                  }`}
                  style={{
                    backgroundColor: `${label.color || '#64748b'}20`,
                    color: label.color || '#64748b'
                  }}
                >
                  <Tag className="w-3 h-3" />
                  {label.name}
                  {selectedLabels.includes(label.id) && (
                    <X className="w-3 h-3" />
                  )}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Results */}
      {keyword.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <SearchIcon className="w-16 h-16 text-slate-600 mx-auto mb-4" />
            <p className="text-slate-400 text-lg">Enter a search term to get started</p>
            <p className="text-slate-500 text-sm mt-2">
              Search across organizations, entities, issues, and identities
            </p>
          </CardContent>
        </Card>
      ) : isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : totalResults === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <SearchIcon className="w-16 h-16 text-slate-600 mx-auto mb-4" />
            <p className="text-slate-400 text-lg">No results found for "{keyword}"</p>
            <p className="text-slate-500 text-sm mt-2">
              Try adjusting your search terms or filters
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-6">
          {/* Result Count */}
          <div className="text-sm text-slate-400">
            Found {totalResults} result{totalResults !== 1 ? 's' : ''} for "{keyword}"
          </div>

          {/* Organizations */}
          {(resourceType === 'all' || resourceType === 'organization') && orgResults?.items && orgResults.items.length > 0 && (
            <div>
              <h2 className="text-lg font-semibold text-white mb-3 flex items-center gap-2">
                <Folder className="w-5 h-5 text-primary-400" />
                Organization Units ({orgResults.items.length})
              </h2>
              <div className="space-y-2">
                {orgResults.items.map((org: any) => (
                  <Card
                    key={org.id}
                    className="cursor-pointer hover:ring-2 hover:ring-primary-500 transition-all"
                    onClick={() => navigate(getNavigateUrl('organization', org.id))}
                  >
                    <CardContent className="py-3">
                      <div className="flex items-center gap-3">
                        {getIcon('organization')}
                        <div className="flex-1 min-w-0">
                          <h3 className="text-white font-medium truncate">{org.name}</h3>
                          {org.description && (
                            <p className="text-sm text-slate-400 truncate">{org.description}</p>
                          )}
                        </div>
                        <span className="text-xs text-slate-500">ID: {org.id}</span>
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            </div>
          )}

          {/* Entities */}
          {(resourceType === 'all' || resourceType === 'entity') && entityResults?.items && entityResults.items.length > 0 && (
            <div>
              <h2 className="text-lg font-semibold text-white mb-3 flex items-center gap-2">
                <Box className="w-5 h-5 text-blue-400" />
                Entities ({entityResults.items.length})
              </h2>
              <div className="space-y-2">
                {entityResults.items.map((entity: any) => (
                  <Card
                    key={entity.id}
                    className="cursor-pointer hover:ring-2 hover:ring-primary-500 transition-all"
                    onClick={() => navigate(getNavigateUrl('entity', entity.id))}
                  >
                    <CardContent className="py-3">
                      <div className="flex items-center gap-3">
                        {getIcon('entity')}
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <h3 className="text-white font-medium truncate">{entity.name}</h3>
                            <span className="text-xs px-2 py-0.5 bg-blue-500/20 text-blue-400 rounded">
                              {entity.type}
                            </span>
                          </div>
                          {entity.description && (
                            <p className="text-sm text-slate-400 truncate">{entity.description}</p>
                          )}
                        </div>
                        <span className="text-xs text-slate-500">ID: {entity.id}</span>
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            </div>
          )}

          {/* Issues */}
          {(resourceType === 'all' || resourceType === 'issue') && issueResults?.items && issueResults.items.length > 0 && (
            <div>
              <h2 className="text-lg font-semibold text-white mb-3 flex items-center gap-2">
                <MessageSquare className="w-5 h-5 text-orange-400" />
                Issues ({issueResults.items.length})
              </h2>
              <div className="space-y-2">
                {issueResults.items.map((issue: any) => (
                  <Card
                    key={issue.id}
                    className="cursor-pointer hover:ring-2 hover:ring-primary-500 transition-all"
                    onClick={() => navigate(getNavigateUrl('issue', issue.id))}
                  >
                    <CardContent className="py-3">
                      <div className="flex items-center gap-3">
                        {getIcon('issue')}
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 mb-1">
                            <h3 className="text-white font-medium truncate">{issue.title}</h3>
                            <span className={`text-xs px-2 py-0.5 rounded ${
                              issue.status === 'open'
                                ? 'bg-green-500/20 text-green-400'
                                : issue.status === 'in_progress'
                                ? 'bg-blue-500/20 text-blue-400'
                                : 'bg-slate-500/20 text-slate-400'
                            }`}>
                              {issue.status}
                            </span>
                          </div>
                          {issue.description && (
                            <p className="text-sm text-slate-400 line-clamp-1">{issue.description}</p>
                          )}
                        </div>
                        <span className="text-xs text-slate-500">#{issue.id}</span>
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            </div>
          )}

          {/* Identities */}
          {(resourceType === 'all' || resourceType === 'identity') && identityResults?.items && identityResults.items.length > 0 && (
            <div>
              <h2 className="text-lg font-semibold text-white mb-3 flex items-center gap-2">
                <User className="w-5 h-5 text-green-400" />
                Identities ({identityResults.items.length})
              </h2>
              <div className="space-y-2">
                {identityResults.items.map((identity: any) => (
                  <Card
                    key={identity.id}
                    className="cursor-pointer hover:ring-2 hover:ring-primary-500 transition-all"
                    onClick={() => navigate(getNavigateUrl('identity', identity.id))}
                  >
                    <CardContent className="py-3">
                      <div className="flex items-center gap-3">
                        {getIcon('identity')}
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <h3 className="text-white font-medium truncate">
                              {identity.full_name || identity.username}
                            </h3>
                            <span className="text-xs px-2 py-0.5 bg-green-500/20 text-green-400 rounded">
                              {identity.identity_type}
                            </span>
                          </div>
                          {identity.email && (
                            <p className="text-sm text-slate-400 truncate">{identity.email}</p>
                          )}
                        </div>
                        <span className="text-xs text-slate-500">ID: {identity.id}</span>
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
