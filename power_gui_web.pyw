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
import ctypes
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
from flask import Flask, jsonify, render_template_string, send_file
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
HISTORY_FILE = "power_history.json"
LOG_FILE = "power_log_detailed.csv"
LHM_URL = "http://localhost:8085/data.json"
FLASK_PORT = 5000
MAX_HISTORY_DAYS = 365

# --- SHARED DATA ---
SHARED_DATA = {
    "total_w": 0, "peak_w": 0,
    "cpu_w": 0, "cpu_t": 0, "cpu_load": 0,
    "igpu_w": 0, "igpu_t": 0, "igpu_load": 0,
    "sys_w": 80,
    "gpu_data": [],
    "has_igpu": False,
    "cost_session": 0.0, "cost_today": 0.0,
    "kwh_today": 0.0,
    "cost_month_est": 0.0, "cost_month_real": 0.0,
    "history": [],
    "top_cpu": [],
    "top_gpu": []
}

# --- THEME ---
COLOR_BG = "#1a1a1a"
COLOR_CARD = "#2b2b2b"
COLOR_TEXT_MAIN = "#ffffff"
COLOR_TEXT_SUB = "#a0a0a0"
COLOR_ACCENT = "#00E5FF"
COLOR_SYS = "#9E9E9E"

# --- TDP ESTIMATES ---
TDP_NVIDIA_HIGH = 285
TDP_CPU = 170
TDP_IGPU = 60
TDP_SYS = 150

IS_WINDOWS = platform.system() == "Windows"
os.environ["PATH"] += os.pathsep + os.getcwd()
CPU_COUNT = psutil.cpu_count()

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")
log = logging.getLogger('werkzeug');
log.setLevel(logging.ERROR)


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False


class PowerMonitorApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        # 1. Hardware Init
        self.gpu_data = []
        self.nvml_active = False
        self.setup_nvml()
        self.detected_cpu_name = self.detect_cpu_name()

        # 2. Check for iGPU
        lhm_data = self.fetch_lhm_data()
        self.has_igpu = False
        if lhm_data:
            if self.calculate_igpu_total(lhm_data) > 1.0: self.has_igpu = True
        SHARED_DATA["has_igpu"] = self.has_igpu

        # 3. Window Setup
        num_cards = len(self.gpu_data) + 2 + (1 if self.has_igpu else 0)
        window_width = max(950, num_cards * 160 + 50)

        title_extra = "" if is_admin() else " (Run as Admin for improved Data)"
        self.title(f"⚡ Windows Command Center{title_extra}")
        self.geometry(f"{window_width}x1150")
        self.configure(fg_color=COLOR_BG)
        self.resizable(True, True)

        # 4. Data Init
        self.running = True
        self.start_time = time.time()
        self.peak_w = 0
        self.persistent_data = self.load_data()
        self.history_data = self.load_history()

        self.cfg_overhead = 80.0
        self.cfg_interval = 1

        self.history_x = deque(maxlen=60)
        self.history_y = deque(maxlen=60)
        for i in range(60): self.history_x.append(i); self.history_y.append(0)

        self.save_data()
        self.init_csv()

        # --- UI LAYOUT ---
        self.main_scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.main_scroll.pack(fill="both", expand=True)

        self.frame_header = ctk.CTkFrame(self.main_scroll, fg_color="transparent")
        self.frame_header.pack(pady=(15, 5), fill="x")
        self.lbl_title = ctk.CTkLabel(self.frame_header, text="WINDOWS REAL-TIME POWER", font=("Roboto Medium", 12),
                                      text_color=COLOR_TEXT_SUB)
        self.lbl_title.pack()
        self.lbl_watts = ctk.CTkLabel(self.frame_header, text="--- W", font=("Roboto", 64, "bold"),
                                      text_color=COLOR_ACCENT)
        self.lbl_watts.pack()
        self.lbl_peak = ctk.CTkLabel(self.frame_header, text="Peak: 0 W", font=("Arial", 11), text_color="gray")
        self.lbl_peak.pack()

        # Hardware Grid
        self.frame_hw = ctk.CTkFrame(self.main_scroll, fg_color="transparent")
        self.frame_hw.pack(pady=10, padx=20, fill="x")

        col_idx = 0
        if self.nvml_active:
            for i, gpu in enumerate(self.gpu_data):
                card = self.create_metric_card(self.frame_hw, gpu['short'], "#76b900", 250)
                card['frame'].grid(row=0, column=col_idx, padx=5)
                self.gpu_data[i]['widget_pwr'] = card['lbl_val']
                self.gpu_data[i]['widget_temp'] = card['lbl_temp']
                self.gpu_data[i]['widget_bar'] = card['bar']
                col_idx += 1

        if self.has_igpu:
            self.card_igpu = self.create_metric_card(self.frame_hw, "iGPU (Radeon)", "#E040FB", TDP_IGPU)
            self.card_igpu['frame'].grid(row=0, column=col_idx, padx=5)
            col_idx += 1

        self.card_cpu = self.create_metric_card(self.frame_hw, self.detected_cpu_name, "#ff8c00", TDP_CPU)
        self.card_cpu['frame'].grid(row=0, column=col_idx, padx=5)
        col_idx += 1

        self.card_sys = self.create_metric_card(self.frame_hw, "System Overhead", COLOR_SYS, TDP_SYS)
        self.card_sys['frame'].grid(row=0, column=col_idx, padx=5)
        self.card_sys['lbl_val'].configure(text=f"{int(self.cfg_overhead)} W")
        self.card_sys['lbl_temp'].configure(text="Mobo/Ram/Fans")
        self.card_sys['bar'].set(self.cfg_overhead / TDP_SYS)

        for i in range(col_idx + 1): self.frame_hw.grid_columnconfigure(i, weight=1)

        # --- MID SECTION ---
        self.frame_mid = ctk.CTkFrame(self.main_scroll, fg_color="transparent")
        self.frame_mid.pack(pady=10, padx=25, fill="x")
        self.frame_mid.grid_columnconfigure(0, weight=1)
        self.frame_mid.grid_columnconfigure(1, weight=1)

        # 1. Stats Box
        self.frame_stats = ctk.CTkFrame(self.frame_mid, fg_color=COLOR_CARD, corner_radius=12)
        self.frame_stats.grid(row=0, column=0, sticky="nsew", padx=(0, 10), ipady=5)

        ctk.CTkLabel(self.frame_stats, text="ENERGY COSTS", font=("Arial", 12, "bold"), text_color=COLOR_TEXT_SUB).pack(
            pady=10)
        self.create_stat_row(self.frame_stats, "Session", COLOR_ACCENT)
        self.create_stat_row(self.frame_stats, "Today", "#00FF00")
        self.create_stat_row(self.frame_stats, "Overall", "#FFA500")

        f_cfg = ctk.CTkFrame(self.frame_stats, fg_color="transparent")
        f_cfg.pack(pady=10)
        ctk.CTkLabel(f_cfg, text="Overhead:", font=("Arial", 11)).pack(side="left")
        self.entry_overhead = ctk.CTkEntry(f_cfg, width=40);
        self.entry_overhead.pack(side="left", padx=5);
        self.entry_overhead.insert(0, str(int(self.cfg_overhead)))
        ctk.CTkButton(f_cfg, text="Set", width=40, command=self.apply_config, fg_color="#404040", height=24).pack(
            side="left")

        # 2. Top Processes Box
        self.frame_procs = ctk.CTkFrame(self.frame_mid, fg_color=COLOR_CARD, corner_radius=12)
        self.frame_procs.grid(row=0, column=1, sticky="nsew", padx=(10, 0), ipady=5)

        ctk.CTkLabel(self.frame_procs, text="TASK MANAGER", font=("Arial", 12, "bold"), text_color=COLOR_TEXT_SUB).pack(
            pady=10)

        self.f_proc_cols = ctk.CTkFrame(self.frame_procs, fg_color="transparent")
        self.f_proc_cols.pack(fill="both", expand=True, padx=10)

        # Left: CPU Column
        self.lbl_top_cpu = ctk.CTkLabel(self.f_proc_cols, text="Scanning...", font=("Consolas", 11), justify="left",
                                        text_color="#ff8c00")
        self.lbl_top_cpu.pack(side="left", fill="both", expand=True, anchor="n")

        # Right: GPU Column
        self.lbl_top_gpu = ctk.CTkLabel(self.f_proc_cols, text="Scanning...", font=("Consolas", 11), justify="left",
                                        text_color="#76b900")
        self.lbl_top_gpu.pack(side="right", fill="both", expand=True, anchor="n")

        # Chart Section
        self.frame_chart = ctk.CTkFrame(self.main_scroll, fg_color=COLOR_CARD, corner_radius=12, height=200)
        self.frame_chart.pack(pady=10, padx=25, fill="x")
        self.setup_chart()

        # Footer
        self.frame_footer = ctk.CTkFrame(self.main_scroll, fg_color="transparent")
        self.frame_footer.pack(pady=20, fill="x")

        ips = self.get_all_ips()
        if ips:
            for ip in ips:
                link = f"http://{ip}:{FLASK_PORT}"
                lbl = ctk.CTkLabel(self.frame_footer, text=f"🌐 Dashboard: {link}", text_color=COLOR_ACCENT,
                                   font=("Arial", 12, "bold"), cursor="hand2")
                lbl.pack(pady=2)
                lbl.bind("<Button-1>", lambda e, url=link: webbrowser.open(url))

        # Threads
        self.monitor_thread = threading.Thread(target=self.background_monitor, daemon=True)
        self.monitor_thread.start()
        self.proc_thread = threading.Thread(target=self.process_monitor, daemon=True)
        self.proc_thread.start()
        self.flask_thread = threading.Thread(target=self.run_flask_server, daemon=True)
        self.flask_thread.start()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # --- HELPERS ---
    def detect_cpu_name(self):
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            name = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
            return name.replace("Intel(R) Core(TM) ", "").replace("AMD Ryzen ", "Ryzen ").split(" Processor")[0]
        except:
            return "Generic CPU"

    def apply_config(self):
        try:
            w = float(self.entry_overhead.get())
            self.cfg_overhead = w;
            SHARED_DATA["sys_w"] = w
            self.card_sys['lbl_val'].configure(text=f"{int(w)} W")
        except:
            pass

    def get_all_ips(self):
        try:
            return [ip for ip in socket.gethostbyname_ex(socket.gethostname())[2] if not ip.startswith("127.")]
        except:
            return []

    def setup_chart(self):
        self.fig = Figure(figsize=(5, 2), dpi=100);
        self.fig.patch.set_facecolor(COLOR_CARD)
        self.ax = self.fig.add_subplot(111);
        self.ax.set_facecolor(COLOR_CARD)
        self.line, = self.ax.plot([], [], color=COLOR_ACCENT, linewidth=2)
        self.ax.grid(True, color="#404040", linestyle='--', linewidth=0.5)
        for spine in self.ax.spines.values(): spine.set_visible(False)
        self.ax.spines['bottom'].set_visible(True);
        self.ax.spines['bottom'].set_color('#404040')
        self.ax.spines['left'].set_visible(True);
        self.ax.spines['left'].set_color('#404040')
        self.ax.tick_params(axis='both', colors='gray', labelsize=8)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.frame_chart)
        self.canvas.draw();
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=5, pady=5)

    def update_chart_data(self, new_val):
        self.history_y.append(new_val)
        self.line.set_data(self.history_x, self.history_y)
        self.ax.set_ylim(0, max(max(self.history_y) * 1.2, 100))
        self.ax.set_xlim(0, 60)
        self.canvas.draw()

    def create_metric_card(self, parent, title, title_color, max_val):
        frame = ctk.CTkFrame(parent, width=155, height=140, fg_color=COLOR_CARD, corner_radius=10);
        frame.pack_propagate(False)
        ctk.CTkLabel(frame, text=title, font=("Arial", 11, "bold"), text_color=title_color).pack(pady=(12, 2))
        lbl_val = ctk.CTkLabel(frame, text="0 W", font=("Roboto", 24, "bold"), text_color=COLOR_TEXT_MAIN);
        lbl_val.pack(pady=0)
        bar = ctk.CTkProgressBar(frame, width=100, height=6, progress_color=title_color);
        bar.set(0);
        bar.pack(pady=(5, 5))
        lbl_temp = ctk.CTkLabel(frame, text="-- °C | -- %", font=("Arial", 11), text_color=COLOR_TEXT_SUB);
        lbl_temp.pack(pady=(0, 10))
        return {"frame": frame, "lbl_val": lbl_val, "lbl_temp": lbl_temp, "bar": bar}

    def create_stat_row(self, parent, label, color):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.pack(fill="x", padx=15, pady=2)
        ctk.CTkLabel(f, text=f"{label}:", font=("Arial", 13), text_color=COLOR_TEXT_MAIN).pack(side="left")
        lbl_cost = ctk.CTkLabel(f, text="0.00", font=("Arial", 14, "bold"), text_color=color)
        lbl_cost.pack(side="right")
        setattr(self, f"lbl_{label.lower()}_cost", lbl_cost)

    # --- ADVANCED PROCESS MONITOR ---
    def process_monitor(self):
        """ Scans processes per GPU and merges Load% and VRAM """
        while self.running:
            # 1. CPU
            try:
                procs = []
                for p in psutil.process_iter(['name', 'cpu_percent']):
                    try:
                        n = p.info['name']
                        if n == "System Idle Process": continue
                        if n.endswith(".exe"): n = n[:-4].capitalize()

                        raw_cpu = p.info['cpu_percent'] or 0
                        norm_cpu = raw_cpu / CPU_COUNT
                        if norm_cpu > 0.1: procs.append({"name": n, "cpu": norm_cpu})
                    except:
                        pass

                top_c = sorted(procs, key=lambda x: x['cpu'], reverse=True)[:4]
                txt_c = "CPU:\n"
                for p in top_c: txt_c += f"{p['name'][:10]:<10} {p['cpu']:>4.1f}%\n"
                self.lbl_top_cpu.configure(text=txt_c)
                SHARED_DATA["top_cpu"] = [{"name": p["name"], "val": f"{p['cpu']:.1f}%"} for p in top_c]
            except:
                pass

            # 2. GPU
            if self.nvml_active:
                try:
                    all_gpu_output = ""
                    web_gpu_data = []

                    for i, gpu in enumerate(self.gpu_data):
                        handle = gpu['handle']

                        # --- GLOBAL VRAM STATS ---
                        mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                        mem_used_gb = mem_info.used / 1024 ** 3
                        mem_total_gb = mem_info.total / 1024 ** 3

                        # --- PROCESSES ---
                        proc_map = {}

                        # Pass 1: VRAM (Graphics + Compute)
                        try:
                            # Note: nvmlDeviceGetGraphicsRunningProcesses often returns NO memory info on Windows WDDM
                            # We still call it to get PIDs
                            mem_procs = pynvml.nvmlDeviceGetComputeRunningProcesses(handle) + \
                                        pynvml.nvmlDeviceGetGraphicsRunningProcesses(handle)
                            for p in mem_procs:
                                mem_val = (p.usedGpuMemory or 0) / 1024 / 1024  # MB
                                proc_map[p.pid] = {"pid": p.pid, "vram": mem_val, "load": 0}
                        except:
                            pass

                        # Pass 2: Compute/3D Load
                        try:
                            load_procs = pynvml.nvmlDeviceGetProcessUtilization(handle, 0)
                            for p in load_procs:
                                if p.pid in proc_map:
                                    proc_map[p.pid]["load"] = p.smUtil
                                elif p.smUtil > 0:
                                    proc_map[p.pid] = {"pid": p.pid, "vram": 0, "load": p.smUtil}
                        except:
                            pass

                        # Pass 3: Resolve Names
                        final_list = []
                        for pid, data in proc_map.items():
                            if data["vram"] == 0 and data["load"] == 0: continue
                            try:
                                name = psutil.Process(pid).name()
                                if name.endswith(".exe"): name = name[:-4].capitalize()
                                data["name"] = name
                                final_list.append(data)
                            except:
                                pass

                        # Sort
                        top_g = sorted(final_list, key=lambda x: (x["load"], x["vram"]), reverse=True)[:4]

                        # Format for App (Header with Total VRAM)
                        header = f"GPU {i} ({gpu['short']}):\n"
                        header += f"[Mem: {mem_used_gb:.1f}/{mem_total_gb:.1f} GB]\n"

                        all_gpu_output += header
                        if top_g:
                            for p in top_g:
                                # If VRAM is 0, show --- to indicate "Driver Hidden"
                                load_str = f"{p['load']}%" if p['load'] > 0 else "0%"
                                mem_str = f"{int(p['vram'])}MB" if p['vram'] > 0 else "---"
                                all_gpu_output += f"{p['name'][:10]:<10} {load_str:<4} {mem_str}\n"
                        else:
                            all_gpu_output += "Idle\n"
                        all_gpu_output += "\n"

                        # Format for Web
                        web_procs = []
                        for p in top_g:
                            load_str = f"{p['load']}%"
                            mem_str = f"{int(p['vram'])} MB" if p['vram'] > 0 else "---"
                            web_procs.append({"name": p["name"], "load": load_str, "mem": mem_str})

                        web_gpu_data.append({
                            "gpu_name": f"GPU {i} ({gpu['short']}) [{mem_used_gb:.1f}/{mem_total_gb:.1f} GB]",
                            "processes": web_procs
                        })

                    self.lbl_top_gpu.configure(text=all_gpu_output.strip())
                    SHARED_DATA["top_gpu"] = web_gpu_data

                except:
                    self.lbl_top_gpu.configure(text="GPU:\nN/A")
            else:
                self.lbl_top_gpu.configure(text="GPU:\nN/A")

            time.sleep(3)

            # --- FLASK SERVER (PROXMOX STYLE) ---

    def run_flask_server(self):
        app = Flask(__name__)
        log.setLevel(logging.ERROR)

        @app.route('/')
        def index():
            return render_template_string("""
            <!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
            <script src="https://cdn.jsdelivr.net/npm/chart.js"></script><title>Windows Command Center</title>
            <style>
                :root { --bg: #0f0f13; --card: #1a1a20; --text: #e0e0e0; --accent: #00E5FF; --green: #00FF9D; --red: #FF4455; }
                body { background-color: var(--bg); color: var(--text); font-family: 'Segoe UI', sans-serif; text-align: center; margin: 0; padding: 20px; }
                .watts-display { font-size: 84px; font-weight: 800; color: var(--accent); line-height: 1; margin: 10px 0; text-shadow: 0 0 30px rgba(0, 229, 255, 0.2); }
                .sub-display { font-size: 16px; color: #888; }
                .dashboard { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; max-width: 1200px; margin: 0 auto; }
                .panel { background: var(--card); border-radius: 16px; padding: 20px; border: 1px solid #2a2a35; box-shadow: 0 4px 20px rgba(0,0,0,0.3); }
                .panel h2 { font-size: 14px; color: #888; margin: 0 0 15px 0; text-transform: uppercase; text-align: left; border-bottom: 1px solid #333; padding-bottom: 10px; }
                .row-item { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; font-size: 14px; }
                .row-name { font-weight: 600; } .row-val { font-weight: bold; } .row-meta { font-size: 12px; color: #666; }
                .metric-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }
                .metric { background: #25252e; padding: 15px; border-radius: 8px; text-align: left; }
                .metric label { display: block; font-size: 11px; color: #888; margin-bottom: 4px; }
                .metric span { font-size: 20px; font-weight: bold; color: white; }
                .metric small { font-size: 12px; color: #666; }
                .btn-download { display: inline-block; margin-top: 30px; padding: 12px 25px; background: #25252e; color: #aaa; text-decoration: none; border-radius: 30px; border: 1px solid #333; transition: 0.2s; }
                .btn-download:hover { background: var(--accent); color: black; border-color: var(--accent); }
                .proc-list { text-align: left; font-size: 13px; color: #ccc; }
                .proc-item { margin-bottom: 6px; display: flex; justify-content: space-between; border-bottom: 1px solid #333; padding-bottom: 4px; }
                .proc-name { color: #fff; width: 40%; overflow:hidden; white-space:nowrap; } 
                .proc-data { width: 55%; display:flex; justify-content: flex-end; gap: 10px; }
                .proc-load { color: var(--accent); font-weight: bold; text-align:right; min-width:40px; }
                .proc-mem { color: #E040FB; font-weight: bold; text-align:right; min-width:60px; }
                .gpu-header { font-size:11px; font-weight:bold; color:#76b900; margin-top:15px; margin-bottom:5px; border-bottom: 1px dashed #333; }
            </style></head><body>
            <div style="margin-bottom:30px;"><div style="font-size:14px; letter-spacing:2px; color:#666; font-weight:bold;">WINDOWS COMMAND CENTER</div>
            <div id="total_w" class="watts-display">---</div><div class="sub-display">Peak Today: <span id="peak_w">---</span> W</div></div>
            <div class="dashboard">
                <div class="panel"><h2>System Load</h2><div id="hw_list">Loading...</div>
                    <div style="margin-top:20px; padding-top:15px; border-top:1px solid #333;"><div class="row-item"><span class="row-name" style="color:#aaa">System Overhead</span><span class="row-val" id="sys_w">0 W</span></div></div>
                </div>
                <div class="panel"><h2>Energy Costs</h2><div class="metric-grid">
                    <div class="metric"><label>COST TODAY</label><span id="cost_today" style="color:var(--green)">0.00</span> <small>EGP</small></div>
                    <div class="metric"><label>ENERGY TODAY</label><span id="kwh_today" style="color:var(--accent)">0.000</span> <small>kWh</small></div>
                    <div class="metric"><label>MONTH (REAL)</label><span id="cost_month_real" style="color:#E040FB">0.00</span> <small>EGP</small></div>
                    <div class="metric"><label>MONTH (EST)</label><span id="cost_month_est" style="color:#888">0.00</span> <small>EGP</small></div>
                </div></div>
                <div class="panel"><h2>Task Manager</h2>
                    <div style="margin-bottom:10px;"><span style="color:#ff8c00; font-size:12px; font-weight:bold;">TOP CPU</span></div>
                    <div id="top_cpu" class="proc-list">Scanning...</div>

                    <div style="margin-top:15px; margin-bottom:10px;"><span style="color:#76b900; font-size:12px; font-weight:bold;">TOP GPU</span></div>
                    <div id="top_gpu" class="proc-list">Scanning...</div>
                </div>
                <div class="panel" style="grid-column: 1/-1;"><h2>30-Day Cost Trend</h2><div style="height:250px;"><canvas id="costChart"></canvas></div></div>
            </div>
            <a href="/download_csv" class="btn-download">📥 Download Full CSV Log</a>
            <script>
                let myChart=null;
                async function fetchStats() {
                    try {
                        let res = await fetch('/api/data'); let data = await res.json();
                        document.getElementById('total_w').innerText = Math.round(data.total_w);
                        document.getElementById('peak_w').innerText = Math.round(data.peak_w);
                        let hwHTML = `<div class="row-item"><span class="row-name" style="color:#ff8c00">CPU</span><span class="row-meta">${Math.round(data.cpu_load)}% | ${data.cpu_t}°C</span><span class="row-val">${Math.round(data.cpu_w)} W</span></div>`;
                        if(data.has_igpu) hwHTML += `<div class="row-item"><span class="row-name" style="color:#E040FB">iGPU</span><span class="row-meta">${data.igpu_t}°C</span><span class="row-val">${Math.round(data.igpu_w)} W</span></div>`;
                        data.gpu_data.forEach(g => { hwHTML += `<div class="row-item"><span class="row-name" style="color:#76b900">${g.short}</span><span class="row-meta">${g.temp}°C | ${g.load}%</span><span class="row-val">${Math.round(g.power)} W</span></div>`; });
                        document.getElementById('hw_list').innerHTML = hwHTML;
                        document.getElementById('sys_w').innerText = Math.round(data.sys_w) + " W";
                        document.getElementById('cost_today').innerText = data.cost_today.toFixed(2);
                        document.getElementById('kwh_today').innerText = data.kwh_today.toFixed(3);
                        document.getElementById('cost_month_est').innerText = data.cost_month_est.toFixed(0);
                        let realTotal = data.cost_today; if(data.history) data.history.forEach(d => realTotal+=d.cost);
                        document.getElementById('cost_month_real').innerText = realTotal.toFixed(2);

                        let cpuHTML = "";
                        if(data.top_cpu) data.top_cpu.forEach(p => { 
                            cpuHTML += `<div class="proc-item"><span class="proc-name">${p.name}</span><span class="proc-data"><span class="proc-load">${p.val}</span></span></div>`; 
                        });
                        document.getElementById('top_cpu').innerHTML = cpuHTML || "Idle";

                        let gpuHTML = "";
                        if(data.top_gpu && data.top_gpu.length > 0) {
                            data.top_gpu.forEach(gpu_block => {
                                gpuHTML += `<div class="gpu-header">${gpu_block.gpu_name}</div>`;
                                if(gpu_block.processes.length > 0) {
                                    gpu_block.processes.forEach(p => { 
                                        gpuHTML += `<div class="proc-item">
                                            <span class="proc-name">${p.name}</span>
                                            <div class="proc-data">
                                                <span class="proc-load">${p.load}</span>
                                                <span class="proc-mem">${p.mem}</span>
                                            </div>
                                        </div>`; 
                                    });
                                } else { gpuHTML += `<div style="font-style:italic; color:#666;">Idle</div>`; }
                            });
                        } else { gpuHTML = "Idle / No Data"; }
                        document.getElementById('top_gpu').innerHTML = gpuHTML;

                        updateChart(data.history);
                    } catch(e){}
                }
                function updateChart(h){ if(!h)return; let sub=h.slice(-30); let lbl=sub.map(d=>d.date.slice(5)); let dat=sub.map(d=>d.cost);
                    if(myChart){ if(myChart.data.labels.length!==lbl.length){myChart.data.labels=lbl; myChart.data.datasets[0].data=dat; myChart.update();}}
                    else { const ctx=document.getElementById('costChart').getContext('2d'); myChart=new Chart(ctx,{type:'bar',data:{labels:lbl,datasets:[{label:'Cost',data:dat,backgroundColor:'#00FF9D',borderRadius:4}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{y:{grid:{color:'#333'},ticks:{color:'#888'}},x:{grid:{display:false},ticks:{color:'#888'}}}}}); }
                }
                setInterval(fetchStats,1000); fetchStats();
            </script></body></html>
            """)

        @app.route('/api/data')
        def data(): return jsonify(SHARED_DATA)

        @app.route('/download_csv')
        def dl_csv(): return send_file(LOG_FILE, as_attachment=True)

        app.run(host='0.0.0.0', port=FLASK_PORT)

    # --- DATA HANDLING ---
    def setup_nvml(self):
        if pynvml:
            try:
                pynvml.nvmlInit()
                for i in range(pynvml.nvmlDeviceGetCount()):
                    h = pynvml.nvmlDeviceGetHandleByIndex(i)
                    n = pynvml.nvmlDeviceGetName(h)
                    if isinstance(n, bytes): n = n.decode()
                    s = n.replace("NVIDIA GeForce ", "").replace("NVIDIA ", "").replace(" RTX", "")
                    self.gpu_data.append({"handle": h, "name": n, "short": s, "widget_pwr": None})
                self.nvml_active = True
            except:
                self.nvml_active = False

    def load_data(self):
        try:
            with open(STATE_FILE, 'r') as f:
                d = json.load(f)
                if d.get("last_date") != datetime.now().strftime("%Y-%m-%d"): self.check_rollover(d)
                return d
        except:
            return {"last_date": datetime.now().strftime("%Y-%m-%d"), "day_kwh": 0.0, "day_cost": 0.0,
                    "lifetime_cost": 0.0, "day_peak": 0.0}

    def load_history(self):
        try:
            with open(HISTORY_FILE, 'r') as f:
                SHARED_DATA["history"] = json.load(f)
                return SHARED_DATA["history"]
        except:
            return []

    def check_rollover(self, data):
        # Archive yesterday
        entry = {"date": data["last_date"], "kwh": data["day_kwh"], "cost": data["day_cost"],
                 "peak_w": data.get("day_peak", 0)}
        hist = self.load_history()
        hist.append(entry)
        if len(hist) > MAX_HISTORY_DAYS: hist = hist[-MAX_HISTORY_DAYS:]
        with open(HISTORY_FILE, 'w') as f: json.dump(hist, f)
        SHARED_DATA["history"] = hist
        # Reset today
        data.update(
            {"last_date": datetime.now().strftime("%Y-%m-%d"), "day_kwh": 0.0, "day_cost": 0.0, "day_peak": 0.0})

    def save_data(self):
        with open(STATE_FILE, 'w') as f: json.dump(self.persistent_data, f, indent=4)

    def init_csv(self):
        if not os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'w', newline='') as f: csv.writer(f).writerow(
                ["Timestamp", "Total Watts", "Total Cost", "CPU Watts", "GPU Watts"])

    def log_csv(self, total, cpu, gpu_total):
        try:
            with open(LOG_FILE, 'a', newline='') as f:
                csv.writer(f).writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), round(total, 1),
                                        round(self.persistent_data['day_cost'], 4), round(cpu, 1), round(gpu_total, 1)])
        except:
            pass

    # --- SENSOR LOOP ---
    def fetch_lhm_data(self):
        if not IS_WINDOWS: return None
        try:
            r = requests.get(LHM_URL, timeout=0.2)
            if r.status_code == 200: return r.json()
        except:
            return None

    def find_sensor(self, node, targets, stype):
        if isinstance(node, dict):
            if node.get("Type") == stype and any(t in node.get("Text", "") for t in targets):
                try:
                    return float(str(node.get("Value", "0")).split()[0])
                except:
                    return 0.0
            if "Children" in node:
                for c in node["Children"]:
                    v = self.find_sensor(c, targets, stype)
                    if v > 0: return v
        elif isinstance(node, list):
            for i in node:
                v = self.find_sensor(i, targets, stype)
                if v > 0: return v
        return 0.0

    def calculate_igpu_total(self, data):
        # Simple scan for Radeon power
        return self.find_sensor(data, ["GPU Core", "GPU SoC"], "Power")

    def background_monitor(self):
        last_save = time.time()
        while self.running:
            # 1. NVIDIA
            nv_w = 0;
            shared_gpus = []
            if self.nvml_active:
                for gpu in self.gpu_data:
                    try:
                        w = pynvml.nvmlDeviceGetPowerUsage(gpu['handle']) / 1000.0;
                        nv_w += w
                        t = pynvml.nvmlDeviceGetTemperature(gpu['handle'], 0)
                        l = pynvml.nvmlDeviceGetUtilizationRates(gpu['handle']).gpu
                        shared_gpus.append({"short": gpu['short'], "power": w, "temp": t, "load": l})
                        if gpu['widget_pwr']:
                            gpu['widget_pwr'].configure(text=f"{int(w)} W")
                            gpu['widget_temp'].configure(text=f"{t} °C | {l} %")
                            gpu['widget_bar'].set(min(1.0, w / 250))
                    except:
                        pass

            # 2. LHM (CPU/iGPU)
            lhm = self.fetch_lhm_data()
            cpu_w = 0;
            cpu_t = 0;
            cpu_l = 0;
            igpu_w = 0;
            igpu_t = 0
            if lhm:
                cpu_w = self.find_sensor(lhm, ["Package", "CPU Package"], "Power")
                cpu_t = self.find_sensor(lhm, ["Core", "Package", "Tctl/Tdie"], "Temperature")
                cpu_l = self.find_sensor(lhm, ["Total", "CPU Total"], "Load")
                if self.has_igpu:
                    igpu_w = self.calculate_igpu_total(lhm)
                    igpu_t = self.find_sensor(lhm, ["GPU Core"], "Temperature")

            # Fallback
            if cpu_w == 0: cpu_l = psutil.cpu_percent(); cpu_w = 20 + (cpu_l * 1.5)

            # 3. Totals
            total_w = nv_w + cpu_w + igpu_w + self.cfg_overhead
            if total_w > self.peak_w: self.peak_w = total_w
            if total_w > self.persistent_data.get("day_peak", 0): self.persistent_data["day_peak"] = total_w

            # 4. Costs
            kwh = (total_w * self.cfg_interval) / 3.6e6
            self.persistent_data["day_kwh"] += kwh
            self.persistent_data["day_cost"] += (kwh * DEFAULT_PRICE)

            # 5. UI Updates
            if datetime.now().strftime("%Y-%m-%d") != self.persistent_data["last_date"]:
                self.check_rollover(self.persistent_data)

            SHARED_DATA.update({
                "total_w": total_w, "peak_w": self.peak_w,
                "cpu_w": cpu_w, "cpu_t": cpu_t, "cpu_load": cpu_l,
                "igpu_w": igpu_w, "igpu_t": igpu_t,
                "gpu_data": shared_gpus, "sys_w": self.cfg_overhead,
                "cost_today": self.persistent_data["day_cost"],
                "kwh_today": self.persistent_data["day_kwh"],
                "cost_month_est": (total_w / 1000) * 24 * 30 * DEFAULT_PRICE
            })

            # Update Local GUI
            try:
                self.lbl_watts.configure(text=f"{int(total_w)} W")
                self.lbl_peak.configure(text=f"Peak: {int(self.peak_w)} W")
                self.card_cpu['lbl_val'].configure(text=f"{int(cpu_w)} W")
                self.card_cpu['lbl_temp'].configure(text=f"{int(cpu_t)} °C")
                self.card_cpu['bar'].set(min(1.0, cpu_w / TDP_CPU))
                self.update_chart_data(total_w)
                self.lbl_session_cost.configure(text=f"{self.persistent_data['day_cost']:.2f}")
                self.lbl_today_cost.configure(text=f"{self.persistent_data['day_cost']:.2f}")
                self.lbl_overall_cost.configure(
                    text=f"{(self.persistent_data.get('lifetime_cost', 0) + self.persistent_data['day_cost']):.2f}")
            except:
                pass

            self.log_csv(total_w, cpu_w, nv_w)
            if time.time() - last_save > 60: self.save_data(); last_save = time.time()
            time.sleep(self.cfg_interval)

    def on_close(self):
        self.running = False;
        self.save_data();
        self.destroy()


if __name__ == "__main__": app = PowerMonitorApp(); app.mainloop()