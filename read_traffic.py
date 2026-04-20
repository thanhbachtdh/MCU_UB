import time
import csv
import os
import subprocess
import json
import sys
import uuid
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion # Thêm dòng này
from routeros_api import RouterOsApiPool

# ──────────────────────────────────────────────────────────────────────────────
# THÔNG SỐ KẾT NỐI
# ──────────────────────────────────────────────────────────────────────────────
MK_IP       = os.environ.get("MK_IP",       "192.168.40.1")
MK_USER     = os.environ.get("MK_USER",     "admin")
MK_PASS     = os.environ.get("MK_PASS",     "123")
MQTT_BROKER = os.environ.get("MQTT_BROKER", "192.168.40.252")
MQTT_PORT   = int(os.environ.get("MQTT_PORT", "1883"))

# ── Identity (phải khớp với tenant/vessel/edge đã seed trên server) ───────────
TENANT_CODE = os.environ.get("TENANT_CODE", "tenant-01")
VESSEL_CODE = os.environ.get("VESSEL_CODE", "vessel-01")
EDGE_CODE   = os.environ.get("EDGE_CODE",   "remote_01")

# ── MQTT topic prefix ─────────────────────────────────────────────────────────
_PREFIX         = f"mcu/{TENANT_CODE}/{VESSEL_CODE}/{EDGE_CODE}"
TOPIC_TELEMETRY = f"{_PREFIX}/telemetry"
TOPIC_HEARTBEAT = f"{_PREFIX}/heartbeat"
TOPIC_ACK       = f"{_PREFIX}/ack"
TOPIC_RESULT    = f"{_PREFIX}/result"
TOPIC_EVENT     = f"{_PREFIX}/event"
TOPIC_COMMAND   = f"{_PREFIX}/command"   # subscribe

WATCH_PORTS = {
    'ether1-Starlink': 'P1-STARLINK',
    'ether2-VSAT':     'P2-VSAT',
    'ether3-LTE':      'P3-LTE',
    'ether4-MCU':      'P4-MCU',
    'ether5-USER':     'P5-USER'
}

BASE_DIR          = os.path.dirname(os.path.abspath(__file__))
LOG_FILE          = os.path.join(BASE_DIR, "traffic_log.csv")
POLICY_SCRIPT     = os.path.join(BASE_DIR, "routeros_policy.py")
COMMAND_HOOK      = os.environ.get("COMMAND_HOOK", "")
TELEMETRY_INTERVAL = int(os.environ.get("TELEMETRY_INTERVAL", "5"))

# ──────────────────────────────────────────────────────────────────────────────
# MQTT CLIENT
# ──────────────────────────────────────────────────────────────────────────────
client = mqtt.Client(CallbackAPIVersion.VERSION2)

# ──────────────────────────────────────────────────────────────────────────────
# ENVELOPE (theo MQTT contract v1 của backend)
# ──────────────────────────────────────────────────────────────────────────────
def make_envelope(payload: dict) -> str:
    return json.dumps({
        "msg_id":         str(uuid.uuid4()),
        "schema_version": "v1",
        "timestamp":      time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        "payload":        payload
    })

# ──────────────────────────────────────────────────────────────────────────────
# XỬ LÝ COMMAND TỪ SERVER
# ──────────────────────────────────────────────────────────────────────────────
def publish_ack(job_id: str):
    ack_payload = make_envelope({
        "command_job_id": job_id,
        "status":         "ack",
        "message":        "Command received by MCU"
    })
    client.publish(TOPIC_ACK, ack_payload, qos=1)
    print(f"[MCU] ACK sent for job_id={job_id}")

def publish_result(job_id: str, success: bool, message: str = "", result_payload: dict = None):
    body = {
        "command_job_id": job_id,
        "status":         "success" if success else "failed",
        "message":        message,
    }
    if result_payload:
        body["result_payload"] = result_payload
    client.publish(TOPIC_RESULT, make_envelope(body), qos=1)
    print(f"[MCU] RESULT sent job_id={job_id} status={'success' if success else 'failed'}")

def publish_event(event_type: str, severity: str, details: dict = None):
    body = {
        "event_type": event_type,
        "severity":   severity,
        "details":    details or {}
    }
    client.publish(TOPIC_EVENT, make_envelope(body), qos=1)
    print(f"[MCU] EVENT sent type={event_type} severity={severity}")

def run_policy_sync(groups: list = None) -> tuple:
    if not os.path.exists(POLICY_SCRIPT):
        return False, f"routeros_policy.py không tìm thấy tại {POLICY_SCRIPT}"
    try:
        if groups is not None:
            # Policy động từ server: truyền qua stdin
            completed = subprocess.run(
                [sys.executable, POLICY_SCRIPT, "--stdin-apply"],
                input=json.dumps(groups),
                capture_output=True, text=True,
                check=False, timeout=120
            )
        else:
            # Fallback: dùng cấu hình mặc định bên trong script
            completed = subprocess.run(
                [sys.executable, POLICY_SCRIPT, "--apply"],
                capture_output=True, text=True,
                check=False, timeout=120
            )
        success = (completed.returncode == 0)
        output  = (completed.stdout + completed.stderr).strip()
        print(f"[policy] returncode={completed.returncode}\n{output}")
        return success, output
    except subprocess.TimeoutExpired:
        return False, "routeros_policy.py timeout sau 120s"
    except Exception as e:
        return False, str(e)

