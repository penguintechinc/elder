import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useState, useCallback } from 'react';
import { ELDER_LOGIN_THEME } from '../themes/elderTheme';
/**
 * Logger utility for cookie consent
 */
const log = {
    debug: (message, data) => {
        if (process.env.NODE_ENV === 'development') {
            console.debug(`[LoginPage:GDPR] ${message}`, data ?? '');
        }
    },
};
/**
 * Cookie preferences modal for granular consent control
 */
const PreferencesModal = ({ isOpen, onClose, onSave, theme, privacyPolicyUrl, cookiePolicyUrl }) => {
    const [functional, setFunctional] = useState(false);
    const [analytics, setAnalytics] = useState(false);
    const [marketing, setMarketing] = useState(false);
    const handleSave = useCallback(() => {
        log.debug('Saving cookie preferences', { functional, analytics, marketing });
        onSave({ functional, analytics, marketing });
    }, [functional, analytics, marketing, onSave]);
    if (!isOpen)
        return null;
    return (_jsx("div", { className: "fixed inset-0 z-[60] overflow-y-auto", "aria-labelledby": "cookie-preferences-title", role: "dialog", "aria-modal": "true", children: _jsxs("div", { className: "flex min-h-screen items-center justify-center p-4", children: [_jsx("div", { className: "fixed inset-0 bg-black/60 transition-opacity", "aria-hidden": "true", onClick: onClose }), _jsxs("div", { className: `relative w-full max-w-lg transform rounded-xl ${theme.cardBackground} border ${theme.cardBorder} p-6 shadow-2xl`, children: [_jsx("h2", { id: "cookie-preferences-title", className: `text-lg font-semibold ${theme.titleText} mb-4`, children: "Cookie Preferences" }), _jsxs("div", { className: "space-y-4 mb-6", children: [_jsx("div", { className: `p-4 rounded-lg border ${theme.cardBorder}`, children: _jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("div", { children: [_jsx("h3", { className: `font-medium ${theme.labelText}`, children: "Essential Cookies" }), _jsx("p", { className: `text-sm ${theme.subtitleText}`, children: "Required for the website to function. Cannot be disabled." })] }), _jsx("div", { className: `px-3 py-1 rounded text-sm ${theme.primaryButton} ${theme.primaryButtonText}`, children: "Always On" })] }) }), _jsx("div", { className: `p-4 rounded-lg border ${theme.cardBorder}`, children: _jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("div", { className: "flex-1 mr-4", children: [_jsx("h3", { className: `font-medium ${theme.labelText}`, children: "Functional Cookies" }), _jsx("p", { className: `text-sm ${theme.subtitleText}`, children: "Enable personalized features and remember your preferences." })] }), _jsxs("label", { className: "relative inline-flex items-center cursor-pointer", children: [_jsx("input", { type: "checkbox", checked: functional, onChange: (e) => setFunctional(e.target.checked), className: "sr-only peer" }), _jsx("div", { className: "w-11 h-6 bg-slate-700 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-amber-500 rounded-full peer peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-amber-500" })] })] }) }), _jsx("div", { className: `p-4 rounded-lg border ${theme.cardBorder}`, children: _jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("div", { className: "flex-1 mr-4", children: [_jsx("h3", { className: `font-medium ${theme.labelText}`, children: "Analytics Cookies" }), _jsx("p", { className: `text-sm ${theme.subtitleText}`, children: "Help us understand how visitors interact with our website." })] }), _jsxs("label", { className: "relative inline-flex items-center cursor-pointer", children: [_jsx("input", { type: "checkbox", checked: analytics, onChange: (e) => setAnalytics(e.target.checked), className: "sr-only peer" }), _jsx("div", { className: "w-11 h-6 bg-slate-700 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-amber-500 rounded-full peer peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-amber-500" })] })] }) }), _jsx("div", { className: `p-4 rounded-lg border ${theme.cardBorder}`, children: _jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("div", { className: "flex-1 mr-4", children: [_jsx("h3", { className: `font-medium ${theme.labelText}`, children: "Marketing Cookies" }), _jsx("p", { className: `text-sm ${theme.subtitleText}`, children: "Used to deliver personalized advertisements." })] }), _jsxs("label", { className: "relative inline-flex items-center cursor-pointer", children: [_jsx("input", { type: "checkbox", checked: marketing, onChange: (e) => setMarketing(e.target.checked), className: "sr-only peer" }), _jsx("div", { className: "w-11 h-6 bg-slate-700 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-amber-500 rounded-full peer peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-amber-500" })] })] }) })] }), _jsxs("div", { className: "flex flex-col sm:flex-row gap-3", children: [_jsx("button", { onClick: handleSave, className: `flex-1 py-2.5 px-4 rounded-lg font-medium ${theme.primaryButton} ${theme.primaryButtonText} ${theme.primaryButtonHover} transition-colors`, children: "Save Preferences" }), _jsx("button", { onClick: onClose, className: `flex-1 py-2.5 px-4 rounded-lg font-medium border ${theme.secondaryButton} ${theme.secondaryButtonText} ${theme.secondaryButtonBorder} ${theme.secondaryButtonHover} transition-colors`, children: "Cancel" })] }), _jsxs("p", { className: `mt-4 text-xs text-center ${theme.subtitleText}`, children: ["Learn more in our", ' ', _jsx("a", { href: privacyPolicyUrl, className: `${theme.linkText} ${theme.linkHoverText} underline`, target: "_blank", rel: "noopener noreferrer", children: "Privacy Policy" }), cookiePolicyUrl && (_jsxs(_Fragment, { children: [' ', "and", ' ', _jsx("a", { href: cookiePolicyUrl, className: `${theme.linkText} ${theme.linkHoverText} underline`, target: "_blank", rel: "noopener noreferrer", children: "Cookie Policy" })] }))] })] })] }) }));
};
/**
 * GDPR cookie consent banner.
 *
 * Features:
 * - Appears on first visit before login form is interactive
 * - Options: Accept All, Essential Only, Manage Preferences
 * - Preferences modal for granular control
 * - Consent stored in localStorage with timestamp
 */
