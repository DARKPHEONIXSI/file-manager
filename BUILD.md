# How to Build the File Organizer Executable

If you ever make changes to `organizer.py` and want to build a fresh, double-clickable `.exe`, follow these simple steps:

1. Open your terminal (PowerShell or Command Prompt) in your project folder (`D:\coding\file manager`).
2. Make sure PyInstaller is installed by running:
   ```bash
   pip install pyinstaller
   ```
3. Run this exact command to package your script into a single file:
   ```bash
   pyinstaller --onefile organizer.py
   ```
   *(Note: This is the simplest possible command. We do not need `--add-data` or any hidden imports because the script uses only standard Python libraries.)*

4. Wait for it to finish. You will see a `Build complete!` message.
5. Your brand-new executable will be located in the `dist` folder:
   `D:\coding\file manager\dist\organizer.exe`

You can safely delete the `build` folder and the `organizer.spec` file that PyInstaller automatically generates; they are only used during the building process.
