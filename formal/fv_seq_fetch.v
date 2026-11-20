`default_nettype none
// jspe_seq fetch properties (VERIF_PLAN 6, ARCH 4.2, ISA 10): refill latency <= G + 1, prefetch credit, request hold,
// one read at a time, redirect discards. gnt from the TDM slot model (jspe_arb proven); internals exposed by seq.sby.
module fv_seq_fetch (
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
    input wire [15:0] mem_rdata
);
`include "jspe_isa.vh"
`include "fv_gap.vh"

  wire gnt;
`include "fv_seq_dut.vh"

  // TDM slot model: free-running counter in any phase, this engine's slots from a constant map
  (* anyconst *) reg [7:0] slotmap;
  (* anyconst *) reg [1:0] eid;
  reg  [1:0] slot;
  always @(posedge clk) slot <= slot + 2'd1;
  wire       own = slotmap[{slot, 1'b0} +: 2] == eid;
  assign     gnt = req & own;
  wire [2:0] g   = gap(slotmap, eid);
  wire [2:0] k   = kgap(slotmap, eid);
  always @(*) assume (g != 3'd0);  // an engine that owns no slot stays stopped (ARCH 2.2)

`include "fv_link_env.vh"


  // reset in the first clock
  reg f_past = 1'b0;
  always @(posedge clk) f_past <= 1'b1;
  always @(*) if (!f_past) assume (rst);

  wire pulse = rst | run_p | stop_p | step_p | exec_p;
  wire act_r = run & ~xm & ~rst;
  wire x_mem = ((ir & I_LD_MASK) == I_LD_VAL) | ((ir & I_ST_MASK) == I_ST_VAL);
  wire x_loop = ~ir[15] & (ir[5:4] != LOOP_NONE);
  wire jmpw  = ((mem_rdata & I_JMP_MASK) == I_JMP_VAL) | ((mem_rdata & I_JMPA_MASK) == I_JMPA_VAL);

  // IR-empty episodes of a running engine, restarted after a redirect or host pulse, with their cause (clock before)
  localparam [2:0] CA_NONE = 3'd0, CA_RED = 3'd1, CA_ADV = 3'd2, CA_LD = 3'd3, CA_ST = 3'd4;
  wire       empty = act_r & ~irv;
  reg  [3:0] ecnt_q;
  reg  [2:0] cause_q, ecause_q;
  reg        econt_q;
  wire [3:0] ecnt = !empty ? 4'd0 : !econt_q ? 4'd1 : (ecnt_q == 4'd15) ? 4'd15 : ecnt_q + 4'd1;
  wire [2:0] cause = pulse ? CA_NONE : redir ? CA_RED : ld_done ? CA_LD : st_done ? CA_ST :
                     (fire & ~x_mem) ? CA_ADV : CA_NONE;
  wire [2:0] ecause = !empty ? CA_NONE : econt_q ? ecause_q : cause_q;
  always @(posedge clk) begin
    econt_q  <= empty & ~redir & ~pulse;
    ecnt_q   <= ecnt;
    cause_q  <= cause;
    ecause_q <= ecause;
  end

  // age of the armed instruction (0 in its first armed clock); jw = a jmp/jmpa word arrived while it was armed
  wire       armed = act_r & irv & ~mo_v;
  reg  [3:0] age_q;
  reg        acont_q, jw_q, arr_q;
  wire       acont = armed & acont_q;
  wire [3:0] age = !acont ? 4'd0 : (age_q == 4'd15) ? 4'd15 : age_q + 4'd1;
  wire       jw  = acont & jw_q;
  wire       pfc_pre = armed & fire & ~redir & ~pulse & ~x_mem & ~x_loop & ~jw;
  reg        pfc_q, pft_q, pfe_q;
  always @(posedge clk) begin
    arr_q   <= gnt & ~we;
    acont_q <= armed & ~fire & ~redir & ~pulse;
    age_q   <= age;
    jw_q    <= jw | (arr_q & jmpw);
    pfc_q   <= pfc_pre & (age >= g);
    pft_q   <= pfc_pre & (age == g - 3'd1);
    pfe_q   <= pfc_pre & (age == g);
  end

  // request bookkeeping
  reg       pend_q, pdat_q, pwe_q, fill_q, pfill_q, red_q, pulse_q;
  reg [8:0] paddr_q, gaddr_q, waddr_q;
  reg [2:0] pcnt_q;
  reg [15:0] word_q;
  always @(posedge clk) begin
    pend_q  <= req & ~gnt & ~pulse;
    pdat_q  <= req & x_mem & (mo_v | fire) & ~redir;
    pwe_q   <= we;
    paddr_q <= addr;
    pcnt_q  <= (req & ~gnt & ~pulse) ? ((pend_q && pcnt_q != 3'd7) ? pcnt_q + 3'd1 : 3'd1) : 3'd0;
    gaddr_q <= addr;
    waddr_q <= gaddr_q;
    word_q  <= mem_rdata;
    fill_q  <= arr_ir & ~pulse;
    pfill_q <= arr_pf & ~pulse;
    red_q   <= redir & ~pulse;
    pulse_q <= pulse;
  end
  wire hold = pend_q & (run | xm) & ~rst & ~redir;

  // a folded jmp moves FP to its target until the next advance, redirect or EXEC (ARCH 4.2)
  reg folded_q, rst_q;
  always @(posedge clk) begin
    folded_q <= rst ? 1'b0 : (redir | adv | exec_p) ? 1'b0 : (fold | folded_q);
    rst_q    <= rst;
  end

  always @(*) if (f_past) begin
    a_lat:     assert (ecnt <= g + 3'd1);                 // IR empty <= G + 1 clk (request -> armed <= G + 1)
    a_lat_red: assert (ecause != CA_RED || ecnt <= g);    // taken jump, call, ret, watcher: target armed <= f + G + 1
    a_lat_adv: assert (ecause != CA_ADV || ecnt <= g);    // completion with the successor not prefetched: armed <= f + G + 1
    a_lat_ld:  assert (ecause != CA_LD || ecnt <= k);     // successor of ld: armed K clk after its data slot + 2
    a_pfc:     assert (!pfc_q || irv);             // armed >= G clk, no redirect, not ld/st/loop, successor not jmp: armed at f + 1
    a_hold:    assert (!hold || (req && we == pwe_q && (pdat_q || addr == paddr_q))); // request held until granted, fetch address stable
    a_one_rd:  assert (!(arr_q & req & ~pulse_q) || redir); // no request in the clock a read arrives, except a redirect
    a_red:     assert (!red_q || (!irv && !pfv && !hrv));    // a redirect empties IR, PF and HR
    a_red_t:   assert (!(redir & ~pulse & gnt) || (addr == target && !we)); // the read granted in a redirect clock is the target
    a_fill:    assert (!fill_q || (ir == word_q && pc == waddr_q)); // IR filled from memory holds the word read at PC
    a_pfill:   assert (!pfill_q || (pf == word_q && fp == waddr_q)); // PF filled from memory holds the word read at FP
    a_rst:     assert (!rst_q || (pc == 9'd0 && fp == 9'd1)); // reset: PC = 0, FP = PC + 1 (ARCH 9.1, D109)
    a_fp:      assert (hrv || folded_q || fp == pc + 9'd1); // FP = PC + 1 unless a jmp is folded or a loop pair is loaded
    h_xmrun:   assert (!(run && xm));                      // an injected instruction runs only in a stopped engine
    h_pf:      assert (!pfv || irv);                       // PF valid only with IR valid
    h_xm:      assert (!xm || (!pfv && !hrv));             // no prefetch or loop pair under EXEC
    h_mo:      assert (!mo_v || (irv && x_mem));           // memory instruction in progress is in IR
    h_fl:      assert (!(fl_v & fl_ld) || mo_v);           // ld data in flight: its ld is in progress
    h_fi:      assert (!(fl_v & ~fl_ld) || (!pfv && !xm && !mo_v)); // instruction read in flight: PF empty, no EXEC, no ld/st
    h_hr:      assert (!hrv || (!hr[15] && hr[5])); // the loop register holds a head or tail (E format)
    h_arr:     assert ((!fl_v || arr_q) && (!arr_q || pulse_q || fl_v)); // in-flight flag = read granted last clock
  end

  always @(*) if (f_past) begin
    c_lat:     cover (ecnt == g + 3'd1 && g == 3'd4 && ecause == CA_ST);
    c_lat_g:   cover (ecnt == g + 3'd1 && g == 3'd2);
    c_lat_red: cover (ecause == CA_RED && ecnt == g && g == 3'd4);
    c_lat_adv: cover (ecause == CA_ADV && ecnt == g && g == 3'd4);
    c_lat_ld:  cover (ecause == CA_LD && ecnt == k && k == 3'd4);
    c_lat_ld_k: cover (ecause == CA_LD && ecnt == k && k > g);
    c_pfc:     cover (pfe_q && irv && g == 3'd4);
    c_pfc_tight: cover (pft_q && !irv && g == 3'd4);
    c_hold:    cover (gnt && pend_q && pcnt_q == g - 3'd1 && g == 3'd4);
    c_one_rd:  cover (arr_q && req && redir);
    c_red:     cover (redir && pfv && hrv);
    c_red_arr: cover (redir && arr_q && !fl_ld);
    c_red_t:   cover (redir && gnt && !ir[15]);
    c_fill:    cover (fill_q && ecause_q == CA_RED);
    c_pfill:   cover (pfill_q && fire);
    c_rst:     cover (rst_q && !rst && run_p);
    c_fp:      cover (fp == pc + 9'd1 && !hrv && fire && pfv && f_past && !rst_q);
    c_fp_fold: cover (folded_q && fp != pc + 9'd1 && !hrv);
    c_xmrun:   cover (xm && irv && !run && fire);
    c_pf:      cover (pfv && fire);
    c_xm:      cover (xm && irv && req);
    c_mo:      cover (mo_v && !gnt && req);
    c_fl:      cover (fl_v && fl_ld && ld_done);
    c_fi:      cover (fl_v && !fl_ld && irv);
    c_hr:      cover (hrv && fire && irv);
    c_arr:     cover (arr_q && pulse_q && !fl_v);
  end
endmodule
