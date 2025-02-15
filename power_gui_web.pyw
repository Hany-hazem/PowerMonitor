import customtkinter as ctk
import threading
import time
import json
import os
import pynvml
import requests  # <--- NEW: Uses Web Requests instead of WMI
from datetime import datetime

# --- CONFIG ---
PRICE_PER_KWH = 2.14
STATE_FILE = "power_state.json"
LHM_URL = "http://localhost:8085/data.json"  # Default LHM Port

# Force DLL lookup
os.environ["PATH"] += os.pathsep + os.getcwd()

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class PowerMonitorApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("⚡ Power Monitor (Web API)")
        self.geometry("450x450")
        self.resizable(False, False)

        # Hardware Setup
        self.gpu_handle = None
        self.nvml_active = False
        self.setup_nvml()
        
        # Data
        self.running = True
        self.power_data = self.load_data()

        # --- UI LAYOUT ---
        self.lbl_title = ctk.CTkLabel(self, text="Real-Time Consumption", font=("Roboto", 20, "bold"))
        self.lbl_title.pack(pady=(20, 10))

        self.lbl_watts = ctk.CTkLabel(self, text="0 W", font=("Roboto", 50, "bold"), text_color="#00E5FF")
        self.lbl_watts.pack(pady=5)
        
        self.lbl_total_sub = ctk.CTkLabel(self, text="Total System Power", font=("Arial", 12), text_color="gray")
        self.lbl_total_sub.pack(pady=(0, 20))

        self.frame_details = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_details.pack(pady=5, padx=20, fill="x")

        # GPU
        self.frame_gpu = ctk.CTkFrame(self.frame_details)
        self.frame_gpu.pack(side="left", expand=True, fill="both", padx=5)
        ctk.CTkLabel(self.frame_gpu, text="GPU (NVIDIA)", font=("Arial", 12, "bold"), text_color="#76b900").pack(pady=(10,0))
        self.lbl_gpu_val = ctk.CTkLabel(self.frame_gpu, text="0 W", font=("Roboto", 24, "bold"))
        self.lbl_gpu_val.pack(pady=(0, 10))

        # CPU
        self.frame_cpu = ctk.CTkFrame(self.frame_details)
        self.frame_cpu.pack(side="right", expand=True, fill="both", padx=5)
        ctk.CTkLabel(self.frame_cpu, text="CPU + Sys", font=("Arial", 12, "bold"), text_color="#ff8c00").pack(pady=(10,0))
        self.lbl_cpu_val = ctk.CTkLabel(self.frame_cpu, text="0 W", font=("Roboto", 24, "bold"))
        self.lbl_cpu_val.pack(pady=(0, 10))

        # Cost
        self.frame_info = ctk.CTkFrame(self)
        self.frame_info.pack(pady=20, padx=20, fill="x")
        self.lbl_cost_title = ctk.CTkLabel(self.frame_info, text="Total Cost (EGP):", font=("Arial", 14))
        self.lbl_cost_title.pack(pady=(10, 0))
        self.lbl_cost_val = ctk.CTkLabel(self.frame_info, text=f"{self.power_data['total_cost']:.4f}", font=("Arial", 28, "bold"), text_color="#00FF00")
        self.lbl_cost_val.pack(pady=(0, 10))

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

    def get_cpu_power_from_web(self):
        """Fetches JSON from LHM Web Server and finds CPU Package Power"""
        try:
            response = requests.get(LHM_URL, timeout=0.5)
            if response.status_code == 200:
                data = response.json()
                # Traverse the JSON tree to find the CPU Package Power
                # The structure is: Children -> Children -> ...
                # We write a recursive search for "CPU Package"
                return self.find_power_value(data)
        except:
            return 0
        return 0

    def find_power_value(self, node):
        """Recursive search for CPU Package Power"""
        # 1. Check if this node is the one we want
        if isinstance(node, dict):
            if node.get("Text") == "CPU Package" and "W" in str(node.get("Value", "")):
                # Clean string "35.5 W" -> 35.5
                val_str = node.get("Value", "0").split()[0]
                return float(val_str)
            
            # 2. Search Children
            if "Children" in node:
                for child in node["Children"]:
                    result = self.find_power_value(child)
                    if result > 0: return result
        
        # 3. Handle Lists (Root is a list)
        elif isinstance(node, list):
            for item in node:
                result = self.find_power_value(item)
                if result > 0: return result
                
        return 0

    def background_monitor(self):
        last_log = time.time()
        while self.running:
            # 1. GPU Power
            gpu_w = 0
            if self.nvml_active:
                try: 
                    gpu_w = pynvml.nvmlDeviceGetPowerUsage(self.gpu_handle) / 1000.0
                except: pass

            # 2. CPU Power (Web API)
            real_cpu_w = self.get_cpu_power_from_web()
            
            # Logic: If Web API works, use it. If not, fallback.
            if real_cpu_w > 0:
                system_w = real_cpu_w + 45 # Add Mobo/RAM/Fan Overhead
                is_estimated = False
            else:
                # Fallback to estimate if LHM is closed
                system_w = 115 
                is_estimated = True

            total_w = gpu_w + system_w

            # 3. Math & Update
            kwh_inc = (total_w * 1.0) / 3_600_000
            self.power_data["total_cost"] += kwh_inc * PRICE_PER_KWH
            
            try:
                self.lbl_watts.configure(text=f"{int(total_w)} W")
                self.lbl_gpu_val.configure(text=f"{int(gpu_w)} W")
                self.lbl_cpu_val.configure(text=f"{int(system_w)} W")
                self.lbl_cost_val.configure(text=f"{self.power_data['total_cost']:.4f}")
                
                if is_estimated:
                     self.lbl_cpu_val.configure(text_color="gray")
                else:
                     self.lbl_cpu_val.configure(text_color="#ff8c00")
            except: pass

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