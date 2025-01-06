import time
import json
import csv
import os
import pynvml
from datetime import datetime
from colorama import init, Fore, Style

# --- CONFIGURATION ---
PRICE_PER_KWH = 2.14  # EGP
STATE_FILE = "power_state.json"
LOG_FILE = "power_history.csv"
POLL_INTERVAL = 1.0   # Seconds between updates
LOG_INTERVAL = 60     # Seconds between writing to CSV

init() # Init colors

class PersistentMonitor:
    def __init__(self):
        self.gpu_handle = None
        self.nvml_active = False
        self.setup_nvml()
        
        # Load previous data or start fresh
        self.state = self.load_state()
        
        # Setup CSV Logging
        self.setup_logging()

    def setup_nvml(self):
        try:
            pynvml.nvmlInit()
            self.gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            self.nvml_active = True
            print(f"{Fore.GREEN}✅ GPU Linked: {pynvml.nvmlDeviceGetName(self.gpu_handle).decode()}{Style.RESET_ALL}")
        except:
            print(f"{Fore.RED}⚠️ GPU Driver not found. using defaults.{Style.RESET_ALL}")

    def load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    print(f"{Fore.CYAN}📂 Loaded previous session data.{Style.RESET_ALL}")
                    return json.load(f)
            except:
                pass
        return {"total_kwh": 0.0, "total_cost": 0.0, "start_date": str(datetime.now())}

    def save_state(self):
        with open(STATE_FILE, 'w') as f:
            json.dump(self.state, f, indent=4)

    def setup_logging(self):
        if not os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(["Timestamp", "GPU_Watts", "Est_Total_Watts", "Session_Cost"])

    def log_to_csv(self, gpu_w, total_w):
        with open(LOG_FILE, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                round(gpu_w, 2),
                round(total_w, 2),
                round(self.state["total_cost"], 4)
            ])

    def get_power_data(self):
        gpu_watts = 0
        gpu_util = 0
        
        if self.nvml_active:
            try:
                gpu_watts = pynvml.nvmlDeviceGetPowerUsage(self.gpu_handle) / 1000.0
                gpu_util = pynvml.nvmlDeviceGetUtilizationRates(self.gpu_handle).gpu
            except: pass

        # --- ESTIMATION LOGIC (Refined for Ryzen 9900X + 4070 Ti Super) ---
        # Base system (Mobo X870 + RAM + Fans + AIO Pump + SSDs) ~ 65W
        base_draw = 65 
        
        # CPU Estimation based on GPU load (Heuristic)
        # If GPU is 0-10% (Desktop), CPU likely idle (~35W)
        # If GPU is 90%+ (Gaming), CPU likely gaming load (~80-100W)
        if gpu_util < 10: cpu_est = 35
        elif gpu_util < 50: cpu_est = 60
        else: cpu_est = 100 
        
        total_watts = gpu_watts + cpu_est + base_draw
        return gpu_watts, total_watts, gpu_util

    def run(self):
        print(f"\n{Fore.YELLOW}⚡ Persistent Power Monitor V2 ⚡{Style.RESET_ALL}")
        print(f"Tracking Cost at: {PRICE_PER_KWH} EGP/kWh")
        print(f"Accumulated Cost so far: {Fore.GREEN}{self.state['total_cost']:.2f} EGP{Style.RESET_ALL}\n")

        last_log_time = time.time()
        
        try:
            while True:
                gpu_w, total_w, gpu_util = self.get_power_data()
                
                # Math: Calculate kWh for this specific 1-second interval
                # kWh = (Watts * Seconds) / (3,600,000)
                kwh_increment = (total_w * POLL_INTERVAL) / 3_600_000
                cost_increment = kwh_increment * PRICE_PER_KWH
                
                # Update State
                self.state["total_kwh"] += kwh_increment
                self.state["total_cost"] += cost_increment
                
                # Visual Output
                color = Fore.WHITE
                if total_w > 300: color = Fore.YELLOW
                if total_w > 500: color = Fore.RED

                print(
                    f"\rGPU: {gpu_w:5.1f}W ({gpu_util}%) | "
                    f"{color}Total: {total_w:5.0f}W{Style.RESET_ALL} | "
                    f"Session Cost: {Fore.GREEN}{self.state['total_cost']:8.4f} EGP{Style.RESET_ALL} ",
                    end=""
                )

                # Periodic Tasks (Save & Log)
                current_time = time.time()
                if current_time - last_log_time >= LOG_INTERVAL:
                    self.save_state() # Save JSON
                    self.log_to_csv(gpu_w, total_w) # Write CSV
                    last_log_time = current_time

                time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            self.save_state()
            print(f"\n\n{Fore.CYAN}💾 Data saved! Final Total: {self.state['total_cost']:.4f} EGP{Style.RESET_ALL}")

if __name__ == "__main__":
    app = PersistentMonitor()
    app.run()