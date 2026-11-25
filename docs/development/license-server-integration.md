## PenguinTech License Server Integration

All projects should integrate with the centralized PenguinTech License Server at `https://license.penguintech.io` for feature gating and enterprise functionality.

### Universal JSON Response Format

All API responses follow this standardized structure based on the `.JSONDESIGN` specification:

```json
{
    "customer": "string",           // Organization name
    "product": "string",            // Product identifier
    "license_version": "string",    // License schema version (2.0)
    "license_key": "string",        // Full license key
    "expires_at": "ISO8601",        // Expiration timestamp
    "issued_at": "ISO8601",         // Issue timestamp
    "tier": "string",               // community/professional/enterprise
    "features": [
        {
            "name": "string",           // Feature identifier
            "entitled": boolean,        // Feature enabled/disabled
            "units": integer,           // Usage units (0 = unlimited, -1 = not applicable)
            "description": "string",    // Human-readable description
            "metadata": object          // Additional feature-specific data
        }
    ],
    "limits": {
        "max_servers": integer,     // -1 = unlimited
        "max_users": integer,       // -1 = unlimited
        "data_retention_days": integer
    },
    "metadata": {
        "server_id": "string",      // For keepalives
        "support_tier": "string",   // community/email/priority
        "custom_fields": object     // Customer-specific data
    }
}
```

### Authentication

All API calls use Bearer token authentication where the license key serves as the bearer token:

```bash
Authorization: Bearer PENG-XXXX-XXXX-XXXX-XXXX-ABCD
```

### License Key Format

- Format: `PENG-XXXX-XXXX-XXXX-XXXX-ABCD`
- Regex: `^PENG-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$`
- Includes SHA256 checksum in final segment
- Universal prefix for all PenguinTech products

### Core API Endpoints

#### 1. Universal License Validation

**Endpoint:** `POST /api/v2/validate`

```bash
curl -X POST https://license.penguintech.io/api/v2/validate \
  -H "Authorization: Bearer PENG-XXXX-XXXX-XXXX-XXXX-ABCD" \
  -H "Content-Type: application/json" \
  -d '{"product": "your-product-name"}'
```

#### 2. Feature Checking

**Endpoint:** `POST /api/v2/features`

```bash
curl -X POST https://license.penguintech.io/api/v2/features \
  -H "Authorization: Bearer PENG-XXXX-XXXX-XXXX-XXXX-ABCD" \
  -H "Content-Type: application/json" \
  -d '{"product": "your-product-name", "feature": "advanced_feature"}'
```

#### 3. Keepalive/Usage Reporting

**Endpoint:** `POST /api/v2/keepalive`

```bash
curl -X POST https://license.penguintech.io/api/v2/keepalive \
  -H "Authorization: Bearer PENG-XXXX-XXXX-XXXX-XXXX-ABCD" \
  -H "Content-Type: application/json" \
  -d '{
    "product": "your-product-name",
    "server_id": "srv_8f7d6e5c4b3a2918",
    "hostname": "server-01.company.com",
    "version": "1.2.3",
    "uptime_seconds": 86400,
    "usage_stats": {
        "active_users": 45,
        "feature_usage": {
            "feature_name": {"usage_count": 1250000}
        }
    }
  }'
```

### Client Library Integration

#### Python Client Example

