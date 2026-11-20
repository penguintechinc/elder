import { Package, FileCode, AlertTriangle, Shield, Clock, CheckCircle, XCircle } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import Card, { CardHeader, CardContent } from '@/components/Card'
import VillageIdBadge from '@/components/VillageIdBadge'

interface SBOMComponent {
  id: number
  name: string
  version: string
  package_type: string
  license: string
  vulnerability_count: number
  source_service?: string
  source_software?: string
  village_id?: string | null
}

interface SBOMScan {
  id: number
  status: string
  repo_url: string
  components_found: number
  created_at: string
  scan_type: string
  village_id?: string | null
}

interface VulnerabilitySummary {
  critical: number
  high: number
  medium: number
  low: number
}

const SEVERITY_COLORS = {
  critical: 'text-red-500 bg-red-500/10',
  high: 'text-orange-500 bg-orange-500/10',
  medium: 'text-yellow-500 bg-yellow-500/10',
  low: 'text-blue-500 bg-blue-500/10',
}

const STATUS_COLORS: Record<string, string> = {
  completed: 'text-green-500 bg-green-500/10',
  running: 'text-blue-500 bg-blue-500/10',
  failed: 'text-red-500 bg-red-500/10',
  pending: 'text-yellow-500 bg-yellow-500/10',
}

