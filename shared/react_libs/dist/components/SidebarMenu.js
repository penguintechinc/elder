import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState } from 'react';
// Default Elder-inspired color scheme (slate dark with blue accent)
const DEFAULT_COLORS = {
    sidebarBackground: 'bg-slate-800',
    sidebarBorder: 'border-slate-700',
    logoSectionBorder: 'border-slate-700',
    categoryHeaderText: 'text-slate-400',
    menuItemText: 'text-slate-300',
    menuItemHover: 'hover:bg-slate-700 hover:text-white',
    menuItemActive: 'bg-primary-600',
    menuItemActiveText: 'text-white',
    collapseIndicator: 'text-slate-400',
    footerBorder: 'border-slate-700',
    footerButtonText: 'text-slate-300',
    footerButtonHover: 'hover:bg-slate-700 hover:text-white',
    scrollbarTrack: 'bg-slate-800',
    scrollbarThumb: 'bg-slate-600',
    scrollbarThumbHover: 'hover:bg-slate-500',
};
// Default collapse/expand icons (simple chevron)
const DefaultChevronDown = ({ className }) => (_jsx("svg", { className: className, fill: "none", viewBox: "0 0 24 24", stroke: "currentColor", children: _jsx("path", { strokeLinecap: "round", strokeLinejoin: "round", strokeWidth: 2, d: "M19 9l-7 7-7-7" }) }));
const DefaultChevronRight = ({ className }) => (_jsx("svg", { className: className, fill: "none", viewBox: "0 0 24 24", stroke: "currentColor", children: _jsx("path", { strokeLinecap: "round", strokeLinejoin: "round", strokeWidth: 2, d: "M9 5l7 7-7 7" }) }));
export const SidebarMenu = ({ logo, categories, currentPath, onNavigate, footerItems = [], userRole, width = 'w-64', colors, collapseIcon: CollapseIcon = DefaultChevronDown, expandIcon: ExpandIcon = DefaultChevronRight, }) => {
    const [collapsedCategories, setCollapsedCategories] = useState({});
    const theme = colors || DEFAULT_COLORS;
    const toggleCategory = (header) => {
        setCollapsedCategories((prev) => ({
            ...prev,
            [header]: !prev[header],
        }));
    };
    const isActive = (itemHref) => {
        return currentPath === itemHref || (itemHref !== '/' && currentPath.startsWith(itemHref));
    };
    const handleItemClick = (href) => {
        if (onNavigate) {
            onNavigate(href);
        }
    };
    const hasPermission = (item) => {
        if (!item.roles || item.roles.length === 0)
            return true;
        if (!userRole)
            return false;
        return item.roles.includes(userRole);
    };
    return (_jsxs("div", { className: `fixed inset-y-0 left-0 ${width} ${theme.sidebarBackground} border-r ${theme.sidebarBorder} flex flex-col`, children: [logo && (_jsx("div", { className: `flex items-center justify-center h-16 px-6 border-b ${theme.logoSectionBorder}`, children: logo })), _jsxs("nav", { className: "flex-1 px-4 py-6 overflow-y-auto", children: [_jsx("style", { children: `
            nav::-webkit-scrollbar {
              width: 10px;
            }
            nav::-webkit-scrollbar-track {
              background: transparent;
            }
            nav::-webkit-scrollbar-thumb {
              background: ${theme.scrollbarThumb.replace('bg-', '#')};
              border-radius: 5px;
            }
            nav::-webkit-scrollbar-thumb:hover {
              background: ${theme.scrollbarThumbHover.replace('hover:bg-', '#')};
            }
          ` }), _jsx("div", { className: "space-y-6", children: categories.map((category, categoryIndex) => {
                            const isCollapsed = category.header ? collapsedCategories[category.header] : false;
                            const visibleItems = category.items.filter((item) => hasPermission(item));
                            if (visibleItems.length === 0)
                                return null;
                            return (_jsxs("div", { children: [category.header && (_jsxs("button", { onClick: () => category.collapsible && toggleCategory(category.header), className: `flex items-center justify-between w-full px-4 py-2 text-xs font-semibold uppercase tracking-wider ${theme.categoryHeaderText} ${category.collapsible ? 'cursor-pointer hover:text-slate-300' : ''}`, children: [_jsx("span", { children: category.header }), category.collapsible && (_jsx("span", { className: theme.collapseIndicator, children: isCollapsed ? _jsx(ExpandIcon, { className: "w-3 h-3" }) : _jsx(CollapseIcon, { className: "w-3 h-3" }) }))] })), !isCollapsed && (_jsx("div", { className: "space-y-1 mt-2", children: visibleItems.map((item) => {
                                            const Icon = item.icon;
                                            const active = isActive(item.href);
                                            return (_jsxs("button", { onClick: () => handleItemClick(item.href), className: `flex items-center w-full px-4 py-3 text-sm font-medium rounded-lg transition-colors ${active
                                                    ? `${theme.menuItemActive} ${theme.menuItemActiveText}`
                                                    : `${theme.menuItemText} ${theme.menuItemHover}`}`, children: [Icon && _jsx(Icon, { className: "w-5 h-5 mr-3 flex-shrink-0" }), _jsx("span", { className: "truncate", children: item.name })] }, item.name));
                                        }) }))] }, category.header || `category-${categoryIndex}`));
                        }) })] }), footerItems.length > 0 && (_jsx("div", { className: `p-4 border-t ${theme.footerBorder} space-y-1`, children: footerItems.filter(hasPermission).map((item) => {
                    const Icon = item.icon;
                    const active = isActive(item.href);
                    return (_jsxs("button", { onClick: () => handleItemClick(item.href), className: `flex items-center w-full px-4 py-3 text-sm font-medium rounded-lg transition-colors ${active
                            ? `${theme.menuItemActive} ${theme.menuItemActiveText}`
                            : `${theme.footerButtonText} ${theme.footerButtonHover}`}`, children: [Icon && _jsx(Icon, { className: "w-5 h-5 mr-3 flex-shrink-0" }), _jsx("span", { className: "truncate", children: item.name })] }, item.name));
                }) }))] }));
};
//# sourceMappingURL=SidebarMenu.js.map