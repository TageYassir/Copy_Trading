Place your application icon files here.

Recommended filenames:
- app_icon.ico  (Windows taskbar/start menu icon, 256x256 recommended)
- app_icon.png  (cross-platform, 256x256 recommended)

Usage:
- The app will look for the icon in either the DATA_ROOT assets folder or the package assets folder.
- On Windows the .ico will be applied with `iconbitmap`. PNG will be used via `iconphoto`.

To set the icon for packaged executables (pyinstaller / cx_Freeze), follow your packager's instructions and point to `assets/app_icon.ico`.
