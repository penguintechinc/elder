/**
 * Standardized form field types and utilities for Elder
 * Provides consistent space handling and validation across all forms
 */

export type FieldType =
  | 'text'           // trim only
  | 'email'          // strip all spaces
  | 'username'       // strip all spaces
  | 'password'       // trim only
  | 'password_generate' // trim only, with generator button
  | 'url'            // strip all spaces
  | 'domain'         // strip all spaces
  | 'ip'             // strip all spaces
  | 'cidr'           // CIDR notation (allows . / and numbers)
  | 'path'           // strip all spaces
  | 'slug'           // strip all spaces
  | 'textarea'       // trim or undefined
  | 'select'         // no processing
  | 'number'         // strip spaces before parse
  | 'checkbox'       // no processing
  | 'date'           // no processing
  | 'color'          // strip all spaces
  | 'multiline'      // split by newline, strip each
  | 'cron'           // cron expression (allows * / - ,)

export interface SelectOption {
  value: string | number
  label: string
}

export interface FormField {
  name: string
  label: string
  type: FieldType
  required?: boolean
  placeholder?: string
  options?: SelectOption[]
  rows?: number  // for textarea/multiline
  defaultValue?: any
  hidden?: boolean
  disabled?: boolean
  helpText?: string
  // For conditional visibility
  triggerField?: string  // Simple: show when this field is truthy
  showWhen?: (values: Record<string, any>) => boolean  // Complex: custom condition
  // Custom validation (in addition to type-based validation)
  validate?: (value: any) => string | undefined  // Return error message or undefined
}

/**
 * Check for dangerous patterns that could indicate injection attacks
 */
