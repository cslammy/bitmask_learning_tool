#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.8"
# dependencies = []
# ///
"""
bitwise.py - An interactive tutor for C/C++ bitwise operations on uint8_t.

Type real C/C++ statements at the prompt; the tool parses them, evaluates them
with 8-bit (uint8_t) semantics, and draws the bit-by-bit work so you can see
exactly what every operator did.

    >>> uint8_t reg = 0x40;
    >>> uint8_t commandByte = reg | (value & WIPER_VALUE_MASK);

Everything in and out is shown as hex (0x4A) and binary (0b0100_1010).

Run:  uv run bitwise.py        (or: python bitwise.py - there are no dependencies)
"""

import random
import re
import sys

# --------------------------------------------------------------------------
# Terminal colour (no third-party deps)
# --------------------------------------------------------------------------

def _enable_vt():
    """Turn on ANSI escape processing on legacy Windows consoles."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not k.GetConsoleMode(h, ctypes.byref(mode)):
            return False
        k.SetConsoleMode(h, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        return True
    except Exception:
        return False


COLOR = _enable_vt()


def c(text, code):
    return f"\033[{code}m{text}\033[0m" if COLOR else str(text)


def dim(t):    return c(t, "90")
def bold(t):   return c(t, "1")
def red(t):    return c(t, "91")
def green(t):  return c(t, "92")
def yellow(t): return c(t, "93")
def blue(t):   return c(t, "96")
def mag(t):    return c(t, "95")


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------

MASK8 = 0xFF


def fmt_hex(v, width=2):
    """0x4A - always shown as the low `width` nibbles, like a register dump."""
    return f"0x{v & ((1 << (4 * width)) - 1):0{width}X}"


def fmt_bin(v, bits=8):
    """0b0100_1010 (nibble-grouped)."""
    s = format(v & ((1 << bits) - 1), f"0{bits}b")
    groups = [s[i:i + 4] for i in range(0, len(s), 4)]
    return "0b" + "_".join(groups)


def bit_ruler(bits=8):
    """Ruler that lines up under fmt_bin(): '  7654_3210'."""
    idx = "".join(str(i % 10) for i in range(bits - 1, -1, -1))
    groups = [idx[i:i + 4] for i in range(0, len(idx), 4)]
    return "  " + "_".join(groups)


def parse_literal(text):
    """Parse 0x.., 0b.., 0.., decimal, or 'c'.  Underscores allowed."""
    t = text.strip().replace("_", "")
    if not t:
        raise ValueError("empty number")
    if len(t) >= 3 and t[0] == "'" and t[-1] == "'":
        return ord(t[1:-1])
    t = re.sub(r"[uUlL]+$", "", t)
    neg = t.startswith("-")
    if neg:
        t = t[1:]
    low = t.lower()
    if low.startswith("0x"):
        v = int(t[2:], 16)
    elif low.startswith("0b"):
        v = int(t[2:], 2)
    elif low.startswith("0o"):
        v = int(t[2:], 8)
    elif len(t) > 1 and t.startswith("0") and t.isdigit():
        v = int(t, 8)  # C octal
    else:
        v = int(t, 10)
    return -v if neg else v


# --------------------------------------------------------------------------
# Lexer
# --------------------------------------------------------------------------

KEYWORD_TYPES = {
    "uint8_t": 8, "int8_t": 8, "unsigned char": 8, "char": 8, "byte": 8,
    "uint16_t": 16, "int16_t": 16, "uint32_t": 32, "int32_t": 32,
    "int": 32, "unsigned": 32, "unsigned int": 32,
}

OPERATORS = [
    "<<=", ">>=",
    "&&", "||", "==", "!=", "<=", ">=", "<<", ">>",
    "&=", "|=", "^=", "+=", "-=", "*=", "/=", "%=",
    "&", "|", "^", "~", "!", "+", "-", "*", "/", "%",
    "<", ">", "=", "(", ")", ",", ";", "?", ":",
]

TOKEN_RE = re.compile(
    r"""\s*(?:
        (?P<num>0[xX][0-9a-fA-F_]+[uUlL]*|0[bB][01_]+[uUlL]*|'\\?.'|\d[\d_]*[uUlL]*)
      | (?P<ident>[A-Za-z_]\w*)
      | (?P<op>%s)
    )""" % "|".join(re.escape(o) for o in OPERATORS),
    re.VERBOSE,
)


class Tok:
    def __init__(self, kind, text, pos):
        self.kind, self.text, self.pos = kind, text, pos

    def __repr__(self):
        return f"{self.kind}:{self.text}"


def lex(src):
    toks, i = [], 0
    while i < len(src):
        if src[i].isspace():
            i += 1
            continue
        m = TOKEN_RE.match(src, i)
        if not m or m.end() == i:
            raise SyntaxError(f"unexpected character {src[i]!r} at column {i + 1}")
        kind = m.lastgroup
        toks.append(Tok(kind, m.group(kind), m.start(kind)))
        i = m.end()
    toks.append(Tok("eof", "", len(src)))
    return toks


# --------------------------------------------------------------------------
# AST  (plain tuples: (kind, ...))
# --------------------------------------------------------------------------
#   ('num',  value, source_text)
#   ('var',  name)
#   ('un',   op, operand)
#   ('bin',  op, left, right)
#   ('cast', bits, typename, expr)
#   ('call', name, [args])
#   ('assign', op, name, expr)          op in {'=','|=','&=','^=','<<=',...}
#   ('decl', bits, typename, name, expr_or_None)
#   ('cond', test, a, b)

ASSIGN_OPS = {"=", "|=", "&=", "^=", "<<=", ">>=", "+=", "-=", "*=", "/=", "%="}


class Parser:
    def __init__(self, toks):
        self.toks, self.i = toks, 0

    # -- helpers ----------------------------------------------------------
    def peek(self, k=0):
        return self.toks[min(self.i + k, len(self.toks) - 1)]

    def at(self, text):
        return self.peek().text == text

    def eat(self, text=None):
        t = self.peek()
        if text is not None and t.text != text:
            raise SyntaxError(f"expected '{text}' but found '{t.text or 'end of line'}'")
        self.i += 1
        return t

    def try_type(self):
        """Match a (possibly two-word) type name; return (bits, name) or None."""
        t = self.peek()
        if t.kind != "ident":
            return None
        two = f"{t.text} {self.peek(1).text}"
        if two in KEYWORD_TYPES:
            self.i += 2
            return KEYWORD_TYPES[two], two
        if t.text in KEYWORD_TYPES:
            self.i += 1
            return KEYWORD_TYPES[t.text], t.text
        return None

    # -- grammar ----------------------------------------------------------
    def parse_statement(self):
        save = self.i
        ty = self.try_type()
        if ty and self.peek().kind == "ident":
            bits, name = ty
            var = self.eat().text
            init = None
            if self.at("="):
                self.eat("=")
                init = self.parse_expr()
            self.opt_semi()
            return ("decl", bits, name, var, init)
        self.i = save
        node = self.parse_expr()
        self.opt_semi()
        return node

    def opt_semi(self):
        if self.at(";"):
            self.eat(";")
        if self.peek().kind != "eof":
            raise SyntaxError(f"trailing input near '{self.peek().text}'")

    def parse_expr(self):
        return self.parse_assign()

    def parse_assign(self):
        # assignment is right-associative and needs an lvalue on the left
        if self.peek().kind == "ident" and self.peek(1).text in ASSIGN_OPS:
            name = self.eat().text
            op = self.eat().text
            return ("assign", op, name, self.parse_assign())
        return self.parse_cond()

    def parse_cond(self):
        test = self.parse_binary(0)
        if self.at("?"):
            self.eat("?")
            a = self.parse_assign()
            self.eat(":")
            b = self.parse_cond()
            return ("cond", test, a, b)
        return test

    # precedence table, lowest first (C order)
    LEVELS = [
        ["||"], ["&&"], ["|"], ["^"], ["&"],
        ["==", "!="], ["<", "<=", ">", ">="],
        ["<<", ">>"], ["+", "-"], ["*", "/", "%"],
    ]

    def parse_binary(self, lvl):
        if lvl >= len(self.LEVELS):
            return self.parse_unary()
        node = self.parse_binary(lvl + 1)
        while self.peek().kind == "op" and self.peek().text in self.LEVELS[lvl]:
            op = self.eat().text
            rhs = self.parse_binary(lvl + 1)
            node = ("bin", op, node, rhs)
        return node

    def parse_unary(self):
        t = self.peek()
        if t.kind == "op" and t.text in ("~", "!", "-", "+"):
            self.eat()
            return ("un", t.text, self.parse_unary())
        if t.text == "(":
            # cast?  "(uint8_t) expr"
            save = self.i
            self.eat("(")
            ty = self.try_type()
            if ty and self.at(")"):
                self.eat(")")
                bits, name = ty
                return ("cast", bits, name, self.parse_unary())
            self.i = save
        return self.parse_primary()

    def parse_primary(self):
        t = self.peek()
        if t.text == "(":
            self.eat("(")
            node = self.parse_expr()
            self.eat(")")
            return node
        if t.kind == "num":
            self.eat()
            return ("num", parse_literal(t.text), t.text)
        if t.kind == "ident":
            self.eat()
            if self.at("("):
                self.eat("(")
                args = []
                if not self.at(")"):
                    args.append(self.parse_expr())
                    while self.at(","):
                        self.eat(",")
                        args.append(self.parse_expr())
                self.eat(")")
                return ("call", t.text, args)
            return ("var", t.text)
        raise SyntaxError(f"unexpected '{t.text or 'end of line'}'")


def parse(src):
    return Parser(lex(src)).parse_statement()


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------

class Var:
    def __init__(self, name, bits, typename, value):
        self.name, self.bits, self.typename = name, bits, typename
        self.value = value & ((1 << bits) - 1)


class Step:
    """One recorded operation, for the trace display."""
    def __init__(self, kind, op, rows, result, note=None, bits=8):
        self.kind, self.op, self.rows = kind, op, rows   # rows: [(label, value)]
        self.result, self.note, self.bits = result, note, bits


BUILTINS = {
    "BIT":       (1, lambda n: 1 << n),
    "_BV":       (1, lambda n: 1 << n),
    "MASK":      (1, lambda n: (1 << n) - 1),
    "LOW_NIBBLE":(1, lambda v: v & 0x0F),
    "HIGH_NIBBLE":(1, lambda v: (v >> 4) & 0x0F),
}


class Machine:
    def __init__(self):
        self.vars = {}
        self.steps = []

    # -- variable helpers -------------------------------------------------
    def define(self, name, bits, typename, value):
        self.vars[name] = Var(name, bits, typename, value)
        return self.vars[name]

    def get(self, name):
        if name not in self.vars:
            raise NameError(
                f"'{name}' is not defined. Declare it first, e.g. "
                f"uint8_t {name} = 0x00;   (type 'vars' to list)")
        return self.vars[name]

    # -- evaluation -------------------------------------------------------
    def eval(self, node, top=True):
        self.steps = [] if top else self.steps
        return self._ev(node)

    def _ev(self, n):
        k = n[0]

        if k == "num":
            return n[1]

        if k == "var":
            return self.get(n[1]).value

        if k == "cast":
            _, bits, name, sub = n
            v = self._ev(sub)
            r = v & ((1 << bits) - 1)
            if r != v:
                self.steps.append(Step(
                    "cast", f"({name})", [(expr_text(sub), v)], r,
                    note=f"cast to {name} keeps only the low {bits} bits", bits=bits))
            return r

        if k == "call":
            name, args = n[1], n[2]
            if name not in BUILTINS:
                raise NameError(f"unknown function '{name}()'. Known: "
                                + ", ".join(sorted(BUILTINS)))
            arity, fn = BUILTINS[name]
            if len(args) != arity:
                raise TypeError(f"{name}() takes {arity} argument(s)")
            vals = [self._ev(a) for a in args]
            r = fn(*vals)
            self.steps.append(Step(
                "call", name, [(expr_text(a), v) for a, v in zip(args, vals)], r,
                note=f"{name}({', '.join(fmt_hex(v) for v in vals)}) -> {fmt_hex(r)}"))
            return r

        if k == "un":
            op, sub = n[1], n[2]
            v = self._ev(sub)
            note = None
            if op == "~":
                r = ~v
                note = ("in C, ~ on a uint8_t promotes to int first, so the true "
                        f"result is {fmt_hex(r & 0xFFFFFFFF, 8)}; it becomes "
                        f"{fmt_hex(r & 0xFF)} only when stored back into a uint8_t")
                r_disp = r & MASK8
                self.steps.append(Step("un", "~", [(expr_text(sub), v)], r_disp, note))
                return r
            if op == "!":
                r = 0 if v else 1
                self.steps.append(Step("un", "!", [(expr_text(sub), v)], r,
                                       "logical NOT: zero -> 1, non-zero -> 0"))
                return r
            if op == "-":
                r = -v
                self.steps.append(Step("un", "-", [(expr_text(sub), v)], r & MASK8,
                                       "two's complement of the low 8 bits"))
                return r
            return v  # unary +

        if k == "bin":
            op, ln, rn = n[1], n[2], n[3]
            a = self._ev(ln)
            b = self._ev(rn)
            r = apply_bin(op, a, b)
            note = None
            if op == "<<" and (a << b) > MASK8:
                note = (f"shifting left dropped bits off the top once stored in 8 "
                        f"bits: full result {fmt_hex(a << b, 4)} -> {fmt_hex(r & 0xFF)}")
            if op in ("<<", ">>") and b > 7:
                note = f"shifting a uint8_t by {b} is undefined behaviour in C (>= width)"
            self.steps.append(Step(
                "bin", op, [(expr_text(ln), a), (expr_text(rn), b)], r, note))
            return r

        if k == "cond":
            t = self._ev(n[1])
            return self._ev(n[2]) if t else self._ev(n[3])

        if k == "assign":
            op, name, sub = n[1], n[2], n[3]
            var = self.get(name)
            rhs = self._ev(sub)
            if op == "=":
                new = rhs
            else:
                new = apply_bin(op[:-1], var.value, rhs)
                self.steps.append(Step(
                    "bin", op[:-1], [(name, var.value), (expr_text(sub), rhs)], new,
                    note=f"{name} {op} X  is shorthand for  {name} = {name} {op[:-1]} X"))
            old = var.value
            var.value = new & ((1 << var.bits) - 1)
            self.steps.append(Step(
                "store", "=", [(f"{name} (was)", old)], var.value,
                note=(f"stored into {var.typename} {name}"
                      + (f"; value truncated from {fmt_hex(new & 0xFFFFFFFF, 8)}"
                         if var.value != new else "")),
                bits=var.bits))
            return var.value

        if k == "decl":
            _, bits, tyname, name, init = n
            v = self._ev(init) if init is not None else 0
            var = self.define(name, bits, tyname, v)
            # only worth a diagram if the declared type had to throw bits away
            if var.value != v:
                self.steps.append(Step(
                    "store", "=", [(f"{tyname} {name}", v)], var.value,
                    note=(f"{tyname} holds only {bits} bits, so the value was "
                          f"truncated from {fmt_hex(v & 0xFFFFFFFF, 8)}"),
                    bits=bits))
            return var.value

        raise RuntimeError(f"cannot evaluate node {k}")


def apply_bin(op, a, b):
    if op == "&":  return a & b
    if op == "|":  return a | b
    if op == "^":  return a ^ b
    if op == "<<": return a << b if 0 <= b < 64 else 0
    if op == ">>": return a >> b if 0 <= b < 64 else 0
    if op == "+":  return a + b
    if op == "-":  return a - b
    if op == "*":  return a * b
    if op == "/":
        if b == 0: raise ZeroDivisionError("division by zero")
        return a // b
    if op == "%":
        if b == 0: raise ZeroDivisionError("modulo by zero")
        return a % b
    if op == "==": return int(a == b)
    if op == "!=": return int(a != b)
    if op == "<":  return int(a < b)
    if op == "<=": return int(a <= b)
    if op == ">":  return int(a > b)
    if op == ">=": return int(a >= b)
    if op == "&&": return int(bool(a) and bool(b))
    if op == "||": return int(bool(a) or bool(b))
    raise RuntimeError(f"unknown operator {op}")


def expr_text(n):
    """Re-render an AST node as compact C source (used for trace labels)."""
    k = n[0]
    if k == "num":  return n[2]
    if k == "var":  return n[1]
    if k == "un":   return f"{n[1]}{expr_text(n[2])}"
    if k == "cast": return f"({n[2]}){expr_text(n[3])}"
    if k == "call": return f"{n[1]}({', '.join(expr_text(a) for a in n[2])})"
    if k == "bin":  return f"({expr_text(n[2])} {n[1]} {expr_text(n[3])})"
    if k == "cond": return f"({expr_text(n[1])} ? {expr_text(n[2])} : {expr_text(n[3])})"
    if k == "assign": return f"{n[2]} {n[1]} {expr_text(n[3])}"
    if k == "decl": return f"{n[2]} {n[3]}" + (f" = {expr_text(n[4])}" if n[4] else "")
    return "?"


# --------------------------------------------------------------------------
# Trace rendering
# --------------------------------------------------------------------------

OP_NAMES = {
    "&": "AND        (1 only where BOTH bits are 1)",
    "|": "OR         (1 where EITHER bit is 1)",
    "^": "XOR        (1 where the bits DIFFER)",
    "~": "NOT        (flip every bit)",
    "<<": "SHIFT LEFT (bits move toward bit 7; zeros come in at the right)",
    ">>": "SHIFT RIGHT(bits move toward bit 0; zeros come in at the left)",
    "!": "logical NOT",
    "==": "EQUAL      (yields 1 or 0 - NOT a bit pattern)",
    "!=": "NOT EQUAL  (yields 1 or 0 - NOT a bit pattern)",
    "<":  "LESS THAN  (yields 1 or 0)",
    ">":  "GREATER    (yields 1 or 0)",
    "<=": "LESS/EQUAL (yields 1 or 0)",
    ">=": "GREATER/EQ (yields 1 or 0)",
    "&&": "logical AND(yields 1 or 0 - did you mean the bitwise & ?)",
    "||": "logical OR (yields 1 or 0 - did you mean the bitwise | ?)",
    "+":  "ADD",
    "-":  "SUBTRACT",
    "*":  "MULTIPLY",
    "/":  "DIVIDE",
    "%":  "REMAINDER",
}


MAXLABEL = 28


def render_step(step, indent=0):
    """Draw one operation as an aligned column of bytes.

    Layout, all widths fixed so the ruler and the change-markers line up:
        <indent><sym ><label       ><hex  ><0bnnnn_nnnn>
    """
    pad = " " * indent
    bits = step.bits
    hw = bits // 4 + 2                                   # width of '0xNN'
    labels = [r[0][:MAXLABEL] for r in step.rows] + ["result"]
    w = max(len(l) for l in labels)
    prefix = indent + 2 + 1 + w + 1 + hw + 2             # up to the '0b'
    barlen = 1 + (w + 1) + (hw + 2) + 2 + bits + (bits // 4 - 1)
    shifty = step.kind == "bin" and step.op in ("<<", ">>")

    title = {
        "bin":  f"{step.op}  {OP_NAMES.get(step.op, '')}",
        "un":   f"{step.op}  {OP_NAMES.get(step.op, '')}",
        "cast": f"{step.op} cast",
        "call": f"{step.op}() macro",
    }.get(step.kind, "= store into the variable")

    lines = [pad + mag(title),
             pad + " " * (prefix - indent) + dim(bit_ruler(bits)) + dim("  <- bit number")]

    for i, (label, val) in enumerate(step.rows):
        sym = " " if i == 0 else (step.op if step.kind == "bin" else " ")
        row = pad + f"{sym:>2} " + f"{label[:MAXLABEL]:<{w}} "
        if shifty and i == 1:
            lines.append(row + f"{'':<{hw}}  " + dim(f"shift by {val} bit"
                                                     + ("s" if val != 1 else "")))
        else:
            lines.append(row + blue(f"{fmt_hex(val, bits // 4):<{hw}}") + "  "
                         + colored_bits(val, bits))
    lines.append(pad + "  " + dim("-" * barlen))
    lines.append(pad + f"{'=':>2} " + f"{'result':<{w}} "
                 + green(f"{fmt_hex(step.result, bits // 4):<{hw}}") + "  "
                 + colored_bits(step.result, bits, highlight=True))

    # which bits changed relative to the first operand?
    if step.kind in ("bin", "un", "store") and step.rows:
        diff = (step.rows[0][1] ^ step.result) & ((1 << bits) - 1)
        if diff:
            marks = "".join("^" if (diff >> i) & 1 else " "
                            for i in range(bits - 1, -1, -1))
            grouped = " ".join(marks[i:i + 4] for i in range(0, bits, 4))
            lines.append(" " * (prefix + 2) + yellow(grouped) + dim("  bits changed"))
    if step.note:
        lines.append(pad + "   " + dim("note: " + step.note))
    return "\n".join(lines)


def colored_bits(v, bits=8, highlight=False):
    s = format(v & ((1 << bits) - 1), f"0{bits}b")
    out = []
    for i, ch in enumerate(s):
        if i and i % 4 == 0:
            out.append(dim("_"))
        out.append(green(ch) if ch == "1" and highlight else
                   (blue(ch) if ch == "1" else dim(ch)))
    return dim("0b") + "".join(out)


def render_result(value, bits=8, name=None):
    m = (1 << bits) - 1
    v = value & m
    head = f"{name} = " if name else "result = "
    print()
    print("  " + bold(head) + green(fmt_hex(v, max(2, bits // 4)))
          + "   " + green(fmt_bin(v, bits))
          + dim(f"   (decimal {v}") + dim(f", set bits: {set_bit_list(v, bits)})"))
    if value != v:
        print("  " + yellow(f"[!] full untruncated value was {fmt_hex(value & 0xFFFFFFFF, 8)}"
                            f" - only the low {bits} bits fit"))


def set_bit_list(v, bits=8):
    s = [str(i) for i in range(bits) if (v >> i) & 1]
    return ", ".join(reversed(s)) if s else "none"


# --------------------------------------------------------------------------
# Reference text
# --------------------------------------------------------------------------

OPS_TABLE = """
  BITWISE OPERATORS IN C / C++            (uint8_t a, b;  int n;)

  a & b     AND      result bit = 1 only if BOTH input bits are 1
                     -> use to CLEAR bits / MASK OFF bits / TEST bits
  a | b     OR       result bit = 1 if EITHER input bit is 1
                     -> use to SET bits / MERGE fields together
  a ^ b     XOR      result bit = 1 if the input bits DIFFER
                     -> use to TOGGLE bits / find differences
  ~a        NOT      flips every bit (a one's complement)
                     -> use to build "everything except" masks
  a << n    LSHIFT   move bits left n places, zeros fill in from the right
                     -> a << 1 doubles the value; used to build masks
  a >> n    RSHIFT   move bits right n places (zeros fill in for unsigned)
                     -> a >> 1 halves the value; used to extract fields

  COMPOUND ASSIGNMENT  --  "do it and store it back"

  a |= b    is exactly   a = a | b      set the bits that are 1 in b
  a &= b    is exactly   a = a & b      keep only the bits that are 1 in b
  a ^= b    is exactly   a = a ^ b      toggle the bits that are 1 in b
  a <<= n   is exactly   a = a << n
  a >>= n   is exactly   a = a >> n

  There is NO  ~=  operator in C or C++.  To invert in place you write:
      a = ~a;          /* flips all 8 bits */
      a ^= 0xFF;       /* same thing, and the idiom you'll see in drivers */

  DO NOT CONFUSE  (this is the #1 beginner bug)

  &   bitwise AND, works on all bits      |   bitwise OR, works on all bits
  &&  logical AND, yields only 0 or 1     ||  logical OR, yields only 0 or 1

      if (flags & 0x04)    tests bit 2       <-- what you almost always want
      if (flags && 0x04)   tests "flags is non-zero"   <-- almost always a bug

  PRECEDENCE TRAP

  &, ^ and | bind LOOSER than  ==  and  !=  in C.  So:
      if (reg & MASK == 0)      parses as   reg & (MASK == 0)     WRONG
      if ((reg & MASK) == 0)    what you meant                    RIGHT
  When in doubt, parenthesise.  Shifts also bind looser than + and - :
      1 << n + 1     is   1 << (n + 1)    not   (1 << n) + 1
"""

IDIOMS = """
  THE STANDARD BIT IDIOMS   (reg is a uint8_t, n is a bit number 0..7)

  set a bit          reg |=  (1 << n);          reg |= 0x04;
  clear a bit        reg &= ~(1 << n);          reg &= ~0x04;
  toggle a bit       reg ^=  (1 << n);          reg ^= 0x04;
  test a bit         if (reg & (1 << n)) { }    non-zero means "set"
  test bit is clear  if (!(reg & (1 << n))) { }
  read bit as 0/1    uint8_t b = (reg >> n) & 1;

  build a mask       #define BIT(n)        (1u << (n))
                     #define WIPER_MASK    0x7F      /* bits 6..0 */
                     low n bits:  ((1u << n) - 1)    /* n=4 -> 0x0F */

  clear a field      reg &= ~WIPER_MASK;
  insert a field     reg = (reg & ~WIPER_MASK) | (value & WIPER_MASK);
  extract a field    uint8_t v = (reg & WIPER_MASK) >> WIPER_SHIFT;

  combine cmd+data   uint8_t commandByte = reg | (value & WIPER_VALUE_MASK);
                     /* the & guards against 'value' spilling into the
                        command bits; the | merges the two fields.       */

  swap nibbles       reg = (uint8_t)((reg << 4) | (reg >> 4));
  split a 16-bit     uint8_t hi = (uint8_t)(word >> 8);
                     uint8_t lo = (uint8_t)(word & 0x00FF);
  rejoin             uint16_t word = ((uint16_t)hi << 8) | lo;

  WHY THE CAST MATTERS

  uint8_t a = 0x0F;
  uint8_t b = ~a;             /* ~a is int 0xFFFFFFF0; b gets 0xF0  */
  if (~a == 0xF0)             /* FALSE! compares int 0xFFFFFFF0     */
  if ((uint8_t)~a == 0xF0)    /* TRUE                               */
"""

HELP = """
  COMMANDS

  <C statement>     evaluate it and show the bit-by-bit work, e.g.
                      uint8_t reg = 0x40;
                      uint8_t commandByte = reg | (value & 0x7F);
                      reg |= (1 << 3);
                      reg &= ~0x0C;
                      (uint8_t)~reg
  ops               the operator cheat sheet (&, |, ^, ~, <<, >>, |=, ...)
  idioms            the standard set/clear/toggle/test/field recipes
  lesson            guided lessons, run one at a time
  quiz [n]          n practice questions (default 5)
  vars              show every variable you've declared
  del <name>        delete a variable
  reset             forget all variables
  quiet / verbose   hide or show the step-by-step bit diagrams
  table <value>     show one byte in hex, binary, decimal, and set-bit list
  help              this text
  quit              exit

  Built-in macros you can use:  BIT(n), _BV(n), MASK(n), LOW_NIBBLE(v),
  HIGH_NIBBLE(v).   Numbers may be written 0x1F, 0b0001_1111, 037, or 31.
"""


# --------------------------------------------------------------------------
# Lessons
# --------------------------------------------------------------------------

LESSONS = [
    ("AND (&) - clearing and testing bits", """
& keeps a bit only when BOTH bits are 1, so ANDing with a mask keeps
exactly the bits the mask has set and forces the rest to 0.
That is why & is the "keep only these" / "mask off" operator.""",
     ["uint8_t status = 0xB6;",
      "uint8_t LOW_NIBBLE_MASK = 0x0F;",
      "status & LOW_NIBBLE_MASK",
      "status & 0x04"]),

    ("OR (|) - setting bits", """
| makes a bit 1 if EITHER side has it set. Bits already 1 stay 1, so |
never clears anything. That is why | is the "turn these on" operator, and
why merging two fields into one command byte is always done with |.""",
     ["uint8_t reg = 0x40;",
      "reg | 0x0A",
      "reg |= (1 << 0);"]),

    ("XOR (^) - toggling bits", """
^ makes a bit 1 only when the two bits DIFFER. XOR with 1 flips a bit;
XOR with 0 leaves it alone. So x ^ 0xFF inverts all 8 bits, and
x ^ x == 0 always.""",
     ["uint8_t leds = 0b1010_1010;",
      "leds ^ 0x0F",
      "leds ^= 0xFF;",
      "leds ^ leds"]),

    ("NOT (~) - building inverse masks", """
~ flips every bit. Its main job is turning a "these bits" mask into an
"everything except these bits" mask, which is how you clear bits:
    reg &= ~MASK;
Watch the note: in C, ~ on a uint8_t is computed in int width first.""",
     ["uint8_t WIPER_MASK = 0x7F;",
      "(uint8_t)~WIPER_MASK",
      "uint8_t reg = 0xFF;",
      "reg &= ~0x0C;"]),

    ("Shifts (<< and >>) - moving bits into position", """
<< slides bits toward bit 7 and pulls zeros in on the right; >> slides
them toward bit 0. 1 << n is how you name "bit n". Shifting right by the
field's position is how you read a field back out as a plain number.""",
     ["1 << 3",
      "uint8_t reg = 0b1101_0000;",
      "reg >> 4",
      "(reg >> 4) & 0x03"]),

    ("Compound assignment (|=, &=, ^=, <<=)", """
a |= b is nothing more than a = a | b. The value is written back into the
variable, so the variable's type truncates the result to 8 bits. There is
no ~= operator; write a = ~a; or a ^= 0xFF; instead.""",
     ["uint8_t ctrl = 0x00;",
      "ctrl |= (1 << 7);",
      "ctrl |= (1 << 1);",
      "ctrl &= ~(1 << 7);",
      "ctrl ^= 0xFF;"]),

    ("Fields - the read-modify-write pattern", """
To change some bits and leave the rest alone you clear the field with
&= ~MASK, then OR the new value in - and you AND the new value with the
mask first so a too-large value cannot spill into the neighbouring bits:

    reg = (reg & ~WIPER_MASK) | (value & WIPER_MASK);""",
     ["uint8_t WIPER_VALUE_MASK = 0x7F;",
      "uint8_t reg = 0x80;",
      "uint8_t value = 0xC5;",
      "uint8_t commandByte = reg | (value & WIPER_VALUE_MASK);",
      "commandByte & WIPER_VALUE_MASK"]),
]


