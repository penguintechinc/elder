import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Search, Globe, Server, Code, Shield } from 'lucide-react'
import { Link } from 'react-router-dom'
import api from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import Card, { CardHeader, CardContent } from '@/components/Card'
import Input from '@/components/Input'
import Select from '@/components/Select'

interface Service {
  id: number
  name: string
  paths?: string[]
  language?: string
}

const HTTP_METHODS = ['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS', 'HEAD'] as const
type HttpMethod = typeof HTTP_METHODS[number]

interface Endpoint {
  id: string
  method: HttpMethod
  path: string
  serviceName: string
  serviceId: number
  framework?: string
  authentication?: boolean
  sourceFile?: string
}

export default function ServiceEndpoints() {
  const [search, setSearch] = useState('')
  const [methodFilter, setMethodFilter] = useState<string>('')
  const [serviceFilter, setServiceFilter] = useState<string>('')

  const { data: services, isLoading } = useQuery({
    queryKey: queryKeys.services.list({}),
    queryFn: () => api.getServices({ per_page: 1000 }),
  })

  // Transform services into endpoint rows
  const endpoints = useMemo<Endpoint[]>(() => {
    if (!services?.items) return []

    const result: Endpoint[] = []
    services.items.forEach((service: Service) => {
      if (service.paths && Array.isArray(service.paths)) {
        service.paths.forEach((path: string, index: number) => {
          // Extract method from path if format is "METHOD /path"
          const parts = path.trim().split(/\s+/)
          let method: HttpMethod = 'GET'
          let actualPath = path

          if (parts.length >= 2 && HTTP_METHODS.includes(parts[0].toUpperCase() as HttpMethod)) {
            method = parts[0].toUpperCase() as HttpMethod
            actualPath = parts.slice(1).join(' ')
          }

          result.push({
            id: `${service.id}-${index}`,
            method,
            path: actualPath,
            serviceName: service.name,
            serviceId: service.id,
            framework: service.language,
            authentication: undefined,
            sourceFile: undefined,
          })
        })
      }
    })

    return result
  }, [services])

  // Apply filters
  const filteredEndpoints = useMemo(() => {
    return endpoints.filter(endpoint => {
      if (search && !endpoint.path.toLowerCase().includes(search.toLowerCase()) &&
          !endpoint.serviceName.toLowerCase().includes(search.toLowerCase())) {
        return false
      }
      if (methodFilter && endpoint.method !== methodFilter) {
        return false
      }
      if (serviceFilter && endpoint.serviceId.toString() !== serviceFilter) {
        return false
      }
      return true
    })
  }, [endpoints, search, methodFilter, serviceFilter])

  // Calculate summary stats
  const stats = useMemo(() => {
    const methodCounts: Record<string, number> = {}
    const serviceIds = new Set<number>()

    endpoints.forEach(endpoint => {
      methodCounts[endpoint.method] = (methodCounts[endpoint.method] || 0) + 1
      serviceIds.add(endpoint.serviceId)
    })

    return {
      totalEndpoints: endpoints.length,
      servicesWithEndpoints: serviceIds.size,
      methodCounts,
    }
  }, [endpoints])

  const getMethodColor = (method: HttpMethod) => {
    switch (method) {
      case 'GET':
        return 'bg-blue-500/20 text-blue-400'
      case 'POST':
        return 'bg-green-500/20 text-green-400'
      case 'PUT':
        return 'bg-yellow-500/20 text-yellow-400'
      case 'DELETE':
        return 'bg-red-500/20 text-red-400'
      case 'PATCH':
        return 'bg-purple-500/20 text-purple-400'
      default:
        return 'bg-slate-500/20 text-slate-400'
    }
  }

  return (
    <div className="p-8">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-white">Service Endpoints</h1>
        <p className="mt-2 text-slate-400">
          View and manage discovered API endpoints from services
        </p>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
        <Card>
          <CardContent className="p-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-slate-400">Total Endpoints</p>
                <p className="text-2xl font-bold text-white mt-1">{stats.totalEndpoints}</p>
              </div>
              <Globe className="w-8 h-8 text-primary-400" />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-slate-400">Services with Endpoints</p>
                <p className="text-2xl font-bold text-white mt-1">{stats.servicesWithEndpoints}</p>
              </div>
              <Server className="w-8 h-8 text-primary-400" />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-4">
            <div>
              <p className="text-sm text-slate-400 mb-2">Endpoints by Method</p>
              <div className="flex flex-wrap gap-2">
                {HTTP_METHODS.map(method => (
                  <span key={method} className={`text-xs px-2 py-1 rounded ${getMethodColor(method)}`}>
                    {method}: {stats.methodCounts[method] || 0}
                  </span>
                ))}
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-4 mb-6">
        <div className="flex-1 min-w-[200px] max-w-md">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-slate-400" />
            <Input
              type="text"
              placeholder="Search endpoints..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-10"
            />
          </div>
        </div>
        <Select
          value={methodFilter}
          onChange={(e) => setMethodFilter(e.target.value)}
          className="w-36"
        >
          <option value="">All Methods</option>
          {HTTP_METHODS.map(method => (
            <option key={method} value={method}>{method}</option>
          ))}
        </Select>
        <Select
          value={serviceFilter}
          onChange={(e) => setServiceFilter(e.target.value)}
          className="w-48"
        >
          <option value="">All Services</option>
          {services?.items?.map((service: Service) => (
            <option key={service.id} value={service.id}>
              {service.name}
            </option>
          ))}
        </Select>
      </div>

      {/* Endpoints Table */}
      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : filteredEndpoints.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <Globe className="w-16 h-16 text-slate-600 mx-auto mb-4" />
            <p className="text-slate-400">No endpoints found</p>
            <p className="text-sm text-slate-500 mt-2">
              Add paths to your services to see them here
            </p>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader>
            <h2 className="text-lg font-semibold text-white">
              Endpoints ({filteredEndpoints.length})
            </h2>
          </CardHeader>
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead className="bg-slate-800/50 border-b border-slate-700">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-medium text-slate-400 uppercase tracking-wider">
                      Method
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-slate-400 uppercase tracking-wider">
                      Path
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-slate-400 uppercase tracking-wider">
                      Service
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-slate-400 uppercase tracking-wider">
                      Framework
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-slate-400 uppercase tracking-wider">
                      Authentication
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700">
                  {filteredEndpoints.map(endpoint => (
                    <tr key={endpoint.id} className="hover:bg-slate-800/30 transition-colors">
                      <td className="px-4 py-3 whitespace-nowrap">
                        <span className={`text-xs px-2 py-1 rounded font-medium ${getMethodColor(endpoint.method)}`}>
                          {endpoint.method}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <code className="text-sm text-slate-300 font-mono">
                          {endpoint.path}
                        </code>
                      </td>
                      <td className="px-4 py-3">
                        <Link
                          to="/services"
                          className="text-sm text-primary-400 hover:text-primary-300 flex items-center gap-1"
                        >
                          <Server className="w-4 h-4" />
                          {endpoint.serviceName}
                        </Link>
                      </td>
                      <td className="px-4 py-3">
                        {endpoint.framework ? (
                          <div className="flex items-center gap-1 text-sm text-slate-300">
                            <Code className="w-4 h-4" />
                            {endpoint.framework}
                          </div>
                        ) : (
                          <span className="text-sm text-slate-500">-</span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {endpoint.authentication !== undefined ? (
                          endpoint.authentication ? (
                            <div className="flex items-center gap-1 text-sm text-green-400">
                              <Shield className="w-4 h-4" />
                              Required
                            </div>
                          ) : (
                            <span className="text-sm text-slate-400">None</span>
                          )
                        ) : (
                          <span className="text-sm text-slate-500">-</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