export default function SBOMDashboard() {
  const [searchTerm, setSearchTerm] = useState('')
  const [licenseFilter, setLicenseFilter] = useState('')
  const [packageTypeFilter, setPackageTypeFilter] = useState('')
  const [searchParams] = useSearchParams()
  const highlightId = searchParams.get('highlight')

  // TODO: Wire up real API calls once backend endpoints are implemented
  // For now using mock data to avoid TypeScript errors while allowing page to render
  const componentsData = { items: [] }
  const componentsLoading = false
  const scansData = { items: [] }
  const scansLoading = false
  const dashboardData = {
    total_components: 0,
    total_scans: 0,
    vulnerability_summary: {
      critical: 0,
      high: 0,
      medium: 0,
      low: 0,
    },
    license_violations: 0,
  }

  const components: SBOMComponent[] = componentsData?.items || []
  const scans: SBOMScan[] = scansData?.items || []

  useEffect(() => {
    if (!highlightId) return
    const el =
      document.getElementById(`sbom-scan-${highlightId}`) ||
      document.getElementById(`sbom-component-${highlightId}`) ||
      document.querySelector<HTMLElement>('[data-highlighted="true"]')
    el?.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
  }, [highlightId])
  const vulnSummary: VulnerabilitySummary = dashboardData?.vulnerability_summary || {
    critical: 0,
    high: 0,
    medium: 0,
    low: 0,
  }

  // Filter components
  const filteredComponents = components.filter((comp) => {
    const matchesSearch =
      comp.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      comp.version.toLowerCase().includes(searchTerm.toLowerCase())
    const matchesLicense = !licenseFilter || comp.license === licenseFilter
    const matchesPackageType = !packageTypeFilter || comp.package_type === packageTypeFilter
    return matchesSearch && matchesLicense && matchesPackageType
  })

  // Get unique licenses and package types for filters
  const uniqueLicenses = Array.from(new Set(components.map((c) => c.license).filter(Boolean)))
  const uniquePackageTypes = Array.from(new Set(components.map((c) => c.package_type)))

  // Summary statistics
  const totalComponents = dashboardData?.total_components || components.length
  const totalScans = dashboardData?.total_scans || scans.length
  const licenseViolations = dashboardData?.license_violations || 0

  const stats = [
    {
      name: 'Total Components',
      value: totalComponents,
      icon: Package,
      color: 'text-blue-500',
      bgColor: 'bg-blue-500/10',
    },
    {
      name: 'Total Scans',
      value: totalScans,
      icon: FileCode,
      color: 'text-green-500',
      bgColor: 'bg-green-500/10',
    },
    {
      name: 'Critical/High Vulnerabilities',
      value: vulnSummary.critical + vulnSummary.high,
      icon: AlertTriangle,
      color: 'text-red-500',
      bgColor: 'bg-red-500/10',
    },
    {
      name: 'License Violations',
      value: licenseViolations,
      icon: Shield,
      color: 'text-yellow-500',
      bgColor: 'bg-yellow-500/10',
    },
  ]

  return (
    <div className="p-8">
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-white">SBOM Dashboard</h1>
        <p className="mt-2 text-slate-400">
          Software Bill of Materials - Component and Vulnerability Overview
        </p>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
        {stats.map((stat) => {
          const Icon = stat.icon
          return (
            <Card key={stat.name}>
              <CardContent className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-slate-400">{stat.name}</p>
                  <p className="text-3xl font-bold text-white mt-1">{stat.value}</p>
                </div>
                <div className={`p-3 rounded-lg ${stat.bgColor}`}>
                  <Icon className={`w-6 h-6 ${stat.color}`} />
                </div>
              </CardContent>
            </Card>
          )
        })}
      </div>

      {/* Vulnerability Breakdown */}
      <Card className="mb-8">
        <CardHeader>
          <h3 className="text-lg font-semibold text-white">Vulnerability Breakdown</h3>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-4 gap-4">
            {Object.entries(vulnSummary).map(([severity, count]) => (
              <div key={severity} className="text-center">
                <div
                  className={`px-3 py-2 rounded-lg ${
                    SEVERITY_COLORS[severity as keyof typeof SEVERITY_COLORS]
                  }`}
                >
                  <p className="text-2xl font-bold">{count}</p>
                  <p className="text-sm font-medium capitalize mt-1">{severity}</p>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Recent Scans */}
      <Card className="mb-8">
        <CardHeader>
          <h3 className="text-lg font-semibold text-white">Recent Scans</h3>
        </CardHeader>
        <CardContent>
          {scansLoading ? (
            <div className="flex items-center justify-center py-8">
              <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
            </div>
          ) : scans.length === 0 ? (
            <p className="text-slate-400 text-sm text-center py-8">No scans found</p>
          ) : (
            <div className="space-y-3">
              {scans.slice(0, 5).map((scan) => {
                const isHighlighted = Boolean(
                  highlightId &&
                    (String(scan.id) === highlightId || scan.village_id === highlightId)
                )
                const StatusIcon =
                  scan.status === 'completed'
                    ? CheckCircle
                    : scan.status === 'failed'
                    ? XCircle
                    : Clock
                return (
                  <div
                    key={scan.id}
                    id={`sbom-scan-${scan.id}`}
                    data-highlighted={isHighlighted ? 'true' : undefined}
                    className={`flex items-center justify-between p-4 rounded-lg bg-slate-800/30 hover:bg-slate-800/50 transition-colors ${
                      isHighlighted ? 'ring-2 ring-primary-500 bg-primary-500/10' : ''
                    }`}
                  >
                    <div className="flex items-center gap-4">
                      <StatusIcon className={`w-5 h-5 ${STATUS_COLORS[scan.status]?.split(' ')[0] || 'text-slate-400'}`} />
                      <div>
                        <div className="flex items-center gap-2 flex-wrap">
                          <p className="text-white font-medium truncate max-w-md">{scan.repo_url}</p>
                          {scan.village_id && <VillageIdBadge villageId={scan.village_id} />}
                        </div>
                        <p className="text-sm text-slate-400">
                          {scan.components_found} components found • {scan.scan_type}
                        </p>
                      </div>
                    </div>
                    <div className="flex items-center gap-4">
                      <span
                        className={`px-3 py-1 rounded text-sm ${
                          STATUS_COLORS[scan.status] || 'text-slate-400 bg-slate-700'
                        }`}
                      >
                        {scan.status}
                      </span>
                      <p className="text-sm text-slate-500">
                        {new Date(scan.created_at).toLocaleDateString()}
                      </p>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </CardContent>
      </Card>

      {/* SBOM Components Table */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <h3 className="text-lg font-semibold text-white">SBOM Components</h3>
            <div className="flex gap-3">
              <input
                type="text"
                placeholder="Search components..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="px-3 py-2 bg-slate-800 text-white rounded-lg border border-slate-700 focus:outline-none focus:border-primary-500"
              />
              <select
                value={packageTypeFilter}
                onChange={(e) => setPackageTypeFilter(e.target.value)}
                className="px-3 py-2 bg-slate-800 text-white rounded-lg border border-slate-700 focus:outline-none focus:border-primary-500"
              >
                <option value="">All Package Types</option>
                {uniquePackageTypes.map((type) => (
                  <option key={type} value={type}>
                    {type}
                  </option>
                ))}
              </select>
              <select
                value={licenseFilter}
                onChange={(e) => setLicenseFilter(e.target.value)}
                className="px-3 py-2 bg-slate-800 text-white rounded-lg border border-slate-700 focus:outline-none focus:border-primary-500"
              >
                <option value="">All Licenses</option>
                {uniqueLicenses.map((license) => (
                  <option key={license} value={license}>
                    {license}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </CardHeader>
        <CardContent className="p-0">
          {componentsLoading ? (
            <div className="flex items-center justify-center py-12">
              <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
            </div>
          ) : filteredComponents.length === 0 ? (
            <p className="text-slate-400 text-sm text-center py-12">No components found</p>
          ) : (
            <table className="w-full">
              <thead>
                <tr className="border-b border-slate-700 text-left">
                  <th className="px-4 py-3 text-sm font-medium text-slate-400">Name</th>
                  <th className="px-4 py-3 text-sm font-medium text-slate-400">Version</th>
                  <th className="px-4 py-3 text-sm font-medium text-slate-400">Package Type</th>
                  <th className="px-4 py-3 text-sm font-medium text-slate-400">License</th>
                  <th className="px-4 py-3 text-sm font-medium text-slate-400">Vulnerabilities</th>
                  <th className="px-4 py-3 text-sm font-medium text-slate-400">Source</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700">
                {filteredComponents.map((component) => {
                  const isHighlighted = Boolean(
                    highlightId &&
                      (String(component.id) === highlightId ||
                        component.village_id === highlightId)
                  )
                  return (
                  <tr
                    key={component.id}
                    id={`sbom-component-${component.id}`}
                    data-highlighted={isHighlighted ? 'true' : undefined}
                    className={`hover:bg-slate-700/50 transition-colors ${
                      isHighlighted ? 'bg-primary-500/15 ring-2 ring-inset ring-primary-500' : ''
                    }`}
                  >
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2 flex-wrap">
                        <Package className="w-4 h-4 text-primary-400" />
                        <span className="text-white font-medium">{component.name}</span>
                        {component.village_id && (
                          <VillageIdBadge villageId={component.village_id} />
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-slate-300 font-mono text-sm">
                      {component.version}
                    </td>
                    <td className="px-4 py-3">
                      <span className="px-2 py-1 rounded text-xs bg-slate-700 text-slate-300">
                        {component.package_type}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-slate-300 text-sm">{component.license || '-'}</td>
                    <td className="px-4 py-3">
                      {component.vulnerability_count > 0 ? (
                        <span
                          className={`px-2 py-1 rounded text-xs font-medium ${
                            component.vulnerability_count >= 10
                              ? SEVERITY_COLORS.critical
                              : component.vulnerability_count >= 5
                              ? SEVERITY_COLORS.high
                              : SEVERITY_COLORS.medium
                          }`}
                        >
                          {component.vulnerability_count}
                        </span>
                      ) : (
                        <span className="text-slate-500 text-sm">None</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-slate-400 text-sm">
                      {component.source_service || component.source_software || '-'}
                    </td>
                  </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
