"""
Elder gRPC Client Example (Python)

This example demonstrates how to use the Elder gRPC API from Python.

Installation:
    pip install grpcio grpcio-tools protobuf

Usage:
    python python_client_example.py
"""

import grpc

from apps.api.grpc.generated import (
    common_pb2,
    elder_pb2,
    elder_pb2_grpc,
    entity_pb2,
    organization_pb2,
)


def run_examples():
    """Run gRPC client examples."""

    # Create gRPC channel
    # For production, use secure channel with TLS:
    # credentials = grpc.ssl_channel_credentials()
    # channel = grpc.secure_channel('grpc.example.com:443', credentials)

    channel = grpc.insecure_channel("localhost:50051")

    # Create stub (client)
    stub = elder_pb2_grpc.ElderServiceStub(channel)

    print("=" * 70)
    print("Elder gRPC Client Examples")
    print("=" * 70)

    # Example 1: Health Check
    print("\n1. Health Check")
    print("-" * 70)
    try:
        response = stub.HealthCheck(common_pb2.Empty())
        print(f"Success: {response.success}")
        print(f"Message: {response.message}")
        print(f"Details: {dict(response.details)}")
    except grpc.RpcError as e:
        print(f"Error: {e.code()} - {e.details()}")

    # Example 2: List Organizations
    print("\n2. List Organizations")
    print("-" * 70)
    try:
        request = organization_pb2.ListOrganizationsRequest(
            pagination=common_pb2.PaginationRequest(page=1, per_page=10)
        )
        response = stub.ListOrganizations(request)
        print(f"Total: {response.pagination.total}")
        print(f"Pages: {response.pagination.pages}")
        print(f"Organizations:")
        for org in response.organizations:
            print(f"  - {org.id}: {org.name}")
    except grpc.RpcError as e:
        print(f"Error: {e.code()} - {e.details()}")

    # Example 3: Create Organization
    print("\n3. Create Organization")
    print("-" * 70)
    try:
        request = organization_pb2.CreateOrganizationRequest(
            name="Engineering Department",
            description="Engineering team organization",
            metadata={"team_size": "50", "location": "Building A"},
        )
        response = stub.CreateOrganization(request)
        print(f"Created Organization:")
        print(f"  ID: {response.organization.id}")
        print(f"  Name: {response.organization.name}")
        print(f"  Description: {response.organization.description}")

        # Save ID for next examples
        org_id = response.organization.id
    except grpc.RpcError as e:
        print(f"Error: {e.code()} - {e.details()}")
        org_id = None

    # Example 4: Get Organization
    if org_id:
        print("\n4. Get Organization")
        print("-" * 70)
        try:
            request = organization_pb2.GetOrganizationRequest(id=org_id)
            response = stub.GetOrganization(request)
            print(f"Organization:")
            print(f"  ID: {response.organization.id}")
            print(f"  Name: {response.organization.name}")
            print(f"  Created: {response.organization.created_at.seconds}")
        except grpc.RpcError as e:
            print(f"Error: {e.code()} - {e.details()}")

    # Example 5: Update Organization
    if org_id:
        print("\n5. Update Organization")
        print("-" * 70)
        try:
            request = organization_pb2.UpdateOrganizationRequest(
                id=org_id,
                description="Updated engineering team organization",
                metadata={"team_size": "60", "location": "Building B"},
            )
            response = stub.UpdateOrganization(request)
            print(f"Updated Organization:")
            print(f"  Description: {response.organization.description}")
            print(
                f"  Metadata: {[f'{m.key}={m.value}' for m in response.organization.metadata]}"
            )
        except grpc.RpcError as e:
            print(f"Error: {e.code()} - {e.details()}")

    # Example 6: List Entities
    print("\n6. List Entities")
    print("-" * 70)
    try:
        request = entity_pb2.ListEntitiesRequest(
            pagination=common_pb2.PaginationRequest(page=1, per_page=10),
            entity_type=entity_pb2.EntityType.COMPUTE,
        )
        response = stub.ListEntities(request)
        print(f"Total Compute Entities: {response.pagination.total}")
        for entity in response.entities:
            print(f"  - {entity.id}: {entity.name} ({entity.entity_type})")
    except grpc.RpcError as e:
        print(f"Error: {e.code()} - {e.details()}")

    # Example 7: Create Entity
    if org_id:
        print("\n7. Create Entity")
        print("-" * 70)
        try:
            request = entity_pb2.CreateEntityRequest(
                organization_id=org_id,
                entity_type=entity_pb2.EntityType.COMPUTE,
                name="web-server-01",
                description="Production web server",
                metadata={
                    "ip_address": "10.0.1.100",
                    "cpu_cores": "8",
                    "memory_gb": "32",
                },
            )
            response = stub.CreateEntity(request)
            print(f"Created Entity:")
            print(f"  ID: {response.entity.id}")
            print(f"  Unique ID: {response.entity.unique_id}")
            print(f"  Name: {response.entity.name}")
            print(f"  Type: {response.entity.entity_type}")
        except grpc.RpcError as e:
            print(f"Error: {e.code()} - {e.details()}")

    # Example 8: Delete Organization (cleanup)
    if org_id:
        print("\n8. Delete Organization (Cleanup)")
        print("-" * 70)
        try:
            request = organization_pb2.DeleteOrganizationRequest(id=org_id)
            response = stub.DeleteOrganization(request)
            print(f"Success: {response.status.success}")
            print(f"Message: {response.status.message}")
        except grpc.RpcError as e:
            print(f"Error: {e.code()} - {e.details()}")

    # Close channel
    channel.close()
    print("\n" + "=" * 70)
    print("Examples completed!")
    print("=" * 70)


if __name__ == "__main__":
    run_examples()
