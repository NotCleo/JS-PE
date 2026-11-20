`default_nettype none
// Pin plane (ARCH 2.1, D076): 2-FF sync, optional 3-sample filter, host pad configuration, registered pads.
module jspe_pins (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [7:0]  ui_in,
    input  wire [7:0]  uio_in,
    output wire [15:0] lvl,
    output wire [15:0] lvl_raw,
    input  wire [15:0] filt,
    input  wire [15:0] e_val,
    input  wire [15:0] e_oe,
    input  wire        pad_we,
    input  wire [3:0]  pad_idx,
    input  wire [6:0]  pad_wdata,
    input  wire        h_tx,
    input  wire        h_rdy,
    output reg  [7:0]  uo_out,
    output reg  [7:0]  uio_out,
    output reg  [7:0]  uio_oe
);
  // PAD bit positions (HOSTIF 5)
  localparam P_INV = 4;
  localparam P_OD  = 5;
  localparam P_EN  = 6;

  reg  [15:0] s1, s2, h1, h2, held;
  wire [15:0] eq   = ~(s2 ^ h1) & ~(s2 ^ h2);
  wire [15:0] fout = (s2 & eq) | (held & ~eq);

  always @(posedge clk) begin
    s1 <= {uio_in, ui_in};
    s2 <= s1;
    h1 <= s2;
    h2 <= h1;
  end

  always @(posedge clk) begin
    if (!rst_n) held <= 16'h0000;
    else        held <= fout;
  end

  assign lvl_raw = s2;
  assign lvl     = (s2 & ~filt) | (fout & filt);

  wire [7:0] uo_d, uio_d, oe_d;

  genvar j;
  generate
    for (j = 0; j < 8; j = j + 1) begin : g_uo
      if (j == 4 || j == 5) begin : g_link
        assign uo_d[j] = (j == 4) ? h_tx : h_rdy;
      end else begin : g_pad
        reg [4:0] c;
        reg       en;
        always @(posedge clk) begin
          if (!rst_n) begin
            c  <= 5'd0;
            en <= 1'b0;
          end else if (pad_we && pad_idx == j) begin
            c  <= pad_wdata[4:0];
            en <= pad_wdata[P_EN];
          end
        end
        assign uo_d[j] = en & (e_val[c[3:0]] ^ c[P_INV]);
      end
    end
    for (j = 0; j < 8; j = j + 1) begin : g_uio
      reg [6:0] c;
      always @(posedge clk) begin
        if (!rst_n) c <= 7'd0;
        else if (pad_we && pad_idx == (j + 8)) c <= pad_wdata;
      end
      wire v = e_val[c[3:0]] ^ c[P_INV];
      wire o = e_oe[c[3:0]];
      assign uio_d[j] = c[P_EN] & ~c[P_OD] & v;
      assign oe_d[j]  = c[P_EN] & o & ~(c[P_OD] & v);
    end
  endgenerate

  always @(posedge clk) begin
    if (!rst_n) begin
      uo_out  <= 8'h10;
      uio_out <= 8'h00;
      uio_oe  <= 8'h00;
    end else begin
      uo_out  <= uo_d;
      uio_out <= uio_d;
      uio_oe  <= oe_d;
    end
  end
endmodule
