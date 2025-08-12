#!/usr/bin/env python3

import os
import signal
from string import Template
import subprocess
import time
from TdcPlugin import TdcPlugin

from tdc_config import *

try:
    from scapy.all import *
except ImportError:
    print("Unable to import the scapy python module.")
    print("\nIf not already installed, you may do so with:")
    print("\t\tpip3 install scapy==2.4.2")
    exit(1)

import ast
import scapy.all as scapy_all


def _validate_ast_for_scapy(expr: str) -> ast.AST:
    tree = ast.parse(expr, mode="eval")
    allowed_nodes = (
        ast.Expression,
        ast.BinOp,
        ast.Call,
        ast.Name,
        ast.Attribute,
        ast.keyword,
        ast.Load,
        ast.Constant,
        ast.Dict,
        ast.List,
        ast.Tuple,
        ast.UnaryOp,
        ast.USub,
        ast.UAdd,
    )
    allowed_ops = (ast.Div, ast.Add)

    class Validator(ast.NodeVisitor):
        def visit(self, node):
            if not isinstance(node, allowed_nodes):
                raise ValueError(f"Disallowed syntax in packet expression: {type(node).__name__}")
            return super().visit(node)

        def visit_BinOp(self, node: ast.BinOp):
            if not isinstance(node.op, allowed_ops):
                raise ValueError("Only '/' layering and '+' payload concat are allowed")
            self.visit(node.left)
            self.visit(node.right)

        def visit_Name(self, node: ast.Name):
            if node.id not in dir(scapy_all):
                # Allow True/False/None used as constants in kwargs
                if node.id not in {"True", "False", "None"}:
                    raise ValueError(f"Unknown or disallowed name: {node.id}")

        def visit_Attribute(self, node: ast.Attribute):
            self.visit(node.value)

        def visit_Call(self, node: ast.Call):
            self.visit(node.func)
            for arg in node.args:
                self.visit(arg)
            for kw in node.keywords:
                self.visit(kw.value)

    Validator().visit(tree)
    return tree


def safe_eval_packet(expr: str):
    tree = _validate_ast_for_scapy(expr)
    env = {name: getattr(scapy_all, name) for name in dir(scapy_all)}
    return eval(compile(tree, filename="<packet>", mode="eval"), {"__builtins__": {}}, env)


class SubPlugin(TdcPlugin):
    def __init__(self):
        self.sub_class = 'scapy/SubPlugin'
        super().__init__()

    def post_execute(self):
        if 'scapy' not in self.args.caseinfo:
            if self.args.verbose:
                print('{}.post_execute: no scapy info in test case'.format(self.sub_class))
            return

        # Check for required fields
        lscapyinfo = self.args.caseinfo['scapy']
        if type(lscapyinfo) != list:
            lscapyinfo = [ lscapyinfo, ]

        for scapyinfo in lscapyinfo:
            scapy_keys = ['iface', 'count', 'packet']
            missing_keys = []
            keyfail = False
            for k in scapy_keys:
                if k not in scapyinfo:
                    keyfail = True
                    missing_keys.append(k)
            if keyfail:
                print('{}: Scapy block present in the test, but is missing info:'
                    .format(self.sub_class))
                print('{}'.format(missing_keys))

            pkt = safe_eval_packet(scapyinfo['packet'])
            if '$' in scapyinfo['iface']:
                tpl = Template(scapyinfo['iface'])
                scapyinfo['iface'] = tpl.safe_substitute(NAMES)
            for count in range(scapyinfo['count']):
                sendp(pkt, iface=scapyinfo['iface'])
