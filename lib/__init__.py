"""paper-dual-reduce 的公共库。

导入即接管 stdout/stderr 的编码错误策略（编不出的字符降级为 '?' 而不是崩），
因为这里每个脚本都会被不同宿主的 shell 以不同控制台编码调用——中文 Windows 是
GBK、agent 宿主多是 UTF-8 管道、有的还是 cp1252。这条副作用只影响终端显示，
不影响任何数据文件（一律显式 UTF-8 读写）。
"""

from __future__ import annotations

from .cli import safe_io as _safe_io

_safe_io()
