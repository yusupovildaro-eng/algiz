@echo off
cd /d "C:\Users\yusup\tg-migalki-bot"
claude -p "/prices" --permission-mode dontAsk --allowedTools "Bash(python main.py prices *),WebSearch,WebFetch,Edit(reports/**)" >> logs\prices.log 2>&1
