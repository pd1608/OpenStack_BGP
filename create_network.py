"""
Module 1: Virtual Network Creation & Public Network Connection
==============================================================
Creates multiple virtual networks and subnets, all connected to a
SINGLE shared router that provides internet access via the public
external network — using openstacksdk.

Topology:
    vn-alpha ─┐
    vn-beta  ──┤── router-main ── [public/internet]
    vn-gamma ─┘

Usage:
    python 01_network_setup.py

Install:
    pip install openstacksdk
"""

import json
import openstack

# ─────────────────────────────────────────────
# CONFIG — edit these or export as env vars
# (openstacksdk also reads clouds.yaml automatically)
# ─────────────────────────────────────────────

AUTH = dict(
    auth_url            = "http://10.224.76.78/identity",
    username            = "admin",
    password            = "pranav",       # <-- change
    project_name        = "admin",
    user_domain_name    = "Default",
    project_domain_name = "Default",
)

VIRTUAL_NETWORKS = [
    {"name": "vn-alpha", "cidr": "192.168.10.0/24", "gateway": "192.168.10.1", "dns": "8.8.8.8"},
    {"name": "vn-beta",  "cidr": "192.168.20.0/24", "gateway": "192.168.20.1", "dns": "8.8.8.8"},
    {"name": "vn-gamma", "cidr": "192.168.30.0/24", "gateway": "192.168.30.1", "dns": "8.8.8.8"},
]

EXTERNAL_NETWORK_NAME = "public"
SHARED_ROUTER_NAME    = "router-main"   # single router for all VNs


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def get_or_create_network(conn, name):
    net = conn.network.find_network(name)
    if net:
        print(f"  [NET] '{name}' already exists — skipping.")
        return net
    net = conn.network.create_network(name=name, admin_state_up=True)
    print(f"  [NET] Created '{name}'  id={net.id[:8]}…")
    return net


def get_or_create_subnet(conn, network_id, name, cidr, gateway, dns):
    for s in conn.network.subnets(network_id=network_id):
        if s.cidr == cidr:
            print(f"  [SUB] Subnet {cidr} already exists — skipping.")
            return s
    subnet = conn.network.create_subnet(
        name            = f"{name}-subnet",
        network_id      = network_id,
        ip_version      = 4,
        cidr            = cidr,
        gateway_ip      = gateway,
        dns_nameservers = [dns],
        enable_dhcp     = True,
    )
    print(f"  [SUB] Created '{subnet.name}'  cidr={cidr}")
    return subnet


def get_or_create_shared_router(conn, name, ext_network_id):
    router = conn.network.find_router(name)
    if router:
        print(f"  [RTR] Shared router '{name}' already exists — skipping.")
        return router
    router = conn.network.create_router(
        name                  = name,
        admin_state_up        = True,
        external_gateway_info = {"network_id": ext_network_id, "enable_snat": True},
    )
    print(f"  [RTR] Created shared router '{name}'  id={router.id[:8]}…")
    return router


def add_interface(conn, router, subnet):
    try:
        conn.network.add_interface_to_router(router, subnet_id=subnet.id)
        print(f"  [RTR] Attached '{subnet.name}' to '{router.name}'")
    except Exception as e:
        if "already exists" in str(e).lower() or "in use" in str(e).lower():
            print(f"  [RTR] '{subnet.name}' already attached — skipping.")
        else:
            raise


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    print("[AUTH] Connecting…")
    conn = openstack.connect(**AUTH)
    print("[AUTH] Connected.\n")

    # Locate external network
    print(f"[STEP 1] Locating external network '{EXTERNAL_NETWORK_NAME}'…")
    ext_net = conn.network.find_network(EXTERNAL_NETWORK_NAME)
    if not ext_net:
        raise SystemExit(f"ERROR: External network '{EXTERNAL_NETWORK_NAME}' not found.")
    print(f"  Found  id={ext_net.id[:8]}…\n")

    # Create the ONE shared router (gateway → public network)
    print(f"[STEP 2] Creating shared router '{SHARED_ROUTER_NAME}'…")
    router = get_or_create_shared_router(conn, SHARED_ROUTER_NAME, ext_net.id)
    print()

    # Create each VN + subnet, then attach to the shared router
    print(f"[STEP 3] Creating {len(VIRTUAL_NETWORKS)} virtual network(s) and attaching to router…")
    created = []
    for vn in VIRTUAL_NETWORKS:
        print(f"\n  ── {vn['name']} ({vn['cidr']}) ──")
        network = get_or_create_network(conn, vn["name"])
        subnet  = get_or_create_subnet(conn, network.id, vn["name"],
                                       vn["cidr"], vn["gateway"], vn["dns"])
        add_interface(conn, router, subnet)
        created.append({
            "vn_name":    vn["name"],
            "network_id": network.id,
            "subnet_id":  subnet.id,
            "router_id":  router.id,   # same router ID for all VNs
            "cidr":       vn["cidr"],
        })

    # Summary
    print("\n" + "="*55)
    print("NETWORK SETUP COMPLETE")
    print("="*55)
    print(f"  Shared router: {SHARED_ROUTER_NAME}  ({router.id[:8]}…)")
    print(f"  Gateway:       {EXTERNAL_NETWORK_NAME} → internet\n")
    for c in created:
        print(f"  {c['vn_name']:10s}  cidr={c['cidr']}  → router-main")

    with open("network_ids.json", "w") as f:
        json.dump(created, f, indent=2)
    print("\n[INFO] IDs saved to network_ids.json")


if __name__ == "__main__":
    main()