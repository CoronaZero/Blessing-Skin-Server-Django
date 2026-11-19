"""cache 共享后端部署契约测试。

部署背景：gunicorn --workers 3 + 默认 LocMemCache（进程内存隔离）时，
OAuth state 写进 worker A、回调落到 worker B 读不到 →「授权状态已
过期或无效」约 2/3 概率触发。要求任何部署形态下 cache 为进程外共
享后端（Redis / 数据库）。

- 后端选择逻辑：tests/test_settings.py::TestCaches
- 跨句柄/跨 worker 共享语义：cache_worker_probe.py（子进程真实
  DatabaseCache 验证，规避 pytest-django 对 default 连接的事务包裹
  与 :memory: 每连接一库的限制）
- 启动链契约：本文件 TestDeploymentContracts
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.django_db(transaction=True)


class TestStateCrossWorker:
    def test_state_survives_across_workers(self, tmp_path):
        """子进程端到端：state 写入句柄 A、回调句柄 B 可见且一次性作废。"""
        import subprocess
        import sys

        db_file = tmp_path / "workers.sqlite3"
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).parent / "cache_worker_probe.py"),
                str(db_file),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=None,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"probe 失败：{result.stdout} {result.stderr}"
        )
        assert "OK" in result.stdout


class TestDeploymentContracts:
    def test_startup_commands_include_createcachetable(self):
        """部署契约：Dockerfile 启动命令含 createcachetable（CI 可见）。

        DatabaseCache 的表不在迁移体系内（django_cache_table 由
        createcachetable 建立），漏掉启动命令 = 上线即 500。
        agent.md 在 .gitignore 中（本地文档），存在时一并检查，
        CI checkout 无此文件自然跳过——不得断言其必然存在。
        """
        dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
        assert "createcachetable" in dockerfile
        agent = Path("agent.md")
        if agent.exists():
            assert "createcachetable" in agent.read_text(encoding="utf-8")

    def test_settings_source_has_no_default_memcache(self):
        """CACHES 配置行必须为共享后端（无内存后端混入）。"""
        src = Path("config/settings/base.py").read_text(encoding="utf-8")
        assert "django.core.cache.backends.db.DatabaseCache" in src
        assert "django.core.cache.backends.redis.RedisCache" in src
        code_lines = [
            line for line in src.splitlines()
            if "BACKEND" in line and not line.strip().startswith("#")
        ]
        assert code_lines, "settings 未配置任何 cache BACKEND"
        for line in code_lines:
            assert "locmem" not in line.lower(), f"内存后端混入配置：{line}"

    def test_probe_script_in_sync_with_state_module(self):
        """probe 脚本引用的接口与 oauth_bridge.state 保持一致（防漂移）。"""
        probe = Path(__file__).with_name("cache_worker_probe.py")
        src = probe.read_text(encoding="utf-8")
        assert "put_state" in src and "pop_state" in src
        assert "SESSION_KEY" in src
        state_mod = Path("oauth_bridge/state.py").read_text(encoding="utf-8")
        assert "def put_state" in state_mod and "def pop_state" in state_mod
