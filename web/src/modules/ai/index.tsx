import { lazy } from "react";
import type { RouteObject } from "react-router-dom";
import type { MenuCategory } from "@penguintechinc/react-libs/components";
import type { FrontendModule } from "../types";
import { Bot } from "lucide-react";

// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const AIResources = lazy(() => import("@/pages/AIResources"));

const navigation: MenuCategory[] = [
  {
    header: "AI Resources",
    collapsible: true,
    items: [{ name: "Artificial Intelligence", href: "/ai", icon: Bot }],
  },
];

const routes: RouteObject[] = [
  { path: "ai", element: <AIResources /> },
  { path: "ai/llms", element: <AIResources defaultType="llm" /> },
  { path: "ai/gateways", element: <AIResources defaultType="ai_gateway" /> },
  { path: "ai/mcp-servers", element: <AIResources defaultType="mcp_server" /> },
  {
    path: "ai/code-mode-servers",
    element: <AIResources defaultType="code_mode_server" />,
  },
  {
    path: "ai/utcp-servers",
    element: <AIResources defaultType="utcp_server" />,
  },
];

const aiModule: FrontendModule = {
  id: "ai",
  name: "AI",
  group: "entities",
  nav: navigation,
  routes,
};

export default aiModule;
