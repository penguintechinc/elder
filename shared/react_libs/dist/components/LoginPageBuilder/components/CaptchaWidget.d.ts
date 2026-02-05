import React from 'react';
import type { CaptchaWidgetProps } from '../types';
/**
 * ALTCHA CAPTCHA widget wrapper.
 *
 * Features:
 * - Dynamic script loading for ALTCHA Web Component
 * - Proof-of-work verification
 * - Event handling for verified/error states
 * - Elder-styled container
 *
 * Requirements:
 * - Backend must expose challenge endpoint at `challengeUrl`
 * - Returns challenge data in ALTCHA format
 */
export declare const CaptchaWidget: React.FC<CaptchaWidgetProps>;
//# sourceMappingURL=CaptchaWidget.d.ts.map