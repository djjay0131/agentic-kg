"""Structural tests for the experiment execution isolation (ADR-0008).

These parse the committed Terraform and Docker files; they do not call GCP.
The assertions pin the isolation properties the runbook promises: a dedicated
VPC with ALL_TRAFFIC egress and no path to Neo4j, an egress deny for the
internet with only the Private Google APIs VIP allowed, prefix-scoped GCS IAM,
and a non-root minimal image.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MAIN_TF = _REPO_ROOT / "infra" / "main.tf"
_STAGING_TFVARS = _REPO_ROOT / "infra" / "envs" / "staging.tfvars"
_DOCKERFILE = _REPO_ROOT / "docker" / "Dockerfile.experiment"
_BUILD_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "build-images.yml"


@pytest.fixture(scope="module")
def main_tf() -> str:
    return _MAIN_TF.read_text(encoding="utf-8")


class TestExperimentTerraform:
    def test_staging_uses_cloudrun_job_and_named_bucket(self):
        staging = _STAGING_TFVARS.read_text(encoding="utf-8")
        assert 'experiment_backend     = "cloudrun_job"' in staging
        assert 'experiment_bucket      = "vt-gcp-00042-agentic-kg-experiments"' in staging

    def test_job_uses_all_traffic_on_dedicated_network(self, main_tf):
        block = main_tf.split('resource "google_cloud_run_v2_job" "experiment"', 1)[1]
        block = block.split("# The API may run this one Job", 1)[0]
        assert 'egress = "ALL_TRAFFIC"' in block
        assert "google_compute_network.experiments[0]" in block
        assert "google_compute_subnetwork.experiments[0]" in block
        assert "var.network" not in block
        assert "NEO4J" not in block

    def test_internet_is_denied_and_only_private_google_apis_allowed(self, main_tf):
        deny = main_tf.split(
            'resource "google_compute_firewall" "experiment_deny_internet"', 1
        )[1].split("}\n\nresource", 1)[0]
        assert 'direction = "EGRESS"' in deny
        assert 'destination_ranges = ["0.0.0.0/0"]' in deny
        assert "deny {" in deny

        allow = main_tf.split(
            'resource "google_compute_firewall" "experiment_allow_google_apis"', 1
        )[1].split("}\n\nresource", 1)[0]
        assert "199.36.153.4/30" in allow
        assert "199.36.153.8/30" in allow

    def test_bucket_iam_is_prefix_scoped(self, main_tf):
        for resource in (
            'resource "google_storage_bucket_iam_member" "experiment_objects"',
            'resource "google_storage_bucket_iam_member" "experiment_api_objects"',
        ):
            block = main_tf.split(resource, 1)[1].split("}\n\n", 1)[0]
            assert "roles/storage.objectViewer" in block
            assert "roles/storage.objectCreator" in block
            assert "experiment-prefix-only" in block
            assert "resource.name.startsWith" in block

    def test_job_is_invoked_with_job_scoped_roles(self, main_tf):
        assert (
            'role     = "roles/run.jobsExecutorWithOverrides"' in main_tf
        )
        assert 'role     = "roles/run.invoker"' in main_tf
        assert (
            'resource "google_cloud_run_v2_job_iam_member" "api_can_run_experiment"'
            in main_tf
        )

    def test_experiment_sa_has_no_project_level_iam(self, main_tf):
        for block in main_tf.split('resource "google_project_iam_member"')[1:]:
            block = block.split("\n}\n", 1)[0]
            assert "google_service_account.experiment" not in block

    def test_network_user_is_subnet_scoped(self, main_tf):
        block = main_tf.split(
            'resource "google_compute_subnetwork_iam_member" "experiment_network_user"',
            1,
        )[1].split("\n}\n", 1)[0]
        assert 'role       = "roles/compute.networkUser"' in block
        assert "google_compute_subnetwork.experiments[0].id" in block


class TestExperimentImage:
    def test_image_is_minimal_and_non_root(self):
        dockerfile = _DOCKERFILE.read_text(encoding="utf-8")
        assert "USER experiment" in dockerfile
        assert "numpy" in dockerfile and "scikit-learn" in dockerfile
        assert "agentic_kg" not in dockerfile.replace(
            "agentic_kg/agents/experiment_runner.py", ""
        )
        instructions = "\n".join(
            line for line in dockerfile.splitlines() if not line.lstrip().startswith("#")
        )
        assert "neo4j" not in instructions.lower()

    def test_build_workflow_builds_it(self):
        workflow = _BUILD_WORKFLOW.read_text(encoding="utf-8")
        assert "docker/Dockerfile.experiment" in workflow
        assert "experiment_image" in workflow
