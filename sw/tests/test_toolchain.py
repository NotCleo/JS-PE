# A1-A5: isa.yaml generator, encodings, assembler directives and errors, images.
import os, random, struct, subprocess, sys
import pytest
from harness import SW, ROOT, PROG, prog, assemble, assemble_text, decode, AsmError
from jspe_asm import encode, INSTR, ISA, Assembler

WORDS = {"uart_tx": 8, "uart_rx": 8, "spi_ctrl": 18, "spi_tgt": 20, "i2c_ctrl": 41, "i2c_tgt": 32, "ws2812": 9,
         "manchester": 23}


def test_gen_isa_up_to_date():
    r = subprocess.run([sys.executable, os.path.join(SW, "gen_isa.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
    assert "67 instructions, 3416 undefined words, 0 errors" in r.stdout


def test_encode_decode_round_trip():
    rnd = random.Random(1)
    und = ISA["undefined"]
    for name, ins in INSTR.items():
        for _ in range(50):
            f = {k: rnd.randrange(1 << (hi - lo + 1)) for k, (hi, lo) in ins.get("fields", {}).items()}
            w = encode(name, **f)
            i, fl = decode(w)
            if any(w & u["mask"] == u["value"] for u in und):
                assert i is None
            else:
                assert i["name"] == name and fl == f


def test_undefined_classes_trap():
    for w in (0xD870, 0xDA00, 0xDAC0, 0xF0C0, 0xF340, 0xF380, 0xF3C0, 0xFAA8, 0xC080, 0xCD00, 0xFC00, 0xF801):
        assert decode(w)[0] is None, hex(w)


def test_programs_assemble_with_header_word_counts():
    for name, n in WORDS.items():
        assert len(prog(name).words) == n, name


@pytest.mark.parametrize("name,defs", [
    ("uart_tx", {"PARITY": 1}), ("uart_tx", {"DBITS": 9, "PARITY": 2, "STOP2": 1}), ("uart_tx", {"STOP15": 1}),
    ("uart_tx", {"BAUD": 115200}), ("uart_rx", {"PARITY": 2}), ("uart_rx", {"DBITS": 9}),
    ("uart_rx", {"BAUD": 3000000}), ("spi_ctrl", {"CPHA": 1}), ("spi_tgt", {"MODE12": 1}),
    ("i2c_ctrl", {"THD": 0}), ("i2c_tgt", {"THD": 5, "TSU": 60}), ("ws2812", {"THIRD": 20}),
    ("manchester", {"BIT": 22})])
def test_variants_and_deadlines(name, defs):
    img = prog(name, **defs)
    assert all(d["bound"] <= d["n"] for d in img.deadlines)


def test_multi_file_local_symbols_and_defines():
    p = [os.path.join(PROG, f + ".s") for f in ("uart_tx", "uart_rx", "spi_tgt", "i2c_tgt")]
    img = assemble(p, {"uart_tx.s:ENG": "1", "uart_rx.s:ENG": "2", "spi_tgt.s:ENG": "3", "i2c_tgt.s:ENG": "0",
                       "uart_tx.s:ORG": "200", "uart_rx.s:ORG": "220"})
    assert sorted(img.engines) == [0, 1, 2, 3]
    assert img.label("idle", "uart_tx.s") == 200 and img.label("idle", "spi_tgt.s") == 64
    assert img.engines[1]["pads"] == {0: 0 | 1 << 2}


def test_mem_limit_and_overlaps():
    with pytest.raises(AsmError, match="--mem"):
        assemble([os.path.join(PROG, "i2c_tgt.s")], mem=128)
    assemble([os.path.join(PROG, "i2c_tgt.s")], {"ORG": "40"}, mem=128)
    with pytest.raises(AsmError, match="already used"):
        assemble([os.path.join(PROG, "uart_tx.s"), os.path.join(PROG, "uart_rx.s")], {"uart_rx.s:ORG": "4"})
    with pytest.raises(AsmError, match="two owners"):
        assemble_text(".engine 0\n.pad uio1, p0\n.engine 1\n.pad uio1, p2\n")
    with pytest.raises(AsmError, match="second .slots"):
        assemble_text(".slots 0,1,2,3\n.slots 0,0,0,0\n")

def memmap_regions():
    import re
    m = {k: int(v) for k, v in re.findall(r"^\.equ\s+(\w+),\s*(\d+)", open(os.path.join(PROG, "cleo_mem.inc")).read(), re.M)}
    return m, [(m["SWD_BOX"], m["SWD_BOX_N"]), (m["USB_BUF"], m["USB_BUF_N"]), (m["FAULT_W"], m["FAULT_N"]), (m["BR_MBOX"], 1), (m["BR_RING"], m["BR_RING_N"])]


def test_memmap_disjoint_above_code():
    m, regs = memmap_regions()
    words = [a for b, n in regs for a in range(b, b + n)]
    assert len(words) == len(set(words)) and min(words) == m["MEM_DATA"] and max(words) == 511


def test_reserve_and_include_overlap_check():
    inc = '.include "%s"\n' % os.path.join(PROG, "cleo_mem.inc")
    br = [os.path.join(PROG, "bridge_i2c_uart.s"), os.path.join(PROG, "bridge_uart_spi.s")]
    m, _ = memmap_regions()
    with pytest.raises(AsmError, match="reserved word %d already used by bridge_i2c_uart.s" % m["BR_MBOX"]):
        Assembler([(br[0], open(br[0]).read()), ("evil.s", inc + ".engine 2\n.reserve BR_MBOX - 1, 2\n")]).run()
    with pytest.raises(AsmError, match="address %d reserved by evil.s" % m["FAULT_W"]):
        Assembler([("evil.s", inc + ".reserve FAULT_W, FAULT_N\n"), ("code.s", inc + ".org FAULT_W\n.word 0\n")]).run()
    with pytest.raises(AsmError, match="reserved word %d already used by code.s" % m["USB_BUF"]):
        Assembler([("code.s", ".org %d\nnop\n" % m["USB_BUF"]), ("usb.s", inc + ".reserve USB_BUF, USB_BUF_N\n")]).run()
    img = assemble_text(inc + ".reserve FAULT_W, 1\n.org FAULT_W\n.word 7\n")      # own region: may store
    assert img.words[m["FAULT_W"]] == 7
    with pytest.raises(AsmError, match="cannot open nothere.inc"):
        assemble_text('.include "nothere.inc"\n')
    with pytest.raises(AsmError, match=r"cleo_mem.inc:\d+: 'MEM_DATA' defined twice"):
        assemble_text(inc + inc)



@pytest.mark.parametrize("src,msg", [
    ("x: @tick : jz x", "guard"),
    ("x: nop\n.space 70\n jz x", "out of range"),
    (".init\n jz y\n.code\ny: nop", "inside .init"),
    ("p2 <- t", "unencodable"),
    ("p0 <- out, shin", "with shin"),
    ("p0 <- 1, p0 <- 0", "two actions"),
    ("frob R0", "unknown instruction"),
    ("li P, #1", "R0-R2, S only"),
    ("add R0, #200", "outside"),
    ("wcfg SIG, R0", "read-only"),
    (".deadline 3", "must follow"),
    ("loop { li R0, #1 }", "only pin/shift"),
    (".if 1\nnop\n", "missing .endif"),
    (".assert 1 == 2, \"no\"", "assertion failed: no"),
])
def test_errors_have_line_numbers(src, msg):
    with pytest.raises(AsmError, match=msg) as e:
        assemble_text(src, name="t.s")
    assert "t.s" in str(e.value) or "missing .endif" in str(e.value)


def test_encodings_and_relaxation():
    img = assemble_text("""
a:      li    R0, #0x1234
        li    R1, #-1
        loop { @rise p0 : shin }
        loop {
          @tick : p0 <- 0, p2 <- d
          @tock : p0 <- 1, shin
        }
        jmp   far
        jmp   a
        ld    R2, [S+1]!
        st    R1, [R0+S]
        sub   R2, #3
.org 300
far:    @never : nop
""")
    w = [img.words[k] for k in sorted(img.words)]
    assert w[0:2] == [encode("li", rd=0, imm=0x34), encode("lih", rd=0, imm=0x12)]
    assert w[2:4] == [encode("li", rd=1, imm=0xFF), encode("lih", rd=1, imm=0xFF)]
    assert decode(w[4])[1]["rr"] == 1 and decode(w[5])[1]["rr"] == 2 and decode(w[6])[1]["rr"] == 3
    assert decode(w[5])[1]["pa"] == 2 and decode(w[5])[1]["pb"] == 0
    assert decode(w[7])[0]["name"] == "jmpa" and decode(w[8])[0]["name"] == "jmp"
    assert decode(w[9])[1] == {"rd": 2, "rb": 3, "mode": 1} and decode(w[10])[1] == {"rs": 1, "rb": 0, "mode": 2}
    assert w[11] == encode("addi", rd=2, imm=0xFD)


def test_deadline_violation_detected():
    src = open(os.path.join(PROG, "uart_tx.s")).read().replace("tx:     pull  S", "tx:     pull  S\n        nop")
    with pytest.raises(AsmError, match="deadline 10 exceeded: bound 14"):
        assemble_text(src, name="uart_tx.s")
    src = open(os.path.join(PROG, "ws2812.s")).read().replace(".slots", ";")
    img = assemble_text(src + "\n.slots 0, 0, 1, 2\n", name="ws2812.s")
    assert [d["bound"] for d in img.deadlines] == [5, 13]     # 0x90: G 3, chained access K 4 (D054)


def test_images():
    img = prog("spi_tgt")
    hx = img.hex_text().split()
    assert len(hx) == 512 and int(hx[64], 16) == img.words[64]
    b = img.bin_bytes()
    assert b[:4] == b"JSPE" and b[4] == 1
    n = struct.unpack_from("<H", b, 6)[0]
    types, p = [], 8
    for _ in range(n):
        t = b[p]
        types.append(t)
        p += 1 + (4 + 2 * struct.unpack_from("<H", b, p + 3)[0] if t == 1 else {2: 3, 3: 3, 4: 1, 5: 1, 6: 4}[t])
    assert p == len(b)
    assert img.clk is None and 6 not in types                # clocked from outside: no CLK record
    k = types.index(3)
    assert types[:3] == [5, 1, 2] and types[3] == 2 and types[-1] == 4 and types[-2] == 2
    assert types[k:k + len(img.exec_words(0))] == [3] * len(img.exec_words(0))
    assert "next guard armed" in img.lst_text()


def test_clk_record():
    # HOSTIF 7: one CLK record from CLK_HZ; files assembled for different clocks are refused
    a = os.path.join(PROG, "uart_tx.s")
    b = os.path.join(PROG, "uart_rx.s")
    img = assemble([a, b], {"CLK_HZ": "48000000"})
    rec = img.bin_bytes()
    assert img.clk == 48000000 and rec[8] == 6 and struct.unpack_from("<I", rec, 9)[0] == 48000000 and rec[13] == 5
    with pytest.raises(AsmError):
        assemble([a, b], {"uart_rx.s:CLK_HZ": "48000000"})