def run_lessons(m, args):
    idx = None
    if args:
        try:
            idx = int(args[0]) - 1
        except ValueError:
            pass
    if idx is None:
        print()
        for i, (title, _, _) in enumerate(LESSONS, 1):
            print(f"  {bold(str(i))}. {title}")
        print(dim("\n  Run one with:  lesson 3      (or 'lesson all')"))
        return
    if args and args[0] == "all":
        order = range(len(LESSONS))
    elif 0 <= idx < len(LESSONS):
        order = [idx]
    else:
        print(red(f"  no lesson {idx + 1}"))
        return
    for i in order:
        title, body, examples = LESSONS[i]
        print("\n" + bold("=" * 70))
        print(bold(f"  LESSON {i + 1}: {title}"))
        print(bold("=" * 70))
        print(body.rstrip())
        for ex in examples:
            print("\n" + dim("  " + "-" * 66))
            print("  " + bold(">>> ") + yellow(ex))
            execute(m, ex)
        if len(list(order)) > 1:
            try:
                input(dim("\n  [enter] for the next lesson... "))
            except EOFError:
                return


# --------------------------------------------------------------------------
# Quiz
# --------------------------------------------------------------------------

def rand_byte():
    return random.randint(0, 255)


def make_question():
    kind = random.choice(
        ["and", "or", "xor", "not", "shl", "shr", "setbit", "clearbit",
         "togglebit", "testbit", "field", "orassign", "andassign"])
    a, b, n = rand_byte(), rand_byte(), random.randint(0, 7)

    if kind == "and":
        return f"uint8_t a = {fmt_hex(a)};  uint8_t b = {fmt_hex(b)};   a & b", a & b, \
               "AND keeps a bit only where both operands have a 1."
    if kind == "or":
        return f"uint8_t a = {fmt_hex(a)};  uint8_t b = {fmt_hex(b)};   a | b", a | b, \
               "OR sets a bit wherever either operand has a 1."
    if kind == "xor":
        return f"uint8_t a = {fmt_hex(a)};  uint8_t b = {fmt_hex(b)};   a ^ b", a ^ b, \
               "XOR sets a bit only where the two operands differ."
    if kind == "not":
        return f"uint8_t a = {fmt_hex(a)};   (uint8_t)~a", (~a) & 0xFF, \
               "~ flips all 8 bits (the cast keeps it in 8 bits)."
    if kind == "shl":
        s = random.randint(1, 4)
        return f"uint8_t a = {fmt_hex(a)};   (uint8_t)(a << {s})", (a << s) & 0xFF, \
               f"Bits move {s} place(s) toward bit 7; anything past bit 7 is lost."
    if kind == "shr":
        s = random.randint(1, 4)
        return f"uint8_t a = {fmt_hex(a)};   a >> {s}", a >> s, \
               f"Bits move {s} place(s) toward bit 0; zeros come in on the left."
    if kind == "setbit":
        return f"uint8_t reg = {fmt_hex(a)};   reg |= (1 << {n});   reg", a | (1 << n), \
               f"|= (1 << {n}) turns bit {n} on and leaves everything else alone."
    if kind == "clearbit":
        return f"uint8_t reg = {fmt_hex(a)};   reg &= ~(1 << {n});   reg", a & ~(1 << n) & 0xFF, \
               f"&= ~(1 << {n}) turns bit {n} off and leaves everything else alone."
    if kind == "togglebit":
        return f"uint8_t reg = {fmt_hex(a)};   reg ^= (1 << {n});   reg", a ^ (1 << n), \
               f"^= (1 << {n}) flips bit {n}."
    if kind == "testbit":
        return f"uint8_t reg = {fmt_hex(a)};   (reg >> {n}) & 1", (a >> n) & 1, \
               f"Shift bit {n} down to position 0, then mask off everything else."
    if kind == "field":
        mask = random.choice([0x0F, 0x7F, 0x3F, 0xF0])
        return (f"uint8_t reg = {fmt_hex(a)};  uint8_t value = {fmt_hex(b)};\n"
                f"      uint8_t commandByte = reg | (value & {fmt_hex(mask)});   commandByte"), \
               (a | (b & mask)) & 0xFF, \
               "First & clamps value to the mask, then | merges it into reg."
    if kind == "orassign":
        return f"uint8_t reg = {fmt_hex(a)};   reg |= {fmt_hex(b)};   reg", a | b, \
               "|= is just  reg = reg | value."
    return f"uint8_t reg = {fmt_hex(a)};   reg &= {fmt_hex(b)};   reg", a & b, \
           "&= is just  reg = reg & value."


