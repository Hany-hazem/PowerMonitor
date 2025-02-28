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
LHM_URL = "http://localhost:8085/data.json"  # Default LibreHardwareMonitor URL

# --- SYSTEM SETUP ---
# Force Python to look in the script directory for nvml.dll (Fixes Driver Not Found)
os.environ["PATH"] += os.pathsep + os.getcwd()

# GUI Appearance Settings
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class PowerMonitorApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        # 1. Window Setup
        self.title("⚡ Power Monitor (Web API)")
        self.geometry("460x480")
        self.resizable(False, False)

        # 2. Initialize Hardware & Data
        self.gpu_handle = None
        self.nvml_active = False
        self.setup_nvml()
        
        self.running = True
        self.power_data = self.load_data()

        # --- UI LAYOUT ---
        # Title
        self.lbl_title = ctk.CTkLabel(self, text="Real-Time Consumption", font=("Roboto", 22, "bold"))
        self.lbl_title.pack(pady=(25, 10))

        # Big Total Watts
        self.lbl_watts = ctk.CTkLabel(self, text="--- W", font=("Roboto", 54, "bold"), text_color="#00E5FF")
        self.lbl_watts.pack(pady=5)
        
        self.lbl_total_sub = ctk.CTkLabel(self, text="Total System Power", font=("Arial", 12), text_color="gray")
        self.lbl_total_sub.pack(pady=(0, 25))

        # Split View: GPU vs CPU
        self.frame_details = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_details.pack(pady=5, padx=20, fill="x")

        # -- GPU Section (Left) --
        self.frame_gpu = ctk.CTkFrame(self.frame_details)
        self.frame_gpu.pack(side="left", expand=True, fill="both", padx=8)
        
        ctk.CTkLabel(self.frame_gpu, text="GPU (NVIDIA)", font=("Arial", 12, "bold"), text_color="#76b900").pack(pady=(12,0))
        self.lbl_gpu_val = ctk.CTkLabel(self.frame_gpu, text="0 W", font=("Roboto", 26, "bold"))
        self.lbl_gpu_val.pack(pady=(0, 12))

        # -- CPU Section (Right) --
        self.frame_cpu = ctk.CTkFrame(self.frame_details)
        self.frame_cpu.pack(side="right", expand=True, fill="both", padx=8)
        
        ctk.CTkLabel(self.frame_cpu, text="CPU + Sys", font=("Arial", 12, "bold"), text_color="#ff8c00").pack(pady=(12,0))
        self.lbl_cpu_val = ctk.CTkLabel(self.frame_cpu, text="0 W", font=("Roboto", 26, "bold"))
        self.lbl_cpu_val.pack(pady=(0, 12))

        # Cost Section
        self.frame_info = ctk.CTkFrame(self)
        self.frame_info.pack(pady=25, padx=25, fill="x")
        
        self.lbl_cost_title = ctk.CTkLabel(self.frame_info, text="Total Cost (EGP):", font=("Arial", 14))
        self.lbl_cost_title.pack(pady=(12, 0))
        
        self.lbl_cost_val = ctk.CTkLabel(self.frame_info, text=f"{self.power_data['total_cost']:.4f}", font=("Arial", 30, "bold"), text_color="#00FF00")
        self.lbl_cost_val.pack(pady=(0, 12))

        # Status Bar
        self.lbl_status = ctk.CTkLabel(self, text="Initializing...", text_color="gray", font=("Arial", 11))
        self.lbl_status.pack(side="bottom", pady=8)

        # 3. Start Background Thread
        self.monitor_thread = threading.Thread(target=self.background_monitor, daemon=True)
        self.monitor_thread.start()

        # Handle Closing
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def setup_nvml(self):
        """Initializes NVIDIA Driver connection"""
        try:
            pynvml.nvmlInit()
            self.gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            self.nvml_active = True
            
            # Safe name decoding
            raw_name = pynvml.nvmlDeviceGetName(self.gpu_handle)
            name = raw_name.decode() if isinstance(raw_name, bytes) else raw_name
            print(f"✅ GPU Linked: {name}")
        except Exception as e:
            print(f"⚠️ GPU Driver Error: {e}")
            self.nvml_active = False

    def load_data(self):
        """Loads previous session cost"""
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f: return json.load(f)
            except: pass
        return {"total_kwh": 0.0, "total_cost": 0.0}

    def save_data(self):
        """Saves current session cost"""
        with open(STATE_FILE, 'w') as f:
            json.dump(self.power_data, f, indent=4)

    def get_cpu_power_from_web(self):
        """Fetches JSON from LibreHardwareMonitor and finds CPU Package Power"""
        try:
            # Short timeout to prevent freezing if LHM is closed
            response = requests.get(LHM_URL, timeout=0.2)
            if response.status_code == 200:
                data = response.json()
                return self.find_power_value(data)
        except:
            return 0 # Connection failed
        return 0

    def find_power_value(self, node):
        """Recursive search for 'CPU Package' in the JSON tree"""
        # 1. Check current node
        if isinstance(node, dict):
            # Look for "CPU Package" (Intel/AMD standard label in LHM)
            if node.get("Text") == "Package" and "W" in str(node.get("Value", "")):
                # Clean string "67.7 W" -> 67.7
                val_str = node.get("Value", "0").split()[0]
                return float(val_str)
            
            # 2. Check Children
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
        """Main Loop: Reads sensors -> Calculates Cost -> Updates UI"""
        last_log = time.time()
        
        while self.running:
            # --- 1. GET SENSOR DATA ---
            
            # A. GPU Power
            gpu_w = 0
            if self.nvml_active:
                try: 
                    gpu_w = pynvml.nvmlDeviceGetPowerUsage(self.gpu_handle) / 1000.0
                except: pass

            # B. CPU Power (Web API)
            real_cpu_w = self.get_cpu_power_from_web()
            
            # Logic: Switch between Real Data and Estimation
            if real_cpu_w > 0:
                # SUCCESS: We have real data from LHM!
                # Add 45W for Motherboard + RAM + Fans + Pump overhead
                system_w = real_cpu_w + 45 
                status_msg = "Status: Live Data (LHM Connected)"
                is_estimated = False
            else:
                # FAIL: LHM is closed. Use fallback estimate.
                # Estimate based on GPU usage (if GPU is busy, CPU probably is too)
                gpu_util = 0
                if self.nvml_active:
                    try: gpu_util = pynvml.nvmlDeviceGetUtilizationRates(self.gpu_handle).gpu
                    except: pass
                
                base_draw = 45
                if gpu_util < 10: cpu_est = 35    # Idle
                elif gpu_util < 50: cpu_est = 55  # Medium
                else: cpu_est = 75                # Heavy
                
                system_w = cpu_est + base_draw
                status_msg = "Status: Estimating (Open LHM for Accuracy)"
                is_estimated = True

            total_w = gpu_w + system_w

            # --- 2. CALCULATE COST ---
            # kWh = (Watts * Seconds) / 3,600,000
            kwh_inc = (total_w * 1.0) / 3_600_000
            self.power_data["total_cost"] += kwh_inc * PRICE_PER_KWH
            
            # --- 3. UPDATE GUI ---
            try:
                self.lbl_watts.configure(text=f"{int(total_w)} W")
                self.lbl_gpu_val.configure(text=f"{int(gpu_w)} W")
                self.lbl_cpu_val.configure(text=f"{int(system_w)} W")
                self.lbl_cost_val.configure(text=f"{self.power_data['total_cost']:.4f}")
                self.lbl_status.configure(text=status_msg)
                
                # Visual Feedback
                if is_estimated:
                     self.lbl_cpu_val.configure(text_color="gray")
                else:
                     self.lbl_cpu_val.configure(text_color="#ff8c00") # Orange for CPU
                     
                # Dynamic Total Color
                if total_w > 500: self.lbl_watts.configure(text_color="#FF4444") # Red
                elif total_w > 300: self.lbl_watts.configure(text_color="#FFD700") # Gold
                else: self.lbl_watts.configure(text_color="#00E5FF") # Cyan

            except: pass

            # --- 4. AUTO-SAVE ---
            # Save every 60 seconds
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