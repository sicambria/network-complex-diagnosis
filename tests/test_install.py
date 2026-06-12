import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _bash_syntax_ok(script):
    result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    return result.returncode == 0, result.stderr


def _read(path):
    return (ROOT / path).read_text()


class TestInstallSh:
    def test_syntax(self):
        ok, err = _bash_syntax_ok(ROOT / "install.sh")
        assert ok, f"install.sh syntax error:\n{err}"

    def test_has_platform_branches(self):
        c = _read("install.sh")
        assert "Linux)" in c
        assert "Darwin)" in c
        assert "MINGW*|MSYS*|CYGWIN*)" in c

    def test_installs_system_deps(self):
        c = _read("install.sh")
        assert "Step 1/4" in c
        assert "apt" in c or "dnf" in c or "pacman" in c
        assert "iputils-ping" in c or "iproute2" in c

    def test_installs_pip_deps(self):
        c = _read("install.sh")
        assert "Step 2/4" in c
        assert "pip install" in c
        assert "fastapi" in c
        assert "uvicorn" in c

    def test_creates_symlink(self):
        c = _read("install.sh")
        assert "Step 3/4" in c
        assert "ln -sf" in c
        assert "/usr/local/bin/netdiag" in c

    def test_desktop_integration(self):
        c = _read("install.sh")
        assert "Step 4/4" in c
        assert "install-desktop.sh" in c

    def test_shows_usage(self):
        c = _read("install.sh")
        assert "Installation complete" in c
        assert "netdiag --gui" in c
        assert "netdiag --daemon" in c


class TestInstallBat:
    def test_syntax(self):
        c = _read("install.bat")
        assert c.startswith("@echo off")
        assert "setlocal" in c

    def test_checks_python(self):
        c = _read("install.bat")
        assert "python3 --version" in c
        assert "python.org" in c

    def test_installs_pip_deps(self):
        c = _read("install.bat")
        assert "pip install" in c
        assert "fastapi" in c
        assert "uvicorn" in c

    def test_delegates_desktop_shortcuts(self):
        c = _read("install.bat")
        assert "setup\\windows\\install.bat" in c

    def test_lists_optional_tools(self):
        c = _read("install.bat")
        assert "speedtest-cli" in c
        assert "iperf3" in c

    def test_shows_usage(self):
        c = _read("install.bat")
        assert "Installation complete" in c
        assert "--gui" in c
        assert "--daemon" in c


class TestUninstallSh:
    def test_syntax(self):
        ok, err = _bash_syntax_ok(ROOT / "uninstall.sh")
        assert ok, f"uninstall.sh syntax error:\n{err}"

    def test_removes_symlink(self):
        c = _read("uninstall.sh")
        assert "/usr/local/bin/netdiag" in c
        assert "rm -f /usr/local/bin/netdiag" in c

    def test_removes_history(self):
        c = _read("uninstall.sh")
        assert "HISTDIR" in c or ".netdiag" in c

    def test_removes_system_packages(self):
        c = _read("uninstall.sh")
        assert "apt remove" in c
        assert "iputils-ping" in c or "iproute2" in c

    def test_removes_pip_packages(self):
        c = _read("uninstall.sh")
        assert "pip uninstall" in c
        assert "fastapi" in c
        assert "uvicorn" in c

    def test_removes_systemd_service(self):
        c = _read("uninstall.sh")
        assert "systemctl" in c

    def test_silent_flag(self):
        c = _read("uninstall.sh")
        assert "--silent" in c


class TestInstallDesktopSh:
    def test_syntax(self):
        ok, err = _bash_syntax_ok(ROOT / "setup/install-desktop.sh")
        assert ok, f"install-desktop.sh syntax error:\n{err}"

    def test_has_linux_branch(self):
        c = _read("setup/install-desktop.sh")
        assert "Linux)" in c
        assert ".local/share/applications" in c

    def test_has_macos_branch(self):
        c = _read("setup/install-desktop.sh")
        assert "Darwin)" in c
        assert "NetDiag.app" in c

    def test_has_windows_branch(self):
        c = _read("setup/install-desktop.sh")
        assert "MINGW*|MSYS*|CYGWIN*)" in c
        assert "Start Menu" in c