export const CookieConsent = ({ gdpr, onAccept, colors, }) => {
    const theme = { ...ELDER_LOGIN_THEME, ...colors };
    const [showPreferences, setShowPreferences] = useState(false);
    const handleAcceptAll = useCallback(() => {
        log.debug('User accepted all cookies');
        onAccept({
            accepted: true,
            essential: true,
            functional: true,
            analytics: true,
            marketing: true,
            timestamp: Date.now(),
        });
    }, [onAccept]);
    const handleAcceptEssential = useCallback(() => {
        log.debug('User accepted essential cookies only');
        onAccept({
            accepted: true,
            essential: true,
            functional: false,
            analytics: false,
            marketing: false,
            timestamp: Date.now(),
        });
    }, [onAccept]);
    const handleSavePreferences = useCallback((preferences) => {
        setShowPreferences(false);
        onAccept({
            accepted: true,
            essential: true,
            functional: preferences.functional ?? false,
            analytics: preferences.analytics ?? false,
            marketing: preferences.marketing ?? false,
            timestamp: Date.now(),
        });
    }, [onAccept]);
    const consentText = gdpr.consentText ||
        'We use cookies to enhance your experience. By continuing to visit this site you agree to our use of cookies.';
    return (_jsxs(_Fragment, { children: [_jsx("div", { className: `fixed bottom-0 left-0 right-0 z-50 ${theme.bannerBackground} border-t ${theme.bannerBorder} shadow-lg`, children: _jsx("div", { className: "max-w-4xl mx-auto px-4 py-4 sm:px-6", children: _jsxs("div", { className: "flex flex-col sm:flex-row items-start sm:items-center gap-4", children: [_jsx("div", { className: "hidden sm:flex shrink-0 w-10 h-10 rounded-full bg-amber-500/10 items-center justify-center", children: _jsx("svg", { className: "w-5 h-5 text-amber-400", fill: "none", viewBox: "0 0 24 24", stroke: "currentColor", strokeWidth: 2, children: _jsx("path", { strokeLinecap: "round", strokeLinejoin: "round", d: "M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" }) }) }), _jsx("div", { className: "flex-1", children: _jsxs("p", { className: `text-sm ${theme.bannerText}`, children: [consentText, ' ', _jsx("a", { href: gdpr.privacyPolicyUrl, className: `${theme.linkText} ${theme.linkHoverText} underline`, target: "_blank", rel: "noopener noreferrer", children: "Privacy Policy" }), gdpr.cookiePolicyUrl && (_jsxs(_Fragment, { children: [' | ', _jsx("a", { href: gdpr.cookiePolicyUrl, className: `${theme.linkText} ${theme.linkHoverText} underline`, target: "_blank", rel: "noopener noreferrer", children: "Cookie Policy" })] }))] }) }), _jsxs("div", { className: "flex flex-wrap gap-2 shrink-0", children: [_jsx("button", { onClick: handleAcceptAll, className: `px-4 py-2 text-sm font-medium rounded-lg ${theme.primaryButton} ${theme.primaryButtonText} ${theme.primaryButtonHover} transition-colors`, children: "Accept All" }), _jsx("button", { onClick: handleAcceptEssential, className: `px-4 py-2 text-sm font-medium rounded-lg border ${theme.secondaryButton} ${theme.secondaryButtonText} ${theme.secondaryButtonBorder} ${theme.secondaryButtonHover} transition-colors`, children: "Essential Only" }), gdpr.showPreferences !== false && (_jsx("button", { onClick: () => setShowPreferences(true), className: `px-4 py-2 text-sm font-medium ${theme.linkText} ${theme.linkHoverText} transition-colors`, children: "Manage Preferences" }))] })] }) }) }), _jsx(PreferencesModal, { isOpen: showPreferences, onClose: () => setShowPreferences(false), onSave: handleSavePreferences, theme: theme, privacyPolicyUrl: gdpr.privacyPolicyUrl, cookiePolicyUrl: gdpr.cookiePolicyUrl })] }));
};
//# sourceMappingURL=CookieConsent.js.map