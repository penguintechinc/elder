# Security (App-Specific Addendums)

## Elder-Specific Dependency Constraints

### protobuf / grpcio — CANNOT upgrade to 6.x (Tracked: Dependabot alert #58)

**Constraint**: `google-cloud-iam`, `google-cloud-resource-manager`, and `google-cloud-secret-manager`
all require `protobuf<6.0.0dev`. The latest versions of these packages (as of 2026-02)
still do NOT support protobuf 6.x.

**Blocker**: `grpcio-tools>=1.70.0` requires `protobuf>=6.31.1`. These two requirements are
mutually exclusive — there is no version of grpcio/protobuf that satisfies both.

**Current pin**: `grpcio==1.69.0`, `grpcio-tools==1.69.0`, `protobuf==5.29.5`

**DO NOT attempt to upgrade grpcio past 1.69.0 or protobuf past 5.x** until the
google-cloud-* packages ship support for protobuf 6.x. Attempting the upgrade will cause
`ERROR: pip's dependency resolver...` failures in the API Docker build.

**CVE-2026-0994** (protobuf 6.x fix) cannot be applied until then. The Dependabot alert
has been dismissed with a note explaining this constraint.

**Action when google-cloud packages release 6.x support**:
1. Bump `grpcio`, `grpcio-tools`, `grpcio-reflection` to latest
2. Bump `protobuf` to latest 6.x
3. Remove the blocking comment in `requirements.txt`
4. Close Dependabot alert #58
