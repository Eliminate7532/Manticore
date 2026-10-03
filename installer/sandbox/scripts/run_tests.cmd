@echo off
rem Runs inside Windows Sandbox at logon (the LogonCommand in the .wsb), with nobody at the keyboard.
rem The real work is in run_tests.ps1; this only starts it. Results: C:\sandbox_results\<timestamp>\ (the host's
rem installer\sandbox\results\<timestamp>\): steps.txt every step, version.txt, soak_summary.txt, data_folders.txt, verdict.txt.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\sandbox_scripts\run_tests.ps1
