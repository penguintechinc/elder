import type { SAMLProvider } from '../types';
/**
 * Build SAML AuthnRequest XML
 *
 * Creates a minimal SP-initiated SAML 2.0 AuthnRequest following
 * the SAML 2.0 Core specification.
 */
export declare function buildSAMLRequest(config: SAMLProvider): string;
/**
 * Build SAML redirect URL for SP-initiated SSO
 *
 * Uses HTTP-Redirect binding (SAMLRequest as query parameter)
 */
export declare function buildSAMLRedirectUrl(config: SAMLProvider): string;
/**
 * Build SAML POST form for SP-initiated SSO
 *
 * Uses HTTP-POST binding (auto-submitting form)
 * Returns HTML form that should be injected and auto-submitted
 */
export declare function buildSAMLPostForm(config: SAMLProvider): string;
/**
 * Initiate SAML login via redirect
 */
export declare function initiateSAMLLogin(config: SAMLProvider): void;
/**
 * Initiate SAML login via POST (opens in new form submission)
 */
export declare function initiateSAMLPostLogin(config: SAMLProvider): void;
/**
 * Validate SAML RelayState to prevent CSRF attacks
 */
export declare function validateRelayState(receivedRelayState: string): boolean;
/**
 * Get stored SAML request ID for response validation
 */
export declare function getStoredRequestId(): string | null;
/**
 * Clear SAML session storage after successful validation
 */
export declare function clearSAMLSession(): void;
//# sourceMappingURL=saml.d.ts.map