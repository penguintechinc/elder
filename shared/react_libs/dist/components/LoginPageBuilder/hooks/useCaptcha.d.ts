import type { UseCaptchaReturn, CaptchaConfig } from '../types';
/**
 * Hook for managing CAPTCHA state and failed login attempt tracking.
 *
 * Features:
 * - Tracks failed login attempts in localStorage
 * - Shows CAPTCHA after threshold is exceeded
 * - Auto-resets attempts after configurable timeout (default: 15 min)
 * - Provides verification state management
 */
export declare function useCaptcha(config?: CaptchaConfig): UseCaptchaReturn;
//# sourceMappingURL=useCaptcha.d.ts.map