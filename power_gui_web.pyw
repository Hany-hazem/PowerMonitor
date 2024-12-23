import time
import os
import pynvml

# --- CONFIGURATION ---
# Egyptian Electricity Prices (Approximate Tier 6/7 rates, adjust as needed)
PRICE_PER_KWH = 2.14  # EGP
POLLING_INTERVAL = 1.0  # Seconds

class SystemPowerMonitor:
    def __init__(self):
        self.pynvml_init = False
        try:
            pynvml.nvmlInit()
            self.pynvml_init = True
            self.gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            print(f"✅ GPU Found: {pynvml.nvmlDeviceGetName(self.gpu_handle).decode()}")
        except Exception as e:
            print(f"⚠️  NVIDIA Driver not found: {e}")

        # Find CPU Power Path (RAPL)
        # On modern Linux, AMD Ryzen sensors often hide in /sys/class/powercap/intel-rapl
        self.cpu_rapl_path = None
        base_path = "/sys/class/powercap"
        if os.path.exists(base_path):
            for dir_name in os.listdir(base_path):
                # We are looking for the "package" energy counter
                if "intel-rapl" in dir_name and ":" in dir_name:
                    path = os.path.join(base_path, dir_name, "energy_uj")
                    if os.path.exists(path):
                        self.cpu_rapl_path = path
                        print(f"✅ CPU Sensors Found at: {path}")
                        break

    def get_gpu_watts(self):
        if not self.pynvml_init: return 0
        try:
            # Returns milliwatts
            return pynvml.nvmlDeviceGetPowerUsage(self.gpu_handle) / 1000.0
        except:
            return 0

    def get_cpu_watts(self, last_energy, last_time):
        if not self.cpu_rapl_path: return 0, 0, 0
        
        try:
            with open(self.cpu_rapl_path, 'r') as f:
                current_energy = int(f.read()) # Microjoules
            
            current_time = time.time()
            
            if last_energy == 0:
                return 0, current_energy, current_time

            # Watts = Joules / Seconds
            energy_delta = (current_energy - last_energy) / 1_000_000 # Convert uJ to J
            time_delta = current_time - last_time
            
            watts = energy_delta / time_delta
            return watts, current_energy, current_time
        except:
            return 0, 0, 0

    def run(self):
        print("\n⚡ Starting Power Monitor... (Press Ctrl+C to stop)")
        print(f"{'GPU (W)':<10} | {'CPU (W)':<10} | {'TOTAL (W)':<10} | {'COST (EGP)':<10}")
        print("-" * 50)

        total_kwh_accumulated = 0
        
        # CPU Init values
        c_watts, last_energy, last_time = self.get_cpu_watts(0, 0)
        time.sleep(0.1) # Brief pause to get first delta

        try:
            while True:
                gpu_watts = self.get_gpu_watts()
                cpu_watts, last_energy, last_time = self.get_cpu_watts(last_energy, last_time)
                
                # Estimated extra for RAM/Motherboard/Fans (approx 50W for your high-end build)
                system_overhead = 50 
                total_watts = gpu_watts + cpu_watts + system_overhead

                # Cost Calculation
                # kWh = (Watts * Seconds) / (1000 * 3600)
                # Since we loop every POLLING_INTERVAL seconds:
                kwh_increment = (total_watts * POLLING_INTERVAL) / 3_600_000
                total_kwh_accumulated += kwh_increment
                total_cost = total_kwh_accumulated * PRICE_PER_KWH

                print(f"{gpu_watts:<10.2f} | {cpu_watts:<10.2f} | {total_watts:<10.2f} | {total_cost:<10.4f}\r", end="")
                time.sleep(POLLING_INTERVAL)

        except KeyboardInterrupt:
            print(f"\n\n🛑 Final Cost: {total_cost:.4f} EGP")

if __name__ == "__main__":
    monitor = SystemPowerMonitor()
    monitor.run()