@echo off
chcp 65001 >nul
rem Запуск бота карточек на Windows. Первый запуск ставит зависимости (5-10 минут).
cd /d "%~dp0"
if not exist .venv-cardbot (
    python -m venv .venv-cardbot
    .venv-cardbot\Scripts\python -m pip install --upgrade pip
    .venv-cardbot\Scripts\pip install -r card_bot\requirements.txt
)
if not exist card_bot\.env (
    copy card_bot\.env.example card_bot\.env
    echo Впишите токен бота в card_bot\.env и запустите снова.
    notepad card_bot\.env
    exit /b
)
.venv-cardbot\Scripts\python -m card_bot
pause
