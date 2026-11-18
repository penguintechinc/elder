# IconSearch - Quick Reference

## Import

```typescript
import { IconSearch, useIconSearch } from '@/components/diagram/icons/search';
import { iconMap, iconCategories } from '@/components/diagram/icons';
import type { IconDefinition } from '@/components/diagram/icons/types';
```

## Basic Usage (3 lines)

```typescript
const allIcons = Object.values(iconCategories).flatMap(c => c.icons);
<IconSearch onSelect={console.log} allIcons={allIcons} iconMap={iconMap} />
```

## Component Props

| Prop | Type | Required | Example |
|------|------|----------|---------|
| `onSelect` | `(icon: IconDefinition) => void` | ✓ | `(icon) => addNode(icon)` |
| `allIcons` | `IconDefinition[]` | ✓ | `iconCategories.cloud.icons` |
| `iconMap` | `IconMap` | ✓ | `{ aws: AwsIcon, ... }` |
| `placeholder` | `string` | | `"Find an icon..."` |

## Icon Definition

```typescript
interface IconDefinition {
  id: string;              // 'aws'
  label: string;           // 'AWS'
  color: string;           // '#FF9900'
  source: IconSource;      // 'aws' | 'azure' | 'gcp' | 'ibm' | 'iconoir' | 'internal'
  tags?: string[];         // ['cloud', 'provider']
}
```

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `↑` / `↓` | Navigate results |
| `Enter` | Select result |
| `Escape` | Close dropdown |
| Type | Search |

## Complete Example

```typescript
import React, { useState } from 'react';
import { IconSearch } from '@/components/diagram/icons/search';
import { iconMap, iconCategories } from '@/components/diagram/icons';
import type { IconDefinition } from '@/components/diagram/icons/types';

export default function MyComponent() {
  const [selected, setSelected] = useState<IconDefinition | null>(null);

  const allIcons = Object.values(iconCategories)
    .flatMap(category => category.icons);

  return (
    <div className="p-4">
      <IconSearch
        onSelect={setSelected}
        allIcons={allIcons}
        placeholder="Search icons..."
        iconMap={iconMap}
      />

      {selected && (
        <div className="mt-4 p-4 bg-dark-900 rounded">
          <h3>{selected.label}</h3>
          <p>ID: {selected.id}</p>
          <p>Source: {selected.source}</p>
        </div>
      )}
    </div>
  );
}
```

## Using the Hook Directly

```typescript
import { useIconSearch } from '@/components/diagram/icons/search';

function CustomSearch() {
  const { results, isLoading, search, clear } = useIconSearch({
    maxResults: 50
  });

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    search(e.target.value, allIcons);
  };

  return (
    <>
      <input onChange={handleChange} placeholder="Search..." />
      {isLoading && <p>Searching...</p>}
      <ul>
        {results.map(icon => (
          <li key={icon.id}>{icon.label}</li>
        ))}
      </ul>
    </>
  );
}
```

## In DrawingEditor

```typescript
// Add to DrawingEditor.tsx
const [showIconSearch, setShowIconSearch] = useState(false);
const allIcons = Object.values(iconCategories).flatMap(c => c.icons);

const handleIconSelect = (icon: IconDefinition) => {
  addIconNode(icon.id, icon.label, icon.color);
  setShowIconSearch(false);
};

// In JSX:
<button onClick={() => setShowIconSearch(!showIconSearch)}>
  Search Icons
</button>

{showIconSearch && (
  <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
    <div className="w-96 bg-dark-900 rounded-lg shadow-xl p-4">
      <IconSearch
        onSelect={handleIconSelect}
        allIcons={allIcons}
        iconMap={iconMap}
      />
    </div>
  </div>
)}
```

## Source Badge Colors

```typescript
// Colors by source
{
  'aws': 'orange',      // bg-orange-500/20
  'azure': 'blue',      // bg-blue-500/20
  'gcp': 'red',         // bg-red-500/20
  'ibm': 'blue',        // bg-blue-500/20
  'iconoir': 'purple',  // bg-purple-500/20
  'internal': 'gray'    // bg-gray-500/20
}
```

## Search Examples

```
Query: "aws"          → Finds: AWS, AWS Lambda
Query: "database"     → Finds: Database, PostgreSQL, Redis
Query: "db"          → Finds: Database (fuzzy)
Query: "cloud"       → Finds: AWS, Azure, GCP (tags)
Query: "security"    → Finds: Lock, Key, Certificate (tags)
Query: "ser"         → Finds: Server, User, PostgreSQL (fuzzy)
```

## Styling Classes

