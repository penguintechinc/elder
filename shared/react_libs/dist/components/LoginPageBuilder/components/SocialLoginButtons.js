import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useCallback } from 'react';
import { ELDER_LOGIN_THEME } from '../themes/elderTheme';
import { getProviderLabel, getProviderColors } from '../utils/oauth';
import { GoogleIcon, GitHubIcon, MicrosoftIcon, AppleIcon, TwitchIcon, DiscordIcon, SSOIcon, EnterpriseIcon, } from './icons';
/**
 * Logger utility for social login buttons
 */
const log = {
    debug: (message, data) => {
        if (process.env.NODE_ENV === 'development') {
            console.debug(`[LoginPage:Social] ${message}`, data ?? '');
        }
    },
    info: (message, data) => {
        console.info(`[LoginPage:Social] ${message}`, data ?? '');
    },
};
/**
 * Get icon component for a provider
 */
function getProviderIcon(config) {
    // Check for custom icon first
    if ('icon' in config && config.icon) {
        return config.icon;
    }
    switch (config.provider) {
        case 'google':
            return _jsx(GoogleIcon, { className: "w-5 h-5" });
        case 'github':
            return _jsx(GitHubIcon, { className: "w-5 h-5" });
        case 'microsoft':
            return _jsx(MicrosoftIcon, { className: "w-5 h-5" });
        case 'apple':
            return _jsx(AppleIcon, { className: "w-5 h-5" });
        case 'twitch':
            return _jsx(TwitchIcon, { className: "w-5 h-5" });
        case 'discord':
            return _jsx(DiscordIcon, { className: "w-5 h-5" });
        case 'oidc':
            return _jsx(SSOIcon, { className: "w-5 h-5" });
        case 'saml':
            return _jsx(EnterpriseIcon, { className: "w-5 h-5" });
        case 'oauth2':
            return _jsx(SSOIcon, { className: "w-5 h-5" });
        default:
            return _jsx(SSOIcon, { className: "w-5 h-5" });
    }
}
/**
 * Get button styling for a provider
 */
function getButtonStyles(config, theme) {
    // Check for custom styling
    if (config.provider === 'oauth2' && config.buttonColor) {
        return {
            className: `w-full flex items-center justify-center gap-3 py-2.5 px-4 rounded-lg font-medium border transition-colors duration-200 disabled:opacity-50 disabled:cursor-not-allowed`,
            style: {
                backgroundColor: config.buttonColor,
                color: config.textColor || '#ffffff',
                borderColor: config.buttonColor,
            },
        };
    }
    // Built-in provider colors
    const brandColors = getProviderColors(config.provider);
    if (brandColors) {
        return {
            className: `w-full flex items-center justify-center gap-3 py-2.5 px-4 rounded-lg font-medium border transition-colors duration-200 disabled:opacity-50 disabled:cursor-not-allowed ${brandColors.background} ${brandColors.text} ${brandColors.hover}`,
        };
    }
    // Default to theme-based styling
    return {
        className: `w-full flex items-center justify-center gap-3 py-2.5 px-4 rounded-lg font-medium border transition-colors duration-200 disabled:opacity-50 disabled:cursor-not-allowed ${theme.socialButtonBackground} ${theme.socialButtonText} ${theme.socialButtonBorder} ${theme.socialButtonHover}`,
    };
}
/**
 * Social login buttons component.
 *
 * Features:
 * - Built-in icons and branding for major providers
 * - Support for custom OAuth2, OIDC, and SAML providers
 * - Brand-consistent button styling
 * - Disabled state during login
 */
export const SocialLoginButtons = ({ providers, onProviderClick, colors, disabled = false, }) => {
    const theme = { ...ELDER_LOGIN_THEME, ...colors };
    const handleClick = useCallback((config) => {
        log.info('Social login button clicked', { provider: config.provider });
        onProviderClick(config);
    }, [onProviderClick]);
    if (!providers || providers.length === 0) {
        return null;
    }
    return (_jsx("div", { className: "space-y-3", children: providers.map((config, index) => {
            const label = getProviderLabel(config);
            const icon = getProviderIcon(config);
            const buttonStyles = getButtonStyles(config, theme);
            return (_jsxs("button", { type: "button", onClick: () => handleClick(config), disabled: disabled, className: buttonStyles.className, style: buttonStyles.style, children: [icon, _jsx("span", { children: label })] }, `${config.provider}-${index}`));
        }) }));
};
/**
 * Divider component for "or" separator between login methods
 */
export const LoginDivider = ({ colors }) => {
    const theme = { ...ELDER_LOGIN_THEME, ...colors };
    return (_jsxs("div", { className: "relative my-6", children: [_jsx("div", { className: "absolute inset-0 flex items-center", children: _jsx("div", { className: `w-full border-t ${theme.dividerColor}` }) }), _jsx("div", { className: "relative flex justify-center text-sm", children: _jsx("span", { className: `px-4 ${theme.cardBackground} ${theme.dividerText}`, children: "or continue with" }) })] }));
};
//# sourceMappingURL=SocialLoginButtons.js.map