```python
import requests
from datetime import datetime, timedelta

class PenguinTechLicenseClient:
    def __init__(self, license_key, product, base_url="https://license.penguintech.io"):
        self.license_key = license_key
        self.product = product
        self.base_url = base_url
        self.server_id = None
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {license_key}",
            "Content-Type": "application/json"
        })

    def validate(self):
        """Validate license and get server ID for keepalives"""
        response = self.session.post(
            f"{self.base_url}/api/v2/validate",
            json={"product": self.product}
        )

        if response.status_code == 200:
            data = response.json()
            if data.get("valid"):
                self.server_id = data["metadata"].get("server_id")
                return data

        return {"valid": False, "message": f"Validation failed: {response.text}"}

    def check_feature(self, feature):
        """Check if specific feature is enabled"""
        response = self.session.post(
            f"{self.base_url}/api/v2/features",
            json={"product": self.product, "feature": feature}
        )

        if response.status_code == 200:
            data = response.json()
            return data.get("features", [{}])[0].get("entitled", False)

        return False

    def keepalive(self, usage_data=None):
        """Send keepalive with optional usage statistics"""
        if not self.server_id:
            validation = self.validate()
            if not validation.get("valid"):
                return validation

        payload = {
            "product": self.product,
            "server_id": self.server_id
        }

        if usage_data:
            payload.update(usage_data)

        response = self.session.post(
            f"{self.base_url}/api/v2/keepalive",
            json=payload
        )

        return response.json()

# Usage example
def requires_feature(feature_name):
    """Decorator to gate functionality behind license features"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not AVAILABLE_FEATURES.get(feature_name, False):
                raise FeatureNotAvailableError(f"Feature '{feature_name}' requires upgrade")
            return func(*args, **kwargs)
        return wrapper
    return decorator

@requires_feature("advanced_feature")
def advanced_functionality():
    """This function only works with professional+ licenses"""
    pass
```

#### Go Client Example

```go
package license

import (
    "bytes"
    "encoding/json"
    "fmt"
    "net/http"
    "time"
)

type Client struct {
    LicenseKey string
    Product    string
    BaseURL    string
    ServerID   string
    HTTPClient *http.Client
}

type ValidationResponse struct {
    Valid     bool   `json:"valid"`
    Customer  string `json:"customer"`
    Tier      string `json:"tier"`
    Features  []Feature `json:"features"`
    Metadata  struct {
        ServerID string `json:"server_id"`
    } `json:"metadata"`
}

type Feature struct {
    Name     string `json:"name"`
    Entitled bool   `json:"entitled"`
}

func NewClient(licenseKey, product string) *Client {
    return &Client{
        LicenseKey: licenseKey,
        Product:    product,
        BaseURL:    "https://license.penguintech.io",
        HTTPClient: &http.Client{Timeout: 30 * time.Second},
    }
}

func (c *Client) Validate() (*ValidationResponse, error) {
    payload := map[string]string{"product": c.Product}

    resp, err := c.makeRequest("POST", "/api/v2/validate", payload)
    if err != nil {
        return nil, err
    }

    var validation ValidationResponse
    if err := json.Unmarshal(resp, &validation); err != nil {
        return nil, err
    }

    if validation.Valid {
        c.ServerID = validation.Metadata.ServerID
    }

    return &validation, nil
}

func (c *Client) CheckFeature(feature string) (bool, error) {
    payload := map[string]string{
        "product": c.Product,
        "feature": feature,
    }

    resp, err := c.makeRequest("POST", "/api/v2/features", payload)
    if err != nil {
        return false, err
    }

    var response struct {
        Features []Feature `json:"features"`
    }

    if err := json.Unmarshal(resp, &response); err != nil {
        return false, err
    }

    if len(response.Features) > 0 {
        return response.Features[0].Entitled, nil
    }

    return false, nil
}

func (c *Client) makeRequest(method, endpoint string, payload interface{}) ([]byte, error) {
    jsonData, err := json.Marshal(payload)
    if err != nil {
        return nil, err
    }

    req, err := http.NewRequest(method, c.BaseURL+endpoint, bytes.NewBuffer(jsonData))
    if err != nil {
        return nil, err
    }

    req.Header.Set("Authorization", "Bearer "+c.LicenseKey)
    req.Header.Set("Content-Type", "application/json")

    resp, err := c.HTTPClient.Do(req)
    if err != nil {
        return nil, err
    }
    defer resp.Body.Close()

    var buf bytes.Buffer
    _, err = buf.ReadFrom(resp.Body)
    if err != nil {
        return nil, err
    }

    if resp.StatusCode != http.StatusOK {
        return nil, fmt.Errorf("API request failed: %d", resp.StatusCode)
    }

    return buf.Bytes(), nil
}
```

### Environment Variables for License Integration

```bash
# License Server Configuration
LICENSE_KEY=PENG-XXXX-XXXX-XXXX-XXXX-ABCD
LICENSE_SERVER_URL=https://license.penguintech.io
PRODUCT_NAME=your-product-identifier

# Optional: Custom License Server (for testing/development)
LICENSE_SERVER_URL=https://license-dev.penguintech.io
```
