@echo off
rem ============================================================================
rem  Construit les programmes d'installation Windows :
rem    dist\ShellyConfigurator-Setup-<version>-x64.exe  (Windows 64 bits)
rem    dist\ShellyConfigurator-Setup-<version>-x86.exe  (Windows 32 bits, fonctionne aussi en 64 bits)
rem
rem  Usage :  build_setup.bat          -> les deux
rem           build_setup.bat x64      -> 64 bits seulement
rem           build_setup.bat x86      -> 32 bits seulement
rem
rem  Prerequis sur le poste de construction (Windows 7 SP1 a Windows 11) :
rem    - Python 3.8.x 64 bits et/ou 32 bits (3.8.10 conseille, derniere version compatible Windows 7)
rem    - Inno Setup 6 (https://jrsoftware.org/isinfo.php)
rem ============================================================================
setlocal
cd /d "%~dp0\..\.."

set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
    echo Inno Setup 6 introuvable. Installez-le puis relancez ce script.
    exit /b 1
)

set "TARGETS=%~1"
if "%TARGETS%"=="" set "TARGETS=x64 x86"

for %%A in (%TARGETS%) do (
    call :build %%A || goto :error
)

echo.
echo TERMINE : programme(s) d'installation dans le dossier dist\
dir /b dist\*.exe
exit /b 0

rem ----------------------------------------------------------------------------
:build
set "ARCH=%~1"
if /i "%ARCH%"=="x64" (set "BITS=64") else if /i "%ARCH%"=="x86" (set "BITS=32") else (
    echo Architecture inconnue : %ARCH% ^(x64 ou x86^)
    exit /b 1
)
set "PY=py -3.8-%BITS%"
echo.
echo ############################################################
echo  Construction %ARCH% avec Python 3.8 %BITS% bits
echo ############################################################
%PY% -c "import struct, sys; sys.exit(0 if sys.version_info[:2] == (3, 8) and struct.calcsize('P') * 8 == %BITS% else 1)"
if errorlevel 1 (
    echo Python 3.8 %BITS% bits introuvable ^(commande : %PY%^).
    echo Installez-le depuis python.org puis relancez ce script.
    exit /b 1
)

echo === Outils de construction ===
%PY% -m pip install --disable-pip-version-check -r installer\windows\requirements-build.txt || exit /b 1

echo === Tests ===
%PY% -m unittest discover -s tests -t . || exit /b 1

echo === Executable (PyInstaller) ===
%PY% -m PyInstaller --noconfirm --clean --windowed --name ShellyConfigurator ^
    --distpath dist\%ARCH% --workpath build\%ARCH% --specpath build\%ARCH% ^
    --hidden-import tkinter --hidden-import tkinter.ttk ^
    shelly_configurator_launcher.pyw || exit /b 1

echo === Programme d'installation (Inno Setup) ===
set "VER="
for /f "delims=" %%V in ('%PY% -c "import shelly_configurator as s; print(s.__version__)"') do set "VER=%%V"
if "%VER%"=="" exit /b 1
"%ISCC%" /Q /DArch=%ARCH% /DMyAppVersion=%VER% installer\windows\setup.iss || exit /b 1
exit /b 0

:error
echo.
echo ECHEC de la construction.
exit /b 1
