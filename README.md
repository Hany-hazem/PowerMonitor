# PowerMonitor ⚡

**PowerMonitor** is a cross-platform, real-time energy monitoring "Command Center" for your workstation. It tracks power consumption, electricity costs, and hardware utilization (CPU & GPU) with a modern desktop GUI and a remote-accessible Web Dashboard.

It is designed to give you deep insights into your system's efficiency, including a **"Power Task Manager"** that identifies exactly which processes are hogging your CPU load and GPU VRAM.

![Dashboard Preview](https://github.com/user-attachments/assets/placeholder-image-b580aa.png)
*(Replace with your actual screenshot link)*

## 🌟 Key Features

* **Real-Time Power Tracking:** Monitors CPU Package power, Discrete GPU power (NVIDIA), and iGPU power.
* **Financial Analytics:** Calculates costs in real-time based on your local electricity rate (EGP/kWh). Tracks Session, Daily, and Monthly costs.
* **"Power Task Manager":** Identifies specific processes using the most CPU resources and GPU VRAM.
* **Web Dashboard:** A built-in Flask web server (Port 5000) lets you view stats from any device on your network (phone, laptop, tablet).
* **Multi-GPU Support:** Independently tracks and displays stats for multiple GPUs (e.g., dedicated graphics vs. compute cards).
* **Data Logging:** Automatically saves detailed usage logs to CSV for analysis.
* **Visual History:** Interactive charts showing power and cost trends over the last 30 days.

## 🚀 Installation & Usage

### Option 1: Download the App (Recommended)
1.  Go to the **[Releases](https://github.com/Hany-hazem/PowerMonitor/releases)** page.
2.  Download the executable for your OS:
    * **Windows:** `PowerMonitor_Windows.exe`
    * **macOS:** `PowerMonitor_Mac.app.zip`
    * **Linux:** `PowerMonitor_Linux.bin`

> **⚠️ Windows Requirement:** You must run **[LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)** in the background for CPU data.
> * Open LibreHardwareMonitor.
> * Go to **Options** > **Remote Web Server** > **Run**. (Ensure it uses port 8085).

> **⚠️ macOS Note:** If the app doesn't open, run this command in Terminal to bypass Gatekeeper:
> `sudo xattr -rd com.apple.quarantine /path/to/PowerMonitor_Mac.app`

### Option 2: Run from Source
1.  Clone the repository:
    ```bash
    git clone [https://github.com/Hany-hazem/PowerMonitor.git](https://github.com/Hany-hazem/PowerMonitor.git)
    cd PowerMonitor
    ```
2.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```
3.  Run the application:
    ```bash
    # On Windows (Run as Admin for full GPU process visibility)
    python power_gui_web.pyw
    ```

## 🧠 How It Works (Under the Hood)

The application is built using **Python** (`customtkinter` for GUI, `flask` for Web) and combines local hardware APIs with a web-based frontend.

### 1. Data Acquisition Layer
* **NVIDIA NVML (`pynvml`):** Direct communication with NVIDIA drivers to fetch extremely accurate power draw (Watts), temperature, usage load, and per-process VRAM consumption.
* **LibreHardwareMonitor (LHM) Bridge:** Connects to `localhost:8085` to fetch CPU and AMD/Intel GPU power data via JSON.
* **System Overhead:** A configurable "Base Load" (default 80W) is added to account for motherboard, RAM, fans, and storage.

### 2. The "Smart" Calculation Engine
* **Power Integration:** $Total Watts = CPU_{watts} + \sum GPU_{watts} + Overhead$
* **Cost Logic:** Energy is calculated in kWh by integrating wattage over time.
    * `kWh += (Total_Watts * Poll_Interval) / 3,600,000`
    * `Cost = kWh * Price_Per_Unit`
* **Persistence:** State (Daily totals, History) is saved to `power_state.json` and `power_history.json` every 60 seconds.

### 3. Process & Task Monitoring
A dedicated background thread acts as a "Power Task Manager":
* **CPU Hogging:** Scans `psutil.process_iter()` to find processes with high CPU utilization, normalizing the load across all cores (fixing the "1200% usage" bug on multi-core systems).
* **GPU Hogging:** Queries the GPU driver for "Compute" and "Graphics" contexts. It matches internal PIDs (Process IDs) with system names to display exactly which app is using your VRAM or 3D engine.

### 4. The Unified Dashboard (Flask)
* The application launches a local **Flask** web server on port `5000`.
* It serves a dynamic HTML/JS frontend that mimics the desktop GUI.
* **Sync:** The frontend polls the `/api/data` endpoint every second to update gauges, charts, and tables without refreshing the page.

## ⚙️ Configuration

* **Overhead:** Adjust "System Overhead" wattage in the GUI to match your specific hardware (e.g., number of fans, RGB strips).
* **Price:** Default electricity price is set in the script (2.14 EGP).
* **Web Port:** Default is `5000`. Access via `http://<YOUR_PC_IP>:5000`.

## 🛠️ Build Process
The project uses **GitHub Actions** to automatically build standalone executables for all platforms using `PyInstaller`.
* **Windows:** Builds a single-file `.exe` (`--onefile`).
* **macOS:** Builds a `.app` bundle structure (`--onedir`) to comply with macOS requirements.
* **Linux:** Builds a standard binary executable (`--onefile`).

---
*Created by [Hany-hazem](https://github.com/Hany-hazem)*