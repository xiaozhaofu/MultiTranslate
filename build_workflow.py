"""从明确的文件清单直接构建安装包，不建立 staging 项目副本。"""
from __future__ import annotations

import json
import plistlib
import subprocess
import tempfile
import zipfile
from pathlib import Path

from config import BUNDLE_ID, ROOT, VERSION

RUNTIME_FILES = (
    "main.py", "config.py", "panel_import.py", "models.py", "parser.py", "scheduler.py", "cache.py",
    "http_client.py", "output.py", "utils.py", "bootstrap.sh", "config.example.json",
    "info.plist", "README.md", "settings_window.py", "config_window.swift", "config_window",
    "icon.png", "icon.svg",
)


def build_native_window() -> None:
    """直接在原目录编译双架构窗口；临时编译缓存不进入发行包。"""
    sdk = subprocess.check_output(["xcrun", "--show-sdk-path"], text=True).strip()
    with tempfile.TemporaryDirectory(prefix=".native-build-", dir=ROOT) as directory:
        temporary = Path(directory)
        relative = temporary.relative_to(ROOT)
        objects = []
        for architecture in ("x86_64", "arm64"):
            target = relative / architecture
            subprocess.run([
                "xcrun", "swiftc", "-Osize", "-sdk", sdk,
                "-target", architecture + "-apple-macosx11.0",
                "-module-name", "MultiTranslateConfig",
                "-module-cache-path", str(relative / "module-cache"),
                "-debug-prefix-map", str(ROOT) + "=.", "-file-compilation-dir", ".",
                "config_window.swift", "-o", str(target),
            ], cwd=ROOT, check=True)
            objects.append(str(target))
        output = relative / "config_window"
        subprocess.run(["xcrun", "lipo", "-create", *objects, "-output", str(output)], cwd=ROOT, check=True)
        subprocess.run(["codesign", "--force", "--sign", "-", "--timestamp=none", str(output)],
                       cwd=ROOT, check=True, capture_output=True)
        content = (ROOT / output).read_bytes()
        if any(str(path).encode() in content for path in (ROOT, Path.home())):
            raise ValueError("窗口程序包含本机路径，拒绝打包")
        (ROOT / output).chmod(0o755)
        (ROOT / output).replace(ROOT / "config_window")


def build() -> Path:
    metadata = plistlib.loads((ROOT / "info.plist").read_bytes())
    if metadata["bundleid"] != BUNDLE_ID or metadata["version"] != VERSION:
        raise ValueError("Workflow 元数据与代码版本不一致")
    example = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    if example.get("proxy", {}).get("url") or any(settings.get("proxy_url") for settings in example["engines"].values()):
        raise ValueError("配置模板不得预填代理地址或代理凭据")
    for settings in example["engines"].values():
        if any(settings.get(key) for key in ("ak", "sk", "key", "appid", "appkey", "app_key", "app_secret")):
            raise ValueError("配置模板不得包含凭据")
    private_variables = {"PYTHON_BINARY", "MT_PROXY_URL"}
    for engine, settings in example["engines"].items():
        private_variables.update(f"MT_{engine.upper()}_{key.upper()}" for key in settings
                                 if key not in ("enable", "region", "proxy_mode"))
    for field in metadata.get("userconfigurationconfig", []):
        if field.get("variable") in private_variables and field.get("config", {}).get("default"):
            raise ValueError("原生面板不得预填密钥、代理地址或本机解释器路径")
    if any(metadata.get("variables", {}).get(name) for name in private_variables):
        raise ValueError("发行环境变量不得包含私有值")
    build_native_window()
    files = [ROOT / name for name in RUNTIME_FILES]
    files.extend(sorted((ROOT / "adapters").glob("*.py")))
    output = ROOT / "dist" / "MultiTranslate.alfredworkflow"
    output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            if path.is_symlink() or not path.is_file():
                raise ValueError("打包清单包含缺失文件或符号链接")
            info = zipfile.ZipInfo(path.relative_to(ROOT).as_posix(), (2026, 11, 5, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o100755 if path.name in ("bootstrap.sh", "config_window") else 0o100644) << 16
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
    return output


if __name__ == "__main__":
    print(build().relative_to(ROOT))
