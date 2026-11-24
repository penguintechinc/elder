import {
  useLocation,
  Outlet,
  useNavigate,
  Link,
  Navigate,
} from "react-router-dom";
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  LayoutDashboard,
  Search as SearchIcon,
  MapIcon,
  User,
  LogOut,
  Settings,
  FileText,
  Users,
  ChevronDown,
  ChevronRight,
  Shield,
  Plug,
  Lock,
  Zap,
  Cpu,
} from "lucide-react";
import {
  SidebarMenu,
  MenuCategory,
  MenuItem,
} from "@penguintechinc/react-libs/components";
import api from "@/lib/api";

// Module framework
import { navFor } from "@/modules/registry";
import { useModules } from "@/hooks/useModules";

// Core navigation (always available)
const coreNavigation: MenuCategory[] = [
  {
    items: [
      { name: "Dashboard", href: "/", icon: LayoutDashboard },
      { name: "Search", href: "/search", icon: SearchIcon },
      { name: "Map", href: "/map", icon: MapIcon },
    ],
  },
];

// Admin navigation - shown based on user role
const adminNavigation: MenuItem[] = [
  {
    name: "Audit Logs",
    href: "/admin/audit-logs",
    icon: FileText,
    roles: ["admin", "support", "tenant_admin"],
  },
  {
    name: "Settings",
    href: "/admin/settings",
    icon: Settings,
    roles: ["admin"],
  },
  {
    name: "SSO Config",
    href: "/admin/sso",
    icon: Shield,
    roles: ["admin", "tenant_admin"],
  },
  {
    name: "Module Toggles",
    href: "/admin/modules",
    icon: Zap,
    roles: ["admin"],
  },
  {
    name: "Integrations",
    href: "/admin/integrations",
    icon: Plug,
    roles: ["admin"],
  },
  {
    name: "License Policies",
    href: "/admin/license-policies",
    icon: Lock,
    roles: ["admin"],
  },
  { name: "Workers", href: "/admin/workers", icon: Cpu, roles: ["admin"] },
  { name: "Tenants", href: "/admin/tenants", icon: Users, roles: ["admin"] },
];

// Footer items for profile and logout
const footerItems: MenuItem[] = [
  { name: "Profile", href: "/profile", icon: User },
  { name: "Logout", href: "#logout", icon: LogOut },
];

export default function Layout() {
  const location = useLocation();
  const navigate = useNavigate();

  // Fetch user profile for role-based navigation
  const { data: userProfile } = useQuery({
    queryKey: ["portal-profile"],
    queryFn: () => api.getPortalProfile(),
    staleTime: 60000, // Cache for 1 minute
    retry: false,
  });

  // Fetch enabled modules
  const { enabled: enabledModules } = useModules();

  // Determine user roles for admin navigation visibility
  const globalRole = userProfile?.global_role;
  const tenantRole = userProfile?.tenant_role;

  // Filter admin navigation based on user roles
  const visibleAdminNav = adminNavigation.filter((item) => {
    if (globalRole === "admin") return item.roles?.includes("admin");
    if (globalRole === "support") return item.roles?.includes("support");
    if (tenantRole === "admin") return item.roles?.includes("tenant_admin");
    return false;
  });

  // Build navigation: core + module nav + admin
  const allCategories: MenuCategory[] = useMemo(() => {
    const categories = [
      ...coreNavigation,
      ...navFor(enabledModules), // Module-gated nav
      ...(visibleAdminNav.length > 0
        ? [
            {
              header: "Administration",
              collapsible: true,
              items: visibleAdminNav,
            },
          ]
        : []),
    ];
    return categories;
  }, [enabledModules, visibleAdminNav]);

  const handleLogout = () => {
    // Tokens are HttpOnly cookies (gh security audit) -- only the backend
    // can clear them, so this must round-trip through the API before
    // navigating away. Best-effort: api.logout() never throws.
    void api.logout().finally(() => {
      window.location.href = "/login";
    });
  };

  const handleNavigate = (href: string) => {
    if (href === "#logout") {
      handleLogout();
    } else {
      navigate(href);
    }
  };

  const isFullBleedCanvas =
    /^\/(streams|diagrams)\/[^/]+$/.test(location.pathname) &&
    !location.pathname.startsWith("/streams/executions") &&
    !location.pathname.startsWith("/streams/approvals");

  const mfaSetupRequired = Boolean(userProfile?.mfa_setup_required);
  const mfaLockedOut = Boolean(userProfile?.mfa_locked_out);
  const mfaGraceDaysRemaining =
    typeof userProfile?.mfa_grace_days_remaining === "number"
      ? userProfile.mfa_grace_days_remaining
      : 0;
  const mfaGraceDeadlineLabel = userProfile?.mfa_grace_expires_at
    ? new Date(userProfile.mfa_grace_expires_at).toLocaleDateString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
      })
    : null;
  const isOnProfilePage = location.pathname === "/profile";

  return (
    <div className="min-h-screen bg-slate-900">
      {/* Sidebar */}
      <SidebarMenu
        logo={
          <img src="/elder-logo.png" alt="Elder Logo" className="h-12 w-auto" />
        }
        categories={allCategories}
        currentPath={location.pathname}
        onNavigate={handleNavigate}
        footerItems={footerItems}
        userRole={globalRole || tenantRole}
        collapseIcon={ChevronDown}
        expandIcon={ChevronRight}
      />

      {/* Main content */}
      <div className="lg:pl-64 pl-0">
        {mfaSetupRequired &&
          (mfaLockedOut ? (
            <div
              role="alert"
              data-testid="mfa-lockout-banner"
              className="bg-red-500/20 border-b border-red-500/40 px-4 py-3 text-red-100 text-sm flex items-center justify-between gap-4 flex-wrap"
            >
              <div className="flex items-center gap-2">
                <Lock className="w-4 h-4 text-red-300 shrink-0" />
                <span>
                  Your MFA grace period has expired. You must set up Google
                  Authenticator to continue using Elder.
                </span>
              </div>
              {!isOnProfilePage && (
                <Link
                  to="/profile"
                  className="font-semibold text-red-200 hover:text-white underline whitespace-nowrap"
                >
                  Go to Profile
                </Link>
              )}
            </div>
          ) : (
            <div
              role="alert"
              data-testid="mfa-grace-warning-banner"
              className="bg-amber-500/15 border-b border-amber-500/30 px-4 py-2.5 text-amber-100 text-sm flex items-center justify-between gap-4 flex-wrap"
            >
              <div className="flex items-center gap-2">
                <Shield className="w-4 h-4 text-amber-400 shrink-0" />
                <span>
                  Your organization requires Google Authenticator (TOTP) or
                  Passkey MFA. You will be locked out in{" "}
                  <strong>
                    {mfaGraceDaysRemaining}{" "}
                    {mfaGraceDaysRemaining === 1 ? "day" : "days"}
                  </strong>
                  {mfaGraceDeadlineLabel
                    ? ` (by ${mfaGraceDeadlineLabel})`
                    : ""}{" "}
                  unless you enroll MFA.
                </span>
              </div>
              <Link
                to="/profile"
                className="font-semibold text-amber-300 hover:text-amber-100 underline whitespace-nowrap"
              >
                Set up MFA
              </Link>
            </div>
          ))}
        <main
          className={
            isFullBleedCanvas
              ? "h-[100dvh] w-full max-w-none"
              : "min-h-screen max-w-screen-2xl mx-auto"
          }
        >
          {mfaLockedOut && !isOnProfilePage ? (
            <Navigate to="/profile" replace />
          ) : (
            <Outlet />
          )}
        </main>
      </div>
    </div>
  );
}
