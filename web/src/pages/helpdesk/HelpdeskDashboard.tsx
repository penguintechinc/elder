import { useQuery } from '@tanstack/react-query'
import { AlertCircle, Clock, CheckCircle2, TrendingUp } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import Card, { CardContent } from '@/components/Card'

const StatCard = ({ icon: Icon, label, value, color = 'text-amber-400' }: {
  icon: React.ComponentType<{ className?: string }>
  label: string
  value: string | number
  color?: string
}) => (
  <Card className="bg-slate-800 border-slate-700">
    <CardContent className="p-6">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-slate-400 text-sm">{label}</p>
          <p className={`text-3xl font-bold mt-2 ${color}`}>{value}</p>
        </div>
        <Icon className={`w-12 h-12 ${color} opacity-20`} />
      </div>
    </CardContent>
  </Card>
)

export default function HelpdeskDashboard() {
  const { data, isLoading, error } = useQuery({
    queryKey: queryKeys.helpdesk.dashboard(),
    queryFn: () => api.getHelpdeskDashboardStats(),
  })

  if (error) {
    toast.error('Failed to load dashboard stats')
  }

  const stats = data?.data || {
    total_tickets: 0,
    open_tickets: 0,
    in_progress_tickets: 0,
    closed_tickets: 0,
    avg_resolution_hours: 0,
    sla_compliance_percent: 0,
  }

  return (
    <div className="p-8">
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-white">Helpdesk Dashboard</h1>
        <p className="mt-2 text-slate-400">Track tickets, SLAs, and team performance</p>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center h-96">
          <p className="text-slate-400">Loading dashboard...</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          <StatCard
            icon={AlertCircle}
            label="Total Tickets"
            value={stats.total_tickets}
            color="text-blue-400"
          />
          <StatCard
            icon={Clock}
            label="Open Tickets"
            value={stats.open_tickets}
            color="text-amber-400"
          />
          <StatCard
            icon={TrendingUp}
            label="In Progress"
            value={stats.in_progress_tickets}
            color="text-sky-400"
          />
          <StatCard
            icon={CheckCircle2}
            label="Closed Tickets"
            value={stats.closed_tickets}
            color="text-green-400"
          />
          <StatCard
            icon={Clock}
            label="Avg Resolution"
            value={`${stats.avg_resolution_hours}h`}
            color="text-purple-400"
          />
          <StatCard
            icon={CheckCircle2}
            label="SLA Compliance"
            value={`${stats.sla_compliance_percent}%`}
            color="text-green-400"
          />
        </div>
      )}
    </div>
  )
}
