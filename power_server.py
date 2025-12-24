import time
import json
import os
import threading
import psutil
import socket
import glob
import logging
import csv
import subprocess
from flask import Flask, jsonify, render_template_string, send_file
from datetime import datetime

# --- CONFIGURATION ---
FLASK_PORT = 5000
PRICE_PER_KWH = 2.14
POLL_INTERVAL = 2.0  # CPU Optimization: Poll every 2s
VM_SCAN_INTERVAL = 30  # CPU Optimization: Scan VMs every 30s

# --- DATA STORAGE ---
DATA_FILE = "power_data.json"
HISTORY_FILE = "power_history.json"
CSV_LOG_FILE = "power_log_detailed.csv"
MAX_HISTORY_DAYS = 365

# --- HARDWARE CONSTANTS ---
FAN_COUNT = 5
HDD_COUNT = 2
SSD_COUNT = 2
RAM_STICKS = 2

# --- GLOBAL STATE ---
PERSISTENT_DATA = {
    "last_date": datetime.now().strftime("%Y-%m-%d"),
    "day_kwh": 0.0,
    "day_cost": 0.0,
    "day_peak": 0.0
}

SHARED_DATA = {
    "total_w": 0, "peak_w": 0,
    "cpu_w": 0, "cpu_load": 0, "cpu_temp": 0,
    "gpu_data": [],
    "sys_w": 0, "fan_w": 0, "disk_w": 0, "mobo_w": 0,
    "cost_today": 0.0, "kwh_today": 0.0,
    "cost_month_est": 0.0, "cost_month_real": 0.0,
    "history": [],
    "instances": []
}

# --- CACHED PATHS (CPU Optimization) ---
CACHED_GPU_PATHS = []
CACHED_RAPL_PATH = None

app = Flask(__name__)
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)


