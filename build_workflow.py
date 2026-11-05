"""从明确的文件清单直接构建安装包，不建立 staging 项目副本。"""
from __future__ import annotations

import json
import plistlib
import zipfile
from pathlib import Path

from config import BUNDLE_ID, ROOT, VERSION

RUNTIME_FILES = (
    "main.py", "config.py", "models.py", "parser.py", "scheduler.py", "cache.py",
    "http_client.py", "output.py", "utils.py", "bootstrap.sh", "config.example.json",
    "info.plist", "README.md",
)


def build() -> Path:
    metadata = plistlib.loads((ROOT / "info.plist").read_bytes())
    if metadata["bundleid"] != BUNDLE_ID or metadata["version"] != VERSION:
        raise ValueError("Workflow 元数据与代码版本不一致")
    example = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    for settings in example["engines"].values():
        if any(settings.get(key) for key in ("ak", "sk", "key", "appid", "appkey")):
            raise ValueError("配置模板不得包含凭据")
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
            info.external_attr = (0o100755 if path.name == "bootstrap.sh" else 0o100644) << 16
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
    return output


if __name__ == "__main__":
    print(build().relative_to(ROOT))
