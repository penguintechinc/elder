import type { SocialLoginConfig, BuiltInOAuth2Provider, CustomOAuth2Provider, OIDCProvider } from '../types';
/**
 * Generate a cryptographically random state parameter for CSRF protection
 */
export declare function generateState(): string;
/**
 * Generate PKCE code verifier (43-128 characters)
 */
export declare function generateCodeVerifier(): string;
/**
 * Generate PKCE code challenge from verifier
 */
export declare function generateCodeChallenge(verifier: string): Promise<string>;
/**
 * Build OAuth2 authorization URL for built-in providers
 */
export declare function buildOAuth2Url(config: BuiltInOAuth2Provider): string;
/**
 * Build OAuth2 authorization URL for custom providers
 */
export declare function buildCustomOAuth2Url(config: CustomOAuth2Provider): string;
/**
 * Build OIDC authorization URL with auto-discovery support
 */
export declare function buildOIDCUrl(config: OIDCProvider): Promise<string>;
/**
 * Validate OAuth state parameter to prevent CSRF attacks
 */
export declare function validateState(receivedState: string): boolean;
/**
 * Get display label for a social login provider
 */
export declare function getProviderLabel(config: SocialLoginConfig): string;
/**
 * Get brand colors for built-in providers
 */
export declare function getProviderColors(provider: string): {
    background: string;
    text: string;
    hover: string;
} | null;
//# sourceMappingURL=oauth.d.ts.map