import customtkinter as ctk
import threading
import time
import json
import os
import csv  # <--- NEW: For Excel Logging
import pynvml
import requests
from datetime import datetime

# --- CONFIGURATION ---
PRICE_PER_KWH = 2.14          
STATE_FILE = "power_state.json"
LOG_FILE = "power_log.csv"  # <--- NEW: Excel File
LHM_URL = "http://localhost:8085/data.json"

# --- SYSTEM SETUP ---
os.environ["PATH"] += os.pathsep + os.getcwd()

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class PowerMonitorApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        # 1. Hardware Init
        self.gpu_data = [] 
        self.nvml_active = False
        self.setup_nvml()
        
        # Dynamic Width Calculation
        extra_width = max(0, (len(self.gpu_data) - 1) * 140) 
        window_width = 750 + extra_width
        
        self.title("⚡ Power Monitor (Excel Logger)")
        self.geometry(f"{window_width}x640") 
        self.resizable(True, False)

        # 2. Data Init
        self.running = True
        self.start_time = time.time()
        self.session_data = {"kwh": 0.0, "cost": 0.0}
        self.persistent_data = self.load_data()
        self.save_data()
        self.init_csv() # <--- NEW: Setup Excel File

        # --- UI LAYOUT ---
        self.lbl_title = ctk.CTkLabel(self, text="Real-Time Consumption", font=("Roboto", 22, "bold"))
        self.lbl_title.pack(pady=(20, 5))

        self.lbl_watts = ctk.CTkLabel(self, text="--- W", font=("Roboto", 60, "bold"), text_color="#00E5FF")
        self.lbl_watts.pack(pady=5)
        
        # --- HARDWARE COLUMNS ---
        self.frame_hw = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_hw.pack(pady=10, padx=10, fill="x")

        # A. NVIDIA GPUs
        if self.nvml_active:
            for i, gpu in enumerate(self.gpu_data):
                short_name = gpu['name'].replace("NVIDIA GeForce ", "").replace("NVIDIA ", "")
                
                frame = ctk.CTkFrame(self.frame_hw, width=140)
                frame.pack(side="left", expand=True, padx=5)
                
                ctk.CTkLabel(frame, text=short_name, font=("Arial", 11, "bold"), text_color="#76b900").pack(pady=(5,0))
                lbl_val = ctk.CTkLabel(frame, text="0 W", font=("Roboto", 20, "bold"))
                lbl_val.pack(pady=(0, 0))
                lbl_temp = ctk.CTkLabel(frame, text="-- °C", font=("Arial", 12), text_color="gray")
                lbl_temp.pack(pady=(0, 10))
                
                self.gpu_data[i]['widget_pwr'] = lbl_val
                self.gpu_data[i]['widget_temp'] = lbl_temp

        # B. AMD iGPU
        self.frame_igpu = ctk.CTkFrame(self.frame_hw, width=140)
        self.frame_igpu.pack(side="left", expand=True, padx=5)
        ctk.CTkLabel(self.frame_igpu, text="iGPU (Radeon)", font=("Arial", 11, "bold"), text_color="#FF3333").pack(pady=(5,0))
        self.lbl_igpu_val = ctk.CTkLabel(self.frame_igpu, text="0 W", font=("Roboto", 20, "bold"))
        self.lbl_igpu_val.pack(pady=(0, 0))
        self.lbl_igpu_temp = ctk.CTkLabel(self.frame_igpu, text="-- °C", font=("Arial", 12), text_color="gray")
        self.lbl_igpu_temp.pack(pady=(0, 10))

        # C. Ryzen CPU
        self.frame_cpu = ctk.CTkFrame(self.frame_hw, width=140)
        self.frame_cpu.pack(side="right", expand=True, padx=5)
        ctk.CTkLabel(self.frame_cpu, text="Ryzen 9900X", font=("Arial", 11, "bold"), text_color="#ff8c00").pack(pady=(5,0))
        self.lbl_cpu_val = ctk.CTkLabel(self.frame_cpu, text="0 W", font=("Roboto", 20, "bold"))
        self.lbl_cpu_val.pack(pady=(0, 0))
        self.lbl_cpu_temp = ctk.CTkLabel(self.frame_cpu, text="-- °C", font=("Arial", 12), text_color="gray")
        self.lbl_cpu_temp.pack(pady=(0, 10))

        # --- STATS GRID ---
        self.frame_stats = ctk.CTkFrame(self)
        self.frame_stats.pack(pady=20, padx=20, fill="x")

        # Headers
        ctk.CTkLabel(self.frame_stats, text="TIMELINE", font=("Arial", 12, "bold"), text_color="gray").grid(row=0, column=0, padx=20, pady=10, sticky="w")
        ctk.CTkLabel(self.frame_stats, text="COST (EGP)", font=("Arial", 12, "bold"), text_color="gray").grid(row=0, column=1, padx=15, pady=10, sticky="e")
        ctk.CTkLabel(self.frame_stats, text="ENERGY", font=("Arial", 12, "bold"), text_color="gray").grid(row=0, column=2, padx=15, pady=10, sticky="e")
        ctk.CTkLabel(self.frame_stats, text="DURATION", font=("Arial", 12, "bold"), text_color="gray").grid(row=0, column=3, padx=20, pady=10, sticky="e")

        # Rows
        ctk.CTkLabel(self.frame_stats, text="Session:", font=("Arial", 14)).grid(row=1, column=0, padx=20, pady=5, sticky="w")
        self.lbl_sess_cost = ctk.CTkLabel(self.frame_stats, text="0.00", font=("Arial", 18, "bold"), text_color="#00E5FF")
        self.lbl_sess_cost.grid(row=1, column=1, padx=15, pady=5, sticky="e")
        self.lbl_sess_kwh = ctk.CTkLabel(self.frame_stats, text="0.000 kWh", font=("Arial", 12))
        self.lbl_sess_kwh.grid(row=1, column=2, padx=15, pady=5, sticky="e")
        self.lbl_sess_time = ctk.CTkLabel(self.frame_stats, text="00:00:00", font=("Arial", 12), text_color="silver")
        self.lbl_sess_time.grid(row=1, column=3, padx=20, pady=5, sticky="e")

        ctk.CTkLabel(self.frame_stats, text="Today:", font=("Arial", 14)).grid(row=2, column=0, padx=20, pady=5, sticky="w")
        self.lbl_day_cost = ctk.CTkLabel(self.frame_stats, text="0.00", font=("Arial", 18, "bold"), text_color="#00FF00")
        self.lbl_day_cost.grid(row=2, column=1, padx=15, pady=5, sticky="e")
        self.lbl_day_kwh = ctk.CTkLabel(self.frame_stats, text="0.000 kWh", font=("Arial", 12))
        self.lbl_day_kwh.grid(row=2, column=2, padx=15, pady=5, sticky="e")
        self.lbl_day_time = ctk.CTkLabel(self.frame_stats, text="00:00:00", font=("Arial", 12), text_color="silver")
        self.lbl_day_time.grid(row=2, column=3, padx=20, pady=5, sticky="e")

        ctk.CTkLabel(self.frame_stats, text="Overall:", font=("Arial", 14)).grid(row=3, column=0, padx=20, pady=5, sticky="w")
        self.lbl_life_cost = ctk.CTkLabel(self.frame_stats, text="0.00", font=("Arial", 18, "bold"), text_color="#FFA500")
        self.lbl_life_cost.grid(row=3, column=1, padx=15, pady=5, sticky="e")
        self.lbl_life_kwh = ctk.CTkLabel(self.frame_stats, text="0.000 kWh", font=("Arial", 12))
        self.lbl_life_kwh.grid(row=3, column=2, padx=15, pady=5, sticky="e")
        self.lbl_life_time = ctk.CTkLabel(self.frame_stats, text="00:00:00", font=("Arial", 12), text_color="silver")
        self.lbl_life_time.grid(row=3, column=3, padx=20, pady=5, sticky="e")

        self.lbl_status = ctk.CTkLabel(self, text="Initializing...", text_color="gray", font=("Arial", 11))
        self.lbl_status.pack(side="bottom", pady=10)

        self.monitor_thread = threading.Thread(target=self.background_monitor, daemon=True)
        self.monitor_thread.start()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def setup_nvml(self):
        try:
            pynvml.nvmlInit()
            count = pynvml.nvmlDeviceGetCount()
            for i in range(count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                raw_name = pynvml.nvmlDeviceGetName(handle)
                name = raw_name.decode() if isinstance(raw_name, bytes) else raw_name
                self.gpu_data.append({"handle": handle, "name": name, "widget_pwr": None, "widget_temp": None})
            self.nvml_active = True
        except: self.nvml_active = False

    def load_data(self):
        today_str = datetime.now().strftime("%Y-%m-%d")
        default = {"last_date": today_str, "day_kwh": 0.0, "day_cost": 0.0, "day_seconds": 0, "lifetime_kwh": 0.0, "lifetime_cost": 0.0, "lifetime_seconds": 0}
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    data = json.load(f)
                    if "lifetime_seconds" not in data: data.update({"lifetime_seconds": 0, "day_seconds": 0})
                    if data.get("last_date") != today_str:
                        data.update({"last_date": today_str, "day_kwh": 0.0, "day_cost": 0.0, "day_seconds": 0})
                    return data
            except: pass
        return default

    def save_data(self):
        with open(STATE_FILE, 'w') as f: json.dump(self.persistent_data, f, indent=4)

    def init_csv(self):
        """Creates the CSV file with headers if it doesn't exist"""
        if not os.path.exists(LOG_FILE):
            headers = ["Timestamp", "Total Watts", "Total Cost (Daily)", "CPU Temp", "CPU Watts", "iGPU Temp", "iGPU Watts"]
            # Add dynamic GPU columns
            for i in range(len(self.gpu_data)):
                headers.extend([f"GPU{i} Temp", f"GPU{i} Watts"])
                
            with open(LOG_FILE, mode='w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(headers)

    def log_to_csv(self, total_w, cpu_t, cpu_w, igpu_t, igpu_w, nv_metrics):
        """Appends a row to the CSV"""
        try:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            row = [
                now_str, 
                f"{total_w:.1f}", 
                f"{self.persistent_data['day_cost']:.4f}",
                f"{cpu_t:.1f}", f"{cpu_w:.1f}",
                f"{igpu_t:.1f}", f"{igpu_w:.1f}"
            ]
            # Add individual GPU data
            for metric in nv_metrics:
                row.extend([f"{metric['temp']:.1f}", f"{metric['power']:.1f}"])
                
            with open(LOG_FILE, mode='a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(row)
        except: pass

    def fetch_lhm_data(self):
        try:
            response = requests.get(LHM_URL, timeout=0.2)
            if response.status_code == 200: return response.json()
        except: return None
        return None

    def find_sensor_value(self, node, target_names, sensor_type="Power"):
        if isinstance(node, dict):
            if node.get("Type") == sensor_type:
                if any(name.lower() in node.get("Text", "").lower() for name in target_names):
                    val_str = str(node.get("Value", "0")).split()[0]
                    try: return float(val_str)
                    except: return 0.0
            if "Children" in node:
                for child in node["Children"]:
                    res = self.find_sensor_value(child, target_names, sensor_type)
                    if res > 0: return res
        elif isinstance(node, list):
            for item in node:
                res = self.find_sensor_value(item, target_names, sensor_type)
                if res > 0: return res
        return 0.0

    def calculate_igpu_total(self, data):
        total = 0.0
        def scan(node):
            nonlocal total
            if isinstance(node, dict):
                if "Radeon" in node.get("Text", "") or "Generic VGA" in node.get("Text", ""):
                    total = self.sum_radeon_powers(node)
                    return True
                if "Children" in node:
                    for child in node["Children"]:
                        if scan(child): return True
            elif isinstance(node, list):
                for item in node:
                    if scan(item): return True
            return False
        scan(data)
        return total

    def sum_radeon_powers(self, node):
        acc = 0.0
        if "Children" in node:
            for child in node["Children"]:
                if child.get("Type") == "Power":
                    name = child.get("Text", "")
                    if name in ["GPU Core", "GPU SoC", "GPU Power"]:
                        try: acc += float(str(child.get("Value", "0")).split()[0])
                        except: pass
                acc += self.sum_radeon_powers(child)
        return acc

    def get_color(self, temp):
        if temp < 60: return "#76b900"
        if temp < 80: return "#ff8c00"
        return "#FF4444"

    def format_time(self, seconds):
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def background_monitor(self):
        last_log = time.time()
        
        while self.running:
            total_nvidia_w = 0
            nv_metrics = [] # Store temp/power for CSV
            
            # 1. Update NVIDIA GPUs
            if self.nvml_active:
                for gpu in self.gpu_data:
                    try: 
                        w = pynvml.nvmlDeviceGetPowerUsage(gpu['handle']) / 1000.0
                        total_nvidia_w += w
                        t = pynvml.nvmlDeviceGetTemperature(gpu['handle'], 0)
                        
                        nv_metrics.append({"power": w, "temp": t})
                        
                        if gpu['widget_pwr']:
                            gpu['widget_pwr'].configure(text=f"{int(w)} W")
                            gpu['widget_temp'].configure(text=f"{t} °C", text_color=self.get_color(t))
                    except: 
                        nv_metrics.append({"power": 0, "temp": 0})

            lhm_data = self.fetch_lhm_data()
            cpu_w, igpu_w = 0, 0
            cpu_t, igpu_t = 0, 0
            is_estimated = False
            status_msg = "Live Data"

            if lhm_data:
                cpu_w = self.find_sensor_value(lhm_data, ["Package", "CPU Package"], "Power")
                igpu_w = self.calculate_igpu_total(lhm_data)
                cpu_t = self.find_sensor_value(lhm_data, ["Core (Tctl/Tdie)", "Package", "Core #1"], "Temperature")
                igpu_t = self.find_sensor_value(lhm_data, ["GPU Core", "GPU Temperature"], "Temperature")
                
                if cpu_w == 0: is_estimated = True
            else:
                is_estimated = True
                status_msg = "Estimating (LHM Off)"
                if not self.nvml_active: cpu_w = 55 
            
            # 2. Totals
            overhead = 55 
            total_w = total_nvidia_w + igpu_w + cpu_w + overhead

            # 3. Stats Logic
            kwh_inc = (total_w * 1.0) / 3_600_000
            cost_inc = kwh_inc * PRICE_PER_KWH
            self.session_data["kwh"] += kwh_inc
            self.session_data["cost"] += cost_inc
            self.persistent_data["day_kwh"] += kwh_inc
            self.persistent_data["day_cost"] += cost_inc
            self.persistent_data["day_seconds"] += 1
            self.persistent_data["lifetime_kwh"] += kwh_inc
            self.persistent_data["lifetime_cost"] += cost_inc
            self.persistent_data["lifetime_seconds"] += 1

            current_date = datetime.now().strftime("%Y-%m-%d")
            if current_date != self.persistent_data["last_date"]:
                self.persistent_data["last_date"] = current_date
                self.persistent_data["day_kwh"] = 0.0
                self.persistent_data["day_cost"] = 0.0
                self.persistent_data["day_seconds"] = 0

            # 4. GUI Update
            try:
                self.lbl_sess_time.configure(text=self.format_time(time.time() - self.start_time))
                self.lbl_day_time.configure(text=self.format_time(self.persistent_data["day_seconds"]))
                self.lbl_life_time.configure(text=self.format_time(self.persistent_data["lifetime_seconds"]))

                self.lbl_watts.configure(text=f"{int(total_w)} W")
                self.lbl_igpu_val.configure(text=f"{int(igpu_w)} W")
                self.lbl_cpu_val.configure(text=f"{int(cpu_w)} W")
                self.lbl_status.configure(text=status_msg)

                self.lbl_igpu_temp.configure(text=f"{int(igpu_t)} °C", text_color=self.get_color(igpu_t))
                self.lbl_cpu_temp.configure(text=f"{int(cpu_t)} °C", text_color=self.get_color(cpu_t))

                if total_w > 500: self.lbl_watts.configure(text_color="#FF4444")
                elif total_w > 300: self.lbl_watts.configure(text_color="#FFD700")
                else: self.lbl_watts.configure(text_color="#00E5FF")
                
                color_state = "gray" if is_estimated else "#ff8c00"
                self.lbl_cpu_val.configure(text_color=color_state)

                self.lbl_sess_cost.configure(text=f"{self.session_data['cost']:.4f}")
                self.lbl_sess_kwh.configure(text=f"{self.session_data['kwh']:.4f} kWh")
                self.lbl_day_cost.configure(text=f"{self.persistent_data['day_cost']:.4f}")
                self.lbl_day_kwh.configure(text=f"{self.persistent_data['day_kwh']:.4f} kWh")
                self.lbl_life_cost.configure(text=f"{self.persistent_data['lifetime_cost']:.4f}")
                self.lbl_life_kwh.configure(text=f"{self.persistent_data['lifetime_kwh']:.4f} kWh")
            except: pass

            # 5. SAVE & LOG (Every 60s)
            if time.time() - last_log > 60:
                self.save_data()
                self.log_to_csv(total_w, cpu_t, cpu_w, igpu_t, igpu_w, nv_metrics) # <--- LOGGING HAPPENS HERE
                last_log = time.time()
            time.sleep(1)

    def on_close(self):
        self.running = False
        self.save_data()
        self.destroy()

if __name__ == "__main__":
    app = PowerMonitorApp()
    app.mainloop()