import customtkinter as ctk
import threading
import time
import json
import os
import csv
import pynvml
import requests
from datetime import datetime

# --- CONFIGURATION ---
PRICE_PER_KWH = 2.14          
DAILY_LIMIT_EGP = 10.00       
STATE_FILE = "power_state.json"
LOG_FILE = "power_log.csv"
LHM_URL = "http://localhost:8085/data.json"

# --- THEME COLORS ---
COLOR_BG = "#1a1a1a"
COLOR_CARD = "#2b2b2b"
COLOR_TEXT_MAIN = "#ffffff"
COLOR_TEXT_SUB = "#a0a0a0"
COLOR_ACCENT = "#00E5FF"    # Cyan
COLOR_WARN = "#FFD700"      # Gold
COLOR_CRIT = "#FF4444"      # Red

# --- ESTIMATED TDP (For Progress Bars) ---
# Adjust these if you want the bars to fill differently
TDP_NVIDIA_HIGH = 285 # RTX 4070 Ti estimate
TDP_NVIDIA_MID = 120  # GTX 1660 Ti estimate
TDP_CPU = 170         # Ryzen 9900X PPT
TDP_IGPU = 60         # iGPU estimate

# --- SYSTEM SETUP ---
os.environ["PATH"] += os.pathsep + os.getcwd()
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class PowerMonitorApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        # 1. Hardware Init
        self.gpu_data = [] 
        self.nvml_active = False
        self.setup_nvml()
        
        # Window Calculation
        extra_width = max(0, (len(self.gpu_data) - 1) * 160) 
        window_width = 800 + extra_width
        
        self.title("⚡ Power Monitor (Visualizer)")
        self.geometry(f"{window_width}x660") # Slightly shorter (Compact)
        self.configure(fg_color=COLOR_BG)
        self.resizable(True, True)

        # 2. Data Init
        self.running = True
        self.start_time = time.time()
        self.peak_w = 0 # Track max spike
        self.session_data = {"kwh": 0.0, "cost": 0.0}
        self.persistent_data = self.load_data()
        self.save_data()
        self.init_csv()

        # --- UI LAYOUT ---
        
        # A. HEADER (Total + Peak)
        self.frame_header = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_header.pack(pady=(15, 5), fill="x")
        
        self.lbl_title = ctk.CTkLabel(self.frame_header, text="SYSTEM POWER DRAW", font=("Roboto Medium", 12), text_color=COLOR_TEXT_SUB)
        self.lbl_title.pack(pady=(0, 0))

        self.lbl_watts = ctk.CTkLabel(self.frame_header, text="--- W", font=("Roboto", 64, "bold"), text_color=COLOR_ACCENT)
        self.lbl_watts.pack(pady=0)
        
        self.lbl_peak = ctk.CTkLabel(self.frame_header, text="Peak: 0 W", font=("Arial", 11), text_color="gray")
        self.lbl_peak.pack(pady=(0, 0))

        # B. HARDWARE CARDS (Grid)
        self.frame_hw = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_hw.pack(pady=15, padx=20, fill="x")
        
        self.frame_hw.grid_columnconfigure(0, weight=1)
        col_idx = 1
        
        # 1. NVIDIA GPUs
        if self.nvml_active:
            for i, gpu in enumerate(self.gpu_data):
                short_name = gpu['name'].replace("NVIDIA GeForce ", "").replace("NVIDIA ", "").replace(" RTX", "")
                
                # Guess TDP for bar scaling (High end vs Mid range)
                est_max = TDP_NVIDIA_HIGH if "4070" in short_name or "3080" in short_name else TDP_NVIDIA_MID
                
                card = self.create_metric_card(self.frame_hw, short_name, "#76b900", est_max)
                card['frame'].grid(row=0, column=col_idx, padx=8)
                
                self.gpu_data[i]['widget_pwr'] = card['lbl_val']
                self.gpu_data[i]['widget_temp'] = card['lbl_temp']
                self.gpu_data[i]['widget_bar'] = card['bar']
                self.gpu_data[i]['max_w'] = est_max
                col_idx += 1

        # 2. iGPU (Purple title)
        self.card_igpu = self.create_metric_card(self.frame_hw, "iGPU (Radeon)", "#E040FB", TDP_IGPU)
        self.card_igpu['frame'].grid(row=0, column=col_idx, padx=8)
        col_idx += 1

        # 3. CPU
        self.card_cpu = self.create_metric_card(self.frame_hw, "Ryzen 9900X", "#ff8c00", TDP_CPU)
        self.card_cpu['frame'].grid(row=0, column=col_idx, padx=8)
        col_idx += 1
        
        self.frame_hw.grid_columnconfigure(col_idx, weight=1)

        # C. STATS PANEL
        self.frame_stats = ctk.CTkFrame(self, fg_color=COLOR_CARD, corner_radius=12)
        self.frame_stats.pack(pady=10, padx=25, fill="x", ipadx=15, ipady=5)

        self.frame_stats.grid_columnconfigure(0, weight=1)
        self.frame_stats.grid_columnconfigure(1, weight=1)
        self.frame_stats.grid_columnconfigure(2, weight=1)
        self.frame_stats.grid_columnconfigure(3, weight=1)

        # Headers
        h_font = ("Arial", 10, "bold")
        ctk.CTkLabel(self.frame_stats, text="TIMELINE", font=h_font, text_color=COLOR_TEXT_SUB).grid(row=0, column=0, pady=10, sticky="w")
        ctk.CTkLabel(self.frame_stats, text="COST (EGP)", font=h_font, text_color=COLOR_TEXT_SUB).grid(row=0, column=1, pady=10, sticky="e")
        ctk.CTkLabel(self.frame_stats, text="ENERGY", font=h_font, text_color=COLOR_TEXT_SUB).grid(row=0, column=2, pady=10, sticky="e")
        ctk.CTkLabel(self.frame_stats, text="DURATION", font=h_font, text_color=COLOR_TEXT_SUB).grid(row=0, column=3, pady=10, sticky="e")

        # Rows
        self.create_stat_row(1, "Session", COLOR_ACCENT)
        self.create_stat_row(2, "Today", "#00FF00")
        self.create_stat_row(3, "Overall", "#FFA500")

        # Footer
        self.lbl_status = ctk.CTkLabel(self, text="Initializing...", text_color="gray", font=("Arial", 10))
        self.lbl_status.pack(side="bottom", pady=10)

        # Thread
        self.monitor_thread = threading.Thread(target=self.background_monitor, daemon=True)
        self.monitor_thread.start()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def create_metric_card(self, parent, title, title_color, max_val_estimate):
        """Creates card with Title, Value, Progress Bar, Temp"""
        frame = ctk.CTkFrame(parent, width=155, height=140, fg_color=COLOR_CARD, corner_radius=10)
        frame.pack_propagate(False)
        
        # Title
        ctk.CTkLabel(frame, text=title, font=("Arial", 11, "bold"), text_color=title_color).pack(pady=(12, 2))
        
        # Value
        lbl_val = ctk.CTkLabel(frame, text="0 W", font=("Roboto", 24, "bold"), text_color=COLOR_TEXT_MAIN)
        lbl_val.pack(pady=0)
        
        # Progress Bar (Visual Load)
        bar = ctk.CTkProgressBar(frame, width=100, height=6, progress_color=title_color)
        bar.set(0)
        bar.pack(pady=(5, 5))
        
        # Temp
        lbl_temp = ctk.CTkLabel(frame, text="-- °C", font=("Arial", 12), text_color=COLOR_TEXT_SUB)
        lbl_temp.pack(pady=(0, 10))
        
        return {"frame": frame, "lbl_val": lbl_val, "lbl_temp": lbl_temp, "bar": bar, "max": max_val_estimate}

    def create_stat_row(self, row_idx, label_text, color):
        ctk.CTkLabel(self.frame_stats, text=f"{label_text}:", font=("Arial", 13), text_color=COLOR_TEXT_MAIN).grid(row=row_idx, column=0, pady=5, sticky="w")
        
        lbl_cost = ctk.CTkLabel(self.frame_stats, text="0.00", font=("Arial", 15, "bold"), text_color=color)
        lbl_cost.grid(row=row_idx, column=1, pady=5, sticky="e")
        
        lbl_kwh = ctk.CTkLabel(self.frame_stats, text="0.000 kWh", font=("Arial", 12), text_color=COLOR_TEXT_MAIN)
        lbl_kwh.grid(row=row_idx, column=2, pady=5, sticky="e")
        
        lbl_time = ctk.CTkLabel(self.frame_stats, text="00:00:00", font=("Arial", 12), text_color=COLOR_TEXT_SUB)
        lbl_time.grid(row=row_idx, column=3, pady=5, sticky="e")
        
        setattr(self, f"lbl_{label_text.lower()}_cost", lbl_cost)
        setattr(self, f"lbl_{label_text.lower()}_kwh", lbl_kwh)
        setattr(self, f"lbl_{label_text.lower()}_time", lbl_time)

    # --- LOGIC ---
    def setup_nvml(self):
        try:
            pynvml.nvmlInit()
            count = pynvml.nvmlDeviceGetCount()
            for i in range(count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                raw_name = pynvml.nvmlDeviceGetName(handle)
                name = raw_name.decode() if isinstance(raw_name, bytes) else raw_name
                self.gpu_data.append({"handle": handle, "name": name, "widget_pwr": None})
            self.nvml_active = True
        except: self.nvml_active = False

    def load_data(self):
        today_str = datetime.now().strftime("%Y-%m-%d")
        default = {"last_date": today_str, "day_kwh": 0.0, "day_cost": 0.0, "day_seconds": 0, "lifetime_kwh": 0.0, "lifetime_cost": 0.0, "lifetime_seconds": 0}
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    data = json.load(f)
                    if "lifetime_seconds" not in data: data.update({"lifetime_seconds": 0, "day_seconds": 0})
                    if data.get("last_date") != today_str:
                        data.update({"last_date": today_str, "day_kwh": 0.0, "day_cost": 0.0, "day_seconds": 0})
                    return data
            except: pass
        return default

    def save_data(self):
        with open(STATE_FILE, 'w') as f: json.dump(self.persistent_data, f, indent=4)

    def init_csv(self):
        if not os.path.exists(LOG_FILE):
            headers = ["Timestamp", "Total Watts", "Total Cost", "CPU Temp", "CPU Watts", "iGPU Temp", "iGPU Watts"]
            for i in range(len(self.gpu_data)): headers.extend([f"GPU{i} Temp", f"GPU{i} Watts"])
            with open(LOG_FILE, mode='w', newline='') as f: csv.writer(f).writerow(headers)

    def log_to_csv(self, total_w, cpu_t, cpu_w, igpu_t, igpu_w, nv_metrics):
        try:
            row = [datetime.now().strftime("%Y-%m-%d %H:%M:%S"), f"{total_w:.1f}", f"{self.persistent_data['day_cost']:.4f}", f"{cpu_t:.1f}", f"{cpu_w:.1f}", f"{igpu_t:.1f}", f"{igpu_w:.1f}"]
            for metric in nv_metrics: row.extend([f"{metric['temp']:.1f}", f"{metric['power']:.1f}"])
            with open(LOG_FILE, mode='a', newline='') as f: csv.writer(f).writerow(row)
        except: pass

    def fetch_lhm_data(self):
        try:
            r = requests.get(LHM_URL, timeout=0.2)
            if r.status_code == 200: return r.json()
        except: return None

    def find_sensor_value(self, node, target_names, sensor_type="Power"):
        if isinstance(node, dict):
            if node.get("Type") == sensor_type and any(name.lower() in node.get("Text", "").lower() for name in target_names):
                try: return float(str(node.get("Value", "0")).split()[0])
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
                if child.get("Type") == "Power" and child.get("Text", "") in ["GPU Core", "GPU SoC", "GPU Power"]:
                    try: acc += float(str(child.get("Value", "0")).split()[0])
                    except: pass
                acc += self.sum_radeon_powers(child)
        return acc

    def get_color(self, temp):
        if temp < 60: return "#76b900"
        if temp < 80: return "#ff8c00"
        return "#FF4444"

    def format_time(self, seconds):
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def update_card_visuals(self, widget_dict, power, temp, max_ref):
        """Updates text and progress bar"""
        try:
            # Text
            widget_dict['lbl_val'].configure(text=f"{int(power)} W")
            widget_dict['lbl_temp'].configure(text=f"{int(temp)} °C", text_color=self.get_color(temp))
            
            # Bar (0.0 to 1.0)
            ratio = min(1.0, power / max_ref)
            widget_dict['bar'].set(ratio)
        except: pass

    def background_monitor(self):
        last_log = time.time()
        while self.running:
            total_nvidia_w = 0
            nv_metrics = [] 
            
            # NVIDIA
            if self.nvml_active:
                for gpu in self.gpu_data:
                    try: 
                        w = pynvml.nvmlDeviceGetPowerUsage(gpu['handle']) / 1000.0
                        total_nvidia_w += w
                        t = pynvml.nvmlDeviceGetTemperature(gpu['handle'], 0)
                        nv_metrics.append({"power": w, "temp": t})
                        
                        if gpu['widget_pwr']:
                            # Update with bar logic
                            ratio = min(1.0, w / gpu['max_w'])
                            gpu['widget_pwr'].configure(text=f"{int(w)} W")
                            gpu['widget_temp'].configure(text=f"{t} °C", text_color=self.get_color(t))
                            gpu['widget_bar'].set(ratio)
                    except: nv_metrics.append({"power": 0, "temp": 0})

            # LHM
            lhm = self.fetch_lhm_data()
            cpu_w, igpu_w, cpu_t, igpu_t = 0, 0, 0, 0
            is_estimated = False
            status_msg = "Status: Live Data"

            if lhm:
                cpu_w = self.find_sensor_value(lhm, ["Package", "CPU Package"], "Power")
                igpu_w = self.calculate_igpu_total(lhm)
                cpu_t = self.find_sensor_value(lhm, ["Core (Tctl/Tdie)", "Package", "Core #1"], "Temperature")
                igpu_t = self.find_sensor_value(lhm, ["GPU Core", "GPU Temperature"], "Temperature")
                if cpu_w == 0: is_estimated = True
            else:
                is_estimated = True
                status_msg = "Status: Estimating (LHM Off)"
                if not self.nvml_active: cpu_w = 55 
            
            # Totals
            total_w = total_nvidia_w + igpu_w + cpu_w + 55
            
            # Peak Tracker
            if total_w > self.peak_w: self.peak_w = total_w

            # Stats logic
            kwh_inc = (total_w * 1.0) / 3_600_000
            cost_inc = kwh_inc * PRICE_PER_KWH
            self.session_data["kwh"] += kwh_inc
            self.session_data["cost"] += cost_inc
            self.persistent_data["day_kwh"] += kwh_inc
            self.persistent_data["day_cost"] += cost_inc
            self.persistent_data["day_seconds"] += 1
            self.persistent_data["lifetime_kwh"] += kwh_inc
            self.persistent_data["lifetime_cost"] += cost_inc
            self.persistent_data["lifetime_seconds"] += 1

            if datetime.now().strftime("%Y-%m-%d") != self.persistent_data["last_date"]:
                self.persistent_data.update({"last_date": datetime.now().strftime("%Y-%m-%d"), "day_kwh": 0.0, "day_cost": 0.0, "day_seconds": 0})

            # UI Update
            try:
                self.lbl_watts.configure(text=f"{int(total_w)} W")
                self.lbl_peak.configure(text=f"Peak: {int(self.peak_w)} W")
                
                if total_w > 500: self.lbl_watts.configure(text_color=COLOR_CRIT)
                elif total_w > 300: self.lbl_watts.configure(text_color=COLOR_WARN)
                else: self.lbl_watts.configure(text_color=COLOR_ACCENT)

                # Update Manual Cards (iGPU + CPU)
                self.update_card_visuals(self.card_igpu, igpu_w, igpu_t, TDP_IGPU)
                self.update_card_visuals(self.card_cpu, cpu_w, cpu_t, TDP_CPU)
                self.card_cpu['lbl_val'].configure(text_color="gray" if is_estimated else COLOR_TEXT_MAIN)

                # Stats Text
                self.lbl_session_time.configure(text=self.format_time(time.time() - self.start_time))
                self.lbl_session_cost.configure(text=f"{self.session_data['cost']:.4f}")
                self.lbl_session_kwh.configure(text=f"{self.session_data['kwh']:.4f} kWh")
                
                self.lbl_today_time.configure(text=self.format_time(self.persistent_data["day_seconds"]))
                self.lbl_today_cost.configure(text=f"{self.persistent_data['day_cost']:.4f}")
                self.lbl_today_kwh.configure(text=f"{self.persistent_data['day_kwh']:.4f} kWh")
                
                self.lbl_overall_time.configure(text=self.format_time(self.persistent_data["lifetime_seconds"]))
                self.lbl_overall_cost.configure(text=f"{self.persistent_data['lifetime_cost']:.4f}")
                self.lbl_overall_kwh.configure(text=f"{self.persistent_data['lifetime_kwh']:.4f} kWh")

                if self.persistent_data["day_cost"] > DAILY_LIMIT_EGP:
                    self.lbl_today_cost.configure(text_color=COLOR_CRIT)
                    self.lbl_status.configure(text="⚠️ DAILY BUDGET EXCEEDED ⚠️" if int(time.time())%2==0 else f"Limit: {DAILY_LIMIT_EGP} EGP", text_color=COLOR_CRIT)
                else:
                    self.lbl_today_cost.configure(text_color="#00FF00")
                    self.lbl_status.configure(text=status_msg, text_color="gray")
            except: pass

            if time.time() - last_log > 60:
                self.save_data()
                self.log_to_csv(total_w, cpu_t, cpu_w, igpu_t, igpu_w, nv_metrics)
                last_log = time.time()
            time.sleep(1)

    def on_close(self):
        self.running = False
        self.save_data()
        self.destroy()

if __name__ == "__main__":
    app = PowerMonitorApp()
    app.mainloop()