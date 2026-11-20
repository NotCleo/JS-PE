`default_nettype none
// jspe_seq decoder (VERIF_PLAN 6, ISA 2, 8.10): undef equals the predicate generated from isa.yaml (jspe_fv_isa.vh) for
// every word in IR; the format decodes equal the isa.yaml formats, which partition the words; the class decodes used
// by the sequencer equal their isa.yaml instructions on every defined word.
module fv_seq_dec (
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
`include "jspe_fv_isa.vh"
`include "fv_seq_dut.vh"

  reg f_past = 1'b0;
  always @(posedge clk) f_past <= 1'b1;
  always @(*) if (!f_past) assume (rst);

  function hit;
    input [15:0] w, m, v;
    hit = (w & m) == v;
  endfunction
  wire m_e  = hit(ir, FMT_E_MASK, FMT_E_VAL);
  wire m_jp = hit(ir, FMT_JP_MASK, FMT_JP_VAL);
  wire m_jf = hit(ir, FMT_JF_MASK, FMT_JF_VAL);
  wire m_gm = hit(ir, FMT_GM_MASK, FMT_GM_VAL);
  wire m_im = hit(ir, FMT_IM_MASK, FMT_IM_VAL);
  wire m_rx = hit(ir, FMT_RX_MASK, FMT_RX_VAL);
  wire [2:0] nfmt = m_e + m_jp + m_jf + m_gm + m_im + m_rx;
  wire y_mem = hit(ir, I_LD_MASK, I_LD_VAL) | hit(ir, I_ST_MASK, I_ST_VAL);
  wire y_txw = hit(ir, I_PUSH_MASK, I_PUSH_VAL) | hit(ir, I_PUSHH_MASK, I_PUSHH_VAL) | hit(ir, I_EVT_MASK, I_EVT_VAL);
  wire y_pull = hit(ir, I_PULL_MASK, I_PULL_VAL) | hit(ir, I_PULLH_MASK, I_PULLH_VAL);
  wire y_undef = fv_undef(ir);

  always @(*) if (f_past) begin
    a_undef: assert (undef == y_undef);                  // undefined <=> no isa.yaml instruction matches, or an undefined group
    a_fmt:   assert (nfmt == 3'd1 && f_e == m_e && f_jp == m_jp && f_jf == m_jf && f_gm == m_gm && f_rx == m_rx); // one format per word
    a_cls:   assert (y_undef || (is_mem == y_mem && (!is_mem || is_st == hit(ir, I_ST_MASK, I_ST_VAL))
                     && is_jmpa == hit(ir, I_JMPA_MASK, I_JMPA_VAL) && is_call == hit(ir, I_CALL_MASK, I_CALL_VAL)
                     && is_wcfg == hit(ir, I_WCFG_MASK, I_WCFG_VAL) && is_ret == hit(ir, I_RET_MASK, I_RET_VAL)
                     && is_halt == hit(ir, I_HALT_MASK, I_HALT_VAL) && is_ovclr == hit(ir, I_OVCLR_MASK, I_OVCLR_VAL)
                     && is_watch == hit(ir, I_WATCH_MASK, I_WATCH_VAL) && is_txw == y_txw && is_pull == y_pull)); // class decodes on defined words
  end

  always @(*) if (f_past) begin
    c_undef:     cover (undef && m_jf && irv);
    c_undef_gm:  cover (undef && m_gm && irv);
    c_undef_rx:  cover (undef && m_rx && irv);
    c_undef_sig: cover (undef && hit(ir, UNDEF0_MASK, UNDEF0_VAL) && irv);
    c_undef_def: cover (!undef && m_rx && fire);
    c_fmt:       cover (m_im && fire);
    c_fmt_jp:    cover (m_jp && fire);
    c_cls:       cover (fire && is_mem && is_st);
    c_cls_pull:  cover (fire && is_pull);
    c_cls_watch: cover (fire && is_watch);
  end
endmodule
