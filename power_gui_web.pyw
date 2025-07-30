import customtkinter as ctk
import threading
import time
import json
import os
import csv
import socket
import webbrowser
import pynvml
import requests
from datetime import datetime
from collections import deque

# --- FLASK (WEB SERVER) ---
from flask import Flask, jsonify, render_template_string
import logging

# --- MATPLOTLIB ---
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# --- CONFIGURATION ---
PRICE_PER_KWH = 2.14          
DAILY_LIMIT_EGP = 10.00       
STATE_FILE = "power_state.json"
LOG_FILE = "power_log.csv"
LHM_URL = "http://localhost:8085/data.json"
FLASK_PORT = 5000
CSV_LOG_INTERVAL = 1  # <--- NEW: Log every 1 second (was 60)

# --- SHARED DATA ---
SHARED_DATA = {
    "total_w": 0, "peak_w": 0,
    "cpu_w": 0, "cpu_t": 0, "cpu_load": 0,
    "igpu_w": 0, "igpu_t": 0, "igpu_load": 0,
    "gpu_data": [], 
    "cost_session": 0.0, "cost_today": 0.0, "cost_overall": 0.0,
    "time_session": "00:00:00",
    "alert": False
}

# --- THEME COLORS ---
COLOR_BG = "#1a1a1a"
COLOR_CARD = "#2b2b2b"
COLOR_TEXT_MAIN = "#ffffff"
COLOR_TEXT_SUB = "#a0a0a0"
COLOR_ACCENT = "#00E5FF"    
COLOR_WARN = "#FFD700"      
COLOR_CRIT = "#FF4444"      

# --- ESTIMATED TDP ---
TDP_NVIDIA_HIGH = 285 
TDP_NVIDIA_MID = 120  
TDP_CPU = 170         
TDP_IGPU = 60         

