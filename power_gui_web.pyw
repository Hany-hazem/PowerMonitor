import customtkinter as ctk
import threading
import time
import json
import os
import csv
import socket
import webbrowser
import requests
import psutil
import platform
import subprocess
import warnings
from datetime import datetime
from collections import deque

# --- SAFE IMPORTS ---
try:
    warnings.filterwarnings("ignore", category=FutureWarning)
    import pynvml
except ImportError:
    pynvml = None 

# --- FLASK ---
from flask import Flask, jsonify, render_template_string
import logging

# --- MATPLOTLIB ---
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# --- CONFIGURATION ---
DEFAULT_PRICE = 2.14          
DEFAULT_LIMIT = 50.00
STATE_FILE = "power_state.json"
LOG_FILE = "power_log.csv"
LHM_URL = "http://localhost:8085/data.json"
FLASK_PORT = 5000

# --- SHARED DATA ---
SHARED_DATA = {
    "total_w": 0, "peak_w": 0,
    "cpu_w": 0, "cpu_t": 0, "cpu_load": 0,
    "igpu_w": 0, "igpu_t": 0, "igpu_load": 0,
    "sys_w": 40, # Default lower for laptops
    "gpu_data": [], 
    "cost_session": 0.0, "cost_today": 0.0, "cost_overall": 0.0, 
    "cost_est_month": 0.0,
    "time_session": "00:00:00",
    "alert": False,
    "price_per_kwh": DEFAULT_PRICE,
    "daily_limit": DEFAULT_LIMIT
}

# --- THEME ---
COLOR_BG = "#1a1a1a"
COLOR_CARD = "#2b2b2b"
COLOR_TEXT_MAIN = "#ffffff"
COLOR_TEXT_SUB = "#a0a0a0"
COLOR_ACCENT = "#00E5FF"    
COLOR_WARN = "#FFD700"      
COLOR_CRIT = "#FF4444"      
COLOR_SYS = "#9E9E9E"       

# --- TDP ESTIMATES ---
TDP_CPU = 100         
TDP_IGPU = 45         
TDP_SYS = 100         

IS_WINDOWS = platform.system() == "Windows"

os.environ["PATH"] += os.pathsep + os.getcwd()
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

class PowerMonitorApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        # 1. Hardware Discovery
        self.gpu_data = [] 
        self.nvml_active = False
        self.setup_nvml() # Finds NVIDIA GPUs
        self.detected_cpu_name = self.detect_cpu_name()
        
        # 2. Dynamic Size Calculation
        # Base width (Stats) + (160px per hardware card)
        # Cards = GPUs + CPU + System + iGPU (always assumed present for safety)
        num_cards = len(self.gpu_data) + 3 
        window_width = max(850, num_cards * 160 + 50)
        
        self.title(f"⚡ Power Monitor (V45 Adaptive)")
        self.geometry(f"{window_width}x1150") 
        self.configure(fg_color=COLOR_BG)
        self.resizable(True, True)

        # 3. Data Init
        self.running = True
        self.start_time = time.time()
        self.peak_w = 0 
        self.session_data = {"kwh": 0.0, "cost": 0.0}
        self.persistent_data = self.load_data()
        
        # Dynamic Settings
        self.cfg_overhead = 80.0 if len(self.gpu_data) > 0 else 40.0 # Less overhead if no dGPU
        self.cfg_interval = 1
        self.cfg_limit = DEFAULT_LIMIT
        
        self.history_x = deque(maxlen=60)
        self.history_y = deque(maxlen=60)
        for i in range(60): 
            self.history_x.append(i)
            self.history_y.append(0)

        self.save_data()
        self.init_csv()

        # --- UI LAYOUT ---
        self.main_scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.main_scroll.pack(fill="both", expand=True)

        # Header
        self.frame_header = ctk.CTkFrame(self.main_scroll, fg_color="transparent")
        self.frame_header.pack(pady=(15, 5), fill="x")
        self.lbl_title = ctk.CTkLabel(self.frame_header, text="SYSTEM POWER DRAW", font=("Roboto Medium", 12), text_color=COLOR_TEXT_SUB)
        self.lbl_title.pack()
        self.lbl_watts = ctk.CTkLabel(self.frame_header, text="--- W", font=("Roboto", 64, "bold"), text_color=COLOR_ACCENT)
        self.lbl_watts.pack()
        self.lbl_peak = ctk.CTkLabel(self.frame_header, text="Peak: 0 W", font=("Arial", 11), text_color="gray")
        self.lbl_peak.pack()

        # Hardware Grid (Dynamic)
        self.frame_hw = ctk.CTkFrame(self.main_scroll, fg_color="transparent")
        self.frame_hw.pack(pady=10, padx=20, fill="x")
        
        col_idx = 0
        # 1. NVIDIA GPUs (If any)
        if self.nvml_active:
            for i, gpu in enumerate(self.gpu_data):
                card = self.create_metric_card(self.frame_hw, gpu['short'], "#76b900", 250) # Generic max 250W
                card['frame'].grid(row=0, column=col_idx, padx=5)
                self.gpu_data[i]['widget_pwr'] = card['lbl_val']
                self.gpu_data[i]['widget_temp'] = card['lbl_temp']
                self.gpu_data[i]['widget_bar'] = card['bar']
                self.gpu_data[i]['max_w'] = 250 
                col_idx += 1

        # 2. iGPU (Always show, might be 0W)
        self.card_igpu = self.create_metric_card(self.frame_hw, "iGPU (Integrated)", "#E040FB", TDP_IGPU)
        self.card_igpu['frame'].grid(row=0, column=col_idx, padx=5)
        col_idx += 1

        # 3. CPU (Auto Detected)
        self.card_cpu = self.create_metric_card(self.frame_hw, self.detected_cpu_name, "#ff8c00", TDP_CPU)
        self.card_cpu['frame'].grid(row=0, column=col_idx, padx=5)
        col_idx += 1
        
        # 4. System Overhead
        self.card_sys = self.create_metric_card(self.frame_hw, "System (Misc)", COLOR_SYS, TDP_SYS)
        self.card_sys['frame'].grid(row=0, column=col_idx, padx=5)
        self.card_sys['lbl_val'].configure(text=f"{int(self.cfg_overhead)} W")
        self.card_sys['lbl_temp'].configure(text="Mobo/Ram/Fans")
        self.card_sys['bar'].set(self.cfg_overhead / TDP_SYS)
        
        # Center the grid
        for i in range(col_idx + 1):
            self.frame_hw.grid_columnconfigure(i, weight=1)

        # Chart
        self.frame_chart = ctk.CTkFrame(self.main_scroll, fg_color=COLOR_CARD, corner_radius=12, height=200)
        self.frame_chart.pack(pady=10, padx=25, fill="x")
        self.setup_chart()

        # Stats
        self.frame_stats = ctk.CTkFrame(self.main_scroll, fg_color=COLOR_CARD, corner_radius=12)
        self.frame_stats.pack(pady=10, padx=25, fill="x", ipadx=15, ipady=5)
        self.frame_stats.grid_columnconfigure((0,1,2,3), weight=1)

        h_font = ("Arial", 10, "bold")
        ctk.CTkLabel(self.frame_stats, text="TIMELINE", font=h_font, text_color=COLOR_TEXT_SUB).grid(row=0, column=0, pady=10, sticky="w")
        ctk.CTkLabel(self.frame_stats, text="COST (EGP)", font=h_font, text_color=COLOR_TEXT_SUB).grid(row=0, column=1, pady=10, sticky="e")
        ctk.CTkLabel(self.frame_stats, text="ENERGY", font=h_font, text_color=COLOR_TEXT_SUB).grid(row=0, column=2, pady=10, sticky="e")
        ctk.CTkLabel(self.frame_stats, text="DURATION", font=h_font, text_color=COLOR_TEXT_SUB).grid(row=0, column=3, pady=10, sticky="e")

        self.create_stat_row(1, "Session", COLOR_ACCENT)
        self.create_stat_row(2, "Today", "#00FF00")
        self.create_stat_row(3, "Overall", "#FFA500")
        
        ctk.CTkFrame(self.frame_stats, height=2, fg_color="#404040").grid(row=4, column=0, columnspan=4, sticky="ew", pady=10)

        # Config Row
        ctk.CTkLabel(self.frame_stats, text="LIVE CONFIGURATION", font=("Arial", 11, "bold"), text_color="gray").grid(row=5, column=0, pady=(5,0), sticky="w")
        self.frame_config = ctk.CTkFrame(self.frame_stats, fg_color="transparent")
        self.frame_config.grid(row=6, column=0, columnspan=4, sticky="ew", pady=2)
        
        ctk.CTkLabel(self.frame_config, text="Overhead (W):", text_color="gray").pack(side="left")
        self.entry_overhead = ctk.CTkEntry(self.frame_config, width=40, height=25, justify="center")
        self.entry_overhead.pack(side="left", padx=5)
        self.entry_overhead.insert(0, str(int(self.cfg_overhead)))
        
        ctk.CTkLabel(self.frame_config, text="Limit (EGP):", text_color="gray").pack(side="left", padx=(10,0))
        self.entry_limit = ctk.CTkEntry(self.frame_config, width=40, height=25, justify="center")
        self.entry_limit.pack(side="left", padx=5)
        self.entry_limit.insert(0, str(int(DEFAULT_LIMIT)))

        ctk.CTkLabel(self.frame_config, text="Log (s):", text_color="gray").pack(side="left", padx=(10,0))
        self.entry_interval = ctk.CTkEntry(self.frame_config, width=30, height=25, justify="center")
        self.entry_interval.pack(side="left", padx=5)
        self.entry_interval.insert(0, "1")
        
        self.btn_apply = ctk.CTkButton(self.frame_config, text="Apply", width=50, height=25, fg_color="#404040", hover_color="#606060", command=self.apply_config)
        self.btn_apply.pack(side="left", padx=15)

        ctk.CTkFrame(self.frame_stats, height=2, fg_color="#404040").grid(row=7, column=0, columnspan=4, sticky="ew", pady=10)

        # Calculators
        ctk.CTkLabel(self.frame_stats, text="COST ESTIMATORS", font=("Arial", 11, "bold"), text_color="#E040FB").grid(row=8, column=0, pady=(5,0), sticky="w")
        
        self.frame_calc1 = ctk.CTkFrame(self.frame_stats, fg_color="transparent")
        self.frame_calc1.grid(row=9, column=0, columnspan=4, sticky="ew", pady=2)
        ctk.CTkLabel(self.frame_calc1, text="Monthly 24/7:", text_color="#a0a0a0", width=90, anchor="w").pack(side="left")
        self.entry_hours_1 = ctk.CTkEntry(self.frame_calc1, width=40, height=25, justify="center"); self.entry_hours_1.pack(side="left"); self.entry_hours_1.insert(0, "24")
        ctk.CTkLabel(self.frame_calc1, text="h/d x", text_color="gray").pack(side="left", padx=5)
        self.entry_days_1 = ctk.CTkEntry(self.frame_calc1, width=40, height=25, justify="center"); self.entry_days_1.pack(side="left"); self.entry_days_1.insert(0, "30")
        ctk.CTkLabel(self.frame_calc1, text="d", text_color="gray").pack(side="left", padx=2)
        self.btn_calc_1 = ctk.CTkButton(self.frame_calc1, text="Calc", width=60, height=25, fg_color="#404040", hover_color="#606060", command=lambda: self.calculate_custom_cost(1))
        self.btn_calc_1.pack(side="left", padx=10)
        self.lbl_calc_result_1 = ctk.CTkLabel(self.frame_calc1, text="---", font=("Arial", 13, "bold"), text_color=COLOR_TEXT_MAIN)
        self.lbl_calc_result_1.pack(side="left")

        self.frame_calc2 = ctk.CTkFrame(self.frame_stats, fg_color="transparent")
        self.frame_calc2.grid(row=10, column=0, columnspan=4, sticky="ew", pady=2)
        ctk.CTkLabel(self.frame_calc2, text="Custom Task:", text_color="#00E5FF", width=90, anchor="w").pack(side="left")
        self.entry_hours_2 = ctk.CTkEntry(self.frame_calc2, width=80, height=25, justify="center"); self.entry_hours_2.pack(side="left"); self.entry_hours_2.insert(0, "35:25:17") 
        ctk.CTkLabel(self.frame_calc2, text="(Duration)", text_color="gray").pack(side="left", padx=5)
        self.entry_days_2 = ctk.CTkEntry(self.frame_calc2, width=0, height=0); self.entry_days_2.insert(0, "1")
        self.btn_calc_2 = ctk.CTkButton(self.frame_calc2, text="Calc", width=60, height=25, fg_color="#404040", hover_color="#606060", command=lambda: self.calculate_custom_cost(2))
        self.btn_calc_2.pack(side="left", padx=10)
        self.lbl_calc_result_2 = ctk.CTkLabel(self.frame_calc2, text="---", font=("Arial", 13, "bold"), text_color=COLOR_TEXT_MAIN)
        self.lbl_calc_result_2.pack(side="left")

        # Footer
        self.frame_footer = ctk.CTkFrame(self.main_scroll, fg_color="transparent")
        self.frame_footer.pack(pady=20, fill="x")
        self.lbl_status = ctk.CTkLabel(self.frame_footer, text="Initializing...", text_color="gray", font=("Arial", 11))
        self.lbl_status.pack()
        ips = self.get_all_ips()
        if not ips: ctk.CTkLabel(self.frame_footer, text="No Network Found", text_color="gray").pack()
        else:
            for ip in ips:
                link = f"http://{ip}:{FLASK_PORT}"
                label_text = f"☁️ Tailscale: {link}" if ip.startswith("100.") else f"🏠 Home LAN: {link}"
                color = "#E040FB" if ip.startswith("100.") else COLOR_ACCENT
                lbl = ctk.CTkLabel(self.frame_footer, text=label_text, text_color=color, font=("Arial", 12, "bold"), cursor="hand2")
                lbl.pack(pady=2)
                lbl.bind("<Button-1>", lambda e, url=link: webbrowser.open(url))

        # Threads
        self.monitor_thread = threading.Thread(target=self.background_monitor, daemon=True)
        self.monitor_thread.start()
        self.flask_thread = threading.Thread(target=self.run_flask_server, daemon=True)
        self.flask_thread.start()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def detect_cpu_name(self):
        try:
            if platform.system() == "Windows":
                import winreg
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
                name = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
                name = name.replace("Intel(R) Core(TM) ", "").replace("AMD Ryzen ", "Ryzen ")
                name = name.replace(" Processor", "").replace(" 12-Core", "")
                return name
            elif platform.system() == "Darwin":
                return subprocess.check_output(["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"]).decode().strip()
            elif platform.system() == "Linux":
                with open("/proc/cpuinfo", "r") as f:
                    for line in f:
                        if "model name" in line: return line.split(":")[1].strip()
            return "Generic CPU"
        except: return "Generic CPU"

    def apply_config(self):
        try:
            w = float(self.entry_overhead.get())
            limit = float(self.entry_limit.get())
            i = int(self.entry_interval.get())
            if i < 1: i = 1
            self.cfg_overhead = w; self.cfg_interval = i; self.cfg_limit = limit
            SHARED_DATA["sys_w"] = w; SHARED_DATA["daily_limit"] = limit
            self.card_sys['lbl_val'].configure(text=f"{int(w)} W")
            self.card_sys['bar'].set(min(1.0, w / TDP_SYS))
            self.lbl_status.configure(text=f"Settings Saved: Limit {limit} EGP", text_color="#00FF00")
        except ValueError: self.lbl_status.configure(text="Invalid Config Input", text_color="red")

    def parse_time_input(self, val_str):
        val_str = val_str.lower().replace("h", "").strip()
        try:
            if ":" in val_str:
                parts = list(map(float, val_str.split(":")))
                if len(parts) == 3: return parts[0] + parts[1]/60 + parts[2]/3600
                elif len(parts) == 2: return parts[0] + parts[1]/60
                return 0.0
            return float(val_str)
        except: return 0.0

    def calculate_custom_cost(self, calc_id):
        try:
            h_entry = self.entry_hours_1 if calc_id == 1 else self.entry_hours_2
            d_entry = self.entry_days_1 if calc_id == 1 else self.entry_days_2
            res_lbl = self.lbl_calc_result_1 if calc_id == 1 else self.lbl_calc_result_2
            h = self.parse_time_input(h_entry.get())
            d = float(d_entry.get())
            w = SHARED_DATA["total_w"]
            cost = (w / 1000.0) * h * d * DEFAULT_PRICE
            res_lbl.configure(text=f"{cost:.2f} EGP")
        except ValueError: pass

    def get_all_ips(self):
        ip_list = []
        try: hostname = socket.gethostname(); ip_list = [ip for ip in socket.gethostbyname_ex(hostname)[2] if not ip.startswith("127.")]
        except: pass
        try: s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("8.8.8.8", 80)); main_ip = s.getsockname()[0]; s.close(); ip_list.insert(0, main_ip) if main_ip not in ip_list else None
        except: pass
        return list(dict.fromkeys(ip_list))

    def setup_chart(self):
        self.fig = Figure(figsize=(5, 2), dpi=100); self.fig.patch.set_facecolor(COLOR_CARD)
        self.ax = self.fig.add_subplot(111); self.ax.set_facecolor(COLOR_CARD)
        self.line, = self.ax.plot([], [], color=COLOR_ACCENT, linewidth=2)
        self.ax.grid(True, color="#404040", linestyle='--', linewidth=0.5)
        for spine in self.ax.spines.values(): spine.set_visible(False)
        self.ax.spines['bottom'].set_visible(True); self.ax.spines['bottom'].set_color('#404040')
        self.ax.spines['left'].set_visible(True); self.ax.spines['left'].set_color('#404040')
        self.ax.tick_params(axis='x', colors='gray', labelsize=8); self.ax.tick_params(axis='y', colors='gray', labelsize=8)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.frame_chart); self.canvas.draw(); self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=5, pady=5)

    def update_chart_data(self, new_val):
        self.history_y.append(new_val)
        self.line.set_data(self.history_x, self.history_y)
        self.ax.set_ylim(0, max(max(self.history_y) * 1.2, 100))
        self.ax.set_xlim(0, 60)
        self.canvas.draw()

    def create_metric_card(self, parent, title, title_color, max_val_estimate):
        frame = ctk.CTkFrame(parent, width=155, height=140, fg_color=COLOR_CARD, corner_radius=10); frame.pack_propagate(False)
        ctk.CTkLabel(frame, text=title, font=("Arial", 11, "bold"), text_color=title_color).pack(pady=(12, 2))
        lbl_val = ctk.CTkLabel(frame, text="0 W", font=("Roboto", 24, "bold"), text_color=COLOR_TEXT_MAIN); lbl_val.pack(pady=0)
        bar = ctk.CTkProgressBar(frame, width=100, height=6, progress_color=title_color); bar.set(0); bar.pack(pady=(5, 5))
        lbl_temp = ctk.CTkLabel(frame, text="-- °C | -- %", font=("Arial", 11), text_color=COLOR_TEXT_SUB); lbl_temp.pack(pady=(0, 10))
        return {"frame": frame, "lbl_val": lbl_val, "lbl_temp": lbl_temp, "bar": bar, "max": max_val_estimate}

    def create_stat_row(self, row_idx, label_text, color):
        ctk.CTkLabel(self.frame_stats, text=f"{label_text}:", font=("Arial", 13), text_color=COLOR_TEXT_MAIN).grid(row=row_idx, column=0, pady=5, sticky="w")
        lbl_cost = ctk.CTkLabel(self.frame_stats, text="0.00", font=("Arial", 15, "bold"), text_color=color); lbl_cost.grid(row=row_idx, column=1, pady=5, sticky="e")
        lbl_kwh = ctk.CTkLabel(self.frame_stats, text="0.000 kWh", font=("Arial", 12), text_color=COLOR_TEXT_MAIN); lbl_kwh.grid(row=row_idx, column=2, pady=5, sticky="e")
        lbl_time = ctk.CTkLabel(self.frame_stats, text="00:00:00", font=("Arial", 12), text_color=COLOR_TEXT_SUB); lbl_time.grid(row=row_idx, column=3, pady=5, sticky="e")
        setattr(self, f"lbl_{label_text.lower()}_cost", lbl_cost); setattr(self, f"lbl_{label_text.lower()}_kwh", lbl_kwh); setattr(self, f"lbl_{label_text.lower()}_time", lbl_time)

    # --- FLASK SERVER ---
    def run_flask_server(self):
        app = Flask(__name__)
        @app.route('/')
        def index():
            return render_template_string("""
            <!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Power Monitor</title>
                <style>
                    body { background-color: #1a1a1a; color: white; font-family: 'Segoe UI', sans-serif; text-align: center; padding: 20px; }
                    h1 { margin-bottom: 5px; color: #a0a0a0; font-size: 16px; }
                    .watts { font-size: 60px; font-weight: bold; color: #00E5FF; margin: 0; }
                    .peak { color: gray; font-size: 14px; margin-bottom: 20px; }
                    .grid { display: flex; flex-wrap: wrap; justify-content: center; gap: 10px; margin-bottom: 20px; }
                    .card { background: #2b2b2b; padding: 15px; border-radius: 10px; width: 150px; }
                    .card-title { font-size: 12px; font-weight: bold; margin-bottom: 5px; display: block; }
                    .card-val { font-size: 22px; font-weight: bold; }
                    .card-temp { font-size: 11px; color: #a0a0a0; margin-top:5px; line-height: 1.4; }
                    .stats { background: #2b2b2b; border-radius: 10px; padding: 15px; text-align: left; }
                    .row { display: flex; justify-content: space-between; margin-bottom: 10px; border-bottom: 1px solid #404040; padding-bottom: 5px; }
                    .calc-box { margin-top: 15px; padding-top: 15px; border-top: 1px solid #404040; }
                    .calc-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
                    .calc-input { background: #404040; border: none; color: white; width: 60px; text-align: center; padding: 5px; border-radius: 4px; }
                    .calc-res { color: #E040FB; font-weight: bold; font-size: 14px; min-width: 70px; text-align: right; }
                    .calc-sub { font-size:10px; color:gray; display:block; margin-top:2px; }
                </style></head><body>
                <h1>SYSTEM POWER DRAW</h1><div id="total_w" class="watts">--- W</div><div id="peak_w" class="peak">Peak: --- W</div>
                <div class="grid" id="gpu_grid"></div>
                <div class="stats">
                    <div class="row"><span class="label">Session</span><span id="cost_session" class="cost" style="color:#00E5FF">0.00 EGP</span></div>
                    <div class="row"><span class="label">Today</span><span id="cost_today" class="cost" style="color:#00FF00">0.00 EGP</span></div>
                    <div class="row"><span class="label">Overall</span><span id="cost_overall" class="cost" style="color:#FFA500">0.00 EGP</span></div>
                    <div class="row"><span class="label" style="color:#a0a0a0">Est. 24/7 (Month)</span><span id="cost_est_month" class="cost" style="color:#gray">0.00 EGP</span></div>
                    <div class="calc-box">
                        <div style="font-weight:bold; color:#E040FB; margin-bottom:10px;">COST CALCULATORS</div>
                        <div class="calc-row"><span style="color:#a0a0a0; font-size:12px; width:60px;">Monthly 24/7</span><input id="in_hrs_1" class="calc-input" value="24" oninput="recalc(1)" style="width:40px;"><span style="color:gray; margin:0 2px;">x</span><input id="in_days_1" class="calc-input" value="30" oninput="recalc(1)" style="width:40px;"><span id="calc_res_1" class="calc-res">0.00 EGP</span></div>
                        <div class="calc-row"><span style="color:#00E5FF; font-size:12px; width:60px;">Custom Task</span><input id="in_hrs_2" class="calc-input" value="35:25:17" oninput="recalc(2)" style="width:95px;"><input id="in_days_2" type="hidden" value="1"><span id="calc_res_2" class="calc-res">0.00 EGP</span></div>
                        <span class="calc-sub">Supports "35", "35h", or "20:25:17"</span>
                    </div>
                </div>
                <script>
                    let currentWatts = 0; let price = 0;
                    async function update() {
                        try {
                            const res = await fetch('/api/data'); const data = await res.json();
                            currentWatts = data.total_w; price = data.price_per_kwh;
                            document.getElementById('total_w').innerText = Math.round(data.total_w) + " W";
                            document.getElementById('peak_w').innerText = "Peak: " + Math.round(data.peak_w) + " W";
                            
                            document.getElementById('cost_session').innerText = data.cost_session.toFixed(4) + " EGP";
                            document.getElementById('cost_today').innerText = data.cost_today.toFixed(4) + " EGP";
                            document.getElementById('cost_overall').innerText = data.cost_overall.toFixed(4) + " EGP";
                            document.getElementById('cost_est_month').innerText = data.cost_est_month.toFixed(2) + " EGP";
                            
                            if (data.cost_today > data.daily_limit) { document.getElementById('cost_today').style.color = '#FF4444'; }
                            else { document.getElementById('cost_today').style.color = '#00FF00'; }

                            const grid = document.getElementById('gpu_grid'); grid.innerHTML = "";
                            // NVIDIA GPUs
                            data.gpu_data.forEach(gpu => {
                                let subText = Math.round(gpu.temp) + " °C | " + Math.round(gpu.load) + " %";
                                if(gpu.vram_used > 1.0) { subText += "<br>VRAM: " + gpu.vram_used.toFixed(1) + " GB"; }
                                grid.innerHTML += `<div class="card"><span class="card-title" style="color:#76b900">${gpu.name}</span><div class="card-val">${Math.round(gpu.power)} W</div><div class="card-temp">${subText}</div></div>`;
                            });
                            // STATIC CARDS
                            grid.innerHTML += `<div class="card"><span class="card-title" style="color:#E040FB">iGPU</span><div class="card-val">${Math.round(data.igpu_w)} W</div><div class="card-temp">${Math.round(data.igpu_t)} °C</div></div>`;
                            grid.innerHTML += `<div class="card"><span class="card-title" style="color:#ff8c00">CPU</span><div class="card-val">${Math.round(data.cpu_w)} W</div><div class="card-temp">${Math.round(data.cpu_t)} °C</div></div>`;
                            grid.innerHTML += `<div class="card"><span class="card-title" style="color:#9E9E9E">System</span><div class="card-val">${Math.round(data.sys_w)} W</div><div class="card-temp">Fixed</div></div>`;

                            recalc(1); recalc(2);
                        } catch (e) { console.log(e); }
                    }
                    function parseTime(val) {
                        if (!val) return 0; val = val.toString().toLowerCase().replace("h", "").trim();
                        if (val.includes(":")) { let parts = val.split(":").map(Number); if (parts.length === 3) return parts[0] + parts[1]/60 + parts[2]/3600; if (parts.length === 2) return parts[0] + parts[1]/60; return 0; }
                        return parseFloat(val) || 0;
                    }
                    function recalc(id) {
                        let valStr = document.getElementById('in_hrs_' + id).value; let h = parseTime(valStr);
                        let d = parseFloat(document.getElementById('in_days_' + id).value) || 0;
                        let cost = (currentWatts / 1000.0) * h * d * price;
                        document.getElementById('calc_res_' + id).innerText = cost.toFixed(2) + " EGP";
                    }
                    setInterval(update, 1000); update();
                </script></body></html>""")
        @app.route('/api/data')
        def data(): return jsonify(SHARED_DATA)
        app.run(host='0.0.0.0', port=FLASK_PORT)

    # --- MONITORING LOGIC ---
    def setup_nvml(self):
        if pynvml:
            try:
                pynvml.nvmlInit()
                count = pynvml.nvmlDeviceGetCount()
                for i in range(count):
                    handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                    name = pynvml.nvmlDeviceGetName(handle)
                    if isinstance(name, bytes): name = name.decode()
                    short_name = name.replace("NVIDIA GeForce ", "").replace("NVIDIA ", "").replace(" RTX", "")
                    self.gpu_data.append({"handle": handle, "name": name, "short": short_name, "widget_pwr": None})
                self.nvml_active = True
            except: self.nvml_active = False
        else: self.nvml_active = False

    def load_data(self):
        today_str = datetime.now().strftime("%Y-%m-%d")
        default = {"last_date": today_str, "day_kwh": 0.0, "day_cost": 0.0, "day_seconds": 0, "lifetime_kwh": 0.0, "lifetime_cost": 0.0, "lifetime_seconds": 0}
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    data = json.load(f)
                    if "lifetime_seconds" not in data: data.update({"lifetime_seconds": 0, "day_seconds": 0})
                    if data.get("last_date") != today_str: data.update({"last_date": today_str, "day_kwh": 0.0, "day_cost": 0.0, "day_seconds": 0})
                    return data
            except: pass
        return default

    def save_data(self):
        with open(STATE_FILE, 'w') as f: json.dump(self.persistent_data, f, indent=4)

    def init_csv(self):
        headers = ["Timestamp", "Total Watts", "Total Cost", "CPU Temp", "CPU Watts", "CPU Load", "iGPU Temp", "iGPU Watts", "iGPU Load"]
        for i in range(len(self.gpu_data)): headers.extend([f"GPU{i} Temp", f"GPU{i} Watts", f"GPU{i} Load", f"GPU{i} VRAM Used"])
        if not os.path.exists(LOG_FILE):
            with open(LOG_FILE, mode='w', newline='') as f: csv.writer(f).writerow(headers)
        else:
            try:
                with open(LOG_FILE, 'r') as f: existing_headers = f.readline().strip().split(',')
                if "GPU0 VRAM Used" not in existing_headers:
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    os.rename(LOG_FILE, f"power_log_backup_{timestamp}.csv")
                    with open(LOG_FILE, mode='w', newline='') as f: csv.writer(f).writerow(headers)
            except: pass

    def log_to_csv(self, total_w, cpu_t, cpu_w, cpu_l, igpu_t, igpu_w, igpu_l, nv_metrics):
        try:
            row = [datetime.now().strftime("%Y-%m-%d %H:%M:%S"), f"{total_w:.1f}", f"{self.persistent_data['day_cost']:.4f}", 
                   f"{cpu_t:.1f}", f"{cpu_w:.1f}", f"{cpu_l:.1f}", f"{igpu_t:.1f}", f"{igpu_w:.1f}", f"{igpu_l:.1f}"]
            for metric in nv_metrics: 
                row.extend([f"{metric['temp']:.1f}", f"{metric['power']:.1f}", f"{metric['load']:.1f}", f"{metric['vram_used']:.2f}"])
            with open(LOG_FILE, mode='a', newline='') as f: csv.writer(f).writerow(row)
        except PermissionError: pass

    def fetch_lhm_data(self):
        if not IS_WINDOWS: return None
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
                    total = self.sum_radeon_powers(node); return True
                if "Children" in node:
                    for child in node["Children"]:
                        if scan(child): return True
            elif isinstance(node, list):
                for item in node:
                    if scan(item): return True
            return False
        scan(data); return total

    def sum_radeon_powers(self, node):
        acc = 0.0
        if "Children" in node:
            for child in node["Children"]:
                if child.get("Type") == "Power" and child.get("Text", "") in ["GPU Core", "GPU SoC", "GPU Power"]:
                    try: acc += float(str(child.get("Value", "0")).split()[0])
                    except: pass
                acc += self.sum_radeon_powers(child)
        return acc

    def get_color(self, temp): return "#FF4444" if temp > 80 else ("#ff8c00" if temp > 60 else "#76b900")
    def format_time(self, seconds): m, s = divmod(int(seconds), 60); h, m = divmod(m, 60); return f"{h:02d}:{m:02d}:{s:02d}"

    def background_monitor(self):
        last_save = time.time()
        while self.running:
            total_nvidia_w = 0; nv_metrics = []; shared_gpu_list = []
            real_cpu_load = psutil.cpu_percent(interval=None)

            if self.nvml_active:
                for gpu in self.gpu_data:
                    try: 
                        w = pynvml.nvmlDeviceGetPowerUsage(gpu['handle']) / 1000.0; total_nvidia_w += w
                        t = pynvml.nvmlDeviceGetTemperature(gpu['handle'], 0)
                        l = pynvml.nvmlDeviceGetUtilizationRates(gpu['handle']).gpu
                        mem_info = pynvml.nvmlDeviceGetMemoryInfo(gpu['handle'])
                        vram_used_gb = mem_info.used / (1024**3); vram_total_gb = mem_info.total / (1024**3)
                        nv_metrics.append({"power": w, "temp": t, "load": l, "vram_used": vram_used_gb})
                        shared_gpu_list.append({"name": gpu['short'], "power": w, "temp": t, "load": l, "vram_used": vram_used_gb})
                        if gpu['widget_pwr']:
                            status_text = f"{t} °C | {l} %"
                            if vram_used_gb > 1.0: status_text += f"\n{vram_used_gb:.1f} / {vram_total_gb:.1f} GB VRAM"
                            gpu['widget_temp'].configure(text=status_text, text_color=self.get_color(t))
                            gpu['widget_bar'].set(min(1.0, vram_used_gb / vram_total_gb))
                            gpu['widget_pwr'].configure(text=f"{int(w)} W")
                    except: nv_metrics.append({"power": 0, "temp": 0, "load": 0, "vram_used": 0})

            lhm = self.fetch_lhm_data()
            cpu_w, igpu_w, cpu_t, igpu_t = 0, 0, 0, 0; cpu_l, igpu_l = 0, 0; is_estimated = False; status_msg = "Status: Live Data"

            if lhm:
                cpu_w = self.find_sensor_value(lhm, ["Package", "CPU Package", "CPU PPT", "CPU Cores"], "Power")
                igpu_w = self.calculate_igpu_total(lhm)
                cpu_t = self.find_sensor_value(lhm, ["Core (Tctl/Tdie)", "Package", "Core #1"], "Temperature")
                igpu_t = self.find_sensor_value(lhm, ["GPU Core", "GPU Temperature"], "Temperature")
                cpu_l = self.find_sensor_value(lhm, ["Total", "CPU Total"], "Load")
                igpu_l = self.find_sensor_value(lhm, ["GPU Core", "D3D 3D", "Video Engine"], "Load") 
                if cpu_w == 0:
                    is_estimated = True; status_msg = "⚠️ LHM Error (Est. CPU)"
                    use_load = cpu_l if cpu_l > 0 else real_cpu_load
                    cpu_w = 25.0 + (use_load * 1.5); cpu_l = use_load 
            else:
                is_estimated = True; status_msg = "⚠️ LHM Down (Using Estimates)"
                cpu_l = real_cpu_load; cpu_w = 25.0 + (cpu_l * 1.5); igpu_w = 0; igpu_t = 0; igpu_l = 0; cpu_t = 0 
            
            total_w = total_nvidia_w + igpu_w + cpu_w + self.cfg_overhead
            if total_w > self.peak_w: self.peak_w = total_w

            kwh_inc = (total_w * 1.0) / 3_600_000
            cost_inc = kwh_inc * DEFAULT_PRICE
            self.session_data["kwh"] += kwh_inc; self.session_data["cost"] += cost_inc
            self.persistent_data["day_kwh"] += kwh_inc; self.persistent_data["day_cost"] += cost_inc; self.persistent_data["day_seconds"] += 1
            self.persistent_data["lifetime_kwh"] += kwh_inc; self.persistent_data["lifetime_cost"] += cost_inc; self.persistent_data["lifetime_seconds"] += 1

            if datetime.now().strftime("%Y-%m-%d") != self.persistent_data["last_date"]:
                self.persistent_data.update({"last_date": datetime.now().strftime("%Y-%m-%d"), "day_kwh": 0.0, "day_cost": 0.0, "day_seconds": 0})

            est_cost_month = (total_w / 1000.0) * 24.0 * 30.0 * DEFAULT_PRICE

            SHARED_DATA["total_w"] = total_w; SHARED_DATA["peak_w"] = self.peak_w
            SHARED_DATA["cpu_w"] = cpu_w; SHARED_DATA["cpu_t"] = cpu_t; SHARED_DATA["cpu_load"] = cpu_l
            SHARED_DATA["igpu_w"] = igpu_w; SHARED_DATA["igpu_t"] = igpu_t; SHARED_DATA["igpu_load"] = igpu_l
            SHARED_DATA["sys_w"] = self.cfg_overhead; SHARED_DATA["gpu_data"] = shared_gpu_list
            SHARED_DATA["cost_session"] = self.session_data["cost"]; SHARED_DATA["cost_today"] = self.persistent_data["day_cost"]
            SHARED_DATA["cost_overall"] = self.persistent_data["lifetime_cost"]; SHARED_DATA["cost_est_month"] = est_cost_month
            SHARED_DATA["alert"] = self.persistent_data["day_cost"] > self.cfg_limit; SHARED_DATA["price_per_kwh"] = DEFAULT_PRICE; SHARED_DATA["daily_limit"] = self.cfg_limit

            try:
                self.update_chart_data(total_w)
                self.lbl_watts.configure(text=f"{int(total_w)} W"); self.lbl_peak.configure(text=f"Peak: {int(self.peak_w)} W")
                if total_w > 500: self.lbl_watts.configure(text_color=COLOR_CRIT)
                elif total_w > 300: self.lbl_watts.configure(text_color=COLOR_WARN)
                else: self.lbl_watts.configure(text_color=COLOR_ACCENT)

                # UPDATE GPU WIDGETS
                for gpu in self.gpu_data:
                    if gpu['widget_pwr']:
                        gpu['widget_pwr'].configure(text=f"{int(pynvml.nvmlDeviceGetPowerUsage(gpu['handle']) / 1000.0)} W")

                self.card_igpu['lbl_val'].configure(text=f"{int(igpu_w)} W")
                self.card_igpu['lbl_temp'].configure(text=f"{int(igpu_t)} °C  |  {int(igpu_l)} %", text_color=self.get_color(igpu_t))
                self.card_igpu['bar'].set(min(1.0, igpu_w / TDP_IGPU))
                
                self.card_cpu['lbl_val'].configure(text=f"{int(cpu_w)} W", text_color="gray" if is_estimated else COLOR_TEXT_MAIN)
                self.card_cpu['lbl_temp'].configure(text=f"{int(cpu_t)} °C  |  {int(cpu_l)} %", text_color=self.get_color(cpu_t))
                self.card_cpu['bar'].set(min(1.0, cpu_w / TDP_CPU))

                self.lbl_session_time.configure(text=self.format_time(time.time() - self.start_time))
                self.lbl_session_cost.configure(text=f"{self.session_data['cost']:.4f}")
                self.lbl_session_kwh.configure(text=f"{self.session_data['kwh']:.4f} kWh")
                self.lbl_today_time.configure(text=self.format_time(self.persistent_data["day_seconds"]))
                self.lbl_today_cost.configure(text=f"{self.persistent_data['day_cost']:.4f}")
                self.lbl_today_kwh.configure(text=f"{self.persistent_data['day_kwh']:.4f} kWh")
                self.lbl_overall_time.configure(text=self.format_time(self.persistent_data["lifetime_seconds"]))
                self.lbl_overall_cost.configure(text=f"{self.persistent_data['lifetime_cost']:.4f}")
                self.lbl_overall_kwh.configure(text=f"{self.persistent_data['lifetime_kwh']:.4f} kWh")

                if self.persistent_data["day_cost"] > self.cfg_limit:
                    self.lbl_today_cost.configure(text_color=COLOR_CRIT); self.lbl_status.configure(text="⚠️ DAILY BUDGET EXCEEDED ⚠️" if int(time.time())%2==0 else f"Limit: {self.cfg_limit} EGP", text_color=COLOR_CRIT)
                else:
                    self.lbl_today_cost.configure(text_color="#00FF00"); self.lbl_status.configure(text=status_msg, text_color="gray")
            except: pass

            self.log_to_csv(total_w, cpu_t, cpu_w, cpu_l, igpu_t, igpu_w, igpu_l, nv_metrics)
            if time.time() - last_save > 60: self.save_data(); last_save = time.time()
            time.sleep(self.cfg_interval)

    def on_close(self): self.running = False; self.save_data(); self.destroy()

if __name__ == "__main__": app = PowerMonitorApp(); app.mainloop()