class TestWindowsSetup:
    def test_install_bat_structure(self):
        c = _read("setup/windows/install.bat")
        assert "python3" in c or "Python" in c
        assert "pip install" in c
        assert "Start Menu" in c
        assert "Desktop" in c

    def test_netdiag_ps1_launcher(self):
        c = _read("setup/windows/netdiag.ps1")
        assert "netdiag.py" in c
        assert "--gui" in c
        assert "localhost:8080" in c
        assert "Start-Process" in c

    def test_netdiag_bat_launcher(self):
        c = _read("setup/windows/netdiag.bat")
        assert "netdiag.py" in c
        assert "--gui" in c
        assert "localhost:8080" in c
        assert "start" in c


class TestMacOSSetup:
    def test_app_launcher_syntax(self):
        ok, err = _bash_syntax_ok(ROOT / "setup/macos/NetDiag.app/Contents/MacOS/NetDiag")
        assert ok, f"macOS launcher syntax error:\n{err}"

    def test_app_launcher_structure(self):
        c = _read("setup/macos/NetDiag.app/Contents/MacOS/NetDiag")
        assert "netdiag.py" in c
        assert "--gui" in c
        assert "localhost:8080" in c

    def test_macos_launcher_script_syntax(self):
        ok, err = _bash_syntax_ok(ROOT / "setup/macos/macos-launcher.sh")
        assert ok, f"macos-launcher.sh syntax error:\n{err}"

    def test_macos_launcher_script_structure(self):
        c = _read("setup/macos/macos-launcher.sh")
        assert "netdiag.py" in c
        assert "--gui" in c
        assert "open" in c

    def test_info_plist_exists(self):
        p = ROOT / "setup/macos/NetDiag.app/Contents/Info.plist"
        assert p.exists()
        c = p.read_text()
        assert "NetDiag" in c


class TestLinuxSetup:
    def test_launcher_syntax(self):
        ok, err = _bash_syntax_ok(ROOT / "setup/linux/netdiag-launcher.sh")
        assert ok, f"netdiag-launcher.sh syntax error:\n{err}"

    def test_launcher_structure(self):
        c = _read("setup/linux/netdiag-launcher.sh")
        assert "netdiag.py" in c
        assert "--gui" in c

    def test_desktop_entry_exists(self):
        p = ROOT / "setup/linux/netdiag.desktop"
        assert p.exists()
        c = p.read_text()
        assert "NetDiag" in c
        assert "Terminal" in c


class TestRootConsistency:
    def test_install_sh_and_bat_have_same_steps(self):
        sh = _read("install.sh")
        bat = _read("install.bat")
        for keyword in ["Python", "pip", "Desktop", "speedtest-cli", "iperf3"]:
            assert keyword in sh or keyword.lower() in sh
            assert keyword in bat or keyword.lower() in bat
        has_step = lambda c: "Step 1" in c or "1/3" in c or "1/4" in c
        assert has_step(sh)
        assert has_step(bat)
        for phrase in ["--gui", "--daemon", "Installation complete"]:
            assert phrase in sh
            assert phrase in bat

    def test_all_root_installers_exist(self):
        assert (ROOT / "install.sh").exists()
        assert (ROOT / "install.bat").exists()
        assert (ROOT / "uninstall.sh").exists()

    def test_all_launcher_scripts_exist(self):
        assert (ROOT / "setup/linux/netdiag-launcher.sh").exists()
        assert (ROOT / "setup/macos/NetDiag.app/Contents/MacOS/NetDiag").exists()
        assert (ROOT / "setup/macos/macos-launcher.sh").exists()
        assert (ROOT / "setup/windows/netdiag.ps1").exists()
        assert (ROOT / "setup/windows/netdiag.bat").exists()
