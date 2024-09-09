#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

from pathlib import Path
from pytest_operator.plugin import OpsTest
from ..helpers import get_application_relation_data, get_secret_data
from tenacity import RetryError, Retrying, stop_after_attempt, wait_exponential
from datetime import datetime
from typing import Optional, Dict
import json
import ops
import logging
from ops.model import Unit
from ..client_relations.helpers import get_external_uri
from ..helpers import get_mongos_uri

logger = logging.getLogger(__name__)


EXTERNAL_PEM_PATH = "/etc/mongod/external-cert.pem"
EXTERNAL_CERT_PATH = "/etc/mongod/external-ca.crt"
INTERNAL_CERT_PATH = "/etc/mongod/internal-ca.crt"
MONGO_SHELL = "/usr/bin/mongosh"
MONGOS_APP_NAME = "mongos-k8s"
CERT_REL_NAME = "certificates"
CERTS_APP_NAME = "self-signed-certificates"

CONFIG_SERVER_APP_NAME = "config-server"
SHARD_APP_NAME = "shard0"
CLUSTER_COMPONENTS = [CONFIG_SERVER_APP_NAME, SHARD_APP_NAME]
MONGOS_SERVICE = "mongos.service"


class ProcessError(Exception):
    """Raised when a process fails."""


async def deploy_tls(ops_test: OpsTest) -> None:
    """Deploys the self-signed certificate operator."""
    await ops_test.model.deploy(CERTS_APP_NAME, channel="edge")

    await ops_test.model.wait_for_idle(
        apps=[CERTS_APP_NAME],
        idle_period=20,
        raise_on_blocked=False,
        raise_on_error=False,
    )


async def integrate_mongos_with_tls(ops_test: OpsTest) -> None:
    """Integrate mongos to the TLS interface."""
    await ops_test.model.integrate(
        f"{MONGOS_APP_NAME}:{CERT_REL_NAME}",
        f"{CERTS_APP_NAME}:{CERT_REL_NAME}",
    )


async def integrate_cluster_with_tls(ops_test: OpsTest) -> None:
    """Integrate cluster components to the TLS interface."""
    for cluster_component in CLUSTER_COMPONENTS:
        await ops_test.model.integrate(
            f"{cluster_component}:{CERT_REL_NAME}",
            f"{CERTS_APP_NAME}:{CERT_REL_NAME}",
        )

    await ops_test.model.wait_for_idle(
        apps=CLUSTER_COMPONENTS,
        idle_period=20,
        raise_on_blocked=False,
        status="active",
    )


async def check_mongos_tls_disabled(ops_test: OpsTest) -> None:
    # check mongos is running with TLS enabled
    for unit in ops_test.model.applications[MONGOS_APP_NAME].units:
        await check_tls(ops_test, unit, enabled=False)


async def check_mongos_tls_enabled(ops_test: OpsTest, internal=True) -> None:
    # check each replica set is running with TLS enabled
    for unit in ops_test.model.applications[MONGOS_APP_NAME].units:
        await check_tls(ops_test, unit, enabled=True, internal=internal)


async def toggle_tls_mongos(
    ops_test: OpsTest, enable: bool, certs_app_name: str = CERTS_APP_NAME
) -> None:
    """Toggles TLS on mongos application to the specified enabled state."""
    if enable:
        await ops_test.model.integrate(
            f"{MONGOS_APP_NAME}:{CERT_REL_NAME}",
            f"{certs_app_name}:{CERT_REL_NAME}",
        )
    else:
        await ops_test.model.applications[MONGOS_APP_NAME].remove_relation(
            f"{MONGOS_APP_NAME}:{CERT_REL_NAME}",
            f"{certs_app_name}:{CERT_REL_NAME}",
        )

    await ops_test.model.wait_for_idle(apps=[MONGOS_APP_NAME], idle_period=30)


