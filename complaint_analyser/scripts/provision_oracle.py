#!/usr/bin/env python3
"""Provision an OCI VM.Standard.A1.Flex instance with automatic retry.

SETUP (one-time)
----------------
1. Install the SDK:
       pip install oci

2. Create an OCI API key and config:
       mkdir -p ~/.oci
       oci setup config          # interactive — or see manual steps below

   Manual steps if you prefer:
   a) OCI Console → top-right avatar → My Profile → API Keys → Add API Key
   b) Generate a new key pair, download the private key to ~/.oci/oci_api_key.pem
   c) Copy the config snippet shown and save it to ~/.oci/config

3. Set required env vars (or edit the USER CONFIG section below):
       export OCI_SUBNET_ID="ocid1.subnet.oc1..."   # required
       export OCI_COMPARTMENT_ID="ocid1.tenancy..."  # optional; defaults to tenancy root

4. Run:
       python scripts/provision_oracle.py

SLEEP PREVENTION (macOS)
------------------------
The script launches `caffeinate -i -s -w <pid>` which prevents the system
from sleeping while it runs.
  - `-i`  prevents idle sleep (display timeout)
  - `-s`  prevents system sleep — works on AC power, even with lid closed
  - `-w`  ties caffeinate to this process

Keep the MacBook PLUGGED IN. On battery, macOS ignores -s and sleeps anyway.

RETRY INTERVAL
--------------
Default: 60 s. OCI free-tier capacity for A1.Flex appears and disappears
quickly but unpredictably. 60 s polls frequently enough to catch openings
without burning OCI API quota. You can go as low as 30 s with --interval 30.

WHAT IT DOES
------------
  1. Reads credentials from ~/.oci/config (DEFAULT profile).
  2. Discovers all availability domains in your region.
  3. Cycles through ADs round-robin, attempting to launch the instance.
  4. On "Out of capacity" (HTTP 500) it waits and retries.
  5. On success: waits for RUNNING state, fetches the public IP,
     prints the ssh command, and writes instance_info.json.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import subprocess
import sys
import time
from pathlib import Path

try:
    import oci
except ImportError:
    print(
        "ERROR: OCI SDK not installed.\n"
        "Run:  pip install oci",
        file=sys.stderr,
    )
    sys.exit(1)

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── User config ───────────────────────────────────────────────────────────────
# Override via env vars or edit directly.

COMPARTMENT_ID = os.getenv("OCI_COMPARTMENT_ID", "")  # blank → tenancy root
SUBNET_ID = os.getenv("OCI_SUBNET_ID", "")  # required
IMAGE_ID = os.getenv("OCI_IMAGE_ID", "")  # blank → auto-discover Ubuntu 22.04 aarch64
SSH_KEY_PATH = os.getenv(
    "OCI_SSH_KEY_PATH",
    str(Path.home() / ".ssh" / "oracle_complaint.pub"),
)
DISPLAY_NAME = os.getenv("OCI_DISPLAY_NAME", "complaint-analyser")

SHAPE = "VM.Standard.A1.Flex"
OCPUS = 4
MEMORY_GBS = 24
BOOT_VOLUME_GBS = 50

DEFAULT_INTERVAL = 60  # seconds between attempts

# ─────────────────────────────────────────────────────────────────────────────


def _start_caffeinate() -> subprocess.Popen | None:
    """Prevent macOS from sleeping while this script runs.

    Uses -s (system sleep prevention on AC) so the Mac stays awake even
    with the lid closed — as long as it is plugged in.
    """
    if sys.platform != "darwin":
        return None
    try:
        proc = subprocess.Popen(
            ["caffeinate", "-i", "-s", "-w", str(os.getpid())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.info(
            "caffeinate running (PID %d) — keep the MacBook PLUGGED IN "
            "to stay awake with lid closed",
            proc.pid,
        )
        return proc
    except FileNotFoundError:
        log.warning("caffeinate not found — sleep prevention skipped")
        return None


def _resolve_compartment(config: dict) -> str:
    if COMPARTMENT_ID:
        return COMPARTMENT_ID
    tenancy = config.get("tenancy", "")
    if not tenancy:
        raise ValueError(
            "Cannot resolve compartment: set OCI_COMPARTMENT_ID "
            "or ensure 'tenancy' is in ~/.oci/config"
        )
    log.info("OCI_COMPARTMENT_ID not set — using tenancy root: %s", tenancy)
    return tenancy


def _resolve_image(compute: oci.core.ComputeClient, compartment_id: str) -> str:
    if IMAGE_ID:
        return IMAGE_ID
    log.info("OCI_IMAGE_ID not set — searching for Ubuntu 22.04 Minimal aarch64…")
    images = oci.pagination.list_call_get_all_results(
        compute.list_images,
        compartment_id=compartment_id,
        operating_system="Canonical Ubuntu",
        shape=SHAPE,
        sort_by="TIMECREATED",
        sort_order="DESC",
    ).data
    # Prefer "22.04 Minimal aarch64", fall back to any 22.04 ARM
    for strict in (True, False):
        for img in images:
            name = img.display_name.lower()
            if "22.04" not in name:
                continue
            if strict and not ("minimal" in name and "aarch64" in name):
                continue
            log.info("Using image: %s  (%s)", img.display_name, img.id)
            return img.id
    names = [i.display_name for i in images[:8]]
    raise ValueError(
        f"No Ubuntu 22.04 aarch64 image found for shape {SHAPE}.\n"
        f"Available images: {names}\n"
        "Set OCI_IMAGE_ID manually."
    )


def _get_availability_domains(
    identity: oci.identity.IdentityClient, compartment_id: str
) -> list[str]:
    ads = identity.list_availability_domains(compartment_id).data
    names = [ad.name for ad in ads]
    log.info("Availability domains: %s", names)
    return names


def _is_capacity_error(exc: oci.exceptions.ServiceError) -> bool:
    return exc.status == 500 and "capacity" in exc.message.lower()


def _try_launch(
    compute: oci.core.ComputeClient,
    ad: str,
    compartment_id: str,
    image_id: str,
    ssh_key: str,
) -> oci.core.models.Instance | None:
    """Return the Instance on success, None on capacity error, raise on other errors."""
    details = oci.core.models.LaunchInstanceDetails(
        availability_domain=ad,
        compartment_id=compartment_id,
        shape=SHAPE,
        shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
            ocpus=OCPUS,
            memory_in_gbs=MEMORY_GBS,
        ),
        display_name=DISPLAY_NAME,
        source_details=oci.core.models.InstanceSourceViaImageDetails(
            source_type="image",
            image_id=image_id,
            boot_volume_size_in_gbs=BOOT_VOLUME_GBS,
        ),
        create_vnic_details=oci.core.models.CreateVnicDetails(
            subnet_id=SUBNET_ID,
            assign_public_ip=True,
        ),
        metadata={"ssh_authorized_keys": ssh_key},
    )
    try:
        return compute.launch_instance(details).data
    except oci.exceptions.ServiceError as exc:
        if _is_capacity_error(exc):
            return None
        raise


def _wait_for_running(
    compute: oci.core.ComputeClient, instance_id: str
) -> oci.core.models.Instance:
    log.info("Waiting for RUNNING state…")
    while True:
        instance = compute.get_instance(instance_id).data
        state = instance.lifecycle_state
        log.info("  %s", state)
        if state == "RUNNING":
            return instance
        if state in ("TERMINATED", "TERMINATING"):
            raise RuntimeError(f"Instance moved to {state} — unexpected failure")
        time.sleep(10)


def _get_public_ip(
    oci_config: dict,
    compute: oci.core.ComputeClient,
    compartment_id: str,
    instance_id: str,
) -> str | None:
    attachments = compute.list_vnic_attachments(
        compartment_id=compartment_id,
        instance_id=instance_id,
    ).data
    if not attachments:
        return None
    vcn_client = oci.core.VirtualNetworkClient(oci_config)
    vnic = vcn_client.get_vnic(attachments[0].vnic_id).data
    return vnic.public_ip


# ── Main provisioning loop ────────────────────────────────────────────────────


def provision(interval: int) -> None:
    log.info("Loading ~/.oci/config (DEFAULT profile)…")
    oci_config = oci.config.from_file()
    oci.config.validate_config(oci_config)

    compute = oci.core.ComputeClient(oci_config)
    identity = oci.identity.IdentityClient(oci_config)

    compartment_id = _resolve_compartment(oci_config)
    image_id = _resolve_image(compute, compartment_id)
    ads = _get_availability_domains(identity, compartment_id)
    ssh_key = Path(SSH_KEY_PATH).read_text().strip()

    log.info("─" * 60)
    log.info("Shape      : %s  (%d OCPUs / %d GB RAM)", SHAPE, OCPUS, MEMORY_GBS)
    log.info("Boot disk  : %d GB", BOOT_VOLUME_GBS)
    log.info("Display    : %s", DISPLAY_NAME)
    log.info("Subnet     : %s", SUBNET_ID)
    log.info("Interval   : %d s (+±5 s jitter)", interval)
    log.info("─" * 60)

    attempt = 0
    ad_index = 0

    while True:
        attempt += 1
        ad = ads[ad_index % len(ads)]
        ad_index += 1

        log.info("Attempt %d  →  %s", attempt, ad)

        try:
            instance = _try_launch(compute, ad, compartment_id, image_id, ssh_key)
        except oci.exceptions.ServiceError as exc:
            # Non-capacity API errors (404, 401, 400) are config problems — exit immediately
            log.error(
                "  OCI %d (%s): %s", exc.status, exc.code, exc.message
            )
            if exc.status == 404:
                log.error(
                    "  404 = subnet, image, or compartment OCID not found. "
                    "Check OCI_SUBNET_ID is set correctly."
                )
            raise
        except Exception as exc:
            log.error("  Unexpected error: %s", exc, exc_info=True)
            _sleep_with_jitter(interval)
            continue

        if instance is None:
            log.info("  No capacity in %s", ad)
            _sleep_with_jitter(interval)
            continue

        # ── Success ───────────────────────────────────────────────────────────
        log.info("=" * 60)
        log.info("Instance created: %s", instance.id)
        instance = _wait_for_running(compute, instance.id)
        public_ip = _get_public_ip(oci_config, compute, compartment_id, instance.id)

        log.info("=" * 60)
        log.info("PROVISIONED SUCCESSFULLY")
        log.info("  Instance  : %s", instance.id)
        log.info("  AD        : %s", instance.availability_domain)
        log.info("  Public IP : %s", public_ip)
        log.info("=" * 60)
        log.info("SSH command:")
        log.info("  ssh -i ~/.ssh/oracle_complaint ubuntu@%s", public_ip)
        log.info("=" * 60)

        result = {
            "instance_id": instance.id,
            "display_name": instance.display_name,
            "availability_domain": instance.availability_domain,
            "public_ip": public_ip,
            "shape": instance.shape,
        }
        out = Path(__file__).parent.parent / "instance_info.json"
        out.write_text(json.dumps(result, indent=2))
        log.info("Instance info saved to %s", out)
        break


def _sleep_with_jitter(base: int) -> None:
    """Sleep for base ± 5 s to avoid thundering-herd if multiple scripts run."""
    delay = base + random.randint(-5, 5)
    log.info("  Retrying in %d s…", delay)
    time.sleep(delay)


# ─────────────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Provision OCI VM.Standard.A1.Flex with automatic retry",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL,
        help=f"Seconds between attempts (default: {DEFAULT_INTERVAL}; minimum recommended: 30)",
    )
    parser.add_argument(
        "--no-caffeinate",
        action="store_true",
        help="Skip launching caffeinate (sleep prevention disabled)",
    )
    args = parser.parse_args()

    if args.interval < 10:
        print("ERROR: --interval must be at least 10 s to avoid OCI rate limiting.", file=sys.stderr)
        sys.exit(1)

    if not SUBNET_ID:
        print(
            "ERROR: OCI_SUBNET_ID is not set.\n"
            "Find it in OCI Console → Networking → Virtual Cloud Networks\n"
            "→ <your VCN> → Subnets → copy the subnet OCID.",
            file=sys.stderr,
        )
        sys.exit(1)

    ssh_pub = Path(SSH_KEY_PATH)
    if not ssh_pub.exists():
        print(
            f"ERROR: SSH public key not found at {SSH_KEY_PATH}\n"
            "Generate one with:  ssh-keygen -t ed25519 -f ~/.ssh/oracle_complaint",
            file=sys.stderr,
        )
        sys.exit(1)

    caff = None if args.no_caffeinate else _start_caffeinate()
    try:
        provision(args.interval)
    except KeyboardInterrupt:
        log.info("Interrupted — exiting")
    except Exception as exc:
        log.error("Fatal: %s", exc, exc_info=True)
        sys.exit(1)
    finally:
        if caff and caff.poll() is None:
            caff.terminate()


if __name__ == "__main__":
    main()
