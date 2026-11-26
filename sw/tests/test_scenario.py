# host/scenario.py: conflict checker (each rule with a scenario that must be rejected) and the ISS backend.
import os
import sys
import textwrap
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "host"))
import scenario as S  # noqa: E402

UART_TX = """
name: uart_tx
clk_hz: 50000000
programs:
  - {file: uart_tx.s, engines: [0]}
stimulus:
  - {at: 100, send: {e: 0, data: [0x55, 0xA5, 0x00, 0xFF]}}
expect:
  - {pad: "uo_out[0]", uart: {bit: 50, tol: 0.02}, data: %s}
  - {clean: [ovr, late, trap]}
timeout: 20000
"""

UART_RX = """
name: uart_rx
clk_hz: 50000000
programs:
  - {file: uart_rx.s, engines: [1]}
gpio: {rp: %s}
stimulus:
  - {at: 200, partner: {model: uart, pin: "ui_in[7]", bit: 51, bytes: [0x12, 0x34, 0xFE]}}
expect:
  - {stream: {e: 1}, data: [0x12, 0x34, 0xFE], within: 3000}
timeout: 20000
"""

STUB = """
.engine {e}
{extra}
.entry L{e}
.org {org}
L{e}:    jmp L{e}
"""


def write(tmp_path, text, progs=None):
    for name, body in (progs or {}).items():
        (tmp_path / name).write_text(textwrap.dedent(body))
    p = tmp_path / "s.yaml"
    p.write_text(textwrap.dedent(text))
    return str(p)


def stub(e, org, extra=""):
    return STUB.format(e=e, org=org, extra=extra)


def two(tmp_path, a, b, more=""):
    # scenario with stub programs a.s (E0) and b.s (E1)
    text = """
    name: t
    clk_hz: 50000000
    programs:
      - {file: a.s, engines: [0]}
      - {file: b.s, engines: [1]}
    %s
    expect: [{clean: ovr}]
    timeout: 10
    """ % more
    return write(tmp_path, text, {"a.s": a, "b.s": b})


def rejects(path, word):
    with pytest.raises(S.Conflict) as x:
        S.check(S.load(path))
    assert any(word in m for m in x.value.msgs), x.value.msgs
    return x.value.msgs


# ---------------- ISS backend ----------------
def test_uart_tx_pass(tmp_path):
    ok, text = S.run(write(tmp_path, UART_TX % "[0x55, 0xA5, 0x00, 0xFF]"))
    assert ok, text
    assert text.splitlines()[-1] == "PASS uart_tx" and "4 chars" in text


def test_uart_tx_wrong_data_fails(tmp_path):
    ok, text = S.run(write(tmp_path, UART_TX % "[0x55, 0xA5, 0x00, 0xFE]"))
    assert not ok and text.splitlines()[-1] == "FAIL uart_tx" and text.startswith("FAIL pad")


def test_uart_rx_partner_pass(tmp_path):
    ok, text = S.run(write(tmp_path, UART_RX % '["ui_in[7]"]'))
    assert ok, text


def test_uart_rx_rate_off_fails(tmp_path):
    ok, text = S.run(write(tmp_path, UART_RX.replace("bit: 51", "bit: 60") % '["ui_in[7]"]'))
    assert not ok, text


def test_timeout_fails(tmp_path):
    t = UART_RX.replace("data: [0x12, 0x34, 0xFE], within: 3000", "data: [0x12, 0x34, 0xFE, 0x00]") \
        .replace("timeout: 20000", "timeout: 4000")
    ok, text = S.run(write(tmp_path, t % '["ui_in[7]"]'))
    assert not ok and "FAIL stream E1: 3 entries" in text


def test_event_trigger_and_fault(tmp_path):
    # the second send waits for nothing that ever comes: the report names the unfired stimulus
    t = UART_TX % "[0x55, 0xA5, 0x00, 0xFF]"
    t = t.replace("timeout: 20000", "timeout: 20000\nsettle: 100").replace(
        "stimulus:\n", "stimulus:\n  - {after: {e: 0, evt: 9}, send: {e: 0, data: [1]}}\n"
                       "  - {at: 10, fault: {e: 0, word: 0}}\n")
    ok, text = S.run(write(tmp_path, t))
    assert not ok and "1 of 3 stimuli never fired" in text


def test_board_plan(tmp_path):
    _, plan = S.check(S.load(write(tmp_path, UART_RX % '["ui_in[7]"]')))
    bp = S.board_plan(plan)
    assert bp == {"rp_ui": [3, 7], "rp_uio": [], "pmod": []}
    assert ("ui", 7) not in plan["inputs"] and ("uio", 4) in plan["inputs"]


def test_cli_exit_codes(tmp_path, capsys):
    assert S.main([write(tmp_path, UART_TX % "[0x55, 0xA5, 0x00, 0xFF]")]) == 0
    assert S.main([write(tmp_path, UART_RX % '["uo_out[1]"]')]) == 2
    assert "REJECT pads" in capsys.readouterr().out


