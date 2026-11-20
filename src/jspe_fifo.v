`default_nettype none
// Stream FIFO, depth 4, head always in entry 0 (ARCH 2.4, D046, D072)
module jspe_fifo #(
    parameter W = 8
) (
    input  wire         clk,
    input  wire         wr,
    input  wire [W-1:0] wdata,
    input  wire         rd,
    output wire [W-1:0] rdata,
    output wire         empty,
    output wire         full,
    output wire [2:0]   free,
    input  wire         flush
);
  reg [3:0] v;  // thermometer: v[k] = entry k valid
  reg [W-1:0] m0, m1, m2, m3;

  // limits follow the registered levels
  wire pop = rd & v[0];
  wire push = wr & ~v[3];
  // entry k takes wdata when it is at or behind the write position (entries behind it are invalid)
  wire [3:0] ld = {4{push}} & ~(pop ? {1'b0, v[3:1]} : v);

  always @(posedge clk) begin
    if (flush) v <= 4'b0000;
    else if (push & ~pop) v <= {v[2:0], 1'b1};
    else if (pop & ~push) v <= {1'b0, v[3:1]};
  end

  always @(posedge clk) begin
    if (ld[0]) m0 <= wdata;
    else if (pop) m0 <= m1;
    if (ld[1]) m1 <= wdata;
    else if (pop) m1 <= m2;
    if (ld[2]) m2 <= wdata;
    else if (pop) m2 <= m3;
    if (ld[3]) m3 <= wdata;
  end

  assign rdata = m0;
  assign empty = ~v[0];
  assign full = v[3];
  assign free = {~v[0], v[0] & ~v[2], (v[0] & ~v[1]) | (v[2] & ~v[3])};
endmodule
