`default_nettype none
// Properties of jspe_epins (VERIF_PLAN 6, ISA 5, 7, 8.1, ARCH 9.1); epins.sby exposes bnd and fol
module fv_epins (
    input wire        clk,
    input wire        rst,
    input wire [15:0] ir,
    input wire        fire,
    input wire [15:0] opb,
    input wire [15:0] lvl,
    input wire [7:0]  sig,
    input wire        dcur,
    input wire        dact,
    input wire        q,
    input wire        diff
);
  wire [3:0]  lv, rise, fall, val, oe, fol;
  wire [23:0] bnd;

  jspe_epins dut (
      .clk  (clk),
      .rst  (rst),
      .ir   (ir),
      .fire (fire),
      .opb  (opb),
      .lvl  (lvl),
      .sig  (sig),
      .dcur (dcur),
      .dact (dact),
      .q    (q),
      .diff (diff),
      .lv   (lv),
      .rise (rise),
      .fall (fall),
      .val_q(val),
      .oe_q (oe),
      .bnd  (bnd),
      .fol  (fol)
  );

  // reset in the first clock; checks start after it
  reg f_past = 1'b0;
  always @(posedge clk) f_past <= 1'b1;
  always @(*) if (!f_past) assume (rst);

  // previous clock
  reg        rst_q, fire_q, dcur_q, dact_q, q_q, diff_q;
  reg [15:0] ir_q, opb_q;
  reg [23:0] bnd_q;
  reg [3:0]  lv_q, val_p, oe_p, fol_p;
  always @(posedge clk) begin
    rst_q  <= rst;
    fire_q <= fire;
    dcur_q <= dcur;
    dact_q <= dact;
    q_q    <= q;
    diff_q <= diff;
    ir_q   <= ir;
    opb_q  <= opb;
    bnd_q  <= bnd;
    lv_q   <= lv;
    val_p  <= val;
    oe_p   <= oe;
    fol_p  <= fol;
  end

  // isa.yaml: pin (E) mask 0x8000 value 0, pa [14:13] aa [12:10] pb [9] ab [8:7]; wcfg mask 0xFF00 value 0xFA00, creg [7:3]
  wire       e_q   = fire_q && ir_q[15] == 1'b0;
  wire [1:0] pa_q  = ir_q[14:13];
  wire [2:0] aa_q  = ir_q[12:10];
  wire       pb_q  = ir_q[9];
  wire [1:0] ab_q  = ir_q[8:7];
  wire       wc_q  = fire_q && ir_q[15:8] == 8'hFA;
  wire       b01_q = wc_q && ir_q[7:3] == 5'd3;
  wire       b23_q = wc_q && ir_q[7:3] == 5'd4;

  // pins named by action A / B in the previous clock
  reg [3:0] na_q, nb_q;
  integer k;
  always @(*)
    for (k = 0; k < 4; k = k + 1) begin
      na_q[k] = e_q && aa_q != 3'd0 && pa_q == k;
      nb_q[k] = e_q && ab_q != 2'd0 && k < 2 && pb_q == k;
    end
  wire [3:0] nm_q = na_q | nb_q;

  // ISA 8.1 step 2 (A: 0 1 z d out q f; B: 0 1 t, B wins on its pin), ISA 7 follow, ARCH 9.1 reset
  reg [3:0] m_val, m_oe, m_fol;
  always @(*)
    for (k = 0; k < 4; k = k + 1) begin
      m_val[k] = fol_p[k] ? dcur_q : val_p[k];
      m_oe[k]  = oe_p[k];
      m_fol[k] = fol_p[k];
      if (nb_q[k]) begin
        m_oe[k]  = 1'b1;
        m_fol[k] = 1'b0;
        m_val[k] = (ab_q == 2'd3) ? ~val_p[k] : (ab_q == 2'd2);
      end else if (na_q[k]) begin
        m_oe[k]  = aa_q != 3'd3;
        m_fol[k] = aa_q == 3'd7;
        case (aa_q)
          3'd1:       m_val[k] = 1'b0;
          3'd2:       m_val[k] = 1'b1;
          3'd3:       m_val[k] = val_p[k];
          3'd4, 3'd5: m_val[k] = diff_q ? (val_p[k] ^ dact_q) : dact_q;
          3'd6:       m_val[k] = q_q;
          default:    m_val[k] = dact_q;
        endcase
      end
      if (rst_q) begin
        m_val[k] = 1'b0;
        m_oe[k]  = 1'b0;
        m_fol[k] = 1'b0;
      end
    end

  // ISA 5 BIND01/23 ([4:0] p0 source, [5] p0 invert, [12:8] p1, [13] p1 invert) in the jspe_epins packing: pin k at [6k+5:6k]
  reg [23:0] m_bnd;
  always @(*) begin
    m_bnd = bnd_q;
    if (b01_q) m_bnd[11:0]  = {opb_q[13], opb_q[12:8], opb_q[5], opb_q[4:0]};
    if (b23_q) m_bnd[23:12] = {opb_q[13], opb_q[12:8], opb_q[5], opb_q[4:0]};
    if (rst_q) m_bnd = 24'd0;
  end

  // ISA 5 sources: 0-7 ui_in, 8-15 uio_in, 16-23 G0-G7, 24 constant 0, 25 constant 1, 26-31 read 0
  function lvf;
    input [5:0]  b;
    input [15:0] l;
    input [7:0]  g;
    begin
      if (b[4:0] < 5'd16)       lvf = l[b[3:0]];
      else if (b[4:0] < 5'd24)  lvf = g[b[2:0]];
      else if (b[4:0] == 5'd25) lvf = 1'b1;
      else                      lvf = 1'b0;
      lvf = lvf ^ b[5];
    end
  endfunction
  reg [3:0] m_lv;
  always @(*) for (k = 0; k < 4; k = k + 1) m_lv[k] = lvf(bnd[6*k +: 6], lvl, sig);

  wire [3:0] m_prev = rst_q ? 4'h0 : lv_q;
  wire [3:0] m_sup  = rst_q ? 4'h0 : {b23_q, b23_q, b01_q, b01_q};

  always @(*) begin
    a_lv:    assert (lv == m_lv); // level = source ^ invert; sources 0-15 lvl, 16-23 G0-G7, 24 0, 25 1, 26-31 0 (ISA 5)
    a_rf:    assert ((rise & fall) == 4'h0); // rise and fall never together
  end
  always @(*) if (f_past) begin
    a_bnd:   assert (bnd == m_bnd); // BIND01/23 = 0 after reset, written only by their wcfg with opb[5:0], opb[13:8] (ISA 5)
    a_edge:  assert (rise == (lv & ~m_prev & ~m_sup) && fall == (~lv & m_prev & ~m_sup)); // edges = level change vs the previous clock (0 after reset), except after a BIND write
    a_bsup:  assert (((rise | fall) & m_sup) == 4'h0); // no edge in the clock after a write to the pin's BIND register
    a_act:   assert (val == m_val && oe == m_oe && fol == m_fol); // pin state = ISA 8.1 step 2 (A, then B wins), ISA 7 follow; 0 after reset
    a_fol:   assert ((fol_p & ~nm_q & {4{!rst_q}} & (val ^ {4{dcur_q}})) == 4'h0); // a following pin that no action names takes dcur
    a_oefol: assert ((~nm_q & {4{!rst_q}} & ((oe ^ oe_p) | (fol ^ fol_p))) == 4'h0); // oe, fol change only when an E action names the pin
    a_val:   assert ((~nm_q & ~fol_p & {4{!rst_q}} & (val ^ val_p)) == 4'h0); // val changes only by an action naming the pin or by follow
  end

  always @(*) if (f_past) begin
    c_lv:     cover (bnd[4:0] == 5'd25 && !bnd[5] && lv[0]);
    c_lv_sig: cover (bnd[10:6] == 5'd20 && bnd[11] && !lv[1] && sig[4]);
    c_lv_hi:  cover (bnd[16:12] == 5'd30 && bnd[17] && lv[2]);
    c_rf:     cover (rise[0] && fall[1]);
    c_bnd:    cover (!rst_q && b23_q && bnd[23:12] != bnd_q[23:12]);
    c_bnd_01: cover (!rst_q && b01_q && bnd[11:6] == 6'b110011);
    c_edge:   cover (rise[2] && bnd[16:12] >= 5'd8 && bnd[16:12] < 5'd16);
    c_edge_f: cover (fall[3] && bnd[22:18] >= 5'd16 && bnd[22:18] < 5'd24);
    c_bsup:   cover (m_sup[0] && lv[0] != lv_q[0]);
    c_act:    cover (!rst_q && na_q[0] && nb_q[0] && aa_q == 3'd3 && ab_q == 2'd2 && oe[0] && val[0]);
    c_act_t:  cover (!rst_q && fol_p[1] && nb_q[1] && ab_q == 2'd3 && !fol[1] && val[1] != val_p[1]);
    c_act_f:  cover (!rst_q && !fol_p[2] && fol[2] && val[2]);
    c_act_d:  cover (!rst_q && na_q[3] && aa_q == 3'd4 && diff_q && val_p[3] && dact_q && !val[3]);
    c_act_q:  cover (!rst_q && na_q[1] && aa_q == 3'd6 && q_q && val[1] && !dact_q);
    c_fol:    cover (!rst_q && fol_p[3] && !nm_q[3] && val[3] != val_p[3]);
    c_oefol:  cover (!rst_q && oe_p[3] && !oe[3]);
    c_val:    cover (!rst_q && !nm_q[0] && !fol_p[0] && val[0] && !dcur_q && fol_p[1]);
  end
endmodule