def run_quiz(count=5):
    print("\n" + bold("=" * 70))
    print(bold(f"  QUIZ - {count} questions"))
    print(dim("  Answer in hex (0x4A), binary (0b0100_1010), or decimal."))
    print(dim("  Type 'skip' to see the answer, 'stop' to end early."))
    print(bold("=" * 70))
    score = 0
    asked = 0
    for i in range(1, count + 1):
        q, ans, why = make_question()
        print(f"\n  {bold(f'Q{i}.')}  {yellow(q)}")
        print("  " + dim("What is the result?"))
        while True:
            try:
                raw = input("  " + bold("your answer > ")).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if raw.lower() in ("stop", "quit", "q"):
                print(dim(f"\n  Stopped. Score {score}/{asked}."))
                return
            asked = i
            if raw.lower() in ("skip", "?"):
                print("  " + yellow(f"answer: {fmt_hex(ans)}  {fmt_bin(ans)}"))
                print("  " + dim(why))
                break
            try:
                got = parse_literal(raw)
            except ValueError:
                print(red("  Not a number I understand. Try 0x0F, 0b0000_1111, or 15."))
                continue
            if got & 0xFF == ans & 0xFF:
                score += 1
                print("  " + green(f"correct!  {fmt_hex(ans)}  {fmt_bin(ans)}"))
                print("  " + dim(why))
            else:
                print("  " + red(f"not quite. You said {fmt_hex(got)} {fmt_bin(got)}"))
                print("  " + yellow(f"answer:        {fmt_hex(ans)}  {fmt_bin(ans)}"))
                print("  " + dim(why))
            break
    print("\n  " + bold(f"Score: {score}/{count}"))
    if score == count:
        print("  " + green("Perfect."))
    elif score >= count * 0.6:
        print("  " + yellow("Solid. Run 'idioms' for the patterns you missed."))
    else:
        print("  " + dim("Try 'lesson all', then quiz again."))


