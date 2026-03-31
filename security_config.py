"""
Module 3: Security Groups & Port Security
=========================================
Configures security groups and port security for intra-VN and
inter-VN communication — using openstacksdk.

  sg-intra-vn   → all TCP/UDP/ICMP within the same VN
  sg-inter-vn   → all TCP/UDP/ICMP across different VNs
  sg-host-access→ SSH (22) + ICMP from anywhere (host → VM)

All three SGs are applied to every VM port.
Port security is enabled on every port.

Usage:
    python 03_security_config.py  (requires network_ids.json + vm_ids.json)

Install:
    pip install openstacksdk
"""

import json
import os
import sys
import openstack

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

AUTH = dict(
    auth_url            = "http://10.224.76.78/identity",
    username            = "admin",
    password            = "pranav",       # <-- change
    project_name        = "admin",
    user_domain_name    = "Default",
    project_domain_name = "Default",
)

SG_INTRA = "sg-intra-vn"
SG_INTER = "sg-inter-vn"
SG_HOST  = "sg-host-access"


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def recreate_sg(conn, name, description):
    """Delete existing SG by name (if any) and create a fresh one."""
    existing = conn.network.find_security_group(name)
    if existing:
        print(f"  [SG] Removing stale '{name}' to apply fresh rules…")
        conn.network.delete_security_group(existing.id)
    sg = conn.network.create_security_group(name=name, description=description)
    print(f"  [SG] Created '{name}'  id={sg.id[:8]}…")
    return sg


def add_rule(conn, sg_id, direction, protocol=None,
             port_min=None, port_max=None, remote_cidr=None):
    """Add a rule, silently ignoring duplicates."""
    kwargs = dict(
        security_group_id = sg_id,
        direction         = direction,
        ethertype         = "IPv4",
    )
    if protocol:    kwargs["protocol"]        = protocol
    if port_min:    kwargs["port_range_min"]  = port_min
    if port_max:    kwargs["port_range_max"]  = port_max
    if remote_cidr: kwargs["remote_ip_prefix"]= remote_cidr
    try:
        conn.network.create_security_group_rule(**kwargs)
    except Exception as e:
        if "already exists" not in str(e).lower():
            raise


def build_intra_vn_sg(conn, cidrs):
    sg = recreate_sg(conn, SG_INTRA,
                     "Intra-VN: allow all traffic between VMs on the same network")
    for cidr in cidrs:
        add_rule(conn, sg.id, "ingress", "icmp",  remote_cidr=cidr)
        add_rule(conn, sg.id, "ingress", "tcp",  1, 65535, cidr)
        add_rule(conn, sg.id, "ingress", "udp",  1, 65535, cidr)
        print(f"    ingress tcp/udp/icmp ← {cidr}")
    add_rule(conn, sg.id, "egress",  remote_cidr="0.0.0.0/0")
    print(f"    egress → 0.0.0.0/0")
    return sg.id


def build_inter_vn_sg(conn, cidrs):
    sg = recreate_sg(conn, SG_INTER,
                     "Inter-VN: allow all traffic between VMs on different networks")
    for cidr in cidrs:
        add_rule(conn, sg.id, "ingress", "icmp",  remote_cidr=cidr)
        add_rule(conn, sg.id, "ingress", "tcp",  1, 65535, cidr)
        add_rule(conn, sg.id, "ingress", "udp",  1, 65535, cidr)
        print(f"    ingress tcp/udp/icmp ← {cidr}")
    add_rule(conn, sg.id, "egress",  remote_cidr="0.0.0.0/0")
    print(f"    egress → 0.0.0.0/0")
    return sg.id


def build_host_access_sg(conn):
    sg = recreate_sg(conn, SG_HOST,
                     "Host access: SSH (22) + ICMP from 0.0.0.0/0")
    add_rule(conn, sg.id, "ingress", "tcp",  22, 22, "0.0.0.0/0")
    print(f"    ingress TCP:22 (SSH) ← 0.0.0.0/0")
    add_rule(conn, sg.id, "ingress", "icmp", remote_cidr="0.0.0.0/0")
    print(f"    ingress ICMP (ping)  ← 0.0.0.0/0")
    add_rule(conn, sg.id, "egress",  remote_cidr="0.0.0.0/0")
    print(f"    egress → 0.0.0.0/0")
    return sg.id


def configure_port(conn, port, sg_ids):
    """Enable port security and apply security groups to a port."""
    conn.network.update_port(
        port,
        port_security_enabled = True,
        security_groups       = sg_ids,
    )


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    for fname in ("network_ids.json", "vm_ids.json"):
        if not os.path.exists(fname):
            raise SystemExit(f"ERROR: '{fname}' not found. Run previous modules first.")

    with open("network_ids.json") as f:
        networks = json.load(f)
    with open("vm_ids.json") as f:
        vms = json.load(f)

    cidrs = [n["cidr"] for n in networks]
    print(f"[INFO] VN CIDRs: {cidrs}\n")

    print("[AUTH] Connecting…")
    conn = openstack.connect(**AUTH)
    print("[AUTH] Connected.\n")

    # Build security groups
    print(f"[STEP 1] Building security groups…\n")
    print(f"  ── {SG_INTRA} ──")
    sg_intra_id = build_intra_vn_sg(conn, cidrs)

    print(f"\n  ── {SG_INTER} ──")
    sg_inter_id = build_inter_vn_sg(conn, cidrs)

    print(f"\n  ── {SG_HOST} ──")
    sg_host_id  = build_host_access_sg(conn)

    desired_sgs = [sg_intra_id, sg_inter_id, sg_host_id]

    # Apply to all VM ports
    print(f"\n[STEP 2] Applying security groups to VM ports…\n")
    for vm in vms:
        ports = list(conn.network.ports(device_id=vm["server_id"]))
        if not ports:
            print(f"  [WARN] No ports found for {vm['name']} — skipping.")
            continue
        for port in ports:
            configure_port(conn, port, desired_sgs)
            print(f"  [PORT] {vm['name']:16s}  port={port.id[:8]}…  "
                  f"port_security=ENABLED  SGs=[intra, inter, host-access]")

    # Summary
    print("\n" + "="*65)
    print("SECURITY CONFIGURATION COMPLETE")
    print("="*65)
    print(f"""
  Security Groups:
    {SG_INTRA:<22}  id={sg_intra_id[:8]}…  (intra-VN traffic)
    {SG_INTER:<22}  id={sg_inter_id[:8]}…  (inter-VN traffic)
    {SG_HOST:<22}  id={sg_host_id[:8]}…  (SSH + ICMP from host)

  Connectivity:
    Host → any VM          SSH (22) + ICMP    via floating IP  ✓
    VM   → VM (same VN)    all protocols      via fixed IP     ✓
    VM   → VM (diff VN)    all protocols      via floating IP  ✓
    VM   → Internet        all protocols      via SNAT/egress  ✓
""")

    with open("security_config.json", "w") as f:
        json.dump({
            "security_groups": {
                "intra_vn":    {"name": SG_INTRA, "id": sg_intra_id},
                "inter_vn":    {"name": SG_INTER, "id": sg_inter_id},
                "host_access": {"name": SG_HOST,  "id": sg_host_id},
            },
            "vn_cidrs": cidrs,
            "vms_configured": [v["name"] for v in vms],
        }, f, indent=2)
    print("[INFO] Config saved to security_config.json")


if __name__ == "__main__":
    main()