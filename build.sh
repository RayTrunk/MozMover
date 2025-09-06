#!/bin/bash

echo "========================================"
echo "   MozMover Build Script - Linux/macOS"
echo "========================================"
echo

# Check if Nuitka is installed
if ! python3 -c "import nuitka" &> /dev/null; then
    echo "Installing Nuitka..."
    pip3 install nuitka
fi

echo "Installing dependencies..."
pip3 install -r requirements.txt

echo
echo "Building MozMover..."
echo "This may take several minutes for the first build..."
echo

python3 -m nuitka --standalone --onefile --enable-plugin=pyside6 --include-qt-plugins=sensible --remove-output MozMover.py

if [ $? -eq 0 ]; then
    echo
    echo "========================================"
    echo "   BUILD SUCCESSFUL!"
    echo "   Executable: MozMover.bin"
    echo "========================================"
else
    echo
    echo "========================================"
    echo "   BUILD FAILED!"
    echo "   Please check the error messages above"
    echo "========================================"
fi
