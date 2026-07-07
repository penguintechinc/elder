import type { RouteObject } from 'react-router-dom'
import type { MenuCategory } from '@penguintechinc/react-libs/components'
import type { FrontendModule } from '../types'

// Access Reviews module - no pages yet in Phase 0
// Future: Add access review pages and navigation when UI is designed

const navigation: MenuCategory[] = []
const routes: RouteObject[] = []

const accessReviewsModule: FrontendModule = {
  id: 'access_reviews',
  name: 'Access Reviews',
  nav: navigation,
  routes,
}

export default accessReviewsModule