# --------------------------------------------------------------------------
# REPL
# --------------------------------------------------------------------------

VERBOSE = True


def check_common_mistakes(src):
    """Catch the syntax traps beginners hit, and explain them by name."""
    if re.search(r"~\s*=", src) and not re.search(r"[=!<>]\s*~", src):
        name = re.match(r"\s*(\w+)", src)
        v = name.group(1) if name else "reg"
        print("  " + red("there is no  ~=  operator in C or C++."))
        print("  " + dim(f"~ is a unary operator, so it has no compound form. Write:"))
        print("      " + yellow(f"{v} = ~{v};") + dim("      /* invert all 8 bits */"))
        print("      " + yellow(f"{v} ^= 0xFF;") + dim("     /* same result, the usual driver idiom */"))
        return True
    if re.search(r"&&\s*(0x|0b|\d)", src) or re.search(r"\|\|\s*(0x|0b|\d)", src):
        print("  " + yellow("heads up: && and || are LOGICAL operators - they only ever yield 0 or 1."))
        print("  " + dim("  For per-bit work you want the single & or |. Evaluating it as written:"))
    if re.search(r"[&|^][^&|^=]*[=!]=", src) and "(" not in src.split("=")[0]:
        print("  " + yellow("heads up: == binds TIGHTER than & ^ |, so this may not group how you"))
        print("  " + dim("  expect. 'reg & MASK == 0' means 'reg & (MASK == 0)'. Parenthesise:"))
        print("  " + dim("  '(reg & MASK) == 0'. Evaluating it as written:"))
    return False


