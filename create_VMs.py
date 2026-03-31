import json
import os
import sys
import time
import openstack
from openstack.exceptions import ResourceFailure

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

AUTH = dict(
    auth_url            = "http://10.224.76.78/identity",
    username            = "admin",
    password            = "pranav",
    project_name        = "admin",
    user_domain_name    = "Default",
    project_domain_name = "Default",
)

KEYPAIR_NAME  = "my-key"
CREATE_KEYPAIR = False
FLAVOR_NAME   = "m1.small"
IMAGE_NAME    = "cirros-0.6.3-x86_64-disk"
EXTERNAL_NETWORK_NAME = "public"
SECURITY_GROUP_NAME   = "allow-all"

VM_DEFINITIONS = [
    {"name": "vm-alpha-01", "vn_index": 0, "tenant": "single"},
    {"name": "vm-alpha-02", "vn_index": 0, "tenant": "single"},
    {"name": "vm-alpha-03", "vn_index": 0, "tenant": "single"},
    {"name": "vm-multi-a",  "vn_index": 0, "tenant": "multi"},
    {"name": "vm-multi-b",  "vn_index": 1, "tenant": "multi"},
    {"name": "vm-multi-c",  "vn_index": 2, "tenant": "multi"},
]

BOOT_TIMEOUT = 180  # Increased timeout for stability

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def save_vm_status(results):
    """Writes the current results list to vm_ids.json immediately."""
    with open("vm_ids.json", "w") as f:
        json.dump(results, f, indent=2)

def attach_floating_ip(conn, server, ext_net_name):
    # Check existing
    for net_name, addrs in server.addresses.items():
        for addr in addrs:
            if addr.get("OS-EXT-IPS:type") == "floating":
                return addr["addr"]

    # Neutron-native assignment (more stable)
    ports = list(conn.network.ports(device_id=server.id))
    if not ports:
        return "port_not_found"
    
    ext_net = conn.network.find_network(ext_net_name)
    fip = conn.network.create_ip(floating_network_id=ext_net.id, port_id=ports[0].id)
    return fip.floating_ip_address

# ... (ensure_keypair and ensure_security_group remain the same) ...

def ensure_keypair(conn, name, create):
    kp = conn.compute.find_keypair(name)
    if kp:
        print(f"  [KEY] Keypair '{name}' already exists.")
        return
    if not create:
        raise SystemExit(f"ERROR: Keypair '{name}' not found.")
    kp = conn.compute.create_keypair(name=name, type="ssh")
    with open(f"{name}.pem", "w") as f:
        f.write(kp.private_key)
    os.chmod(f"{name}.pem", 0o600)

def ensure_security_group(conn, name):
    sg = conn.network.find_security_group(name)
    if sg:
        print(f"  [SG] Security group '{name}' already exists.")
        return sg
    sg = conn.network.create_security_group(name=name)
    for direction in ("ingress", "egress"):
        try:
            conn.network.create_security_group_rule(security_group_id=sg.id, direction=direction, ethertype="IPv4")
        except: pass
    return sg

def create_or_get_server(conn, name, image, flavor, network_id, keypair, sg_name):
    existing = conn.compute.find_server(name)
    if existing:
        print(f"  [VM] '{name}' already exists.")
        return existing
    return conn.compute.create_server(
        name=name, image_id=image.id, flavor_id=flavor.id,
        networks=[{"uuid": network_id}], key_name=keypair,
        security_groups=[{"name": sg_name}]
    )

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    if not os.path.exists("network_ids.json"):
        raise SystemExit("ERROR: network_ids.json not found.")

    with open("network_ids.json") as f:
        networks = json.load(f)

    # 1. Initialize the file immediately
    results = []
    save_vm_status(results)
    print("[INFO] Initialized vm_ids.json")

    conn = openstack.connect(**AUTH)
    image = conn.compute.find_image(IMAGE_NAME)
    flavor = conn.compute.find_flavor(FLAVOR_NAME)
    
    ensure_keypair(conn, KEYPAIR_NAME, CREATE_KEYPAIR)
    ensure_security_group(conn, SECURITY_GROUP_NAME)

    print(f"\n[STEP 4] Provisioning {len(VM_DEFINITIONS)} VMs...")
    
    for vmdef in VM_DEFINITIONS:
        net = networks[vmdef["vn_index"]]
        print(f"\n  ── {vmdef['name']} ──")

        try:
            server = create_or_get_server(conn, vmdef["name"], image, flavor, net["network_id"], KEYPAIR_NAME, SECURITY_GROUP_NAME)
            
            # Wait for ACTIVE
            server = conn.compute.wait_for_server(server, wait=BOOT_TIMEOUT)
            print("  [VM] ACTIVE.")
            
            # Settle time for Neutron
            time.sleep(2)
            
            fip = attach_floating_ip(conn, server, EXTERNAL_NETWORK_NAME)
            
            fixed_ip = next((a["addr"] for addrs in server.addresses.values() 
                            for a in addrs if a.get("OS-EXT-IPS:type") == "fixed"), "unknown")

            # 2. Append result and update file immediately
            vm_data = {
                "name": vmdef["name"],
                "server_id": server.id,
                "status": "SUCCESS",
                "fixed_ip": fixed_ip,
                "floating_ip": fip
            }
            results.append(vm_data)
            save_vm_status(results)
            print(f"  [JSON] Updated vm_ids.json with {vmdef['name']}")

        except Exception as e:
            print(f"  [ERROR] Failed to provision {vmdef['name']}: {e}")
            results.append({"name": vmdef["name"], "status": "FAILED", "error": str(e)})
            save_vm_status(results)

    print("\n[FINISH] Provisioning cycle complete. Check vm_ids.json for final states.")

if __name__ == "__main__":
    main()