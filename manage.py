#!/usr/bin/env python
"""Django 命令行入口。默认使用开发配置，可用 DJANGO_SETTINGS_MODULE 覆盖。"""

import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
