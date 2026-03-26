import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Building2, Box, GitBranch, Users, AlertCircle } from 'lucide-react'
import api from '@/lib/api'
import Card, { CardHeader, CardContent } from '@/components/Card'

export default function Dashboard() {
  const navigate = useNavigate()
  const { data: orgs } = useQuery({
    queryKey: ['organizations', { page: 1, per_page: 5 }],
    queryFn: () => api.getOrganizations({ page: 1, per_page: 5 }),
  })

  const { data: entities } = useQuery({
    queryKey: ['entities', { page: 1, per_page: 5 }],
    queryFn: () => api.getEntities({ page: 1, per_page: 5 }),
  })

  const { data: dependencies } = useQuery({
    queryKey: ['dependencies', { page: 1, per_page: 5 }],
    queryFn: () => api.getDependencies({ page: 1, per_page: 5 }),
  })

  const { data: identities } = useQuery({
    queryKey: ['identities', { page: 1, per_page: 5 }],
    queryFn: () => api.getIdentities({ page: 1, per_page: 5 }),
  })

  const { data: issues } = useQuery({
    queryKey: ['issues'],
    queryFn: () => api.getIssues(),
  })

  const stats = [
    {
      name: 'Organizations',
      value: orgs?.total || 0,
      icon: Building2,
      color: 'text-blue-500',
      bgColor: 'bg-blue-500/10',
      href: '/organizations',
    },
    {
      name: 'Entities',
      value: entities?.total || 0,
      icon: Box,
      color: 'text-green-500',
      bgColor: 'bg-green-500/10',
      href: '/entities',
    },
    {
      name: 'Dependencies',
      value: dependencies?.total || 0,
      icon: GitBranch,
      color: 'text-purple-500',
      bgColor: 'bg-purple-500/10',
      href: '/dependencies',
    },
    {
      name: 'Identities',
      value: identities?.total || 0,
      icon: Users,
      color: 'text-yellow-500',
      bgColor: 'bg-yellow-500/10',
      href: '/iam',
    },
    {
      name: 'Open Issues',
      value: issues?.items?.filter((i: any) => i.status === 'open').length || 0,
      icon: AlertCircle,
      color: 'text-red-500',
      bgColor: 'bg-red-500/10',
      href: '/issues',
    },
  ]

  return (
    <div className="p-8">
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-white">Dashboard</h1>
        <p className="mt-2 text-slate-400">
          Welcome to Elder - Entity Relationship Tracking System
        </p>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-6 mb-8">
        {stats.map((stat) => {
          const Icon = stat.icon
          return (
            <Card
              key={stat.name}
              className="cursor-pointer hover:ring-1 hover:ring-slate-500 transition-all"
              onClick={() => navigate(stat.href)}
            >
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

      {/* Recent Activity */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Recent Organizations */}
        <Card>
          <CardHeader>
            <h3 className="text-lg font-semibold text-white">Recent Organizations</h3>
          </CardHeader>
          <CardContent>
            {orgs?.items?.length === 0 ? (
              <p className="text-slate-400 text-sm">No organizations yet</p>
            ) : (
              <ul className="space-y-3">
                {orgs?.items?.map((org: any) => (
                  <li
                    key={org.id}
                    className="flex items-center justify-between p-3 rounded-lg bg-slate-800/30 hover:bg-slate-800/50 cursor-pointer transition-colors"
                    onClick={() => navigate(`/organizations/${org.id}`)}
                  >
                    <div>
                      <p className="text-white font-medium">{org.name}</p>
                      {org.description && (
                        <p className="text-sm text-slate-400 truncate">{org.description}</p>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        {/* Recent Entities */}
        <Card>
          <CardHeader>
            <h3 className="text-lg font-semibold text-white">Recent Entities</h3>
          </CardHeader>
          <CardContent>
            {entities?.items?.length === 0 ? (
              <p className="text-slate-400 text-sm">No entities yet</p>
            ) : (
              <ul className="space-y-3">
                {entities?.items?.map((entity: any) => (
                  <li
                    key={entity.id}
                    className="flex items-center justify-between p-3 rounded-lg bg-slate-800/30 hover:bg-slate-800/50 cursor-pointer transition-colors"
                    onClick={() => navigate(`/entities/${entity.id}`)}
                  >
                    <div>
                      <p className="text-white font-medium">{entity.name}</p>
                      <p className="text-sm text-slate-400">
                        {entity.entity_type.replace('_', ' ').toUpperCase()}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
