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

# Force DLL lookup (Fixes your driver issue)
os.environ["PATH"] += os.pathsep + os.getcwd()

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class PowerMonitorApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Window Setup
        self.title("⚡ Power Monitor")
        self.geometry("400x350")
        self.resizable(False, False)

        # Data State
        self.running = True
        self.state = self.load_state()
        self.gpu_handle = None
        self.nvml_active = False
        self.setup_nvml()

        # --- UI LAYOUT ---
        # Title
        self.lbl_title = ctk.CTkLabel(self, text="Real-Time Consumption", font=("Roboto", 20, "bold"))
        self.lbl_title.pack(pady=15)

        # Watts Display (The Big Number)
        self.lbl_watts = ctk.CTkLabel(self, text="0 W", font=("Roboto", 48, "bold"), text_color="#00E5FF")
        self.lbl_watts.pack(pady=5)

        # Cost Display
        self.frame_info = ctk.CTkFrame(self)
        self.frame_info.pack(pady=20, padx=20, fill="x")

        self.lbl_cost_title = ctk.CTkLabel(self.frame_info, text="Total Cost (EGP):", font=("Arial", 14))
        self.lbl_cost_title.pack(pady=(10, 0))
        
        self.lbl_cost_val = ctk.CTkLabel(self.frame_info, text=f"{self.state['total_cost']:.2f}", font=("Arial", 24, "bold"), text_color="#00FF00")
        self.lbl_cost_val.pack(pady=(0, 10))

        # Status Bar
        self.lbl_status = ctk.CTkLabel(self, text="Status: Monitoring Active", text_color="gray")
        self.lbl_status.pack(side="bottom", pady=10)

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
        except:
            self.nvml_active = False

    def load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f: return json.load(f)
            except: pass
        return {"total_kwh": 0.0, "total_cost": 0.0}

    def save_state(self):
        with open(STATE_FILE, 'w') as f:
            json.dump(self.state, f, indent=4)

    def background_monitor(self):
        last_log = time.time()
        while self.running:
            # 1. Get Power
            gpu_w = 0
            if self.nvml_active:
                try:
                    gpu_w = pynvml.nvmlDeviceGetPowerUsage(self.gpu_handle) / 1000.0
                    util = pynvml.nvmlDeviceGetUtilizationRates(self.gpu_handle).gpu
                except: util = 0
            else: util = 0

            # Estimate CPU/System
            cpu_est = 35 if util < 10 else (60 if util < 50 else 100)
            base_draw = 65
            total_w = gpu_w + cpu_est + base_draw

            # 2. Math
            kwh_inc = (total_w * 1.0) / 3_600_000
            cost_inc = kwh_inc * PRICE_PER_KWH
            
            self.state["total_kwh"] += kwh_inc
            self.state["total_cost"] += cost_inc

            # 3. Update GUI (Safe way)
            self.lbl_watts.configure(text=f"{int(total_w)} W")
            self.lbl_cost_val.configure(text=f"{self.state['total_cost']:.4f}")
            
            # Color logic
            color = "#00E5FF" # Cyan
            if total_w > 300: color = "#FFD700" # Gold
            if total_w > 500: color = "#FF4444" # Red
            self.lbl_watts.configure(text_color=color)

            # 4. Save/Log periodically
            if time.time() - last_log > 60:
                self.save_state()
                last_log = time.time()

            time.sleep(1)

    def on_close(self):
        self.running = False
        self.save_state()
        self.destroy()

if __name__ == "__main__":
    app = PowerMonitorApp()
    app.mainloop()