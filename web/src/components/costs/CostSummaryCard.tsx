import { useQuery } from '@tanstack/react-query'
import api from '@/lib/api'

interface CostData {
  cost_to_date: number
  cost_ytd: number
  cost_mtd: number
  estimated_monthly_cost: number | null
  currency: string
  cost_provider: string
  last_synced_at: string | null
  resource_created_at: string | null
  created_by_identity_id: number | null
  recommendations: Array<{
    type: string
    title: string
    description: string
    estimated_savings: number
  }> | null
}

interface CostSummaryCardProps {
  resourceType: string
  resourceId: number
}

function formatCurrency(amount: number, currency: string = 'USD'): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    minimumFractionDigits: 2,
  }).format(amount)
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return 'N/A'
  return new Date(dateStr).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

export default function CostSummaryCard({ resourceType, resourceId }: CostSummaryCardProps) {
  const { data, isLoading } = useQuery({
    queryKey: ['costs', resourceType, resourceId],
    queryFn: () => api.getResourceCosts(resourceType, resourceId),
    enabled: !!resourceId,
  })

  const costs: CostData | null = data?.data ?? null

  if (isLoading) {
    return (
      <div className="border border-slate-700 rounded-lg p-4 mt-4">
        <h3 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Cost Summary</h3>
        <div className="text-slate-500 text-sm">Loading cost data...</div>
      </div>
    )
  }

  if (!costs) {
    return (
      <div className="border border-slate-700 rounded-lg p-4 mt-4">
        <h3 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Cost Summary</h3>
        <div className="text-slate-500 text-sm">No cost data available</div>
      </div>
    )
  }

  return (
    <div className="border border-slate-700 rounded-lg p-4 mt-4">
      <h3 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Cost Summary</h3>

      <div className="grid grid-cols-2 gap-3 text-sm">
        <div>
          <span className="text-slate-400">Cost to Date</span>
          <div className="text-white font-medium">{formatCurrency(costs.cost_to_date, costs.currency)}</div>
        </div>
        <div>
          <span className="text-slate-400">Year to Date</span>
          <div className="text-white font-medium">{formatCurrency(costs.cost_ytd, costs.currency)}</div>
        </div>
        <div>
          <span className="text-slate-400">Month to Date</span>
          <div className="text-white font-medium">{formatCurrency(costs.cost_mtd, costs.currency)}</div>
        </div>
        {costs.estimated_monthly_cost !== null && (
          <div>
            <span className="text-slate-400">Est. Monthly</span>
            <div className="text-amber-400 font-medium">{formatCurrency(costs.estimated_monthly_cost, costs.currency)}</div>
          </div>
        )}
        <div>
          <span className="text-slate-400">Provider</span>
          <div className="text-white">{costs.cost_provider || 'N/A'}</div>
        </div>
        <div>
          <span className="text-slate-400">Last Synced</span>
          <div className="text-white">{formatDate(costs.last_synced_at)}</div>
        </div>
        {costs.resource_created_at && (
          <div>
            <span className="text-slate-400">Resource Created</span>
            <div className="text-white">{formatDate(costs.resource_created_at)}</div>
          </div>
        )}
      </div>

      {costs.recommendations && costs.recommendations.length > 0 && (
        <div className="mt-4 border-t border-slate-700 pt-3">
          <h4 className="text-sm font-semibold text-slate-400 mb-2">Recommendations</h4>
          <div className="space-y-2">
            {costs.recommendations.map((rec, idx) => (
              <div key={idx} className="bg-slate-800 rounded p-2">
                <div className="flex items-center gap-2">
                  <span className="text-xs px-2 py-0.5 rounded bg-amber-400/20 text-amber-400">{rec.type}</span>
                  <span className="text-sm text-white">{rec.title}</span>
                </div>
                <p className="text-xs text-slate-400 mt-1">{rec.description}</p>
                {rec.estimated_savings > 0 && (
                  <p className="text-xs text-green-400 mt-1">
                    Est. savings: {formatCurrency(rec.estimated_savings, costs.currency)}/mo
                  </p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
