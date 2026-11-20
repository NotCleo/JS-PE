`default_nettype none
// Engine sequencer (ARCH 3.3, 4-6): IR / PF / HR, fetch and prefetch, jump folding, guards, event flags,
// watchers, micro-loop, host control. Next state follows sw/jspe_iss.py Engine.step clock for clock.
module jspe_seq (
    input  wire        clk,
    input  wire        rst,
    input  wire        run_p,
    input  wire        stop_p,
    input  wire        step_p,
    input  wire        exec_p,
    input  wire [15:0] exec_word,
    input  wire        clr_ovr,
    input  wire        clr_ht,
    input  wire        rx_ovf,
    input  wire [3:0]  lv,
    input  wire [3:0]  rise,
    input  wire [3:0]  fall,
    input  wire        tick,
    input  wire        tock,
    input  wire        t_start,
    input  wire        mis,
    input  wire        hrx,
    input  wire        htx,
    input  wire        fb,
    input  wire        fpar,
    input  wire        frun,
    input  wire        fw,
    input  wire        loop_end,
    input  wire        z_n,
    input  wire        cy_n,
    input  wire        flag_we,
    input  wire [15:0] opb,
    input  wire        wr_en,
    input  wire [2:0]  wr_reg,
    input  wire [8:0]  wr_data,
    input  wire [8:0]  maddr,
    input  wire        gnt,
    input  wire [15:0] mem_rdata,
    output reg  [15:0] ir,
    output wire        fire,
    output wire        ld_done,
    output wire        st_done,
    output wire        req,
    output wire        we,
    output wire [8:0]  addr,
    output reg  [8:0]  pc,
    output reg  [8:0]  l,
    output reg         cy,
    output reg         run,
    output reg         waiting,
    output reg         ovr,
    output reg         halted,
    output reg         trap,
    output reg         xm
);
/* verilator lint_off UNUSEDPARAM */
`include "jspe_isa.vh"
/* verilator lint_on UNUSEDPARAM */

  reg [15:0] hr, pf;
  reg [8:0]  fp, wv0, wv1;
  reg [13:0] cc0, cc1;
  reg [9:0]  f, d;
  reg [1:0]  wen, wprev;
  reg        irv, hrv, pfv, stepm, fl_v, fl_ld, mo_v, z;

  // ---------------- decode
  wire f_e  = ~ir[15];
  wire f_jp = (ir[15:14] == 2'b10);
  wire f_jf = (ir[15:12] == 4'hC);
  wire f_gm = (ir[15:12] == 4'hD);
  wire f_rx = (ir[15:12] == 4'hF);
  wire [1:0] sub = ir[11:10];
  wire [2:0] gk  = ir[9:7];
  wire [2:0] gn  = ir[6:4];
  wire undef_jf = (ir[11:8] > COND_MIS) | (ir[11:7] == 5'd1);
  wire undef_gm = (sub == 2'd1 & gk[2]) |
                  (sub == 2'd2 & ((gk == 3'd0 & gn == 3'd7) | gk == 3'd4 | (gk == 3'd5 & gn[2]) | gk[2:1] == 2'b11));
  wire undef_rx = (sub == 2'd0 & (ir[9:6] == 4'd3 | ir[9:6] > ALU_RRC)) |
                  (ir[11:9] == 3'b100 & ir[0]) | (ir[11:8] == 4'hA & ir[7:3] == CREG_SIG) | sub == 2'd3;
  wire undef = (f_jf & undef_jf) | (f_gm & undef_gm) | (f_rx & undef_rx);

  wire is_mem   = f_rx & (ir[11:9] == 3'b100);
  wire is_st    = ir[8];
  wire is_jmpa  = f_rx & (ir[11:9] == 3'b010);
  wire is_call  = f_rx & (ir[11:9] == 3'b011);
  wire is_wcfg  = f_rx & (ir[11:8] == 4'hA);
  wire is_ret   = (ir & I_RET_MASK) == I_RET_VAL;
  wire is_halt  = (ir & I_HALT_MASK) == I_HALT_VAL;
  wire is_ovclr = (ir & I_OVCLR_MASK) == I_OVCLR_VAL;
  wire is_watch = (ir & I_WATCH_MASK) == I_WATCH_VAL;
  wire is_txw   = f_gm & ((sub == 2'd1 & ~ir[8]) | sub == 2'd3);      // push, pushh, evt
  wire is_pull  = f_gm & sub == 2'd1 & ir[8];

  // ---------------- events, condition cells, watchers
  wire [9:0]  live = {tock, tick, fall, rise};
  wire [15:0] ccv  = {hrx, mis, tock, tick, fall, rise, lv};
  wire cv0 = cc0[4'd8 + {2'd0, ccv[cc0[7:4]], ccv[cc0[3:0]]}];
  wire cv1 = cc1[4'd8 + {2'd0, ccv[cc1[7:4]], ccv[cc1[3:0]]}];
  wire wrun = run & ~xm & ~rst;
  wire wf0 = wrun & wen[0] & (cc0[13] ^ cc0[12]) & cv0 & ~wprev[0];
  wire wf1 = wrun & wen[1] & (cc1[13] ^ cc1[12]) & cv1 & ~wprev[1] & ~wf0;
  wire wf  = wf0 | wf1;
  wire wcall  = wf0 ? cc0[13] : cc1[13];
  wire wabort = wf & ~wcall;

  // ---------------- guard
  wire [3:0] g = (f_e | f_jp | f_gm) ? ir[3:0] : G_ALWAYS;
  wire [9:0] evt = live | f;
  wire gev_v = g[3] | g == G_TICK | g == G_TOCK;
  wire [3:0] gev = g[3] ? {1'b0, g[2:0]} : (g == G_TICK) ? 4'd8 : 4'd9;
  reg gok;
  always @(*) begin
    case (g)
      G_ALWAYS: gok = 1'b1;
      G_HRX:    gok = hrx;
      G_HTX:    gok = htx;
      G_C0:     gok = cv0;
      G_C1:     gok = cv1;
      G_NEVER:  gok = 1'b0;
      default:  gok = evt[gev];
    endcase
  end

  wire act  = (run | xm) & ~rst;
  wire arm  = act & irv & ~wf & ~mo_v;
  wire trap_now = arm & undef;
  assign fire   = arm & ~undef & gok;
  wire wait_now = arm & ~undef & ~gok;

  // ---------------- jumps and micro-loop
  reg jcond;
  always @(*) begin
    case (ir[11:8])
      COND_TRUE: jcond = 1'b1;
      COND_Z:    jcond = z;
      COND_CY:   jcond = cy;
      COND_B:    jcond = fb;
      COND_PAR:  jcond = fpar;
      COND_RUN:  jcond = frun;
      COND_W:    jcond = fw;
      COND_HRX:  jcond = hrx;
      COND_HTX:  jcond = htx;
      COND_C0:   jcond = cv0;
      COND_C1:   jcond = cv1;
      COND_OVR:  jcond = ovr;
      default:   jcond = mis;
    endcase
  end
  wire jrel  = (f_jp & (lv[ir[12:11]] == ir[13])) | (f_jf & (jcond ^ ir[7]));
  wire jabs  = is_jmpa | is_call | is_ret;
  wire jump  = fire & (jrel | jabs);
  wire [1:0] rr = ir[5:4];
  wire loop  = fire & f_e & ~xm & (rr != LOOP_NONE);
  wire head  = loop & (rr == LOOP_HEAD);
  wire tail  = loop & (rr == LOOP_TAIL);
  wire loop_adv   = loop & ((rr == LOOP_REP & loop_end) | (head & (loop_end == hrv)) | (tail & loop_end));
  wire loop_redir = (head & loop_end & ~hrv) | (tail & ~loop_end & ~hrv);
  wire loop_swap  = (head | tail) & ~loop_end & hrv;
  wire head_first = head & ~loop_end & ~hrv;

  // PC + 1 + x: jump target, call link, head +1 / +2, tail -1
  wire [6:0] rel = f_jp ? ir[10:4] : ir[6:0];
  wire [8:0] pcx = f_e ? (rr[0] ? 9'h1FE : {8'd0, loop_end}) : (f_jp | f_jf) ? {{2{rel[6]}}, rel} : 9'd0;
  wire [8:0] pcrel = pc + pcx + 9'd1;

  // ---------------- memory instruction and arriving read
  wire arr_v  = fl_v & ~rst;
  wire arr_f  = arr_v & ~fl_ld;
  wire mo_now = (mo_v | (fire & is_mem)) & ~wf & ~rst;
  wire mem_req = mo_now & act & ~arr_v;
  wire gq = req & gnt;
  assign ld_done = mo_now & arr_v & fl_ld;
  assign st_done = mem_req & is_st & gq;
  wire mo_n = mo_now & ~ld_done & ~st_done;

  // ---------------- next instruction
  wire redir = wf | jump | loop_redir;
  wire [8:0] target = wf0 ? wv0 : wf1 ? wv1 : (jump & jabs) ? (is_ret ? l : ir[8:0]) : pcrel;
  wire pf_jmp = (pf & I_JMP_MASK) == I_JMP_VAL;
  wire pf_jmpa = (pf & I_JMPA_MASK) == I_JMPA_VAL;
  wire fold_pre = act & ~xm & pfv & ~arr_v & (pf_jmp | pf_jmpa) & ~redir;
  wire fold = fold_pre & ~mo_n;
  wire [8:0] ftarget = pf_jmp ? fp + {{2{pf[6]}}, pf[6:0]} + 9'd1 : pf[8:0];

  wire fire_adv = fire & ~is_mem & ~jump & ~loop;
  wire complete = (fire & ~is_mem) | ld_done | st_done;
  wire advance  = fire_adv | loop_adv | ld_done | st_done;
  wire adv   = advance & ~xm;
  wire adv_x = advance & xm;
  wire trapx = trap_now & xm;
  wire take_pf  = adv & pfv & ~fold;
  wire take_arr = adv & ~pfv & arr_f;
  wire irv_mid  = take_pf | (irv & ~adv & ~adv_x & ~trapx);
  wire arr_ir   = arr_f & ~redir & (take_arr | ~irv_mid);
  wire arr_pf   = arr_f & ~redir & ~take_arr & irv_mid;
  wire irv_n = ~redir & (irv_mid | arr_f);
  wire pfv_n = ~redir & (arr_pf | (pfv & ~adv & ~fold));
  // EXEC: FP <- PC + 1 (PC after this clock), so the stopped program is refetched in order
  wire [8:0] fbase = redir ? target : fold ? ftarget : (exec_p & ~adv) ? pc : fp;
  wire [8:0] fp_n  = fbase + {8'd0, redir | adv | exec_p};

  // ---------------- request: never a function of gnt
  wire adv_q   = (fire_adv | loop_adv) & ~xm;
  wire other   = act & ~arr_v & ~mo_now & ~xm & ~redir & ~fold_pre;
  wire req_ir  = other & (adv_q ? ~pfv : ~irv);
  wire req_pf  = other & ~adv_q & irv & ~pfv;
  wire req_red = act & redir & ~xm;
  wire req_fld = fold_pre & ~mo_now;
  assign req  = mem_req | req_red | req_fld | req_ir | req_pf;
  assign we   = mem_req & is_st;
  assign addr = mem_req ? maddr : redir ? target : fold_pre ? ftarget : req_pf ? fp : adv_q ? fp : pc;

  // ---------------- event flags (ARCH 6)
  wire fclr = ~act | wait_now | wabort;
  reg [9:0] f_n, d_n;
  integer k;
  always @(*) begin
    for (k = 0; k < 10; k = k + 1) begin
      f_n[k] = f[k];
      d_n[k] = d[k];
      if (fire && gev_v && gev == k[3:0]) begin
        f_n[k] = f[k] & live[k];
        d_n[k] = 1'b0;
      end else if (live[k]) begin
        f_n[k] = 1'b1;
        d_n[k] = f[k];
      end
      if (fclr || (t_start && k >= 8)) begin
        f_n[k] = 1'b0;
        d_n[k] = 1'b0;
      end
    end
  end
  wire ovr_set = fire & ((gev_v & d[gev]) | (is_txw & ~htx) | (is_pull & ~hrx));

  always @(posedge clk) begin
    if (rst) begin
      run     <= 1'b0;
      xm      <= 1'b0;
      stepm   <= 1'b0;
      irv     <= 1'b0;
      hrv     <= 1'b0;
      pfv     <= 1'b0;
      fl_v    <= 1'b0;
      fl_ld   <= 1'b0;
      mo_v    <= 1'b0;
      pc      <= 9'd0;
      fp      <= 9'd1;
      l       <= 9'd0;
      wv0     <= 9'd0;
      wv1     <= 9'd0;
      cc0     <= 14'd0;
      cc1     <= 14'd0;
      wen     <= 2'd0;
      wprev   <= 2'd0;
      f       <= 10'd0;
      d       <= 10'd0;
      ovr     <= 1'b0;
      z       <= 1'b0;
      cy      <= 1'b0;
      halted  <= 1'b0;
      trap    <= 1'b0;
      waiting <= 1'b0;
    end else begin
      // instruction registers
      if (loop_swap) ir <= hr;
      else if (take_pf) ir <= pf;
      else if (arr_ir) ir <= mem_rdata;
      if (arr_pf) pf <= mem_rdata;
      if (loop_swap | head_first) hr <= ir;
      irv <= irv_n;
      pfv <= pfv_n;
      if (redir | (loop & loop_end)) hrv <= 1'b0;
      else if (head_first) hrv <= 1'b1;
      if (redir) pc <= target;
      else if (loop_swap) pc <= pcrel;
      else if (adv) pc <= (pfv & fold) ? ftarget : fp;
      if (redir | adv | fold | exec_p) fp <= fp_n;
      if (wf & wcall) l <= pc;
      else if (fire & is_call) l <= pcrel;
      else if (wr_en && wr_reg == REG_L) l <= wr_data;
      // memory
      fl_v  <= gq & ~we;
      fl_ld <= mem_req;
      mo_v  <= mo_n;
      // control
      if (redir | adv_x | trapx) xm <= 1'b0;
      if (trap_now | (fire & is_halt) | (complete & stepm & ~xm)) run <= 1'b0;
      if ((complete & ~xm) | trap_now) stepm <= 1'b0;
      if (fire & is_halt) halted <= 1'b1;
      if (trap_now) trap <= 1'b1;
      waiting <= wait_now;
      if (fire & is_watch) wen <= ir[5:4];
      wprev <= {cv1, cv0};
      if (fire & is_wcfg) begin
        case (ir[7:3])
          CREG_CC0: cc0 <= opb[13:0];
          CREG_CC1: cc1 <= opb[13:0];
          CREG_WV0: wv0 <= opb[8:0];
          CREG_WV1: wv1 <= opb[8:0];
          default: ;
        endcase
      end
      if (flag_we) begin
        z  <= z_n;
        cy <= cy_n;
      end
      f <= f_n;
      d <= d_n;
      ovr <= rx_ovf | (~clr_ovr & ((ovr & ~(fire & is_ovclr)) | ovr_set));
      // host pulses, in the order of the ISS: stop, run, step, exec
      if (stop_p) begin
        run   <= 1'b0;
        stepm <= 1'b0;
        if (xm) begin
          xm   <= 1'b0;
          irv  <= 1'b0;
          mo_v <= 1'b0;
          fl_v <= 1'b0;
        end
      end
      if (run_p | step_p) begin
        run    <= 1'b1;
        halted <= 1'b0;
        stepm  <= step_p;
      end
      if (exec_p) begin
        ir    <= exec_word;
        irv   <= 1'b1;
        xm    <= 1'b1;
        pfv   <= 1'b0;
        hrv   <= 1'b0;
        fl_v  <= 1'b0;
        mo_v  <= 1'b0;
      end
      if (clr_ht) begin
        halted <= 1'b0;
        trap   <= 1'b0;
      end
    end
  end
  wire _unused = &{1'b0, opb[15:14]};
endmodule
