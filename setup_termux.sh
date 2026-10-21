#!/data/data/com.termux/files/usr/bin/bash
# One-time setup inside Termux (install Termux from F-Droid, not the Play Store).
set -e
pkg update -y
pkg install -y python tmux termux-api git
# pydantic is a compiled dependency of FastAPI; the Termux package avoids a long Rust build.
pkg install -y python-pydantic || pkg install -y rust binutils clang make libffi openssl
pip install -r requirements.txt
mkdir -p data logs
echo
echo "Done. Start with:  ./run.sh"
echo "Optional extras:   pip install -r requirements-live.txt   (live trading via ccxt)"
echo "                   pip install -r requirements-crewai.txt (CrewAI commentary; often fails on Android)"
