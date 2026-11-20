`default_nettype none
// Properties of jspe_timer (VERIF_PLAN 6, ARCH 7, ISA 5, D084); timer.sby exposes tcfg, fa, psc, trun, tone, skip
module fv_timer (
    input wire        clk,
    input wire        rst,
    input wire [15:0] ir,
    input wire        fire,
    input wire [15:0] opb,
    input wire        wr_en,
    input wire [2:0]  wr_reg,
    input wire [15:0] wr_data,
    input wire [3:0]  rise,
    input wire [3:0]  fall
);
  wire [15:0] t, p, c;
  wire        tick, tock, t_start, trun, tone, skip;
  wire [14:0] tcfg;
  wire [7:0]  fa, psc;

  jspe_timer dut (
      .clk    (clk),
      .rst    (rst),
      .ir     (ir),
      .fire   (fire),
      .opb    (opb),
      .wr_en  (wr_en),
      .wr_reg (wr_reg),
      .wr_data(wr_data),
      .rise   (rise),
      .fall   (fall),
      .t      (t),
      .p      (p),
      .c      (c),
      .tick   (tick),
      .tock   (tock),
      .t_start(t_start),
      .tcfg   (tcfg),
      .fa     (fa),
      .psc    (psc),
      .trun   (trun),
      .tone   (tone),
      .skip   (skip)
  );

  // reset in the first clock; checks start after it
  reg f_past = 1'b0;
  always @(posedge clk) f_past <= 1'b1;
  always @(*) if (!f_past) assume (rst);

  // isa.yaml: tsync mask 0xFFF0 value 0xD800, tstop value 0xD810, tset mask 0xFF80 value 0xD880; wcfg 0xFA00 creg [7:3] = 2 (TCFG)
  wire tsync = fire && (ir & 16'hFFF0) == 16'hD800;
  wire tstop = fire && (ir & 16'hFFF0) == 16'hD810;
  wire tset  = fire && (ir & 16'hFF80) == 16'hD880;
  wire wtcfg = fire && ir[15:8] == 8'hFA && ir[7:3] == 5'd2;

  // ARCH 7 in the current clock; TCFG [7:0] frac, [9:8] pre, [11:10] sedge, [13:12] spin, [14] swin (ISA 5)
  wire [1:0] pre   = tcfg[9:8];
  wire [1:0] sedge = tcfg[11:10];
  wire [1:0] spin  = tcfg[13:12];
  wire       swin  = tcfg[14];
  wire       st    = (pre == 2'd1) ? (psc[3:0] == 4'hF) : (pre == 2'd2) ? (psc == 8'hFF) : 1'b1;
  wire       cnt   = trun && st && !skip;
  wire       edg   = (sedge[0] && rise[spin]) || (sedge[1] && fall[spin]);
  wire       aut   = edg && (!swin || !trun || t < c);
  wire       start = tsync || tset || aut;

  // expected next state (ARCH 7, ARCH 9.1: T, P, C not reset)
  reg [15:0] n_t, n_p, n_c;
  reg [14:0] n_tcfg;
  reg [7:0]  n_fa, n_psc;
  reg        n_trun, n_tone, n_skip;
  always @(*) begin
    n_t    = t;
    n_p    = p;
    n_c    = c;
    n_tcfg = tcfg;
    n_fa   = fa;
    n_psc  = psc;
    n_trun = trun;
    n_tone = tone;
    n_skip = skip;
    if (start) begin
      n_t    = tset ? opb : p;
      n_tone = tset ? 1'b1 : tsync ? 1'b0 : swin;
      n_trun = 1'b1;
      n_fa   = 8'd0;
      n_psc  = 8'd0;
      n_skip = 1'b0;
    end else if (tstop) begin
      n_trun = 1'b0;
    end else if (trun) begin
      n_psc = psc + 8'd1;
      if (st) begin
        if (skip) n_skip = 1'b0;
        else if (t != 16'd0) n_t = t - 16'd1;
        else if (tone) n_trun = 1'b0;
        else begin
          n_t            = p;
          {n_skip, n_fa} = {1'b0, fa} + {1'b0, tcfg[7:0]};
        end
      end
    end
    if (wtcfg) n_tcfg = opb[14:0];
    if (wr_en && wr_reg == 3'd4) n_p = wr_data;
    if (wr_en && wr_reg == 3'd5) n_c = wr_data;
    if (rst) begin
      n_t    = t;
      n_p    = p;
      n_c    = c;
      n_tcfg = 15'd0;
      n_fa   = 8'd0;
      n_psc  = 8'd0;
      n_trun = 1'b0;
      n_tone = 1'b0;
      n_skip = 1'b0;
    end
  end

  reg [15:0] x_t, x_p, x_c, q_t, q_opb, q_p;
  reg [14:0] x_tcfg;
  reg [7:0]  x_fa, x_psc;
  reg        x_trun, x_tone, x_skip, q_cntdn, q_one, q_start, q_tset;
  always @(posedge clk) begin
    x_t     <= n_t;
    x_p     <= n_p;
    x_c     <= n_c;
    x_tcfg  <= n_tcfg;
    x_fa    <= n_fa;
    x_psc   <= n_psc;
    x_trun  <= n_trun;
    x_tone  <= n_tone;
    x_skip  <= n_skip;
    q_cntdn <= !rst && !start && !tstop && cnt && t != 16'd0;
    q_one   <= !rst && !start && tick && tone;
    q_start <= !rst && start;
    q_tset  <= tset;
    q_t     <= t;
    q_opb   <= opb;
    q_p     <= p;
  end

  // ARCH 7 timing with pre = 0: a start with value V in clock k gives tick in k + V + 1, tock in k + V - C + 1
  reg        tr = 1'b0;
  reg [15:0] tv, el;
  always @(posedge clk) begin
    if (rst) tr <= 1'b0;
    else if (start) begin
      tr <= (pre == 2'd0 || pre == 2'd3) && !wtcfg && !(wr_en && wr_reg == 3'd5);
      tv <= tset ? opb : p;
      el <= 16'd0;
    end else if (tr) begin
      if (tstop || wtcfg || tick || (wr_en && wr_reg == 3'd5)) tr <= 1'b0;
      el <= el + 16'd1;
    end
  end

  always @(*) begin
    a_tick:   assert (tick == (cnt && t == 16'd0)); // tick <=> trun, step enabled, no skip, T = 0
    a_tock:   assert (tock == (cnt && t == c)); // tock <=> trun, step enabled, no skip, T = C
    a_win:    assert (!(swin && edg && trun && t >= c && !tsync && !tset) || !t_start); // windowed sync only when stopped or T < C
    a_tstart: assert (t_start == start); // t_start = an instruction start (tsync, tset) or an automatic sync
  end
  always @(*) if (f_past) begin
    a_step:   assert (t == x_t && p == x_p && c == x_c && tcfg == x_tcfg && fa == x_fa && trun == x_trun && tone == x_tone && skip == x_skip && (!trun || psc == x_psc)); // next state = ARCH 7 (prescaler, skip, count, one-shot stop, reload with FA carry, starts, D084, writes, reset)
    a_cnt:    assert (!q_cntdn || t == q_t - 16'd1); // T counts down by one per enabled clock
    a_one:    assert (!q_one || !trun); // a one-shot stops after its tick unless restarted in that clock
    a_start:  assert (!q_start || (trun && fa == 8'd0 && psc == 8'd0 && !skip && t == (q_tset ? q_opb : q_p))); // a start loads T (tset: rs, else P), clears FA, PSC, skip; wins over the count step and tstop
    a_lat:    assert (!tr || (tick == (el == tv) && tock == (c <= tv && el == tv - c))); // pre = 0: tick V + 1 clk, tock V - C + 1 clk after a start with value V (ARCH 7)
    h_trk:    assert (!tr || (trun && !skip && el <= tv && t == tv - el && (pre == 2'd0 || pre == 2'd3))); // tracked start: T = V - clocks since
  end

  always @(*) if (f_past) begin
    c_tick:     cover (tick && tone && !start);
    c_tick_per: cover (tick && !tone && {1'b0, fa} + {1'b0, tcfg[7:0]} > 9'd255);
    c_tock:     cover (tock && c != 16'd0);
    c_win:      cover (swin && edg && trun && t >= c && !tsync && !tset);
    c_win_ok:   cover (swin && edg && trun && t < c && !tsync && !tset);
    c_tstart:   cover (t_start && aut && tstop && !tsync && !tset);
    c_step:     cover (skip && trun && st);
    c_step_pre: cover (st && pre == 2'd1 && trun && t != tv);
    c_cnt:      cover (q_cntdn && t == 16'd5);
    c_one:      cover (q_one);
    c_start:    cover (q_start && q_tset && t == 16'h1234);
    c_lat:      cover (tr && tick && tv == 16'd5);
    c_lat_tock: cover (tr && tock && tv == 16'd6 && c == 16'd2);
    c_trk:      cover (tr && el == 16'd3 && tv == 16'd9);
  end
endmodule
