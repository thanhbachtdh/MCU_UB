import time
from routeros_api import RouterOsApiPool

# --- THÔNG SỐ KẾT NỐI ---
MK_IP   = "10.0.0.1"
MK_USER = "admin"
MK_PASS = "123"

# Interface cần theo dõi
interfaces_to_watch = ['vlan10-Starlink', 'vlan20-VSAT', 'vlan30-LTE']

print(f"🚀 Kết nối MikroTik API tại {MK_IP}...")

try:
    api_pool = RouterOsApiPool(MK_IP, username=MK_USER, password=MK_PASS, plaintext_login=True)
    api = api_pool.get_api()
    resource = api.get_resource('/interface')

    # 1. Lấy dữ liệu mốc ban đầu (để tính Tổng tích lũy từ lúc chạy script)
    initial_data = resource.get()
    start_bytes = {i['name']: {'rx': int(i.get('rx-byte', 0)), 'tx': int(i.get('tx-byte', 0))} 
                   for i in initial_data if i['name'] in interfaces_to_watch}

    # 2. Biến lưu dữ liệu của giây trước đó (để tính Mbps tức thời)
    prev_bytes = start_bytes.copy()
    prev_time = time.time()

    last_report_time = time.time()
    print("📡 Đang đo tốc độ (Mbps) mỗi 1s. Tổng kết dung lượng mỗi 10s...\n")

    while True:
        time.sleep(1) # Chu kỳ 1 giây
        current_time = time.time()
        interval = current_time - prev_time
        data = resource.get()
        
        print(f"\n--- {time.strftime('%H:%M:%S')} (Interval: {interval:.2f}s) ---")
        
        for iface in data:
            name = iface['name']
            if name in interfaces_to_watch:
                curr_rx = int(iface.get('rx-byte', 0))
                curr_tx = int(iface.get('tx-byte', 0))

                # Tính tốc độ tức thời (Mbps)
                # (Byte_mới - Byte_cũ) * 8 bit / 1024 / 1024 / giây
                rx_speed = ((curr_rx - prev_bytes[name]['rx']) * 8) / (1024 * 1024 * interval)
                tx_speed = ((curr_tx - prev_bytes[name]['tx']) * 8) / (1024 * 1024 * interval)

                print(f"[{name:15}] ⬇ {rx_speed:6.2f} Mbps | ⬆ {tx_speed:6.2f} Mbps")

                # Cập nhật prev_bytes cho vòng lặp kế tiếp
                prev_bytes[name] = {'rx': curr_rx, 'tx': curr_tx}

        # 3. Tổng kết dung lượng đã dùng sau mỗi 10 giây
        if current_time - last_report_time >= 10:
            print("\n" + "📊" + "="*45)
            print(f" TỔNG DUNG LƯỢNG ĐÃ DÙNG (Kể từ {time.strftime('%H:%M:%S', time.localtime(last_report_time))})")
            for iface in data:
                name = iface['name']
                if name in interfaces_to_watch:
                    total_rx = (int(iface.get('rx-byte', 0)) - start_bytes[name]['rx']) / (1024*1024)
                    total_tx = (int(iface.get('tx-byte', 0)) - start_bytes[name]['tx']) / (1024*1024)
                    print(f" > {name:15}: {total_rx + total_tx:8.2f} MB (R:{total_rx:6.2f} / T:{total_tx:6.2f})")
            print("="*48 + "\n")
            last_report_time = current_time
        
        prev_time = current_time

except Exception as e:
    print(f"❌ Lỗi: {e}")
finally:
    if 'api_pool' in locals():
        api_pool.disconnect()
