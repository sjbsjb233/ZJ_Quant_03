"""YAML配置加载：字典与属性两种访问方式并存."""

from __future__ import annotations

from pathlib import Path

import yaml


class Cfg(dict):
    """支持属性访问的配置字典：cfg.model.d_model 等价于 cfg['model']['d_model']."""

    def __getattr__(self, name):
        try:
            v = self[name]
        except KeyError as e:
            raise AttributeError(name) from e
        return Cfg(v) if isinstance(v, dict) else v

    def __setattr__(self, name, value):
        self[name] = value


def load_config(path: str | Path) -> Cfg:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return Cfg(raw)
