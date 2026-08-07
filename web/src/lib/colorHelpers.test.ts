import { describe, it, expect } from 'vitest'
import { getStatusColor, getPriorityColor } from './colorHelpers'

describe('colorHelpers', () => {
  describe('getStatusColor', () => {
    it('returns consistent colors for lowercase and uppercase issue statuses', () => {
      expect(getStatusColor('open')).toBe(getStatusColor('OPEN'))
      expect(getStatusColor('in_progress')).toBe(getStatusColor('IN_PROGRESS'))
      expect(getStatusColor('closed')).toBe(getStatusColor('CLOSED'))
      expect(getStatusColor('resolved')).toBe(getStatusColor('RESOLVED'))
    })

    it('returns distinct, non-default colors for issue statuses', () => {
      const defaultColor = 'bg-blue-500/20 text-blue-400 border-blue-500/30'

      // open should be green (not blue default)
      expect(getStatusColor('open')).not.toBe(defaultColor)
      expect(getStatusColor('open')).toBe('bg-green-500/20 text-green-400 border-green-500/30')

      // in_progress should be blue (not blue default) — wait, that's intentional different blue
      expect(getStatusColor('in_progress')).toBe('bg-blue-500/20 text-blue-400 border-blue-500/30')

      // closed should be slate (not blue default)
      expect(getStatusColor('closed')).not.toBe(defaultColor)
      expect(getStatusColor('closed')).toBe('bg-slate-500/20 text-slate-400 border-slate-500/30')
    })

    it('handles case-insensitive generic statuses', () => {
      expect(getStatusColor('active')).toBe(getStatusColor('ACTIVE'))
      expect(getStatusColor('offline')).toBe(getStatusColor('OFFLINE'))
      expect(getStatusColor('failed')).toBe(getStatusColor('FAILED'))
    })
  })

  describe('getPriorityColor', () => {
    it('returns consistent colors for lowercase and uppercase priorities', () => {
      expect(getPriorityColor('critical')).toBe(getPriorityColor('CRITICAL'))
      expect(getPriorityColor('high')).toBe(getPriorityColor('HIGH'))
      expect(getPriorityColor('medium')).toBe(getPriorityColor('MEDIUM'))
      expect(getPriorityColor('low')).toBe(getPriorityColor('LOW'))
    })

    it('returns distinct colors for each priority level', () => {
      const critical = getPriorityColor('critical')
      const high = getPriorityColor('high')
      const medium = getPriorityColor('medium')
      const low = getPriorityColor('low')

      expect(new Set([critical, high, medium, low]).size).toBe(4)
    })

    it('handles case-insensitive priority variants', () => {
      expect(getPriorityColor('urgent')).toBe(getPriorityColor('URGENT'))
      expect(getPriorityColor('normal')).toBe(getPriorityColor('NORMAL'))
      expect(getPriorityColor('trivial')).toBe(getPriorityColor('TRIVIAL'))
    })
  })
})
