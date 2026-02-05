import React, { ReactNode } from 'react';
export interface MenuItem {
    name: string;
    href: string;
    icon?: React.ComponentType<{
        className?: string;
    }>;
    roles?: string[];
}
export interface MenuCategory {
    header?: string;
    collapsible?: boolean;
    items: MenuItem[];
}
export interface SidebarColorConfig {
    sidebarBackground: string;
    sidebarBorder: string;
    logoSectionBorder: string;
    categoryHeaderText: string;
    menuItemText: string;
    menuItemHover: string;
    menuItemActive: string;
    menuItemActiveText: string;
    collapseIndicator: string;
    footerBorder: string;
    footerButtonText: string;
    footerButtonHover: string;
    scrollbarTrack: string;
    scrollbarThumb: string;
    scrollbarThumbHover: string;
}
export interface SidebarMenuProps {
    logo?: ReactNode;
    categories: MenuCategory[];
    currentPath: string;
    onNavigate?: (href: string) => void;
    footerItems?: MenuItem[];
    userRole?: string;
    width?: string;
    colors?: SidebarColorConfig;
    collapseIcon?: React.ComponentType<{
        className?: string;
    }>;
    expandIcon?: React.ComponentType<{
        className?: string;
    }>;
}
export declare const SidebarMenu: React.FC<SidebarMenuProps>;
//# sourceMappingURL=SidebarMenu.d.ts.map