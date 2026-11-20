`default_nettype none
// Properties of jspe_shift (VERIF_PLAN 6, ISA 5, 7, 8.1, ARCH 8); shift.sby exposes shcfg, tapcfg, n, runl, lvq, ldq
module fv_shift (
    input wire        clk,
    input wire        rst,
    input wire [15:0] ir,
    input wire        fire,
    input wire [15:0] opb,
    input wire        wr_en,
    input wire [2:0]  wr_reg,
    input wire [15:0] wr_data,
    input wire        wb_en,
    input wire [1:0]  wb_reg,
    input wire [15:0] wb_data,
    input wire [3:0]  lv,
    input wire        hrx,
    input wire        htx,
    input wire [7:0]  rx_rdata
);
  wire [15:0] s;
  wire        dcur, dact, q, diff, mis, b, par, runc, wnz, loop_end, apush_stb, apull_stb, lf_step, lf_bit, lvq, ldq;
  wire [7:0]  apush_data, tapcfg;
  wire [10:0] shcfg;
  wire [3:0]  n;
  wire [2:0]  runl;

  jspe_shift dut (
      .clk       (clk),
      .rst       (rst),
      .ir        (ir),
      .fire      (fire),
      .opb       (opb),
      .wr_en     (wr_en),
      .wr_reg    (wr_reg),
      .wr_data   (wr_data),
      .wb_en     (wb_en),
      .wb_reg    (wb_reg),
      .wb_data   (wb_data),
      .lv        (lv),
      .hrx       (hrx),
      .htx       (htx),
      .rx_rdata  (rx_rdata),
      .s         (s),
      .dcur      (dcur),
      .dact      (dact),
      .q         (q),
      .diff      (diff),
      .mis       (mis),
      .b         (b),
      .par       (par),
      .runc      (runc),
      .wnz       (wnz),
      .loop_end  (loop_end),
      .apush_stb (apush_stb),
      .apush_data(apush_data),
      .apull_stb (apull_stb),
      .lf_step   (lf_step),
      .lf_bit    (lf_bit),
      .shcfg     (shcfg),
      .tapcfg    (tapcfg),
      .n         (n),
      .runl      (runl),
      .lvq       (lvq),
      .ldq       (ldq)
  );

  // reset in the first clock; checks start after it
  reg f_past = 1'b0;
  always @(posedge clk) f_past <= 1'b1;
  always @(*) if (!f_past) assume (rst);

  // isa.yaml: pin (E) mask 0x8000 value 0, aa [12:10], s [6]; smp 0xD820, wrst 0xD830 (mask 0xFFF0); wcfg 0xFA00, creg [7:3]
  wire       e    = fire && !ir[15];
  wire [2:0] aa   = ir[12:10];
  wire       sb   = ir[6];
  wire       smp  = fire && (ir & 16'hFFF0) == 16'hD820;
  wire       wrst = fire && (ir & 16'hFFF0) == 16'hD830;
  wire       wsh  = fire && ir[15:8] == 8'hFA && ir[7:3] == 5'd0;
  wire       wtap = fire && ir[15:8] == 8'hFA && ir[7:3] == 5'd1;
  wire       wr_s = wr_en && wr_reg == 3'd3;
  wire       wb_s = wb_en && wb_reg == 2'd3;

  // SHCFG [3:0] wl, [4] lsb, [6:5] inpin, [7] inv, [8] diff, [9] apush, [10] apull; TAPCFG [1:0] alt, [2] podd, [3] lfen, [6:4] rl, [7] r1
  wire [3:0]  w     = shcfg[3:0];
  wire        lsb   = shcfg[4];
  wire        inv   = shcfg[7];
  wire        apush = shcfg[9];
  wire        apull = shcfg[10];
  wire [1:0]  alt   = tapcfg[1:0];
  wire        podd  = tapcfg[2];
  wire        lfen  = tapcfg[3];
  wire [2:0]  rl    = tapcfg[6:4];
  wire        r1    = tapcfg[7];
  wire [16:0] mk    = (17'd1 << ({1'b0, w} + 5'd1)) - 17'd1;
  wire [15:0] mask  = mk[15:0];

  // ISA 7: tap_out, shift; ISA 8.1 steps 1-2 (sample shift with bin, out shift with fill 1, one shift per instant)
  function tapo;
    input [15:0] x;
    input        l;
    input [3:0]  ww;
    tapo = l ? x[0] : x[ww];
  endfunction
  function [15:0] shf;
    input [15:0] x;
    input        f;
    input        l;
    input [3:0]  ww;
    input [15:0] mm;
    shf = l ? (((x & mm) >> 1) | ({15'd0, f} << ww)) : (((x << 1) | {15'd0, f}) & mm);
  endfunction
  function [2:0] inc7;
    input [2:0] v;
    inc7 = (v == 3'd7) ? 3'd7 : v + 3'd1;
  endfunction

  wire        lin     = lv[shcfg[6:5]];
  wire        bin     = lin ^ (shcfg[8] & lvq) ^ inv;
  wire        m_shift = !ir[15] && (sb || aa == 3'd5);
  wire        m_dcur  = tapo(s, lsb, w) ^ inv;
  wire [15:0] sx0     = shf(s, sb ? bin : 1'b1, lsb, w, mask);
  wire        m_wend  = m_shift && (n == 4'd1 || (n == 4'd0 && w == 4'd0));
  wire        m_ok    = (!apush || htx) && (!apull || hrx);
  wire        m_xfer  = m_wend && (apush || apull) && m_ok;
  wire [15:0] sx      = (m_xfer && apull) ? {8'd0, rx_rdata} : sx0;
  wire        m_dact  = (!ir[15] && sb) ? (tapo(sx, lsb, w) ^ inv) : m_dcur;
  wire        m_t     = (!ir[15] && !sb && aa == 3'd5) ? m_dact : bin;
  wire [2:0]  run1    = r1 ? (m_t ? inc7(runl) : 3'd0) : ((m_t == b) ? inc7(runl) : 3'd1);
  wire        par1    = ((n == 4'd0) ? podd : par) ^ m_t;
  wire        b_s     = m_shift ? m_t : b;
  wire [2:0]  run_s   = m_shift ? run1 : runl;
  wire        par_s   = m_shift ? par1 : par;
  wire        m_q     = (alt == 2'd0) ? par_s : (alt == 2'd1) ? ~m_dact : (alt == 2'd2) ? ~b_s : b_s;
  wire        stuff   = aa == 3'd6 && alt == 2'd2;
  wire [2:0]  run2    = r1 ? (b_s ? 3'd0 : inc7(run_s)) : 3'd1;

  // expected next state (ISA 8.1 step 3, ISA 7, ISA 5 SHCFG write = wrst, ARCH 9.1: S not reset)
  reg [15:0] n_s;
  reg [10:0] n_shcfg;
  reg [7:0]  n_tapcfg;
  reg [3:0]  n_n;
  reg [2:0]  n_runl;
  reg        n_b, n_par, n_lvq, n_ldq;
  always @(*) begin
    n_s      = s;
    n_shcfg  = shcfg;
    n_tapcfg = tapcfg;
    n_n      = n;
    n_runl   = runl;
    n_b      = b;
    n_par    = par;
    n_lvq    = lvq;
    n_ldq    = ldq;
    if (e) begin
      if (m_shift) begin
        n_s   = sx;
        n_n   = (n == 4'd0) ? w : n - 4'd1;
        n_par = par1;
      end
      n_b    = stuff ? ~b_s : b_s;
      n_runl = stuff ? run2 : run_s;
      if (sb) n_lvq = lin;
      if (aa[2]) n_ldq = (aa == 3'd6) ? m_q : m_dact;
    end
    if (smp) begin
      n_lvq  = lin;
      n_b    = bin;
      n_runl = run1;
      n_par  = par ^ bin;
    end
    if (wrst || wsh) begin
      n_n    = 4'd0;
      n_par  = podd;
      n_runl = 3'd0;
    end
    if (wsh) n_shcfg = opb[10:0];
    if (wtap) n_tapcfg = opb[7:0];
    if (wb_s) n_s = wb_data;
    if (wr_s) n_s = wr_data;
    if (rst) begin
      n_s      = s;
      n_shcfg  = 11'h007;
      n_tapcfg = 8'd0;
      n_n      = 4'd0;
      n_runl   = 3'd0;
      n_b      = 1'b0;
      n_par    = 1'b0;
      n_lvq    = 1'b0;
      n_ldq    = 1'b0;
    end
  end

  reg [15:0] x_s, q_s, q_mask, q_sx;
  reg [10:0] x_shcfg;
  reg [7:0]  x_tapcfg;
  reg [3:0]  x_n, q_n, q_nstep;
  reg [2:0]  x_runl;
  reg        x_b, x_par, x_lvq, x_ldq, q_above, q_both, q_smp;
  always @(posedge clk) begin
    x_s      <= n_s;
    x_shcfg  <= n_shcfg;
    x_tapcfg <= n_tapcfg;
    x_n      <= n_n;
    x_runl   <= n_runl;
    x_b      <= n_b;
    x_par    <= n_par;
    x_lvq    <= n_lvq;
    x_ldq    <= n_ldq;
    q_above  <= !rst && e && m_shift && !(m_xfer && apull) && !wr_s && !wb_s;
    q_both   <= !rst && e && sb && aa == 3'd5 && !wr_s && !wb_s;
    q_smp    <= !rst && smp && !wr_s && !wb_s;
    q_mask   <= mask;
    q_sx     <= sx;
    q_s      <= s;
    q_n      <= n;
    q_nstep  <= (n == 4'd0) ? w : n - 4'd1;
  end

  always @(*) begin
    a_out:  assert (dcur == m_dcur && dact == m_dact && q == m_q && diff == shcfg[8] && mis == (lin ^ ldq) && runc == (rl != 3'd0 && runl == rl) && wnz == (n != 4'd0) && apush_stb == (e && m_xfer && apush) && apull_stb == (e && m_xfer && apull) && (!apush_stb || apush_data == sx0[7:0]) && lf_step == (e && m_shift && lfen) && (!lf_step || lf_bit == m_t) && (!e || loop_end == (m_wend && !m_xfer))); // outputs = ISA 7 / 8.1 (D, ALT, MIS, run, wend, auto-transfer, LFSR step)
    a_auto: assert ((!apush_stb || (e && m_wend && apush && htx && (!apull || hrx))) && (!apull_stb || (e && m_wend && apull && hrx && (!apush || htx)))); // auto-transfer strobes only at wend with every enabled FIFO able
  end
  always @(*) if (f_past) begin
    a_step:  assert (s == x_s && shcfg == x_shcfg && tapcfg == x_tapcfg && n == x_n && runl == x_runl && b == x_b && par == x_par && lvq == x_lvq && ldq == x_ldq); // next state = ISA 8.1 step 3 (S, N, B, RUN, PAR incl. stuff tap, LV, LD), wrst/SHCFG side effects, writes, reset
    a_above: assert (!q_above || (s & ~q_mask) == 16'h0000); // bits of S above the word are 0 after a shift (no pull, no S write)
    a_one:   assert (!q_both || (s == q_sx && n == q_nstep)); // out together with shin: one shift (the sampling one)
    a_smp:   assert (!q_smp || (s == q_s && n == q_n)); // smp changes neither S nor N
    a_nle:   assert (n <= shcfg[3:0]); // bit counter N <= wl
  end

  always @(*) if (f_past) begin
    c_out:      cover (e && aa == 3'd6 && alt == 2'd2 && m_shift && q);
    c_nle:      cover (n == 4'd15 && shcfg[3:0] == 4'd15);
    c_auto:     cover (apush_stb && apull_stb);
    c_auto_blk: cover (e && m_wend && apush && !htx && loop_end);
    c_step:     cover (e && stuff && m_shift && sb && b != b_s);
    c_above:    cover (q_above && s != 16'h0000 && shcfg[4] && shcfg[3:0] == 4'd3);
    c_one:      cover (q_both && s != q_s);
    c_smp:      cover (q_smp && s != 16'h0000 && b);
  end
endmodule