async def mongos_tls_command(ops_test: OpsTest, unit, internal=True) -> str:
    """Generates a command which verifies TLS status."""
    unit_id = unit.name.split("/")[1]
    if not internal:
        client_uri = await get_external_uri(ops_test, unit_id=unit_id)
    else:
        client_uri = await get_mongos_uri(ops_test, unit_id=unit_id)
        # secret data not added properly, wait for DPE-5215 to merge
        # secret_uri = await get_application_relation_data(
        #     ops_test, MONGOS_APP_NAME, "mongos", "secret-user"
        # )

        # secret_data = await get_secret_data(ops_test, secret_uri)
        # client_uri = secret_data.get("uris")

    return (
        f"{MONGO_SHELL} '{client_uri}'  --eval 'db.getUsers()'"
        f" --tls --tlsCAFile {EXTERNAL_CERT_PATH}"
        f" --tlsCertificateKeyFile {EXTERNAL_PEM_PATH}"
    )


async def check_tls(ops_test, unit, enabled, internal=True) -> None:
    """Returns True if TLS matches the expected state "enabled"."""
    try:
        mongos_tls_check = await mongos_tls_command(ops_test, unit=unit, internal=internal)
        print(mongos_tls_check)
        complete_command = f"ssh --container mongos {unit.name} {mongos_tls_check}"
        return_code, _, stderr = await ops_test.juju(*complete_command.split())

        tls_enabled = return_code == 0
        if enabled != tls_enabled:
            logger.error(stderr)
            raise ValueError(f"TLS is{' not' if not tls_enabled else ''} enabled on {unit.name}")
        return True
    except RetryError:
        return False


async def get_sans_ips(ops_test: OpsTest, unit: Unit, internal: bool) -> str:
    """Retrieves the sans for the for mongos on the provided unit."""
    cert_name = "internal" if internal else "external"
    get_sans_cmd = f"openssl x509 -noout -ext subjectAltName -in /etc/mongod/{cert_name}-cert.pem"
    complete_command = f"ssh --container mongos {unit.name} {get_sans_cmd}"
    _, result, _ = await ops_test.juju(*complete_command.split())
    return result


async def time_file_created(ops_test: OpsTest, unit_name: str, path: str) -> int:
    """Returns the unix timestamp of when a file was created on a specified unit."""
    time_cmd = f"ls -l --time-style=full-iso {path} "
    complete_command = f"ssh --container mongos {unit_name} {time_cmd}"
    return_code, ls_output, stderr = await ops_test.juju(*complete_command.split())

    if return_code != 0:
        logger.error(stderr)
        raise ProcessError(
            "Expected time command %s to succeed instead it failed: %s; %s",
            time_cmd,
            return_code,
            stderr,
        )

    return process_ls_time(ls_output)


async def time_process_started(ops_test: OpsTest, unit_name: str, process_name: str) -> int:
    """Retrieves the time that a given process started according to systemd."""
    logs = await run_command_on_unit(ops_test, unit_name, "/charm/bin/pebble changes")

    # find most recent start time. By parsing most recent logs (ie in reverse order)
    for log in reversed(logs.split("\n")):
        if "Replan" in log:
            return process_pebble_time(log.split()[4])

    raise Exception("Service was never started")


async def run_command_on_unit(ops_test: OpsTest, unit_name: str, command: str) -> str:
    """Run a command on a specific unit.

    Args:
        ops_test: The ops test framework instance
        unit_name: The name of the unit to run the command on
        command: The command to run

    Returns:
        the command output if it succeeds, otherwise raises an exception.
    """
    complete_command = f"ssh --container mongos {unit_name} {command}"
    return_code, stdout, _ = await ops_test.juju(*complete_command.split())
    if return_code != 0:
        raise Exception(
            "Expected command %s to succeed instead it failed: %s", command, return_code
        )
    return stdout


def process_pebble_time(changes_output):
    """Parse time representation as returned by the 'pebble changes' command."""
    return datetime.strptime(changes_output, "%H:%M")


