import customtkinter as ctk
import threading
import time
import json
import os
import pynvml
import requests
from datetime import datetime

# --- CONFIGURATION ---
PRICE_PER_KWH = 2.14          # Your electricity rate
STATE_FILE = "power_state.json"
LHM_URL = "http://localhost:8085/data.json"  # LibreHardwareMonitor Web Server

# --- SYSTEM SETUP ---
os.environ["PATH"] += os.pathsep + os.getcwd()

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class PowerMonitorApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        # 1. Window Setup (Made wider for 3 columns)
        self.title("⚡ Power Monitor (Full Spectrum)")
        self.geometry("600x480") 
        self.resizable(False, False)

        # 2. Hardware Init
        self.gpu_handle = None
        self.nvml_active = False
        self.setup_nvml()
        
        self.running = True
        self.power_data = self.load_data()

        # --- UI LAYOUT ---
        # Title
        self.lbl_title = ctk.CTkLabel(self, text="Real-Time Consumption", font=("Roboto", 22, "bold"))
        self.lbl_title.pack(pady=(20, 10))

        # Big Total Watts
        self.lbl_watts = ctk.CTkLabel(self, text="--- W", font=("Roboto", 54, "bold"), text_color="#00E5FF")
        self.lbl_watts.pack(pady=5)
        
        self.lbl_total_sub = ctk.CTkLabel(self, text="Total System Power", font=("Arial", 12), text_color="gray")
        self.lbl_total_sub.pack(pady=(0, 20))

        # --- 3-COLUMN SPLIT VIEW ---
        self.frame_details = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_details.pack(pady=5, padx=10, fill="x")

        # Col 1: Discrete GPU (NVIDIA)
        self.frame_dgpu = ctk.CTkFrame(self.frame_details)
        self.frame_dgpu.pack(side="left", expand=True, fill="both", padx=5)
        ctk.CTkLabel(self.frame_dgpu, text="RTX 4070 Ti", font=("Arial", 11, "bold"), text_color="#76b900").pack(pady=(10,0))
        self.lbl_dgpu_val = ctk.CTkLabel(self.frame_dgpu, text="0 W", font=("Roboto", 22, "bold"))
        self.lbl_dgpu_val.pack(pady=(0, 10))

        # Col 2: Integrated GPU (AMD iGPU)
        self.frame_igpu = ctk.CTkFrame(self.frame_details)
        self.frame_igpu.pack(side="left", expand=True, fill="both", padx=5)
        ctk.CTkLabel(self.frame_igpu, text="iGPU (Radeon)", font=("Arial", 11, "bold"), text_color="#FF3333").pack(pady=(10,0))
        self.lbl_igpu_val = ctk.CTkLabel(self.frame_igpu, text="0 W", font=("Roboto", 22, "bold"))
        self.lbl_igpu_val.pack(pady=(0, 10))

        # Col 3: CPU (Ryzen)
        self.frame_cpu = ctk.CTkFrame(self.frame_details)
        self.frame_cpu.pack(side="right", expand=True, fill="both", padx=5)
        ctk.CTkLabel(self.frame_cpu, text="Ryzen 9900X", font=("Arial", 11, "bold"), text_color="#ff8c00").pack(pady=(10,0))
        self.lbl_cpu_val = ctk.CTkLabel(self.frame_cpu, text="0 W", font=("Roboto", 22, "bold"))
        self.lbl_cpu_val.pack(pady=(0, 10))

        # Cost Section
        self.frame_info = ctk.CTkFrame(self)
        self.frame_info.pack(pady=25, padx=25, fill="x")
        
        self.lbl_cost_title = ctk.CTkLabel(self.frame_info, text="Total Cost (EGP):", font=("Arial", 14))
        self.lbl_cost_title.pack(pady=(10, 0))
        self.lbl_cost_val = ctk.CTkLabel(self.frame_info, text=f"{self.power_data['total_cost']:.4f}", font=("Arial", 30, "bold"), text_color="#00FF00")
        self.lbl_cost_val.pack(pady=(0, 10))

        # Status Bar
        self.lbl_status = ctk.CTkLabel(self, text="Initializing...", text_color="gray", font=("Arial", 11))
        self.lbl_status.pack(side="bottom", pady=8)

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
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f: return json.load(f)
            except: pass
        return {"total_kwh": 0.0, "total_cost": 0.0}

    def save_data(self):
        with open(STATE_FILE, 'w') as f: json.dump(self.power_data, f, indent=4)

    def fetch_lhm_data(self):
        """Gets all sensor data from LibreHardwareMonitor Web API"""
        try:
            response = requests.get(LHM_URL, timeout=0.2)
            if response.status_code == 200:
                return response.json()
        except:
            return None
        return None

    def find_sensor_value(self, node, target_names, sensor_type="Power"):
        """Recursive search for a specific sensor (e.g., 'Package' or 'GPU Power')"""
        # Check current node
        if isinstance(node, dict):
            # Check if this node matches our target sensor
            # Criteria: Type matches (Power) AND Name is in our list AND Value has "W"
            if node.get("Type") == sensor_type: # Only look at Power sensors
                if any(name.lower() in node.get("Text", "").lower() for name in target_names):
                    # Clean "67.7 W" -> 67.7
                    val_str = str(node.get("Value", "0")).split()[0]
                    try: return float(val_str)
                    except: return 0.0
            
            # Recursively check children
            if "Children" in node:
                for child in node["Children"]:
                    result = self.find_sensor_value(child, target_names, sensor_type)
                    if result > 0: return result
        
        # Handle lists (Root)
        elif isinstance(node, list):
            for item in node:
                result = self.find_sensor_value(item, target_names, sensor_type)
                if result > 0: return result
                
        return 0.0

    def find_igpu_power(self, data):
        """Scans for iGPU/Radeon Graphics power"""
        # Strategy: Look for the specific sensor "GPU Power" 
        # But we need to make sure it's NOT the NVIDIA one (if LHM sees it).
        # Usually, LHM lists the iGPU as "AMD Radeon Graphics" or "Generic VGA".
        
        # We search specifically for the "GPU Power" sensor inside the AMD/Generic node
        # This is tricky because "GPU Power" is generic. 
        # We rely on the fact that LHM usually groups iGPU separate from NVIDIA.
        
        # Search for typical iGPU power labels
        return self.find_sensor_value(data, ["GPU Power", "Graphics Power", "GFX Power"])

    def background_monitor(self):
        last_log = time.time()
        
        while self.running:
            # 1. Discrete GPU (NVIDIA Driver)
            dgpu_w = 0
            if self.nvml_active:
                try: dgpu_w = pynvml.nvmlDeviceGetPowerUsage(self.gpu_handle) / 1000.0
                except: pass

            # 2. Fetch LHM Data (CPU + iGPU)
            lhm_data = self.fetch_lhm_data()
            
            cpu_w = 0
            igpu_w = 0
            is_estimated = False

            if lhm_data:
                # Find CPU (Ryzen 9900X)
                cpu_w = self.find_sensor_value(lhm_data, ["Package", "CPU Package"])
                
                # Find iGPU (Radeon)
                # Note: If iGPU is idle, it might show 0 or be hidden.
                # We subtract dGPU power if LHM accidentally picked up the NVIDIA card (unlikely via Web)
                raw_gfx = self.find_sensor_value(lhm_data, ["GPU Power", "GFX Power", "Graphics Power"])
                
                # Logic: If the "Graphics" power found is massive (>100W), it's probably the 4070 Ti 
                # confusing LHM. iGPU should be small (0-50W).
                if raw_gfx < 80: 
                    igpu_w = raw_gfx
                
                status_msg = "Status: Live Data (LHM Connected)"
                
                # If LHM returns 0 for CPU (bug?), fall back
                if cpu_w == 0: is_estimated = True
            else:
                # Fallback Estimate
                is_estimated = True
                status_msg = "Status: Estimating (LHM Disconnected)"
                # Basic estimates
                base_load = 45
                dgpu_util = 0
                if self.nvml_active:
                    try: dgpu_util = pynvml.nvmlDeviceGetUtilizationRates(self.gpu_handle).gpu
                    except: pass
                
                if dgpu_util < 10: cpu_w = 35
                elif dgpu_util < 50: cpu_w = 55
                else: cpu_w = 75
                igpu_w = 5 # Idle guess

            # 3. Total Calculation
            # Add 45W Overhead (Mobo/RAM/Fans/Pump)
            overhead = 45
            total_w = dgpu_w + igpu_w + cpu_w + overhead

            # 4. Cost Math
            kwh_inc = (total_w * 1.0) / 3_600_000
            self.power_data["total_cost"] += kwh_inc * PRICE_PER_KWH

            # 5. Update GUI
            try:
                self.lbl_watts.configure(text=f"{int(total_w)} W")
                self.lbl_dgpu_val.configure(text=f"{int(dgpu_w)} W")
                self.lbl_igpu_val.configure(text=f"{int(igpu_w)} W")
                self.lbl_cpu_val.configure(text=f"{int(cpu_w)} W")
                self.lbl_cost_val.configure(text=f"{self.power_data['total_cost']:.4f}")
                self.lbl_status.configure(text=status_msg)
                
                # Gray out if estimating
                color_state = "gray" if is_estimated else "#ff8c00"
                self.lbl_cpu_val.configure(text_color=color_state)
                
                # Total Color
                if total_w > 500: self.lbl_watts.configure(text_color="#FF4444")
                elif total_w > 300: self.lbl_watts.configure(text_color="#FFD700")
                else: self.lbl_watts.configure(text_color="#00E5FF")
            except: pass

            # 6. Save Data
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