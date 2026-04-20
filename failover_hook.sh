#!/bin/bash
# failover_hook.sh
# Được gọi bởi read_traffic.py khi nhận command failover/failback/restore từ server.
# Biến môi trường được truyền vào:
#   MCU_COMMAND_TYPE    : failover_starlink | failback_vsat | restore_automatic
#   MK_IP               : IP của MikroTik
#   MK_USER             : username
#   MK_PASS             : password
#   MCU_COMMAND_PAYLOAD : JSON payload từ server (nếu có)

set -e

MK_IP="${MK_IP:-192.168.40.1}"
MK_USER="${MK_USER:-admin}"
MK_PASS="${MK_PASS:-123}"
COMMAND="${MCU_COMMAND_TYPE:-}"

echo "[hook] Running command: ${COMMAND} on MikroTik ${MK_IP}"

# Hàm chạy lệnh RouterOS qua SSH
ros_cmd() {
    sshpass -p "${MK_PASS}" ssh -o StrictHostKeyChecking=no \
        "${MK_USER}@${MK_IP}" "$@" 2>/dev/null
}

case "${COMMAND}" in

  failover_starlink)
    # Ưu tiên Starlink: set distance thấp hơn VSAT
    echo "[hook] Switching active uplink to Starlink..."
    ros_cmd "/ip route set [find gateway=\$(ip route get dst=0.0.0.0/0 routing-table=main | get gateway) routing-table=main] distance=10" || true
    # Cách đơn giản hơn: đặt cứng theo gateway
    # ros_cmd "/ip route set [find dst-address=0.0.0.0/0 gateway=10.18.150.225] distance=1"
    # ros_cmd "/ip route set [find dst-address=0.0.0.0/0 gateway=192.168.1.1] distance=2"
    echo "[hook] Failover to Starlink done"
    ;;

  failback_vsat)
    # Ưu tiên VSAT: set distance thấp hơn Starlink
    echo "[hook] Switching active uplink to VSAT..."
    # ros_cmd "/ip route set [find dst-address=0.0.0.0/0 gateway=10.18.150.225] distance=1"
    # ros_cmd "/ip route set [find dst-address=0.0.0.0/0 gateway=192.168.1.1] distance=2"
    echo "[hook] Failback to VSAT done"
    ;;

  restore_automatic)
    # Khôi phục về chế độ tự động (cân bằng tải hoặc distance mặc định)
    echo "[hook] Restoring automatic routing..."
    # ros_cmd "/ip route set [find dst-address=0.0.0.0/0 gateway=10.18.150.225] distance=1"
    # ros_cmd "/ip route set [find dst-address=0.0.0.0/0 gateway=192.168.1.1] distance=2"
    echo "[hook] Restore automatic done"
    ;;

  *)
    echo "[hook] ERROR: Unknown command type: '${COMMAND}'"
    exit 1
    ;;

esac

exit 0