def run_command_hook(command_type: str, command_payload: dict) -> tuple:
    if not COMMAND_HOOK or not os.path.exists(COMMAND_HOOK):
        msg = f"COMMAND_HOOK không được cấu hình hoặc không tồn tại: '{COMMAND_HOOK}'"
        print(f"[MCU] WARNING: {msg}")
        return False, msg
    env = os.environ.copy()
    env["MCU_COMMAND_TYPE"]    = command_type
    env["MCU_COMMAND_PAYLOAD"] = json.dumps(command_payload)
    env["MK_IP"]               = MK_IP
    env["MK_USER"]             = MK_USER
    env["MK_PASS"]             = MK_PASS
    try:
        completed = subprocess.run(
            ["/bin/bash", COMMAND_HOOK],
            capture_output=True, text=True,
            check=False, timeout=60, env=env
        )
        success = (completed.returncode == 0)
        output  = (completed.stdout + completed.stderr).strip()
        print(f"[hook] {command_type} returncode={completed.returncode}\n{output}")
        return success, output
    except subprocess.TimeoutExpired:
        return False, f"{COMMAND_HOOK} timeout sau 60s"
    except Exception as e:
        return False, str(e)

def execute_command(raw_message: str):
    try:
        envelope = json.loads(raw_message)
    except json.JSONDecodeError as e:
        print(f"[MCU] WARNING command JSON parse error: {e}")
        return

    job_id = (
        envelope.get("msg_id")
        or envelope.get("payload", {}).get("command_job_id")
        or str(uuid.uuid4())
    )

    publish_ack(job_id)

    payload      = envelope.get("payload", envelope)
    command_type = payload.get("command_type", "")
    cmd_payload  = payload.get("command_payload", {}) or {}

    print(f"[MCU] Executing command type={command_type} job_id={job_id}")

    if command_type == "policy_sync":
        groups = cmd_payload.get("groups") if isinstance(cmd_payload, dict) else None
        success, output = run_policy_sync(groups=groups)
        publish_result(
            job_id, success,
            message=output[:500],
            result_payload={"groups_count": len(groups) if groups else 0}
        )
        if not success:
            publish_event("policy_error", "warning", {"command_type": command_type, "detail": output[:200]})

    elif command_type in ("failover_starlink", "failback_vsat", "restore_automatic"):
        success, output = run_command_hook(command_type, cmd_payload)
        publish_result(job_id, success, message=output[:500])

    else:
        msg = f"Unknown command_type: '{command_type}'"
        print(f"[MCU] WARNING {msg}")
        publish_result(job_id, False, message=msg)

# ──────────────────────────────────────────────────────────────────────────────
# MQTT CALLBACKS
# ──────────────────────────────────────────────────────────────────────────────
def on_connect(client_ref, userdata, flags, reason_code, properties):
    if reason_code == 0:
        print(f"[MCU] MQTT connected to {MQTT_BROKER}:{MQTT_PORT}")
        client_ref.subscribe(TOPIC_COMMAND, qos=1)
        print(f"[MCU] Subscribed to {TOPIC_COMMAND}")
    else:
        print(f"[MCU] WARNING MQTT connect failed: reason_code={reason_code}")

def on_disconnect(client_ref, userdata, flags, reason_code, properties):
    print(f"[MCU] WARNING MQTT disconnected reason_code={reason_code}, se tu reconnect...")

def on_message(client_ref, userdata, msg):
    print(f"[MCU] Command received on {msg.topic}")
    try:
        execute_command(msg.payload.decode("utf-8"))
    except Exception as e:
        print(f"[MCU] WARNING Error processing command: {e}")

# ──────────────────────────────────────────────────────────────────────────────
# KẾT NỐI MQTT
# ──────────────────────────────────────────────────────────────────────────────
def connect_mqtt() -> bool:
    client.on_connect    = on_connect
    client.on_disconnect = on_disconnect
    client.on_message    = on_message
    try:
        client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        client.loop_start()
        return True
    except Exception as e:
        print(f"[MCU] WARNING MQTT connect error: {e}")
        return False

