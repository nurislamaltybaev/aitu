@echo off
rem Builds dist\Jarvis_Portable.exe: the desktop client only, no API keys inside.
rem Before building, set DEFAULT_SERVER (and CLIENT_TOKEN if your server uses one) in vision_client.py.
rem The server (server\) is deployed separately, see render.yaml.
cd /d "%~dp0"
python -m pip install -r requirements.txt || exit /b 1
python -m PyInstaller --noconfirm --clean --onefile --noconsole --name Jarvis_Portable ^
  --hidden-import pydantic --hidden-import pydantic_core --hidden-import mss --hidden-import keyboard ^
  --hidden-import PIL --hidden-import PIL.ImageDraw --hidden-import PIL.ImageFont ^
  --hidden-import httpx --hidden-import dotenv --hidden-import pyperclip --hidden-import server.planner ^
  --hidden-import sounddevice --hidden-import speech_recognition --collect-data speech_recognition ^
  --hidden-import pyttsx3 --hidden-import pyttsx3.drivers --hidden-import pyttsx3.drivers.sapi5 --hidden-import pythoncom ^
  --exclude-module openai --exclude-module fastapi --exclude-module uvicorn --exclude-module anthropic ^
  main.py || exit /b 1
echo.
echo Done: %~dp0dist\Jarvis_Portable.exe
