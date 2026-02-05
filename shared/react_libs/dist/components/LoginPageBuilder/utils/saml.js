/**
 * Logger utility for SAML - never logs certificates or assertions
 */
const log = {
    debug: (message, data) => {
        if (process.env.NODE_ENV === 'development') {
            console.debug(`[LoginPage:SAML] ${message}`, data ? sanitizeSamlLog(data) : '');
        }
    },
    info: (message, data) => {
        console.info(`[LoginPage:SAML] ${message}`, data ? sanitizeSamlLog(data) : '');
    },
    error: (message, error) => {
        console.error(`[LoginPage:SAML] ${message}`, error instanceof Error ? error.message : error);
    },
};
function sanitizeSamlLog(data) {
    const sanitized = { ...data };
    // Never log certificates, assertions, or credentials
    if ('certificate' in sanitized)
        sanitized.certificate = '[REDACTED]';
    if ('assertion' in sanitized)
        sanitized.assertion = '[REDACTED]';
    if ('SAMLResponse' in sanitized)
        sanitized.SAMLResponse = '[REDACTED]';
    if ('SAMLRequest' in sanitized)
        sanitized.SAMLRequest = '[LENGTH:' + String(sanitized.SAMLRequest).length + ']';
    return sanitized;
}
/**
 * Generate a unique request ID for SAML AuthnRequest
 */
function generateRequestId() {
    const array = new Uint8Array(16);
    crypto.getRandomValues(array);
    return '_' + Array.from(array, (byte) => byte.toString(16).padStart(2, '0')).join('');
}
/**
 * Generate ISO timestamp for SAML
 */
function getISOTimestamp() {
    return new Date().toISOString();
}
/**
 * Base64 encode string for SAML
 */
function base64Encode(str) {
    return btoa(unescape(encodeURIComponent(str)));
}
/**
 * Build SAML AuthnRequest XML
 *
 * Creates a minimal SP-initiated SAML 2.0 AuthnRequest following
 * the SAML 2.0 Core specification.
 */
export function buildSAMLRequest(config) {
    const requestId = generateRequestId();
    const issueInstant = getISOTimestamp();
    // Store request ID for response validation
    sessionStorage.setItem('saml_request_id', requestId);
    const authnRequest = `<?xml version="1.0" encoding="UTF-8"?>
<samlp:AuthnRequest
    xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
    xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"
    ID="${requestId}"
    Version="2.0"
    IssueInstant="${issueInstant}"
    Destination="${config.idpSsoUrl}"
    AssertionConsumerServiceURL="${config.acsUrl}"
    ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST">
    <saml:Issuer>${config.entityId}</saml:Issuer>
    <samlp:NameIDPolicy
        Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
        AllowCreate="true"/>
</samlp:AuthnRequest>`;
    log.debug('Built SAML AuthnRequest', {
        requestId,
        entityId: config.entityId,
        acsUrl: config.acsUrl,
    });
    return authnRequest;
}
/**
 * Build SAML redirect URL for SP-initiated SSO
 *
 * Uses HTTP-Redirect binding (SAMLRequest as query parameter)
 */
export function buildSAMLRedirectUrl(config) {
    const authnRequest = buildSAMLRequest(config);
    // Encode the request (deflate + base64 + URL encode is ideal, but base64 is minimum)
    const encodedRequest = base64Encode(authnRequest);
    // Generate relay state for CSRF protection
    const relayState = generateRequestId();
    sessionStorage.setItem('saml_relay_state', relayState);
    // Build URL with query parameters
    const url = new URL(config.idpSsoUrl);
    url.searchParams.set('SAMLRequest', encodedRequest);
    url.searchParams.set('RelayState', relayState);
    log.info('Built SAML redirect URL', {
        idpSsoUrl: config.idpSsoUrl,
        entityId: config.entityId,
    });
    return url.toString();
}
/**
 * Build SAML POST form for SP-initiated SSO
 *
 * Uses HTTP-POST binding (auto-submitting form)
 * Returns HTML form that should be injected and auto-submitted
 */
export function buildSAMLPostForm(config) {
    const authnRequest = buildSAMLRequest(config);
    const encodedRequest = base64Encode(authnRequest);
    const relayState = generateRequestId();
    sessionStorage.setItem('saml_relay_state', relayState);
    log.info('Built SAML POST form', {
        idpSsoUrl: config.idpSsoUrl,
        entityId: config.entityId,
    });
    return `
    <html>
    <body onload="document.forms[0].submit()">
      <noscript>
        <p>JavaScript is disabled. Click the button below to continue.</p>
      </noscript>
      <form method="POST" action="${config.idpSsoUrl}">
        <input type="hidden" name="SAMLRequest" value="${encodedRequest}" />
        <input type="hidden" name="RelayState" value="${relayState}" />
        <noscript>
          <input type="submit" value="Continue to Login" />
        </noscript>
      </form>
    </body>
    </html>
  `;
}
/**
 * Initiate SAML login via redirect
 */
export function initiateSAMLLogin(config) {
    const url = buildSAMLRedirectUrl(config);
    log.info('Initiating SAML redirect');
    window.location.href = url;
}
/**
 * Initiate SAML login via POST (opens in new form submission)
 */
export function initiateSAMLPostLogin(config) {
    const formHtml = buildSAMLPostForm(config);
    log.info('Initiating SAML POST login');
    // Create a new window/tab with the auto-submitting form
    const popup = window.open('', '_self');
    if (popup) {
        popup.document.write(formHtml);
        popup.document.close();
    }
    else {
        log.error('Failed to open SAML login window');
        throw new Error('Failed to initiate SAML login - popup blocked');
    }
}
/**
 * Validate SAML RelayState to prevent CSRF attacks
 */
export function validateRelayState(receivedRelayState) {
    const storedRelayState = sessionStorage.getItem('saml_relay_state');
    const isValid = storedRelayState === receivedRelayState;
    if (!isValid) {
        log.error('SAML RelayState validation failed - possible CSRF attack');
    }
    else {
        log.debug('SAML RelayState validated successfully');
        sessionStorage.removeItem('saml_relay_state');
    }
    return isValid;
}
/**
 * Get stored SAML request ID for response validation
 */
export function getStoredRequestId() {
    return sessionStorage.getItem('saml_request_id');
}
/**
 * Clear SAML session storage after successful validation
 */
export function clearSAMLSession() {
    sessionStorage.removeItem('saml_request_id');
    sessionStorage.removeItem('saml_relay_state');
    log.debug('SAML session storage cleared');
}
//# sourceMappingURL=saml.js.map