@app.route('/')
def index():
    return render_template_string("""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <title>Proxmox Eco Monitor</title>
        <style>
            :root { --bg: #0f0f13; --card: #1a1a20; --text: #e0e0e0; --accent: #00E5FF; --green: #00FF9D; --red: #FF4455; }
            body { background-color: var(--bg); color: var(--text); font-family: 'Segoe UI', Roboto, sans-serif; text-align: center; margin: 0; padding: 20px; }
            .header { margin-bottom: 30px; }
            .title { font-size: 14px; letter-spacing: 2px; color: #666; font-weight: bold; text-transform: uppercase; }
            .watts-display { font-size: 84px; font-weight: 800; color: var(--accent); line-height: 1; margin: 10px 0; text-shadow: 0 0 30px rgba(0, 229, 255, 0.2); }
            .sub-display { font-size: 16px; color: #888; }
            .dashboard { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; max-width: 1200px; margin: 0 auto; }
            .panel { background: var(--card); border-radius: 16px; padding: 20px; border: 1px solid #2a2a35; box-shadow: 0 4px 20px rgba(0,0,0,0.3); }
            .panel h2 { font-size: 14px; color: #888; margin: 0 0 15px 0; text-transform: uppercase; text-align: left; border-bottom: 1px solid #333; padding-bottom: 10px; }
            .row-item { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; font-size: 14px; }
            .row-name { font-weight: 600; }
            .row-val { font-weight: bold; }
            .row-meta { font-size: 12px; color: #666; }
            .status-dot { height: 8px; width: 8px; border-radius: 50%; display: inline-block; margin-right: 8px; }
            .running { background-color: var(--green); box-shadow: 0 0 5px var(--green); }
            .stopped { background-color: #444; }
            .metric-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }
            .metric { background: #25252e; padding: 15px; border-radius: 8px; text-align: left; }
            .metric label { display: block; font-size: 11px; color: #888; margin-bottom: 4px; }
            .metric span { font-size: 20px; font-weight: bold; color: white; }
            .metric small { font-size: 12px; color: #666; }
            table { width: 100%; border-collapse: collapse; font-size: 13px; color: #ccc; }
            th { text-align: left; color: #666; padding: 8px; border-bottom: 1px solid #333; }
            td { padding: 8px; border-bottom: 1px solid #25252e; text-align: left; }
            .btn-download { display: inline-block; margin-top: 30px; padding: 12px 25px; background: #25252e; color: #aaa; text-decoration: none; border-radius: 30px; border: 1px solid #333; transition: 0.2s; }
            .btn-download:hover { background: var(--accent); color: black; border-color: var(--accent); }
        </style>
    </head>
    <body>
        <div class="header">
            <div class="title">Proxmox Eco Monitor</div>
            <div id="total_w" class="watts-display">---</div>
            <div class="sub-display">Peak Today: <span id="peak_w">---</span> W</div>
        </div>
        <div class="dashboard">
            <div class="panel">
                <h2>Hardware Load</h2>
                <div id="hw_list">Loading...</div>
                <div style="margin-top:20px; padding-top:15px; border-top:1px solid #333;">
                    <div class="row-item"><span class="row-name" style="color:#aaa">Fans</span><span class="row-val" id="fan_w">0 W</span></div>
                    <div class="row-item"><span class="row-name" style="color:#aaa">Disks</span><span class="row-val" id="disk_w">0 W</span></div>
                    <div class="row-item"><span class="row-name" style="color:#aaa">Mobo</span><span class="row-val" id="mobo_w">0 W</span></div>
                </div>
            </div>
            <div class="panel">
                <h2>Energy Costs</h2>
                <div class="metric-grid">
                    <div class="metric"><label>COST TODAY</label><span id="cost_today" style="color:var(--green)">0.00</span> <small>EGP</small></div>
                    <div class="metric"><label>ENERGY TODAY</label><span id="kwh_today" style="color:var(--accent)">0.000</span> <small>kWh</small></div>
                    <div class="metric"><label>MONTH (REAL)</label><span id="cost_month_real" style="color:#E040FB">0.00</span> <small>EGP</small></div>
                    <div class="metric"><label>MONTH (EST)</label><span id="cost_month_est" style="color:#888">0.00</span> <small>EGP</small></div>
                </div>
            </div>
            <div class="panel">
                <h2>Active Workloads</h2>
                <div id="vm_list" style="max-height: 250px; overflow-y: auto;">Scanning...</div>
            </div>
            <div class="panel" style="grid-column: 1 / -1;">
                <h2>30-Day Cost Trend</h2>
                <div style="height:250px;"><canvas id="costChart"></canvas></div>
            </div>
            <div class="panel" style="grid-column: 1 / -1;">
                <h2>Daily Logs</h2>
                <table id="history_table"></table>
            </div>
        </div>
        <a href="/download_csv" class="btn-download">📥 Download Full CSV Log</a>
        <script>
            let myChart = null;
            async function fetchStats() {
                try {
                    let res = await fetch('/api/data');
                    let data = await res.json();
                    document.getElementById('total_w').innerText = Math.round(data.total_w);
                    document.getElementById('peak_w').innerText = Math.round(data.peak_w);

                    let hwHTML = `<div class="row-item"><span class="row-name" style="color:#00BFFF">i5-10400F</span><span class="row-meta">${Math.round(data.cpu_load)}% | ${data.cpu_temp}°C</span><span class="row-val">${Math.round(data.cpu_w)} W</span></div>`;
                    data.gpu_data.forEach(g => { hwHTML += `<div class="row-item"><span class="row-name" style="color:#FF4455">${g.name}</span><span class="row-meta">${g.temp}°C</span><span class="row-val">${Math.round(g.power)} W</span></div>`; });
                    document.getElementById('hw_list').innerHTML = hwHTML;

                    let vmHTML = "";
                    if (data.instances && data.instances.length > 0) {
                        data.instances.forEach(vm => {
                            let statusClass = vm.status === 'running' ? 'running' : 'stopped';
                            let statusColor = vm.status === 'running' ? '#fff' : '#666';
                            vmHTML += `<div class="row-item"><div style="display:flex; align-items:center;"><span class="status-dot ${statusClass}"></span><span class="row-name" style="color:${statusColor}">${vm.name}</span><span style="font-size:10px; color:#555; margin-left:8px;">(${vm.type} ${vm.id})</span></div><span class="row-meta">${vm.status}</span></div>`;
                        });
                    } else { vmHTML = "<div style='color:#666; font-style:italic;'>No Active Instances Found</div>"; }
                    document.getElementById('vm_list').innerHTML = vmHTML;

                    document.getElementById('fan_w').innerText = data.fan_w.toFixed(1) + " W";
                    document.getElementById('disk_w').innerText = data.disk_w.toFixed(1) + " W";
                    document.getElementById('mobo_w').innerText = data.mobo_w.toFixed(1) + " W";
                    document.getElementById('cost_today').innerText = data.cost_today.toFixed(2);
                    document.getElementById('kwh_today').innerText = data.kwh_today.toFixed(3);
                    document.getElementById('cost_month_est').innerText = data.cost_month_est.toFixed(0);
                    let realTotal = data.cost_today;
                    if(data.history) data.history.forEach(d => realTotal += d.cost);
                    document.getElementById('cost_month_real').innerText = realTotal.toFixed(2);

                    updateChart(data.history);
                    updateTable(data.history);
                } catch(e) {}
            }
            function updateTable(history) {
                if(!history) return;
                let html = "<tr><th>Date</th><th>Peak (W)</th><th>kWh</th><th>Cost</th></tr>";
                [...history].reverse().slice(0, 7).forEach(d => {
                    html += `<tr><td>${d.date}</td><td>${Math.round(d.peak_w)} W</td><td>${d.kwh.toFixed(2)}</td><td style="color:var(--green)">${d.cost.toFixed(2)} EGP</td></tr>`;
                });
                document.getElementById('history_table').innerHTML = html;
            }
            function updateChart(history) {
                if (!history) return;
                let subset = history.slice(-30); 
                let labels = subset.map(d => d.date.slice(5)); 
                let data = subset.map(d => d.cost);
                if (myChart) {
                    if (myChart.data.labels.length !== labels.length) {
                        myChart.data.labels = labels; myChart.data.datasets[0].data = data; myChart.update();
                    }
                } else {
                    const ctx = document.getElementById('costChart').getContext('2d');
                    myChart = new Chart(ctx, {
                        type: 'bar',
                        data: { labels: labels, datasets: [{ label: 'Daily Cost (EGP)', data: data, backgroundColor: '#00FF9D', borderRadius: 4 }] },
                        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { grid: { color: '#333' }, ticks: { color: '#888' } }, x: { grid: { display: false }, ticks: { color: '#888' } } } }
                    });
                }
            }
            setInterval(fetchStats, 2000); 
            fetchStats();
        </script>
    </body>
    </html>
    """)


