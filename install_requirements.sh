#!/bin/bash

echo "=========================================="
echo "   POWER MONITOR LAUNCHER (Mac/Linux)"
echo "=========================================="

# 1. Check for Python
if command -v python3 &>/dev/null; then
    PY_CMD="python3"
elif command -v python &>/dev/null; then
    PY_CMD="python"
else
    echo "❌ Error: Python is not installed."
    echo "Please install Python 3 from python.org or your package manager."
    read -p "Press Enter to exit..."
    exit 1
fi

# 2. Setup Virtual Environment (Keeps their PC clean)
VENV_DIR=".venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "⚙️  Creating virtual environment..."
    $PY_CMD -m venv $VENV_DIR
fi

# 3. Activate Venv
source $VENV_DIR/bin/activate

# 4. Install Dependencies (Quietly)
if [ ! -f "installed.flag" ]; then
    echo "⬇️  Installing libraries (First run only)..."
    
    # Check if pynvml is needed (Linux only, not Mac)
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        pip install nvidia-ml-py &>/dev/null
    fi
    
    pip install -r requirements.txt
    touch installed.flag
fi

# 5. Run the App
echo "🚀 Starting Dashboard..."
# Run in background using python (not pythonw on linux/mac usually)
python power_gui_web.pyw &

echo "Done! You can close this terminal."
sleep 2