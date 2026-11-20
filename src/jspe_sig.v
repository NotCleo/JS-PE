`default_nettype none
// Inter-engine signal flags G0-G7 (ARCH 2.6, D048)
module jspe_sig (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [31:0] e_set,
    input  wire [31:0] e_clr,
    input  wire        sig_we,
    input  wire [7:0]  sig_wdata,
    output reg  [7:0]  sig
);
  // engine i at [8i +: 8]
  wire [7:0] s = e_set[7:0] | e_set[15:8] | e_set[23:16] | e_set[31:24];
  wire [7:0] c = e_clr[7:0] | e_clr[15:8] | e_clr[23:16] | e_clr[31:24];

  // clear wins over set, a host write replaces the result
  always @(posedge clk) begin
    if (!rst_n) sig <= 8'h00;
    else if (sig_we) sig <= sig_wdata;
    else sig <= (sig | s) & ~c;
  end
endmodule
