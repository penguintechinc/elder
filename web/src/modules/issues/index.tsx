import { lazy } from "react";
import type { RouteObject } from "react-router-dom";
import type {
  MenuCategory,
  MenuItem,
} from "@penguintechinc/react-libs/components";
import type { FrontendModule } from "../types";
import { AlertCircle, Tag, Flag, FolderKanban } from "lucide-react";

// eslint-disable react-refresh/only-export-components -- Intentional: exports both lazy components and module manifest
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const Issues = lazy(() => import("@/pages/Issues"));
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const IssueDetail = lazy(() => import("@/pages/IssueDetail"));
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const Projects = lazy(() => import("@/pages/Projects"));
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const ProjectDetail = lazy(() => import("@/pages/ProjectDetail"));
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const Milestones = lazy(() => import("@/pages/Milestones"));
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const Labels = lazy(() => import("@/pages/Labels"));
// eslint-enable react-refresh/only-export-components

const navigation: MenuCategory[] = [
  {
    header: "Work Management",
    collapsible: true,
    items: [
      { name: "Issues", href: "/issues", icon: AlertCircle },
      { name: "Labels", href: "/labels", icon: Tag },
      { name: "Milestones", href: "/milestones", icon: Flag },
      { name: "Projects", href: "/projects", icon: FolderKanban },
    ],
  },
];

const adminNav: MenuItem[] = [];

const routes: RouteObject[] = [
  { path: "issues", element: <Issues /> },
  { path: "issues/:id", element: <IssueDetail /> },
  { path: "projects", element: <Projects /> },
  { path: "projects/:id", element: <ProjectDetail /> },
  { path: "milestones", element: <Milestones /> },
  { path: "labels", element: <Labels /> },
];

const issuesModule: FrontendModule = {
  id: "issues",
  name: "Work Management",
  group: "issues",
  nav: navigation,
  adminNav,
  routes,
};

export default issuesModule;
