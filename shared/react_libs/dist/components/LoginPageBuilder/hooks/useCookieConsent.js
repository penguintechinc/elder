import { useState, useCallback, useEffect } from 'react';
const STORAGE_KEY = 'gdpr_consent';
/**
 * Logger utility for cookie consent - sanitizes sensitive data
 */
const log = {
    debug: (message, data) => {
        if (process.env.NODE_ENV === 'development') {
            console.debug(`[LoginPage:GDPR] ${message}`, data ?? '');
        }
    },
    info: (message, data) => {
        console.info(`[LoginPage:GDPR] ${message}`, data ?? '');
    },
};
/**
 * Default consent state - only essential cookies accepted
 */
const DEFAULT_CONSENT = {
    accepted: false,
    essential: true, // Always true - required for functionality
    functional: false,
    analytics: false,
    marketing: false,
};
/**
 * Hook for managing GDPR cookie consent state.
 *
 * Features:
 * - Persists consent state to localStorage
 * - Provides granular consent management (essential, functional, analytics, marketing)
 * - Stores timestamp for audit compliance
 * - Returns whether login form should be interactive
 */
export function useCookieConsent(gdpr) {
    const enabled = gdpr?.enabled ?? true;
    const [consent, setConsent] = useState(() => {
        // Load from storage if available
        if (typeof window === 'undefined')
            return DEFAULT_CONSENT;
        try {
            const stored = localStorage.getItem(STORAGE_KEY);
            if (stored) {
                const parsed = JSON.parse(stored);
                log.debug('Loaded consent from storage', {
                    accepted: parsed.accepted,
                    categories: {
                        essential: parsed.essential,
                        functional: parsed.functional,
                        analytics: parsed.analytics,
                        marketing: parsed.marketing,
                    },
                });
                return parsed;
            }
        }
        catch {
            // Invalid stored data, use default
        }
        return DEFAULT_CONSENT;
    });
    const [showBanner, setShowBanner] = useState(!consent.accepted && enabled);
    const [showPreferences, setShowPreferences] = useState(false);
    // Update banner visibility when consent changes
    useEffect(() => {
        setShowBanner(!consent.accepted && enabled);
    }, [consent.accepted, enabled]);
    /**
     * Accept all cookies
     */
    const acceptAll = useCallback(() => {
        const newConsent = {
            accepted: true,
            essential: true,
            functional: true,
            analytics: true,
            marketing: true,
            timestamp: Date.now(),
        };
        log.info('User accepted all cookies', {
            categories: {
                essential: true,
                functional: true,
                analytics: true,
                marketing: true,
            },
        });
        setConsent(newConsent);
        setShowBanner(false);
        setShowPreferences(false);
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(newConsent));
        }
        catch {
            // Storage error - consent still works for session
        }
        return newConsent;
    }, []);
    /**
     * Accept essential cookies only
     */
    const acceptEssential = useCallback(() => {
        const newConsent = {
            accepted: true,
            essential: true,
            functional: false,
            analytics: false,
            marketing: false,
            timestamp: Date.now(),
        };
        log.info('User accepted essential cookies only');
        setConsent(newConsent);
        setShowBanner(false);
        setShowPreferences(false);
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(newConsent));
        }
        catch {
            // Storage error
        }
        return newConsent;
    }, []);
    /**
     * Save custom preferences
     */
    const savePreferences = useCallback((preferences) => {
        const newConsent = {
            accepted: true,
            essential: true, // Always required
            functional: preferences.functional ?? false,
            analytics: preferences.analytics ?? false,
            marketing: preferences.marketing ?? false,
            timestamp: Date.now(),
        };
        log.info('User saved custom cookie preferences', {
            categories: {
                essential: true,
                functional: newConsent.functional,
                analytics: newConsent.analytics,
                marketing: newConsent.marketing,
            },
        });
        setConsent(newConsent);
        setShowBanner(false);
        setShowPreferences(false);
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(newConsent));
        }
        catch {
            // Storage error
        }
        return newConsent;
    }, []);
    /**
     * Open preferences modal
     */
    const openPreferences = useCallback(() => {
        log.debug('Opening cookie preferences modal');
        setShowPreferences(true);
    }, []);
    /**
     * Close preferences modal
     */
    const closePreferences = useCallback(() => {
        log.debug('Closing cookie preferences modal');
        setShowPreferences(false);
    }, []);
    /**
     * Reset consent (for testing or user request)
     */
    const resetConsent = useCallback(() => {
        log.info('Resetting cookie consent');
        setConsent(DEFAULT_CONSENT);
        setShowBanner(enabled);
        try {
            localStorage.removeItem(STORAGE_KEY);
        }
        catch {
            // Storage error
        }
    }, [enabled]);
    // If GDPR is disabled, always allow interaction
    const canInteract = !enabled || consent.accepted;
    return {
        consent,
        showBanner,
        showPreferences,
        canInteract,
        acceptAll,
        acceptEssential,
        savePreferences,
        openPreferences,
        closePreferences,
        resetConsent,
    };
}
//# sourceMappingURL=useCookieConsent.js.map