# IconSearch Component

A powerful, accessible icon search component for IceCharts that enables users to quickly find and select icons from the comprehensive icon library.

## Features

- **Smart Search**: Fuzzy matching, tag-based search, and relevance scoring
- **Keyboard Navigation**: Full support for arrow keys, Enter, and Escape
- **Loading State**: Visual feedback with spinner while searching
- **Responsive Design**: Works seamlessly across different screen sizes
- **Source Badging**: Color-coded badges for different icon sources (AWS, Azure, GCP, IBM, Iconoir, Internal)
- **Maximum Results**: Shows up to 30 results for better performance
- **Auto-scroll**: Selected items automatically scroll into view
- **Click Outside**: Dropdown closes when clicking outside

## Installation

The component is part of the IceCharts diagram icons system. Import it from:

```typescript
import { IconSearch, useIconSearch } from '@/components/diagram/icons/search';
```

## Usage

### Basic Example

```typescript
import { IconSearch } from '@/components/diagram/icons/search';
import { iconMap } from '@/components/diagram/icons';
import type { IconDefinition } from '@/components/diagram/icons/types';

function MyComponent() {
  const allIcons: IconDefinition[] = [
    { id: 'aws', label: 'AWS', source: 'aws', color: '#FF9900' },
    { id: 'database', label: 'Database', source: 'internal', color: '#6B7280' },
    // ... more icons
  ];

  const handleIconSelect = (icon: IconDefinition) => {
    console.log('Selected icon:', icon);
    // Do something with the selected icon
  };

  return (
    <IconSearch
      onSelect={handleIconSelect}
      allIcons={allIcons}
      placeholder="Search icons..."
      iconMap={iconMap}
    />
  );
}
```

### Props

#### `IconSearch` Component

| Prop | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `onSelect` | `(icon: IconDefinition) => void` | Yes | - | Callback function when an icon is selected |
| `allIcons` | `IconDefinition[]` | Yes | - | Array of all available icons to search through |
| `placeholder` | `string` | No | `'Search icons...'` | Placeholder text for the search input |
| `iconMap` | `IconMap` | Yes | - | Map of icon IDs to React components for rendering previews |

### Types

```typescript
interface IconDefinition {
  id: string;
  label: string;
  color: string;
  source: 'internal' | 'aws' | 'azure' | 'gcp' | 'ibm' | 'iconoir';
  tags?: string[];
}

type IconMap = Record<string, React.FC<IconProps>>;

interface IconProps {
  className?: string;
  size?: number | string;
  color?: string;
}
```

## Keyboard Navigation

The component supports full keyboard navigation:

| Key | Action |
|-----|--------|
| `↑` / `↓` | Navigate through results |
| `Enter` | Select highlighted result |
| `Escape` | Close dropdown |
| `Type` | Search for icons |

## Search Behavior

### Matching Algorithm

The search uses a multi-tier relevance system:

1. **Exact matches** (100 points): Exact match of the full label
2. **Prefix matches** (50 points): Label starts with query
3. **Substring matches** (30 points): Query found anywhere in label
4. **Tag matches** (40, 20, or 10 points): Exact, prefix, or substring match in tags
5. **Fuzzy matches** (1 point per character): All query characters appear in order

Results are sorted by relevance score and limited to 30 items.

### Examples

```
Query: "aws"
- AWS (exact: 100)
- AWS Lambda (prefix: 50)

Query: "data"
- Database (substring: 30)
- PostgreSQL (tag: 10)

Query: "db"
- Database (fuzzy: 2)
- Kubernetes (fuzzy: 2)
```

## Source Badges

Icons from different sources are color-coded for easy identification:

| Source | Color | Badge Style |
|--------|-------|------------|
| AWS | Orange | `bg-orange-500/20 text-orange-300` |
| Azure | Blue | `bg-blue-500/20 text-blue-300` |
| GCP | Red | `bg-red-500/20 text-red-300` |
| IBM | Blue | `bg-blue-500/20 text-blue-300` |
| Iconoir | Purple | `bg-purple-500/20 text-purple-300` |
| Internal | Gray | `bg-gray-500/20 text-gray-300` |

## Styling

The component uses Tailwind CSS with dark mode colors:

