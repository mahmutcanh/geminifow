@echo off
title Google Hesabina Giris (Flow)
echo ========================================================
echo Google Hesabina Giris Penceresi Aciliyor...
echo Lutfen acilan Chrome penceresinde Google hesabiniza giris yapin.
echo Flow panelini gordukten sonra tarayiciyi kapatabilirsiniz.
echo ========================================================
cd /d "%~dp0flow_bot"
python login.py
pause
