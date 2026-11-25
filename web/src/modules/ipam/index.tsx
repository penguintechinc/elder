import { Navigate } from "react-router-dom";
import type { RouteObject } from "react-router-dom";
import type { MenuCategory } from "@penguintechinc/react-libs/components";
import type { FrontendModule } from "../types";

// IPAM is consolidated as a tab inside Networking (/networking?tab=ipam).
// Keep an empty navigation array so the `ipam` module toggle controls IPAM tab
// visibility without adding a duplicate top-level sidebar item.
const navigation: MenuCategory[] = [];

const routes: RouteObject[] = [
  { path: "ipam", element: <Navigate to="/networking?tab=ipam" replace /> },
];

const ipamModule: FrontendModule = {
  id: "ipam",
  name: "IPAM",
  group: "entities",
  nav: navigation,
  routes,
};

export default ipamModule;
