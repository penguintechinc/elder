# Okta Profile URL Synchronization

## Overview

When Elder syncs identities from Okta, it can automatically update each user's `profileUrl` in Okta to link back to their Elder profile page using their unique `village_id`.

This creates bidirectional linking between Elder and Okta, making it easy to:
- Navigate from Okta to Elder profiles
- Cross-reference users between systems
- Provide consistent identity tracking

## Configuration

### Environment Variables

```bash
# Elder Web UI base URL (where profiles are hosted)
ELDER_WEB_URL=https://elder.example.com

# Enable/disable profile URL sync (default: true)
OKTA_SYNC_PROFILE_URL=true

# Standard Okta settings
OKTA_ENABLED=true
OKTA_DOMAIN=your-org.okta.com
OKTA_API_TOKEN=your-api-token
```

### Example Configuration

```bash
# Production
ELDER_WEB_URL=https://elder.penguintech.io
OKTA_SYNC_PROFILE_URL=true

# Development
ELDER_WEB_URL=http://localhost:3000
OKTA_SYNC_PROFILE_URL=false  # Disable in dev
```

## How It Works

### 1. User Sync Process

When the Okta connector syncs users:

```python
# Okta user data synced to Elder
okta_user = {
    "id": "00u1a2b3c4d5e6f7g8h9",
    "profile": {
        "email": "john.doe@example.com",
        "firstName": "John",
        "lastName": "Doe"
    }
}

# Creates/updates Elder identity
elder_identity = {
    "provider": "okta",
    "provider_id": "00u1a2b3c4d5e6f7g8h9",
    "email": "john.doe@example.com",
    "village_id": "abc123def456..."  # Unique Elder ID
}
```

### 2. Profile URL Update

After syncing to Elder, the connector updates Okta:

```python
# Update Okta user's profileUrl
profileUrl = f"{ELDER_WEB_URL}/profile/{village_id}"
# Example: "https://elder.example.com/profile/abc123def456..."

await okta_connector.update_user_profile_url(
    user_id="00u1a2b3c4d5e6f7g8h9",
    village_id="abc123def456..."
)
```

### 3. Result in Okta

The user's Okta profile now contains:

```json
{
  "id": "00u1a2b3c4d5e6f7g8h9",
  "profile": {
    "email": "john.doe@example.com",
    "firstName": "John",
    "lastName": "Doe",
    "profileUrl": "https://elder.example.com/profile/abc123def456..."
  }
}
```

## API Method

### `update_user_profile_url(user_id, village_id)`

Updates a user's profileUrl in Okta to link to their Elder profile.

**Parameters:**
- `user_id` (str): Okta user ID (e.g., "00u1a2b3c4d5e6f7g8h9")
- `village_id` (str): Elder identity village_id (e.g., "abc123def456...")

**Returns:**
- `bool`: True if successful, False otherwise

**Example:**

```python
from apps.worker.connectors.okta_connector import OktaConnector

connector = OktaConnector()
await connector.connect()

success = await connector.update_user_profile_url(
    user_id="00u1a2b3c4d5e6f7g8h9",
    village_id="abc123def456789"
)

if success:
    print("Profile URL updated successfully")
else:
    print("Failed to update profile URL")
```

## Benefits

### 1. **Seamless Navigation**
Click a user's profile in Okta → automatically links to Elder profile with full context

### 2. **Audit Trail**
Clear bidirectional linkage for compliance and security investigations

### 3. **Reduced Context Switching**
No need to manually search for users in Elder when viewing them in Okta

### 4. **Consistent Identification**
Village IDs provide stable, unique identifiers across system integrations

## Implementation Status

### ✅ Completed
- Configuration settings (`elder_web_url`, `okta_sync_profile_url`)
- `update_user_profile_url()` method in OktaConnector
- Okta API integration (POST /api/v1/users/{userId})
- Error handling and logging

### 🔄 Pending
- Full identity sync implementation in Elder API client
- Automatic profile URL sync during user sync
- Bulk update utility for existing users

### 📋 Future Enhancements
- Support for custom profile URL patterns
- Profile URL verification/validation
- Bulk re-sync command for all users
- Webhook trigger for real-time updates

## Troubleshooting

### Profile URLs Not Updating

**Check Configuration:**
```bash
# Verify settings
echo $ELDER_WEB_URL
echo $OKTA_SYNC_PROFILE_URL
```

**Check Logs:**
```bash
# Look for profile URL update messages
docker logs elder-worker | grep "update_user_profile_url"
```

**Common Issues:**
1. **Missing village_id**: Identity not synced to Elder yet
2. **Invalid Okta token**: Check OKTA_API_TOKEN permissions
3. **Network issues**: Verify worker can reach Okta API
4. **Disabled feature**: Ensure OKTA_SYNC_PROFILE_URL=true

### Permissions Required

The Okta API token needs:
- **Users: Update** - To modify user profiles
- **Users: Read** - To fetch user data during sync

## Security Considerations

### Access Control
- Profile URLs are read-only in Okta
- Elder enforces authentication for profile access
- Village IDs are safe to expose (non-sequential, unpredictable)

### Data Privacy
- Only `profileUrl` field is updated in Okta
- No sensitive Elder data is written to Okta profiles
- Profile URLs don't reveal internal Elder IDs

### Compliance
- Creates audit trail in both systems
- Supports SOC 2, ISO 27001 requirements
- GDPR-compliant (no PII in URLs beyond village_id)

## Related Documentation

- [Okta Connector Documentation](./okta-connector.md)
- [Identity Management](./iam.md)
- [Village IDs](./village-ids.md)

---

**Version**: v3.1.0
**Last Updated**: 2026-02-02
**Feature Status**: Ready for integration
**License**: Enterprise Feature