async def check_certs_correctly_distributed(
    ops_test: OpsTest,
    unit: ops.Unit,
    tmpdir: Path,
    app_name: str | None = None,
) -> None:
    """Comparing expected vs distributed certificates.

    Verifying certificates downloaded on the charm against the ones distributed by the TLS operator
    """
    app_name = app_name
    unit_secret_id = await get_secret_id(ops_test, unit.name)
    unit_secret_content = await get_secret_content(ops_test, unit_secret_id)

    # Get the values for certs from the relation, as provided by TLS Charm
    certificates_raw_data = await get_application_relation_data(
        ops_test, app_name, CERT_REL_NAME, "certificates"
    )
    certificates_data = json.loads(certificates_raw_data)

    # compare the TLS resources stored on the disk of the unit with the ones from the TLS relation
    for cert_type, cert_path in [
        ("int", INTERNAL_CERT_PATH),
        ("ext", EXTERNAL_CERT_PATH),
    ]:
        unit_csr = unit_secret_content[f"{cert_type}-csr-secret"]
        tls_item = [
            data
            for data in certificates_data
            if data["certificate_signing_request"].rstrip() == unit_csr.rstrip()
        ][0]

        # Read the content of the cert file stored in the unit
        cert_file_content = await get_file_content(ops_test, unit.name, cert_path, tmpdir)

        # Get the external cert value from the relation
        relation_cert = "\n".join(tls_item["chain"]).strip()

        # confirm that they match
        assert (
            relation_cert == cert_file_content
        ), f"Relation Content for {cert_type}-cert:\n{relation_cert}\nFile Content:\n{cert_file_content}\nMismatch."


async def scp_file_preserve_ctime(
    ops_test: OpsTest, unit_name: str, path: str, tmpdir: Path
) -> int:
    """Returns the unix timestamp of when a file was created on a specified unit."""
    # Retrieving the file
    filename = tmpdir / Path(path.split("/")[-1])
    complete_command = f"scp --container mongos {unit_name}:{path} {filename}"
    return_code, _, stderr = await ops_test.juju(*complete_command.split())

    if return_code != 0:
        logger.error(stderr)
        raise ProcessError(
            "Expected command %s to succeed instead it failed: %s; %s",
            complete_command,
            return_code,
            stderr,
        )

    return f"{filename}"


async def get_file_content(ops_test: OpsTest, unit_name: str, path: str, tmpdir: Path) -> str:
    filename = await scp_file_preserve_ctime(ops_test, unit_name, path, tmpdir)

    with open(filename, mode="r") as fd:
        return fd.read()


def process_ls_time(ls_output):
    """Parse time representation as returned by the 'ls' command."""
    time_as_str = "T".join(ls_output.split("\n")[0].split(" ")[5:7])
    # further strip down additional milliseconds
    time_as_str = time_as_str[0:-3]
    d = datetime.strptime(time_as_str, "%Y-%m-%dT%H:%M:%S.%f")
    return d


def process_systemctl_time(systemctl_output):
    """Parse time representation as returned by the 'systemctl' command."""
    "ActiveEnterTimestamp=Thu 2022-09-22 10:00:00 UTC"
    time_as_str = "T".join(systemctl_output.split("=")[1].split(" ")[1:3])
    d = datetime.strptime(time_as_str, "%Y-%m-%dT%H:%M:%S")
    return d


async def get_secret_id(ops_test, app_or_unit: Optional[str] = None) -> str:
    """Retrieve secret ID for an app or unit."""
    complete_command = "list-secrets"

    if app_or_unit:
        prefix = "unit" if app_or_unit[-1].isdigit() else "application"
        formated_app_or_unit = f"{prefix}-{app_or_unit}"
        if prefix == "unit":
            formated_app_or_unit = formated_app_or_unit.replace("/", "-")
        complete_command += f" --owner {formated_app_or_unit}"

    _, stdout, _ = await ops_test.juju(*complete_command.split())
    output_lines_split = [line.split() for line in stdout.strip().split("\n")]
    if app_or_unit:
        return [line[0] for line in output_lines_split if app_or_unit in line][0]

    return output_lines_split[1][0]


