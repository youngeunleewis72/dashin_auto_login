@echo off
if not exist "collector failure.bat" exit /b 19
if defined CYBOS_CERT_PASSWORD exit /b 20
exit /b 17
