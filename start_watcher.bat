@echo off
cd /d "%~dp0"
py watcher.py >> watcher.log 2>&1
