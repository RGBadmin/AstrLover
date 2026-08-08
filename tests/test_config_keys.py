"""配置键的静态体检。

conf 从普通 dict 换成 Settings 之后，`app.conf["x"]` 这种下标写法会
TypeError——但只在那行真被执行时才炸。导演 bot 的 token 就是这么漏到
线上的：测试里 console_token 一直是空的，那行永远没跑到。

键名写错同理：Settings.get 找不到就给默认值，安静地按默认行为跑，
没有任何报错。所以这两类都得静态扫，不能等运行时。
"""

import ast
import json
from pathlib import Path

from astrlover.settings import BY_KEY

ROOT = Path(__file__).resolve().parent.parent

# imagegen 的后端拿到的是自己那份普通 dict（base.py: self.conf = conf or {}），
# 同名不同物，不在这次体检范围内
SKIP_DIRS = {"imagegen"}


def _wiring_keys() -> set[str]:
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    return set(schema["wiring"]["items"].keys())


def _is_app_conf(node) -> bool:
    """认出 app.conf / self.app.conf / self.conf（后者只在 app.py 里是 Settings）。"""
    if not isinstance(node, ast.Attribute) or node.attr != "conf":
        return False
    base = node.value
    if isinstance(base, ast.Name) and base.id in ("app", "self"):
        return True
    return isinstance(base, ast.Attribute) and base.attr == "app"


def _sources():
    for f in sorted((ROOT / "astrlover").rglob("*.py")):
        if set(f.relative_to(ROOT).parts) & SKIP_DIRS:
            continue
        yield f, ast.parse(f.read_text(encoding="utf-8"))


def test_no_dict_subscript_on_settings():
    """Settings 不是字典，conf[...] 一律用 conf.get(...)。"""
    bad = [
        f"{f.relative_to(ROOT)}:{n.lineno}"
        for f, tree in _sources()
        for n in ast.walk(tree)
        if isinstance(n, ast.Subscript) and _is_app_conf(n.value)
    ]
    assert not bad, "Settings 不支持下标，改用 .get()：\n" + "\n".join(bad)


def test_every_conf_key_exists():
    """conf.get('x') 里的 x 必须是 SPEC 项或接线项，否则永远拿默认值。"""
    known = set(BY_KEY) | _wiring_keys()
    bad = []
    for f, tree in _sources():
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "get" and _is_app_conf(n.func.value)):
                continue
            if n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
                if n.args[0].value not in known:
                    bad.append(f"{f.relative_to(ROOT)}:{n.lineno}  {n.args[0].value!r}")
    assert not bad, "这些键既不在 SPEC 也不在接线里：\n" + "\n".join(bad)


def _s_keys(tree):
    """Cfg 视图里 self._s("x") / _s("x") 引用的键。"""
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        fn = n.func
        name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
        if name != "_s" or not n.args:
            continue
        if isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
            yield n.lineno, n.args[0].value


def test_cfg_view_keys_exist():
    """生命层配置视图 Cfg 里 _s('x') 引用的键同理。"""
    known = set(BY_KEY) | _wiring_keys()
    tree = ast.parse((ROOT / "astrlover" / "config.py").read_text(encoding="utf-8"))
    found = list(_s_keys(tree))
    assert found, "一个都没扫到，说明扫描逻辑坏了"
    bad = [f"config.py:{ln}  {k!r}" for ln, k in found if k not in known]
    assert not bad, "Cfg 引用了不存在的键：\n" + "\n".join(bad)


def test_wiring_and_spec_do_not_overlap():
    """同一个键不能既在配置页又在面板——两处都能改，用户不知道哪个说了算。"""
    dup = set(BY_KEY) & _wiring_keys()
    assert not dup, f"这些键在 SPEC 和接线里各有一份：{sorted(dup)}"
