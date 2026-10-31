@echo off
setlocal
call "C:\Program Files\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" || exit /b 1
cd /d E:\SplatNative
if not exist build\obj mkdir build\obj
if "%1"=="smoke" (
  cl /nologo /c /O2 /bigobj /MD /Isrc\runtime build\gen\sdk_gen_000.c /Fobuild\obj\smoke.obj || exit /b 1
  echo SMOKE_OK
  exit /b 0
)
cl /nologo /O2 /bigobj /MD /Isrc\runtime ^
  src\runtime\main.c src\runtime\a64_helpers.c src\runtime\nso_loader.c ^
  src\runtime\vendor\lz4.c ^
  build\gen\*.c ^
  /Fobuild\obj\ /Fe:build\splatnative.exe /link /SUBSYSTEM:CONSOLE || exit /b 1
echo BUILD_OK
endlocal
