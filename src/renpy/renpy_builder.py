import json
import os
import platform
import re
import shutil
import subprocess
import sys

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "renpy_templates")


def write_options_rpy(game_dir: str, title: str, about: str = "", menu_bg: str = "") -> None:
    """Write game/options.rpy (config name/version/about + build identity) for a project.
    menu_bg: a generated title-card filename under game/images — becomes the main/game menu
    background instead of the stock template art."""
    safe = re.sub(r"[^A-Za-z0-9_]", "", title.replace(" ", "_")) or "UntitledGame"
    content = (
        f'define config.name = {json.dumps(title)}\n'
        f'define config.version = "1.0"\n'
        f'define gui.about = {json.dumps(about)}\n'
        f'define config.window_icon = None\n'
        f'define gui.show_name = True\n'
        f'define config.save_directory = "{safe}"\n'
    )
    if menu_bg and os.path.exists(os.path.join(game_dir, "images", menu_bg)):
        # gui.rpy `define`s these; a second define is a duplicate-definition error, so
        # override after init instead.
        content += (
            f'init 999 python:\n'
            f'    gui.main_menu_background = "images/{menu_bg}"\n'
            f'    gui.game_menu_background = "images/{menu_bg}"\n'
        )
    content += (
        f'init python:\n'
        f'    build.name = "{safe}"\n'
        f'    build.executable_name = "{safe}"\n'
        f'    build.directory_name = "{safe}-1.0"\n'
    )
    with open(os.path.join(game_dir, "options.rpy"), "w", encoding="utf-8") as f:
        f.write(content)

# Ships with every SDK; its gui.rpy/screens.rpy/gui/ are the stock 1280x720 set
_SDK_TEMPLATE_PROJECT = "the_question"


def _seed_templates_from_sdk(sdk_path: str):
    """Populate missing template assets from the SDK's stock project.
    The binary gui/ assets are gitignored, so fresh checkouts need this."""
    src_game = os.path.join(os.path.abspath(sdk_path), _SDK_TEMPLATE_PROJECT, "game")
    if not os.path.isdir(src_game):
        return
    os.makedirs(TEMPLATES_DIR, exist_ok=True)

    for filename in ("screens.rpy", "gui.rpy"):
        dst = os.path.join(TEMPLATES_DIR, filename)
        src = os.path.join(src_game, filename)
        if not os.path.exists(dst) and os.path.exists(src):
            shutil.copy2(src, dst)
            print(f"[renpy_builder]   seeded {filename} from SDK")

    gui_dst = os.path.join(TEMPLATES_DIR, "gui")
    gui_src = os.path.join(src_game, "gui")
    if not os.path.isdir(gui_dst) and os.path.isdir(gui_src):
        shutil.copytree(gui_src, gui_dst)
        print("[renpy_builder]   seeded gui/ from SDK")


def _copy_templates(game_dir: str, sdk_path: str = ""):
    if sdk_path:
        _seed_templates_from_sdk(sdk_path)

    required_files = ["screens.rpy", "gui.rpy"]
    missing = []

    for filename in required_files:
        src = os.path.join(TEMPLATES_DIR, filename)
        dst = os.path.join(game_dir, filename)
        if os.path.exists(src):
            shutil.copy2(src, dst)
            print(f"[renpy_builder]   copied {filename}")
        else:
            missing.append(src)

    gui_src = os.path.join(TEMPLATES_DIR, "gui")
    gui_dst = os.path.join(game_dir, "gui")
    if os.path.isdir(gui_src):
        if os.path.exists(gui_dst):
            shutil.rmtree(gui_dst)
        shutil.copytree(gui_src, gui_dst)
        print("[renpy_builder]   copied gui/")
    else:
        missing.append(gui_src)

    if missing:
        print("[renpy_builder] WARNING: Missing template files:")
        for path in missing:
            print(f"  {path}")
        print(f"  Copy screens.rpy, gui.rpy, and gui/ from any RenPy SDK project into: {TEMPLATES_DIR}/")
        print("  Without these, the game will crash on launch.")


def _distribute(project_dir: str, sdk_path: str) -> dict:
    sdk_path = os.path.abspath(sdk_path)

    if not os.path.isdir(sdk_path):
        print(f"[renpy_builder] ERROR: SDK path does not exist: {sdk_path}")
        return {"dist_error": f"SDK path does not exist: {sdk_path}"}

    launcher_dir = os.path.join(sdk_path, "launcher")
    # Self-contained platform archives land in a known <run>/dist so the download endpoint can
    # serve them without depending on Ren'Py's default (project-parent) output location.
    dist_dir = os.path.join(os.path.dirname(os.path.abspath(project_dir)), "dist")
    dest = ["--destination", dist_dir]
    system = platform.system()
    if system == "Windows":
        renpy_bin = os.path.join(sdk_path, "renpy.exe")
        if not os.path.exists(renpy_bin):
            py = _find_sdk_python(sdk_path)
            cmd = [py, os.path.join(sdk_path, "renpy.py"), launcher_dir, "distribute", os.path.abspath(project_dir), *dest]
        else:
            cmd = [renpy_bin, launcher_dir, "distribute", os.path.abspath(project_dir), *dest]
    else:
        renpy_sh = os.path.join(sdk_path, "renpy.sh")
        if not os.path.exists(renpy_sh):
            print(f"[renpy_builder] ERROR: renpy.sh not found in SDK: {sdk_path}")
            return {"dist_error": f"renpy.sh not found in SDK: {sdk_path}"}
        cmd = [renpy_sh, launcher_dir, "distribute", os.path.abspath(project_dir), *dest]

    print("[renpy_builder] Building distribution...")
    print(f"  SDK:     {sdk_path}")
    print(f"  Project: {os.path.abspath(project_dir)}")
    print(f"  Command: {' '.join(cmd)}")

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=60,
        )
        if proc.returncode == 0:
            print("[renpy_builder] Distribution built successfully.")
        else:
            print(f"[renpy_builder] Distribution build failed (exit {proc.returncode}).")
            if proc.stdout:
                print(proc.stdout)
            if proc.stderr:
                print(proc.stderr, file=sys.stderr)
        return {
            "dist_returncode": proc.returncode,
            "dist_stdout":     proc.stdout,
            "dist_stderr":     proc.stderr,
            "dist_dir":        dist_dir if proc.returncode == 0 else None,
        }
    except subprocess.TimeoutExpired as e:
        print(f"[renpy_builder] ERROR: SDK timed out after {e.timeout}s (likely waiting for input).")
        return {"dist_returncode": -1, "dist_error": f"timeout after {e.timeout}s"}
    except Exception as e:
        print(f"[renpy_builder] ERROR running SDK: {e}")
        return {"dist_error": str(e)}


def _find_sdk_python(sdk_path: str) -> str:
    lib = os.path.join(sdk_path, "lib")
    if os.path.isdir(lib):
        for entry in os.listdir(lib):
            if entry.startswith("py3-windows"):
                py = os.path.join(lib, entry, "python.exe")
                if os.path.exists(py):
                    return py
    return "python"
