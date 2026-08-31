@echo off
REM Regenerate every figure and the HTML page from the current answers export.
REM Double-click this file, or run it from a terminal, after saving a new CSV.
REM
REM The options below are the ones this study settles on; edit this line when
REM they change:
REM   --days all            every assessment point the export contains
REM   --alias Poo=P00 ...   fold mistyped participant ids into the right one
REM   --exclude Juvion-001  drop the test submission
set OPTIONS=--days all --alias Poo=P00 52=P52 --exclude Juvion-001

cd /d "%~dp0.."

echo Reading form_answers\juvionhs01_satisfaction_form_answers.csv
echo.

python scripts\plot_satisfaction.py %OPTIONS% --format both
if errorlevel 1 goto failed

python scripts\plot_satisfaction.py %OPTIONS% --per-section --format png
if errorlevel 1 goto failed

echo.
echo Done. Refresh the page in your browser (Ctrl+R) to see the new numbers.
goto end

:failed
echo.
echo Something went wrong - the message above says what.

:end
echo.
pause
