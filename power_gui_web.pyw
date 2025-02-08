import customtkinter as ctk
import threading
import time
import json
import os
import pynvml
import wmi
from datetime import datetime

# --- CONFIG ---
PRICE_PER_KWH = 2.14
STATE_FILE = "power_state.json"
# Force DLL lookup
os.environ["PATH"] += os.pathsep + os.getcwd()

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class PowerMonitorApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("⚡ Power Monitor (Hybrid)")
        self.geometry("450x450")
        self.resizable(False, False)

        # 1. Try to Connect to Real Sensors (LHM)
        self.wmi_client = None
        try:
            # Try connecting to LibreHardwareMonitor WMI
            self.wmi_client = wmi.WMI(namespace=r"root\OpenHardwareMonitor")
            print("✅ Connected to Real CPU Sensors (LibreHardwareMonitor)")
        except Exception as e:
            print(f"⚠️ Could not find LibreHardwareMonitor: {e}")
            print("   -> Switched to ESTIMATION mode for CPU.")
            self.wmi_client = None

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

    def get_real_cpu_power(self):
        """Attempts to read Real CPU Power from LHM"""
        if self.wmi_client is None: return 0
        try:
            sensors = self.wmi_client.Sensor()
            for sensor in sensors:
                if sensor.SensorType == u'Power' and u'Package' in sensor.Name:
                    return float(sensor.Value)
        except:
            return 0
        return 0

    def background_monitor(self):
        last_log = time.time()
        while self.running:
            # 1. GPU Power (Always Accurate)
            gpu_w = 0
            gpu_util = 0
            if self.nvml_active:
                try: 
                    gpu_w = pynvml.nvmlDeviceGetPowerUsage(self.gpu_handle) / 1000.0
                    gpu_util = pynvml.nvmlDeviceGetUtilizationRates(self.gpu_handle).gpu
                except: pass

            # 2. CPU Power (Hybrid Approach)
            real_cpu_w = self.get_real_cpu_power()
            
            if real_cpu_w > 0:
                # SUCCESS: We have real data!
                # We still add ~45W for Mobo/RAM/Fans because "Package" is ONLY the chip
                system_w = real_cpu_w + 45 
                is_estimated = False
            else:
                # FAILURE: LHM is closed. Use Estimate Logic (Calibrated)
                base_draw = 45
                if gpu_util < 10: cpu_est = 35    # Idle
                elif gpu_util < 50: cpu_est = 55  # Medium
                else: cpu_est = 75                # Gaming
                system_w = cpu_est + base_draw
                is_estimated = True

            total_w = gpu_w + system_w

            # 3. Math & GUI Update
            kwh_inc = (total_w * 1.0) / 3_600_000
            self.power_data["total_cost"] += kwh_inc * PRICE_PER_KWH
            
            try:
                self.lbl_watts.configure(text=f"{int(total_w)} W")
                self.lbl_gpu_val.configure(text=f"{int(gpu_w)} W")
                self.lbl_cpu_val.configure(text=f"{int(system_w)} W")
                self.lbl_cost_val.configure(text=f"{self.power_data['total_cost']:.4f}")
                
                # Visual Feedback: Grey out CPU text if we are guessing
                if is_estimated:
                     self.lbl_cpu_val.configure(text_color="gray")
                     self.lbl_status = "Status: Using Estimates (Open LHM for Real Data)"
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