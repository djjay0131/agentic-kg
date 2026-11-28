"""The deploy diagnosis must print the experiment Job definition (#171).

The formatter is a real script (not an inline gcloud ``--format``), so it can
be run against a recorded JSON fixture. The fixture is the gcloud **v1** job
shape (``spec.template.spec``), which is what ``gcloud run jobs describe``
emits; the v2 API/Terraform shape (``template.template``) is covered too so a
gcloud change cannot silently print blanks again.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "format_run_job.py"
_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "gcloud_run_job_experiment_staging.json"
_DEPLOY_MASTER = _REPO_ROOT / ".github" / "workflows" / "deploy-master.yml"
_GCP_DIAGNOSE = _REPO_ROOT / ".github" / "workflows" / "gcp-diagnose.yml"


def _run_formatter(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        check=False,
    )


def test_formatter_renders_the_recorded_v1_job_fixture():
    result = _run_formatter(str(_FIXTURE))

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    output = {line.split(":", 1)[0]: line.split(":", 1)[1].strip() for line in lines}

    assert "experiment@sha256:6a3788ae" in output["image"]
    assert output["command"] == ""
    assert output["args"] == ""
    assert output["timeout"] == "300"
    assert output["maxRetries"] == "0"
    assert (
        output["serviceAccount"]
        == "agentic-kg-experiment-staging@vt-gcp-00042.iam.gserviceaccount.com"
    )
    assert "networkInterfaces" in output["vpcAccess"]
    assert "2Gi" in output["resources"]
    assert output["envNames"] == (
        "EXPERIMENT_BUCKET, EXPERIMENT_GCS_PREFIX, EXPERIMENT_GCS_ENDPOINT"
    )


def test_formatter_field_mode_prints_one_value():
    result = _run_formatter(str(_FIXTURE), "--field", "serviceAccount")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == (
        "agentic-kg-experiment-staging@vt-gcp-00042.iam.gserviceaccount.com"
    )


def test_the_recorded_fixture_is_the_real_two_level_v1_job_shape():
    """A Job nests the task template under spec.template.spec.template.spec.

    #172's fixture used the *Execution* shape (spec.template.spec.containers),
    which is why the formatter passed in tests and printed blanks against the
    real Job.
    """
    job = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    containers = job["spec"]["template"]["spec"]["template"]["spec"]["containers"]
    assert containers[0]["image"].startswith("us-central1-docker.pkg.dev/")

    result = _run_formatter(str(_FIXTURE))
    assert result.returncode == 0, result.stderr
    output = {
        line.split(":", 1)[0]: line.split(":", 1)[1].strip()
        for line in result.stdout.splitlines()
    }
    assert output["image"].startswith("us-central1-docker.pkg.dev/")
    assert output["serviceAccount"].endswith(".iam.gserviceaccount.com")
    assert "networkInterfaces" in output["vpcAccess"]
    assert output["timeout"] == "300"
    assert output["maxRetries"] == "0"


def test_formatter_still_accepts_a_v1_execution_or_service_shape(tmp_path):
    job = {
        "apiVersion": "run.googleapis.com/v1",
        "kind": "Execution",
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "image": "example/execution:v1",
                            "resources": {"limits": {"memory": "1Gi"}},
                        }
                    ],
                    "serviceAccountName": "execution@example.iam.gserviceaccount.com",
                    "timeoutSeconds": "60",
                }
            }
        },
    }
    path = tmp_path / "execution.json"
    path.write_text(json.dumps(job), encoding="utf-8")

    result = _run_formatter(str(path))

    assert result.returncode == 0, result.stderr
    assert "image: example/execution:v1" in result.stdout
    assert (
        "serviceAccount: execution@example.iam.gserviceaccount.com"
        in result.stdout
    )
    assert "timeout: 60" in result.stdout


def test_formatter_also_accepts_the_v2_api_shape(tmp_path):
    job = {
        "template": {
            "template": {
                "containers": [
                    {
                        "image": "example/experiment:v2",
                        "command": ["python", "/app/experiment_runner.py"],
                        "args": ["gs://b/p/script.py"],
                        "env": [{"name": "EXPERIMENT_GCS_PREFIX"}],
                    }
                ],
                "timeout": "300s",
                "maxRetries": 0,
                "serviceAccount": "v2@example.iam.gserviceaccount.com",
                "vpcAccess": {"networkInterfaces": [{"network": "n"}]},
            }
        }
    }
    path = tmp_path / "job.json"
    path.write_text(json.dumps(job), encoding="utf-8")

    result = _run_formatter(str(path))

    assert result.returncode == 0, result.stderr
    assert "image: example/experiment:v2" in result.stdout
    assert "command: python /app/experiment_runner.py" in result.stdout
    assert "args: gs://b/p/script.py" in result.stdout
    assert "timeout: 300s" in result.stdout
    assert "serviceAccount: v2@example.iam.gserviceaccount.com" in result.stdout


def test_formatter_rejects_non_json():
    result = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        input="not json",
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        check=False,
    )
    assert result.returncode == 2
    assert "could not read job JSON" in result.stderr


def test_deploy_and_diagnose_use_the_tested_formatter():
    deploy = _DEPLOY_MASTER.read_text(encoding="utf-8")
    diagnose = _GCP_DIAGNOSE.read_text(encoding="utf-8")

    for workflow in (deploy, diagnose):
        assert "scripts/format_run_job.py" in workflow
        # The broken paths are gone for good.
        assert "template.template.containers" not in workflow
        assert "spec.template.template" not in workflow

    # The service-account email is printed by the formatter (defect A hinges
    # on the experiment Job's identity).
    assert "serviceAccount" in _SCRIPT.read_text(encoding="utf-8")
    # gcp-diagnose must check out the repo before it can run the script.
    assert "actions/checkout" in diagnose
    # The deploy prints the image it rolled, from the same formatter.
    assert "--field image" in deploy