async def get_secret_content(ops_test, secret_id) -> Dict[str, str]:
    """Retrieve contents of a Juju Secret."""
    secret_id = secret_id.split("/")[-1]
    complete_command = f"show-secret {secret_id} --reveal --format=json"
    _, stdout, _ = await ops_test.juju(*complete_command.split())
    data = json.loads(stdout)
    return data[secret_id]["content"]["Data"]


async def get_file_contents(ops_test: OpsTest, unit: str, filepath: str) -> str:
    """Returns the contents of the provided filepath."""
    mv_cmd = f"exec --unit {unit.name} sudo cat {filepath} "
    _, stdout, _ = await ops_test.juju(*mv_cmd.split())
    return stdout


async def rotate_and_verify_certs(ops_test: OpsTest, app: str, tmpdir: Path) -> None:
    """Verify provided app can rotate its TLS certs."""
    original_tls_info = {}
    for unit in ops_test.model.applications[app].units:
        original_tls_info[unit.name] = {}
        original_tls_info[unit.name]["external_cert_contents"] = await get_file_content(
            ops_test, unit.name, EXTERNAL_CERT_PATH, tmpdir
        )
        original_tls_info[unit.name]["internal_cert_contents"] = await get_file_content(
            ops_test, unit.name, INTERNAL_CERT_PATH, tmpdir
        )
        original_tls_info[unit.name]["external_cert"] = await time_file_created(
            ops_test, unit.name, EXTERNAL_CERT_PATH
        )
        original_tls_info[unit.name]["internal_cert"] = await time_file_created(
            ops_test, unit.name, INTERNAL_CERT_PATH
        )
        original_tls_info[unit.name]["mongos_service"] = await time_process_started(
            ops_test, unit.name, MONGOS_SERVICE
        )
        await check_certs_correctly_distributed(ops_test, unit, app_name=app, tmpdir=tmpdir)

    # set external and internal key using auto-generated key for each unit
    for unit in ops_test.model.applications[app].units:
        action = await unit.run_action(action_name="set-tls-private-key")
        action = await action.wait()
        assert action.status == "completed", "setting external and internal key failed."

    # wait for certificate to be available and processed. Can get receive two certificate
    # available events and restart twice so we want to ensure we are idle for at least 1 minute
    await ops_test.model.wait_for_idle(apps=[app], status="active", timeout=1000, idle_period=60)

    # After updating both the external key and the internal key a new certificate request will be
    # made; then the certificates should be available and updated.
    for unit in ops_test.model.applications[app].units:
        new_external_cert = await get_file_content(ops_test, unit.name, EXTERNAL_CERT_PATH, tmpdir)
        new_internal_cert = await get_file_content(ops_test, unit.name, INTERNAL_CERT_PATH, tmpdir)
        new_external_cert_time = await time_file_created(ops_test, unit.name, EXTERNAL_CERT_PATH)
        new_internal_cert_time = await time_file_created(ops_test, unit.name, INTERNAL_CERT_PATH)
        new_mongos_service_time = await time_process_started(ops_test, unit.name, MONGOS_SERVICE)

        await check_certs_correctly_distributed(ops_test, unit, app_name=app, tmpdir=tmpdir)
        assert (
            new_external_cert != original_tls_info[unit.name]["external_cert_contents"]
        ), "external cert not rotated"

        assert (
            new_internal_cert != original_tls_info[unit.name]["external_cert_contents"]
        ), "external cert not rotated"
        assert (
            new_external_cert_time > original_tls_info[unit.name]["external_cert"]
        ), f"external cert for {unit.name} was not updated."
        assert (
            new_internal_cert_time > original_tls_info[unit.name]["internal_cert"]
        ), f"internal cert for {unit.name} was not updated."

        # Once the certificate requests are processed and updated the .service file should be
        # restarted

        assert (
            new_mongos_service_time > original_tls_info[unit.name]["mongos_service"]
        ), f"mongos service for {unit.name} was not restarted."

    # Verify that TLS is functioning on all units.
    for unit in ops_test.model.applications[MONGOS_APP_NAME].units:
        await check_mongos_tls_enabled(ops_test)
