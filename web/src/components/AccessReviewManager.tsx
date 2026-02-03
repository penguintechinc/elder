import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Clock, Check, X, AlertCircle, Calendar, Users, CheckCircle, XCircle } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import Button from '@/components/Button'
import Card, { CardContent } from '@/components/Card'
import Select from '@/components/Select'

interface AccessReviewManagerProps {
  organizationId?: number
}

interface Review {
  id: number
  group_id: number
  group_name: string
  status: 'scheduled' | 'in_progress' | 'completed' | 'overdue'
  due_date: string
  total_members: number
  members_reviewed: number
  members_kept: number
  members_removed: number
  review_period_start: string
  review_period_end: string
}

interface ReviewItem {
  id: number
  identity_id: number
  username: string
  email: string
  full_name?: string
  joined_at?: string
  expires_at?: string
  decision?: 'keep' | 'remove' | 'extend'
  justification?: string
  reviewed_at?: string
  membership_id: number
}

export default function AccessReviewManager({ organizationId: _organizationId }: AccessReviewManagerProps) {
  const [selectedReview, setSelectedReview] = useState<Review | null>(null)
  const [statusFilter, setStatusFilter] = useState<string>('in_progress')
  const queryClient = useQueryClient()

  // Fetch reviews assigned to current user
  const { data: myReviewsData } = useQuery({
    queryKey: ['access-reviews', 'my-reviews', statusFilter],
    queryFn: () => api.getMyAccessReviews(statusFilter ? { status: statusFilter } : undefined),
  })

  const myReviews = myReviewsData?.reviews || []

  // Fetch review items when review selected
  const { data: reviewItemsData } = useQuery({
    queryKey: ['access-review-items', selectedReview?.id],
    queryFn: () => api.getAccessReviewItems(selectedReview!.id),
    enabled: !!selectedReview,
  })

  const reviewItems = reviewItemsData?.items || []

  // Submit decision mutation
  const submitDecisionMutation = useMutation({
    mutationFn: (data: {
      membership_id: number
      decision: 'keep' | 'remove' | 'extend'
      justification?: string
      new_expiration?: string
    }) => api.submitReviewDecision(selectedReview!.id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['access-review-items'] })
      queryClient.invalidateQueries({ queryKey: ['access-reviews'] })
      toast.success('Decision saved')
    },
    onError: (error: any) => {
      toast.error(error.response?.data?.error || 'Failed to save decision')
    },
  })

  // Complete review mutation
  const completeReviewMutation = useMutation({
    mutationFn: (reviewId: number) => api.completeAccessReview(reviewId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['access-reviews'] })
      setSelectedReview(null)
      toast.success('Review completed and decisions applied')
    },
    onError: (error: any) => {
      toast.error(error.response?.data?.error || 'Failed to complete review')
    },
  })

  const handleDecision = (item: ReviewItem, decision: 'keep' | 'remove' | 'extend') => {
    submitDecisionMutation.mutate({
      membership_id: item.membership_id,
      decision,
      justification: undefined, // Could add justification input
    })
  }

  const handleCompleteReview = () => {
    if (!selectedReview) return

    const unreviewed = reviewItems.filter((item: ReviewItem) => !item.decision).length
    if (unreviewed > 0) {
      toast.error(`Cannot complete: ${unreviewed} members not reviewed`)
      return
    }

    if (confirm(`Complete review and apply decisions?\n${selectedReview.members_removed} members will be removed.`)) {
      completeReviewMutation.mutate(selectedReview.id)
    }
  }

  const getStatusBadge = (status: string) => {
    const colors = {
      scheduled: 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-300',
      in_progress: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-300',
      completed: 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300',
      overdue: 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300',
    }

    return (
      <span className={`px-2 py-1 rounded-full text-xs font-medium ${colors[status as keyof typeof colors]}`}>
        {status.replace('_', ' ').toUpperCase()}
      </span>
    )
  }

  const isOverdue = (dueDate: string) => {
    return new Date(dueDate) < new Date()
  }

  if (selectedReview) {
    // Review detail view
    const progress = selectedReview.total_members > 0
      ? (selectedReview.members_reviewed / selectedReview.total_members) * 100
      : 0

    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <Button
              onClick={() => setSelectedReview(null)}
              className="mb-2 text-sm"
            >
              ← Back to Reviews
            </Button>
            <h2 className="text-2xl font-bold text-gray-900 dark:text-gray-100">
              Review: {selectedReview.group_name}
            </h2>
            <p className="text-sm text-gray-600 dark:text-gray-400">
              Due: {new Date(selectedReview.due_date).toLocaleDateString()}
              {isOverdue(selectedReview.due_date) && (
                <span className="ml-2 text-red-600 font-medium">OVERDUE</span>
              )}
            </p>
          </div>
          <div className="flex items-center gap-4">
            {getStatusBadge(selectedReview.status)}
            {selectedReview.status !== 'completed' && (
              <Button
                onClick={handleCompleteReview}
                disabled={selectedReview.members_reviewed < selectedReview.total_members || completeReviewMutation.isPending}
                className="bg-green-600 hover:bg-green-700"
              >
                {completeReviewMutation.isPending ? 'Completing...' : 'Complete Review'}
              </Button>
            )}
          </div>
        </div>

        {/* Progress Bar */}
        <Card>
          <CardContent>
            <div className="space-y-2">
              <div className="flex justify-between text-sm text-gray-600 dark:text-gray-400">
                <span>Progress: {selectedReview.members_reviewed} / {selectedReview.total_members} reviewed</span>
                <span>{Math.round(progress)}%</span>
              </div>
              <div className="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-2">
                <div
                  className="bg-blue-600 h-2 rounded-full transition-all"
                  style={{ width: `${progress}%` }}
                />
              </div>
              <div className="flex gap-6 text-sm pt-2">
                <span className="text-green-600">
                  <CheckCircle className="inline w-4 h-4 mr-1" />
                  {selectedReview.members_kept} kept
                </span>
                <span className="text-red-600">
                  <XCircle className="inline w-4 h-4 mr-1" />
                  {selectedReview.members_removed} removed
                </span>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Member Review List */}
        <div className="space-y-2">
          {reviewItems.map((item: ReviewItem) => (
            <Card key={item.id}>
              <CardContent>
                <div className="flex items-center justify-between">
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-gray-900 dark:text-gray-100">
                        {item.full_name || item.username}
                      </span>
                      {item.decision && (
                        <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                          item.decision === 'keep' ? 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300' :
                          item.decision === 'remove' ? 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300' :
                          'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-300'
                        }`}>
                          {item.decision.toUpperCase()}
                        </span>
                      )}
                    </div>
                    <p className="text-sm text-gray-600 dark:text-gray-400">{item.email}</p>
                    <p className="text-xs text-gray-500 dark:text-gray-500">
                      Joined: {item.joined_at ? new Date(item.joined_at).toLocaleDateString() : 'Unknown'}
                      {item.expires_at && ` • Expires: ${new Date(item.expires_at).toLocaleDateString()}`}
                    </p>
                  </div>
                  {selectedReview.status !== 'completed' && (
                    <div className="flex gap-2">
                      <Button
                        onClick={() => handleDecision(item, 'keep')}
                        disabled={submitDecisionMutation.isPending}
                        className={`${item.decision === 'keep' ? 'bg-green-600' : 'bg-gray-200 text-gray-700'}`}
                      >
                        <Check className="w-4 h-4" />
                        Keep
                      </Button>
                      <Button
                        onClick={() => handleDecision(item, 'remove')}
                        disabled={submitDecisionMutation.isPending}
                        className={`${item.decision === 'remove' ? 'bg-red-600' : 'bg-gray-200 text-gray-700'}`}
                      >
                        <X className="w-4 h-4" />
                        Remove
                      </Button>
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      </div>
    )
  }

  // Review list view
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold text-gray-900 dark:text-gray-100">
          My Access Reviews
        </h2>
        <Select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="w-48"
        >
          <option value="">All Statuses</option>
          <option value="in_progress">In Progress</option>
          <option value="scheduled">Scheduled</option>
          <option value="overdue">Overdue</option>
          <option value="completed">Completed</option>
        </Select>
      </div>

      {myReviews.length === 0 ? (
        <Card>
          <CardContent>
            <div className="text-center py-8 text-gray-500 dark:text-gray-400">
              <Clock className="w-12 h-12 mx-auto mb-2 opacity-50" />
              <p>No reviews assigned</p>
            </div>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4">
          {myReviews.map((review: Review) => (
            <Card
              key={review.id}
              className="cursor-pointer hover:shadow-lg transition-shadow"
              onClick={() => setSelectedReview(review)}
            >
              <CardContent>
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-2">
                      <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
                        {review.group_name}
                      </h3>
                      {getStatusBadge(review.status)}
                      {isOverdue(review.due_date) && review.status !== 'completed' && (
                        <AlertCircle className="w-5 h-5 text-red-600" />
                      )}
                    </div>
                    <div className="flex gap-4 text-sm text-gray-600 dark:text-gray-400">
                      <span>
                        <Calendar className="inline w-4 h-4 mr-1" />
                        Due: {new Date(review.due_date).toLocaleDateString()}
                      </span>
                      <span>
                        <Users className="inline w-4 h-4 mr-1" />
                        {review.total_members} members
                      </span>
                    </div>
                    <div className="mt-2">
                      <div className="flex justify-between text-xs text-gray-500 mb-1">
                        <span>{review.members_reviewed} / {review.total_members} reviewed</span>
                        <span>{review.total_members > 0 ? Math.round((review.members_reviewed / review.total_members) * 100) : 0}%</span>
                      </div>
                      <div className="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-1.5">
                        <div
                          className="bg-blue-600 h-1.5 rounded-full"
                          style={{ width: `${review.total_members > 0 ? (review.members_reviewed / review.total_members) * 100 : 0}%` }}
                        />
                      </div>
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
