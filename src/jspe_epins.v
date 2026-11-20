`default_nettype none
// Engine logical pins (ARCH 3.1, ISA 5-8.1): bind mux, edges, output state val / oe / fol with the E actions.
module jspe_epins (
    input  wire        clk,
    input  wire        rst,
    input  wire [15:0] ir,
    input  wire        fire,
    input  wire [15:0] opb,
    input  wire [15:0] lvl,
    input  wire [7:0]  sig,
    input  wire        dcur,
    input  wire        dact,
    input  wire        q,
    input  wire        diff,
    output wire [3:0]  lv,
    output wire [3:0]  rise,
    output wire [3:0]  fall,
    output wire [3:0]  val_q,
    output wire [3:0]  oe_q
);
/* verilator lint_off UNUSEDPARAM */
`include "jspe_isa.vh"
/* verilator lint_on UNUSEDPARAM */

  reg [23:0] bnd;          // pin k: [6k+4:6k] source, [6k+5] invert
  reg [3:0]  prev, oe, val, fol;
  reg [1:0]  bsup;          // no edge in the clock after a BIND write

  wire x_wcfg = fire && (ir & I_WCFG_MASK) == I_WCFG_VAL;
  wire x_b01  = x_wcfg && ir[7:3] == CREG_BIND01;
  wire x_b23  = x_wcfg && ir[7:3] == CREG_BIND23;
  wire x_e    = fire & ~ir[15];
  wire [1:0] pa = ir[14:13];
  wire [2:0] aa = ir[12:10];
  wire       pb = ir[9];
  wire [1:0] ab = ir[8:7];

  wire [31:0] src = {6'd0, 1'b1, 1'b0, sig, lvl};
  genvar k;
  generate
    for (k = 0; k < 4; k = k + 1) begin : g_pin
      assign lv[k]   = src[bnd[6*k+4:6*k]] ^ bnd[6*k+5];
      assign rise[k] = lv[k] & ~prev[k] & ~bsup[k/2];
      assign fall[k] = ~lv[k] & prev[k] & ~bsup[k/2];
    end
  endgenerate

  // next pin state: follow, then action A, then action B (B wins on the same pin)
  reg [3:0] fol_n, val_c, oe_c;
  integer i;
  always @(*) begin
    for (i = 0; i < 4; i = i + 1) begin
      val_c[i] = fol[i] ? dcur : val[i];
      oe_c[i]  = oe[i];
      fol_n[i] = fol[i];
      if (x_e && aa != ACTA_NONE && pa == i[1:0]) begin
        oe_c[i]  = (aa != ACTA_Z);
        fol_n[i] = (aa == ACTA_F);
        case (aa)
          ACTA_DRIVE0:      val_c[i] = 1'b0;
          ACTA_DRIVE1:      val_c[i] = 1'b1;
          ACTA_Z:           val_c[i] = val[i];
          ACTA_D, ACTA_OUT: val_c[i] = diff ? (val[i] ^ dact) : dact;
          ACTA_Q:           val_c[i] = q;
          default:          val_c[i] = dact;
        endcase
      end
      if (x_e && ab != ACTB_NONE && i[1] == 1'b0 && pb == i[0]) begin
        oe_c[i]  = 1'b1;
        fol_n[i] = 1'b0;
        val_c[i] = (ab == ACTB_T) ? ~val[i] : ab[1];
      end
    end
  end
  // registered pin state to the pad owner mux: the pad register adds one clock (D081)
  assign val_q = val;
  assign oe_q  = oe;

  always @(posedge clk) begin
    if (rst) begin
      bnd <= 24'd0;
      prev <= 4'd0;
      bsup <= 2'd0;
      oe   <= 4'd0;
      val  <= 4'd0;
      fol  <= 4'd0;
    end else begin
      if (x_b01) bnd[11:0]  <= {opb[13:8], opb[5:0]};
      if (x_b23) bnd[23:12] <= {opb[13:8], opb[5:0]};
      bsup <= {x_b23, x_b01};
      prev <= lv;
      oe   <= oe_c;
      val  <= val_c;
      fol  <= fol_n;
    end
  end
  wire _unused = &{1'b0, opb[15:14], opb[7:6], ir[6:0]};
endmodule
