"""
routeros_policy.py
Áp policy routing lên MikroTik.

Chế độ chạy:
  python3 routeros_policy.py --apply            # dùng cấu hình mặc định (env / hardcode)
  python3 routeros_policy.py --stdin-apply      # nhận groups JSON từ stdin (server gửi xuống)
  python3 routeros_policy.py --dry-run          # in ra lệnh sẽ chạy, không thực thi
"""

import os
import sys
import json
import argparse
from routeros_api import RouterOsApiPool

# ──────────────────────────────────────────────────────────────────────────────
# KẾT NỐI
# ──────────────────────────────────────────────────────────────────────────────
MK_IP   = os.environ.get("MK_IP",   "10.0.0.1")
MK_USER = os.environ.get("MK_USER", "admin")
MK_PASS = os.environ.get("MK_PASS", "123")

# ──────────────────────────────────────────────────────────────────────────────
# CẤU HÌNH MẶC ĐỊNH (dùng khi --apply, không có payload từ server)
# Mỗi group = 1 nhóm IP muốn đi qua 1 uplink riêng
# ──────────────────────────────────────────────────────────────────────────────
DEFAULT_GROUPS = [
    {
        "name":               "work",
        "preferred_uplink":   "VSAT",
        "address_list_name":  "mcu-work",
        "routing_table":      "to-vsat",
        "gateway":            os.environ.get("VSAT_GATEWAY", "192.168.88.1"),
        "source_addresses":   os.environ.get("WORK_IPS", "").split(",") if os.environ.get("WORK_IPS") else []
    },
    {
        "name":               "entertainment",
        "preferred_uplink":   "Starlink",
        "address_list_name":  "mcu-entertainment",
        "routing_table":      "to-starlink",
        "gateway":            os.environ.get("STARLINK_GATEWAY", "192.168.99.1"),
        "source_addresses":   os.environ.get("ENTERTAINMENT_IPS", "").split(",") if os.environ.get("ENTERTAINMENT_IPS") else []
    }
]

# ──────────────────────────────────────────────────────────────────────────────
# HÀM IDEMPOTENT: chỉ tạo nếu chưa có, cập nhật nếu khác
# ──────────────────────────────────────────────────────────────────────────────

def ensure_routing_table(api, table_name: str, dry_run: bool = False):
    """Tạo routing table nếu chưa tồn tại"""
    res = api.get_resource('/routing/table')
    existing = res.get(name=table_name)
    if existing:
        print(f"  [skip] routing table '{table_name}' already exists")
        return
    if dry_run:
        print(f"  [dry-run] /routing/table add name={table_name} fib")
        return
    res.add(name=table_name, fib='')
    print(f"  [ok] routing table '{table_name}' created")

def ensure_address_list(api, list_name: str, addresses: list, dry_run: bool = False):
    """Đồng bộ IP vào address-list (thêm mới, bỏ qua đã có)"""
    res = api.get_resource('/ip/firewall/address-list')
    existing_entries = res.get(**{'list': list_name})
    existing_ips = {e.get('address') for e in existing_entries}

    for addr in addresses:
        addr = addr.strip()
        if not addr:
            continue
        if addr in existing_ips:
            print(f"  [skip] address-list '{list_name}' already has {addr}")
            continue
        if dry_run:
            print(f"  [dry-run] /ip/firewall/address-list add list={list_name} address={addr}")
            continue
        res.add(**{'list': list_name, 'address': addr})
        print(f"  [ok] address-list '{list_name}' added {addr}")

def ensure_routing_rule(api, address_list_name: str, routing_table: str, dry_run: bool = False):
    """Tạo routing rule: src-address-list → routing table"""
    res = api.get_resource('/routing/rule')
    existing = res.get(**{'src-address-list': address_list_name, 'action': 'lookup', 'table': routing_table})
    if existing:
        print(f"  [skip] routing rule for '{address_list_name}' -> '{routing_table}' already exists")
        return
    if dry_run:
        print(f"  [dry-run] /routing/rule add src-address-list={address_list_name} action=lookup table={routing_table}")
        return
    res.add(**{'src-address-list': address_list_name, 'action': 'lookup', 'table': routing_table})
    print(f"  [ok] routing rule '{address_list_name}' -> '{routing_table}' created")

def ensure_default_route(api, routing_table: str, gateway: str, dry_run: bool = False):
    """Tạo default route (0.0.0.0/0) trong routing table"""
    res = api.get_resource('/ip/route')
    existing = res.get(**{'dst-address': '0.0.0.0/0', 'routing-table': routing_table, 'gateway': gateway})
    if existing:
        print(f"  [skip] default route in '{routing_table}' via {gateway} already exists")
        return
    if dry_run:
        print(f"  [dry-run] /ip/route add dst-address=0.0.0.0/0 routing-table={routing_table} gateway={gateway}")
        return
    res.add(**{'dst-address': '0.0.0.0/0', 'routing-table': routing_table, 'gateway': gateway})
    print(f"  [ok] default route in '{routing_table}' via {gateway} created")

# ──────────────────────────────────────────────────────────────────────────────
# ÁP POLICY CHO 1 GROUP
# ──────────────────────────────────────────────────────────────────────────────
def apply_group(api, group: dict, dry_run: bool = False):
    name        = group.get("name", "unnamed")
    list_name   = group.get("address_list_name", f"mcu-{name}")
    table       = group.get("routing_table",     f"to-{name}")
    gateway     = group.get("gateway", "")
    addresses   = group.get("source_addresses",  [])

    print(f"\n--- Group: {name} (uplink={group.get('preferred_uplink','?')}) ---")

    if not gateway:
        print(f"  [skip] gateway not set for group '{name}', skipping")
        return

    ensure_routing_table(api, table, dry_run)
    ensure_address_list(api, list_name, addresses, dry_run)
    ensure_routing_rule(api, list_name, table, dry_run)
    ensure_default_route(api, table, gateway, dry_run)

# ──────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Apply MikroTik routing policy")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply",       action="store_true", help="Apply default groups from env/hardcode")
    mode.add_argument("--stdin-apply", action="store_true", help="Read groups JSON from stdin and apply")
    mode.add_argument("--dry-run",     action="store_true", help="Print commands without executing")
    args = parser.parse_args()

    # Xác định groups
    if args.stdin_apply:
        raw = sys.stdin.read().strip()
        if not raw:
            print("[policy] ERROR: stdin is empty", file=sys.stderr)
            sys.exit(1)
        try:
            groups = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"[policy] ERROR: stdin JSON parse failed: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"[policy] Received {len(groups)} group(s) from stdin")
    else:
        groups = DEFAULT_GROUPS
        print(f"[policy] Using {len(groups)} default group(s)")

    dry_run = args.dry_run

    # Kết nối MikroTik
    print(f"[policy] Connecting to MikroTik {MK_IP} ...")
    try:
        pool = RouterOsApiPool(MK_IP, username=MK_USER, password=MK_PASS, plaintext_login=True)
        api  = pool.get_api()
    except Exception as e:
        print(f"[policy] ERROR: Cannot connect to MikroTik: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        for group in groups:
            apply_group(api, group, dry_run)
        print("\n[policy] All groups applied successfully")
    except Exception as e:
        print(f"[policy] ERROR during apply: {e}", file=sys.stderr)
        pool.disconnect()
        sys.exit(1)

    pool.disconnect()
    sys.exit(0)

if __name__ == "__main__":
    main()
