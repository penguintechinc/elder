import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Search, Calendar, Plus } from 'lucide-react'
import { queryKeys } from '@/lib/queryKeys'
import api from '@/lib/api'
import Card, { CardContent } from '@/components/Card'
import Input from '@/components/Input'


const SEVERITY_LEVELS = [
  { value: 'all', label: 'All', color: 'bg-slate-500/20 text-slate-400' },
  { value: 'critical', label: 'Critical', color: 'bg-red-500/20 text-red-400' },
  { value: 'high', label: 'High', color: 'bg-orange-500/20 text-orange-400' },
  { value: 'medium', label: 'Medium', color: 'bg-yellow-500/20 text-yellow-400' },
  { value: 'low', label: 'Low', color: 'bg-green-500/20 text-green-400' },
]

const STATUS_OPTIONS = [
  { value: 'all', label: 'All Statuses' },
  { value: 'open', label: 'Open' },
  { value: 'in_progress', label: 'In Progress' },
  { value: 'resolved', label: 'Resolved' },
  { value: 'accepted_risk', label: 'Accepted Risk' },
]

export default function Vulnerabilities() {
  const [search, setSearch] = useState('')
  const [severityFilter, setSeverityFilter] = useState<string>('all')
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [assignModalOpen, setAssignModalOpen] = useState(false)
  const [selectedVulnerability, setSelectedVulnerability] = useState<any>(null)
  const [assignParentType, setAssignParentType] = useState<string>('service')
  const queryClient = useQueryClient()

  // Fetch services and software for assignment dropdowns
  const { data: servicesData } = useQuery({
    queryKey: queryKeys.services.all,
    queryFn: () => api.getServices({ per_page: 200 }),
  })

  const { data: softwareData } = useQuery({
    queryKey: queryKeys.software.all,
    queryFn: () => api.getSoftware({ per_page: 200 }),
  })

  const assignMutation = useMutation({
    mutationFn: (data: { vulnId: number; parent_type: 'service' | 'software'; parent_id: number; notes?: string }) =>
      api.assignVulnerability(data.vulnId, {
        parent_type: data.parent_type,
        parent_id: data.parent_id,
        notes: data.notes,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.vulnerabilities.all })
      setAssignModalOpen(false)
      setSelectedVulnerability(null)
    },
  })

  // Mock data - replace with actual API calls when backend is ready
  const mockVulnerabilities = [
    {
      id: 1,
      cve_id: 'CVE-2024-1234',
      severity: 'critical',
      component: 'OpenSSL',
      status: 'open',
      published_date: '2024-01-15',
      description: 'Critical vulnerability in cryptographic operations',
      affected_versions: ['1.1.1', '3.0.0'],
      affected_entities: [{ parent_type: 'service', parent_name: 'Auth Service', source_file: 'requirements.txt' }],
    },
    {
      id: 2,
      cve_id: 'CVE-2024-5678',
      severity: 'high',
      component: 'Log4j',
      status: 'in_progress',
      published_date: '2024-02-10',
      description: 'Remote code execution in logging library',
      affected_versions: ['2.14.0', '2.15.0'],
      affected_entities: [{ parent_type: 'software', parent_name: 'Logging Platform', source_file: 'pom.xml' }],
    },
    {
      id: 3,
      cve_id: 'CVE-2024-9012',
      severity: 'medium',
      component: 'Django',
      status: 'resolved',
      published_date: '2024-03-05',
      description: 'SQL injection in query processing',
      affected_versions: ['3.2.0', '4.0.0'],
      affected_entities: [{ parent_type: 'service', parent_name: 'Web API', source_file: 'requirements.txt' }],
    },
    {
      id: 4,
      cve_id: 'CVE-2024-3456',
      severity: 'low',
      component: 'Python',
      status: 'open',
      published_date: '2024-01-20',
      description: 'Information disclosure in stdlib',
      affected_versions: ['3.11.0'],
      affected_entities: [],
    },
  ]

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.vulnerabilities.list({ search, severity: severityFilter, status: statusFilter }),
    queryFn: () => api.getVulnerabilities({
      search: search || undefined,
      severity: severityFilter !== 'all' ? severityFilter : undefined,
      status: statusFilter !== 'all' ? statusFilter : undefined,
    }),
    placeholderData: {
      items: mockVulnerabilities,
      total: mockVulnerabilities.length,
      page: 1,
      per_page: 50,
      pages: 1,
    },
  })

  const stats = {
    total: mockVulnerabilities.length,
    critical: mockVulnerabilities.filter(v => v.severity === 'critical').length,
    high: mockVulnerabilities.filter(v => v.severity === 'high').length,
    medium: mockVulnerabilities.filter(v => v.severity === 'medium').length,
    low: mockVulnerabilities.filter(v => v.severity === 'low').length,
  }

  const getSeverityColor = (severity: string): string => {
    const colorMap: { [key: string]: string } = {
      'critical': 'bg-red-500/10 text-red-400 border border-red-500/20',
      'high': 'bg-orange-500/10 text-orange-400 border border-orange-500/20',
      'medium': 'bg-yellow-500/10 text-yellow-400 border border-yellow-500/20',
      'low': 'bg-green-500/10 text-green-400 border border-green-500/20',
    }
    return colorMap[severity] || 'bg-slate-500/10 text-slate-400'
  }

  const getStatusColor = (status: string): string => {
    const colorMap: { [key: string]: string } = {
      'open': 'bg-red-500/20 text-red-400',
      'in_progress': 'bg-blue-500/20 text-blue-400',
      'resolved': 'bg-green-500/20 text-green-400',
      'accepted_risk': 'bg-slate-500/20 text-slate-400',
    }
    return colorMap[status] || 'bg-slate-500/20 text-slate-400'
  }

  const formatDate = (date: string): string => {
    return new Date(date).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })
  }

  return (
    <div className="p-8">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-white">Vulnerabilities</h1>
        <p className="mt-2 text-slate-400">
          Track and manage security vulnerabilities across your infrastructure
        </p>
      </div>

      {/* Dashboard Stats */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-8">
        <Card>
          <CardContent className="p-4">
            <div className="text-sm text-slate-400 mb-1">Total</div>
            <div className="text-2xl font-bold text-white">{stats.total}</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="text-sm text-red-400 mb-1">Critical</div>
            <div className="text-2xl font-bold text-red-400">{stats.critical}</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="text-sm text-orange-400 mb-1">High</div>
            <div className="text-2xl font-bold text-orange-400">{stats.high}</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="text-sm text-yellow-400 mb-1">Medium</div>
            <div className="text-2xl font-bold text-yellow-400">{stats.medium}</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="text-sm text-green-400 mb-1">Low</div>
            <div className="text-2xl font-bold text-green-400">{stats.low}</div>
          </CardContent>
        </Card>
      </div>

      {/* Severity Filter Tabs */}
      <div className="flex flex-wrap gap-2 mb-6">
        {SEVERITY_LEVELS.map((level) => (
          <button
            key={level.value}
            onClick={() => setSeverityFilter(level.value)}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${
              severityFilter === level.value
                ? `${level.color} ring-2 ring-offset-2 ring-offset-slate-900`
                : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
            }`}
          >
            {level.label}
          </button>
        ))}
      </div>

      {/* Filters */}
      <div className="flex flex-col gap-4 mb-6 lg:flex-row lg:items-end">
        <div className="flex-1 max-w-md">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-slate-400" />
            <Input
              type="text"
              placeholder="Search CVE ID, component..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-10"
            />
          </div>
        </div>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="px-4 py-2 bg-slate-800 border border-slate-700 rounded-lg text-slate-200 hover:border-slate-600 transition-colors"
        >
          {STATUS_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>

      {/* Vulnerabilities Table */}
      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : data?.items?.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <AlertTriangle className="w-16 h-16 text-slate-600 mx-auto mb-4" />
            <p className="text-slate-400">No vulnerabilities found</p>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-slate-700 bg-slate-800/50">
                    <th className="px-6 py-4 text-left text-xs font-semibold text-slate-400 uppercase">CVE ID</th>
                    <th className="px-6 py-4 text-left text-xs font-semibold text-slate-400 uppercase">Severity</th>
                    <th className="px-6 py-4 text-left text-xs font-semibold text-slate-400 uppercase">Component</th>
                    <th className="px-6 py-4 text-left text-xs font-semibold text-slate-400 uppercase">Service / Software</th>
                    <th className="px-6 py-4 text-left text-xs font-semibold text-slate-400 uppercase">Source File</th>
                    <th className="px-6 py-4 text-left text-xs font-semibold text-slate-400 uppercase">Status</th>
                    <th className="px-6 py-4 text-left text-xs font-semibold text-slate-400 uppercase">Published</th>
                    <th className="px-6 py-4 text-left text-xs font-semibold text-slate-400 uppercase">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {data?.items?.map((vuln: any, idx: number) => (
                    <tr
                      key={vuln.id}
                      className={`border-b border-slate-700 hover:bg-slate-800/50 transition-colors ${
                        idx % 2 === 0 ? 'bg-slate-900/30' : ''
                      }`}
                    >
                      <td className="px-6 py-4">
                        <a href={`https://cve.mitre.org/cgi-bin/cvename.cgi?name=${vuln.cve_id}`} target="_blank" rel="noopener noreferrer">
                          <span className="text-primary-400 hover:text-primary-300 font-mono text-sm">
                            {vuln.cve_id}
                          </span>
                        </a>
                      </td>
                      <td className="px-6 py-4">
                        <span className={`px-3 py-1 rounded-full text-xs font-semibold ${getSeverityColor(vuln.severity)}`}>
                          {vuln.severity.charAt(0).toUpperCase() + vuln.severity.slice(1)}
                        </span>
                      </td>
                      <td className="px-6 py-4">
                        <div>
                          <div className="text-white text-sm font-medium">{vuln.component}</div>
                          <div className="text-slate-400 text-xs mt-1">{vuln.description}</div>
                        </div>
                      </td>
                      <td className="px-6 py-4">
                        <div className="flex flex-wrap gap-1">
                          {vuln.affected_entities?.map((entity: any, i: number) => (
                            <span
                              key={i}
                              className={`px-2 py-0.5 rounded text-xs font-medium ${
                                entity.parent_type === 'service'
                                  ? 'bg-blue-500/10 text-blue-400 border border-blue-500/20'
                                  : 'bg-purple-500/10 text-purple-400 border border-purple-500/20'
                              }`}
                            >
                              {entity.parent_name}
                            </span>
                          )) || <span className="text-slate-500 text-xs">—</span>}
                        </div>
                      </td>
                      <td className="px-6 py-4">
                        <div className="text-slate-400 text-xs font-mono">
                          {vuln.affected_entities?.map((e: any) => e.source_file).filter(Boolean).join(', ') || '—'}
                        </div>
                      </td>
                      <td className="px-6 py-4">
                        <span className={`px-3 py-1 rounded-full text-xs font-semibold ${getStatusColor(vuln.status)}`}>
                          {vuln.status.replace('_', ' ').charAt(0).toUpperCase() + vuln.status.replace('_', ' ').slice(1)}
                        </span>
                      </td>
                      <td className="px-6 py-4">
                        <div className="flex items-center gap-2 text-slate-400 text-sm">
                          <Calendar className="w-4 h-4" />
                          {formatDate(vuln.published_date)}
                        </div>
                      </td>
                      <td className="px-6 py-4">
                        <button
                          onClick={() => {
                            setSelectedVulnerability(vuln)
                            setAssignModalOpen(true)
                          }}
                          className="px-3 py-1 text-xs font-medium rounded bg-primary-600/20 text-primary-400 hover:bg-primary-600/30 transition-colors flex items-center gap-1"
                        >
                          <Plus className="w-3 h-3" />
                          Assign
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Assign Vulnerability Modal */}
      {assignModalOpen && selectedVulnerability && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-slate-800 rounded-lg border border-slate-700 p-6 w-full max-w-md">
            <h3 className="text-lg font-semibold text-white mb-4">
              Assign {selectedVulnerability.cve_id}
            </h3>
            <form
              onSubmit={(e) => {
                e.preventDefault()
                const formData = new FormData(e.currentTarget)
                const parentType = formData.get('parent_type') as 'service' | 'software'
                const parentId = Number(formData.get('parent_id'))
                const notes = formData.get('notes') as string
                if (parentId) {
                  assignMutation.mutate({
                    vulnId: selectedVulnerability.id,
                    parent_type: parentType,
                    parent_id: parentId,
                    notes: notes || undefined,
                  })
                }
              }}
            >
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-slate-300 mb-1">Type</label>
                  <select
                    name="parent_type"
                    value={assignParentType}
                    onChange={(e) => setAssignParentType(e.target.value)}
                    className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-slate-200"
                  >
                    <option value="service">Service</option>
                    <option value="software">Software</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-300 mb-1">
                    {assignParentType === 'service' ? 'Service' : 'Software'}
                  </label>
                  <select
                    name="parent_id"
                    className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-slate-200"
                    required
                  >
                    <option value="">Select...</option>
                    {assignParentType === 'service'
                      ? servicesData?.items?.map((s: any) => (
                          <option key={s.id} value={s.id}>{s.name}</option>
                        ))
                      : softwareData?.items?.map((s: any) => (
                          <option key={s.id} value={s.id}>{s.name}</option>
                        ))}
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-300 mb-1">Notes (optional)</label>
                  <textarea
                    name="notes"
                    rows={3}
                    maxLength={5000}
                    className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-slate-200 resize-none"
                    placeholder="Why is this vulnerability being assigned?"
                  />
                </div>
              </div>
              <div className="flex justify-end gap-3 mt-6">
                <button
                  type="button"
                  onClick={() => {
                    setAssignModalOpen(false)
                    setSelectedVulnerability(null)
                  }}
                  className="px-4 py-2 text-sm font-medium text-slate-400 hover:text-white transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={assignMutation.isPending}
                  className="px-4 py-2 text-sm font-medium rounded-lg bg-primary-600 text-white hover:bg-primary-700 disabled:opacity-50 transition-colors"
                >
                  {assignMutation.isPending ? 'Assigning...' : 'Assign'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
