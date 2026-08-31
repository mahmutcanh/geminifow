@echo off
chcp 65001 > nul
title Geminiflow Web Panel Server
cd /d "%~dp0webpanel"
echo Web Paneli Baslatiliyor (Port 5051)...
python app.py
pause
