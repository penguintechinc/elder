export function parseEgressDomains(raw: string): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const part of raw.split(",")) {
    const trimmed = part.trim().toLowerCase();
    if (trimmed && !seen.has(trimmed)) {
      seen.add(trimmed);
      result.push(trimmed);
    }
  }
  return result;
}

export function formatEgressDomains(domains?: string[] | null): string {
  if (!Array.isArray(domains) || domains.length === 0) {
    return "";
  }
  return domains.join(", ");
}

export interface HelperSdkReference {
  title: string;
  subtitle: string;
  snippet: string;
}

export function getHelperSdkSnippet(runtime: string): HelperSdkReference {
  switch (runtime.toLowerCase()) {
    case "python":
      return {
        title: "Python Built-In Helper (import elder)",
        subtitle:
          "Pre-injected elder module wired to ELDER_GATEWAY_URL and ELDER_GATEWAY_TOKEN.",
        snippet: `import elder

def handler(input_data: dict) -> dict:
    # Requires: entities:write
    elder.update_entity_metadata(
        entity_id=input_data["entity_id"],
        metadata={"owner": "platform-sec", "compliance": "verified"},
    )
    # Requires: integrations:sync (e.g. FleetDM)
    sync = elder.trigger_integration_sync("fleetdm")
    return {"ok": True, "sync_id": sync.get("id")}`,
      };
    case "nodejs":
      return {
        title: "Node.js Built-In Helper (./elder.mjs)",
        subtitle:
          "Pre-injected ESM helper wired to ELDER_GATEWAY_URL and ELDER_GATEWAY_TOKEN.",
        snippet: `import elder from './elder.mjs';

export async function handler(input) {
  // Requires: entities:write
  await elder.update_entity_metadata(input.entity_id, {
    owner: 'platform-sec',
    compliance: 'verified',
  });
  // Requires: integrations:sync (e.g. FleetDM)
  const sync = await elder.trigger_integration_sync('fleetdm');
  return { ok: true, sync_id: sync.id };
}`,
      };
    case "ruby":
      return {
        title: "Ruby Built-In Helper (require_relative 'elder')",
        subtitle:
          "Pre-injected Ruby helper wired to ELDER_GATEWAY_URL and ELDER_GATEWAY_TOKEN.",
        snippet: `require_relative 'elder'

def handler(input)
  # Requires: entities:write
  elder.update_entity_metadata(
    input['entity_id'],
    { owner: 'platform-sec', compliance: 'verified' }
  )
  # Requires: integrations:sync (e.g. FleetDM)
  sync = elder.trigger_integration_sync('fleetdm')
  { ok: true, sync_id: sync['id'] }
end`,
      };
    case "golang":
      return {
        title: "Go Sandbox Gateway (ELDER_GATEWAY_URL + ELDER_GATEWAY_TOKEN)",
        subtitle:
          "Authenticate HTTP calls to the local Elder gateway using injected env vars.",
        snippet: `gatewayURL := os.Getenv("ELDER_GATEWAY_URL")
token := os.Getenv("ELDER_GATEWAY_TOKEN")

// Requires: entities:write (equivalent to elder.update_entity_metadata)
patchReq, _ := http.NewRequest("PATCH", gatewayURL+"/entities/"+entityID+"/metadata", body)
patchReq.Header.Set("Authorization", "Bearer "+token)

// Requires: integrations:sync (equivalent to elder.trigger_integration_sync("fleetdm"))
syncReq, _ := http.NewRequest("POST", gatewayURL+"/integrations/fleetdm/sync", nil)
syncReq.Header.Set("Authorization", "Bearer "+token)`,
      };
    case "rust":
      return {
        title: "Rust Sandbox Gateway (ELDER_GATEWAY_URL + ELDER_GATEWAY_TOKEN)",
        subtitle:
          "Authenticate HTTP calls to the local Elder gateway using injected env vars.",
        snippet: `let gateway_url = std::env::var("ELDER_GATEWAY_URL")?;
let token = std::env::var("ELDER_GATEWAY_TOKEN")?;

// Requires: entities:write (equivalent to elder.update_entity_metadata)
// PATCH {gateway_url}/entities/{entity_id}/metadata
// Authorization: Bearer {token}

// Requires: integrations:sync (equivalent to elder.trigger_integration_sync("fleetdm"))
// POST {gateway_url}/integrations/fleetdm/sync
// Authorization: Bearer {token}`,
      };
    default:
      return {
        title: "Sandbox Gateway (ELDER_GATEWAY_URL + ELDER_GATEWAY_TOKEN)",
        subtitle:
          "Authenticate HTTP calls to the local Elder gateway using injected env vars.",
        snippet: `# Use ELDER_GATEWAY_URL and Authorization: Bearer $ELDER_GATEWAY_TOKEN
# PATCH $ELDER_GATEWAY_URL/entities/<entity_id>/metadata
# POST  $ELDER_GATEWAY_URL/integrations/fleetdm/sync`,
      };
  }
}