@app.route('/api/data')
def get_data():
    return jsonify(SHARED_DATA)


@app.route('/download_csv')
def download_csv():
    return send_file(CSV_LOG_FILE, as_attachment=True)


# --- BACKEND LOGIC ---
def init_csv():
    if not os.path.exists(CSV_LOG_FILE):
        try:
            with open(CSV_LOG_FILE, 'w', newline='') as f:
                csv.writer(f).writerow(["Timestamp", "Total Watts", "CPU Watts", "GPU Watts", "Cost Accumulator"])
        except:
            pass


def log_to_csv(total, cpu, gpu, cost):
    try:
        with open(CSV_LOG_FILE, 'a', newline='') as f:
            csv.writer(f).writerow(
                [datetime.now().strftime("%Y-%m-%d %H:%M:%S"), round(total, 1), round(cpu, 1), round(gpu, 1),
                 round(cost, 4)])
    except:
        pass


def load_state():
    global PERSISTENT_DATA
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                PERSISTENT_DATA.update(json.load(f))
                check_rollover()
        except:
            pass
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r') as f:
                SHARED_DATA["history"] = json.load(f)
        except:
            pass


def save_state():
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump(PERSISTENT_DATA, f)
    except:
        pass


def archive_history(date_str, kwh, cost, peak):
    hist = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r') as f:
                hist = json.load(f)
        except:
            pass
    hist.append({"date": date_str, "kwh": kwh, "cost": cost, "peak_w": peak})
    if len(hist) > MAX_HISTORY_DAYS: hist = hist[-MAX_HISTORY_DAYS:]
    try:
        with open(HISTORY_FILE, 'w') as f:
            json.dump(hist, f)
        SHARED_DATA["history"] = hist
    except:
        pass


def check_rollover():
    curr = datetime.now().strftime("%Y-%m-%d")
    if PERSISTENT_DATA["last_date"] != curr:
        archive_history(PERSISTENT_DATA["last_date"], PERSISTENT_DATA["day_kwh"], PERSISTENT_DATA["day_cost"],
                        PERSISTENT_DATA.get("day_peak", 0))
        PERSISTENT_DATA.update({"last_date": curr, "day_kwh": 0.0, "day_cost": 0.0, "day_peak": 0.0})
        save_state()


# --- PROXMOX API (Optimized) ---
def get_proxmox_instances():
    instances = []
    # 1. VMs
    try:
        out = subprocess.check_output("/usr/sbin/qm list", shell=True).decode()
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 3:
                instances.append({"id": parts[0], "name": parts[1], "status": parts[2], "type": "VM"})
    except:
        pass
    # 2. CTs
    try:
        out = subprocess.check_output("/usr/sbin/pct list", shell=True).decode()
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                instances.append({"id": parts[0], "name": parts[-1], "status": parts[1], "type": "CT"})
    except:
        pass
    instances.sort(key=lambda x: (x['status'] != 'running', int(x['id'])))
    return instances


