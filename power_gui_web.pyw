import customtkinter as ctk
import psutil

app = ctk.CTk()
app.geometry("300x200")
app.title("Power Monitor V2")

label = ctk.CTkLabel(app, text="Initializing...", font=("Arial", 24))
label.pack(pady=20, padx=20)

def update_power():
    cpu = psutil.cpu_percent()
    watts = 30 + (cpu * 1.5)
    label.configure(text=f"{watts:.1f} W")
    app.after(1000, update_power)

update_power()
app.mainloop()