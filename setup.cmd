@echo off
rem One-click setup and launch for Windows. Double-click, or run: setup.cmd [-SkipInstall] [-NoBrowser]
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1" %*
