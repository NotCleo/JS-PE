# Read-before-write lint (sw/jspe_lint.py): state the RTL leaves unreset must be written (or .init-written) first.
import pytest
from harness import assemble_text
from jspe_iss import Chip
from jspe_lint import Lint, LintError

HEAD = """.engine 0
.init
%s
.entry go
.code
go:
%s
        halt
"""


def run(init, code, mode="error", n=300):
    img = assemble_text(HEAD % (init, code))
    c = Chip()
    c.lint = Lint(mode)
    c.boot(img)
    for _ in range(n):
        c.step()
    return c


@pytest.mark.parametrize("init,code", [
    ("", "        add   R1, #1"),                                  # R1 never written
    ("        li    R0, #1", "        xor   C, C"),                # C (the TXJK case before D202's .init write)
    ("", "        cmp   S, #0xD2"),                                # S (the usb_ls ACK test before .init li S)
    ("", "        tsync"),                                         # P
    ("", "        ret"),                                           # L
    ("        lih   R2, #1", "        mov   R0, R2"),              # lih writes the high byte only
    ("", "        mov   R0, T"),                                   # T before any timer start
    ("", "        rcfg  R0, LFSR_LO\n        push  R0"),           # LFSR
])
def test_lint_catches(init, code):
    with pytest.raises(LintError):
        run(init, code)


@pytest.mark.parametrize("init,code", [
    ("        li    R1, #0", "        add   R1, #1"),
    ("        li    R0, #3\n        mov   C, R0", "        xor   C, C"),
    ("", "        loop { shin }\n        cmp   S, #0x80"),          # a full word shifted in: S defined (8-bit word)
    ("        li    R0, #9\n        mov   P, R0", "        tsync\n        mov   R1, T"),
    ("", "        call  f\n        jmp   d\nf:      ret\nd:      nop"),
    ("        li    R0, #0\n        wcfg  LFSR_LO, R0", "        rcfg  R1, LFSR_LO\n        push  R1"),
])
def test_lint_clean(init, code):
    run(init, code)


def test_lint_warn_once():
    with pytest.warns(UserWarning, match="reads R1"):
        c = run("", "l:      add   R1, #1\n        jmp   l", mode="warn")
    assert len(c.lint.sites) == 1
