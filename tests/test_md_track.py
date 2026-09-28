"""M3 MD 轨测试: persona 常驻段 + HostFilesIndex 索引/召回。
"""
import tempfile
from pathlib import Path

import pytest

from soul_buddy.memory.persona import render_persona
from soul_buddy.memory.host_files import HostFilesIndex


def _root():
    d = Path(tempfile.mkdtemp())
    return d


def test_persona_render_and_degrade():
    d = _root()
    assert render_persona(d) == ""                # 无文件降级空段
    (d / "SOUL.md").write_text("我是乐于助人的编码助手", encoding="utf-8")
    (d / "AGENTS.md").write_text("遵守既有约定", encoding="utf-8")
    text = render_persona(d)
    assert "## Persona" in text
    assert "编码助手" in text and "既有约定" in text
    # 系统只读: 渲染不改文件
    assert (d / "SOUL.md").read_text(encoding="utf-8") == "我是乐于助人的编码助手"


def test_hostfiles_index_and_search():
    d = _root()
    (d / "MEMORY.md").write_text("项目用pytest做单元测试，覆盖率目标90%", encoding="utf-8")
    (d / "PROJECT.md").write_text("后端用FastAPI，前端用React", encoding="utf-8")
    (d / "USER.md").write_text("用户偏好简洁的代码风格", encoding="utf-8")
    idx = HostFilesIndex(d, db_path=tempfile.mktemp(suffix=".db"))
    # 中文逐字命中
    assert any("pytest" in h.snippet for h in idx.search("pytest"))
    assert any("单元测试" in h.snippet for h in idx.search("单元测试"))
    assert any("React" in h.snippet for h in idx.search("React"))
    # layer 名是文件名
    names = {h.name for h in idx.search("测试")}
    assert "MEMORY.md" in names
    # 幂等: 再扫不重建(统计 added=0)
    stats = idx.scan()
    assert stats["added"] == 0
    idx.close()


def test_hostfiles_detect_change_and_delete():
    d = _root()
    p = d / "MEMORY.md"
    p.write_text("A", encoding="utf-8")
    idx = HostFilesIndex(d, db_path=tempfile.mktemp(suffix=".db"))
    assert idx.search("A")
    p.write_text("B内容变了", encoding="utf-8")
    idx.scan()                                    # mtime 变 → 重建
    assert any("变了" in h.snippet for h in idx.search("变了"))
    p.unlink()
    idx.scan()                                    # 删除 → 移除索引
    assert idx.search("A") == [] and idx.search("变了") == []
    idx.close()
