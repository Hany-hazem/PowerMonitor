import time
import pynvml
from colorama import init, Fore, Style

# Initialize color output for Windows terminal
init()

class WindowsPowerMonitor:
    def __init__(self):
        # 1. Setup NVIDIA GPU Monitoring
        self.pynvml_init = False
        try:
            pynvml.nvmlInit()
            self.pynvml_init = True
            self.gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            print(f"{Fore.GREEN}✅ GPU Detected: {pynvml.nvmlDeviceGetName(self.gpu_handle).decode()}{Style.RESET_ALL}")
        except:
            print(f"{Fore.RED}❌ NVIDIA Driver not found.{Style.RESET_ALL}")

    def get_gpu_data(self):
        if not self.pynvml_init: return 0, 0
        try:
            # Power is in milliwatts
            watts = pynvml.nvmlDeviceGetPowerUsage(self.gpu_handle) / 1000.0
            # Usage is in percentage
            util = pynvml.nvmlDeviceGetUtilizationRates(self.gpu_handle).gpu
            return watts, util
        except:
            return 0, 0

    def estimate_total_power(self, gpu_watts):
        # On Windows, reading AMD Ryzen 'Package Power' via Python requires 
        # complex C# wrappers (LibreHardwareMonitor). 
        # For this script, we will estimate CPU power based on state.
        
        # Base system idle (Fans, RAM, NVMe, Mobo) approx 60W for your rig
        base_system = 60 
        
        # Rough estimation: If GPU is working hard, CPU is likely working too.
        # This is a heuristic since we can't read the sensor directly without a driver.
        estimated_cpu = 40 # Idle
        if gpu_watts > 100: estimated_cpu = 100 # Gaming load
        if gpu_watts > 200: estimated_cpu = 150 # Heavy load
        
        return gpu_watts + estimated_cpu + base_system

    def run(self):
        print(f"\n{Fore.CYAN}--- Windows Power Monitor (GPU Accurate / System Est.) ---{Style.RESET_ALL}")
        print(f"Price: 2.14 EGP/kWh\n")
        
        try:
            while True:
                gpu_watts, gpu_util = self.get_gpu_data()
                total_watts = self.estimate_total_power(gpu_watts)
                
                # Cost Calculation
                # Cost per hour = (Watts / 1000) * Price
                cost_per_hour = (total_watts / 1000) * 2.14
                
                # Dynamic Color for Load
                color = Fore.GREEN
                if total_watts > 300: color = Fore.YELLOW
                if total_watts > 500: color = Fore.RED

                output = (
                    f"GPU Load: {gpu_util}% | "
                    f"GPU Power: {gpu_watts:.1f}W | "
                    f"{color}Est. Total: {total_watts:.0f}W{Style.RESET_ALL} | "
                    f"Cost: {cost_per_hour:.2f} EGP/hr"
                )
                
                print(output, end="\r")
                time.sleep(1)
                
        except KeyboardInterrupt:
            print("\nStopped.")

if __name__ == "__main__":
    monitor = WindowsPowerMonitor()
    monitor.run()