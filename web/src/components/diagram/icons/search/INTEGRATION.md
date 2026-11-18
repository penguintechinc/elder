# IconSearch Integration Guide

This guide explains how to integrate the IconSearch component into IceCharts.

## Quick Start

### 1. Basic Usage

```typescript
import { IconSearch } from '@/components/diagram/icons/search';
import { iconMap } from '@/components/diagram/icons';
import type { IconDefinition } from '@/components/diagram/icons/types';

// Define your icons
const myIcons: IconDefinition[] = [
  {
    id: 'aws',
    label: 'AWS',
    source: 'aws',
    color: '#FF9900',
    tags: ['cloud', 'provider']
  },
  // ... more icons
];

// Use in your component
function MyComponent() {
  const handleSelect = (icon: IconDefinition) => {
    console.log('Selected:', icon.id);
  };

  return (
    <IconSearch
      onSelect={handleSelect}
      allIcons={myIcons}
      placeholder="Search icons..."
      iconMap={iconMap}
    />
  );
}
```

### 2. Integration with DrawingEditor

To add IconSearch to the DrawingEditor for better icon selection:

```typescript
import { IconSearch } from '@/components/diagram/icons/search';
import { iconMap, iconCategories } from '@/components/diagram/icons';

function DrawingEditor() {
  const [showIconSearch, setShowIconSearch] = useState(false);

  // Flatten all icons from categories
  const allIcons = Object.values(iconCategories)
    .flatMap(category => category.icons);

  const handleIconSelect = (icon: IconDefinition) => {
    // Determine if it's a cloud provider
    const isCloudProvider = icon.source === 'aws' ||
                            icon.source === 'azure' ||
                            icon.source === 'gcp';

    // Add the selected icon to the drawing
    if (isCloudProvider) {
      addIconNode(icon.id, icon.label, icon.color, true);
    } else {
      addIconNode(icon.id, icon.label, icon.color, false);
    }

    setShowIconSearch(false);
  };

  return (
    <div>
      {/* Existing toolbar code */}

      {/* Add a button to open icon search */}
      <button
        onClick={() => setShowIconSearch(!showIconSearch)}
        className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600"
      >
        Search Icons
      </button>

      {/* Icon search modal or inline */}
      {showIconSearch && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="w-96 bg-dark-900 rounded-lg shadow-xl p-4">
            <div className="flex justify-between items-center mb-4">
              <h2 className="text-lg font-semibold text-white">Select Icon</h2>
              <button
                onClick={() => setShowIconSearch(false)}
                className="text-dark-400 hover:text-white"
              >
                ✕
              </button>
            </div>

            <IconSearch
              onSelect={handleIconSelect}
              allIcons={allIcons}
              iconMap={iconMap}
            />
          </div>
        </div>
      )}
    </div>
  );
}
```

## Implementation Steps

### Step 1: Prepare Icon Data

Ensure all icons have proper metadata:

```typescript
const iconDef: IconDefinition = {
  id: 'unique-id',           // Unique identifier
  label: 'Display Name',     // User-friendly label
  source: 'aws',            // Icon source (required for styling)
  color: '#FF9900',         // Default color
  tags: ['tag1', 'tag2'],   // Searchable tags (optional)
};
```

### Step 2: Create Icon Definitions List

You can either:

**Option A: Use existing categories**
```typescript
import { iconCategories } from '@/components/diagram/icons';

const allIcons = Object.values(iconCategories)
  .flatMap(category => category.icons);
```

**Option B: Create custom list**
```typescript
const allIcons: IconDefinition[] = [
  { id: 'aws', label: 'AWS', source: 'aws', color: '#FF9900' },
  { id: 'database', label: 'Database', source: 'internal', color: '#6B7280' },
  // ... more icons
];
```

### Step 3: Import and Use Component

```typescript
import { IconSearch } from '@/components/diagram/icons/search';
import { iconMap } from '@/components/diagram/icons';

<IconSearch
  onSelect={handleSelect}
  allIcons={allIcons}
  iconMap={iconMap}
/>
```

## Advanced Usage

### Custom Styling

Wrap in a styled container:

```typescript
<div className="custom-search-container">
  <IconSearch
    onSelect={handleSelect}
    allIcons={allIcons}
    placeholder="Find an icon..."
    iconMap={iconMap}
  />
</div>
```

### Using the Hook Directly

For more control, use the `useIconSearch` hook:

```typescript
import { useIconSearch } from '@/components/diagram/icons/search';

function CustomSearch() {
  const { results, isLoading, search, clear } = useIconSearch({
    maxResults: 50
  });

  const handleSearch = (e: React.ChangeEvent<HTMLInputElement>) => {
    const query = e.target.value;
    search(query, allIcons);
  };

  return (
    <div>
      <input onChange={handleSearch} />

      {isLoading && <p>Searching...</p>}

      <ul>
        {results.map(icon => (
          <li key={icon.id}>{icon.label}</li>
        ))}
      </ul>
    </div>
  );
}
```

### Filtering by Source

Create a filtered icon list:

```typescript
const awsIcons = allIcons.filter(icon => icon.source === 'aws');
const internalIcons = allIcons.filter(icon => icon.source === 'internal');

// Then use with IconSearch
<IconSearch
  onSelect={handleSelect}
  allIcons={filteredIcons}
  iconMap={iconMap}
/>
```

## Styling Customization

The component uses Tailwind CSS dark mode colors. To customize:

### Color Scheme

```typescript
// Input field colors
bg-dark-800      // Background
border-dark-600  // Border
text-white       // Text
focus:ring-blue-500  // Focus ring

// Dropdown colors
bg-dark-900      // Background
border-dark-700  // Border
bg-dark-700      // Selected item

// Badge colors (source indicators)
bg-orange-500/20  // AWS
bg-blue-500/20    // Azure
```

### Size Customization

Edit the component's JSX to adjust:
- Input height: `py-2` (padding)
- Icon size: `w-6 h-6` (width/height)
- Dropdown max-height: `max-h-96` (max height)
- Badge size: `px-2 py-1 text-xs` (padding/text)

## Testing

See `IconSearch.test.tsx` for unit test examples.

### Running Tests

```bash
npm test -- IconSearch.test

# Run with coverage
npm test -- IconSearch.test --coverage
```

### Manual Testing

Use the example component:

```bash
# Navigate to /icon-search route to test manually
```

## Performance Optimization

For large icon sets (1000+), consider:

1. **Virtual Scrolling**
```typescript
import { FixedSizeList } from 'react-window';

// Wrap results in virtual list
<FixedSizeList
  height={400}
  itemCount={results.length}
  itemSize={48}
  width="100%"
>
  {ResultRow}
</FixedSizeList>
```

2. **Increase Debounce**
```typescript
// In IconSearch component, increase setTimeout delay
setTimeout(() => {
  // search logic
}, 100); // Increase from 50ms
```

3. **Lazy Load Icons**
```typescript
// Load icon components on demand
const IconComponent = lazy(() =>
  import(`@/components/diagram/icons/${icon.source}/${icon.id}`)
);
```

## Troubleshooting

### Issue: No results showing
**Solution**:
- Verify `allIcons` array is populated
- Check icon IDs match `iconMap` keys
- Ensure search query matches labels or tags

### Issue: Icon not rendering
**Solution**:
- Confirm `iconMap` has the icon ID
- Check icon component accepts `className` prop
- Verify icon files exist in source directory

### Issue: Search is slow
**Solution**:
- Reduce number of icons
- Increase debounce delay
- Implement virtual scrolling
- Use search index for 1000+ icons

### Issue: Dropdown overlapping content
**Solution**:
- Increase z-index: `z-50` → `z-[9999]`
- Use a modal wrapper for full-screen search
- Adjust dropdown max-height

## Migration Guide

If migrating from old icon selector:

```typescript
// Old code
const oldIcons = iconCategories.cloud.icons;

// New code with IconSearch
import { IconSearch } from '@/components/diagram/icons/search';

const allIcons = Object.values(iconCategories)
  .flatMap(cat => cat.icons);

// Replace old selector with:
<IconSearch
  onSelect={handleSelect}
  allIcons={allIcons}
  iconMap={iconMap}
/>
```

## File Structure

```
src/client/components/diagram/icons/search/
├── IconSearch.tsx              # Main component
├── useIconSearch.ts            # Hook for search logic
├── searchIndex.ts              # Fast search indexing
├── IconSearch.example.tsx      # Example usage
├── IconSearch.test.tsx         # Unit tests
├── index.ts                    # Public exports
├── README.md                   # Full documentation
└── INTEGRATION.md              # This file
```

## API Reference

See `/src/client/components/diagram/icons/search/README.md` for complete API documentation.

## Support

For issues or questions:
1. Check the README.md for detailed documentation
2. Review IconSearch.example.tsx for usage patterns
3. Check test files for expected behavior
4. File an issue with reproduction steps

## Next Steps

1. Integrate into DrawingEditor sidebar
2. Add keyboard shortcut (e.g., Ctrl+K) to open search
3. Show recently used icons
4. Add favorite/starred icons feature
5. Implement keyboard-only navigation mode
