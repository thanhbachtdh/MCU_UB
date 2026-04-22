import paho.mqtt.client as mqtt
import json
import sys
from routeros_api import RouterOsApiPool

# ================= CẤU HÌNH HỆ THỐNG =================
MQTT_BROKER = "192.168.40.252"
MQTT_PORT = 1883
TOPIC_CMD = "tram1/cmd/hotspot"
TOPIC_REPLY = "tram1/reply/hotspot"

MK_IP = "10.0.0.1"
MK_USER = "admin"
MK_PASS = "123"
# =====================================================

def ensure_profile_exists(api, profile_name, qos_rate):
    """Hàm kiểm tra và tự tạo Profile nếu chưa có"""
    profile_resource = api.get_resource('/ip/hotspot/user/profile')
    
    # 1. Kiểm tra xem profile đã tồn tại chưa
    existing_profiles = profile_resource.get(name=profile_name)
    
    if existing_profiles:
        # Nếu đã có, kiểm tra xem có cần cập nhật QoS không (Tùy chọn)
        # Tạm thời ở đây chúng ta giữ nguyên nếu đã tồn tại
        print(f"[*] Profile '{profile_name}' đã tồn tại. Bỏ qua bước tạo Profile.")
        return True, "Profile exists"
        
    # 2. Nếu chưa có, tiến hành tạo mới
    print(f"[*] Profile '{profile_name}' chưa có. Tiến hành tạo mới với QoS: {qos_rate}...")
    try:
        profile_params = {
            'name': profile_name,
            'rate-limit': qos_rate if qos_rate else "" # Nếu không gửi QoS thì để trống
        }
        profile_resource.add(**profile_params)
        print(f"[+] Tạo thành công Profile: {profile_name}")
        return True, "Profile created"
    except Exception as e:
        return False, f"Lỗi tạo Profile: {str(e)}"

def create_mk_user(username, password, profile, qos_rate):
    """Hàm giao tiếp tổng hợp với MikroTik"""
    api_pool = None
    try:
        api_pool = RouterOsApiPool(MK_IP, username=MK_USER, password=MK_PASS, plaintext_login=True)
        api = api_pool.get_api()
        
        # --- BƯỚC 1: XỬ LÝ PROFILE TRƯỚC ---
        if profile and profile != "default":
            prof_success, prof_msg = ensure_profile_exists(api, profile, qos_rate)
            if not prof_success:
                return False, prof_msg # Nếu tạo profile xịt thì dừng luôn báo lỗi
                
        # --- BƯỚC 2: TẠO ACCOUNT USER ---
        hs_resource = api.get_resource('/ip/hotspot/user')

        if hs_resource.get(name=username):
            return False, f"Tài khoản '{username}' đã tồn tại trên MikroTik."

        # Xây dựng thông số tạo User
        user_params = {
            'name': username,
            'password': password,
            'profile': profile,
            'comment': 'Auto-created via Edge Node'
        }
        
        # Lưu ý: Không nhét rate-limit trực tiếp vào User nữa, 
        # vì băng thông sẽ bị quản lý bởi cái Profile mà nó thuộc về.

        hs_resource.add(**user_params)
        return True, f"Tạo thành công User '{username}' thuộc nhóm '{profile}'"

    except Exception as e:
        return False, f"Lỗi API MikroTik: {str(e)}"
    finally:
        if api_pool:
            api_pool.disconnect()

def send_reply(client, status, message, user):
    reply_payload = {
        "action": "create_account",
        "station": "tram1",
        "username": user,
        "status": status,
        "message": message
    }
    client.publish(TOPIC_REPLY, json.dumps(reply_payload))
    print(f"📤 Đã phản hồi: {status.upper()} - {message}")

def on_connect(client, userdata, flags, rc):
    print(f"✅ Đã kết nối Broker {MQTT_BROKER}. Lắng nghe tại: {TOPIC_CMD}")
    client.subscribe(TOPIC_CMD)

def on_message(client, userdata, msg):
    payload = msg.payload.decode("utf-8")
    print(f"\n📩 Nhận lệnh: {payload}")
    
    try:
        data = json.loads(payload)
        username = data.get("username")
        password = data.get("password")
        profile = data.get("profile", "default")
        qos_rate = data.get("qos", "") # Lấy QoS để phục vụ cho việc tạo Profile

        if not username or not password:
            send_reply(client, "error", "Thiếu username/password", "unknown")
            return

        is_success, result_msg = create_mk_user(username, password, profile, qos_rate)
        
        if is_success:
            send_reply(client, "success", result_msg, username)
        else:
            send_reply(client, "error", result_msg, username)

    except Exception as e:
        send_reply(client, "error", f"Lỗi hệ thống: {str(e)}", "unknown")

if __name__ == "__main__":
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    try:
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_forever()
    except KeyboardInterrupt:
        pass	
