import type { EntityTags } from '@/types'

export interface TagChip {
  key: string
  label: string
}

/**
 * Normalizes entity tags into a flat list of chip-ready labels. Tags may
 * arrive as a {key: value} dict (K8s labels / cloud provider tags) or as a
 * legacy list[str] (user-applied classification tags) — callers should
 * never need to branch on which shape they got.
 */
export function normalizeTags(tags: EntityTags | undefined | null): TagChip[] {
  if (!tags) return []
  if (Array.isArray(tags)) {
    return tags
      .filter((value): value is string => typeof value === 'string' && value.length > 0)
      .map((value, index) => ({ key: String(index), label: value }))
  }
  return Object.entries(tags)
    .filter(([, value]) => value !== undefined && value !== null && String(value).length > 0)
    .map(([key, value]) => ({ key, label: `${key}: ${value}` }))
}
