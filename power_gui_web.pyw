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
COLOR_BG = "#1a1a1a"        # Main Background
COLOR_CARD = "#2b2b2b"      # Card Background
COLOR_TEXT_MAIN = "#ffffff"
COLOR_TEXT_SUB = "#a0a0a0"
COLOR_ACCENT = "#00E5FF"    # Cyan

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
        
        # Dynamic Width: Base (Stats) + (GPUs * CardWidth)
        # Card width = 160 (150 width + 10 padding)
        extra_width = max(0, (len(self.gpu_data) - 1) * 160) 
        window_width = 800 + extra_width
        
        self.title("⚡ Power Monitor (Modern UI)")
        self.geometry(f"{window_width}x680") 
        self.configure(fg_color=COLOR_BG)
        self.resizable(True, True)

        # 2. Data Init
        self.running = True
        self.start_time = time.time()
        self.session_data = {"kwh": 0.0, "cost": 0.0}
        self.persistent_data = self.load_data()
        self.save_data()
        self.init_csv()

        # --- UI LAYOUT ---
        
        # A. HEADER SECTION (Total Power)
        self.frame_header = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_header.pack(pady=(20, 10), fill="x")
        
        self.lbl_title = ctk.CTkLabel(self.frame_header, text="SYSTEM POWER DRAW", font=("Roboto Medium", 14), text_color=COLOR_TEXT_SUB)
        self.lbl_title.pack(pady=(0, 5))

        self.lbl_watts = ctk.CTkLabel(self.frame_header, text="--- W", font=("Roboto", 72, "bold"), text_color=COLOR_ACCENT)
        self.lbl_watts.pack(pady=0)

        # B. HARDWARE CARDS ROW
        self.frame_hw = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_hw.pack(pady=20, padx=20, fill="x")
        
        # Using a grid layout for cards to keep them centered
        self.frame_hw.grid_columnconfigure(0, weight=1) # Left spacer
        col_idx = 1
        
        # 1. NVIDIA GPUs
        if self.nvml_active:
            for i, gpu in enumerate(self.gpu_data):
                short_name = gpu['name'].replace("NVIDIA GeForce ", "").replace("NVIDIA ", "").replace(" RTX", "")
                
                # Create Card
                card = self.create_metric_card(self.frame_hw, short_name, "#76b900")
                card['frame'].grid(row=0, column=col_idx, padx=10)
                
                # Store widgets for updates
                self.gpu_data[i]['widget_pwr'] = card['lbl_val']
                self.gpu_data[i]['widget_temp'] = card['lbl_temp']
                col_idx += 1

        # 2. iGPU Card
        self.card_igpu = self.create_metric_card(self.frame_hw, "iGPU (Radeon)", "#FF3333")
        self.card_igpu['frame'].grid(row=0, column=col_idx, padx=10)
        col_idx += 1

        # 3. CPU Card
        self.card_cpu = self.create_metric_card(self.frame_hw, "Ryzen 9900X", "#ff8c00")
        self.card_cpu['frame'].grid(row=0, column=col_idx, padx=10)
        col_idx += 1
        
        self.frame_hw.grid_columnconfigure(col_idx, weight=1) # Right spacer


        # C. STATS PANEL (Bottom)
        self.frame_stats = ctk.CTkFrame(self, fg_color=COLOR_CARD, corner_radius=15)
        self.frame_stats.pack(pady=20, padx=30, fill="x", ipadx=20, ipady=10)

        # Grid config for stats
        self.frame_stats.grid_columnconfigure(0, weight=1) # Timeline
        self.frame_stats.grid_columnconfigure(1, weight=1) # Cost
        self.frame_stats.grid_columnconfigure(2, weight=1) # Energy
        self.frame_stats.grid_columnconfigure(3, weight=1) # Time

        # Headers
        h_font = ("Arial", 11, "bold")
        ctk.CTkLabel(self.frame_stats, text="TIMELINE", font=h_font, text_color=COLOR_TEXT_SUB).grid(row=0, column=0, pady=15, sticky="w")
        ctk.CTkLabel(self.frame_stats, text="COST (EGP)", font=h_font, text_color=COLOR_TEXT_SUB).grid(row=0, column=1, pady=15, sticky="e")
        ctk.CTkLabel(self.frame_stats, text="ENERGY", font=h_font, text_color=COLOR_TEXT_SUB).grid(row=0, column=2, pady=15, sticky="e")
        ctk.CTkLabel(self.frame_stats, text="DURATION", font=h_font, text_color=COLOR_TEXT_SUB).grid(row=0, column=3, pady=15, sticky="e")

        # Row 1: Session
        self.create_stat_row(1, "Session", COLOR_ACCENT)
        # Row 2: Today
        self.create_stat_row(2, "Today", "#00FF00")
        # Row 3: Overall
        self.create_stat_row(3, "Overall", "#FFA500")

        # Footer Status
        self.lbl_status = ctk.CTkLabel(self, text="Initializing...", text_color="gray", font=("Arial", 11))
        self.lbl_status.pack(side="bottom", pady=15)

        # Thread Start
        self.monitor_thread = threading.Thread(target=self.background_monitor, daemon=True)
        self.monitor_thread.start()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def create_metric_card(self, parent, title, title_color):
        """Helper to build a unified hardware card"""
        frame = ctk.CTkFrame(parent, width=150, height=130, fg_color=COLOR_CARD, corner_radius=12)
        frame.pack_propagate(False) # Force fixed size
        
        # Title
        ctk.CTkLabel(frame, text=title, font=("Arial", 12, "bold"), text_color=title_color).pack(pady=(15, 5))
        
        # Value (Big)
        lbl_val = ctk.CTkLabel(frame, text="0 W", font=("Roboto", 28, "bold"), text_color=COLOR_TEXT_MAIN)
        lbl_val.pack(pady=(0, 0))
        
        # Temp (Small)
        lbl_temp = ctk.CTkLabel(frame, text="-- °C", font=("Arial", 13), text_color=COLOR_TEXT_SUB)
        lbl_temp.pack(pady=(5, 10))
        
        return {"frame": frame, "lbl_val": lbl_val, "lbl_temp": lbl_temp}

    def create_stat_row(self, row_idx, label_text, color):
        """Helper to create a row in the stats table"""
        # Timeline Label
        ctk.CTkLabel(self.frame_stats, text=f"{label_text}:", font=("Arial", 14), text_color=COLOR_TEXT_MAIN).grid(row=row_idx, column=0, pady=8, sticky="w")
        
        # Cost
        lbl_cost = ctk.CTkLabel(self.frame_stats, text="0.00", font=("Arial", 16, "bold"), text_color=color)
        lbl_cost.grid(row=row_idx, column=1, pady=8, sticky="e")
        
        # Energy
        lbl_kwh = ctk.CTkLabel(self.frame_stats, text="0.000 kWh", font=("Arial", 13), text_color=COLOR_TEXT_MAIN)
        lbl_kwh.grid(row=row_idx, column=2, pady=8, sticky="e")
        
        # Time
        lbl_time = ctk.CTkLabel(self.frame_stats, text="00:00:00", font=("Arial", 13), text_color=COLOR_TEXT_SUB)
        lbl_time.grid(row=row_idx, column=3, pady=8, sticky="e")
        
        # Save references
        setattr(self, f"lbl_{label_text.lower()}_cost", lbl_cost)
        setattr(self, f"lbl_{label_text.lower()}_kwh", lbl_kwh)
        setattr(self, f"lbl_{label_text.lower()}_time", lbl_time)

    # --- LOGIC METHODS ---
    def setup_nvml(self):
        try:
            pynvml.nvmlInit()
            count = pynvml.nvmlDeviceGetCount()
            for i in range(count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                raw_name = pynvml.nvmlDeviceGetName(handle)
                name = raw_name.decode() if isinstance(raw_name, bytes) else raw_name
                self.gpu_data.append({"handle": handle, "name": name, "widget_pwr": None, "widget_temp": None})
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
            headers = ["Timestamp", "Total Watts", "Total Cost (Daily)", "CPU Temp", "CPU Watts", "iGPU Temp", "iGPU Watts"]
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

    def background_monitor(self):
        last_log = time.time()
        while self.running:
            total_nvidia_w = 0
            nv_metrics = [] 
            
            # NVIDIA Logic
            if self.nvml_active:
                for gpu in self.gpu_data:
                    try: 
                        w = pynvml.nvmlDeviceGetPowerUsage(gpu['handle']) / 1000.0
                        total_nvidia_w += w
                        t = pynvml.nvmlDeviceGetTemperature(gpu['handle'], 0)
                        nv_metrics.append({"power": w, "temp": t})
                        if gpu['widget_pwr']:
                            gpu['widget_pwr'].configure(text=f"{int(w)} W")
                            gpu['widget_temp'].configure(text=f"{t} °C", text_color=self.get_color(t))
                    except: nv_metrics.append({"power": 0, "temp": 0})

            # LHM Logic
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
            total_w = total_nvidia_w + igpu_w + cpu_w + 55 # Overhead
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

            # Day Reset
            if datetime.now().strftime("%Y-%m-%d") != self.persistent_data["last_date"]:
                self.persistent_data.update({"last_date": datetime.now().strftime("%Y-%m-%d"), "day_kwh": 0.0, "day_cost": 0.0, "day_seconds": 0})

            # UI Update
            try:
                self.lbl_watts.configure(text=f"{int(total_w)} W")
                if total_w > 500: self.lbl_watts.configure(text_color="#FF4444")
                elif total_w > 300: self.lbl_watts.configure(text_color="#FFD700")
                else: self.lbl_watts.configure(text_color=COLOR_ACCENT)

                self.card_igpu['lbl_val'].configure(text=f"{int(igpu_w)} W")
                self.card_igpu['lbl_temp'].configure(text=f"{int(igpu_t)} °C", text_color=self.get_color(igpu_t))
                
                self.card_cpu['lbl_val'].configure(text=f"{int(cpu_w)} W", text_color="gray" if is_estimated else COLOR_TEXT_MAIN)
                self.card_cpu['lbl_temp'].configure(text=f"{int(cpu_t)} °C", text_color=self.get_color(cpu_t))

                # Update Stats Rows
                self.lbl_session_time.configure(text=self.format_time(time.time() - self.start_time))
                self.lbl_session_cost.configure(text=f"{self.session_data['cost']:.4f}")
                self.lbl_session_kwh.configure(text=f"{self.session_data['kwh']:.4f} kWh")
                
                self.lbl_today_time.configure(text=self.format_time(self.persistent_data["day_seconds"]))
                self.lbl_today_cost.configure(text=f"{self.persistent_data['day_cost']:.4f}")
                self.lbl_today_kwh.configure(text=f"{self.persistent_data['day_kwh']:.4f} kWh")
                
                self.lbl_overall_time.configure(text=self.format_time(self.persistent_data["lifetime_seconds"]))
                self.lbl_overall_cost.configure(text=f"{self.persistent_data['lifetime_cost']:.4f}")
                self.lbl_overall_kwh.configure(text=f"{self.persistent_data['lifetime_kwh']:.4f} kWh")

                # Budget Alert
                if self.persistent_data["day_cost"] > DAILY_LIMIT_EGP:
                    self.lbl_today_cost.configure(text_color="#FF4444")
                    self.lbl_status.configure(text="⚠️ DAILY BUDGET EXCEEDED ⚠️" if int(time.time())%2==0 else f"Limit: {DAILY_LIMIT_EGP} EGP", text_color="#FF4444")
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