```typescript
// Input
'bg-dark-800 border-dark-600 text-white rounded-lg'

// Dropdown
'bg-dark-900 border-dark-700 rounded-lg shadow-xl'

// Selected Item
'bg-dark-700'

// Source Badges
'bg-{color}-500/20 text-{color}-300 border-{color}-500/30'
```

### Customization

To customize colors, you can extend the component and override the `getSourceBadgeColor` function:

```typescript
const customBadgeColor = (source: string): string => {
  // Your custom color logic
};
```

## Hook: `useIconSearch`

The underlying `useIconSearch` hook provides the search logic and can be used independently:

```typescript
import { useIconSearch } from '@/components/diagram/icons/search';

function MyComponent() {
  const { results, isLoading, search, clear } = useIconSearch({
    maxResults: 30,
  });

  const handleSearch = (query: string, icons: IconDefinition[]) => {
    search(query, icons);
  };

  // Use results, isLoading, and clear as needed
}
```

### Hook API

```typescript
interface UseIconSearchReturn {
  results: SearchResult[];           // Current search results with scores
  isLoading: boolean;                 // Whether search is in progress
  search: (query: string, allIcons: IconDefinition[]) => void;  // Perform search
  clear: () => void;                  // Clear results
}

interface SearchResult extends IconDefinition {
  score: number;  // Relevance score
}
```

## Performance Considerations

1. **Debouncing**: Search has a 50ms delay to feel natural
2. **Result Limiting**: Maximum 30 results to prevent performance degradation
3. **Memoization**: Uses `useCallback` for stability
4. **Virtual Scrolling**: Consider implementing for 1000+ items
5. **Lazy Loading**: Load icon components only when needed

## Accessibility

- Full keyboard navigation support
- ARIA-friendly dropdown behavior
- Screen reader compatible labels
- Proper focus management
- High contrast colors
- Clear visual feedback for selected items

## Example Implementation

See `IconSearch.example.tsx` for a complete working example with:
- Sample icon definitions
- Icon selection handling
- Details display
- Usage instructions
- Search tips

Run the example:

```typescript
import IconSearchExample from '@/components/diagram/icons/search/IconSearch.example';

// In your route configuration:
{
  path: '/icon-search',
  element: <IconSearchExample />
}
```

## Integration with DrawingEditor

The IconSearch component can be integrated into the DrawingEditor to enhance icon selection:

```typescript
import { IconSearch } from '@/components/diagram/icons/search';

function DrawingEditor() {
  const [showIconSearch, setShowIconSearch] = useState(false);

  const handleIconSelect = (icon: IconDefinition) => {
    // Add icon node to drawing
    addIconNode(icon.id, icon.label, icon.color);
    setShowIconSearch(false);
  };

  return (
    <div>
      {showIconSearch && (
        <IconSearch
          onSelect={handleIconSelect}
          allIcons={allAvailableIcons}
          iconMap={iconMap}
        />
      )}
      {/* Rest of editor */}
    </div>
  );
}
```

## Browser Support

- Chrome/Edge 90+
- Firefox 88+
- Safari 14+
- Mobile browsers (iOS Safari, Chrome Mobile)

## Dependencies

- React 16.8+ (for hooks)
- Tailwind CSS 3.0+
- Icon map from `@/components/diagram/icons`

## Troubleshooting

### No results showing

- Verify `allIcons` array is properly populated
- Check icon IDs match entries in `iconMap`
- Ensure search query matches icon labels or tags

### Icons not rendering

- Confirm `iconMap` includes all icon IDs
- Check icon components accept `className` prop
- Verify icons exist in source directories

### Dropdown not closing

- Check `clickOutside` event listener is working
- Verify no overlapping z-index issues
- Test in browser's element inspector

### Search performance issues

- Reduce number of icons being searched
- Increase `maxResults` threshold cautiously
- Consider debouncing search input further
- Implement virtual scrolling for 1000+ items

## Future Enhancements

- [ ] Virtual scrolling for large datasets
- [ ] Custom search algorithm preferences
- [ ] Keyboard shortcut customization
- [ ] Recently selected icons
- [ ] Favorite/starred icons
- [ ] Category-based filtering
- [ ] Icon preview size customization
- [ ] Hotkey support
- [ ] Analytics/usage tracking
- [ ] Multi-select mode

## License

Part of IceCharts project. See project LICENSE for details.
