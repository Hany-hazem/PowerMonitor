import customtkinter as ctk
import threading
import time
import json
import os
import sys
import pynvml
from datetime import datetime

# --- CONFIG ---
PRICE_PER_KWH = 2.14
STATE_FILE = "power_state.json"
LOG_FILE = "power_history.csv"

# Force DLL lookup
os.environ["PATH"] += os.pathsep + os.getcwd()

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class PowerMonitorApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Window Setup
        self.title("⚡ Power Monitor Pro")
        self.geometry("450x450") # Made slightly taller for new info
        self.resizable(False, False)

        # Data State
        self.running = True
        self.power_data = self.load_data()
        self.gpu_handle = None
        self.nvml_active = False
        self.setup_nvml()

        # --- UI LAYOUT ---
        # 1. Title
        self.lbl_title = ctk.CTkLabel(self, text="Real-Time Consumption", font=("Roboto", 20, "bold"))
        self.lbl_title.pack(pady=(20, 10))

        # 2. The Big "Total" Number
        self.lbl_watts = ctk.CTkLabel(self, text="0 W", font=("Roboto", 50, "bold"), text_color="#00E5FF")
        self.lbl_watts.pack(pady=5)
        
        self.lbl_total_sub = ctk.CTkLabel(self, text="Total System Power", font=("Arial", 12), text_color="gray")
        self.lbl_total_sub.pack(pady=(0, 20))

        # 3. SPLIT VIEW (GPU vs CPU)
        self.frame_details = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_details.pack(pady=5, padx=20, fill="x")

        # -- Left Column (GPU) --
        self.frame_gpu = ctk.CTkFrame(self.frame_details)
        self.frame_gpu.pack(side="left", expand=True, fill="both", padx=5)
        
        ctk.CTkLabel(self.frame_gpu, text="GPU (NVIDIA)", font=("Arial", 12, "bold"), text_color="#76b900").pack(pady=(10,0))
        self.lbl_gpu_val = ctk.CTkLabel(self.frame_gpu, text="0 W", font=("Roboto", 24, "bold"))
        self.lbl_gpu_val.pack(pady=(0, 10))

        # -- Right Column (CPU) --
        self.frame_cpu = ctk.CTkFrame(self.frame_details)
        self.frame_cpu.pack(side="right", expand=True, fill="both", padx=5)
        
        ctk.CTkLabel(self.frame_cpu, text="CPU + Sys", font=("Arial", 12, "bold"), text_color="#ff8c00").pack(pady=(10,0))
        self.lbl_cpu_val = ctk.CTkLabel(self.frame_cpu, text="0 W", font=("Roboto", 24, "bold"))
        self.lbl_cpu_val.pack(pady=(0, 10))

        # 4. Cost Section
        self.frame_info = ctk.CTkFrame(self)
        self.frame_info.pack(pady=20, padx=20, fill="x")

        self.lbl_cost_title = ctk.CTkLabel(self.frame_info, text="Total Cost (EGP):", font=("Arial", 14))
        self.lbl_cost_title.pack(pady=(10, 0))
        
        self.lbl_cost_val = ctk.CTkLabel(self.frame_info, text=f"{self.power_data['total_cost']:.4f}", font=("Arial", 28, "bold"), text_color="#00FF00")
        self.lbl_cost_val.pack(pady=(0, 10))

        # 5. Status Bar
        self.lbl_status = ctk.CTkLabel(self, text="Status: Monitoring Active", text_color="gray", font=("Arial", 10))
        self.lbl_status.pack(side="bottom", pady=5)

        # Threading
        self.monitor_thread = threading.Thread(target=self.background_monitor, daemon=True)
        self.monitor_thread.start()

        # Handle Window Close
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def setup_nvml(self):
        try:
            pynvml.nvmlInit()
            self.gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            self.nvml_active = True
            
            # Safe Decode
            raw_name = pynvml.nvmlDeviceGetName(self.gpu_handle)
            name = raw_name.decode() if isinstance(raw_name, bytes) else raw_name
            print(f"GPU Found: {name}")
            
        except Exception as e:
            print(f"NVML Error: {e}")
            self.nvml_active = False

    def load_data(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f: return json.load(f)
            except: pass
        return {"total_kwh": 0.0, "total_cost": 0.0}

    def save_data(self):
        with open(STATE_FILE, 'w') as f:
            json.dump(self.power_data, f, indent=4)

    def background_monitor(self):
        last_log = time.time()
        
        while self.running:
            # 1. Get Power Data
            gpu_w = 0
            if self.nvml_active:
                try:
                    gpu_w = pynvml.nvmlDeviceGetPowerUsage(self.gpu_handle) / 1000.0
                    util = pynvml.nvmlDeviceGetUtilizationRates(self.gpu_handle).gpu
                except: util = 0
            else: util = 0

            # Logic: CPU + Motherboard + RAM + Fans + AIO Pump
            base_draw = 65 
            cpu_est = 35 if util < 10 else (60 if util < 50 else 100)
            
            # Combine System (CPU + RAM + Mobo)
            system_w = cpu_est + base_draw
            total_w = gpu_w + system_w

            # 2. Math
            kwh_inc = (total_w * 1.0) / 3_600_000
            cost_inc = kwh_inc * PRICE_PER_KWH
            
            self.power_data["total_kwh"] += kwh_inc
            self.power_data["total_cost"] += cost_inc

            # 3. Update GUI
            try:
                # Update Big Total
                self.lbl_watts.configure(text=f"{int(total_w)} W")
                
                # Update Split Numbers
                self.lbl_gpu_val.configure(text=f"{int(gpu_w)} W")
                self.lbl_cpu_val.configure(text=f"{int(system_w)} W")
                
                # Update Cost
                self.lbl_cost_val.configure(text=f"{self.power_data['total_cost']:.4f}")
                
                # Dynamic Colors for Total Load
                color = "#00E5FF" # Cyan (Safe)
                if total_w > 300: color = "#FFD700" # Gold (Gaming)
                if total_w > 500: color = "#FF4444" # Red (Heavy)
                self.lbl_watts.configure(text_color=color)

            except:
                pass 

            # 4. Save Periodically
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