os.environ["PATH"] += os.pathsep + os.getcwd()
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

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
        
        self.title("⚡ Power Monitor (High Frequency Log)")
        self.geometry(f"{window_width}x900") 
        self.configure(fg_color=COLOR_BG)
        self.resizable(True, True)

        # 2. Data Init
        self.running = True
        self.start_time = time.time()
        self.peak_w = 0 
        self.session_data = {"kwh": 0.0, "cost": 0.0}
        self.persistent_data = self.load_data()
        
        # Graph Data
        self.history_x = deque(maxlen=60)
        self.history_y = deque(maxlen=60)
        for i in range(60): 
            self.history_x.append(i)
            self.history_y.append(0)

        self.save_data()
        self.init_csv()

        # --- UI LAYOUT ---
        
        # A. HEADER
        self.frame_header = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_header.pack(pady=(15, 5), fill="x")
        
        self.lbl_title = ctk.CTkLabel(self.frame_header, text="SYSTEM POWER DRAW", font=("Roboto Medium", 12), text_color=COLOR_TEXT_SUB)
        self.lbl_title.pack(pady=(0, 0))

        self.lbl_watts = ctk.CTkLabel(self.frame_header, text="--- W", font=("Roboto", 64, "bold"), text_color=COLOR_ACCENT)
        self.lbl_watts.pack(pady=0)
        
        self.lbl_peak = ctk.CTkLabel(self.frame_header, text="Peak: 0 W", font=("Arial", 11), text_color="gray")
        self.lbl_peak.pack(pady=(0, 0))

        # B. HARDWARE CARDS
        self.frame_hw = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_hw.pack(pady=10, padx=20, fill="x")
        self.frame_hw.grid_columnconfigure(0, weight=1)
        col_idx = 1
        
        if self.nvml_active:
            for i, gpu in enumerate(self.gpu_data):
                short_name = gpu['name'].replace("NVIDIA GeForce ", "").replace("NVIDIA ", "").replace(" RTX", "")
                est_max = TDP_NVIDIA_HIGH if "4070" in short_name or "3080" in short_name else TDP_NVIDIA_MID
                
                card = self.create_metric_card(self.frame_hw, short_name, "#76b900", est_max)
                card['frame'].grid(row=0, column=col_idx, padx=8)
                
                self.gpu_data[i]['widget_pwr'] = card['lbl_val']
                self.gpu_data[i]['widget_temp'] = card['lbl_temp']
                self.gpu_data[i]['widget_bar'] = card['bar']
                self.gpu_data[i]['max_w'] = est_max
                col_idx += 1

        self.card_igpu = self.create_metric_card(self.frame_hw, "iGPU (Radeon)", "#E040FB", TDP_IGPU)
        self.card_igpu['frame'].grid(row=0, column=col_idx, padx=8)
        col_idx += 1

        self.card_cpu = self.create_metric_card(self.frame_hw, "Ryzen 9900X", "#ff8c00", TDP_CPU)
        self.card_cpu['frame'].grid(row=0, column=col_idx, padx=8)
        col_idx += 1
        self.frame_hw.grid_columnconfigure(col_idx, weight=1)

        # C. LIVE CHART
        self.frame_chart = ctk.CTkFrame(self, fg_color=COLOR_CARD, corner_radius=12, height=200)
        self.frame_chart.pack(pady=10, padx=25, fill="x")
        self.setup_chart()

        # D. STATS PANEL
        self.frame_stats = ctk.CTkFrame(self, fg_color=COLOR_CARD, corner_radius=12)
        self.frame_stats.pack(pady=10, padx=25, fill="x", ipadx=15, ipady=5)
        self.frame_stats.grid_columnconfigure(0, weight=1)
        self.frame_stats.grid_columnconfigure(1, weight=1)
        self.frame_stats.grid_columnconfigure(2, weight=1)
        self.frame_stats.grid_columnconfigure(3, weight=1)

        h_font = ("Arial", 10, "bold")
        ctk.CTkLabel(self.frame_stats, text="TIMELINE", font=h_font, text_color=COLOR_TEXT_SUB).grid(row=0, column=0, pady=10, sticky="w")
        ctk.CTkLabel(self.frame_stats, text="COST (EGP)", font=h_font, text_color=COLOR_TEXT_SUB).grid(row=0, column=1, pady=10, sticky="e")
        ctk.CTkLabel(self.frame_stats, text="ENERGY", font=h_font, text_color=COLOR_TEXT_SUB).grid(row=0, column=2, pady=10, sticky="e")
        ctk.CTkLabel(self.frame_stats, text="DURATION", font=h_font, text_color=COLOR_TEXT_SUB).grid(row=0, column=3, pady=10, sticky="e")

        self.create_stat_row(1, "Session", COLOR_ACCENT)
        self.create_stat_row(2, "Today", "#00FF00")
        self.create_stat_row(3, "Overall", "#FFA500")

        # E. FOOTER
        self.frame_footer = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_footer.pack(side="bottom", pady=10, fill="x")

        self.lbl_status = ctk.CTkLabel(self.frame_footer, text="Initializing...", text_color="gray", font=("Arial", 11))
        self.lbl_status.pack()
        
        # IP Links
        ips = self.get_all_ips()
        if not ips:
             ctk.CTkLabel(self.frame_footer, text="No Network Found", text_color="gray").pack()
        else:
            for ip in ips:
                link = f"http://{ip}:{FLASK_PORT}"
                label_text = f"☁️ Tailscale: {link}" if ip.startswith("100.") else f"🏠 Home LAN: {link}"
                color = "#E040FB" if ip.startswith("100.") else COLOR_ACCENT
                
                lbl = ctk.CTkLabel(self.frame_footer, text=label_text, text_color=color, font=("Arial", 12, "bold"), cursor="hand2")
                lbl.pack(pady=2)
                lbl.bind("<Button-1>", lambda e, url=link: webbrowser.open(url))

        # F. THREADS
        self.monitor_thread = threading.Thread(target=self.background_monitor, daemon=True)
        self.monitor_thread.start()
        
        self.flask_thread = threading.Thread(target=self.run_flask_server, daemon=True)
        self.flask_thread.start()
        
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def get_all_ips(self):
        ip_list = []
        try:
            hostname = socket.gethostname()
            for ip in socket.gethostbyname_ex(hostname)[2]:
                if not ip.startswith("127."):
                    ip_list.append(ip)
        except: pass
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            main_ip = s.getsockname()[0]
            s.close()
            if main_ip not in ip_list:
                ip_list.insert(0, main_ip)
        except: pass
        return list(dict.fromkeys(ip_list))

    def setup_chart(self):
        self.fig = Figure(figsize=(5, 2), dpi=100)
        self.fig.patch.set_facecolor(COLOR_CARD)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor(COLOR_CARD)
        self.line, = self.ax.plot([], [], color=COLOR_ACCENT, linewidth=2)
        self.ax.grid(True, color="#404040", linestyle='--', linewidth=0.5)
        self.ax.spines['top'].set_visible(False)
        self.ax.spines['right'].set_visible(False)
        self.ax.spines['bottom'].set_color('#404040')
        self.ax.spines['left'].set_color('#404040')
        self.ax.tick_params(axis='x', colors='gray', labelsize=8)
        self.ax.tick_params(axis='y', colors='gray', labelsize=8)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.frame_chart)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=5, pady=5)

    def update_chart_data(self, new_val):
        self.history_y.append(new_val)
        self.line.set_data(self.history_x, self.history_y)
        self.ax.set_ylim(0, max(max(self.history_y) * 1.2, 100))
        self.ax.set_xlim(0, 60)
        self.canvas.draw()

    def create_metric_card(self, parent, title, title_color, max_val_estimate):
        frame = ctk.CTkFrame(parent, width=155, height=140, fg_color=COLOR_CARD, corner_radius=10)
        frame.pack_propagate(False)
        ctk.CTkLabel(frame, text=title, font=("Arial", 11, "bold"), text_color=title_color).pack(pady=(12, 2))
        lbl_val = ctk.CTkLabel(frame, text="0 W", font=("Roboto", 24, "bold"), text_color=COLOR_TEXT_MAIN)
        lbl_val.pack(pady=0)
        bar = ctk.CTkProgressBar(frame, width=100, height=6, progress_color=title_color)
        bar.set(0)
        bar.pack(pady=(5, 5))
        lbl_temp = ctk.CTkLabel(frame, text="-- °C | -- %", font=("Arial", 11), text_color=COLOR_TEXT_SUB)
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

    # --- FLASK SERVER ---
    def run_flask_server(self):
        app = Flask(__name__)

        @app.route('/')
        def index():
            return render_template_string("""
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Power Monitor</title>
                <style>
                    body { background-color: #1a1a1a; color: white; font-family: 'Segoe UI', sans-serif; text-align: center; padding: 20px; }
                    h1 { margin-bottom: 5px; color: #a0a0a0; font-size: 16px; }
                    .watts { font-size: 60px; font-weight: bold; color: #00E5FF; margin: 0; }
                    .peak { color: gray; font-size: 14px; margin-bottom: 20px; }
                    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 20px; }
                    .card { background: #2b2b2b; padding: 15px; border-radius: 10px; }
                    .card-title { font-size: 12px; font-weight: bold; margin-bottom: 5px; display: block; }
                    .card-val { font-size: 22px; font-weight: bold; }
                    .card-temp { font-size: 11px; color: #a0a0a0; margin-top:5px; }
                    .stats { background: #2b2b2b; border-radius: 10px; padding: 15px; text-align: left; }
                    .row { display: flex; justify-content: space-between; margin-bottom: 10px; border-bottom: 1px solid #404040; padding-bottom: 5px; }
                    .row:last-child { border: none; margin: 0; }
                    .label { font-size: 14px; }
                    .cost { font-size: 16px; font-weight: bold; }
                    .alert { color: #FF4444 !important; }
                </style>
            </head>
            <body>
                <h1>SYSTEM POWER DRAW</h1>
                <div id="total_w" class="watts">--- W</div>
                <div id="peak_w" class="peak">Peak: --- W</div>

                <div class="grid" id="gpu_grid"></div>

                <div class="grid">
                     <div class="card">
                        <span class="card-title" style="color:#E040FB">iGPU (Radeon)</span>
                        <div id="igpu_w" class="card-val">0 W</div>
                        <div id="igpu_stats" class="card-temp">-- °C | -- %</div>
                    </div>
                    <div class="card">
                        <span class="card-title" style="color:#ff8c00">Ryzen 9900X</span>
                        <div id="cpu_w" class="card-val">0 W</div>
                        <div id="cpu_stats" class="card-temp">-- °C | -- %</div>
                    </div>
                </div>

                <div class="stats">
                    <div class="row">
                        <span class="label">Session</span>
                        <span id="cost_session" class="cost" style="color:#00E5FF">0.00 EGP</span>
                    </div>
                    <div class="row">
                        <span class="label">Today</span>
                        <span id="cost_today" class="cost" style="color:#00FF00">0.00 EGP</span>
                    </div>
                    <div class="row">
                        <span class="label">Overall</span>
                        <span id="cost_overall" class="cost" style="color:#FFA500">0.00 EGP</span>
                    </div>
                </div>

                <script>
                    async function update() {
                        try {
                            const res = await fetch('/api/data');
                            const data = await res.json();
                            
                            document.getElementById('total_w').innerText = Math.round(data.total_w) + " W";
                            document.getElementById('total_w').style.color = data.total_w > 500 ? "#FF4444" : (data.total_w > 300 ? "#FFD700" : "#00E5FF");
                            document.getElementById('peak_w').innerText = "Peak: " + Math.round(data.peak_w) + " W";
                            
                            document.getElementById('cpu_w').innerText = Math.round(data.cpu_w) + " W";
                            document.getElementById('cpu_stats').innerText = Math.round(data.cpu_t) + " °C | " + Math.round(data.cpu_load) + " %";
                            
                            document.getElementById('igpu_w').innerText = Math.round(data.igpu_w) + " W";
                            document.getElementById('igpu_stats').innerText = Math.round(data.igpu_t) + " °C | " + Math.round(data.igpu_load) + " %";

                            document.getElementById('cost_session').innerText = data.cost_session.toFixed(4) + " EGP";
                            document.getElementById('cost_today').innerText = data.cost_today.toFixed(4) + " EGP";
                            document.getElementById('cost_overall').innerText = data.cost_overall.toFixed(4) + " EGP";

                            if (data.alert) {
                                document.getElementById('cost_today').classList.add("alert");
                            }

                            const grid = document.getElementById('gpu_grid');
                            grid.innerHTML = "";
                            data.gpu_data.forEach(gpu => {
                                grid.innerHTML += `
                                    <div class="card">
                                        <span class="card-title" style="color:#76b900">${gpu.name}</span>
                                        <div class="card-val">${Math.round(gpu.power)} W</div>
                                        <div class="card-temp">${Math.round(gpu.temp)} °C | ${Math.round(gpu.load)} %</div>
                                    </div>
                                `;
                            });

                        } catch (e) { console.log(e); }
                    }
                    setInterval(update, 1000);
                    update();
                </script>
            </body>
            </html>
            """)

        @app.route('/api/data')
        def data():
            return jsonify(SHARED_DATA)

        app.run(host='0.0.0.0', port=FLASK_PORT)

    # --- MONITORING LOGIC ---
    def setup_nvml(self):
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
        # Header definition
        headers = ["Timestamp", "Total Watts", "Total Cost", "CPU Temp", "CPU Watts", "CPU Load", "iGPU Temp", "iGPU Watts", "iGPU Load"]
        for i in range(len(self.gpu_data)): headers.extend([f"GPU{i} Temp", f"GPU{i} Watts", f"GPU{i} Load"])
        
        should_create = False
        if not os.path.exists(LOG_FILE):
            should_create = True
        else:
            # CHECK if headers match (Auto-Upgrade old files)
            try:
                with open(LOG_FILE, 'r') as f:
                    existing_headers = f.readline().strip().split(',')
                if "CPU Load" not in existing_headers:
                    print("⚠️ Old CSV detected. Backing up and upgrading...")
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    os.rename(LOG_FILE, f"power_log_backup_{timestamp}.csv")
                    should_create = True
            except: 
                should_create = True
        
        if should_create:
            with open(LOG_FILE, mode='w', newline='') as f: csv.writer(f).writerow(headers)

    def log_to_csv(self, total_w, cpu_t, cpu_w, cpu_l, igpu_t, igpu_w, igpu_l, nv_metrics):
        try:
            row = [datetime.now().strftime("%Y-%m-%d %H:%M:%S"), f"{total_w:.1f}", f"{self.persistent_data['day_cost']:.4f}", 
                   f"{cpu_t:.1f}", f"{cpu_w:.1f}", f"{cpu_l:.1f}", 
                   f"{igpu_t:.1f}", f"{igpu_w:.1f}", f"{igpu_l:.1f}"]
            for metric in nv_metrics: row.extend([f"{metric['temp']:.1f}", f"{metric['power']:.1f}", f"{metric['load']:.1f}"])
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
        last_save = time.time()
        while self.running:
            total_nvidia_w = 0
            nv_metrics = [] 
            shared_gpu_list = [] 

            # NVIDIA
            if self.nvml_active:
                for gpu in self.gpu_data:
                    try: 
                        w = pynvml.nvmlDeviceGetPowerUsage(gpu['handle']) / 1000.0
                        total_nvidia_w += w
                        t = pynvml.nvmlDeviceGetTemperature(gpu['handle'], 0)
                        
                        load_struct = pynvml.nvmlDeviceGetUtilizationRates(gpu['handle'])
                        l = load_struct.gpu
                        
                        nv_metrics.append({"power": w, "temp": t, "load": l})
                        shared_gpu_list.append({"name": gpu['short'], "power": w, "temp": t, "load": l})
                        
                        if gpu['widget_pwr']:
                            ratio = min(1.0, w / gpu['max_w'])
                            gpu['widget_pwr'].configure(text=f"{int(w)} W")
                            gpu['widget_temp'].configure(text=f"{t} °C  |  {l} %", text_color=self.get_color(t))
                            gpu['widget_bar'].set(ratio)
                    except: 
                        nv_metrics.append({"power": 0, "temp": 0, "load": 0})

            # LHM
            lhm = self.fetch_lhm_data()
            cpu_w, igpu_w, cpu_t, igpu_t = 0, 0, 0, 0
            cpu_l, igpu_l = 0, 0
            is_estimated = False
            status_msg = "Status: Live Data"

            if lhm:
                cpu_w = self.find_sensor_value(lhm, ["Package", "CPU Package"], "Power")
                igpu_w = self.calculate_igpu_total(lhm)
                cpu_t = self.find_sensor_value(lhm, ["Core (Tctl/Tdie)", "Package", "Core #1"], "Temperature")
                igpu_t = self.find_sensor_value(lhm, ["GPU Core", "GPU Temperature"], "Temperature")
                
                cpu_l = self.find_sensor_value(lhm, ["Total", "CPU Total"], "Load")
                igpu_l = self.find_sensor_value(lhm, ["GPU Core", "D3D 3D", "Video Engine"], "Load") 
                
                if cpu_w == 0: is_estimated = True
            else:
                is_estimated = True
                status_msg = "Status: Estimating (LHM Off)"
                if not self.nvml_active: cpu_w = 55 
            
            total_w = total_nvidia_w + igpu_w + cpu_w + 55
            if total_w > self.peak_w: self.peak_w = total_w

            # Metrics
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

            # SHARED DATA
            SHARED_DATA["total_w"] = total_w
            SHARED_DATA["peak_w"] = self.peak_w
            SHARED_DATA["cpu_w"] = cpu_w
            SHARED_DATA["cpu_t"] = cpu_t
            SHARED_DATA["cpu_load"] = cpu_l
            SHARED_DATA["igpu_w"] = igpu_w
            SHARED_DATA["igpu_t"] = igpu_t
            SHARED_DATA["igpu_load"] = igpu_l
            SHARED_DATA["gpu_data"] = shared_gpu_list
            SHARED_DATA["cost_session"] = self.session_data["cost"]
            SHARED_DATA["cost_today"] = self.persistent_data["day_cost"]
            SHARED_DATA["cost_overall"] = self.persistent_data["lifetime_cost"]
            SHARED_DATA["alert"] = self.persistent_data["day_cost"] > DAILY_LIMIT_EGP

            # UI Update
            try:
                self.update_chart_data(total_w)
                self.lbl_watts.configure(text=f"{int(total_w)} W")
                self.lbl_peak.configure(text=f"Peak: {int(self.peak_w)} W")
                
                if total_w > 500: self.lbl_watts.configure(text_color=COLOR_CRIT)
                elif total_w > 300: self.lbl_watts.configure(text_color=COLOR_WARN)
                else: self.lbl_watts.configure(text_color=COLOR_ACCENT)

                # iGPU Card
                self.card_igpu['lbl_val'].configure(text=f"{int(igpu_w)} W")
                self.card_igpu['lbl_temp'].configure(text=f"{int(igpu_t)} °C  |  {int(igpu_l)} %", text_color=self.get_color(igpu_t))
                self.card_igpu['bar'].set(min(1.0, igpu_w / TDP_IGPU))
                
                # CPU Card
                self.card_cpu['lbl_val'].configure(text=f"{int(cpu_w)} W")
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

                if self.persistent_data["day_cost"] > DAILY_LIMIT_EGP:
                    self.lbl_today_cost.configure(text_color=COLOR_CRIT)
                    self.lbl_status.configure(text="⚠️ DAILY BUDGET EXCEEDED ⚠️" if int(time.time())%2==0 else f"Limit: {DAILY_LIMIT_EGP} EGP", text_color=COLOR_CRIT)
                else:
                    self.lbl_today_cost.configure(text_color="#00FF00")
                    self.lbl_status.configure(text=status_msg, text_color="gray")
            except: pass

            # LOGGING LOGIC
            self.log_to_csv(total_w, cpu_t, cpu_w, cpu_l, igpu_t, igpu_w, igpu_l, nv_metrics)
            
            # SAVE LOGIC (Every 60s)
            if time.time() - last_save > 60:
                self.save_data()
                last_save = time.time()
                
            time.sleep(1)

    def on_close(self):
        self.running = False
        self.save_data()
        self.destroy()

if __name__ == "__main__":
    app = PowerMonitorApp()
    app.mainloop()