`default_nettype none
// jspe_seq watchers, trap, halt (VERIF_PLAN 6, ISA 6, 8.4, 8.10, ARCH 5, 9.2, D065, D086): configuration writes, cells,
// watcher fire, its effects, and the stop/flag rules against a model; undef from the DUT (sub-step 4). gnt is free;
// proven without the link guarantees, covers reached with them (LINK_ENV).
module fv_seq_wch (
    input wire        clk,
    input wire        rst,
    input wire        run_p,
    input wire        stop_p,
    input wire        step_p,
    input wire        exec_p,
    input wire [15:0] exec_word,
    input wire        clr_ovr,
    input wire        clr_ht,
    input wire        rx_ovf,
    input wire [3:0]  lv,
    input wire [3:0]  rise,
    input wire [3:0]  fall,
    input wire        tick,
    input wire        tock,
    input wire        t_start,
    input wire        mis,
    input wire        hrx,
    input wire        htx,
    input wire        fb,
    input wire        fpar,
    input wire        frun,
    input wire        fw,
    input wire        loop_end,
    input wire        z_n,
    input wire        cy_n,
    input wire        flag_we,
    input wire [15:0] opb,
    input wire        wr_en,
    input wire [2:0]  wr_reg,
    input wire [8:0]  wr_data,
    input wire [8:0]  maddr,
    input wire        gnt,
    input wire [15:0] mem_rdata
);
`include "jspe_isa.vh"
`include "fv_seq_dut.vh"
`ifdef LINK_ENV
`include "fv_link_env.vh"
`endif

  reg f_past = 1'b0;
  always @(posedge clk) f_past <= 1'b1;
  always @(*) if (!f_past) assume (rst);

  wire act   = (run | xm) & ~rst;
  wire run_w = run & ~xm & ~rst;
  wire pulse = rst | run_p | stop_p | step_p | exec_p;

  // configuration model: CC0, CC1, WV0, WV1 written by wcfg (creg [7:3], data = rs), wen by watch #m (m [5:4])
  wire       x_wcfg  = fire & ((ir & I_WCFG_MASK) == I_WCFG_VAL);
  wire       x_watch = fire & ((ir & I_WATCH_MASK) == I_WATCH_VAL);
  wire [4:0] creg    = ir[7:3];
  reg [13:0] m_cc0, m_cc1;
  reg [8:0]  m_wv0, m_wv1;
  reg [1:0]  m_wen;
  always @(posedge clk) begin
    if (rst) begin
      m_cc0 <= 14'd0;
      m_cc1 <= 14'd0;
      m_wv0 <= 9'd0;
      m_wv1 <= 9'd0;
      m_wen <= 2'd0;
    end else begin
      if (x_wcfg && creg == CREG_CC0) m_cc0 <= opb[13:0];
      if (x_wcfg && creg == CREG_CC1) m_cc1 <= opb[13:0];
      if (x_wcfg && creg == CREG_WV0) m_wv0 <= opb[8:0];
      if (x_wcfg && creg == CREG_WV1) m_wv1 <= opb[8:0];
      if (x_watch) m_wen <= ir[5:4];
    end
  end

  // ISA 6 cell: lut[{b, a}], a = source selA [3:0], b = source selB [7:4], lut [11:8]; mode [13:12]: 1 abort, 2 call
  // sources: 0-3 level p0-p3, 4-7 rise, 8-11 fall, 12 tick, 13 tock, 14 MIS, 15 hrx
  wire [15:0] src = {hrx, mis, tock, tick, fall, rise, lv};
  function cellv;
    input [13:0] c;
    input [15:0] s;
    reg [3:0] lut;
    reg a, b;
    begin
      lut  = c[11:8];
      a    = s[c[3:0]];
      b    = s[c[7:4]];
      cellv = lut[{b, a}];
    end
  endfunction
  wire mc0 = cellv(m_cc0, src);
  wire mc1 = cellv(m_cc1, src);
  reg  mp0, mp1;
  always @(posedge clk) {mp1, mp0} <= rst ? 2'b00 : {mc1, mc0};   // previous cell value, every clock (ARCH 5 rule 11)
  wire on0   = m_cc0[13:12] == 2'd1 || m_cc0[13:12] == 2'd2;
  wire on1   = m_cc1[13:12] == 2'd1 || m_cc1[13:12] == 2'd2;
  wire mw0   = run_w & m_wen[0] & on0 & mc0 & ~mp0;
  wire mw1   = run_w & m_wen[1] & on1 & mc1 & ~mp1 & ~mw0;       // watcher 0 wins
  wire mwf   = mw0 | mw1;
  wire mcall = mw0 ? (m_cc0[13:12] == 2'd2) : (m_cc1[13:12] == 2'd2);
  wire [8:0] mvec = mw0 ? m_wv0 : m_wv1;

  // trap, halt, stop
  wire arm      = act & irv & ~wf & ~mo_v;
  wire trapc    = arm & undef;
  wire x_halt   = (ir & I_HALT_MASK) == I_HALT_VAL;
  wire x_mem    = ((ir & I_LD_MASK) == I_LD_VAL) | ((ir & I_ST_MASK) == I_ST_VAL);
  wire haltc    = fire & x_halt;
  wire complete = (fire & ~x_mem) | ld_done | st_done;
  // next instruction after halt: FP, or the target of a jmp/jmpa in PF that is folded in that clock (ISA 8.3, 8.7)
  wire       pf_jmp  = (pf & I_JMP_MASK) == I_JMP_VAL;
  wire       pf_jmpa = (pf & I_JMPA_MASK) == I_JMPA_VAL;
  wire [8:0] pf_tgt  = pf_jmpa ? pf[8:0] : fp + {{2{pf[6]}}, pf[6:0]} + 9'd1;
  wire [8:0] nxt     = (pfv & (pf_jmp | pf_jmpa)) ? pf_tgt : fp;

  reg       wf_q, call_q, trap_q, halt_q, xm_q, run_q, trapf_q, haltf_q, ht_q;
  reg       tset_q, tclr_q, hset_q, hclr_q, rdn_q, rup_q, xup_q;
  reg [8:0] vec_q, pc_q, nxt_q;
  reg       f_past2, stepc_q, stop_q, htc_q, runp_q, rs_q;
  reg [15:0] ir_q;
  always @(posedge clk) begin
    wf_q    <= wf & ~pulse;
    call_q  <= wf & ~wabort & ~pulse;
    vec_q   <= mvec;
    pc_q    <= pc;
    nxt_q   <= nxt;
    f_past2 <= f_past;
    stepc_q <= complete & stepm & ~xm & ~pulse & ~haltc & ~trapc;
    stop_q  <= stop_p & ~rst;
    htc_q   <= clr_ht & ~rst;
    runp_q  <= run_p & ~rst;
    rs_q    <= (run_p | step_p) & ~rst;
    ir_q    <= ir;
    xm_q    <= xm;
    trap_q  <= trapc & ~pulse & ~clr_ht;
    halt_q  <= haltc & ~pulse & ~clr_ht;
    ht_q    <= clr_ht & ~rst;
    run_q   <= run;
    trapf_q <= trap;
    haltf_q <= halted;
    tset_q  <= trapc & ~rst;
    tclr_q  <= clr_ht | rst;
    hset_q  <= haltc & ~rst;
    hclr_q  <= clr_ht | run_p | step_p | rst;
    rdn_q   <= haltc | trapc | stop_p | (complete & stepm & ~xm) | rst;
    rup_q   <= (run_p | step_p) & ~rst;
    xup_q   <= exec_p & ~rst;
  end

  always @(*) if (f_past) begin
    a_cfg:     assert (cc0 == m_cc0 && cc1 == m_cc1 && wv0 == m_wv0 && wv1 == m_wv1 && wen == m_wen); // CC0/1, WV0/1 only by wcfg, wen only by watch
    a_cell:    assert (cv0 == mc0 && cv1 == mc1);       // cell k = lut[{b, a}] of the selected sources (ISA 6)
    a_wf:      assert (wf == mwf && (!mwf || wabort == !mcall)); // watcher fires iff enabled, abort/call, rising cell, running; 0 wins
    a_wf_only: assert (!wf || (run_w && ((m_wen[0] && on0 && mc0 && !mp0) || (m_wen[1] && on1 && mc1 && !mp1)))); // fires only enabled, rising, running
    a_wf_blk:  assert (!wf || (!fire && !we && !ld_done && !st_done)); // watcher clock: no fire, no store written, no ld/st completes
    a_wf_red:  assert (!wf_q || (pc == vec_q && !irv && !mo_v)); // PC <- WV_k; armed instruction and ld/st in progress dropped
    a_wf_call: assert (!call_q || l == pc_q);           // call mode: L <- PC of the preempted instruction
    a_trap:    assert (!trap_q || (!run && trap && pc == pc_q && (xm_q ? (!xm && !irv) : (irv && ir == ir_q)))); // trap: stopped, PC kept; EXEC: injection ends
    a_trap_nf: assert (!trapc || !fire);                // an undefined instruction never fires
    a_halt:    assert (!halt_q || (!run && halted && pc == (xm_q ? pc_q : nxt_q))); // halt: stopped, PC on the next instruction (EXEC: kept)
    a_ht_host: assert (!ht_q || (!trap && !halted));    // host clear wins over trap/halt set (D065)
    a_trap_up: assert (!trap || trapf_q || tset_q);     // trap rises only when an undefined instruction is armed
    a_trap_dn: assert (trap || !trapf_q || tclr_q);     // trap falls only by host clear or reset
    a_halt_up: assert (!halted || haltf_q || hset_q);   // halted rises only by a fired halt
    a_halt_dn: assert (halted || !haltf_q || hclr_q);   // halted falls only by host clear, RUN, STEP, reset
    a_halt_run: assert (!rs_q || (run && !halted));     // RUN/STEP: running, halted cleared (HOSTIF 3)
    a_run_dn:  assert (run || !run_q || rdn_q);         // run falls only by halt, trap, STOP, step completion, reset
    a_run_up:  assert (!run || run_q || rup_q);         // run rises only by RUN or STEP
    a_xm_up:   assert (!xm || xm_q || xup_q);           // xm rises only by EXEC (link proof assumption, D114)
    h_wprev:   assert (wprev == {mp1, mp0});            // wprev <- cell values every clock, also when stopped
  end

  always @(*) if (f_past) begin
    c_cfg:      cover (x_wcfg && creg == CREG_WV1 && opb[8:0] != 9'd0);
    c_cell:     cover (mc0 && m_cc0[11:8] == 4'h8 && m_cc0[3:0] != m_cc0[7:4]);
    c_wf:       cover (mw0 && !mcall && irv);
    c_wf_call:  cover (mw1 && mcall);
    c_wf_both:  cover (mw0 && m_wen[1] && on1 && mc1 && !mp1);
    c_wf_only:  cover (!wf && run_w && m_wen[0] && on0 && mc0 && mp0);
    c_wf_blk:   cover (wf && arm == 1'b0 && irv && !mo_v && act);
    c_wf_blk_m: cover (wf && mo_v);
    c_wf_red:   cover (wf_q && pc != pc_q + 9'd1 && pc != pc_q);
    c_wf_call_l: cover (call_q && l != 9'd0);
    c_trap:     cover (trap_q && !xm_q);
    c_trap_x:   cover (trap_q && xm_q);
    c_trap_nf:  cover (trapc && ir[15:12] == 4'hF);
    c_halt:     cover (halt_q && !xm_q && nxt_q != pc_q + 9'd1);
    c_halt_x:   cover (halt_q && xm_q);
    c_ht_host:  cover (clr_ht && trapc);
    c_trap_up:  cover (trap && !trapf_q && f_past2);
    c_trap_dn:  cover (!trap && trapf_q && htc_q && f_past2);
    c_halt_up:  cover (halted && !haltf_q && f_past2);
    c_halt_dn:  cover (!halted && haltf_q && runp_q && f_past2);
    c_halt_run: cover (rs_q && haltf_q);
    c_halt_run_same: cover (rs_q && hset_q);
    c_run_dn:   cover (!run && run_q && stepc_q && f_past2);
    c_run_dn_stop: cover (!run && run_q && stop_q && f_past2);
    c_run_up:   cover (run && !run_q && f_past2);
    c_xm_up:    cover (xm && !xm_q && f_past2);
    c_wprev:    cover (!act && mp0 && !rst);
  end
endmodule
