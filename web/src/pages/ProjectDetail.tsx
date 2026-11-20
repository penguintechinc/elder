import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, Calendar, GanttChart, List, FolderKanban } from 'lucide-react'
import api from '@/lib/api'
import { Issue } from '@/types'
import Button from '@/components/Button'
import Card, { CardHeader, CardContent } from '@/components/Card'

type ViewMode = 'list' | 'calendar' | 'gantt'

interface ProjectData {
  id: number
  name: string
  description?: string
  status: string
  start_date?: string
  end_date?: string
}

export default function ProjectDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [viewMode, setViewMode] = useState<ViewMode>('list')

  const { data: project, isLoading: projectLoading } = useQuery({
    queryKey: ['project', id],
    queryFn: () => api.getProject(parseInt(id!)),
    enabled: !!id,
  })

  const { data: issues, isLoading: issuesLoading } = useQuery({
    queryKey: ['project-issues', id],
    queryFn: () => api.getIssues({ project_id: parseInt(id!) }),
    enabled: !!id,
  })

  if (projectLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  if (!project) {
    return (
      <div className="p-8">
        <Card>
          <CardContent className="text-center py-12">
            <FolderKanban className="w-16 h-16 text-slate-600 mx-auto mb-4" />
            <p className="text-slate-400">Project not found</p>
            <Button className="mt-4" onClick={() => navigate('/projects')}>
              Back to Projects
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'active':
        return 'bg-green-500/20 text-green-400'
      case 'completed':
        return 'bg-blue-500/20 text-blue-400'
      case 'archived':
        return 'bg-slate-500/20 text-slate-400'
      case 'on_hold':
        return 'bg-yellow-500/20 text-yellow-400'
      default:
        return 'bg-slate-500/20 text-slate-400'
    }
  }

  const getPriorityColor = (priority: string) => {
    switch (priority) {
      case 'critical':
        return 'bg-red-500/20 text-red-400'
      case 'high':
        return 'bg-orange-500/20 text-orange-400'
      case 'medium':
        return 'bg-yellow-500/20 text-yellow-400'
      case 'low':
        return 'bg-blue-500/20 text-blue-400'
      default:
        return 'bg-slate-500/20 text-slate-400'
    }
  }

  const issueStatusColor = (status: string) => {
    switch (status) {
      case 'open':
        return 'bg-green-500/20 text-green-400'
      case 'in_progress':
        return 'bg-blue-500/20 text-blue-400'
      case 'closed':
        return 'bg-slate-500/20 text-slate-400'
      case 'resolved':
        return 'bg-purple-500/20 text-purple-400'
      default:
        return 'bg-slate-500/20 text-slate-400'
    }
  }

  return (
    <div className="p-8">
      {/* Header */}
      <div className="mb-8">
        <Button variant="ghost" onClick={() => navigate('/projects')} className="mb-4">
          <ArrowLeft className="w-4 h-4 mr-2" />
          Back to Projects
        </Button>
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold text-white">{project.name}</h1>
            {project.description && (
              <p className="mt-2 text-slate-400">{project.description}</p>
            )}
          </div>
          <div className="flex gap-2 items-center">
            <span className={`text-xs px-3 py-1.5 rounded ${getStatusColor(project.status)}`}>
              {project.status.replace('_', ' ')}
            </span>
          </div>
        </div>

        {/* Project Dates */}
        {(project.start_date || project.end_date) && (
          <div className="mt-4 flex gap-4 text-sm text-slate-400">
            {project.start_date && (
              <div>
                <span className="text-slate-500">Start:</span>{' '}
                {new Date(project.start_date).toLocaleDateString()}
              </div>
            )}
            {project.end_date && (
              <div>
                <span className="text-slate-500">End:</span>{' '}
                {new Date(project.end_date).toLocaleDateString()}
              </div>
            )}
          </div>
        )}
      </div>

      {/* View Mode Tabs */}
      <div className="flex gap-2 mb-6">
        <Button
          variant={viewMode === 'list' ? 'primary' : 'ghost'}
          onClick={() => setViewMode('list')}
        >
          <List className="w-4 h-4 mr-2" />
          List
        </Button>
        <Button
          variant={viewMode === 'calendar' ? 'primary' : 'ghost'}
          onClick={() => setViewMode('calendar')}
        >
          <Calendar className="w-4 h-4 mr-2" />
          Calendar
        </Button>
        <Button
          variant={viewMode === 'gantt' ? 'primary' : 'ghost'}
          onClick={() => setViewMode('gantt')}
        >
          <GanttChart className="w-4 h-4 mr-2" />
          Gantt
        </Button>
      </div>

      {/* Content Area */}
      {issuesLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : (
        <>
          {viewMode === 'list' && (
            <IssuesList issues={issues?.items || []} getPriorityColor={getPriorityColor} issueStatusColor={issueStatusColor} />
          )}
          {viewMode === 'calendar' && (
            <CalendarView issues={issues?.items || []} getPriorityColor={getPriorityColor} />
          )}
          {viewMode === 'gantt' && (
            <GanttView issues={issues?.items || []} project={project} getPriorityColor={getPriorityColor} />
          )}
        </>
      )}
    </div>
  )
}

