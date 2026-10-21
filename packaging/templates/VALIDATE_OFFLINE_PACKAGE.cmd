@echo off
rem Checks this package on this PC without Internet: files, Python, Java, JMeter, Chromium, credential store.
call "%~dp0payload\VALIDATE_OFFLINE.cmd" %*
