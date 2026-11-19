"""环境变量样例契约测试：.env.example 须覆盖 settings 实际读取的全部变量。

真实事故：REDIS_URL / YGG_VERBOSE_LOG 等先后加入 settings 而
.env.example 未同步——站长按样例配环境时漏配，问题难自查。
此处锁定：settings 读的每个环境变量在样例中可寻（注释性变量除外）。
"""

from pathlib import Path


def _env_names_from_settings() -> set[str]:
    """从 base.py 提取全部 os.environ / env_bool / env_int 读取的变量名。"""
    src = Path("config/settings/base.py").read_text(encoding="utf-8")
    import re

    names = set()
    for m in re.finditer(r'os\.environ(?:\.get)?\(\s*["\'](\w+)["\']', src):
        names.add(m.group(1))
    for m in re.finditer(r'env_bool\(\s*["\'](\w+)["\']', src):
        names.add(m.group(1))
    for m in re.finditer(r'env_int\(\s*["\'](\w+)["\']', src):
        names.add(m.group(1))
    # 内部机制变量：样例中无意义
    names -= {"ENV_FILE"}
    return names


class TestEnvExampleCoversSettings:
    def test_every_settings_env_var_documented(self):
        example = Path(".env.example").read_text(encoding="utf-8")
        missing = [
            name
            for name in sorted(_env_names_from_settings())
            if name not in example
        ]
        assert not missing, f".env.example 缺少文档：{missing}"

    def test_example_has_real_defaults_sections(self):
        """样例保留分区结构与关键缺省值说明（防样例被精简成空壳）。"""
        example = Path(".env.example").read_text(encoding="utf-8")
        for section in ("应用", "缓存", "Yggdrasil", "数据库"):
            assert section in example
        # 缺省回落语义必须写明（站长据此判断可否留空）
        assert "createcachetable" in example
        assert "DatabaseCache" in example or "数据库" in example
