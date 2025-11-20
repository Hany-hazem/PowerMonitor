import customtkinter as ctk
import psutil
import threading
from flask import Flask, jsonify

# Shared Data
power_data = {"watts": 0}

# Flask Server
server = Flask(__name__)
@server.route('/')
def get_data():
    return jsonify(power_data)

def run_server():
    server.run(port=5000)

# GUI
app = ctk.CTk()
app.geometry("400x200")
app.title("Power Monitor V4 (Web)")

lbl = ctk.CTkLabel(app, text="0 W", font=("Arial", 30))
lbl.pack(pady=40)

def update():
    w = 30 + (psutil.cpu_percent() * 1.5)
    power_data["watts"] = w
    lbl.configure(text=f"{int(w)} W")
    app.after(1000, update)

# Start Threads
t = threading.Thread(target=run_server, daemon=True)
t.start()

update()
app.mainloop()