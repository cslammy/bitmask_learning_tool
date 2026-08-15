# Bitwise Tutor

An interactive tutor for C/C++ bitwise operations on a single byte (`uint8_t`).
You type real C statements; it parses them, evaluates them with 8-bit semantics,
and draws the bit-by-bit work. All input and output is hex and binary.

No dependencies. Python 3.8+. Carries [PEP 723](https://peps.python.org/pep-0723/)
inline script metadata, so uv manages the interpreter for you.

```bash
uv run bitwise.py
```

Force script mode (ignores any surrounding uv project):

```bash
uv run --script bitwise.py
```

Pin an interpreter — uv downloads it if you don't have it:

```bash
uv run --python 3.12 bitwise.py
```

Plain Python works too, since there is nothing to install:

```bash
python bitwise.py
```

## What it looks like

```
>>> uint8_t commandByte = reg | (value & WIPER_VALUE_MASK);

  &  AND        (1 only where BOTH bits are 1)
                              7654_3210  <- bit number
     value            0xC5  0b1100_0101
   & WIPER_VALUE_MASK 0x7F  0b0111_1111
    -----------------------------------
   = result           0x45  0b0100_0101
                              ^          bits changed

  |  OR         (1 where EITHER bit is 1)
                                        7654_3210  <- bit number
     reg                        0x80  0b1000_0000
   | (value & WIPER_VALUE_MASK) 0x45  0b0100_0101
    ---------------------------------------------
   = result                     0xC5  0b1100_0101
                                         ^    ^ ^  bits changed

  commandByte = 0xC5   0b1100_0101   (decimal 197, set bits: 7, 6, 2, 0)
```

## Commands

| Command | What it does |
| --- | --- |
| *a C statement* | evaluate it and show the bit diagram |
| `ops` | operator cheat sheet — `& \| ^ ~ << >>` and the compound forms |
| `idioms` | the standard set/clear/toggle/test/field recipes |
| `lesson` | list the 7 guided lessons; `lesson 3` runs one, `lesson all` runs them all |
| `quiz [n]` | n practice questions (default 5); answer in hex, binary, or decimal |
| `vars` | show every variable you've declared |
| `table 0x4A` | one byte broken down every way |
| `del x` / `reset` | forget one or all variables |
| `quiet` / `verbose` | hide or show the step-by-step diagrams |
| `help`, `quit` | |

## What it accepts

- Declarations — `uint8_t reg = 0x40;` (also `int8_t`, `uint16_t`, `uint32_t`, `int`, `char`)
- Assignment and compound assignment — `reg = ...`, `|=`, `&=`, `^=`, `<<=`, `>>=`, `+=`, `-=`
- All the bitwise, arithmetic, comparison, and logical operators, with correct C precedence
- Casts — `(uint8_t)~reg`
- `#define NAME value` — including `(1u << 3)` style
- `if (...)` / `while (...)` — evaluates the condition and tells you whether the body runs
- Numbers as `0x1F`, `0b0001_1111`, `037` (octal), `31`, `'A'`, with `u`/`U`/`l`/`L` suffixes
- Built-in macros — `BIT(n)`, `_BV(n)`, `MASK(n)`, `LOW_NIBBLE(v)`, `HIGH_NIBBLE(v)`

Pre-defined on startup: `uint8_t reg`, `uint8_t value`, `WIPER_VALUE_MASK = 0x7F`.

## Things it will warn you about

- `~=` — not an operator in C; it shows you `x = ~x;` and `x ^= 0xFF;` instead
- `&&` / `||` used where you meant `&` / `|`
- `reg & MASK == 0` — `==` binds tighter than `&`, so this isn't what you meant
- Integer promotion — `~a` on a `uint8_t` is computed as an `int`, so it's `0xFFFFFFF0`,
  not `0xF0`, until you store or cast it back to 8 bits
- Bits shifted off the top of a byte, and shifts of 8 or more (undefined behaviour)
- Any value truncated by the type it was stored into
