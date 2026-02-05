import type { CookieConsentState, GDPRConfig } from '../types';
/**
 * Hook for managing GDPR cookie consent state.
 *
 * Features:
 * - Persists consent state to localStorage
 * - Provides granular consent management (essential, functional, analytics, marketing)
 * - Stores timestamp for audit compliance
 * - Returns whether login form should be interactive
 */
export declare function useCookieConsent(gdpr?: GDPRConfig): {
    consent: CookieConsentState;
    showBanner: boolean;
    showPreferences: boolean;
    canInteract: boolean;
    acceptAll: () => CookieConsentState;
    acceptEssential: () => CookieConsentState;
    savePreferences: (preferences: Partial<CookieConsentState>) => CookieConsentState;
    openPreferences: () => void;
    closePreferences: () => void;
    resetConsent: () => void;
};
//# sourceMappingURL=useCookieConsent.d.ts.map