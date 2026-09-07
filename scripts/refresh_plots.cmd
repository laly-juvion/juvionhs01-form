@echo off
REM Regenerate every figure and the HTML page from the current answers export.
REM Double-click this file, or run it from a terminal, after saving a new CSV.
REM
REM The options below are the ones this study settles on; edit this line when
REM they change:
REM   --days all            every assessment point the export contains
REM   --exclude Juvion-001  drop the test submission
REM
REM The Poo=P00 and 52=P52 aliases were removed 2026-09-07: the source data
REM now uses the correct ids (P00, P52) directly, so folding them is no
REM longer needed. If a similar typo shows up again, add it back here as
REM --alias WRONG=RIGHT.
set OPTIONS=--days all --exclude Juvion-001

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