# --- SENSORS (Optimized with Caching) ---
def init_sensor_paths():
    global CACHED_GPU_PATHS, CACHED_RAPL_PATH
    CACHED_GPU_PATHS = glob.glob("/sys/class/drm/card*/device/hwmon/hwmon*")
    if not CACHED_GPU_PATHS:
        CACHED_GPU_PATHS = glob.glob("/sys/class/hwmon/hwmon*")
    if os.path.exists("/sys/class/powercap/intel-rapl/intel-rapl:0/energy_uj"):
        CACHED_RAPL_PATH = "/sys/class/powercap/intel-rapl/intel-rapl:0/energy_uj"


def get_cpu_rapl():
    if not CACHED_RAPL_PATH: return 0.0
    try:
        with open(CACHED_RAPL_PATH) as f:
            e1 = int(f.read())
            time.sleep(0.1)
            f.seek(0)
            e2 = int(f.read())
        return (e2 - e1) / 0.1 / 1e6
    except:
        return 0.0


def get_cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read()) / 1000
    except:
        return 40


def get_amd_gpu():
    lst = []
    for h in CACHED_GPU_PATHS:
        try:
            name = ""
            if os.path.exists(f"{h}/name"):
                with open(f"{h}/name") as f: name = f.read().strip()
            if "amdgpu" in name:
                p = 0
                if os.path.exists(f"{h}/power1_average"):
                    with open(f"{h}/power1_average") as f:
                        p = int(f.read()) / 1e6
                elif os.path.exists(f"{h}/power1_input"):
                    with open(f"{h}/power1_input") as f:
                        p = int(f.read()) / 1e6
                t = 0
                if os.path.exists(f"{h}/temp1_input"):
                    with open(f"{h}/temp1_input") as f: t = int(f.read()) / 1000
                if p > 0 or t > 0: lst.append({"name": "RX 480", "power": p, "temp": int(t)})
        except:
            pass
    return lst


# --- LOOP ---
def monitor_loop():
    print(f"[*] Eco Monitor Active on {FLASK_PORT}")
    init_csv()
    init_sensor_paths()
    load_state()

    last_save = time.time()
    last_vm_scan = 0

    while True:
        # Sensors
        cpu_w = get_cpu_rapl()
        cpu_load = psutil.cpu_percent(None)
        if cpu_w == 0: cpu_w = 20 + (cpu_load * 0.65)
        cpu_temp = get_cpu_temp()

        gpu_list = get_amd_gpu()
        gpu_w = sum(g['power'] for g in gpu_list)

        # Overhead
        fan_w = FAN_COUNT * (1.0 + max(0, min(2.0, (cpu_temp - 30) * 0.05)))
        disk_w = (HDD_COUNT * 5.0) + (SSD_COUNT * 0.5)
        try:
            io = psutil.disk_io_counters()
            if (io.read_bytes + io.write_bytes) > 0: disk_w += (HDD_COUNT * 3.0 * (cpu_load / 100))
        except:
            pass
        mobo_w = 30.0 + (RAM_STICKS * 3.0) + (cpu_load * 0.1)

        total_w = cpu_w + gpu_w + fan_w + disk_w + mobo_w
        if total_w > SHARED_DATA["peak_w"]: SHARED_DATA["peak_w"] = total_w
        if total_w > PERSISTENT_DATA["day_peak"]: PERSISTENT_DATA["day_peak"] = total_w

        # Accumulators
        kwh = (total_w * POLL_INTERVAL) / 3.6e6
        PERSISTENT_DATA["day_kwh"] += kwh
        PERSISTENT_DATA["day_cost"] += (kwh * PRICE_PER_KWH)

        log_to_csv(total_w, cpu_w, gpu_w, PERSISTENT_DATA["day_cost"])

        # HEAVY TASK: Only Scan VMs every 30s
        if time.time() - last_vm_scan > VM_SCAN_INTERVAL:
            SHARED_DATA["instances"] = get_proxmox_instances()
            last_vm_scan = time.time()

        SHARED_DATA.update({
            "total_w": total_w, "peak_w": PERSISTENT_DATA["day_peak"],
            "cpu_w": cpu_w, "cpu_load": cpu_load, "cpu_temp": int(cpu_temp),
            "gpu_data": gpu_list, "fan_w": fan_w, "disk_w": disk_w, "mobo_w": mobo_w,
            "cost_today": PERSISTENT_DATA["day_cost"],
            "kwh_today": PERSISTENT_DATA["day_kwh"],
            "cost_month_est": (total_w / 1000) * 24 * 30 * PRICE_PER_KWH,
        })

        check_rollover()
        if time.time() - last_save > 60:
            save_state()
            last_save = time.time()
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    t = threading.Thread(target=monitor_loop, daemon=True)
    t.start()
    app.run(host='0.0.0.0', port=FLASK_PORT, debug=False)