# ──────────────────────────────────────────────────────────────────────────────
# PING CHECK (giữ nguyên logic cũ)
# ──────────────────────────────────────────────────────────────────────────────
def ping_check(host):
    try:
        output = subprocess.run(['ping', '-c', '1', '-W', '0.8', host],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return "ONLINE" if output.returncode == 0 else "OFFLINE"
    except:
        return "ERROR"

# ──────────────────────────────────────────────────────────────────────────────
# VÒNG LẶP CHÍNH
# ──────────────────────────────────────────────────────────────────────────────
def monitor():
    mqtt_ok = connect_mqtt()
    if not mqtt_ok:
        print("[MCU] WARNING MQTT Broker Offline. Script van chay nhung khong gui duoc du lieu ve Hub.")

    # Gửi heartbeat ban đầu
    if mqtt_ok:
        hb = make_envelope({
            "status":           "online",
            "firmware_version": "MCU-v2.0",
            "cpu_usage_pct":    None,
            "ram_usage_pct":    None,
            "public_wan_ip":    None
        })
        client.publish(TOPIC_HEARTBEAT, hb, qos=1)
        print("[MCU] Heartbeat sent (startup)")

    try:
        api_pool = RouterOsApiPool(MK_IP, username=MK_USER, password=MK_PASS, plaintext_login=True)
        api = api_pool.get_api()
        res_iface = api.get_resource('/interface')

        prev_data = {i['name']: i for i in res_iface.get() if i['name'] in WATCH_PORTS}
        prev_time = time.time()

        while True:
            time.sleep(TELEMETRY_INTERVAL)
            curr_time = time.time()
            interval  = curr_time - prev_time
            timestamp = time.strftime('%H:%M:%S')

            net_stat = ping_check("8.8.8.8")
            be_stat  = ping_check(MQTT_BROKER)

            try:
                current_data = {i['name']: i for i in res_iface.get() if i['name'] in WATCH_PORTS}
            except:
                continue

            interfaces    = []
            active_uplink = None
            total_rx_kbps = 0.0
            total_tx_kbps = 0.0

            print(f"\n📅 {time.strftime('%Y-%m-%d')} | 🕒 {timestamp}")
            print(f"📡 INTERNET: {net_stat} | 🔗 HUB VPN: {be_stat}")
            print("=" * 82)
            print(f"{'PORT':<12} | {'STATUS':<10} | {'TRAFFIC (kbps)':^26} | {'TOTAL (MB)':>12}")
            print("-" * 82)

            for name, label in WATCH_PORTS.items():
                if name not in current_data:
                    continue

                iface  = current_data[name]
                is_up  = iface.get('running') == 'true'
                status_str = "✅ UP" if is_up else "❌ DOWN"

                curr_rx = int(iface.get('rx-byte', 0))
                curr_tx = int(iface.get('tx-byte', 0))
                prev_rx = int(prev_data.get(name, {}).get('rx-byte', 0))
                prev_tx = int(prev_data.get(name, {}).get('tx-byte', 0))

                kbps_in  = ((curr_rx - prev_rx) * 8) / (1024 * interval)
                kbps_out = ((curr_tx - prev_tx) * 8) / (1024 * interval)
                total_mb = (curr_rx + curr_tx) / (1024 * 1024)
                total_gb = (curr_rx + curr_tx) / (1024 * 1024 * 1024)

                # Đưa vào payload MQTT (giữ nguyên format cũ để in terminal)
                traffic = f"⬇ {kbps_in:>8.1f} | ⬆ {kbps_out:>8.1f}"
                print(f"{label:<12} | {status_str:<10} | {traffic} | {total_mb:>10.2f} MB")

                # Contract v1: interfaces[]
                interfaces.append({
                    "name":            name,
                    "status":          "up" if is_up else "down",
                    "rx_kbps":         round(kbps_in,  1),
                    "tx_kbps":         round(kbps_out, 1),
                    "throughput_kbps": round(kbps_in + kbps_out, 1),
                    "total_gb":        round(total_gb, 6)
                })

                total_rx_kbps += kbps_in
                total_tx_kbps += kbps_out

                if is_up and active_uplink is None and name not in ('ether4-MCU', 'ether5-USER'):
                    active_uplink = name

            # Gửi telemetry theo contract v1
            telemetry_body = {
                "active_uplink":   active_uplink,
                "latency_ms":      None,
                "loss_pct":        0.0 if net_stat == "ONLINE" else 100.0,
                "jitter_ms":       None,
                "rx_kbps":         round(total_rx_kbps, 1),
                "tx_kbps":         round(total_tx_kbps, 1),
                "throughput_kbps": round(total_rx_kbps + total_tx_kbps, 1),
                "public_wan_ip":   None,
                "internet":        net_stat,
                "hub_vpn":         be_stat,
                "interfaces":      interfaces,
            }

            if mqtt_ok:
                client.publish(TOPIC_TELEMETRY, make_envelope(telemetry_body), qos=0)

            prev_data, prev_time = current_data, curr_time

    except KeyboardInterrupt:
        print("\n👋 Stopped.")
        client.loop_stop()
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        if 'api_pool' in locals():
            api_pool.disconnect()

if __name__ == "__main__":
    monitor()