def test_can_pair_shares_lfsr(tmp_path):
    t = """
    name: can
    programs: [{file: can.s, engines: [0, 1]}]
    lfsr_share: [[0, 1]]
    gpio: {pmod: ["uio[4]:od"]}
    expect: [{clean: ovr}]
    timeout: 10
    """
    img, _ = S.check(S.load(write(tmp_path, t)))
    assert sorted(img.engines) == [0, 1]
    rejects(write(tmp_path, t.replace("lfsr_share: [[0, 1]]", "")), "LFSR")
    rejects(write(tmp_path, t.replace('"uio[4]:od"', '"uio[4]"')), "driven by the chip (od)")


# ---------------- conflicts that must be rejected ----------------
def test_reject_engine_twice(tmp_path):
    t = """
    name: t
    programs:
      - {file: uart_tx.s, engines: [0]}
      - {file: spi_ctrl.s, engines: [0]}
    expect: [{clean: ovr}]
    timeout: 10
    """
    rejects(write(tmp_path, t), "E0 in uart_tx.s and spi_ctrl.s")


def test_reject_engine_mismatch(tmp_path):
    rejects(write(tmp_path, UART_TX.replace("engines: [0]", "engines: [2]") % "[]"), "uses [0], scenario says [2]")


def test_reject_clock(tmp_path):
    rejects(write(tmp_path, UART_TX.replace("50000000", "48000000") % "[]"), "clock")


def test_reject_uo_out_driven(tmp_path):
    rejects(write(tmp_path, UART_RX % '["uo_out[1]"]'), "chip output")


def test_reject_link_pad(tmp_path):
    rejects(write(tmp_path, UART_RX % '["ui_in[7]", "ui_in[3]"]'), "host link pad")


def test_reject_rp_and_pmod(tmp_path):
    rejects(write(tmp_path, UART_RX.replace("gpio: {rp: %s}", 'gpio: {rp: %s, pmod: ["ui_in[7]"]}')
                  % '["ui_in[7]"]'), "listed twice")


def test_reject_undeclared_partner_pin(tmp_path):
    rejects(write(tmp_path, UART_RX % "[]"), "partner pin ui_in[7]")


def test_reject_push_pull_pad_driven(tmp_path):
    p = two(tmp_path, stub(0, 0, ".pad uio2, p0"), stub(1, 8), 'gpio: {pmod: ["uio[2]:od"]}')
    rejects(p, "driven by the chip (push-pull)")


def test_od_pad_shared_ok(tmp_path):
    p = two(tmp_path, stub(0, 0, ".pad uio2, p0, od"), stub(1, 8), 'gpio: {rp: ["uio[2]:od"]}')
    _, plan = S.check(S.load(p))
    assert S.board_plan(plan)["rp_uio"] == [2]


def test_reject_two_chip_owners(tmp_path):
    rejects(two(tmp_path, stub(0, 0, ".pad uio2, p0"), stub(1, 8, ".pad uio2, p0")), "memory/pads")


def test_reject_memory_overlap(tmp_path):
    rejects(two(tmp_path, stub(0, 0), stub(1, 0)), "memory/pads")


def test_reject_reserve(tmp_path):
    r = '.include "cleo_mem.inc"\n.reserve BR_MBOX, 1'
    (tmp_path / "cleo_mem.inc").write_text(open(os.path.join(S.PROG, "cleo_mem.inc")).read())
    rejects(two(tmp_path, stub(0, 0, r), stub(1, 8, r)), "memory/pads")


def test_reject_data_above_mem_data(tmp_path):
    msgs = rejects(two(tmp_path, stub(0, 0), stub(1, 470)), "MEM_DATA")
    assert len(msgs) == 1


def test_reject_slot_maps_differ(tmp_path):
    rejects(two(tmp_path, stub(0, 0, ".slots 0, 1, 0, 1"), stub(1, 8, ".slots 0, 1, 1, 1")), ".slots differ")


def test_reject_default_map_assumed(tmp_path):
    rejects(two(tmp_path, stub(0, 0, ".slots 0, 1, 0, 1"), stub(1, 8)), "assume the default map")


def test_reject_engine_without_slot(tmp_path):
    t = """
    name: t
    programs: [{file: a.s, engines: [0, 1]}]
    expect: [{clean: ovr}]
    timeout: 10
    """
    rejects(write(tmp_path, t, {"a.s": stub(0, 0, ".slots 0, 0, 0, 0") + stub(1, 8)}), "E1 owns no slot")


def test_reject_lfsr_users(tmp_path):
    a, b = stub(0, 0, ".init\n wcfg LFSR_LO, R0\n.code"), stub(1, 8, ".init\n rcfg R0, LFSR_HI\n.code")
    rejects(two(tmp_path, a, b), "LFSR: engines [0, 1]")
    S.check(S.load(two(tmp_path, a, b, "lfsr_share: [[0, 1]]")))


def test_lfsr_tapcfg_step_bit(tmp_path):
    step = stub(0, 0, ".init\n li R0, #0x08\n wcfg TAPCFG, R0\n.code")
    nostep = stub(1, 8, ".init\n li R0, #0x04\n wcfg TAPCFG, R0\n.code")
    S.check(S.load(two(tmp_path, step, nostep)))
    rejects(two(tmp_path, step, nostep.replace("0x04", "0x0C")), "LFSR")


def test_reject_unknown_key(tmp_path):
    rejects(write(tmp_path, UART_TX % "[]" + "bogus: 1\n"), "unknown keys")