```typescript
// Input
'bg-dark-800 border-dark-600 text-white rounded-lg'

// Dropdown
'bg-dark-900 border-dark-700 rounded-lg shadow-xl'

// Selected item
'bg-dark-700'

// Spinner (loading)
'animate-spin text-blue-500'

// No results
'text-dark-400'
```

## TypeScript Types

```typescript
// Import types
import type {
  IconDefinition,
  IconSource,
  IconComponent,
  IconMap,
  IconProps,
  SearchResult,
  SearchOptions
} from '@/components/diagram/icons/types';

// Use in function
function handleIcon(icon: IconDefinition) {
  const { id, label, source, color, tags } = icon;
}
```

## Common Patterns

### Filter by Source
```typescript
const awsIcons = allIcons.filter(i => i.source === 'aws');
<IconSearch onSelect={handleSelect} allIcons={awsIcons} iconMap={iconMap} />
```

### Filter by Tags
```typescript
const cloudIcons = allIcons.filter(i =>
  i.tags?.includes('cloud')
);
<IconSearch onSelect={handleSelect} allIcons={cloudIcons} iconMap={iconMap} />
```

### With Loading State
```typescript
const [selectedIcon, setSelectedIcon] = useState<IconDefinition | null>(null);
const [isAdding, setIsAdding] = useState(false);

const handleSelect = async (icon: IconDefinition) => {
  setIsAdding(true);
  try {
    await addNodeToDrawing(icon);
    setSelectedIcon(icon);
  } finally {
    setIsAdding(false);
  }
};
```

### With Validation
```typescript
const handleSelect = (icon: IconDefinition) => {
  if (!iconMap[icon.id]) {
    console.error(`Icon ${icon.id} not found in iconMap`);
    return;
  }
  addIconNode(icon);
};
```

## Tailwind Config (Dark Mode)

The component uses these Tailwind colors:
- `dark-800`, `dark-900`: Background
- `dark-600`, `dark-700`: Borders
- `dark-400`, `dark-500`: Text
- `blue-500`: Focus ring

Already configured in `tailwind.config.ts`:
```typescript
extend: {
  colors: {
    'ice-blue': { ... },
    'ice-gold': { ... },
  }
}
```

## Performance Tips

1. **Memoize props** if passing new arrays
```typescript
const allIcons = useMemo(
  () => Object.values(iconCategories).flatMap(c => c.icons),
  []
);
```

2. **Lazy load large icon sets**
```typescript
const [icons, setIcons] = useState<IconDefinition[]>([]);
useEffect(() => {
  loadIconsAsync().then(setIcons);
}, []);
```

3. **Use searchIndex for 1000+ icons**
```typescript
import { buildSearchIndex, searchIcons } from '@/components/diagram/icons/search';
const index = useMemo(() => buildSearchIndex(allIcons), [allIcons]);
```

## Debugging

```typescript
// Log search results
const handleSelect = (icon: IconDefinition) => {
  console.log('Selected:', icon);
  console.log('Has component:', !!iconMap[icon.id]);
};

// Check available icons
console.log('Total icons:', allIcons.length);
console.log('By source:', {
  aws: allIcons.filter(i => i.source === 'aws').length,
  azure: allIcons.filter(i => i.source === 'azure').length,
  // ...
});
```

## Testing

```typescript
// Unit test example
import { renderHook, act } from '@testing-library/react';
import { useIconSearch } from '@/components/diagram/icons/search';

test('should search for icons', async () => {
  const { result } = renderHook(() => useIconSearch());

  act(() => {
    result.current.search('aws', mockIcons);
  });

  await new Promise(r => setTimeout(r, 100));
  expect(result.current.results).toHaveLength(1);
});
```

## Links & Resources

- **Full Docs**: README.md
- **Integration Guide**: INTEGRATION.md
- **Complete Summary**: SUMMARY.md
- **Example Component**: IconSearch.example.tsx
- **Test File**: IconSearch.test.tsx
- **Type Definitions**: ../types.ts
- **Icon Map**: ../icons.tsx

## Common Issues & Solutions

| Issue | Solution |
|-------|----------|
| No results | Check `allIcons` is populated |
| Icons not showing | Verify `iconMap` has icon ID |
| Dropdown overlapping | Increase z-index to `z-[9999]` |
| Slow search | Reduce icons or increase debounce |
| Styling off | Check Tailwind dark mode enabled |

## Version Info

- **Created**: December 10, 2025
- **Framework**: React 18+
- **TypeScript**: 4.0+
- **Tailwind**: 3.0+
- **Status**: Production Ready

---

For more information, see the full documentation files in this directory.
