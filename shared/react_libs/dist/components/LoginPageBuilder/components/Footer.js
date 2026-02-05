import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { ELDER_LOGIN_THEME } from '../themes/elderTheme';
/**
 * Footer component for login page.
 *
 * Features:
 * - "A Penguin Technologies Inc. Application" attribution
 * - Link to LICENSE.md on GitHub repository
 * - Auto-generated copyright year
 */
export const Footer = ({ githubRepo, colors }) => {
    const theme = { ...ELDER_LOGIN_THEME, ...colors };
    const currentYear = new Date().getFullYear();
    // Build LICENSE URL from GitHub repo
    const licenseUrl = githubRepo
        ? `https://github.com/${githubRepo}/blob/main/LICENSE.md`
        : null;
    return (_jsxs("footer", { className: `mt-8 text-center ${theme.footerText}`, children: [_jsxs("p", { className: "text-sm", children: ["A", ' ', _jsx("a", { href: "https://www.penguintech.io", className: `${theme.footerLinkText} hover:underline`, target: "_blank", rel: "noopener noreferrer", children: "Penguin Technologies Inc." }), ' ', "Application"] }), _jsxs("p", { className: "text-xs mt-2", children: ["\u00A9 ", currentYear, " Penguin Technologies Inc.", licenseUrl && (_jsxs(_Fragment, { children: [' | ', _jsx("a", { href: licenseUrl, className: `${theme.footerLinkText} hover:underline`, target: "_blank", rel: "noopener noreferrer", children: "License" })] }))] })] }));
};
//# sourceMappingURL=Footer.js.map