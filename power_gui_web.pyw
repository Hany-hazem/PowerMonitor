import psutil
import time

print("--- Power Monitor Tool V1 ---")
print("Press Ctrl+C to stop")

while True:
    cpu = psutil.cpu_percent(interval=1)
    # Simple math: 30W base + usage
    watts = 30 + (cpu * 1.5)
    print(f"CPU Load: {cpu}% | Est Power: {watts:.1f} W")