// Issues List View
function IssuesList({ issues, getPriorityColor, issueStatusColor }: { issues: Issue[]; getPriorityColor: (priority: string) => string; issueStatusColor: (status: string) => string }) {
  if (issues.length === 0) {
    return (
      <Card>
        <CardContent className="text-center py-12">
          <p className="text-slate-400">No issues linked to this project</p>
        </CardContent>
      </Card>
    )
  }

  return (
    <div className="space-y-4">
      {issues.map((issue: Issue) => (
        <Card key={issue.id}>
          <CardContent>
            <div className="flex items-start justify-between">
              <div className="flex-1">
                <h3 className="text-lg font-semibold text-white mb-2">{issue.title}</h3>
                {issue.description && (
                  <p className="text-sm text-slate-400 mb-3 line-clamp-2">{issue.description}</p>
                )}
                <div className="flex gap-2 flex-wrap">
                  <span className={`text-xs px-2 py-1 rounded ${issueStatusColor(issue.status)}`}>
                    {issue.status.replace('_', ' ')}
                  </span>
                  <span className={`text-xs px-2 py-1 rounded ${getPriorityColor(issue.priority)}`}>
                    {issue.priority}
                  </span>
                  {issue.issue_type && (
                    <span className="text-xs px-2 py-1 rounded bg-slate-500/20 text-slate-400">
                      {issue.issue_type}
                    </span>
                  )}
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

// Calendar View
function toDateKey(value: string): string {
  if (/^\d{4}-\d{2}-\d{2}/.test(value)) {
    return value.slice(0, 10)
  }
  return new Date(value).toISOString().split('T')[0]
}

function CalendarView({ issues, getPriorityColor }: { issues: Issue[]; getPriorityColor: (priority: string) => string }) {
  const [currentDate, setCurrentDate] = useState(new Date())

  // Get first day of month and number of days
  const year = currentDate.getFullYear()
  const month = currentDate.getMonth()
  const firstDay = new Date(year, month, 1).getDay()
  const daysInMonth = new Date(year, month + 1, 0).getDate()

  // Create array of day numbers
  const days = Array.from({ length: daysInMonth }, (_, i) => i + 1)
  const blanks = Array.from({ length: firstDay }, (_, i) => i)

  // Group issues across their start_date..end_date span (falling back to due_date / created_at)
  const monthStartKey = `${year}-${String(month + 1).padStart(2, '0')}-01`
  const monthEndKey = `${year}-${String(month + 1).padStart(2, '0')}-${String(daysInMonth).padStart(2, '0')}`
  const issuesByDate: Record<string, Issue[]> = {}
  issues.forEach((issue: Issue) => {
    const rawStart = issue.start_date || issue.end_date || issue.due_date || issue.created_at
    const rawEnd = issue.end_date || issue.due_date || issue.start_date || issue.created_at
    if (!rawStart || !rawEnd) return

    const startKey = toDateKey(rawStart)
    const endKey = toDateKey(rawEnd) < startKey ? startKey : toDateKey(rawEnd)

    if (endKey < monthStartKey || startKey > monthEndKey) return

    const clampedStart = startKey < monthStartKey ? monthStartKey : startKey
    const clampedEnd = endKey > monthEndKey ? monthEndKey : endKey
    const cursor = new Date(`${clampedStart}T00:00:00Z`)
    const last = new Date(`${clampedEnd}T00:00:00Z`)
    while (cursor <= last) {
      const key = cursor.toISOString().split('T')[0]
      if (!issuesByDate[key]) {
        issuesByDate[key] = []
      }
      issuesByDate[key].push(issue)
      cursor.setUTCDate(cursor.getUTCDate() + 1)
    }
  })

  const monthNames = ['January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December']

  const goToPrevMonth = () => {
    setCurrentDate(new Date(year, month - 1, 1))
  }

  const goToNextMonth = () => {
    setCurrentDate(new Date(year, month + 1, 1))
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <h2 className="text-xl font-semibold text-white">
            {monthNames[month]} {year}
          </h2>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={goToPrevMonth}>
              ←
            </Button>
            <Button variant="ghost" onClick={goToNextMonth}>
              →
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-7 gap-2">
          {/* Day headers */}
          {['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map((day) => (
            <div key={day} className="text-center text-sm font-medium text-slate-400 py-2">
              {day}
            </div>
          ))}

          {/* Blank cells for days before month starts */}
          {blanks.map((i) => (
            <div key={`blank-${i}`} className="aspect-square" />
          ))}

          {/* Calendar days */}
          {days.map((day) => {
            const dateStr = `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`
            const dayIssues = issuesByDate[dateStr] || []
            const isToday = new Date().toISOString().split('T')[0] === dateStr

            return (
              <div
                key={day}
                className={`aspect-square border rounded-lg p-2 ${
                  isToday ? 'border-primary-500 bg-primary-500/10' : 'border-slate-700'
                }`}
              >
                <div className="text-sm text-slate-400 mb-1">{day}</div>
                {dayIssues.slice(0, 3).map((issue: Issue) => (
                  <div
                    key={issue.id}
                    className={`text-xs px-1 py-0.5 rounded mb-1 truncate ${getPriorityColor(issue.priority)}`}
                    title={issue.title}
                  >
                    {issue.title}
                  </div>
                ))}
                {dayIssues.length > 3 && (
                  <div className="text-xs text-slate-500">+{dayIssues.length - 3} more</div>
                )}
              </div>
            )
          })}
        </div>
      </CardContent>
    </Card>
  )
}

// Gantt View
function getIssueRange(issue: Issue): { start: Date; end: Date } | null {
  const rawStart = issue.start_date || issue.created_at
  const rawEnd = issue.end_date || issue.due_date || issue.start_date || issue.created_at
  if (!rawStart || !rawEnd) return null
  const start = new Date(rawStart)
  const parsedEnd = new Date(rawEnd)
  const end = parsedEnd < start ? start : parsedEnd
  return { start, end }
}

function GanttView({ issues, project, getPriorityColor }: { issues: Issue[]; project: ProjectData; getPriorityColor: (priority: string) => string }) {
  // Determine timeline bounds
  const issuesWithDates = issues.filter((i: Issue) => getIssueRange(i) !== null)

  if (issuesWithDates.length === 0 && !project.start_date && !project.end_date) {
    return (
      <Card>
        <CardContent className="text-center py-12">
          <p className="text-slate-400">No timeline data available for Gantt chart</p>
          <p className="text-sm text-slate-500 mt-2">Issues need start/end dates to display in Gantt view</p>
        </CardContent>
      </Card>
    )
  }

  // Calculate timeline bounds
  let minDate = project.start_date ? new Date(project.start_date) : new Date()
  let maxDate = project.end_date ? new Date(project.end_date) : new Date()

  issuesWithDates.forEach((issue: Issue) => {
    const range = getIssueRange(issue)!
    if (range.start < minDate) minDate = range.start
    if (range.end > maxDate) maxDate = range.end
  })

  // Add padding
  const timelineStart = new Date(minDate)
  timelineStart.setDate(timelineStart.getDate() - 7)
  const timelineEnd = new Date(maxDate)
  timelineEnd.setDate(timelineEnd.getDate() + 7)

  const totalDays = Math.max(1, Math.ceil((timelineEnd.getTime() - timelineStart.getTime()) / (1000 * 60 * 60 * 24)))

  // Generate month markers
  const months: Date[] = []
  const currentMonth = new Date(timelineStart)
  currentMonth.setDate(1)
  while (currentMonth <= timelineEnd) {
    months.push(new Date(currentMonth))
    currentMonth.setMonth(currentMonth.getMonth() + 1)
  }

  const getBarPosition = (startDate: Date, endDate: Date) => {
    const start = Math.max(0, Math.floor((startDate.getTime() - timelineStart.getTime()) / (1000 * 60 * 60 * 24)))
    const end = Math.min(totalDays, Math.ceil((endDate.getTime() - timelineStart.getTime()) / (1000 * 60 * 60 * 24)))
    const width = Math.max(1, end - start)
    return {
      left: `${(start / totalDays) * 100}%`,
      width: `${(width / totalDays) * 100}%`,
    }
  }

  return (
    <Card>
      <CardHeader>
        <h2 className="text-xl font-semibold text-white">Project Timeline</h2>
      </CardHeader>
      <CardContent>
        {/* Timeline header */}
        <div className="relative border-b border-slate-700 pb-2 mb-4">
          <div className="flex text-xs text-slate-400">
            {months.map((month, idx) => {
              const monthStart = new Date(month)
              const monthEnd = new Date(month)
              monthEnd.setMonth(monthEnd.getMonth() + 1)
              const pos = getBarPosition(monthStart, monthEnd)

              return (
                <div key={idx} className="absolute" style={{ left: pos.left }}>
                  {month.toLocaleDateString('en-US', { month: 'short', year: 'numeric' })}
                </div>
              )
            })}
          </div>
        </div>

        {/* Project bar */}
        {project.start_date && project.end_date && (
          <div className="mb-6">
            <div className="text-sm text-slate-300 mb-2 font-medium">{project.name}</div>
            <div className="relative h-8">
              <div
                className="absolute h-6 bg-primary-500/30 border border-primary-500 rounded"
                style={getBarPosition(new Date(project.start_date), new Date(project.end_date))}
              />
            </div>
          </div>
        )}

        {/* Issue bars */}
        <div className="space-y-3">
          {issuesWithDates.map((issue: Issue) => {
            const range = getIssueRange(issue)!
            const startDate = range.start
            const endDate = range.end

            return (
              <div key={issue.id}>
                <div className="text-sm text-slate-300 mb-1 flex items-center gap-2">
                  <span className="truncate">{issue.title}</span>
                  <span className={`text-xs px-2 py-0.5 rounded ${getPriorityColor(issue.priority)}`}>
                    {issue.priority}
                  </span>
                </div>
                <div className="relative h-6">
                  <div
                    className={`absolute h-4 rounded ${
                      issue.status === 'closed' || issue.status === 'resolved'
                        ? 'bg-slate-600/50'
                        : 'bg-blue-500/50 border border-blue-500'
                    }`}
                    style={getBarPosition(startDate, endDate)}
                    title={`${issue.title} - ${issue.priority}`}
                  />
                </div>
              </div>
            )
          })}
        </div>
      </CardContent>
    </Card>
  )
}