def execute(m, src):
    """Parse, evaluate and display one C statement."""
    global VERBOSE
    src = src.strip().rstrip("{").strip()
    if check_common_mistakes(src):
        return

    # #define NAME value  ->  treat as a constant you can use by name
    d = re.match(r"#\s*define\s+(\w+)\s+(.+?)\s*(?:/\*.*)?$", src)
    if d:
        name, body = d.group(1), d.group(2)
        try:
            v = m._ev(parse(body))
        except Exception as e:
            print("  " + red(f"cannot evaluate that #define: {e}"))
            return
        bits = 8 if 0 <= v <= 0xFF else 16 if v <= 0xFFFF else 32
        m.define(name, bits, "#define", v)
        print("  " + dim(f"#define {name}  ->  ") + green(fmt_hex(v, bits // 4))
              + "  " + green(fmt_bin(v, bits)))
        return

    # if (cond) / while (cond)  ->  evaluate the condition and say what C does
    w = re.match(r"(if|while)\s*\((.*)\)\s*$", src)
    if w:
        try:
            node = parse(w.group(2))
            m.steps = []
            v = m._ev(node)
        except Exception as e:
            print("  " + red(f"error: {e}"))
            return
        if VERBOSE and m.steps:
            print()
            for st in m.steps:
                print(render_step(st, indent=2))
                print()
        truth = "non-zero -> TRUE, the body runs" if v else "zero -> FALSE, the body is skipped"
        print("  " + bold(f"{w.group(1)} condition = ") + green(fmt_hex(v))
              + "  " + green(fmt_bin(v)) + "   " + yellow(truth))
        return

    if re.match(r"\s*(for|else|switch|return|#)", src):
        print("  " + dim("this tool evaluates expressions, declarations, #define, and"))
        print("  " + dim("if/while conditions - not full control flow. Try just the expression."))
        return

    try:
        node = parse(src)
    except SyntaxError as e:
        print("  " + red(f"syntax error: {e}"))
        return
    except ValueError as e:
        print("  " + red(f"bad number: {e}"))
        return

    try:
        m.steps = []
        value = m._ev(node)
    except (NameError, TypeError, ZeroDivisionError, RuntimeError) as e:
        print("  " + red(f"error: {e}"))
        return

    if VERBOSE and m.steps:
        print()
        for st in m.steps:
            print(render_step(st, indent=2))
            print()

    bits = 8
    name = None
    if node[0] == "decl":
        bits, name = node[1], node[3]
    elif node[0] == "assign":
        v = m.vars.get(node[2])
        if v:
            bits, name = v.bits, v.name
    render_result(value, bits, name)


def show_vars(m):
    if not m.vars:
        print(dim("  no variables yet - try:  uint8_t reg = 0x40;"))
        return
    tw = max(len(v.typename) for v in m.vars.values())
    w = max(len(v.name) for v in m.vars.values())
    # group by width so each group gets a ruler its bits actually line up with
    for bits in sorted({v.bits for v in m.vars.values()}):
        group = [v for v in m.vars.values() if v.bits == bits]
        hw = bits // 4 + 2
        print()
        print("  " + " " * (tw + 1 + w + 1 + hw + 2) + dim(bit_ruler(bits)))
        for v in group:
            print("  " + dim(f"{v.typename:<{tw}}") + " " + bold(f"{v.name:<{w}}") + " "
                  + blue(f"{fmt_hex(v.value, bits // 4):<{hw}}") + "  "
                  + colored_bits(v.value, bits)
                  + dim(f"  ({v.value})"))


def show_table(arg):
    try:
        v = parse_literal(arg)
    except ValueError:
        print(red("  give me a value, e.g.  table 0x4A"))
        return
    print()
    print("  " + " " * 17 + dim(bit_ruler()))
    print("  " + f"{'value':<10} " + blue(fmt_hex(v)) + "  " + colored_bits(v))
    print("  " + f"{'~value':<10} " + blue(fmt_hex(~v & 0xFF)) + "  " + colored_bits(~v & 0xFF))
    print()
    print(dim(f"  decimal      {v & 0xFF}"))
    print(dim(f"  set bits     {set_bit_list(v)}"))
    print(dim(f"  high nibble  {fmt_hex((v >> 4) & 0x0F)}    low nibble  {fmt_hex(v & 0x0F)}"))
    print(dim(f"  as a mask    reg |= {fmt_hex(v & 0xFF)};   "
              f"reg &= ~{fmt_hex(v & 0xFF)};   reg ^= {fmt_hex(v & 0xFF)};"))


BANNER = r"""
   ___  _ _            _            _____     _
  | _ )(_) |___ __ _(_)___ ___  |_   _|  _| |_ ___ _ _
  | _ \| |  _\ V  V / (_-</ -_)   | || || |  _/ _ \ '_|
  |___/|_|\__|\_/\_/|_/__/\___|   |_| \_,_|\__\___/_|
"""


def main():
    global VERBOSE
    m = Machine()
    # a few pre-set constants so the first example works out of the box
    m.define("WIPER_VALUE_MASK", 8, "uint8_t", 0x7F)
    m.define("reg", 8, "uint8_t", 0x00)
    m.define("value", 8, "uint8_t", 0x00)

    print(blue(BANNER))
    print("  " + bold("Learn C/C++ bitwise operations one byte at a time."))
    print(dim("  Type a C statement, or:  help  ops  idioms  lesson  quiz  vars  quit"))
    print(dim("  Pre-defined: uint8_t reg, value, WIPER_VALUE_MASK = 0x7F"))
    print(dim("  Try:  uint8_t commandByte = reg | (value & WIPER_VALUE_MASK);"))

    while True:
        try:
            line = input("\n" + bold(">>> ")).strip()
        except (EOFError, KeyboardInterrupt):
            print(dim("\nbye"))
            return
        if not line:
            continue

        parts = line.split()
        cmd, args = parts[0].lower(), parts[1:]

        if cmd in ("quit", "exit", "q", ":q"):
            print(dim("bye"))
            return
        if cmd in ("help", "?", "h"):
            print(HELP)
            continue
        if cmd in ("ops", "operators", "cheat"):
            print(OPS_TABLE)
            continue
        if cmd in ("idioms", "idiom", "patterns", "recipes"):
            print(IDIOMS)
            continue
        if cmd in ("lesson", "lessons", "learn"):
            run_lessons(m, args)
            continue
        if cmd == "quiz":
            n = 5
            if args:
                try:
                    n = max(1, min(50, int(args[0])))
                except ValueError:
                    pass
            run_quiz(n)
            continue
        if cmd in ("vars", "var", "list"):
            show_vars(m)
            continue
        if cmd == "del" and args:
            for a in args:
                m.vars.pop(a, None)
            print(dim(f"  deleted {', '.join(args)}"))
            continue
        if cmd == "reset":
            m.vars.clear()
            print(dim("  all variables cleared"))
            continue
        if cmd == "quiet":
            VERBOSE = False
            print(dim("  step-by-step diagrams off"))
            continue
        if cmd == "verbose":
            VERBOSE = True
            print(dim("  step-by-step diagrams on"))
            continue
        if cmd == "table" and args:
            show_table(args[0])
            continue

        execute(m, line)


if __name__ == "__main__":
    main()