function containsDangerousPattern(value: string): string | undefined {
  // SQL injection patterns
  const sqlPatterns = /('.*(--))|(--)|(;.*DROP|;.*DELETE|;.*UPDATE|;.*INSERT|;.*SELECT|OR\s+1\s*=\s*1|OR\s+'1'\s*=\s*'1')/i
  if (sqlPatterns.test(value)) {
    return 'Invalid characters detected'
  }

  // Script/code injection patterns
  const scriptPatterns = /<script|javascript:|on\w+\s*=|eval\(|exec\(|\$\{|`.*`|\{\{/i
  if (scriptPatterns.test(value)) {
    return 'Invalid characters detected'
  }

  // Format string patterns
  const formatPatterns = /%[0-9]*[sndxXpP]|%n/
  if (formatPatterns.test(value)) {
    return 'Invalid characters detected'
  }

  // Command injection patterns
  const cmdPatterns = /\$\(|\||&&|;.*\w+|`/
  if (cmdPatterns.test(value)) {
    return 'Invalid characters detected'
  }

  return undefined
}

/**
 * Validate a field value based on its type
 * Returns error message or undefined if valid
 */
export function validateFieldValue(value: any, field: FormField): string | undefined {
  // Skip validation if empty and not required
  if ((value === '' || value === undefined || value === null) && !field.required) {
    return undefined
  }

  // Required validation
  if (field.required && (value === '' || value === undefined || value === null)) {
    return `${field.label} is required`
  }

  // Type-specific validation
  if (value && typeof value === 'string') {
    switch (field.type) {
      case 'username':
        // Only alphanumeric, underscore, hyphen, period
        if (!/^[a-zA-Z0-9_.-]+$/.test(value.replace(/\s+/g, ''))) {
          return 'Username can only contain letters, numbers, underscores, hyphens, and periods'
        }
        break

      case 'slug':
        // Only lowercase alphanumeric and hyphens
        if (!/^[a-z0-9-]+$/.test(value.replace(/\s+/g, ''))) {
          return 'Slug can only contain lowercase letters, numbers, and hyphens'
        }
        break

      case 'text': {
        // Only allow alphanumeric, spaces, and limited special chars: - ! @ . $
        if (!/^[a-zA-Z0-9\s\-!@.$]*$/.test(value)) {
          return 'Only letters, numbers, spaces, and - ! @ . $ are allowed'
        }
        // Also check for dangerous patterns
        const textDanger = containsDangerousPattern(value)
        if (textDanger) return textDanger
        break
      }

      case 'textarea': {
        // Only allow alphanumeric, spaces, newlines, and limited special chars: - ! @ . $
        if (!/^[a-zA-Z0-9\s\-!@.$\n\r]*$/.test(value)) {
          return 'Only letters, numbers, spaces, and - ! @ . $ are allowed'
        }
        // Also check for dangerous patterns
        const textareaDanger = containsDangerousPattern(value)
        if (textareaDanger) return textareaDanger
        break
      }

      case 'password':
      case 'password_generate':
        // Minimum 8 characters
        if (value.length < 8) {
          return 'Password must be at least 8 characters'
        }
        // Only allow alphanumeric and safe special chars: ! @ # $ % ^ & * ( ) - _ = +
        if (!/^[a-zA-Z0-9!@#$%^&*()\-_=+]+$/.test(value)) {
          return 'Password can only contain letters, numbers, and ! @ # $ % ^ & * ( ) - _ = +'
        }
        break

      case 'email':
        if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.replace(/\s+/g, ''))) {
          return 'Invalid email format'
        }
        break

      case 'url':
        try {
          new URL(value.replace(/\s+/g, ''))
        } catch {
          return 'Invalid URL format'
        }
        break

      case 'ip': {
        const ip = value.replace(/\s+/g, '')
        // IPv4 or IPv6 basic validation
        const ipv4Regex = /^(\d{1,3}\.){3}\d{1,3}(\/\d{1,2})?$/
        const ipv6Regex = /^([0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}(\/\d{1,3})?$/
        if (!ipv4Regex.test(ip) && !ipv6Regex.test(ip)) {
          return 'Invalid IP address format'
        }
        break
      }

      case 'domain': {
        const domain = value.replace(/\s+/g, '')
        if (!/^[a-zA-Z0-9][a-zA-Z0-9-_.]+[a-zA-Z0-9]$/.test(domain)) {
          return 'Invalid domain format'
        }
        break
      }

      case 'cidr': {
        const cidrValue = value.replace(/\s+/g, '')
        // Basic CIDR validation: IP address followed by /prefix
        const cidrRegex = /^(\d{1,3}\.){3}\d{1,3}\/\d{1,2}$/
        if (!cidrRegex.test(cidrValue)) {
          return 'Invalid CIDR format. Use format like 10.0.1.0/24'
        }
        // Validate IP octets are in range 0-255
        const [cidrIp, cidrPrefix] = cidrValue.split('/')
        const cidrOctets = cidrIp.split('.').map(Number)
        if (cidrOctets.some(octet => octet < 0 || octet > 255)) {
          return 'IP octets must be between 0 and 255'
        }
        // Validate prefix is in range 0-32
        const cidrPrefixNum = parseInt(cidrPrefix)
        if (cidrPrefixNum < 0 || cidrPrefixNum > 32) {
          return 'Prefix must be between 0 and 32'
        }
        break
      }

      case 'cron': {
        // Basic cron expression validation (5 or 6 fields)
        // Allow: numbers, *, /, -, , and spaces between fields
        const cronParts = value.trim().split(/\s+/)
        if (cronParts.length < 5 || cronParts.length > 6) {
          return 'Cron expression must have 5 or 6 fields (minute hour day month weekday [year])'
        }
        // Each part should only contain valid cron characters
        const cronFieldRegex = /^[0-9*\-,?LW#]+$/
        for (const part of cronParts) {
          if (!cronFieldRegex.test(part)) {
            return 'Invalid cron expression format'
          }
        }
        break
      }

      case 'color': {
        const color = value.replace(/\s+/g, '')
        if (!/^#[0-9A-Fa-f]{6}$/.test(color) && !/^#[0-9A-Fa-f]{3}$/.test(color)) {
          return 'Invalid color format (use #RRGGBB)'
        }
        break
      }
    }
  }

  // Number validation
  if (field.type === 'number' && value !== '' && value !== undefined) {
    const num = typeof value === 'string' ? parseFloat(value.replace(/\s+/g, '')) : value
    if (isNaN(num)) {
      return 'Must be a valid number'
    }
  }

  // Custom validation
  if (field.validate) {
    return field.validate(value)
  }

  return undefined
}

/**
 * Validate all form fields
 * Returns object with field names as keys and error messages as values
 * Skips validation for hidden fields (showWhen returns false)
 */
export function validateForm(
  values: Record<string, any>,
  fields: FormField[]
): Record<string, string> {
  const errors: Record<string, string> = {}

  for (const field of fields) {
    // Skip validation for hidden fields
    if (field.hidden) continue
    if (field.triggerField && !values[field.triggerField]) continue
    if (field.showWhen && !field.showWhen(values)) continue

    const error = validateFieldValue(values[field.name], field)
    if (error) {
      errors[field.name] = error
    }
  }

  return errors
}

export interface FormConfig {
  fields: FormField[]
  submitLabel?: string
  cancelLabel?: string
}

/**
 * Process a form value based on its field type
 * Applies appropriate space handling automatically
 */
export function processFieldValue(value: any, type: FieldType): any {
  if (value === null || value === undefined) {
    return undefined
  }

  switch (type) {
    // Strip ALL spaces - technical fields
    case 'email':
    case 'username':
    case 'url':
    case 'domain':
    case 'ip':
    case 'cidr':
    case 'path':
    case 'slug':
    case 'color':
      return typeof value === 'string'
        ? value.replace(/\s+/g, '') || undefined
        : value

    // Trim only - user-facing text that may have internal spaces
    case 'text':
    case 'password':
    case 'password_generate':
      return typeof value === 'string'
        ? value.trim() || undefined
        : value

    // Trim and return undefined if empty
    case 'textarea':
      return typeof value === 'string'
        ? value.trim() || undefined
        : value

    // Cron expression - just trim
    case 'cron':
      return typeof value === 'string'
        ? value.trim() || undefined
        : value

    // Split by newline, strip each element
    case 'multiline':
      if (typeof value === 'string') {
        return value
          .split('\n')
          .map(v => v.replace(/\s+/g, ''))
          .filter(Boolean)
      }
      return value

    // Strip spaces before parsing number
    case 'number':
      if (typeof value === 'string') {
        const cleaned = value.replace(/\s+/g, '')
        if (!cleaned) return undefined
        const num = parseFloat(cleaned)
        return isNaN(num) ? undefined : num
      }
      return value

    // No processing needed
    case 'select':
    case 'checkbox':
    case 'date':
    default:
      return value
  }
}

/**
 * Process all form values based on field definitions
 */
export function processFormData(
  values: Record<string, any>,
  fields: FormField[]
): Record<string, any> {
  const result: Record<string, any> = {}

  for (const field of fields) {
    const value = values[field.name]
    const processed = processFieldValue(value, field.type)

    // Only include if has value or is required
    if (processed !== undefined || field.required) {
      result[field.name] = processed
    }
  }

  return result
}

/**
 * Get default values from field definitions
 */
export function getDefaultValues(fields: FormField[]): Record<string, any> {
  const values: Record<string, any> = {}

  for (const field of fields) {
    if (field.defaultValue !== undefined) {
      values[field.name] = field.defaultValue
    } else {
      // Set sensible defaults based on type
      switch (field.type) {
        case 'checkbox':
          values[field.name] = false
          break
        case 'select':
          values[field.name] = ''
          break
        default:
          values[field.name] = ''
      }
    }
  }

  return values
}
