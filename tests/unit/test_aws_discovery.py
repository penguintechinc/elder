"""Unit tests for the AWS discovery client.

boto3 is stubbed by conftest_worker_stubs, so these tests drive the client with
MagicMock clients rather than touching AWS. They cover the behaviours that
previously broke silently: terminated-instance filtering, pagination, the
service filter, and error handling that used to swallow failures.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from apps.worker.discovery.aws_discovery import AWSDiscoveryClient

BASE_CONFIG = {
    "provider_type": "aws",
    "region": "us-east-2",
    "access_key_id": "AKIAEXAMPLE",
    "secret_access_key": "secret",  # nosec B105 - dummy value for tests
}


def make_client(**overrides):
    """Build an AWSDiscoveryClient with a mocked boto3 session."""
    config = {**BASE_CONFIG, **overrides}
    with patch("apps.worker.discovery.aws_discovery.boto3") as mock_boto3:
        mock_boto3.Session.return_value = MagicMock()
        client = AWSDiscoveryClient(config)
    return client


def paginated(client_mock, pages):
    """Wire a boto3 client mock so any get_paginator(...) yields `pages`."""
    paginator = MagicMock()
    paginator.paginate.return_value = iter(pages)
    client_mock.get_paginator.return_value = paginator
    return paginator


class TestAuthResolution:
    def test_static_credentials_take_priority(self):
        client = make_client()
        assert client.auth_method == AWSDiscoveryClient.AUTH_STATIC_CREDENTIALS

    def test_falls_back_to_environment_without_static_keys(self):
        client = make_client(access_key_id="", secret_access_key="")
        assert client.auth_method == AWSDiscoveryClient.AUTH_ENVIRONMENT

    def test_region_defaults_are_respected(self):
        assert make_client(region="eu-west-1").region == "eu-west-1"


class TestTerminatedInstances:
    """Terminated instances linger in describe_instances and are not assets."""

    def test_paginate_filters_to_live_states(self):
        client = make_client()
        ec2 = MagicMock()
        paginator = paginated(ec2, [{"Reservations": []}])
        client.session.client.return_value = ec2

        client.discover_compute()

        kwargs = paginator.paginate.call_args.kwargs
        filters = kwargs["Filters"]
        states = filters[0]["Values"]
        assert filters[0]["Name"] == "instance-state-name"
        assert "terminated" not in states
        assert "running" in states

    def test_running_instance_is_returned(self):
        client = make_client()
        ec2 = MagicMock()
        paginated(
            ec2,
            [
                {
                    "Reservations": [
                        {
                            "Instances": [
                                {
                                    "InstanceId": "i-abc",
                                    "InstanceType": "t3.micro",
                                    "State": {"Name": "running"},
                                    "Tags": [{"Key": "Name", "Value": "web"}],
                                }
                            ]
                        }
                    ]
                }
            ],
        )
        client.session.client.return_value = ec2

        resources = client.discover_compute()

        assert len(resources) == 1
        assert resources[0]["resource_id"] == "i-abc"
        assert resources[0]["name"] == "web"
        assert resources[0]["resource_type"] == "ec2_instance"


class TestPagination:
    """Results used to truncate at the first page for several services."""

    def test_instances_across_pages_are_all_returned(self):
        client = make_client()
        ec2 = MagicMock()

        def instance(i):
            return {
                "InstanceId": f"i-{i}",
                "State": {"Name": "running"},
                "Tags": [],
            }

        paginated(
            ec2,
            [
                {"Reservations": [{"Instances": [instance(1)]}]},
                {"Reservations": [{"Instances": [instance(2)]}]},
                {"Reservations": [{"Instances": [instance(3)]}]},
            ],
        )
        client.session.client.return_value = ec2

        resources = client.discover_compute()

        assert [r["resource_id"] for r in resources] == ["i-1", "i-2", "i-3"]

    def test_untagged_instances_get_distinct_names(self):
        client = make_client()
        ec2 = MagicMock()
        paginated(
            ec2,
            [
                {
                    "Reservations": [
                        {
                            "Instances": [
                                {"InstanceId": "i-1", "State": {"Name": "running"}},
                                {"InstanceId": "i-2", "State": {"Name": "running"}},
                            ]
                        }
                    ]
                }
            ],
        )
        client.session.client.return_value = ec2

        names = {r["name"] for r in client.discover_compute()}

        # regression: both would have been "Unnamed" and collapsed downstream
        assert names == {"i-1", "i-2"}


class TestServiceFilter:
    def test_empty_filter_means_discover_everything(self):
        client = make_client()
        assert client.services == []

    def test_explicit_filter_is_preserved(self):
        client = make_client(services=["ec2", "s3"])
        assert client.services == ["ec2", "s3"]

    @pytest.mark.parametrize(
        "service",
        [
            "dynamodb",
            "sqs",
            "sns",
            "ecr",
            "logs",
            "stepfunctions",
            "events",
            "apigateway",
            "efs",
            "elasticache",
            "cloudfront",
            "route53",
        ],
    )
    def test_free_tier_services_are_supported(self, service):
        assert service in make_client().get_supported_services()


class TestErrorHandling:
    def test_aws_error_degrades_to_empty_list(self):
        """A denied permission must not abort the whole scan."""
        client = make_client()
        client.session.client.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "not authorized"}},
            "DescribeInstances",
        )

        assert client.discover_compute() == []

    def test_aws_error_is_logged_not_swallowed(self, caplog):
        """Regression: failures used to `pass` silently, so a permissions
        problem looked identical to an account with no resources."""
        client = make_client()
        client.session.client.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "not authorized"}},
            "DescribeInstances",
        )

        with caplog.at_level(logging.WARNING):
            client.discover_compute()

        assert any("EC2" in r.message or "ec2" in r.message for r in caplog.records)

    def test_unexpected_error_is_not_masked(self):
        """Only AWS errors are handled; a genuine bug must still surface."""
        client = make_client()
        client.session.client.side_effect = TypeError("programming error")

        with pytest.raises(TypeError):
            client.discover_compute()
