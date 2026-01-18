import PyInstaller.__main__
import sys
import os
import shutil

def build():
    print("Starting build process...")
    
    # Determine OS
    is_windows = sys.platform == 'win32'
    is_macos = sys.platform == 'darwin'
    
    # Base arguments
    args = [
        'm3u_editor.py',
        '--name=M3UEditor',
        '--windowed',  # Don't show console
        '--noconfirm',
        '--clean',
        # Include helper module
        '--add-data=performance_utils.py:.',
    ]

    # Platform specific settings
    if is_windows:
        args.append('--icon=icon.ico') # Assuming you might have an icon
    elif is_macos:
        args.append('--icon=icon.icns')
        # macOS specific bundle identifier
        args.append('--osx-bundle-identifier=com.opensource.m3ueditor')

    # Add plugins folder if it exists
    if os.path.exists('plugins'):
        sep = ';' if is_windows else ':'
        args.append(f'--add-data=plugins{sep}plugins')

    # Run PyInstaller
    try:
        PyInstaller.__main__.run(args)
        print("Build complete.")
        
        # Post-build instructions
        dist_folder = os.path.join(os.getcwd(), 'dist')
        if is_macos:
            print(f"App bundle located at: {os.path.join(dist_folder, 'M3UEditor.app')}")
        elif is_windows:
            print(f"Executable located at: {os.path.join(dist_folder, 'M3UEditor', 'M3UEditor.exe')}")
        else:
            print(f"Executable located at: {os.path.join(dist_folder, 'M3UEditor')}")
            
    except Exception as e:
        print(f"Build failed: {e}")

if __name__ == "__main__":
    # Ensure we are in the script directory
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    build()