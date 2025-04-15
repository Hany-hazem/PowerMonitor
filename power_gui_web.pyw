import customtkinter as ctk
import threading
import time
import json
import os
import pynvml
import requests
from datetime import datetime

# --- CONFIGURATION ---
PRICE_PER_KWH = 2.14          
STATE_FILE = "power_state.json"
LHM_URL = "http://localhost:8085/data.json"

# --- SYSTEM SETUP ---
os.environ["PATH"] += os.pathsep + os.getcwd()

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class PowerMonitorApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        # 1. Window Setup
        self.title("⚡ Power Monitor (Full Time Tracking)")
        self.geometry("640x620") # Slightly wider for time strings
        self.resizable(False, False)

        # 2. Hardware Init
        self.gpu_handle = None
        self.nvml_active = False
        self.setup_nvml()
        
        # 3. Data Init
        self.running = True
        self.start_time = time.time()
        self.session_data = {"kwh": 0.0, "cost": 0.0}
        self.persistent_data = self.load_data()
        self.save_data()

        # --- UI LAYOUT ---
        # Title
        self.lbl_title = ctk.CTkLabel(self, text="Real-Time Consumption", font=("Roboto", 22, "bold"))
        self.lbl_title.pack(pady=(20, 5))

        # Big Watts
        self.lbl_watts = ctk.CTkLabel(self, text="--- W", font=("Roboto", 60, "bold"), text_color="#00E5FF")
        self.lbl_watts.pack(pady=5)
        
        # Hardware Split
        self.frame_hw = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_hw.pack(pady=10, padx=10, fill="x")

        # NVIDIA
        self.frame_dgpu = ctk.CTkFrame(self.frame_hw, width=150)
        self.frame_dgpu.pack(side="left", expand=True, padx=5)
        ctk.CTkLabel(self.frame_dgpu, text="RTX 4070 Ti", font=("Arial", 11, "bold"), text_color="#76b900").pack(pady=5)
        self.lbl_dgpu_val = ctk.CTkLabel(self.frame_dgpu, text="0 W", font=("Roboto", 20, "bold"))
        self.lbl_dgpu_val.pack(pady=(0, 10))

        # Radeon
        self.frame_igpu = ctk.CTkFrame(self.frame_hw, width=150)
        self.frame_igpu.pack(side="left", expand=True, padx=5)
        ctk.CTkLabel(self.frame_igpu, text="iGPU (Radeon)", font=("Arial", 11, "bold"), text_color="#FF3333").pack(pady=5)
        self.lbl_igpu_val = ctk.CTkLabel(self.frame_igpu, text="0 W", font=("Roboto", 20, "bold"))
        self.lbl_igpu_val.pack(pady=(0, 10))

        # Ryzen
        self.frame_cpu = ctk.CTkFrame(self.frame_hw, width=150)
        self.frame_cpu.pack(side="right", expand=True, padx=5)
        ctk.CTkLabel(self.frame_cpu, text="Ryzen 9900X", font=("Arial", 11, "bold"), text_color="#ff8c00").pack(pady=5)
        self.lbl_cpu_val = ctk.CTkLabel(self.frame_cpu, text="0 W", font=("Roboto", 20, "bold"))
        self.lbl_cpu_val.pack(pady=(0, 10))

        # --- STATS GRID ---
        self.frame_stats = ctk.CTkFrame(self)
        self.frame_stats.pack(pady=20, padx=20, fill="x")

        # Headers
        self.lbl_h1 = ctk.CTkLabel(self.frame_stats, text="TIMELINE", font=("Arial", 12, "bold"), text_color="gray")
        self.lbl_h1.grid(row=0, column=0, padx=20, pady=10, sticky="w")
        self.lbl_h2 = ctk.CTkLabel(self.frame_stats, text="COST (EGP)", font=("Arial", 12, "bold"), text_color="gray")
        self.lbl_h2.grid(row=0, column=1, padx=20, pady=10, sticky="e")
        self.lbl_h3 = ctk.CTkLabel(self.frame_stats, text="ENERGY", font=("Arial", 12, "bold"), text_color="gray")
        self.lbl_h3.grid(row=0, column=2, padx=20, pady=10, sticky="e")

        # Row 1: Session
        self.lbl_sess_title = ctk.CTkLabel(self.frame_stats, text="Session (00:00:00):", font=("Arial", 14))
        self.lbl_sess_title.grid(row=1, column=0, padx=20, pady=5, sticky="w")
        self.lbl_sess_cost = ctk.CTkLabel(self.frame_stats, text="0.00", font=("Arial", 18, "bold"), text_color="#00E5FF")
        self.lbl_sess_cost.grid(row=1, column=1, padx=20, pady=5, sticky="e")
        self.lbl_sess_kwh = ctk.CTkLabel(self.frame_stats, text="0.000 kWh", font=("Arial", 12))
        self.lbl_sess_kwh.grid(row=1, column=2, padx=20, pady=5, sticky="e")

        # Row 2: Today (Dynamic Timer)
        self.lbl_day_title = ctk.CTkLabel(self.frame_stats, text="Today (00:00:00):", font=("Arial", 14)) # <--- Dynamic
        self.lbl_day_title.grid(row=2, column=0, padx=20, pady=5, sticky="w")
        self.lbl_day_cost = ctk.CTkLabel(self.frame_stats, text="0.00", font=("Arial", 18, "bold"), text_color="#00FF00")
        self.lbl_day_cost.grid(row=2, column=1, padx=20, pady=5, sticky="e")
        self.lbl_day_kwh = ctk.CTkLabel(self.frame_stats, text="0.000 kWh", font=("Arial", 12))
        self.lbl_day_kwh.grid(row=2, column=2, padx=20, pady=5, sticky="e")

        # Row 3: Lifetime (Dynamic Timer)
        self.lbl_life_title = ctk.CTkLabel(self.frame_stats, text="Overall (00:00:00):", font=("Arial", 14)) # <--- Dynamic
        self.lbl_life_title.grid(row=3, column=0, padx=20, pady=5, sticky="w")
        self.lbl_life_cost = ctk.CTkLabel(self.frame_stats, text="0.00", font=("Arial", 18, "bold"), text_color="#FFA500")
        self.lbl_life_cost.grid(row=3, column=1, padx=20, pady=5, sticky="e")
        self.lbl_life_kwh = ctk.CTkLabel(self.frame_stats, text="0.000 kWh", font=("Arial", 12))
        self.lbl_life_kwh.grid(row=3, column=2, padx=20, pady=5, sticky="e")

        # Status Bar
        self.lbl_status = ctk.CTkLabel(self, text="Initializing...", text_color="gray", font=("Arial", 11))
        self.lbl_status.pack(side="bottom", pady=10)

        self.monitor_thread = threading.Thread(target=self.background_monitor, daemon=True)
        self.monitor_thread.start()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def setup_nvml(self):
        try:
            pynvml.nvmlInit()
            self.gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            self.nvml_active = True
        except: self.nvml_active = False

    def load_data(self):
        today_str = datetime.now().strftime("%Y-%m-%d")
        default_data = {
            "last_date": today_str,
            "day_kwh": 0.0, "day_cost": 0.0, "day_seconds": 0,
            "lifetime_kwh": 0.0, "lifetime_cost": 0.0, "lifetime_seconds": 0
        }
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    data = json.load(f)
                    
                    # Auto-Fix: Add new time keys if missing
                    if "lifetime_seconds" not in data:
                        data["lifetime_seconds"] = 0
                        data["day_seconds"] = 0
                    
                    # Auto-Fix: Add lifetime keys if very old file
                    if "lifetime_kwh" not in data:
                        data["lifetime_kwh"] = data.get("total_kwh", 0.0)
                        data["lifetime_cost"] = data.get("total_cost", 0.0)
                        data["day_kwh"] = 0.0
                        data["day_cost"] = 0.0
                        data["last_date"] = today_str
                    
                    # Daily Reset
                    if data.get("last_date") != today_str:
                        data["last_date"] = today_str
                        data["day_kwh"] = 0.0
                        data["day_cost"] = 0.0
                        data["day_seconds"] = 0
                        
                    return data
            except: pass
        return default_data

    def save_data(self):
        with open(STATE_FILE, 'w') as f: json.dump(self.persistent_data, f, indent=4)

    def fetch_lhm_data(self):
        try:
            response = requests.get(LHM_URL, timeout=0.2)
            if response.status_code == 200: return response.json()
        except: return None
        return None

    def find_sensor_value(self, node, target_names, sensor_type="Power"):
        if isinstance(node, dict):
            if node.get("Type") == sensor_type:
                if any(name.lower() == node.get("Text", "").lower() for name in target_names):
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

    def format_time(self, seconds):
        """Converts seconds to HH:MM:SS (Can go > 24h)"""
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def background_monitor(self):
        last_log = time.time()
        
        while self.running:
            # 1. READ HARDWARE
            dgpu_w = 0
            if self.nvml_active:
                try: dgpu_w = pynvml.nvmlDeviceGetPowerUsage(self.gpu_handle) / 1000.0
                except: pass

            lhm_data = self.fetch_lhm_data()
            cpu_w, igpu_w = 0, 0
            is_estimated = False
            status_msg = "Live Data"

            if lhm_data:
                cpu_w = self.find_sensor_value(lhm_data, ["Package", "CPU Package"])
                igpu_w = self.calculate_igpu_total(lhm_data)
                if cpu_w == 0: is_estimated = True
            else:
                is_estimated = True
                status_msg = "Estimating (LHM Off)"
                base_load = 45
                gpu_util = 0
                if self.nvml_active:
                    try: gpu_util = pynvml.nvmlDeviceGetUtilizationRates(self.gpu_handle).gpu
                    except: pass
                if gpu_util < 10: cpu_w = 35
                elif gpu_util < 50: cpu_w = 55
                else: cpu_w = 75

            # 2. TOTALS
            overhead = 45
            total_w = dgpu_w + igpu_w + cpu_w + overhead

            # 3. CALCULATE INCREMENTS
            # We assume this loop runs approx once per second
            kwh_inc = (total_w * 1.0) / 3_600_000
            cost_inc = kwh_inc * PRICE_PER_KWH

            # Update Metrics
            self.session_data["kwh"] += kwh_inc
            self.session_data["cost"] += cost_inc
            
            self.persistent_data["day_kwh"] += kwh_inc
            self.persistent_data["day_cost"] += cost_inc
            self.persistent_data["day_seconds"] += 1 # Add 1 second
            
            self.persistent_data["lifetime_kwh"] += kwh_inc
            self.persistent_data["lifetime_cost"] += cost_inc
            self.persistent_data["lifetime_seconds"] += 1 # Add 1 second

            # Daily Rollover Check
            current_date = datetime.now().strftime("%Y-%m-%d")
            if current_date != self.persistent_data["last_date"]:
                self.persistent_data["last_date"] = current_date
                self.persistent_data["day_kwh"] = 0.0
                self.persistent_data["day_cost"] = 0.0
                self.persistent_data["day_seconds"] = 0

            # 4. GUI UPDATE
            try:
                # Update Timer Strings
                sess_str = self.format_time(time.time() - self.start_time)
                day_str = self.format_time(self.persistent_data["day_seconds"])
                life_str = self.format_time(self.persistent_data["lifetime_seconds"])

                self.lbl_sess_title.configure(text=f"Session ({sess_str}):")
                self.lbl_day_title.configure(text=f"Today ({day_str}):")
                self.lbl_life_title.configure(text=f"Overall ({life_str}):")

                # Update Watts & Colors
                self.lbl_watts.configure(text=f"{int(total_w)} W")
                self.lbl_dgpu_val.configure(text=f"{int(dgpu_w)} W")
                self.lbl_igpu_val.configure(text=f"{int(igpu_w)} W")
                self.lbl_cpu_val.configure(text=f"{int(cpu_w)} W")
                self.lbl_status.configure(text=status_msg)

                if total_w > 500: self.lbl_watts.configure(text_color="#FF4444")
                elif total_w > 300: self.lbl_watts.configure(text_color="#FFD700")
                else: self.lbl_watts.configure(text_color="#00E5FF")
                
                color_state = "gray" if is_estimated else "#ff8c00"
                self.lbl_cpu_val.configure(text_color=color_state)

                # Update Stats Numbers
                self.lbl_sess_cost.configure(text=f"{self.session_data['cost']:.4f}")
                self.lbl_sess_kwh.configure(text=f"{self.session_data['kwh']:.4f} kWh")
                self.lbl_day_cost.configure(text=f"{self.persistent_data['day_cost']:.4f}")
                self.lbl_day_kwh.configure(text=f"{self.persistent_data['day_kwh']:.4f} kWh")
                self.lbl_life_cost.configure(text=f"{self.persistent_data['lifetime_cost']:.4f}")
                self.lbl_life_kwh.configure(text=f"{self.persistent_data['lifetime_kwh']:.4f} kWh")

            except: pass

            # 5. SAVE
            if time.time() - last_log > 60:
                self.save_data()
                last_log = time.time()
            time.sleep(1)

    def on_close(self):
        self.running = False
        self.save_data()
        self.destroy()

if __name__ == "__main__":
    app = PowerMonitorApp()
    app.mainloop()