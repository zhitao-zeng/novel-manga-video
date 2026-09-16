"""Existing named-entity exclusions shared by bible growth and the ledger."""
from __future__ import annotations

import re


GENERIC_NAMES = {
    "少年", "少女", "老者", "老人", "青年", "男子", "女子", "男人", "女人", "父亲", "母亲", "爹", "娘", "众人", "弟子", "长老",
    "中年男子", "中年人", "中年女子", "女孩", "男孩", "孩子", "小孩", "管家", "族人", "侍女", "下人", "仆人", "护卫", "士兵", "路人",
}


APPELLATION = re.compile(r"(老者|老人|青年|少年|少女|男子|女子|男人|女人|管家|长老|宗主|使者|弟子|侍女|公子|小姐|大汉|汉子|老头|老妇|妇人|姑娘|先生|夫人|老爷|少爷)$")
