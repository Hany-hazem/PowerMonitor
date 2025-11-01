import customtkinter as ctk
import psutil
try:
    import pynvml
except:
    pynvml = None

app = ctk.CTk()
app.geometry("400x300")
app.title("Power Monitor V3 (GPU Support)")

# Init NVIDIA
if pynvml:
    try: pynvml.nvmlInit()
    except: pass

lbl_total = ctk.CTkLabel(app, text="--- W", font=("Arial", 40, "bold"))
lbl_total.pack(pady=50)

def get_gpu():
    if not pynvml: return 0
    try:
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        return pynvml.nvmlDeviceGetPowerUsage(h) / 1000.0
    except: return 0

def update():
    cpu_w = 30 + (psutil.cpu_percent() * 1.2)
    gpu_w = get_gpu()
    total = cpu_w + gpu_w
    lbl_total.configure(text=f"Total: {int(total)} W")
    app.after(1000, update)

update()
app.mainloop()